/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef QKX_OWNER_GATE_H
#define QKX_OWNER_GATE_H
#ifdef QKX_OWNER_HOST_TEST
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint32_t u32;
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
#else
#include <linux/types.h>
#include <linux/errno.h>
#endif

#define QKX_OWNER_MAX_FENCE_NS 100000000ULL
enum qkx_owner_domain { QKX_OWNER_CAMERA, QKX_OWNER_FASTRPC, QKX_OWNER_DOMAINS };
enum qkx_owner_kind { QKX_OWNER_SUBMIT, QKX_OWNER_LIFECYCLE, QKX_OWNER_KINDS };
struct qkx_owner_token { unsigned int domain, kind; bool counted; };
struct qkx_owner_fence { unsigned int domain; u64 id, epoch, deadline; };
struct qkx_owner_view {
	u64 epoch, fence_id, deadline;
	u32 active_submit, active_lifecycle;
	bool admission_closed, entry_fence_stable, poisoned;
};

/* Annotated entries only. No resource inventory, retained-object ownership or
 * DMA/shutdown acknowledgement. LIFECYCLE operations always run. A monolithic
 * SUBMIT ioctl gate also fences STOP/UNMAP commands: no owner-control bypass or
 * checked drain controller is supplied. Only snapshot is exported to modules.
 * Tokens are zero-initialized, private to one call, and left exactly once.
 */
#ifdef CONFIG_QKX_OWNER_GATE
int qkx_owner_enter(unsigned int domain, unsigned int kind, struct qkx_owner_token *token);
void qkx_owner_leave(struct qkx_owner_token *token);
int qkx_owner_snapshot(unsigned int domain, struct qkx_owner_view *view);
/* Private core helpers; deliberately not EXPORT_SYMBOL interfaces. */
int qkx_owner_try_fence(unsigned int domain, u64 duration, struct qkx_owner_fence *fence);
int qkx_owner_reopen(const struct qkx_owner_fence *fence);
#else
static inline int qkx_owner_enter(unsigned int domain, unsigned int kind,
				 struct qkx_owner_token *token)
{
	(void)domain; (void)kind;
	if (token) token->counted = false;
	return 0;
}
static inline void qkx_owner_leave(struct qkx_owner_token *token) { (void)token; }
static inline int qkx_owner_snapshot(unsigned int domain, struct qkx_owner_view *view)
{
	(void)domain; (void)view;
	return -EOPNOTSUPP;
}
#endif
#endif
