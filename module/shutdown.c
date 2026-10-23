/* SPDX-License-Identifier: GPL-2.0-only */
#include <linux/errno.h>
#include "shutdown.h"

struct qkx_shutdown_step {
	enum qkx_shutdown_subsystem subsystem;
	enum qkx_shutdown_action action;
};

struct qkx_shutdown_clock {
	const struct qkx_shutdown_plan *plan;
	u64 last;
	u64 deadline;
	bool sampled;
	unsigned int polls;
	unsigned int limit;
};

struct qkx_shutdown_failure {
	int error;
	int backend_return;
	int raw_error;
};

static struct qkx_shutdown_step qkx_shutdown_step(unsigned int index)
{
	struct qkx_shutdown_step step;

	if (index < 7) {
		step.subsystem = index;
		step.action = QKX_SHUTDOWN_CLOSE;
	} else if (index == 7) {
		step.subsystem = QKX_SHUTDOWN_AOP;
		step.action = QKX_SHUTDOWN_CLOSE;
	} else if (index < 22) {
		step.subsystem = (index - 8) % QKX_SHUTDOWN_PRODUCERS;
		step.action = index < 15 ? QKX_SHUTDOWN_DRAIN : QKX_SHUTDOWN_STOP;
	} else {
		step.subsystem = index == 22 ? QKX_SHUTDOWN_SMMU :
			index == 23 ? QKX_SHUTDOWN_SECURE : QKX_SHUTDOWN_AOP;
		step.action = index < 24 ? QKX_SHUTDOWN_RELEASE :
			index == 24 ? QKX_SHUTDOWN_DRAIN : QKX_SHUTDOWN_RETIRE;
	}
	return step;
}

static int qkx_shutdown_inspect(const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_backend *backends, unsigned int count,
	struct qkx_shutdown_inventory *inventory, struct qkx_shutdown_result *result)
{
	unsigned int i, needed, all = (1U << QKX_SHUTDOWN_ACTIONS) - 1;
	struct qkx_shutdown_step step;
	const struct qkx_shutdown_backend *backend;
	struct qkx_shutdown_inventory *item;
	int ret;

	if (!plan || !backends || count != QKX_SHUTDOWN_SUBSYSTEMS ||
	    !plan->epoch || !plan->transaction || !plan->now_ns ||
	    !plan->forward_budget_ns || !plan->rollback_budget_ns ||
	    !plan->forward_poll_limit || !plan->rollback_poll_limit ||
	    plan->forward_poll_limit > QKX_SHUTDOWN_MAX_POLLS ||
	    plan->rollback_poll_limit > QKX_SHUTDOWN_MAX_POLLS)
		return -EINVAL;
	for (i = 0; i < count; i++) {
		backend = &backends[i];
		item = &inventory[i];
		*item = (struct qkx_shutdown_inventory){ 0 };
		if (backend->subsystem != (enum qkx_shutdown_subsystem)i ||
		    !backend->inspect ||
		    !backend->begin || !backend->poll)
			return -EOPNOTSUPP;
		ret = backend->inspect(backend->context, item);
		if (ret) {
			if (result) {
				result->failed_stage = QKX_SHUTDOWN_FAILURE_INSPECT;
				result->inspect_return = ret;
				result->failed.subsystem = i;
			}
			return ret < 0 ? ret : -EPROTO;
		}
		if (!item->client || item->epoch != plan->epoch ||
		    !item->inventory_complete || !item->ownership_complete ||
		    !item->recovery_verified || !item->nonblocking ||
		    !item->admission_owned ||
		    ((item->capabilities | item->rollback_capabilities |
		      item->irreversible_actions) & ~all) ||
		    (item->irreversible_actions & ~item->capabilities))
			return -EOPNOTSUPP;
	}
	/* Check every reversible operation before issuing the first operation. */
	for (i = 0; i < QKX_SHUTDOWN_STEPS; i++) {
		step = qkx_shutdown_step(i);
		backend = &backends[step.subsystem];
		item = &inventory[step.subsystem];
		needed = QKX_SHUTDOWN_CAP(step.action);
		if (!(item->capabilities & needed))
			return -EOPNOTSUPP;
		if (!(item->irreversible_actions & needed) &&
		    (!(item->rollback_capabilities & needed) ||
		     !backend->rollback_begin || !backend->rollback_poll))
			return -EOPNOTSUPP;
	}
	return 0;
}

int qkx_shutdown_validate(const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_backend *backends, unsigned int count)
{
	struct qkx_shutdown_inventory inventory[QKX_SHUTDOWN_SUBSYSTEMS];

	return qkx_shutdown_inspect(plan, backends, count, inventory, 0);
}

static int qkx_shutdown_sample(struct qkx_shutdown_clock *clock, u64 *now)
{
	int ret = clock->plan->now_ns(clock->plan->clock_context, now);

	if (ret)
		return ret < 0 ? ret : -EPROTO;
	if (clock->sampled && *now < clock->last)
		return -ERANGE;
	clock->last = *now;
	clock->sampled = true;
	return 0;
}

static int qkx_shutdown_deadline(struct qkx_shutdown_clock *clock, u64 budget,
	unsigned int limit)
{
	u64 now;
	int ret = qkx_shutdown_sample(clock, &now);

	if (ret)
		return ret;
	if (budget > ~(u64)0 - now)
		return -EOVERFLOW;
	clock->deadline = now + budget;
	clock->polls = 0;
	clock->limit = limit;
	return 0;
}

static int qkx_shutdown_time(struct qkx_shutdown_clock *clock)
{
	u64 now;
	int ret = qkx_shutdown_sample(clock, &now);

	if (ret)
		return ret;
	return now >= clock->deadline ? -ETIMEDOUT : 0;
}

static bool qkx_shutdown_key_equal(const struct qkx_shutdown_key *a,
	const struct qkx_shutdown_key *b)
{
	return a->client == b->client && a->transaction == b->transaction &&
		a->epoch == b->epoch && a->attempt == b->attempt &&
		a->original_attempt == b->original_attempt &&
		a->subsystem == b->subsystem && a->action == b->action &&
		a->direction == b->direction;
}

static unsigned int qkx_shutdown_evidence(const struct qkx_shutdown_key *key)
{
	if (key->direction == QKX_SHUTDOWN_REOPEN)
		return QKX_SHUTDOWN_NO_LATE_COMPLETION |
			QKX_SHUTDOWN_ADMISSION_REOPENED;
	if (key->direction == QKX_SHUTDOWN_UNDO)
		return QKX_SHUTDOWN_NO_LATE_COMPLETION |
			(key->action == QKX_SHUTDOWN_CLOSE ?
			 QKX_SHUTDOWN_ADMISSION_CLOSED : QKX_SHUTDOWN_RESTORED);
	return 1U << key->action;
}

/* Return 1 only for a fully bound, checked completion; zero means pending. */
static int qkx_shutdown_receipt(const struct qkx_shutdown_key *key,
	const struct qkx_shutdown_receipt *receipt)
{
	if (!qkx_shutdown_key_equal(key, &receipt->key))
		return -EPROTO;
	if (receipt->raw_error)
		return receipt->raw_error < 0 ? receipt->raw_error : -EPROTO;
	if (receipt->state == QKX_SHUTDOWN_PENDING)
		return receipt->evidence || receipt->quiesced_attempt ? -EPROTO : 0;
	if (receipt->state != QKX_SHUTDOWN_COMPLETE ||
	    receipt->evidence != qkx_shutdown_evidence(key) ||
	    receipt->quiesced_attempt != key->original_attempt)
		return -EPROTO;
	return 1;
}

static int qkx_shutdown_execute(const struct qkx_shutdown_backend *backend,
	const struct qkx_shutdown_inventory *inventory,
	const struct qkx_shutdown_key *key, struct qkx_shutdown_clock *clock,
	struct qkx_shutdown_result *result, struct qkx_shutdown_failure *failure)
{
	struct qkx_shutdown_receipt receipt;
	bool first = true, forward = key->direction == QKX_SHUTDOWN_FORWARD;
	int ret, observed, time_error;

	*failure = (struct qkx_shutdown_failure){ 0 };
	for (;;) {
		ret = qkx_shutdown_time(clock);
		if (ret)
			break;
		if (!first && clock->polls >= clock->limit) {
			ret = -ETIMEDOUT;
			break;
		}
		if (first && forward) {
			result->attempted++;
			if (inventory->irreversible_actions &
			    QKX_SHUTDOWN_CAP(key->action))
				result->irreversible = true;
		}
		receipt = (struct qkx_shutdown_receipt){ 0 };
		if (!first)
			clock->polls++;
		if (forward)
			ret = first ? backend->begin(backend->context, key, &receipt) :
				backend->poll(backend->context, key, &receipt);
		else
			ret = first ? backend->rollback_begin(backend->context, key, &receipt) :
				backend->rollback_poll(backend->context, key, &receipt);
		failure->backend_return = ret;
		failure->raw_error = receipt.raw_error;
		/* Always check time after the hook, even when it reports completion. */
		time_error = qkx_shutdown_time(clock);
		observed = ret ? (ret < 0 ? ret : -EPROTO) :
			qkx_shutdown_receipt(key, &receipt);
		if (observed < 0) {
			ret = observed;
			break;
		}
		if (time_error) {
			ret = time_error;
			break;
		}
		if (observed == 1)
			return 0;
		first = false;
	}
	failure->error = ret;
	return ret;
}

static struct qkx_shutdown_key qkx_shutdown_key(
	const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_inventory *inventory, unsigned int index,
	u64 attempt, enum qkx_shutdown_direction direction)
{
	struct qkx_shutdown_step step = qkx_shutdown_step(index);
	struct qkx_shutdown_key key = {
		.client = inventory[step.subsystem].client,
		.transaction = plan->transaction, .epoch = plan->epoch,
		.attempt = attempt, .subsystem = step.subsystem,
		.action = step.action, .direction = direction,
		.original_attempt = direction == QKX_SHUTDOWN_FORWARD ? 0 : index + 1
	};

	return key;
}

int qkx_shutdown_run(const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_backend *backends, unsigned int count,
	struct qkx_shutdown_result *result)
{
	struct qkx_shutdown_inventory inventory[QKX_SHUTDOWN_SUBSYSTEMS];
	struct qkx_shutdown_key key;
	struct qkx_shutdown_clock clock = { .plan = plan };
	struct qkx_shutdown_failure failure = { 0 };
	unsigned int i, attempted;
	u64 attempt = 0;
	int ret;

	if (!result)
		return -EINVAL;
	*result = (struct qkx_shutdown_result){ 0 };
	result->failed_stage = QKX_SHUTDOWN_FAILURE_PREFLIGHT;
	result->failed.subsystem = QKX_SHUTDOWN_SUBSYSTEMS;
	result->failed.action = QKX_SHUTDOWN_ACTIONS;
	result->failed.direction = QKX_SHUTDOWN_NO_DIRECTION;
	ret = qkx_shutdown_inspect(plan, backends, count, inventory, result);
	if (ret)
		goto failed;
	ret = qkx_shutdown_deadline(&clock, plan->forward_budget_ns,
		plan->forward_poll_limit);
	if (ret)
		goto failed;
	result->failed_stage = QKX_SHUTDOWN_FAILURE_NONE;
	for (i = 0; i < QKX_SHUTDOWN_STEPS; i++) {
		key = qkx_shutdown_key(plan, inventory, i, ++attempt,
			QKX_SHUTDOWN_FORWARD);
		ret = qkx_shutdown_execute(&backends[key.subsystem],
			&inventory[key.subsystem], &key, &clock, result, &failure);
		if (ret) {
			result->failed_stage = QKX_SHUTDOWN_FAILURE_FORWARD;
			result->failed = key;
			break;
		}
		result->completed++;
	}
	result->forward_polls = clock.polls;
	if (!ret) {
		result->complete = true;
		return 0;
	}
	result->backend_return = failure.backend_return;
	result->backend_raw_error = failure.raw_error;
	result->error = ret;
	attempted = result->attempted;
	if (!attempted)
		return ret;
	if (result->irreversible) {
		result->recovery_required = true;
		return ret;
	}
	/* One separate budget covers cancellation, restoration and reopening. */
	failure = (struct qkx_shutdown_failure){ 0 };
	ret = qkx_shutdown_deadline(&clock, plan->rollback_budget_ns,
		plan->rollback_poll_limit);
	if (ret)
		goto rollback_failed;
	for (i = attempted; i > 0; i--) {
		key = qkx_shutdown_key(plan, inventory, i - 1, ++attempt,
			QKX_SHUTDOWN_UNDO);
		ret = qkx_shutdown_execute(&backends[key.subsystem],
			&inventory[key.subsystem], &key, &clock, result, &failure);
		if (ret)
			goto rollback_failed;
		result->rolled_back++;
	}
	/* Nothing may reopen until every attempted operation is quiesced/restored. */
	for (i = attempted; i > 0; i--) {
		key = qkx_shutdown_key(plan, inventory, i - 1, attempt + 1,
			QKX_SHUTDOWN_REOPEN);
		if (key.action != QKX_SHUTDOWN_CLOSE)
			continue;
		attempt++;
		ret = qkx_shutdown_execute(&backends[key.subsystem],
			&inventory[key.subsystem], &key, &clock, result, &failure);
		if (ret)
			goto rollback_failed;
		result->admission_reopened++;
		result->admission_reopened_mask |= 1U << key.subsystem;
	}
	result->rollback_polls = clock.polls;
	return result->error;

rollback_failed:
	result->rollback_error = ret;
	result->rollback_backend_return = failure.backend_return;
	result->rollback_raw_error = failure.raw_error;
	result->rollback_polls = clock.polls;
	result->recovery_required = true;
	return result->error;
failed:
	result->error = ret;
	return ret;
}
