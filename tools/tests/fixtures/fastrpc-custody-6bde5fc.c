// SPDX-License-Identifier: GPL-2.0-only
/* Exact selected bodies from pinned adsprpc.c, source commit 6bde5fc. */
#include <linux/cpumask.h>

static inline int fastrpc_file_get(struct fastrpc_file *fl);
static void fastrpc_file_put(struct fastrpc_file *fl);

static int context_restore_interrupted(struct fastrpc_file *fl,
				       struct fastrpc_ioctl_invoke_crc *inv,
				       struct smq_invoke_ctx **po)
{
	int err = 0;
	struct smq_invoke_ctx *ctx = NULL, *ictx = NULL;
	struct hlist_node *n;
	struct fastrpc_ioctl_invoke *invoke = &inv->inv;

	spin_lock(&fl->hlock);
	hlist_for_each_entry_safe(ictx, n, &fl->clst.interrupted, hn) {
		if (ictx->pid == current->pid) {
			if (invoke->sc != ictx->sc || ictx->fl != fl)
				err = -1;
			else {
				ctx = ictx;
				hlist_del_init(&ctx->hn);
				hlist_add_head(&ctx->hn, &fl->clst.pending);
			}
			break;
		}
	}
	spin_unlock(&fl->hlock);
	if (ctx)
		*po = ctx;
	return err;
}


static void context_free(struct smq_invoke_ctx *ctx);

static int context_alloc(struct fastrpc_file *fl, uint32_t kernel,
			 struct fastrpc_ioctl_invoke_crc *invokefd,
			 struct smq_invoke_ctx **po)
{
	struct fastrpc_apps *me = &gfa;
	int err = 0, bufs, ii, size = 0, cid = -1;
	struct smq_invoke_ctx *ctx = NULL;
	struct fastrpc_ctx_lst *clst = &fl->clst;
	struct fastrpc_ioctl_invoke *invoke = &invokefd->inv;
	struct fastrpc_channel_ctx *chan = 0;
	unsigned long irq_flags = 0;

	bufs = REMOTE_SCALARS_LENGTH(invoke->sc);
	size = bufs * sizeof(*ctx->lpra) + bufs * sizeof(*ctx->maps) +
		sizeof(*ctx->fds) * (bufs) +
		sizeof(*ctx->attrs) * (bufs) +
		sizeof(*ctx->overs) * (bufs) +
		sizeof(*ctx->overps) * (bufs);

	VERIFY(err, NULL != (ctx = kzalloc(sizeof(*ctx) + size, GFP_KERNEL)));
	if (err)
		goto bail;

	INIT_HLIST_NODE(&ctx->hn);
	hlist_add_fake(&ctx->hn);
	ctx->fl = fl;
	ctx->maps = (struct fastrpc_mmap **)(&ctx[1]);
	ctx->lpra = (remote_arg_t *)(&ctx->maps[bufs]);
	ctx->fds = (int *)(&ctx->lpra[bufs]);
	ctx->attrs = (unsigned int *)(&ctx->fds[bufs]);
	ctx->overs = (struct overlap *)(&ctx->attrs[bufs]);
	ctx->overps = (struct overlap **)(&ctx->overs[bufs]);

	K_COPY_FROM_USER(err, kernel, (void *)ctx->lpra, invoke->pra,
					bufs * sizeof(*ctx->lpra));
	if (err)
		goto bail;

	if (invokefd->fds) {
		K_COPY_FROM_USER(err, kernel, ctx->fds, invokefd->fds,
						bufs * sizeof(*ctx->fds));
		if (err)
			goto bail;
	} else {
		ctx->fds = NULL;
	}
	if (invokefd->attrs) {
		K_COPY_FROM_USER(err, kernel, ctx->attrs, invokefd->attrs,
						bufs * sizeof(*ctx->attrs));
		if (err)
			goto bail;
	}
	ctx->crc = (uint32_t *)invokefd->crc;
	ctx->handle = invoke->handle;
	ctx->sc = invoke->sc;
	if (bufs) {
		VERIFY(err, 0 == context_build_overlap(ctx));
		if (err)
			goto bail;
	}
	ctx->retval = 0xDECAF;
	ctx->pid = current->pid;
	ctx->tgid = fl->tgid;
	init_completion(&ctx->work);
	ctx->magic = FASTRPC_CTX_MAGIC;
	ctx->rspFlags = NORMAL_RESPONSE;
	ctx->isWorkDone = false;

	spin_lock(&fl->hlock);
	hlist_add_head(&ctx->hn, &clst->pending);
	if (!(fl->cid >= ADSP_DOMAIN_ID && fl->cid < NUM_CHANNELS)) {
		err = -ECHRNG;
		spin_unlock(&fl->hlock);
		goto bail;
	}
	cid = fl->cid;
	chan = &me->channel[cid];
	spin_unlock(&fl->hlock);

	spin_lock_irqsave(&chan->ctxlock, irq_flags);
	me->jobid[cid]++;
	for (ii = 0; ii < FASTRPC_CTX_MAX; ii++) {
		if (!chan->ctxtable[ii]) {
			chan->ctxtable[ii] = ctx;
			ctx->ctxid = (me->jobid[cid] << 12) | (ii << 4);
			break;
		}
	}
	spin_unlock_irqrestore(&chan->ctxlock, irq_flags);
	VERIFY(err, ii < FASTRPC_CTX_MAX);
	if (err) {
		pr_err("adsprpc: out of context memory\n");
		goto bail;
	}
	trace_fastrpc_context_alloc((uint64_t)ctx,
		ctx->ctxid | fl->pd, ctx->handle, ctx->sc);
	*po = ctx;
bail:
	if (ctx && err)
		context_free(ctx);
	return err;
}

static void context_save_interrupted(struct smq_invoke_ctx *ctx)
{
	struct fastrpc_ctx_lst *clst = &ctx->fl->clst;

	spin_lock(&ctx->fl->hlock);
	hlist_del_init(&ctx->hn);
	hlist_add_head(&ctx->hn, &clst->interrupted);
	spin_unlock(&ctx->fl->hlock);
}

static void context_free(struct smq_invoke_ctx *ctx)
{
	int i;
	struct fastrpc_apps *me = &gfa;
	int nbufs = REMOTE_SCALARS_INBUFS(ctx->sc) +
		    REMOTE_SCALARS_OUTBUFS(ctx->sc);
	int cid = ctx->fl->cid;
	struct fastrpc_channel_ctx *chan = &me->channel[cid];
	unsigned long irq_flags = 0;

	spin_lock_irqsave(&chan->ctxlock, irq_flags);
	for (i = 0; i < FASTRPC_CTX_MAX; i++) {
		if (chan->ctxtable[i] == ctx) {
			chan->ctxtable[i] = NULL;
			break;
		}
	}
	spin_unlock_irqrestore(&chan->ctxlock, irq_flags);

	spin_lock(&ctx->fl->hlock);
	hlist_del_init(&ctx->hn);
	spin_unlock(&ctx->fl->hlock);

	mutex_lock(&ctx->fl->map_mutex);
	for (i = 0; i < nbufs; ++i) {
		if (ctx->maps[i] && ctx->maps[i]->ctx_refs)
			ctx->maps[i]->ctx_refs--;
		fastrpc_mmap_free(ctx->maps[i], 0);
	}
	mutex_unlock(&ctx->fl->map_mutex);

	fastrpc_buf_free(ctx->buf, 1);
	kfree(ctx->lrpra);
	ctx->lrpra = NULL;
	ctx->magic = 0;
	ctx->ctxid = 0;

	trace_fastrpc_context_free((uint64_t)ctx,
		ctx->msg.invoke.header.ctx, ctx->handle, ctx->sc);
	kfree(ctx);
}

static void context_notify_user(struct smq_invoke_ctx *ctx,
		int retval, uint32_t rspFlags, uint32_t earlyWakeTime)
{
	fastrpc_pm_awake(ctx->fl, gcinfo[ctx->fl->cid].secure);
	ctx->retval = retval;
	switch (rspFlags) {
	case NORMAL_RESPONSE:
		/* normal response with return value */
		ctx->retval = retval;
		ctx->isWorkDone = true;
		break;
	case USER_EARLY_SIGNAL:
		/* user hint of approximate time of completion */
		ctx->earlyWakeTime = earlyWakeTime;
		break;
	case EARLY_RESPONSE:
		/* rpc framework early response with return value */
		ctx->retval = retval;
		break;
	case COMPLETE_SIGNAL:
		/* rpc framework signal to clear if pending on ctx */
		ctx->isWorkDone = true;
		break;
	default:
		break;
	}
	ctx->rspFlags = (enum fastrpc_response_flags)rspFlags;
	trace_fastrpc_context_complete(ctx->fl->cid, (uint64_t)ctx, retval,
		ctx->msg.invoke.header.ctx, ctx->handle, ctx->sc);
	complete(&ctx->work);
}

static void fastrpc_notify_users(struct fastrpc_file *me)
{
	struct smq_invoke_ctx *ictx;
	struct hlist_node *n;

	spin_lock(&me->hlock);
	hlist_for_each_entry_safe(ictx, n, &me->clst.pending, hn) {
		ictx->isWorkDone = true;
		trace_fastrpc_context_complete(me->cid, (uint64_t)ictx,
			ictx->retval, ictx->msg.invoke.header.ctx,
			ictx->handle, ictx->sc);
		complete(&ictx->work);
	}
	hlist_for_each_entry_safe(ictx, n, &me->clst.interrupted, hn) {
		ictx->isWorkDone = true;
		trace_fastrpc_context_complete(me->cid, (uint64_t)ictx,
			ictx->retval, ictx->msg.invoke.header.ctx,
			ictx->handle, ictx->sc);
		complete(&ictx->work);
	}
	spin_unlock(&me->hlock);
}


static void fastrpc_notify_users_staticpd_pdr(struct fastrpc_file *me)
{
	struct smq_invoke_ctx *ictx;
	struct hlist_node *n;

	spin_lock(&me->hlock);
	hlist_for_each_entry_safe(ictx, n, &me->clst.pending, hn) {
		if (ictx->msg.pid) {
			ictx->isWorkDone = true;
			trace_fastrpc_context_complete(me->cid, (uint64_t)ictx,
				ictx->retval, ictx->msg.invoke.header.ctx,
				ictx->handle, ictx->sc);
			complete(&ictx->work);
		}
	}
	hlist_for_each_entry_safe(ictx, n, &me->clst.interrupted, hn) {
		if (ictx->msg.pid) {
			ictx->isWorkDone = true;
			trace_fastrpc_context_complete(me->cid, (uint64_t)ictx,
				ictx->retval, ictx->msg.invoke.header.ctx,
				ictx->handle, ictx->sc);
			complete(&ictx->work);
		}
	}
	spin_unlock(&me->hlock);
}

static void fastrpc_context_list_dtor(struct fastrpc_file *fl)
{
	struct fastrpc_ctx_lst *clst = &fl->clst;
	struct smq_invoke_ctx *ictx = NULL, *ctxfree;
	struct hlist_node *n;

	do {
		ctxfree = NULL;
		spin_lock(&fl->hlock);
		hlist_for_each_entry_safe(ictx, n, &clst->interrupted, hn) {
			hlist_del_init(&ictx->hn);
			ctxfree = ictx;
			break;
		}
		spin_unlock(&fl->hlock);
		if (ctxfree)
			context_free(ctxfree);
	} while (ctxfree);
	do {
		ctxfree = NULL;
		spin_lock(&fl->hlock);
		hlist_for_each_entry_safe(ictx, n, &clst->pending, hn) {
			hlist_del_init(&ictx->hn);
			ctxfree = ictx;
			break;
		}
		spin_unlock(&fl->hlock);
		if (ctxfree)
			context_free(ctxfree);
	} while (ctxfree);
}

static int fastrpc_file_free(struct fastrpc_file *fl);

static inline int fastrpc_file_get(struct fastrpc_file *fl)
{
	int ret = 0;

	if (fl != NULL)
		ret = kref_get_unless_zero(&fl->refcount);
	return ret;
}

static void fastrpc_file_destroy(struct kref *kref)
{
	struct fastrpc_file *fl = container_of(kref,
			struct fastrpc_file, refcount);

	fastrpc_file_free(fl);
}

static inline void fastrpc_file_put(struct fastrpc_file *fl)
{
	if (fl)
		kref_put(&fl->refcount, fastrpc_file_destroy);
}

static int fastrpc_invoke_send(struct smq_invoke_ctx *ctx,
			       uint32_t kernel, uint32_t handle)
{
	struct smq_msg *msg = &ctx->msg;
	struct fastrpc_file *fl = ctx->fl;
	struct fastrpc_channel_ctx *channel_ctx = NULL;
	int err = 0, cid = -1;

	if (!fl) {
		err = -EBADF;
		goto bail;
	}
	cid = fl->cid;
	VERIFY(err, cid >= ADSP_DOMAIN_ID && cid < NUM_CHANNELS);
	if (err) {
		err = -ECHRNG;
		goto bail;
	}

	channel_ctx = &fl->apps->channel[cid];
	mutex_lock(&channel_ctx->smd_mutex);
	msg->pid = fl->tgid;
	msg->tid = current->pid;
	if (fl->sessionid)
		msg->tid |= (1 << SESSION_ID_INDEX);
	if (kernel)
		msg->pid = 0;
	msg->invoke.header.ctx = ctx->ctxid | fl->pd;
	msg->invoke.header.handle = handle;
	msg->invoke.header.sc = ctx->sc;
	msg->invoke.page.addr = ctx->buf ? ctx->buf->phys : 0;
	msg->invoke.page.size = buf_page_size(ctx->used);

	if (fl->ssrcount != channel_ctx->ssrcount) {
		err = -ECONNRESET;
		mutex_unlock(&channel_ctx->smd_mutex);
		goto bail;
	}
	mutex_unlock(&channel_ctx->smd_mutex);

	mutex_lock(&channel_ctx->rpmsg_mutex);
	VERIFY(err, !IS_ERR_OR_NULL(channel_ctx->rpdev));
	if (err) {
		err = -ECONNRESET;
		mutex_unlock(&channel_ctx->rpmsg_mutex);
		goto bail;
	}
	err = rpmsg_send(channel_ctx->rpdev->ept, (void *)msg, sizeof(*msg));
	trace_fastrpc_rpmsg_send(cid, (uint64_t)ctx, msg->invoke.header.ctx,
		handle, ctx->sc, msg->invoke.page.addr, msg->invoke.page.size);
	LOG_FASTRPC_GLINK_MSG(channel_ctx->ipc_log_ctx,
		"sent pkt %pK (sz %d): ctx 0x%llx, handle 0x%x, sc 0x%x (rpmsg err %d)",
		(void *)msg, sizeof(*msg),
		msg->invoke.header.ctx, handle, ctx->sc, err);
	mutex_unlock(&channel_ctx->rpmsg_mutex);
 bail:
	return err;
}

static void fastrpc_wait_for_completion(struct smq_invoke_ctx *ctx,
		 int *pInterrupted, uint32_t kernel)
{
	int interrupted = 0, err = 0;
	int jj;
	bool wait_resp;
	uint32_t wTimeout = FASTRPC_USER_EARLY_HINT_TIMEOUT;
	uint32_t wakeTime = 0;

	if (!ctx) {
		/* This failure is not expected */
		err = *pInterrupted = EFAULT;
		pr_err("Error %d: adsprpc: %s: %s: ctx is NULL, cannot wait for response\n",
					err, current->comm, __func__);
		return;
	}
	wakeTime = ctx->earlyWakeTime;

	do {
		switch (ctx->rspFlags) {
		/* try polling on completion with timeout */
		case USER_EARLY_SIGNAL:
			/* try wait if completion time is less than timeout */
			/* disable preempt to avoid context switch latency */
			preempt_disable();
			jj = 0;
			wait_resp = false;
			for (; wakeTime < wTimeout && jj < wTimeout; jj++) {
				wait_resp = try_wait_for_completion(&ctx->work);
				if (wait_resp)
					break;
				udelay(1);
			}
			preempt_enable_no_resched();
			if (!wait_resp) {
				interrupted = fastrpc_wait_for_response(ctx,
									kernel);
				*pInterrupted = interrupted;
				if (interrupted || ctx->isWorkDone)
					return;
			}
			break;

		/* busy poll on memory for actual job done */
		case EARLY_RESPONSE:
			err = poll_on_early_response(ctx);

			/* Mark job done if poll on memory successful */
			/* Wait for completion if poll on memory timoeut */
			if (!err) {
				ctx->isWorkDone = true;
			} else if (!ctx->isWorkDone) {
				pr_info("adsprpc: %s: %s: poll timeout for handle 0x%x, sc 0x%x\n",
				__func__, current->comm, ctx->handle, ctx->sc);
				interrupted = fastrpc_wait_for_response(ctx,
									kernel);
				*pInterrupted = interrupted;
				if (interrupted || ctx->isWorkDone)
					return;
			}
			break;

		case COMPLETE_SIGNAL:
		case NORMAL_RESPONSE:
			interrupted = fastrpc_wait_for_response(ctx, kernel);
			*pInterrupted = interrupted;
			if (interrupted || ctx->isWorkDone)
				return;
			break;

		default:
			*pInterrupted = EBADR;
			pr_err("Error: adsprpc: %s: unsupported response flags 0x%x for handle 0x%x, sc 0x%x\n",
			current->comm, ctx->rspFlags, ctx->handle, ctx->sc);
			return;
		} /* end of switch */
	} while (!ctx->isWorkDone);
}

static int fastrpc_internal_invoke(struct fastrpc_file *fl, uint32_t mode,
				   uint32_t kernel,
				   struct fastrpc_ioctl_invoke_crc *inv)
{
	struct smq_invoke_ctx *ctx = NULL;
	struct fastrpc_ioctl_invoke *invoke = &inv->inv;
	int err = 0, interrupted = 0, cid = -1;
	struct timespec64 invoket = {0};
	int64_t *perf_counter = NULL;

	cid = fl->cid;
	VERIFY(err, cid >= ADSP_DOMAIN_ID && cid < NUM_CHANNELS);
	if (err) {
		err = -ECHRNG;
		goto bail;
	}
	VERIFY(err, fl->sctx != NULL);
	if (err) {
		pr_err("adsprpc: ERROR: %s: user application %s domain is not set\n",
			__func__, current->comm);
		err = -EBADR;
		goto bail;
	}
	if (fl->profile) {
		perf_counter = getperfcounter(fl, PERF_COUNT);
		ktime_get_real_ts64(&invoket);
	}

	if (!kernel) {
		VERIFY(err, invoke->handle !=
			FASTRPC_STATIC_HANDLE_PROCESS_GROUP);
		VERIFY(err, invoke->handle !=
			FASTRPC_STATIC_HANDLE_DSP_UTILITIES);
		if (err) {
			pr_err("adsprpc: ERROR: %s: user application %s trying to send a kernel RPC message to channel %d, handle 0x%x\n",
				__func__, current->comm, cid, invoke->handle);
			goto bail;
		}
	}

	if (!kernel) {
		err = context_restore_interrupted(fl, inv, &ctx);
		if (err)
			goto bail;
		if (fl->sctx->smmu.faults)
			err = FASTRPC_ENOSUCH;
		if (err)
			goto bail;
		if (ctx) {
			trace_fastrpc_context_restore(cid, (uint64_t)ctx,
				ctx->msg.invoke.header.ctx,
				ctx->handle, ctx->sc);
			goto wait;
		}
	}

	VERIFY(err, 0 == context_alloc(fl, kernel, inv, &ctx));
	if (err)
		goto bail;

	if (REMOTE_SCALARS_LENGTH(ctx->sc)) {
		PERF(fl->profile, GET_COUNTER(perf_counter, PERF_GETARGS),
		VERIFY(err, 0 == get_args(kernel, ctx));
		PERF_END);
		if (err)
			goto bail;
	}

	PERF(fl->profile, GET_COUNTER(perf_counter, PERF_INVARGS),
	inv_args(ctx);
	PERF_END);

	PERF(fl->profile, GET_COUNTER(perf_counter, PERF_LINK),
	VERIFY(err, 0 == fastrpc_invoke_send(ctx, kernel, invoke->handle));
	PERF_END);

	if (err)
		goto bail;
 wait:
	fastrpc_wait_for_completion(ctx, &interrupted, kernel);
	VERIFY(err, 0 == (err = interrupted));
	if (err)
		goto bail;

	if (!ctx->isWorkDone) {
		err = EPROTO;
		pr_err("Error: adsprpc: %s: %s: WorkDone state is invalid for handle 0x%x, sc 0x%x\n",
			__func__, current->comm, invoke->handle, ctx->sc);
		goto bail;
	}

	PERF(fl->profile, GET_COUNTER(perf_counter, PERF_INVARGS),
	inv_args(ctx);
	PERF_END);

	PERF(fl->profile, GET_COUNTER(perf_counter, PERF_PUTARGS),
	VERIFY(err, 0 == (err = put_args(kernel, ctx, invoke->pra)));
	PERF_END);
	if (err)
		goto bail;

	VERIFY(err, 0 == (err = ctx->retval));
	if (err)
		goto bail;
 bail:
	if (ctx && interrupted == -ERESTARTSYS) {
		trace_fastrpc_context_interrupt(cid, (uint64_t)ctx,
			ctx->msg.invoke.header.ctx, ctx->handle, ctx->sc);
		context_save_interrupted(ctx);
	}
	else if (ctx)
		context_free(ctx);
	if (fl->ssrcount != fl->apps->channel[cid].ssrcount)
		err = ECONNRESET;

	if (fl->profile && !interrupted)
		fastrpc_update_invoke_count(invoke->handle, perf_counter,
						&invoket);
	return err;
}

static int fastrpc_file_free(struct fastrpc_file *fl)
{
	struct hlist_node *n = NULL;
	struct fastrpc_mmap *map = NULL, *lmap = NULL;
	struct fastrpc_perf *perf = NULL, *fperf = NULL;
	int cid;

	if (!fl)
		return 0;
	cid = fl->cid;

	(void)fastrpc_release_current_dsp_process(fl);

	spin_lock(&fl->apps->hlock);
	hlist_del_init(&fl->hn);
	spin_unlock(&fl->apps->hlock);
	kfree(fl->proc_buf);
	fl->proc_buf_alloced_attempted = 0;

	if (!fl->sctx) {
		kfree(fl);
		return 0;
	}
	spin_lock(&fl->hlock);
	fl->file_close = 1;
	fl->dsp_process_state = PROCESS_CREATE_DEFAULT;
	spin_unlock(&fl->hlock);
	if (!IS_ERR_OR_NULL(fl->init_mem))
		fastrpc_buf_free(fl->init_mem, 0);
	fastrpc_context_list_dtor(fl);
	fastrpc_cached_buf_list_free(fl);
	mutex_lock(&fl->map_mutex);
	do {
		lmap = NULL;
		hlist_for_each_entry_safe(map, n, &fl->maps, hn) {
			hlist_del_init(&map->hn);
			lmap = map;
			break;
		}
		fastrpc_mmap_free(lmap, 1);
	} while (lmap);
	mutex_unlock(&fl->map_mutex);

	if (fl->sctx)
		fastrpc_session_free(&fl->apps->channel[cid], fl->sctx);
	if (fl->secsctx)
		fastrpc_session_free(&fl->apps->channel[cid], fl->secsctx);

	mutex_lock(&fl->perf_mutex);
	do {
		struct hlist_node *pn = NULL;

		fperf = NULL;
		hlist_for_each_entry_safe(perf, pn, &fl->perf, hn) {
			hlist_del_init(&perf->hn);
			fperf = perf;
			break;
		}
		kfree(fperf);
	} while (fperf);
	fastrpc_remote_buf_list_free(fl);
	mutex_unlock(&fl->perf_mutex);
	mutex_destroy(&fl->perf_mutex);
	mutex_destroy(&fl->map_mutex);
	mutex_destroy(&fl->internal_map_mutex);
	mutex_destroy(&fl->pm_qos_mutex);
	kfree(fl);
	return 0;
}

static int fastrpc_device_release(struct inode *inode, struct file *file)
{
	struct fastrpc_file *fl = (struct fastrpc_file *)file->private_data;

	if (fl) {
		if (fl->qos_request && pm_qos_request_active(&fl->pm_qos_req))
			pm_qos_remove_request(&fl->pm_qos_req);
		if (fl->procfs_file != NULL)
			proc_remove(fl->procfs_file);

		spin_lock(&fl->hlock);
		fl->file_close = 1;
		spin_unlock(&fl->hlock);
		fastrpc_file_sysfs_put(fl->sysfs);
		fastrpc_file_put(fl);
		file->private_data = NULL;
	}
	return 0;
}

static int fastrpc_device_open(struct inode *inode, struct file *filp)
{
	int err = 0;
	struct fastrpc_file *fl = NULL;
	struct fastrpc_apps *me = &gfa;

	/*
	 * Indicates the device node opened
	 * MINOR_NUM_DEV or MINOR_NUM_SECURE_DEV
	 */
	int dev_minor = MINOR(inode->i_rdev);

	VERIFY(err, ((dev_minor == MINOR_NUM_DEV) ||
			(dev_minor == MINOR_NUM_SECURE_DEV)));
	if (err) {
		pr_err("adsprpc: Invalid dev minor num %d\n", dev_minor);
		return err;
	}

	VERIFY(err, NULL != (fl = kzalloc(sizeof(*fl), GFP_KERNEL)));
	if (err)
		return err;

	context_list_ctor(&fl->clst);
	spin_lock_init(&fl->hlock);
	INIT_HLIST_HEAD(&fl->maps);
	INIT_HLIST_HEAD(&fl->perf);
	INIT_HLIST_HEAD(&fl->cached_bufs);
	INIT_HLIST_HEAD(&fl->remote_bufs);
	INIT_HLIST_NODE(&fl->hn);
	kref_init(&fl->refcount);
	fl->sessionid = 0;
	fl->apps = me;
	fl->mode = FASTRPC_MODE_SERIAL;
	fl->cid = -1;
	fl->dev_minor = dev_minor;
	fl->init_mem = NULL;
	fl->dsp_process_state = PROCESS_CREATE_DEFAULT;
	memset(&fl->perf, 0, sizeof(fl->perf));
	fl->qos_request = 0;
	fl->dsp_proc_init = 0;
	filp->private_data = fl;
	mutex_init(&fl->internal_map_mutex);
	mutex_init(&fl->map_mutex);
	spin_lock(&me->hlock);
	hlist_add_head(&fl->hn, &me->drivers);
	spin_unlock(&me->hlock);
	mutex_init(&fl->perf_mutex);
	mutex_init(&fl->pm_qos_mutex);
	init_completion(&fl->shutdown);
	return 0;
}
