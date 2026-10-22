// SPDX-License-Identifier: GPL-2.0
/* One observational snapshot. No camera/DSP control, hooks, or handoff. */
#include <linux/device.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/utsname.h>
#include <linux/workqueue.h>
#include <soc/qcom/subsystem_restart.h>
#include "diag_profile.h"

extern bool cam_req_mgr_is_open(void);
static bool observe;
module_param(observe, bool, 0);
MODULE_PARM_DESC(observe, "Explicitly request one read-only advisory snapshot");

/* QCD_HOST_BEGIN */
#define QCD_RELEASE "4.19.325-cip128-st12-g49638c7a8637"
#define QCD_MAX_SESSIONS 8
#define QCD_LINKS 7
#define QCD_MAX_DSP 16
#define QCD_ANCHOR_BYTES 68

struct qcd_link {
	unsigned int session, slot;
	bool busy, request_busy, fault, queue, workq;
	int pending, free, tasks, open, slots, rd, wr, applied;
	unsigned int work;
};

struct qcd_dsp {
	char name[64];
	int refs, state, crashes, level;
	bool group, fault;
	unsigned int work, restart_work;
};

struct qcd_snapshot {
	void *cam_address, *core_slot;
	const char *camera_status;
	int cam_open, cam_subdevs;
	bool cam_shutdown, camera_fault, camera_truncated, dsp_truncated;
	unsigned int sessions, link_count, dsp_count;
	struct qcd_link links[QCD_MAX_SESSIONS * QCD_LINKS];
	struct qcd_dsp dsps[QCD_MAX_DSP];
};

static int qcd_read(void *out, const void *base, unsigned int offset, size_t size)
{
	if (!base)
		return -EFAULT;
	return probe_kernel_read(out, (const char *)base + offset, size);
}

static u64 qcd_hash(const u8 *data, size_t size)
{
	u64 hash = 0xcbf29ce484222325ULL;
	size_t i;

	for (i = 0; i < size; i++)
		hash = (hash ^ data[i]) * 0x100000001b3ULL;
	return hash;
}

static int qcd_anchor(const void *address)
{
	u8 code[QCD_ANCHOR_BYTES];

	if (qcd_read(code, address, 0, sizeof(code)))
		return -EFAULT;
	return qcd_hash(code, sizeof(code)) == 0xa679ec9abcc4670aULL ? 0 : -EINVAL;
}

static int qcd_target(void)
{
	const char *model;

	if (!observe || strcmp(init_uts_ns.name.release, QCD_RELEASE) ||
	    !of_root || of_property_read_string(of_root, "model", &model) ||
	    strncmp(model, "Hollywood", 9) || !of_machine_is_compatible("qcom,kona"))
		return -EINVAL;
	return 0;
}

/* Caller holds CRM and link locks: link/workq/queue lifetimes are pinned.
 * Request workers take req.lock before session.lock, so never take session.lock.
 */
static void qcd_link_snapshot(void *link, struct qcd_link *out)
{
	struct mutex *lock = (void *)((char *)link + QCD_LINK_LOCK);
	struct mutex *req_lock = (void *)((char *)link + QCD_LINK_REQUEST + QCD_REQUEST_LOCK);
	void *workq = NULL, *queue = NULL;

	if (!mutex_trylock(lock)) {
		out->busy = true;
		return;
	}
	if (qcd_read(&workq, link, QCD_LINK_WORKQ, sizeof(workq))) {
		out->fault = true;
		goto unlock_link;
	}
	if (workq) {
		out->workq = true;
		out->fault = qcd_read(&out->pending, workq, QCD_WQ_PENDING, 4) ||
			qcd_read(&out->free, workq, QCD_WQ_FREE, 4) ||
			qcd_read(&out->tasks, workq, QCD_WQ_TASKS, 4);
		if (out->fault)
			goto unlock_link;
		/* Advisory only: counters can change while a callback runs. */
		out->work = work_busy((void *)((char *)workq + QCD_WQ_WORK));
	}
	if (!mutex_trylock(req_lock)) {
		out->request_busy = true;
		goto unlock_link;
	}
	out->fault = qcd_read(&out->open, link, QCD_LINK_OPEN, 4) ||
		qcd_read(&queue, link, QCD_LINK_REQUEST + QCD_REQUEST_QUEUE, sizeof(queue));
	if (!out->fault && queue) {
		out->queue = true;
		out->fault = qcd_read(&out->slots, queue, QCD_QUEUE_COUNT, 4) ||
			qcd_read(&out->rd, queue, QCD_QUEUE_READ, 4) ||
			qcd_read(&out->wr, queue, QCD_QUEUE_WRITE, 4) ||
			qcd_read(&out->applied, queue, QCD_QUEUE_APPLIED, 4);
	}
	mutex_unlock(req_lock);
unlock_link:
	mutex_unlock(lock);
}

/* bus_for_each_dev owns the device reference during this callback. Device lock
 * excludes the sole core_deinit/free path (driver remove). CRM lock alone does
 * NOT keep the root allocation alive. Every subsequent lock is a trylock.
 */
static int qcd_camera(struct device *dev, void *data)
{
	struct qcd_snapshot *out = data;
	struct mutex *cam_lock, *core_lock;
	struct list_head head, node;
	void *core = NULL, *entry, *session, *links[QCD_LINKS];
	unsigned int i;

	if (strcmp(dev_name(dev), "soc:qcom,cam-req-mgr"))
		return 0;
	out->camera_status = "device-busy";
	if (!device_trylock(dev))
		return 1;
	out->camera_status = "driver-mismatch";
	if (!dev->driver || strcmp(dev->driver->name, "cam_req_mgr"))
		goto unlock_device;
	cam_lock = (void *)((char *)out->cam_address + QCD_CAM_LOCK);
	out->camera_status = "camera-busy";
	if (!mutex_trylock(cam_lock))
		goto unlock_device;
	out->camera_fault = qcd_read(&out->cam_open, out->cam_address, QCD_CAM_OPEN, 4) ||
		qcd_read(&out->cam_subdevs, out->cam_address, QCD_CAM_SUBDEVS, 4) ||
		qcd_read(&out->cam_shutdown, out->cam_address, QCD_CAM_SHUTDOWN, 1) ||
		qcd_read(&core, out->core_slot, 0, sizeof(core));
	out->camera_status = "core-absent";
	if (out->camera_fault || !core)
		goto unlock_camera;
	core_lock = (void *)((char *)core + QCD_CORE_LOCK);
	out->camera_status = "core-busy";
	if (!mutex_trylock(core_lock))
		goto unlock_camera;
	out->camera_status = "sampled";
	if (qcd_read(&head, core, QCD_CORE_HEAD, sizeof(head))) {
		out->camera_fault = true;
		goto unlock_core;
	}
	entry = head.next;
	while (entry != (char *)core + QCD_CORE_HEAD) {
		if (out->sessions == QCD_MAX_SESSIONS) {
			out->camera_truncated = true;
			break;
		}
		if (qcd_read(&node, entry, 0, sizeof(node))) {
			out->camera_fault = true;
			break;
		}
		session = (char *)entry - QCD_SESSION_ENTRY;
		if (qcd_read(links, session, QCD_SESSION_LINKS, sizeof(links))) {
			out->camera_fault = true;
			break;
		}
		/* Membership is sparse; num_links is not a dense-array limit. */
		for (i = 0; i < QCD_LINKS; i++) {
			struct qcd_link *result;

			if (!links[i])
				continue;
			result = &out->links[out->link_count++];
			result->session = out->sessions;
			result->slot = i;
			qcd_link_snapshot(links[i], result);
		}
		out->sessions++;
		entry = node.next;
	}
unlock_core:
	mutex_unlock(core_lock);
unlock_camera:
	mutex_unlock(cam_lock);
unlock_device:
	device_unlock(dev);
	return 1;
}

/* Bus iteration pins subsys_device (freed only by its device release). Copy
 * embedded fields only. Group tracking and provider-owned desc are not followed.
 * Scalars/work bits are separate advisory observations, not an atomic snapshot.
 */
static int qcd_dsp(struct device *dev, void *data)
{
	struct qcd_snapshot *out = data;
	struct qcd_dsp *result;
	void *base = (char *)dev - QCD_DSP_DEV, *group = NULL;
	unsigned int i;

	if (out->dsp_count == QCD_MAX_DSP) {
		out->dsp_truncated = true;
		return 1;
	}
	result = &out->dsps[out->dsp_count++];
	result->fault = qcd_read(result->name, base, QCD_DSP_NAME, sizeof(result->name)) ||
		qcd_read(&result->refs, base, QCD_DSP_COUNT, 4) ||
		qcd_read(&result->state, base, QCD_DSP_TRACK + QCD_TRACK_STATE, 4) ||
		qcd_read(&result->crashes, base, QCD_DSP_CRASH, 4) ||
		qcd_read(&result->level, base, QCD_DSP_LEVEL, 4) ||
		qcd_read(&group, base, QCD_DSP_GROUP, sizeof(group));
	result->name[sizeof(result->name) - 1] = 0;
	for (i = 0; result->name[i]; i++) {
		char c = result->name[i];

		if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
		      (c >= '0' && c <= '9') || c == '(' || c == ')' || c == '_' || c == '-'))
			result->name[i] = '?';
	}
	result->group = !!group;
	if (!result->fault) {
		result->work = work_busy((void *)((char *)base + QCD_DSP_WORK));
		result->restart_work = work_busy((void *)((char *)base + QCD_DSP_RESTART_WORK));
	}
	return 0;
}
/* QCD_HOST_END */

static int __init qcd_init(void)
{
	struct qcd_snapshot *snapshot;
	unsigned int i;
	int ret;

	BUILD_BUG_ON(sizeof(void *) != 8);
	BUILD_BUG_ON(sizeof(struct mutex) != QCD_MUTEX_SIZE);
	BUILD_BUG_ON(sizeof(struct work_struct) != QCD_WORK_SIZE);
	BUILD_BUG_ON(sizeof(struct list_head) != QCD_LIST_SIZE);
	BUILD_BUG_ON(sizeof(struct device) != QCD_DEVICE_SIZE);
	BUILD_BUG_ON(offsetof(struct device, mutex) != QCD_DEVICE_LOCK);
	BUILD_BUG_ON(offsetof(struct device, driver) != QCD_DEVICE_DRIVER);
	BUILD_BUG_ON(sizeof(struct device_driver) != QCD_DRIVER_SIZE);
	BUILD_BUG_ON(offsetof(struct device_driver, name) != QCD_DRIVER_NAME);
	ret = qcd_target();
	if (ret)
		return ret;
	/* Never call the getter (it blocks); verify its actual entry bytes. */
	ret = qcd_anchor((const void *)cam_req_mgr_is_open);
	if (ret) {
		pr_err("qcd: camera anchor mismatch; no private state accessed\n");
		return ret;
	}
	snapshot = kzalloc(sizeof(*snapshot), GFP_KERNEL);
	if (!snapshot)
		return -ENOMEM;
	snapshot->cam_address = (char *)cam_req_mgr_is_open + 0x12d8c00;
	snapshot->core_slot = (char *)cam_req_mgr_is_open + 0x12d8210;
	snapshot->camera_status = "device-absent";
	bus_for_each_dev(&platform_bus_type, NULL, snapshot, qcd_camera);
	bus_for_each_dev(&subsys_bus_type, NULL, snapshot, qcd_dsp);
	pr_info("qcd: camera=%s fault=%u truncated=%u open=%d subdevs=%d shutdown=%u sessions=%u links=%u\n",
		snapshot->camera_status, snapshot->camera_fault, snapshot->camera_truncated,
		snapshot->cam_open, snapshot->cam_subdevs, snapshot->cam_shutdown,
		snapshot->sessions, snapshot->link_count);
	for (i = 0; i < snapshot->link_count; i++) {
		struct qcd_link *l = &snapshot->links[i];

		pr_info("qcd: link session=%u slot=%u busy=%u req_busy=%u fault=%u workq=%u pending=%d free=%d tasks=%d work_advisory=%u queue=%u open=%d slots=%d rd=%d wr=%d applied=%d\n",
			l->session, l->slot, l->busy, l->request_busy, l->fault, l->workq,
			l->pending, l->free, l->tasks, l->work, l->queue, l->open, l->slots,
			l->rd, l->wr, l->applied);
	}
	for (i = 0; i < snapshot->dsp_count; i++) {
		struct qcd_dsp *d = &snapshot->dsps[i];

		pr_info("qcd: dsp=%s fault=%u refs_advisory=%d embedded_state_advisory=%d crashes_advisory=%d level_advisory=%d grouped=%u work_advisory=%u restart_work_advisory=%u\n",
			d->name, d->fault, d->refs, d->state, d->crashes, d->level,
			d->group, d->work, d->restart_work);
	}
	pr_info("qcd: snapshot_complete dsps=%u dsp_truncated=%u handoff_ready=0 DMA_shutdown=unproven watchdog=untouched recovery=unproven\n",
		snapshot->dsp_count, snapshot->dsp_truncated);
	kfree(snapshot);
	return 0;
}

static void __exit qcd_exit(void)
{
	pr_info("qcd: unloaded; no hardware controls changed\n");
}

module_init(qcd_init);
module_exit(qcd_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Quest 2 bounded read-only camera/DSP advisory snapshot");
