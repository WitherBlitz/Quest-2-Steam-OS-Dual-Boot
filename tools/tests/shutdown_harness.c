/* Host-only backend doubles; no hardware or kernel entry points. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/errno.h>
#include "shutdown.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "%d: %s\n", __LINE__, #x); return 1; } } while (0)
#define ALL_ACTIONS ((1U << QKX_SHUTDOWN_ACTIONS) - 1)
struct fixture;
struct owner { struct fixture *f; unsigned int index; };
struct fixture {
	struct qkx_shutdown_plan plan;
	struct qkx_shutdown_backend backends[QKX_SHUTDOWN_SUBSYSTEMS];
	struct qkx_shutdown_inventory inventory[QKX_SHUTDOWN_SUBSYSTEMS];
	struct owner owners[QKX_SHUTDOWN_SUBSYSTEMS];
	struct qkx_shutdown_result result;
	struct qkx_shutdown_key log[160];
	unsigned int calls, inspections, clocks, begins, undos, reopens;
	u64 now, tick;
	int fail_at, fail_return, raw_error, bad_identity, bad_evidence;
	int rollback_failure, late, pending, frozen, after_hook, backward;
	int zero_receipt, check_irreversible, marker_seen;
	int rollback_pending, rollback_frozen, rollback_failure_at, reopen_failure_at;
	int inspect_error, inspect_error_at, clock_error_at, clock_error;
};

static int inspect(void *context, struct qkx_shutdown_inventory *inventory)
{
	struct owner *o = context;
	o->f->inspections++;
	if (o->f->inspect_error && o->index == (unsigned int)o->f->inspect_error_at)
		return o->f->inspect_error;
	*inventory = o->f->inventory[o->index];
	return 0;
}

static int now_ns(void *context, u64 *now)
{
	struct fixture *f = context;
	f->clocks++;
	if (f->clocks == (unsigned int)f->clock_error_at)
		return f->clock_error;
	*now = f->now;
	if (f->backward && f->clocks == 3)
		*now = 0;
	if (!f->frozen)
		f->now += f->tick;
	return 0;
}

static unsigned int proof(const struct qkx_shutdown_key *key)
{
	if (key->direction == QKX_SHUTDOWN_REOPEN)
		return QKX_SHUTDOWN_NO_LATE_COMPLETION | QKX_SHUTDOWN_ADMISSION_REOPENED;
	if (key->direction == QKX_SHUTDOWN_UNDO)
		return QKX_SHUTDOWN_NO_LATE_COMPLETION |
			(key->action == QKX_SHUTDOWN_CLOSE ? QKX_SHUTDOWN_ADMISSION_CLOSED : QKX_SHUTDOWN_RESTORED);
	return 1U << key->action;
}

static int operation(void *context, const struct qkx_shutdown_key *key,
		     struct qkx_shutdown_receipt *receipt, int begin)
{
	struct fixture *f = ((struct owner *)context)->f;
	if (f->calls >= 160)
		abort();
	f->log[f->calls++] = *key;
	if (begin && key->direction == QKX_SHUTDOWN_FORWARD) {
		f->begins++;
		if (f->check_irreversible && f->begins == (unsigned int)f->fail_at)
			f->marker_seen = f->result.irreversible;
	}
	if (begin && key->direction == QKX_SHUTDOWN_UNDO)
		f->undos++;
	if (begin && key->direction == QKX_SHUTDOWN_REOPEN)
		f->reopens++;
	if (key->direction == QKX_SHUTDOWN_FORWARD && f->zero_receipt)
		return 0;
	receipt->key = *key;
	receipt->state = QKX_SHUTDOWN_COMPLETE;
	receipt->evidence = proof(key);
	if (key->direction != QKX_SHUTDOWN_FORWARD)
		receipt->quiesced_attempt = key->original_attempt;
	if (key->direction == QKX_SHUTDOWN_FORWARD) {
		if (f->pending && (begin || f->frozen)) {
			receipt->state = QKX_SHUTDOWN_PENDING;
			receipt->evidence = 0;
		}
		if (f->after_hook)
			f->now += f->plan.forward_budget_ns;
		if (f->bad_evidence)
			receipt->evidence = 0;
		switch (f->bad_identity) {
		case 1: receipt->key.client++; break;
		case 2: receipt->key.transaction++; break;
		case 3: receipt->key.epoch++; break;
		case 4: receipt->key.attempt++; break;
		case 5: receipt->key.original_attempt++; break;
		case 6: receipt->key.subsystem = QKX_SHUTDOWN_AOP; break;
		case 7: receipt->key.action = QKX_SHUTDOWN_RETIRE; break;
		case 8: receipt->key.direction = QKX_SHUTDOWN_UNDO; break;
		}
		if (f->begins == (unsigned int)f->fail_at) {
			receipt->raw_error = f->raw_error;
			return f->fail_return;
		}
	} else if (key->direction == QKX_SHUTDOWN_UNDO) {
		if (f->rollback_pending && (begin || f->rollback_frozen)) {
			receipt->state = QKX_SHUTDOWN_PENDING;
			receipt->evidence = 0;
			receipt->quiesced_attempt = 0;
		}
		if (f->late)
			receipt->evidence &= ~QKX_SHUTDOWN_NO_LATE_COMPLETION;
		if (f->rollback_failure && (!f->rollback_failure_at ||
		    f->undos == (unsigned int)f->rollback_failure_at))
			return -17;
	} else if (f->reopens == (unsigned int)f->reopen_failure_at) {
		return -29;
	}
	return 0;
}

static int begin(void *c, const struct qkx_shutdown_key *k, struct qkx_shutdown_receipt *r)
{ return operation(c, k, r, 1); }
static int poll(void *c, const struct qkx_shutdown_key *k, struct qkx_shutdown_receipt *r)
{ return operation(c, k, r, 0); }

static void init(struct fixture *f)
{
	unsigned int i;
	memset(f, 0, sizeof(*f));
	f->now = 10;
	f->tick = 1;
	f->plan.epoch = 33;
	f->plan.transaction = 77;
	f->plan.forward_budget_ns = 1000;
	f->plan.rollback_budget_ns = 1000;
	f->plan.forward_poll_limit = 30;
	f->plan.rollback_poll_limit = 30;
	f->plan.now_ns = now_ns;
	f->plan.clock_context = f;
	for (i = 0; i < QKX_SHUTDOWN_SUBSYSTEMS; i++) {
		f->owners[i].f = f;
		f->owners[i].index = i;
		f->backends[i].subsystem = (enum qkx_shutdown_subsystem)i;
		f->backends[i].context = &f->owners[i];
		f->backends[i].inspect = inspect;
		f->backends[i].begin = begin;
		f->backends[i].poll = poll;
		f->backends[i].rollback_begin = begin;
		f->backends[i].rollback_poll = poll;
		f->inventory[i].client = 100 + i;
		f->inventory[i].epoch = 33;
		f->inventory[i].capabilities = ALL_ACTIONS;
		f->inventory[i].rollback_capabilities = ALL_ACTIONS;
		f->inventory[i].inventory_complete = true;
		f->inventory[i].ownership_complete = true;
		f->inventory[i].recovery_verified = true;
		f->inventory[i].nonblocking = true;
		f->inventory[i].admission_owned = true;
	}
}

static int run(struct fixture *f)
{
	return qkx_shutdown_run(&f->plan, f->backends, QKX_SHUTDOWN_SUBSYSTEMS, &f->result);
}

int main(int argc, char **argv)
{
	struct fixture f;
	unsigned int i, j;
	int ret;
	if (argc != 2)
		return 2;
	init(&f);
	if (!strcmp(argv[1], "validate")) {
		CHECK(qkx_shutdown_validate(&f.plan, f.backends, 10) == 0);
		CHECK(f.inspections == 10 && !f.calls && !f.clocks);
		CHECK(qkx_shutdown_validate(&f.plan, f.backends, 9) < 0 && !f.calls);
		return 0;
	}
	if (!strcmp(argv[1], "preconditions")) {
		for (i = 0; i < 10; i++) {
			for (j = 0; j < 13; j++) {
				init(&f);
				switch (j) {
				case 0: f.inventory[i].capabilities = 0; break;
				case 1: f.inventory[i].rollback_capabilities = 0; break;
				case 2: f.inventory[i].inventory_complete = false; break;
				case 3: f.inventory[i].ownership_complete = false; break;
				case 4: f.inventory[i].recovery_verified = false; break;
				case 5: f.inventory[i].nonblocking = false; break;
				case 6: f.inventory[i].epoch++; break;
				case 7: f.backends[i].rollback_begin = NULL; break;
				case 8: f.backends[i].rollback_poll = NULL; break;
				case 9: f.backends[i].poll = NULL; break;
				case 10: f.inventory[i].client = 0; break;
				case 11: f.inventory[i].admission_owned = false; break;
				case 12: f.backends[i].subsystem = QKX_SHUTDOWN_SUBSYSTEMS; break;
				}
				CHECK(run(&f) < 0 && !f.calls && !f.clocks && !f.result.attempted);
			}
		}
		return 0;
	}
	if (!strcmp(argv[1], "order")) {
		f.pending = 1;
		CHECK(run(&f) == 0 && f.result.complete);
		CHECK(f.begins == 26 && f.result.completed == 26 && f.calls == 52);
		for (i = 0; i < 7; i++) {
			CHECK(f.log[2*i].subsystem == (enum qkx_shutdown_subsystem)i);
			CHECK(f.log[2*i].action == QKX_SHUTDOWN_CLOSE);
			CHECK(f.log[2*(8+i)].subsystem == (enum qkx_shutdown_subsystem)i);
			CHECK(f.log[2*(8+i)].action == QKX_SHUTDOWN_DRAIN);
			CHECK(f.log[2*(15+i)].subsystem == (enum qkx_shutdown_subsystem)i);
			CHECK(f.log[2*(15+i)].action == QKX_SHUTDOWN_STOP);
		}
		for (i = 0; i < 26; i++) CHECK(f.log[2*i].attempt == i+1);
		CHECK(f.log[14].subsystem == QKX_SHUTDOWN_AOP && f.log[14].action == QKX_SHUTDOWN_CLOSE);
		CHECK(f.log[44].subsystem == QKX_SHUTDOWN_SMMU && f.log[44].action == QKX_SHUTDOWN_RELEASE);
		CHECK(f.log[46].subsystem == QKX_SHUTDOWN_SECURE);
		CHECK(f.log[48].subsystem == QKX_SHUTDOWN_AOP && f.log[48].action == QKX_SHUTDOWN_DRAIN);
		CHECK(f.log[50].subsystem == QKX_SHUTDOWN_AOP && f.log[50].action == QKX_SHUTDOWN_RETIRE);
		return 0;
	}
	if (!strcmp(argv[1], "evidence")) {
		f.zero_receipt = 1;
		CHECK(run(&f) == -EPROTO && !f.result.complete && f.result.rolled_back == 1);
		init(&f); f.bad_evidence = 1;
		CHECK(run(&f) == -EPROTO && !f.result.complete);
		return 0;
	}
	if (!strcmp(argv[1], "identity")) {
		for (i = 1; i <= 8; i++) {
			init(&f); f.bad_identity = (int)i;
			CHECK(run(&f) == -EPROTO && !f.result.complete);
		}
		return 0;
	}
	if (!strcmp(argv[1], "errors")) {
		f.fail_at = 1; f.fail_return = -123; f.raw_error = -456;
		CHECK(run(&f) == -123 && f.result.backend_return == -123 && f.result.backend_raw_error == -456);
		init(&f); f.fail_at = 1; f.raw_error = -456;
		CHECK(run(&f) == -456 && f.result.backend_return == 0 && f.result.backend_raw_error == -456);
		init(&f); f.fail_at = 1; f.fail_return = 7;
		CHECK(run(&f) == -EPROTO && f.result.backend_return == 7);
		return 0;
	}
	if (!strcmp(argv[1], "inspect_positive") || !strcmp(argv[1], "inspect_negative")) {
		f.inspect_error = !strcmp(argv[1], "inspect_positive") ? 7 : -31;
		f.inspect_error_at = f.inspect_error == 7 ? QKX_SHUTDOWN_GPU : QKX_SHUTDOWN_UFS;
		ret = f.inspect_error == 7 ? -EPROTO : -31;
		CHECK(qkx_shutdown_validate(&f.plan, f.backends, 10) == ret);
		CHECK(!f.calls && !f.clocks);
		f.inspections = 0;
		CHECK(run(&f) == ret && f.result.error == ret);
		CHECK(f.result.failed_stage == QKX_SHUTDOWN_FAILURE_INSPECT);
		CHECK(f.result.inspect_return == f.inspect_error);
		CHECK(f.result.failed.subsystem == (enum qkx_shutdown_subsystem)f.inspect_error_at);
		CHECK(f.result.failed.action == QKX_SHUTDOWN_ACTIONS);
		CHECK(f.result.failed.direction == QKX_SHUTDOWN_NO_DIRECTION);
		CHECK(!f.result.failed.attempt && !f.result.backend_return && !f.result.backend_raw_error);
		CHECK(f.inspections == (unsigned int)f.inspect_error_at + 1);
		CHECK(!f.calls && !f.clocks && !f.result.attempted && !f.result.complete);
		return 0;
	}
	f.fail_at = 10; f.fail_return = -123;
	if (!strcmp(argv[1], "rollback")) {
		CHECK(run(&f) == -123 && !f.result.recovery_required);
		CHECK(f.result.attempted == 10 && f.result.rolled_back == 10 && f.undos == 10 && f.reopens == 8);
		for (i = 0; i < 10; i++) {
			CHECK(f.log[10+i].direction == QKX_SHUTDOWN_UNDO);
			CHECK(f.log[10+i].original_attempt == 10-i);
			CHECK(f.log[10+i].attempt > 10);
		}
		CHECK(f.log[20].direction == QKX_SHUTDOWN_REOPEN);
		CHECK(f.result.admission_reopened_mask == 0x27f);
		return 0;
	}
	if (!strcmp(argv[1], "rollback_failure") || !strcmp(argv[1], "late")) {
		for (i = 1; i <= 10; i++) {
			init(&f); f.fail_at = 10; f.fail_return = -123;
			f.rollback_failure = !strcmp(argv[1], "rollback_failure");
			f.rollback_failure_at = (int)i;
			f.late = !strcmp(argv[1], "late");
			CHECK(run(&f) == -123 && f.result.recovery_required && !f.reopens);
			CHECK(f.result.rollback_error == (f.late ? -EPROTO : -17));
		}
		return 0;
	}
	if (!strcmp(argv[1], "irreversible")) {
		f.check_irreversible = 1;
		f.inventory[1].irreversible_actions = QKX_SHUTDOWN_CAP(QKX_SHUTDOWN_DRAIN);
		CHECK(run(&f) == -123 && f.marker_seen && f.result.irreversible);
		CHECK(f.result.recovery_required && !f.undos && !f.reopens);
		return 0;
	}
	if (!strcmp(argv[1], "after_hook")) {
		f.fail_at = 0; f.after_hook = 1;
		CHECK(run(&f) == -ETIMEDOUT && f.result.attempted == 1 && !f.result.completed);
		CHECK(f.result.rolled_back == 1 && !f.result.recovery_required);
		return 0;
	}
	if (!strcmp(argv[1], "deadline")) {
		f.fail_at = 0; f.plan.forward_budget_ns = 10;
		CHECK(run(&f) == -ETIMEDOUT && f.result.attempted < 26);
		CHECK(f.result.rolled_back == f.result.attempted && !f.result.recovery_required);
		return 0;
	}
	if (!strcmp(argv[1], "frozen")) {
		f.fail_at = 0; f.frozen = 1; f.pending = 1; f.plan.forward_poll_limit = 3;
		CHECK(run(&f) == -ETIMEDOUT && f.result.forward_polls == 3);
		CHECK(f.calls == 6 && f.result.rolled_back == 1 && !f.result.recovery_required);
		return 0;
	}
	if (!strcmp(argv[1], "clock")) {
		f.fail_at = 0; f.backward = 1;
		CHECK(run(&f) == -ERANGE && !f.result.complete);
		init(&f); f.now = ~(u64)0 - 3;
		CHECK(run(&f) == -EOVERFLOW && !f.calls);
		init(&f); f.plan.transaction = 0;
		CHECK(run(&f) < 0 && !f.calls);
		return 0;
	}
	if (!strcmp(argv[1], "rollback_deadline")) {
		f.plan.rollback_budget_ns = 1;
		ret = run(&f);
		CHECK(ret == -123 && f.result.recovery_required && f.result.rollback_error == -ETIMEDOUT);
		CHECK(!f.reopens);
		return 0;
	}
	if (!strcmp(argv[1], "each_failure")) {
		for (i = 1; i <= 26; i++) {
			init(&f); f.fail_at = (int)i; f.fail_return = -123;
			CHECK(run(&f) == -123 && !f.result.recovery_required);
			CHECK(f.result.attempted == i && f.result.rolled_back == i);
			CHECK(f.result.completed == i-1 && f.undos == i);
			CHECK(f.reopens == (i < 8 ? i : 8));
		}
		return 0;
	}
	if (!strcmp(argv[1], "shared_polls")) {
		f.fail_at = 0; f.pending = 1; f.plan.forward_poll_limit = 3;
		CHECK(run(&f) == -ETIMEDOUT && f.result.forward_polls == 3);
		CHECK(f.result.attempted == 4 && f.result.rolled_back == 4 && !f.result.recovery_required);
		return 0;
	}
	if (!strcmp(argv[1], "rollback_pending")) {
		f.rollback_pending = 1; f.plan.rollback_poll_limit = 3;
		CHECK(run(&f) == -123 && f.result.recovery_required && !f.reopens);
		CHECK(f.result.rollback_polls == 3 && f.result.rolled_back == 3);
		init(&f); f.fail_at = 1; f.fail_return = -123;
		f.rollback_pending = 1; f.rollback_frozen = 1; f.frozen = 1;
		f.plan.rollback_poll_limit = 3;
		CHECK(run(&f) == -123 && f.result.recovery_required && !f.reopens);
		CHECK(f.result.rollback_error == -ETIMEDOUT && f.result.rollback_polls == 3);
		return 0;
	}
	if (!strcmp(argv[1], "reopen_failure")) {
		f.reopen_failure_at = 3;
		CHECK(run(&f) == -123 && f.result.recovery_required && f.result.rollback_error == -29);
		CHECK(f.result.admission_reopened == 2 && f.result.admission_reopened_mask == 0x240);
		CHECK(f.reopens == 3);
		return 0;
	}
	if (!strcmp(argv[1], "rollback_overflow")) {
		f.now = ~(u64)0 - 200; f.plan.forward_budget_ns = 100;
		CHECK(run(&f) == -123 && f.result.recovery_required && f.result.rollback_error == -EOVERFLOW);
		CHECK(!f.undos && !f.result.rollback_backend_return && !f.result.rollback_raw_error);
		return 0;
	}
	if (!strcmp(argv[1], "invalid")) {
		f.inspect_error = -31;
		CHECK(run(&f) == -31 && !f.calls && !f.clocks);
		init(&f); f.clock_error_at = 1; f.clock_error = -32;
		CHECK(run(&f) == -32 && !f.calls);
		init(&f); f.clock_error_at = 3; f.clock_error = -32;
		CHECK(run(&f) == -32 && f.result.rolled_back == 1 && !f.result.recovery_required);
		init(&f); f.plan.forward_poll_limit = QKX_SHUTDOWN_MAX_POLLS + 1;
		CHECK(run(&f) == -EINVAL && !f.calls);
		init(&f); f.plan.rollback_budget_ns = 0;
		CHECK(run(&f) == -EINVAL && !f.calls);
		CHECK(qkx_shutdown_run(NULL, NULL, 0, &f.result) == -EINVAL);
		CHECK(qkx_shutdown_run(&f.plan, f.backends, 10, NULL) == -EINVAL);
		return 0;
	}
	return 2;
}
