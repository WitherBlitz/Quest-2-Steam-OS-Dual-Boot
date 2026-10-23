/* SPDX-License-Identifier: GPL-2.0 */
#ifndef QKX_SHUTDOWN_IO_H
#define QKX_SHUTDOWN_IO_H

#include <linux/types.h>

/* Pinned 6bde5fc source: dwc3/core.h:147,485; ufs/ufshci.h:55,64,69,231.
 * DWC3 is a logical core offset. An adapter using dwc->regs must use
 * dwc3_readl(), which subtracts DWC3_GLOBALS_REGS_START (0xc100).
 */
#define QKX_SD_IO_DWC3_DSTS		0xc70cU
#define QKX_SD_IO_DWC3_DEVCTRLHLT		(1U << 22)
#define QKX_SD_IO_UFS_TM_DOORBELL		0x78U
#define QKX_SD_IO_UFS_TR_DOORBELL		0x58U
#define QKX_SD_IO_UFS_HCE			0x34U
#define QKX_SD_IO_UFS_HCE_ENABLE		1U
#define QKX_SD_IO_MAX_READS		4096U
#define QKX_SD_IO_PENDING			1

enum qkx_sd_io_probe {
	QKX_SD_IO_DWC3_HALT,
	QKX_SD_IO_UFS_DRAIN,
	QKX_SD_IO_UFS_DISABLED,
};

/* Caller-established conditions, not capabilities discovered by these helpers.
 * A stock Hollywood hardware binding is not supplied by this implementation.
 * The caller retains owner/admission/power/lock protection throughout each step.
 */
struct qkx_sd_io_prerequisites {
	bool binding_supported;
	bool owner_verified;
	bool admission_closed;
	bool power_active;
	bool lock_stable;
	u64 epoch;
};

struct qkx_sd_io_ops {
	void *context;
	/* Both callbacks must be nonblocking/atomic-safe and return 0 or -errno.
	 * No PM/hold/work-flush helper belongs here. A deadline cannot interrupt
	 * a blocking callback, stalled bus access or stalled CPU execution.
	 */
	int (*now_us)(void *context, u64 *now);
	int (*read32)(void *context, u32 logical_offset, u32 *value);
};

/* Zero-initialize once. A pending/finished object cannot be reinitialized to
 * extend its deadline or reset its read budget. Raw values are partial samples;
 * valid_mask identifies successful reads in the latest attempted sample.
 */
struct qkx_sd_io_poll {
	enum qkx_sd_io_probe probe;
	u64 epoch;
	u64 deadline_us;
	u64 last_now_us;
	u32 read_cap;
	u32 read_count;
	u32 last_values[2];
	u32 valid_mask;
	int last_read_result;
	int last_clock_result;
	int result;
	bool initialized;
	bool terminal;
	bool matched;
};

int qkx_sd_io_poll_init(struct qkx_sd_io_poll *poll,
		enum qkx_sd_io_probe probe,
		const struct qkx_sd_io_prerequisites *proof, u64 expected_epoch,
		u64 start_us, u64 timeout_us, u32 read_cap);

/* 0 = matched partial register observation; 1 = pending; negative = refusal or
 * original read/clock error. This does not stop endpoints, change link state,
 * certify system DMA quiescence or prove hardware shutdown. No MMIO is written.
 */
int qkx_sd_io_poll_step(struct qkx_sd_io_poll *poll,
		const struct qkx_sd_io_prerequisites *proof,
		const struct qkx_sd_io_ops *ops);

enum qkx_sd_io_aop_transport {
	QKX_SD_IO_AOP_UNBOUND,
	QKX_SD_IO_AOP_IPCC,
	QKX_SD_IO_AOP_MMIO,
};

enum qkx_sd_io_aop_request {
	QKX_SD_IO_AOP_CHANNEL_CLOSE,
	QKX_SD_IO_AOP_LINK_DOWN,
};

struct qkx_sd_io_aop_input {
	bool binding_supported;
	enum qkx_sd_io_aop_request request;
	enum qkx_sd_io_aop_transport selected_transport;
	enum qkx_sd_io_aop_transport observed_transport;
	u64 expected_epoch;
	u64 observed_epoch;
	u64 expected_transaction;
	u64 observed_transaction;
	u32 expected_channel;
	u32 observed_channel;
	bool enqueue_attempted;
	/* IPCC: negative error or accepted token, including zero.
	 * MMIO: adapter notification result (zero or negative), not a token.
	 */
	int enqueue_result;
	/* A binding must establish fresh, transaction-specific observation;
	 * copying old ACK bits and assigning expected IDs is not such evidence.
	 */
	bool remote_channel_ack;
	bool remote_link_ack;
};

struct qkx_sd_io_aop_receipt {
	struct qkx_sd_io_aop_input raw;
	/* These never establish firmware-wide or system DMA shutdown. */
	bool enqueue_accepted;
	bool channel_acknowledged;
	int result;
};

/* Pure result interpretation: never sends, retries, falls back or touches MMIO.
 * LINK_DOWN remains unsupported even when supplied ACK bits happen to match.
 */
int qkx_sd_io_aop_evaluate(const struct qkx_sd_io_aop_input *input,
		struct qkx_sd_io_aop_receipt *receipt);

#endif
