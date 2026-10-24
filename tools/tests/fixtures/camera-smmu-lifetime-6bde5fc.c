// SPDX-License-Identifier: GPL-2.0-only
/* Exact source excerpts from techpack/camera/drivers/cam_smmu/cam_smmu_api.c
 * Commit: 6bde5fc06b252c97761bc0963dd13c1abcf377ab
 * Original full file SHA-256: 2e5a0991d320bf7227eea5aa5aa40f9689aea6747c85b08b139dbd1de2293de1
 */

static struct cam_iommu_cb_set iommu_cb_set;

static void cam_smmu_clean_user_buffer_list(int idx);
static void cam_smmu_clean_kernel_buffer_list(int idx);

static int cam_smmu_unmap_buf_and_remove_from_list(
	struct cam_dma_buff_info *mapping_info,
	int idx)
{
	int rc;
	size_t size;
	struct iommu_domain *domain;
	struct timespec64 ts1, ts2;
	long microsec = 0;

	if ((!mapping_info->buf) || (!mapping_info->table) ||
		(!mapping_info->attach)) {
		CAM_ERR(CAM_SMMU,
			"Error: Invalid params dev = %pK, table = %pK",
			(void *)iommu_cb_set.cb_info[idx].dev,
			(void *)mapping_info->table);
		CAM_ERR(CAM_SMMU, "Error:dma_buf = %pK, attach = %pK",
			(void *)mapping_info->buf,
			(void *)mapping_info->attach);
		return -EINVAL;
	}

	cam_smmu_update_monitor_array(&iommu_cb_set.cb_info[idx], false,
		mapping_info);

	CAM_DBG(CAM_SMMU,
		"region_id=%d, paddr=%pK, len=%d, dma_map_attrs=%d",
		mapping_info->region_id, mapping_info->paddr, mapping_info->len,
		mapping_info->attach->dma_map_attrs);

	if (iommu_cb_set.map_profile_enable)
		CAM_GET_TIMESTAMP(ts1);

	if (mapping_info->region_id == CAM_SMMU_REGION_SHARED) {
		CAM_DBG(CAM_SMMU,
			"Removing SHARED buffer paddr = %pK, len = %zu",
			(void *)mapping_info->paddr, mapping_info->len);

		domain = iommu_cb_set.cb_info[idx].domain;

		size = iommu_unmap(domain,
			mapping_info->paddr,
			mapping_info->len);

		if (size != mapping_info->len) {
			CAM_ERR(CAM_SMMU, "IOMMU unmap failed");
			CAM_ERR(CAM_SMMU, "Unmapped = %zu, requested = %zu",
				size,
				mapping_info->len);
		}

		rc = cam_smmu_free_iova(mapping_info->paddr,
			mapping_info->len,
			iommu_cb_set.cb_info[idx].handle);

		if (rc)
			CAM_ERR(CAM_SMMU, "IOVA free failed");

		iommu_cb_set.cb_info[idx].shared_mapping_size -=
			mapping_info->len;
	} else if (mapping_info->region_id == CAM_SMMU_REGION_IO) {
		iommu_cb_set.cb_info[idx].io_mapping_size -= mapping_info->len;
	}

	dma_buf_unmap_attachment(mapping_info->attach,
		mapping_info->table, mapping_info->dir);
	dma_buf_detach(mapping_info->buf, mapping_info->attach);
	dma_buf_put(mapping_info->buf);

	if (iommu_cb_set.map_profile_enable) {
		CAM_GET_TIMESTAMP(ts2);
		CAM_GET_TIMESTAMP_DIFF_IN_MICRO(ts1, ts2, microsec);
		trace_cam_log_event("SMMUUnmapProfile",
			"size and time in micro", mapping_info->len, microsec);
	}

	mapping_info->buf = NULL;

	list_del_init(&mapping_info->list);

	/* free one buffer */
	kfree(mapping_info);
	return 0;
}

int cam_smmu_release_sec_heap(int32_t smmu_hdl)
{
	int idx;
	size_t size = 0;
	uint32_t sec_heap_iova = 0;
	size_t sec_heap_iova_len = 0;
	struct secheap_buf_info *secheap_buf = NULL;

	idx = GET_SMMU_TABLE_IDX(smmu_hdl);
	if (idx < 0 || idx >= iommu_cb_set.cb_num) {
		CAM_ERR(CAM_SMMU,
			"Error: handle or index invalid. idx = %d hdl = %x",
			idx, smmu_hdl);
		return -EINVAL;
	}

	if (!iommu_cb_set.cb_info[idx].secheap_support) {
		CAM_ERR(CAM_SMMU, "Secondary heap not supported");
		return -EINVAL;
	}
	mutex_lock(&iommu_cb_set.cb_info[idx].lock);

	if (!iommu_cb_set.cb_info[idx].is_secheap_allocated) {
		CAM_ERR(CAM_SMMU, "Trying to release secheap twice");
		mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
		return -ENOMEM;
	}

	secheap_buf = &iommu_cb_set.cb_info[idx].secheap_buf;
	sec_heap_iova = iommu_cb_set.cb_info[idx].secheap_info.iova_start;
	sec_heap_iova_len = iommu_cb_set.cb_info[idx].secheap_info.iova_len;

	size = iommu_unmap(iommu_cb_set.cb_info[idx].domain,
		sec_heap_iova,
		sec_heap_iova_len);
	if (size != sec_heap_iova_len) {
		CAM_ERR(CAM_SMMU, "Failed: Unmapped = %zu, requested = %zu",
			size,
			sec_heap_iova_len);
	}

	dma_buf_unmap_attachment(secheap_buf->attach,
		secheap_buf->table, DMA_BIDIRECTIONAL);
	dma_buf_detach(secheap_buf->buf, secheap_buf->attach);
	dma_buf_put(secheap_buf->buf);
	iommu_cb_set.cb_info[idx].is_secheap_allocated = false;
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);

	return 0;
}

static void cam_smmu_clean_user_buffer_list(int idx)
{
	int ret;
	struct cam_dma_buff_info *mapping_info, *temp;

	list_for_each_entry_safe(mapping_info, temp,
			&iommu_cb_set.cb_info[idx].smmu_buf_list, list) {
		CAM_DBG(CAM_SMMU, "Free mapping address %pK, i = %d, fd = %d",
			(void *)mapping_info->paddr, idx,
			mapping_info->ion_fd);

		if (mapping_info->ion_fd == 0xDEADBEEF)
			/* Clean up scratch buffers */
			ret = cam_smmu_free_scratch_buffer_remove_from_list(
							mapping_info, idx);
		else
			/* Clean up regular mapped buffers */
			ret = cam_smmu_unmap_buf_and_remove_from_list(
					mapping_info,
					idx);

		if (ret < 0) {
			CAM_ERR(CAM_SMMU, "Buffer delete failed: idx = %d",
				idx);
			CAM_ERR(CAM_SMMU,
				"Buffer delete failed: addr = %lx, fd = %d",
				(unsigned long)mapping_info->paddr,
				mapping_info->ion_fd);
			/*
			 * Ignore this error and continue to delete other
			 * buffers in the list
			 */
			continue;
		}
	}
}

static void cam_smmu_clean_kernel_buffer_list(int idx)
{
	int ret;
	struct cam_dma_buff_info *mapping_info, *temp;

	list_for_each_entry_safe(mapping_info, temp,
			&iommu_cb_set.cb_info[idx].smmu_buf_kernel_list, list) {
		CAM_DBG(CAM_SMMU,
			"Free mapping address %pK, i = %d, dma_buf = %pK",
			(void *)mapping_info->paddr, idx,
			mapping_info->buf);

		/* Clean up regular mapped buffers */
		ret = cam_smmu_unmap_buf_and_remove_from_list(
				mapping_info,
				idx);

		if (ret < 0) {
			CAM_ERR(CAM_SMMU,
				"Buffer delete in kernel list failed: idx = %d",
				idx);
			CAM_ERR(CAM_SMMU,
				"Buffer delete failed: addr = %lx, dma_buf = %pK",
				(unsigned long)mapping_info->paddr,
				mapping_info->buf);
			/*
			 * Ignore this error and continue to delete other
			 * buffers in the list
			 */
			continue;
		}
	}
}

int cam_smmu_destroy_handle(int handle)
{
	int idx;

	if (handle == HANDLE_INIT) {
		CAM_ERR(CAM_SMMU, "Error: Invalid handle");
		return -EINVAL;
	}

	idx = GET_SMMU_TABLE_IDX(handle);
	if (idx < 0 || idx >= iommu_cb_set.cb_num) {
		CAM_ERR(CAM_SMMU,
			"Error: handle or index invalid. idx = %d hdl = %x",
			idx, handle);
		return -EINVAL;
	}

	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"Error: hdl is not valid, table_hdl = %x, hdl = %x",
			iommu_cb_set.cb_info[idx].handle, handle);
		mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
		return -EINVAL;
	}

	if (!list_empty_careful(&iommu_cb_set.cb_info[idx].smmu_buf_list)) {
		CAM_ERR(CAM_SMMU, "UMD %s buffer list is not clean",
			iommu_cb_set.cb_info[idx].name);
		cam_smmu_print_user_list(idx);
		cam_smmu_clean_user_buffer_list(idx);
	}

	if (!list_empty_careful(
		&iommu_cb_set.cb_info[idx].smmu_buf_kernel_list)) {
		CAM_ERR(CAM_SMMU, "KMD %s buffer list is not clean",
			iommu_cb_set.cb_info[idx].name);
		cam_smmu_print_kernel_list(idx);
		cam_smmu_clean_kernel_buffer_list(idx);
	}

	if (iommu_cb_set.cb_info[idx].is_secure) {
		if (iommu_cb_set.cb_info[idx].secure_count == 0) {
			mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
			return -EPERM;
		}

		iommu_cb_set.cb_info[idx].secure_count--;
		if (iommu_cb_set.cb_info[idx].secure_count == 0) {
			iommu_cb_set.cb_info[idx].cb_count = 0;
			iommu_cb_set.cb_info[idx].handle = HANDLE_INIT;
		}

		mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
		return 0;
	}

	iommu_cb_set.cb_info[idx].cb_count = 0;
	iommu_cb_set.cb_info[idx].handle = HANDLE_INIT;
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return 0;
}

static int cam_smmu_create_add_handle_in_table(char *name,
	int *hdl)
{
	int i;
	int handle;

	/* create handle and add in the iommu hardware table */
	for (i = 0; i < iommu_cb_set.cb_num; i++) {
		if (!strcmp(iommu_cb_set.cb_info[i].name, name)) {
			mutex_lock(&iommu_cb_set.cb_info[i].lock);
			if (iommu_cb_set.cb_info[i].handle != HANDLE_INIT) {
				if (iommu_cb_set.cb_info[i].is_secure)
					iommu_cb_set.cb_info[i].secure_count++;

				mutex_unlock(&iommu_cb_set.cb_info[i].lock);
				if (iommu_cb_set.cb_info[i].is_secure) {
					*hdl = iommu_cb_set.cb_info[i].handle;
					return 0;
				}

				CAM_ERR(CAM_SMMU,
					"Error: %s already got handle 0x%x",
					name, iommu_cb_set.cb_info[i].handle);

				return -EINVAL;
			}

			/* make sure handle is unique */
			do {
				handle = cam_smmu_create_iommu_handle(i);
			} while (cam_smmu_check_handle_unique(handle));

			/* put handle in the table */
			iommu_cb_set.cb_info[i].handle = handle;
			iommu_cb_set.cb_info[i].cb_count = 0;
			if (iommu_cb_set.cb_info[i].is_secure)
				iommu_cb_set.cb_info[i].secure_count++;
			*hdl = handle;
			CAM_DBG(CAM_SMMU, "%s creates handle 0x%x",
				name, handle);
			mutex_unlock(&iommu_cb_set.cb_info[i].lock);
			return 0;
		}
	}

	CAM_ERR(CAM_SMMU, "Error: Cannot find name %s or all handle exist",
		name);
	cam_smmu_print_table();
	return -EINVAL;
}

int cam_smmu_ops(int handle, enum cam_smmu_ops_param ops)
{
	int ret = 0, idx;

	if (handle == HANDLE_INIT) {
		CAM_ERR(CAM_SMMU, "Error: Invalid handle");
		return -EINVAL;
	}

	idx = GET_SMMU_TABLE_IDX(handle);
	if (idx < 0 || idx >= iommu_cb_set.cb_num) {
		CAM_ERR(CAM_SMMU, "Error: Index invalid. idx = %d hdl = %x",
			idx, handle);
		return -EINVAL;
	}

	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"Error: hdl is not valid, table_hdl = %x, hdl = %x",
			iommu_cb_set.cb_info[idx].handle, handle);
		mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
		return -EINVAL;
	}

	switch (ops) {
	case CAM_SMMU_ATTACH: {
		ret = cam_smmu_attach(idx);
		break;
	}
	case CAM_SMMU_DETACH: {
		ret = cam_smmu_detach_device(idx);
		break;
	}
	case CAM_SMMU_VOTE:
	case CAM_SMMU_DEVOTE:
	default:
		CAM_ERR(CAM_SMMU, "Error: idx = %d, ops = %d", idx, ops);
		ret = -EINVAL;
	}
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return ret;
}

int cam_smmu_map_user_iova(int handle, int ion_fd, bool dis_delayed_unmap,
	enum cam_smmu_map_dir dir, dma_addr_t *paddr_ptr,
	size_t *len_ptr, enum cam_smmu_region_id region_id)
{
	int idx, rc = 0;
	enum cam_smmu_buf_state buf_state;
	enum dma_data_direction dma_dir;

	rc = cam_smmu_map_iova_validate_params(handle, dir, paddr_ptr,
		len_ptr, region_id);
	if (rc) {
		CAM_ERR(CAM_SMMU, "initial checks failed, unable to proceed");
		return rc;
	}

	dma_dir = (enum dma_data_direction)dir;
	idx = GET_SMMU_TABLE_IDX(handle);
	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].is_secure) {
		CAM_ERR(CAM_SMMU,
			"Error: can't map non-secure mem to secure cb idx=%d",
			idx);
		rc = -EINVAL;
		goto get_addr_end;
	}

	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"hdl is not valid, idx=%d, table_hdl = %x, hdl = %x",
			idx, iommu_cb_set.cb_info[idx].handle, handle);
		rc = -EINVAL;
		goto get_addr_end;
	}

	if (iommu_cb_set.cb_info[idx].state != CAM_SMMU_ATTACH) {
		CAM_ERR(CAM_SMMU,
			"Err:Dev %s should call SMMU attach before map buffer",
			iommu_cb_set.cb_info[idx].name);
		rc = -EINVAL;
		goto get_addr_end;
	}

	buf_state = cam_smmu_check_fd_in_list(idx, ion_fd, paddr_ptr, len_ptr);
	if (buf_state == CAM_SMMU_BUFF_EXIST) {
		CAM_ERR(CAM_SMMU,
			"fd:%d already in list idx:%d, handle=%d, give same addr back",
			ion_fd, idx, handle);
		rc = -EALREADY;
		goto get_addr_end;
	}

	rc = cam_smmu_map_buffer_and_add_to_list(idx, ion_fd,
		dis_delayed_unmap, dma_dir, paddr_ptr, len_ptr, region_id);
	if (rc < 0) {
		CAM_ERR(CAM_SMMU,
			"mapping or add list fail, idx=%d, fd=%d, region=%d, rc=%d",
			idx, ion_fd, region_id, rc);
		cam_smmu_dump_cb_info(idx);
	}

get_addr_end:
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return rc;
}

int cam_smmu_map_kernel_iova(int handle, struct dma_buf *buf,
	enum cam_smmu_map_dir dir, dma_addr_t *paddr_ptr,
	size_t *len_ptr, enum cam_smmu_region_id region_id)
{
	int idx, rc = 0;
	enum cam_smmu_buf_state buf_state;
	enum dma_data_direction dma_dir;

	rc = cam_smmu_map_iova_validate_params(handle, dir, paddr_ptr,
		len_ptr, region_id);
	if (rc) {
		CAM_ERR(CAM_SMMU, "initial checks failed, unable to proceed");
		return rc;
	}

	dma_dir = cam_smmu_translate_dir(dir);
	idx = GET_SMMU_TABLE_IDX(handle);
	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].is_secure) {
		CAM_ERR(CAM_SMMU,
			"Error: can't map non-secure mem to secure cb");
		rc = -EINVAL;
		goto get_addr_end;
	}

	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU, "hdl is not valid, table_hdl = %x, hdl = %x",
			iommu_cb_set.cb_info[idx].handle, handle);
		rc = -EINVAL;
		goto get_addr_end;
	}

	if (iommu_cb_set.cb_info[idx].state != CAM_SMMU_ATTACH) {
		CAM_ERR(CAM_SMMU,
			"Err:Dev %s should call SMMU attach before map buffer",
			iommu_cb_set.cb_info[idx].name);
		rc = -EINVAL;
		goto get_addr_end;
	}

	buf_state = cam_smmu_check_dma_buf_in_list(idx, buf,
	paddr_ptr, len_ptr);
	if (buf_state == CAM_SMMU_BUFF_EXIST) {
		CAM_ERR(CAM_SMMU,
			"dma_buf :%pK already in the list", buf);
		rc = -EALREADY;
		goto get_addr_end;
	}

	rc = cam_smmu_map_kernel_buffer_and_add_to_list(idx, buf, dma_dir,
			paddr_ptr, len_ptr, region_id);
	if (rc < 0)
		CAM_ERR(CAM_SMMU, "mapping or add list fail");

get_addr_end:
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return rc;
}

int cam_smmu_map_stage2_iova(int handle,
		int ion_fd, enum cam_smmu_map_dir dir,
		dma_addr_t *paddr_ptr, size_t *len_ptr)
{
	int idx, rc;
	enum dma_data_direction dma_dir;
	enum cam_smmu_buf_state buf_state;

	if (!paddr_ptr || !len_ptr) {
		CAM_ERR(CAM_SMMU,
			"Error: Invalid inputs, paddr_ptr:%pK, len_ptr: %pK",
			paddr_ptr, len_ptr);
		return -EINVAL;
	}
	/* clean the content from clients */
	*paddr_ptr = (dma_addr_t)NULL;
	*len_ptr = (size_t)0;

	dma_dir = cam_smmu_translate_dir(dir);
	if (dma_dir == DMA_NONE) {
		CAM_ERR(CAM_SMMU,
			"Error: translate direction failed. dir = %d", dir);
		return -EINVAL;
	}

	idx = GET_SMMU_TABLE_IDX(handle);
	if ((handle == HANDLE_INIT) ||
		(idx < 0) ||
		(idx >= iommu_cb_set.cb_num)) {
		CAM_ERR(CAM_SMMU,
			"Error: handle or index invalid. idx = %d hdl = %x",
			idx, handle);
		return -EINVAL;
	}

	if (!iommu_cb_set.cb_info[idx].is_secure) {
		CAM_ERR(CAM_SMMU,
			"Error: can't map secure mem to non secure cb, idx=%d",
			idx);
		return -EINVAL;
	}

	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"Error: hdl is not valid, idx=%d, table_hdl=%x, hdl=%x",
			idx, iommu_cb_set.cb_info[idx].handle, handle);
		rc = -EINVAL;
		goto get_addr_end;
	}

	buf_state = cam_smmu_check_secure_fd_in_list(idx, ion_fd, paddr_ptr,
			len_ptr);
	if (buf_state == CAM_SMMU_BUFF_EXIST) {
		CAM_DBG(CAM_SMMU,
			"fd:%d already in list idx:%d, handle=%d give same addr back",
			ion_fd, idx, handle);
		rc = 0;
		goto get_addr_end;
	}
	rc = cam_smmu_map_stage2_buffer_and_add_to_list(idx, ion_fd, dma_dir,
			paddr_ptr, len_ptr);
	if (rc < 0) {
		CAM_ERR(CAM_SMMU,
			"Error: mapping or add list fail, idx=%d, handle=%d, fd=%d, rc=%d",
			idx, handle, ion_fd, rc);
		goto get_addr_end;
	}

get_addr_end:
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return rc;
}

int cam_smmu_get_iova(int handle, int ion_fd,
	dma_addr_t *paddr_ptr, size_t *len_ptr)
{
	int idx, rc = 0;
	enum cam_smmu_buf_state buf_state;

	if (!paddr_ptr || !len_ptr) {
		CAM_ERR(CAM_SMMU, "Error: Input pointers are invalid");
		return -EINVAL;
	}

	if (handle == HANDLE_INIT) {
		CAM_ERR(CAM_SMMU, "Error: Invalid handle");
		return -EINVAL;
	}

	/* clean the content from clients */
	*paddr_ptr = (dma_addr_t)NULL;
	*len_ptr = (size_t)0;

	idx = GET_SMMU_TABLE_IDX(handle);
	if (idx < 0 || idx >= iommu_cb_set.cb_num) {
		CAM_ERR(CAM_SMMU,
			"Error: handle or index invalid. idx = %d hdl = %x",
			idx, handle);
		return -EINVAL;
	}

	if (iommu_cb_set.cb_info[idx].is_secure) {
		CAM_ERR(CAM_SMMU,
			"Error: can't get non-secure mem from secure cb");
		return -EINVAL;
	}

	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"Error: hdl is not valid, table_hdl = %x, hdl = %x",
			iommu_cb_set.cb_info[idx].handle, handle);
		rc = -EINVAL;
		goto get_addr_end;
	}

	buf_state = cam_smmu_check_fd_in_list(idx, ion_fd, paddr_ptr, len_ptr);
	if (buf_state == CAM_SMMU_BUFF_NOT_EXIST) {
		CAM_ERR(CAM_SMMU, "ion_fd:%d not in the mapped list", ion_fd);
		rc = -EINVAL;
		cam_smmu_dump_cb_info(idx);
		goto get_addr_end;
	}

get_addr_end:
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return rc;
}

int cam_smmu_get_stage2_iova(int handle, int ion_fd,
	dma_addr_t *paddr_ptr, size_t *len_ptr)
{
	int idx, rc = 0;
	enum cam_smmu_buf_state buf_state;

	if (!paddr_ptr || !len_ptr) {
		CAM_ERR(CAM_SMMU, "Error: Input pointers are invalid");
		return -EINVAL;
	}

	if (handle == HANDLE_INIT) {
		CAM_ERR(CAM_SMMU, "Error: Invalid handle");
		return -EINVAL;
	}

	/* clean the content from clients */
	*paddr_ptr = (dma_addr_t)NULL;
	*len_ptr = (size_t)0;

	idx = GET_SMMU_TABLE_IDX(handle);
	if (idx < 0 || idx >= iommu_cb_set.cb_num) {
		CAM_ERR(CAM_SMMU,
			"Error: handle or index invalid. idx = %d hdl = %x",
			idx, handle);
		return -EINVAL;
	}

	if (!iommu_cb_set.cb_info[idx].is_secure) {
		CAM_ERR(CAM_SMMU,
			"Error: can't get secure mem from non secure cb");
		return -EINVAL;
	}

	mutex_lock(&iommu_cb_set.cb_info[idx].lock);
	if (iommu_cb_set.cb_info[idx].handle != handle) {
		CAM_ERR(CAM_SMMU,
			"Error: hdl is not valid, table_hdl = %x, hdl = %x",
			iommu_cb_set.cb_info[idx].handle, handle);
		rc = -EINVAL;
		goto get_addr_end;
	}

	buf_state = cam_smmu_validate_secure_fd_in_list(idx,
		ion_fd,
		paddr_ptr,
		len_ptr);

	if (buf_state == CAM_SMMU_BUFF_NOT_EXIST) {
		CAM_ERR(CAM_SMMU, "ion_fd:%d not in the mapped list", ion_fd);
		rc = -EINVAL;
		goto get_addr_end;
	}

get_addr_end:
	mutex_unlock(&iommu_cb_set.cb_info[idx].lock);
	return rc;
}

static int cam_alloc_smmu_context_banks(struct device *dev)
{
	struct device_node *domains_child_node = NULL;

	if (!dev) {
		CAM_ERR(CAM_SMMU, "Error: Invalid device");
		return -ENODEV;
	}

	iommu_cb_set.cb_num = 0;

	/* traverse thru all the child nodes and increment the cb count */
	for_each_available_child_of_node(dev->of_node, domains_child_node) {
		if (of_device_is_compatible(domains_child_node,
			"qcom,msm-cam-smmu-cb"))
			iommu_cb_set.cb_num++;

		if (of_device_is_compatible(domains_child_node,
			"qcom,qsmmu-cam-cb"))
			iommu_cb_set.cb_num++;
	}

	if (iommu_cb_set.cb_num == 0) {
		CAM_ERR(CAM_SMMU, "Error: no context banks present");
		return -ENOENT;
	}

	/* allocate memory for the context banks */
	iommu_cb_set.cb_info = devm_kzalloc(dev,
		iommu_cb_set.cb_num * sizeof(struct cam_context_bank_info),
		GFP_KERNEL);

	if (!iommu_cb_set.cb_info) {
		CAM_ERR(CAM_SMMU, "Error: cannot allocate context banks");
		return -ENOMEM;
	}

	cam_smmu_reset_iommu_table(CAM_SMMU_TABLE_INIT);
	iommu_cb_set.cb_init_count = 0;

	CAM_DBG(CAM_SMMU, "no of context banks :%d", iommu_cb_set.cb_num);
	return 0;
}
