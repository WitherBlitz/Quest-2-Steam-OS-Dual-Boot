/* Exact adsprpc.c:3290-3486 at 6bde5fc06b252c97761bc0963dd13c1abcf377ab. */
static int fastrpc_unmap_on_dsp(struct fastrpc_file *fl,
		uintptr_t raddr, uint64_t phys, size_t size, uint32_t flags)
{
	struct fastrpc_ioctl_invoke_crc ioctl;
	remote_arg_t ra[1] = {};
	int err = 0;
	struct {
		int pid;
		uintptr_t vaddrout;
		size_t size;
	} inargs;

	inargs.pid = fl->tgid;
	inargs.size = size;
	inargs.vaddrout = raddr;
	ra[0].buf.pv = (void *)&inargs;
	ra[0].buf.len = sizeof(inargs);

	ioctl.inv.handle = FASTRPC_STATIC_HANDLE_PROCESS_GROUP;
	if (fl->apps->compat)
		ioctl.inv.sc = REMOTE_SCALARS_MAKE(5, 1, 0);
	else
		ioctl.inv.sc = REMOTE_SCALARS_MAKE(3, 1, 0);
	ioctl.inv.pra = ra;
	ioctl.fds = NULL;
	ioctl.attrs = NULL;
	ioctl.crc = NULL;
	VERIFY(err, 0 == (err = fastrpc_internal_invoke(fl,
		FASTRPC_MODE_PARALLEL, 1, &ioctl)));
	if (err)
		goto bail;
bail:
	return err;
}

static int fastrpc_mmap_on_dsp(struct fastrpc_file *fl, uint32_t flags,
					uintptr_t va, uint64_t phys,
					size_t size, int refs, uintptr_t *raddr)
{
	struct fastrpc_ioctl_invoke_crc ioctl;
	struct fastrpc_apps *me = &gfa;
	struct smq_phy_page page;
	int num = 1;
	remote_arg_t ra[3];
	int err = 0;
	struct {
		int pid;
		uint32_t flags;
		uintptr_t vaddrin;
		int num;
	} inargs;
	struct {
		uintptr_t vaddrout;
	} routargs;
	int cid = -1;

	if (!fl) {
		err = -EBADF;
		goto bail;
	}
	cid = fl->cid;
	inargs.pid = fl->tgid;
	inargs.vaddrin = (uintptr_t)va;
	inargs.flags = flags;
	inargs.num = fl->apps->compat ? num * sizeof(page) : num;
	ra[0].buf.pv = (void *)&inargs;
	ra[0].buf.len = sizeof(inargs);
	page.addr = phys;
	page.size = size;
	ra[1].buf.pv = (void *)&page;
	ra[1].buf.len = num * sizeof(page);

	ra[2].buf.pv = (void *)&routargs;
	ra[2].buf.len = sizeof(routargs);

	ioctl.inv.handle = FASTRPC_STATIC_HANDLE_PROCESS_GROUP;
	if (fl->apps->compat)
		ioctl.inv.sc = REMOTE_SCALARS_MAKE(4, 2, 1);
	else
		ioctl.inv.sc = REMOTE_SCALARS_MAKE(2, 2, 1);
	ioctl.inv.pra = ra;
	ioctl.fds = NULL;
	ioctl.attrs = NULL;
	ioctl.crc = NULL;
	VERIFY(err, 0 == (err = fastrpc_internal_invoke(fl,
		FASTRPC_MODE_PARALLEL, 1, &ioctl)));
	*raddr = (uintptr_t)routargs.vaddrout;
	if (err)
		goto bail;
	if (flags == ADSP_MMAP_REMOTE_HEAP_ADDR) {
		VERIFY(err, cid >= ADSP_DOMAIN_ID && cid < NUM_CHANNELS);
		if (err) {
			err = -ECHRNG;
			pr_err("invalid channel 0x%zx set for session\n",
			cid);
			goto bail;
		}
	}
	if (flags == ADSP_MMAP_REMOTE_HEAP_ADDR
				&& me->channel[cid].rhvm.vmid && refs == 1) {
		err = hyp_assign_phys(phys, (uint64_t)size,
				hlosvm, 1, me->channel[cid].rhvm.vmid,
				me->channel[cid].rhvm.vmperm,
				me->channel[cid].rhvm.vmcount);
		if (err) {
			pr_err("adsprpc: %s: %s: rh hyp assign failed with %d for phys 0x%llx, size %zd\n",
					__func__, current->comm,
					err, phys, size);
			err = fastrpc_unmap_on_dsp(fl,
				*raddr, phys, size, flags);
			if (err) {
				pr_err("adsprpc: %s: %s: failed to unmap %d for phys 0x%llx, size %zd\n",
					__func__, current->comm,
					err, phys, size);
			}
			goto bail;
		}
	}
bail:
	return err;
}

static int fastrpc_munmap_on_dsp_rh(struct fastrpc_file *fl, uint64_t phys,
					size_t size, uint32_t flags, int locked)
{
	int err = 0;
	struct fastrpc_apps *me = &gfa;
	int tgid = 0;
	int destVM[1] = {VMID_HLOS};
	int destVMperm[1] = {PERM_READ | PERM_WRITE | PERM_EXEC};
	int cid = -1;

	if (!fl) {
		err = -EBADF;
		goto bail;
	}
	cid = fl->cid;
	VERIFY(err, cid >= ADSP_DOMAIN_ID && cid < NUM_CHANNELS);
	if (err) {
		err = -ECHRNG;
		pr_err("invalid channel 0x%zx set for session\n",
			cid);
		goto bail;
	}
	if (flags == ADSP_MMAP_HEAP_ADDR) {
		struct fastrpc_ioctl_invoke_crc ioctl;
		remote_arg_t ra[2];
		int err = 0;
		struct {
			uint8_t skey;
		} routargs;

		tgid = fl->tgid;
		ra[0].buf.pv = (void *)&tgid;
		ra[0].buf.len = sizeof(tgid);

		ra[1].buf.pv = (void *)&routargs;
		ra[1].buf.len = sizeof(routargs);

		ioctl.inv.handle = FASTRPC_STATIC_HANDLE_PROCESS_GROUP;
		ioctl.inv.sc = REMOTE_SCALARS_MAKE(9, 1, 1);
		ioctl.inv.pra = ra;
		ioctl.fds = NULL;
		ioctl.attrs = NULL;
		ioctl.crc = NULL;

		if (locked) {
			mutex_unlock(&fl->map_mutex);
			mutex_unlock(&me->channel[cid].smd_mutex);
		}
		VERIFY(err, 0 == (err = fastrpc_internal_invoke(fl,
				FASTRPC_MODE_PARALLEL, 1, &ioctl)));
		if (locked) {
			mutex_lock(&me->channel[cid].smd_mutex);
			mutex_lock(&fl->map_mutex);
		}
		if (err)
			goto bail;
	} else if (flags == ADSP_MMAP_REMOTE_HEAP_ADDR) {
		if (me->channel[cid].rhvm.vmid) {
			err = hyp_assign_phys(phys,
					(uint64_t)size,
					me->channel[cid].rhvm.vmid,
					me->channel[cid].rhvm.vmcount,
					destVM, destVMperm, 1);
			if (err) {
				pr_err("adsprpc: %s: %s: rh hyp unassign failed with %d for phys 0x%llx, size %zd\n",
					__func__, current->comm,
					err, phys, size);
				goto bail;
			}
		}
	}

bail:
	return err;
}
