// SPDX-License-Identifier: GPL-2.0-only
/* Source-integrated entry accounting, not a hardware ownership backend. */
#include <linux/ktime.h>
#include <linux/spinlock.h>
#include <linux/export.h>
#ifdef QKX_OWNER_SELFTEST
#include "owner_gate.h"
#else
#include <linux/qkx_owner_gate.h>
#endif

/* QOG_HOST_BEGIN */
struct qkx_owner_state {
	u64 epoch, next_fence, fence_id, fence_epoch, deadline, last_now;
	u32 active[QKX_OWNER_KINDS];
	bool poisoned;
};
static struct qkx_owner_state qkx_owner_states[QKX_OWNER_DOMAINS] = {
	{ .epoch = 1 }, { .epoch = 1 }
};
static DEFINE_SPINLOCK(qkx_owner_lock);

static void qkx_owner_poison(struct qkx_owner_state *state)
{
	state->poisoned = true;
	state->fence_id = 0;
}

static bool qkx_owner_advance(struct qkx_owner_state *state)
{
	if (state->epoch == ~(u64)0) {
		qkx_owner_poison(state);
		return false;
	}
	state->epoch++;
	return true;
}

static void qkx_owner_expire(struct qkx_owner_state *state, u64 now)
{
	if (now < state->last_now)
		qkx_owner_poison(state);
	state->last_now = now;
	if (state->fence_id && now >= state->deadline) {
		state->fence_id = 0;
		qkx_owner_advance(state);
	}
}

int qkx_owner_enter(unsigned int domain, unsigned int kind,
		    struct qkx_owner_token *token)
{
	struct qkx_owner_state *state;
	unsigned long flags;
	u64 now;
	int result = 0;

	if (!token || token->counted || domain >= QKX_OWNER_DOMAINS || kind >= QKX_OWNER_KINDS)
		return -EINVAL;
	spin_lock_irqsave(&qkx_owner_lock, flags);
	now = ktime_get_ns();
	state = &qkx_owner_states[domain];
	qkx_owner_expire(state, now);
	if (kind == QKX_OWNER_SUBMIT && state->fence_id) {
		result = -EBUSY;
		goto out;
	}
	if (state->active[kind] == ~(u32)0 || !qkx_owner_advance(state))
		qkx_owner_poison(state);
	if (state->poisoned) {
		/* An uncountable completion/close must still execute. No fence can
		 * be acquired after accounting has become unreliable.
		 */
		if (kind == QKX_OWNER_SUBMIT) result = -EOVERFLOW;
		goto out;
	}
	state->active[kind]++;
	token->domain = domain;
	token->kind = kind;
	token->counted = true;
out:
	spin_unlock_irqrestore(&qkx_owner_lock, flags);
	return result;
}

void qkx_owner_leave(struct qkx_owner_token *token)
{
	struct qkx_owner_state *state;
	unsigned long flags;
	u64 now;

	if (!token || !token->counted) return;
	spin_lock_irqsave(&qkx_owner_lock, flags);
	now = ktime_get_ns();
	if (token->domain >= QKX_OWNER_DOMAINS || token->kind >= QKX_OWNER_KINDS) {
		unsigned int i;
		for (i = 0; i < QKX_OWNER_DOMAINS; i++) qkx_owner_poison(&qkx_owner_states[i]);
		goto out;
	}
	state = &qkx_owner_states[token->domain];
	qkx_owner_expire(state, now);
	if (!state->active[token->kind]) qkx_owner_poison(state);
	else state->active[token->kind]--;
	qkx_owner_advance(state);
out:
	token->counted = false;
	spin_unlock_irqrestore(&qkx_owner_lock, flags);
}

int qkx_owner_snapshot(unsigned int domain, struct qkx_owner_view *view)
{
	struct qkx_owner_state *state;
	unsigned long flags;
	u64 now;

	if (domain >= QKX_OWNER_DOMAINS || !view) return -EINVAL;
	spin_lock_irqsave(&qkx_owner_lock, flags);
	now = ktime_get_ns();
	state = &qkx_owner_states[domain];
	qkx_owner_expire(state, now);
	*view = (struct qkx_owner_view) {
		.epoch = state->epoch, .fence_id = state->fence_id, .deadline = state->deadline,
		.active_submit = state->active[QKX_OWNER_SUBMIT],
		.active_lifecycle = state->active[QKX_OWNER_LIFECYCLE],
		.admission_closed = state->fence_id != 0, .poisoned = state->poisoned,
		.entry_fence_stable = state->fence_id && !state->poisoned &&
			state->epoch == state->fence_epoch && !state->active[0] && !state->active[1]
	};
	spin_unlock_irqrestore(&qkx_owner_lock, flags);
	return 0;
}

int qkx_owner_try_fence(unsigned int domain, u64 duration, struct qkx_owner_fence *fence)
{
	struct qkx_owner_state *state;
	unsigned long flags;
	u64 now;
	int result = 0;

	if (!fence || domain >= QKX_OWNER_DOMAINS || !duration || duration > QKX_OWNER_MAX_FENCE_NS)
		return -EINVAL;
	*fence = (struct qkx_owner_fence) { 0 };
	spin_lock_irqsave(&qkx_owner_lock, flags);
	now = ktime_get_ns();
	state = &qkx_owner_states[domain];
	qkx_owner_expire(state, now);
	if (state->poisoned || state->next_fence == ~(u64)0 || now > ~(u64)0 - duration) {
		qkx_owner_poison(state);
		result = -EOVERFLOW;
	} else if (state->fence_id || state->active[0] || state->active[1]) result = -EBUSY;
	else {
		state->fence_id = ++state->next_fence;
		state->fence_epoch = state->epoch;
		state->deadline = now + duration;
		*fence = (struct qkx_owner_fence) {
			.domain = domain, .id = state->fence_id,
			.epoch = state->epoch, .deadline = state->deadline
		};
	}
	spin_unlock_irqrestore(&qkx_owner_lock, flags);
	return result;
}

int qkx_owner_reopen(const struct qkx_owner_fence *fence)
{
	struct qkx_owner_state *state;
	unsigned long flags;
	u64 now;
	int result = 0;

	if (!fence || fence->domain >= QKX_OWNER_DOMAINS || !fence->id) return -EINVAL;
	spin_lock_irqsave(&qkx_owner_lock, flags);
	now = ktime_get_ns();
	state = &qkx_owner_states[fence->domain];
	qkx_owner_expire(state, now);
	if (state->fence_id != fence->id || state->deadline != fence->deadline ||
	    state->fence_epoch != fence->epoch) result = -ESTALE;
	else { state->fence_id = 0; qkx_owner_advance(state); }
	spin_unlock_irqrestore(&qkx_owner_lock, flags);
	return result;
}
/* QOG_HOST_END */
#ifndef QKX_OWNER_SELFTEST
EXPORT_SYMBOL_GPL(qkx_owner_snapshot);
#endif
