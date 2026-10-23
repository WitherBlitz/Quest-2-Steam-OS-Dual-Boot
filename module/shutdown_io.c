// SPDX-License-Identifier: GPL-2.0
#include <linux/errno.h>
#include <linux/string.h>

#include "shutdown_io.h"

static int qkx_sd_io_check(const struct qkx_sd_io_prerequisites *proof,
		u64 epoch)
{
	if (!proof)
		return -EINVAL;
	if (!proof->binding_supported)
		return -EOPNOTSUPP;
	if (!epoch || proof->epoch != epoch)
		return -ESTALE;
	if (!proof->owner_verified || !proof->admission_closed ||
	    !proof->power_active || !proof->lock_stable)
		return -EPERM;
	return 0;
}

static int qkx_sd_io_finish(struct qkx_sd_io_poll *poll, int result)
{
	poll->terminal = true;
	poll->result = result;
	poll->matched = result == 0;
	return result;
}

int qkx_sd_io_poll_init(struct qkx_sd_io_poll *poll,
		enum qkx_sd_io_probe probe,
		const struct qkx_sd_io_prerequisites *proof, u64 expected_epoch,
		u64 start_us, u64 timeout_us, u32 read_cap)
{
	int ret;

	if (!poll)
		return -EINVAL;
	if (poll->initialized)
		return -EALREADY;
	ret = qkx_sd_io_check(proof, expected_epoch);
	if (ret)
		return ret;
	if (timeout_us > ~(u64)0 - start_us)
		return -EOVERFLOW;
	if (probe < QKX_SD_IO_DWC3_HALT || probe > QKX_SD_IO_UFS_DISABLED ||
	    !timeout_us || !read_cap || read_cap > QKX_SD_IO_MAX_READS ||
	    (probe == QKX_SD_IO_UFS_DRAIN && read_cap < 2))
		return -EINVAL;
	memset(poll, 0, sizeof(*poll));
	poll->probe = probe;
	poll->epoch = expected_epoch;
	poll->deadline_us = start_us + timeout_us;
	poll->last_now_us = start_us;
	poll->read_cap = read_cap;
	poll->initialized = true;
	poll->result = QKX_SD_IO_PENDING;
	return 0;
}

static int qkx_sd_io_clock(struct qkx_sd_io_poll *poll,
		const struct qkx_sd_io_ops *ops)
{
	u64 now = 0;
	int ret;

	ret = ops->now_us(ops->context, &now);
	poll->last_clock_result = ret;
	if (ret)
		return ret < 0 ? ret : -EPROTO;
	if (now < poll->last_now_us)
		return -ERANGE;
	poll->last_now_us = now;
	if (now > poll->deadline_us)
		return -ETIMEDOUT;
	return 0;
}

int qkx_sd_io_poll_step(struct qkx_sd_io_poll *poll,
		const struct qkx_sd_io_prerequisites *proof,
		const struct qkx_sd_io_ops *ops)
{
	u32 offsets[2], count, i, value;
	bool matched;
	int ret;

	if (!poll || !poll->initialized)
		return -EINVAL;
	if (poll->terminal)
		return poll->result;
	ret = qkx_sd_io_check(proof, poll->epoch);
	if (ret)
		return qkx_sd_io_finish(poll, ret);
	if (!ops || !ops->read32 || !ops->now_us)
		return qkx_sd_io_finish(poll, -EINVAL);
	count = poll->probe == QKX_SD_IO_UFS_DRAIN ? 2 : 1;
	if (poll->read_count > poll->read_cap ||
	    count > poll->read_cap - poll->read_count)
		return qkx_sd_io_finish(poll, -ETIMEDOUT);
	offsets[0] = poll->probe == QKX_SD_IO_DWC3_HALT ?
		QKX_SD_IO_DWC3_DSTS : poll->probe == QKX_SD_IO_UFS_DRAIN ?
		QKX_SD_IO_UFS_TM_DOORBELL : QKX_SD_IO_UFS_HCE;
	offsets[1] = QKX_SD_IO_UFS_TR_DOORBELL;
	poll->valid_mask = 0;
	for (i = 0; i < count; i++) {
		ret = qkx_sd_io_clock(poll, ops);
		if (ret)
			return qkx_sd_io_finish(poll, ret);
		ret = qkx_sd_io_check(proof, poll->epoch);
		if (ret)
			return qkx_sd_io_finish(poll, ret);
		poll->read_count++;
		ret = ops->read32(ops->context, offsets[i], &value);
		poll->last_read_result = ret;
		if (ret)
			return qkx_sd_io_finish(poll, ret < 0 ? ret : -EPROTO);
		poll->last_values[i] = value;
		poll->valid_mask |= 1U << i;
		ret = qkx_sd_io_clock(poll, ops);
		if (ret)
			return qkx_sd_io_finish(poll, ret);
		ret = qkx_sd_io_check(proof, poll->epoch);
		if (ret)
			return qkx_sd_io_finish(poll, ret);
	}
	if (poll->probe == QKX_SD_IO_DWC3_HALT)
		matched = !!(poll->last_values[0] & QKX_SD_IO_DWC3_DEVCTRLHLT);
	else if (poll->probe == QKX_SD_IO_UFS_DRAIN)
		matched = !poll->last_values[0] && !poll->last_values[1];
	else
		matched = !(poll->last_values[0] & QKX_SD_IO_UFS_HCE_ENABLE);
	if (matched)
		return qkx_sd_io_finish(poll, 0);
	if (poll->last_now_us == poll->deadline_us ||
	    poll->read_count == poll->read_cap)
		return qkx_sd_io_finish(poll, -ETIMEDOUT);
	return QKX_SD_IO_PENDING;
}

int qkx_sd_io_aop_evaluate(const struct qkx_sd_io_aop_input *input,
		struct qkx_sd_io_aop_receipt *receipt)
{
	int ret;

	if (!input || !receipt)
		return -EINVAL;
	memset(receipt, 0, sizeof(*receipt));
	receipt->raw = *input;
	/* No binding or supplied ACK can enable the unimplemented peer protocol. */
	if (input->request == QKX_SD_IO_AOP_LINK_DOWN) {
		ret = -EOPNOTSUPP;
		goto out;
	}
	if (!input->binding_supported) {
		ret = -EOPNOTSUPP;
		goto out;
	}
	if ((input->selected_transport != QKX_SD_IO_AOP_IPCC &&
	     input->selected_transport != QKX_SD_IO_AOP_MMIO) ||
	    (input->request != QKX_SD_IO_AOP_CHANNEL_CLOSE &&
	     input->request != QKX_SD_IO_AOP_LINK_DOWN)) {
		ret = -EINVAL;
		goto out;
	}
	if (input->selected_transport != input->observed_transport) {
		ret = -EXDEV;
		goto out;
	}
	if (!input->expected_epoch || !input->expected_transaction ||
	    input->expected_epoch != input->observed_epoch ||
	    input->expected_transaction != input->observed_transaction ||
	    input->expected_channel != input->observed_channel) {
		ret = -ESTALE;
		goto out;
	}
	if (input->enqueue_attempted && input->enqueue_result < 0) {
		ret = input->enqueue_result;
		goto out;
	}
	if (input->selected_transport == QKX_SD_IO_AOP_MMIO &&
	    input->enqueue_attempted && input->enqueue_result > 0) {
		ret = -EPROTO;
		goto out;
	}
	receipt->enqueue_accepted = input->enqueue_attempted;
	if (!input->enqueue_attempted && input->remote_channel_ack) {
		ret = -EPROTO;
		goto out;
	}
	ret = QKX_SD_IO_PENDING;
	if (receipt->enqueue_accepted && input->remote_channel_ack) {
		receipt->channel_acknowledged = true;
		ret = 0;
	}
out:
	receipt->result = ret;
	return ret;
}
