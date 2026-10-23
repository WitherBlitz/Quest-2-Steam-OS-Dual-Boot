/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef QKX_SHUTDOWN_H
#define QKX_SHUTDOWN_H

#include <linux/types.h>

#define QKX_SHUTDOWN_PRODUCERS 7
#define QKX_SHUTDOWN_STEPS 26
#define QKX_SHUTDOWN_MAX_POLLS 4096

enum qkx_shutdown_subsystem {
	QKX_SHUTDOWN_CAMERA, QKX_SHUTDOWN_DSP, QKX_SHUTDOWN_GPU,
	QKX_SHUTDOWN_SYNCBOSS, QKX_SHUTDOWN_USB, QKX_SHUTDOWN_UFS,
	QKX_SHUTDOWN_OTHER_DMA, QKX_SHUTDOWN_SMMU, QKX_SHUTDOWN_SECURE,
	QKX_SHUTDOWN_AOP, QKX_SHUTDOWN_SUBSYSTEMS
};

enum qkx_shutdown_action {
	QKX_SHUTDOWN_CLOSE, QKX_SHUTDOWN_DRAIN, QKX_SHUTDOWN_STOP,
	QKX_SHUTDOWN_RELEASE, QKX_SHUTDOWN_RETIRE, QKX_SHUTDOWN_ACTIONS
};

enum qkx_shutdown_direction {
	QKX_SHUTDOWN_FORWARD, QKX_SHUTDOWN_UNDO, QKX_SHUTDOWN_REOPEN,
	QKX_SHUTDOWN_NO_DIRECTION
};

enum qkx_shutdown_failure_stage {
	QKX_SHUTDOWN_FAILURE_NONE, QKX_SHUTDOWN_FAILURE_PREFLIGHT,
	QKX_SHUTDOWN_FAILURE_INSPECT, QKX_SHUTDOWN_FAILURE_FORWARD
};

enum qkx_shutdown_state {
	QKX_SHUTDOWN_PENDING = 1, QKX_SHUTDOWN_COMPLETE
};

#define QKX_SHUTDOWN_CAP(action) (1U << (action))
#define QKX_SHUTDOWN_ADMISSION_CLOSED (1U << 0)
#define QKX_SHUTDOWN_DRAINED (1U << 1)
#define QKX_SHUTDOWN_STOPPED (1U << 2)
#define QKX_SHUTDOWN_RELEASED (1U << 3)
#define QKX_SHUTDOWN_RETIRED (1U << 4)
#define QKX_SHUTDOWN_RESTORED (1U << 5)
#define QKX_SHUTDOWN_NO_LATE_COMPLETION (1U << 6)
#define QKX_SHUTDOWN_ADMISSION_REOPENED (1U << 7)

struct qkx_shutdown_key {
	u64 client;
	u64 transaction;
	u64 epoch;
	u64 attempt;
	u64 original_attempt;
	enum qkx_shutdown_subsystem subsystem;
	enum qkx_shutdown_action action;
	enum qkx_shutdown_direction direction;
};

struct qkx_shutdown_receipt {
	struct qkx_shutdown_key key;
	enum qkx_shutdown_state state;
	unsigned int evidence;
	u64 quiesced_attempt;
	int raw_error;
};

struct qkx_shutdown_inventory {
	u64 client;
	u64 epoch;
	unsigned int capabilities;
	unsigned int rollback_capabilities;
	unsigned int irreversible_actions;
	bool inventory_complete;
	bool ownership_complete;
	bool recovery_verified;
	bool nonblocking;
	/* Includes AOP submitters, role/restart work and other admission paths. */
	bool admission_owned;
};

/*
 * All hooks must return promptly; the coordinator cannot bound a blocking hook.
 * inspect is read-only. Arrays are indexed by enum qkx_shutdown_subsystem.
 * UNDO must cancel/quiesce the original attempt and exclude late completion.
 * UNDO(CLOSE) leaves admission closed; only REOPEN may reopen admission.
 * Completion evidence is an owner adapter's checked assertion, never a raw
 * driver zero or an advisory observation. No stock adapter is provided here.
 * DRAIN begins source-owned stream/producer quiescence, then waits for its
 * outstanding requests/workers/frames; STOP is the final controller stop.
 * AOP CLOSE fences outside submitters/restart work while retaining the owned
 * teardown control channel. Its final DRAIN occurs just before RETIRE.
 * Inventory/ownership/recovery attestations must remain valid for the entire
 * transaction. Hardware adapters must enforce that ownership, not just observe
 * a snapshot. A partial REOPEN failure requires recovery; the result preserves
 * which admissions have already reopened and makes no atomic-reopen claim.
 */
struct qkx_shutdown_backend {
	enum qkx_shutdown_subsystem subsystem;
	void *context;
	int (*inspect)(void *context, struct qkx_shutdown_inventory *inventory);
	int (*begin)(void *context, const struct qkx_shutdown_key *key,
		     struct qkx_shutdown_receipt *receipt);
	int (*poll)(void *context, const struct qkx_shutdown_key *key,
		    struct qkx_shutdown_receipt *receipt);
	int (*rollback_begin)(void *context, const struct qkx_shutdown_key *key,
			      struct qkx_shutdown_receipt *receipt);
	int (*rollback_poll)(void *context, const struct qkx_shutdown_key *key,
			     struct qkx_shutdown_receipt *receipt);
};

struct qkx_shutdown_plan {
	u64 epoch;
	/* Caller supplies a fresh, nonzero transaction and exclusive backend use. */
	u64 transaction;
	u64 forward_budget_ns;
	u64 rollback_budget_ns;
	unsigned int forward_poll_limit;
	unsigned int rollback_poll_limit;
	int (*now_ns)(void *context, u64 *now);
	void *clock_context;
};

struct qkx_shutdown_result {
	int error;
	enum qkx_shutdown_failure_stage failed_stage;
	/* Original inspect callback return; no action callback was issued. */
	int inspect_return;
	int backend_return;
	int backend_raw_error;
	int rollback_error;
	int rollback_backend_return;
	int rollback_raw_error;
	/* Pre-action failures use ACTIONS/NO_DIRECTION sentinels. The subsystem
	 * is SUBSYSTEMS unless an inspection callback identified its owner.
	 */
	struct qkx_shutdown_key failed;
	unsigned int attempted;
	unsigned int completed;
	unsigned int rolled_back;
	unsigned int admission_reopened;
	unsigned int admission_reopened_mask;
	unsigned int forward_polls;
	unsigned int rollback_polls;
	bool irreversible;
	bool recovery_required;
	bool complete;
};

/* Validation invokes only inspect; it never samples the clock or issues work. */
int qkx_shutdown_validate(const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_backend *backends, unsigned int count);
int qkx_shutdown_run(const struct qkx_shutdown_plan *plan,
	const struct qkx_shutdown_backend *backends, unsigned int count,
	struct qkx_shutdown_result *result);

#endif
