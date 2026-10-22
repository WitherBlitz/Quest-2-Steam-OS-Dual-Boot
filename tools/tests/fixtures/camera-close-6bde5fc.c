// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2016-2020, The Linux Foundation. All rights reserved.
 * Copyright (c) 2022 Qualcomm Innovation Center, Inc. All rights reserved.
 */
/* Exact functions from kernel 6bde5fc06b252c97761bc0963dd13c1abcf377ab, cam_req_mgr_dev.c. */
static void cam_req_mgr_rwsem_write_op(enum cam_subdev_rwsem lock)
{
	if (lock == CAM_SUBDEV_LOCK)
		down_write(&rwsem_lock);
	else if (lock == CAM_SUBDEV_UNLOCK)
		up_write(&rwsem_lock);
}

static int cam_req_mgr_close(struct file *filep)
{
	struct v4l2_subdev *sd;
	struct v4l2_fh *vfh = filep->private_data;
	struct v4l2_subdev_fh *subdev_fh = to_v4l2_subdev_fh(vfh);

	CAM_WARN(CAM_CRM,
		"release invoked associated userspace process has died, open_cnt: %d",
		g_dev.open_cnt);

	cam_req_mgr_rwsem_write_op(CAM_SUBDEV_LOCK);

	mutex_lock(&g_dev.cam_lock);
	if (g_dev.open_cnt <= 0) {
		mutex_unlock(&g_dev.cam_lock);
		cam_req_mgr_rwsem_write_op(CAM_SUBDEV_UNLOCK);
		return -EINVAL;
	}

	g_dev.open_cnt--;
	CAM_DBG(CAM_CRM, "CRM open_cnt %d", g_dev.open_cnt);

	if (g_dev.open_cnt > 0) {
		mutex_unlock(&g_dev.cam_lock);
		return 0;
	}

	cam_req_mgr_handle_core_shutdown();

	list_for_each_entry(sd, &g_dev.v4l2_dev->subdevs, list) {
		if (!(sd->flags & V4L2_SUBDEV_FL_HAS_DEVNODE))
			continue;
		if (sd->internal_ops && sd->internal_ops->close) {
			CAM_DBG(CAM_CRM, "Invoke subdev close for device %s",
				sd->name);
			sd->internal_ops->close(sd, subdev_fh);
		}
	}

	v4l2_fh_release(filep);

	spin_lock_bh(&g_dev.cam_eventq_lock);
	g_dev.cam_eventq = NULL;
	spin_unlock_bh(&g_dev.cam_eventq_lock);

	cam_req_mgr_util_free_hdls();
	cam_mem_mgr_deinit();
	mutex_unlock(&g_dev.cam_lock);

	cam_req_mgr_rwsem_write_op(CAM_SUBDEV_UNLOCK);

	return 0;
}
