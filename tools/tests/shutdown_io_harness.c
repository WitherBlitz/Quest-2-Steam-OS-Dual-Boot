/* Executes production shutdown_io.c against nonblocking read/clock doubles. */
#include <stdio.h>
#include <string.h>
#include <linux/errno.h>
#include "shutdown_io.h"

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%d: %s\n", __LINE__, #c); return 1; } } while (0)

struct fixture {
	struct qkx_sd_io_prerequisites proof;
	u64 now;
	u32 values[2];
	u32 offsets[8];
	unsigned int reads;
	unsigned int clocks;
	unsigned int fail_read;
	int read_error;
	int clock_error;
	bool late;
	bool rollback;
	bool change_epoch;
};

static int now_us(void *context, u64 *now)
{
	struct fixture *f = context;

	f->clocks++;
	*now = f->now;
	return f->clock_error;
}

static int read32(void *context, u32 offset, u32 *value)
{
	struct fixture *f = context;

	f->offsets[f->reads % 8] = offset;
	f->reads++;
	*value = f->values[offset == QKX_SD_IO_UFS_TR_DOORBELL];
	if (f->late)
		f->now = 11;
	if (f->rollback)
		f->now = 0;
	if (f->change_epoch)
		f->proof.epoch++;
	return f->reads == f->fail_read ? f->read_error : 0;
}

static struct fixture ready(void)
{
	struct fixture f = {0};

	f.proof.binding_supported = true;
	f.proof.owner_verified = true;
	f.proof.admission_closed = true;
	f.proof.power_active = true;
	f.proof.lock_stable = true;
	f.proof.epoch = 7;
	return f;
}

static int init(struct qkx_sd_io_poll *p, struct fixture *f,
		enum qkx_sd_io_probe probe, u32 cap)
{
	return qkx_sd_io_poll_init(p, probe, &f->proof, 7, 0, 10, cap);
}

static int step(struct qkx_sd_io_poll *p, struct fixture *f)
{
	struct qkx_sd_io_ops ops = {.context = f, .now_us = now_us, .read32 = read32};

	return qkx_sd_io_poll_step(p, &f->proof, &ops);
}

static struct qkx_sd_io_aop_input aop(void)
{
	struct qkx_sd_io_aop_input in = {0};

	in.binding_supported = true;
	in.request = QKX_SD_IO_AOP_CHANNEL_CLOSE;
	in.selected_transport = QKX_SD_IO_AOP_IPCC;
	in.observed_transport = QKX_SD_IO_AOP_IPCC;
	in.expected_epoch = in.observed_epoch = 7;
	in.expected_transaction = in.observed_transaction = 13;
	in.expected_channel = in.observed_channel = 0;
	in.enqueue_attempted = true;
	in.enqueue_result = 3;
	return in;
}

int main(int argc, char **argv)
{
	struct fixture f = ready();
	struct qkx_sd_io_poll p = {0};
	struct qkx_sd_io_aop_input in = aop();
	struct qkx_sd_io_aop_receipt receipt;
	unsigned int i;

	CHECK(argc == 2);
	if (!strcmp(argv[1], "prerequisites")) {
		for (i = 0; i < 6; i++) {
			f = ready(); memset(&p, 0, sizeof(p));
			switch (i) {
			case 0: f.proof.binding_supported = false; break;
			case 1: f.proof.owner_verified = false; break;
			case 2: f.proof.admission_closed = false; break;
			case 3: f.proof.power_active = false; break;
			case 4: f.proof.lock_stable = false; break;
			default: f.proof.epoch++; break;
			}
			CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) < 0);
			CHECK(!f.reads && !f.clocks);
		}
		f = ready(); CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) == 0);
		f.proof.admission_closed = false;
		CHECK(step(&p, &f) == -EPERM && !f.reads && !f.clocks);
	} else if (!strcmp(argv[1], "offsets")) {
		CHECK(QKX_SD_IO_DWC3_DSTS == 0xc70c && QKX_SD_IO_DWC3_DEVCTRLHLT == (1U << 22));
		CHECK(QKX_SD_IO_UFS_TM_DOORBELL == 0x78 && QKX_SD_IO_UFS_TR_DOORBELL == 0x58);
		CHECK(QKX_SD_IO_UFS_HCE == 0x34 && QKX_SD_IO_UFS_HCE_ENABLE == 1);
		CHECK(init(&p, &f, QKX_SD_IO_DWC3_HALT, 2) == 0);
		f.values[0] = QKX_SD_IO_DWC3_DEVCTRLHLT;
		CHECK(step(&p, &f) == 0 && p.matched && f.offsets[0] == 0xc70c);
		memset(&p, 0, sizeof(p)); f = ready();
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 2) == 0);
		CHECK(step(&p, &f) == 0 && f.offsets[0] == 0x78 && f.offsets[1] == 0x58);
		memset(&p, 0, sizeof(p)); f = ready(); f.values[0] = 0x80000000;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, 1) == 0);
		CHECK(step(&p, &f) == 0 && f.offsets[0] == 0x34);
	} else if (!strcmp(argv[1], "deadline")) {
		CHECK(init(&p, &f, QKX_SD_IO_DWC3_HALT, 4) == 0);
		f.now = 9; CHECK(step(&p, &f) == QKX_SD_IO_PENDING);
		f.now = 10; f.values[0] = QKX_SD_IO_DWC3_DEVCTRLHLT;
		CHECK(step(&p, &f) == 0 && p.deadline_us == 10 && p.read_count == 2);
		CHECK(step(&p, &f) == 0 && f.reads == 2);
		memset(&p, 0, sizeof(p)); f = ready();
		CHECK(init(&p, &f, QKX_SD_IO_DWC3_HALT, 4) == 0);
		f.now = 11; CHECK(step(&p, &f) == -ETIMEDOUT && !f.reads);
	} else if (!strcmp(argv[1], "read_cap")) {
		f.values[0] = 1; f.values[1] = 2;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) == 0);
		CHECK(step(&p, &f) == QKX_SD_IO_PENDING);
		CHECK(step(&p, &f) == -ETIMEDOUT && f.reads == 4 && !p.matched);
		CHECK(p.last_values[0] == 1 && p.last_values[1] == 2 && p.valid_mask == 3);
		CHECK(step(&p, &f) == -ETIMEDOUT && f.reads == 4);
	} else if (!strcmp(argv[1], "read_error")) {
		f.values[0] = 5; f.fail_read = 2; f.read_error = -77;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) == 0);
		CHECK(step(&p, &f) == -77 && p.last_read_result == -77 && p.read_count == 2);
		CHECK(p.last_values[0] == 5 && p.valid_mask == 1);
		f.read_error = 0; CHECK(step(&p, &f) == -77 && !p.matched);
	} else if (!strcmp(argv[1], "late_read")) {
		f.late = true; f.values[0] = QKX_SD_IO_DWC3_DEVCTRLHLT;
		CHECK(init(&p, &f, QKX_SD_IO_DWC3_HALT, 4) == 0);
		CHECK(step(&p, &f) == -ETIMEDOUT && !p.matched && p.valid_mask == 1);
		memset(&p, 0, sizeof(p)); f = ready(); f.late = true;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) == 0);
		CHECK(step(&p, &f) == -ETIMEDOUT && f.reads == 1);
	} else if (!strcmp(argv[1], "clock")) {
		f.now = 2; f.rollback = true;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, 4) == 0);
		CHECK(step(&p, &f) == -ERANGE && !p.matched);
		memset(&p, 0, sizeof(p)); f = ready(); f.clock_error = -78;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, 4) == 0);
		CHECK(step(&p, &f) == -78 && p.last_clock_result == -78 && !f.reads);
	} else if (!strcmp(argv[1], "invalid")) {
		CHECK(qkx_sd_io_poll_init(&p, QKX_SD_IO_UFS_DISABLED, &f.proof, 7,
			~(u64)0 - 2, 3, 2) == -EOVERFLOW);
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 1) == -EINVAL);
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, QKX_SD_IO_MAX_READS + 1) == -EINVAL);
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, 4) == 0);
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DISABLED, 4) == -EALREADY);
	} else if (!strcmp(argv[1], "epoch_change")) {
		f.change_epoch = true;
		CHECK(init(&p, &f, QKX_SD_IO_UFS_DRAIN, 4) == 0);
		CHECK(step(&p, &f) == -ESTALE && f.reads == 1 && !p.matched);
	} else if (!strcmp(argv[1], "aop_error")) {
		in.enqueue_result = -ENOBUFS;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -ENOBUFS);
		CHECK(receipt.raw.enqueue_result == -ENOBUFS && !receipt.enqueue_accepted);
		CHECK(receipt.raw.selected_transport == QKX_SD_IO_AOP_IPCC);
		in.enqueue_result = -77;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -77);
	} else if (!strcmp(argv[1], "aop_pending")) {
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == QKX_SD_IO_PENDING);
		CHECK(receipt.enqueue_accepted && !receipt.channel_acknowledged && receipt.raw.enqueue_result == 3);
		in.enqueue_attempted = false; in.remote_channel_ack = true;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -EPROTO);
	} else if (!strcmp(argv[1], "aop_ack")) {
		in.remote_channel_ack = true;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == 0 && receipt.channel_acknowledged);
		in.observed_transaction++; CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -ESTALE);
		in = aop(); in.remote_channel_ack = true; in.observed_epoch++;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -ESTALE);
		in = aop(); in.remote_channel_ack = true; in.observed_channel++;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -ESTALE);
		in = aop(); in.observed_transport = QKX_SD_IO_AOP_MMIO;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -EXDEV);
		in = aop(); in.selected_transport = in.observed_transport = QKX_SD_IO_AOP_MMIO;
		in.enqueue_result = 0;
		in.remote_channel_ack = true;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == 0);
		in.binding_supported = false;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -EOPNOTSUPP);
	} else if (!strcmp(argv[1], "aop_link")) {
		in.request = QKX_SD_IO_AOP_LINK_DOWN;
		in.remote_channel_ack = in.remote_link_ack = true;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -EOPNOTSUPP);
		CHECK(!receipt.channel_acknowledged && receipt.raw.remote_link_ack);
		in.enqueue_result = -77;
		CHECK(qkx_sd_io_aop_evaluate(&in, &receipt) == -EOPNOTSUPP);
		CHECK(receipt.raw.enqueue_result == -77);
	} else {
		return 2;
	}
	return 0;
}
