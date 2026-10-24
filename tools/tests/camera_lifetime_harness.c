/* SPDX-License-Identifier: GPL-2.0-only */
/* Real pinned driver functions below; only kernel/DMA boundaries are doubled. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <errno.h>

#define CAM_ERR(...) ((void)0)
#define CAM_WARN(...) ((void)0)
#define CAM_DBG(...) ((void)0)
#define CAM_ERR_RATE_LIMIT(...) ((void)0)
#define CAM_GET_TIMESTAMP(...) ((void)0)
#define CAM_GET_TIMESTAMP_DIFF_IN_MICRO(a,b,c) ((void)0)
#define trace_cam_log_event(...) ((void)0)
#define GFP_KERNEL 0
#define HANDLE_INIT (-1)
#define COOKIE_SIZE 16
#define GET_SMMU_TABLE_IDX(handle) ((handle) >> COOKIE_SIZE)
#define CAM_MEM_BUFQ_MAX 4
#define CAM_MEM_MMU_MAX_HANDLE 4
#define CAM_MEM_MGR_GET_HDL_IDX(handle) ((handle) & 3)
#define CAM_MEM_MGR_UNINITIALIZED 0
#define CAM_MEM_MGR_INITIALIZED 1
#define CAM_MEM_FLAG_KMD_ACCESS 1U
#define CAM_MEM_FLAG_HW_SHARED_ACCESS 2U
#define CAM_MEM_FLAG_HW_READ_WRITE 4U
#define CAM_MEM_FLAG_PROTECTED_MODE 8U
#define DMA_BIDIRECTIONAL 0
#define BITS_PER_BYTE 8
#define BITS_TO_LONGS(bits) (((bits) + 63) / 64)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define EXPORT_SYMBOL(x)
#define ATOMIC_INIT(x) (x)
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))

struct list_head { struct list_head *next, *prev; };
static void INIT_LIST_HEAD(struct list_head *head) { head->next = head->prev = head; }
static int list_empty(const struct list_head *head) { return head->next == head; }
#define list_empty_careful list_empty
static void list_add(struct list_head *entry, struct list_head *head)
{ entry->next = head->next; entry->prev = head; head->next->prev = entry; head->next = entry; }
static void list_del_init(struct list_head *entry)
{ entry->prev->next = entry->next; entry->next->prev = entry->prev; INIT_LIST_HEAD(entry); }
#define list_for_each_entry_safe(pos, tmp, head, member) \
    for ((pos) = container_of((head)->next, __typeof__(*(pos)), member), \
         (tmp) = container_of((pos)->member.next, __typeof__(*(pos)), member); \
         &(pos)->member != (head); (pos) = (tmp), \
         (tmp) = container_of((tmp)->member.next, __typeof__(*(tmp)), member))

struct mutex { int held, destroyed; };
static void mutex_init(struct mutex *lock) { lock->held = lock->destroyed = 0; }
static void mutex_lock(struct mutex *lock)
{ if (lock->held || lock->destroyed) abort(); lock->held = 1; }
static void mutex_unlock(struct mutex *lock)
{ if (!lock->held) abort(); lock->held = 0; }
static void mutex_destroy(struct mutex *lock)
{ if (lock->held) abort(); lock->destroyed = 1; }
typedef int atomic_t;
static int atomic_read(const atomic_t *value) { return *value; }
static void atomic_set(atomic_t *value, int next) { *value = next; }
struct kref { int refs; };
static int kref_read(struct kref *ref) { return ref->refs; }
static int kref_put(struct kref *ref, void (*release)(struct kref *))
{ if (ref->refs <= 0) abort(); if (--ref->refs) return 0; release(ref); return 1; }

typedef uintptr_t dma_addr_t;
enum cam_smmu_region_id { CAM_SMMU_REGION_SHARED, CAM_SMMU_REGION_IO, CAM_SMMU_REGION_SECHEAP };
enum cam_smmu_mapping_client { CAM_SMMU_MAPPING_USER, CAM_SMMU_MAPPING_KERNEL };
struct dma_buf { int refs; };
struct dma_buf_attachment { int dma_map_attrs; };
struct sg_table { int unused; };
struct iommu_domain { int unused; };
struct device { int unused; };
struct timespec64 { int unused; };
struct secheap_buf_info { struct dma_buf *buf; struct dma_buf_attachment *attach; struct sg_table *table; };
struct cam_dma_buff_info {
    struct dma_buf *buf;
    struct dma_buf_attachment *attach;
    struct sg_table *table;
    int dir;
    enum cam_smmu_region_id region_id;
    int iommu_dir, ref_count;
    dma_addr_t paddr;
    struct list_head list;
    int ion_fd;
    size_t len, phys_len;
};
struct cam_context_bank_info {
    struct device *dev;
    struct iommu_domain *domain;
    const char *name;
    bool is_secure, secheap_support, is_secheap_allocated;
    struct { dma_addr_t iova_start; size_t iova_len; } secheap_info;
    struct secheap_buf_info secheap_buf;
    struct list_head smmu_buf_list, smmu_buf_kernel_list;
    struct mutex lock;
    int handle, cb_count, secure_count;
    size_t io_mapping_size, shared_mapping_size;
};
struct cam_iommu_cb_set { struct cam_context_bank_info *cb_info; unsigned int cb_num; bool map_profile_enable; };
struct cam_mem_buf_queue {
    struct dma_buf *dma_buf;
    int32_t hdls[CAM_MEM_MMU_MAX_HANDLE], num_hdl, fd, buf_handle, align;
    size_t len;
    uint32_t flags;
    uint64_t vaddr;
    uintptr_t kmdvaddr;
    bool active, is_imported;
    struct kref krefcount;
    enum cam_smmu_mapping_client smmu_mapping_client;
};
struct cam_mem_table { struct mutex m_lock; void *bitmap; size_t bits; struct cam_mem_buf_queue bufq[CAM_MEM_BUFQ_MAX]; };
struct cam_mem_mgr_release_cmd { int32_t buf_handle; };
struct cam_mem_mgr_memory_desc { uint32_t mem_handle; int32_t smmu_hdl; enum cam_smmu_region_id region; };

static struct cam_context_bank_info bank;
static struct cam_dma_buff_info mapping;
static struct dma_buf buffer;
static struct dma_buf_attachment attachment;
static struct sg_table sg;
static struct iommu_domain domain;
static int buffer_puts, frees, unmaps, hw_unmaps, secondary_unmaps;
static int iova_error, hw_error;
static int cpu_error, cpu_unmaps;
static size_t unmap_result;
static void *kzalloc(size_t count, int flags) { return calloc(1, count); }
static void kfree(void *pointer) { if (pointer == &mapping) frees++; }
static void bitmap_zero(void *bitmap, size_t bits) { memset(bitmap, 0, (bits + 7) / 8); }
static void set_bit(int bit, void *bitmap) { ((unsigned char *)bitmap)[bit / 8] |= 1U << (bit % 8); }
static void clear_bit(int bit, void *bitmap) { ((unsigned char *)bitmap)[bit / 8] &= ~(1U << (bit % 8)); }
static size_t iommu_unmap(struct iommu_domain *d, dma_addr_t address, size_t length)
{ if (d != &domain || address != 0x1000 || length != 0x2000) abort(); unmaps++; return unmap_result; }
static int cam_smmu_free_iova(uint32_t address, size_t length, int32_t handle)
{ if (address != 0x1000 || length != 0x2000 || handle != 17) abort(); return iova_error; }
static void dma_buf_unmap_attachment(struct dma_buf_attachment *a, struct sg_table *t, int direction)
{ if (a != &attachment || t != &sg) abort(); }
static void dma_buf_detach(struct dma_buf *b, struct dma_buf_attachment *a)
{ if (b != &buffer || a != &attachment) abort(); }
static void dma_buf_put(struct dma_buf *b) { if (b != &buffer || b->refs <= 0) abort(); buffer_puts++; b->refs--; }
static void cam_smmu_update_monitor_array(struct cam_context_bank_info *b, bool mapped, struct cam_dma_buff_info *m) {}
static int cam_smmu_free_scratch_buffer_remove_from_list(struct cam_dma_buff_info *m, int idx) { abort(); }
static void cam_smmu_print_user_list(int idx) {}
static void cam_smmu_print_kernel_list(int idx) {}
static void cam_smmu_print_table(void) {}
static int cam_smmu_create_iommu_handle(int idx) { return 19; }
static int cam_smmu_check_handle_unique(int handle) { return 0; }
static void cam_mem_mgr_unmap_active_buf(int idx) { hw_unmaps++; }
static int cam_mem_mgr_create_debug_fs(void) { return 0; }
static int cam_mem_util_unmap_cpu_va(struct dma_buf *buf, uint64_t address)
{ cpu_unmaps++; return cpu_error; }
static int cam_mem_util_unmap_hw_va(int32_t idx, enum cam_smmu_region_id region, enum cam_smmu_mapping_client client)
{ hw_unmaps++; return hw_error; }
static int cam_smmu_release_sec_heap(int32_t smmu_hdl);

/* REAL_FUNCTIONS */

static void reset(void)
{
    buffer_puts = frees = unmaps = hw_unmaps = secondary_unmaps = 0;
    iova_error = hw_error = 0; unmap_result = 0x2000;
    cpu_error = cpu_unmaps = 0;
    memset(&bank, 0, sizeof(bank)); memset(&mapping, 0, sizeof(mapping));
    memset(&tbl, 0, sizeof(tbl));
    buffer.refs = 4;
    bank.name = "test"; bank.handle = 17; bank.domain = &domain; bank.cb_count = 2;
    bank.shared_mapping_size = 0x2000; bank.secheap_support = true;
    bank.is_secheap_allocated = true; bank.secheap_info.iova_start = 0x1000;
    bank.secheap_info.iova_len = 0x2000; bank.secheap_buf.buf = &buffer;
    bank.secheap_buf.attach = &attachment; bank.secheap_buf.table = &sg;
    INIT_LIST_HEAD(&bank.smmu_buf_list); INIT_LIST_HEAD(&bank.smmu_buf_kernel_list);
    iommu_cb_set.cb_info = &bank; iommu_cb_set.cb_num = 1;
    mapping.buf = &buffer; mapping.attach = &attachment; mapping.table = &sg;
    mapping.paddr = 0x1000; mapping.len = 0x2000; mapping.region_id = CAM_SMMU_REGION_SHARED;
    mapping.ion_fd = 3; INIT_LIST_HEAD(&mapping.list); list_add(&mapping.list, &bank.smmu_buf_list);
    tbl.bitmap = calloc(1, 8); tbl.bits = 64; cam_mem_mgr_state = CAM_MEM_MGR_INITIALIZED;
    tbl.bufq[1].active = true; tbl.bufq[1].buf_handle = 1; tbl.bufq[1].dma_buf = &buffer;
    tbl.bufq[1].vaddr = 0x1000; tbl.bufq[1].len = 0x2000; tbl.bufq[1].num_hdl = 1;
    tbl.bufq[1].hdls[0] = 17; tbl.bufq[1].krefcount.refs = 1;
    tbl.bufq[1].flags = CAM_MEM_FLAG_HW_READ_WRITE;
#if defined(QKX_CLEANUP_PATCHED) && defined(CONFIG_QKX_OWNER_GATE)
    static int errors[1]; errors[0] = 0; qkx_smmu_cleanup_errors = errors;
    memset(qkx_mem_cleanup_errors, 0, sizeof(qkx_mem_cleanup_errors));
    qkx_mem_deinit_pending = false;
#endif
}

int main(int argc, char **argv)
{
    int rc, again;
    if (argc != 2) return 2;
    reset();
    if (!strncmp(argv[1], "shared_", 7) || !strcmp(argv[1], "iova_failure")) {
        if (!strcmp(argv[1], "shared_short")) unmap_result = 0x1000;
        if (!strcmp(argv[1], "shared_zero")) unmap_result = 0;
        if (!strcmp(argv[1], "shared_over")) unmap_result = (size_t)-5;
        if (!strcmp(argv[1], "iova_failure")) iova_error = -12;
        mutex_lock(&bank.lock); rc = cam_smmu_unmap_buf_and_remove_from_list(&mapping, 0);
        if (!strcmp(argv[1], "shared_success")) {
            mutex_unlock(&bank.lock);
            printf("{\"rc\":%d,\"puts\":%d,\"frees\":%d,\"linked\":%d}\n", rc, buffer_puts, frees, !list_empty(&bank.smmu_buf_list));
        } else {
            again = frees ? rc : cam_smmu_unmap_buf_and_remove_from_list(&mapping, 0);
            mutex_unlock(&bank.lock);
            printf("{\"rc\":%d,\"again\":%d,\"unmaps\":%d,\"puts\":%d,\"frees\":%d,\"linked\":%d}\n",
                   rc, again, unmaps, buffer_puts, frees, !list_empty(&bank.smmu_buf_list));
        }
    } else if (!strcmp(argv[1], "secondary_short")) {
        unmap_result = 0x1000; rc = cam_smmu_release_sec_heap(17);
        again = bank.is_secheap_allocated ? cam_smmu_release_sec_heap(17) : rc;
        printf("{\"rc\":%d,\"again\":%d,\"unmaps\":%d,\"puts\":%d,\"allocated\":%d}\n", rc, again, unmaps, buffer_puts, bank.is_secheap_allocated);
    } else if (!strcmp(argv[1], "destroy_failure") || !strcmp(argv[1], "reacquire_failure")) {
        unmap_result = 0x1000; rc = cam_smmu_destroy_handle(17);
        if (!strcmp(argv[1], "reacquire_failure")) {
            int returned = -1; rc = cam_smmu_create_add_handle_in_table("test", &returned);
            printf("{\"rc\":%d,\"handle\":%d,\"returned\":%d}\n", rc, bank.handle, returned);
        } else printf("{\"rc\":%d,\"handle\":%d,\"callbacks\":%d,\"puts\":%d,\"linked\":%d}\n", rc, bank.handle, bank.cb_count, buffer_puts, !list_empty(&bank.smmu_buf_list));
    } else if (!strcmp(argv[1], "memory_deinit")) {
        cam_mem_mgr_deinit();
        int state = cam_mem_mgr_state, active = tbl.bufq[1].active;
        int bitmap = tbl.bitmap != NULL, destroyed = tbl.m_lock.destroyed;
        rc = cam_mem_mgr_init();
        printf("{\"state\":%d,\"active\":%d,\"bitmap\":%d,\"puts\":%d,\"lock_destroyed\":%d,\"init_rc\":%d}\n", state, active, bitmap, buffer_puts, destroyed, rc);
    } else if (!strcmp(argv[1], "memory_release")) {
        struct cam_mem_mgr_release_cmd cmd = {1}; hw_error = -5;
        rc = cam_mem_mgr_release(&cmd);
        again = tbl.bufq[1].active ? cam_mem_mgr_release(&cmd) : rc;
        printf("{\"rc\":%d,\"again\":%d,\"active\":%d,\"handle\":%d,\"puts\":%d,\"hw_unmaps\":%d}\n", rc, again, tbl.bufq[1].active, tbl.bufq[1].buf_handle, buffer_puts, hw_unmaps);
    } else if (!strcmp(argv[1], "memory_cpu_failure")) {
        struct cam_mem_mgr_release_cmd cmd = {1}; cpu_error = -12;
        tbl.bufq[1].kmdvaddr = 0x2000; tbl.bufq[1].flags |= CAM_MEM_FLAG_KMD_ACCESS;
        rc = cam_mem_mgr_release(&cmd);
        again = tbl.bufq[1].active ? cam_mem_mgr_release(&cmd) : rc;
        printf("{\"rc\":%d,\"again\":%d,\"active\":%d,\"cpu_address\":%llu,\"puts\":%d,\"hw_unmaps\":%d,\"cpu_unmaps\":%d}\n", rc, again, tbl.bufq[1].active, (unsigned long long)tbl.bufq[1].kmdvaddr, buffer_puts, hw_unmaps, cpu_unmaps);
    } else if (!strcmp(argv[1], "memory_nonfinal_release")) {
        struct cam_mem_mgr_memory_desc desc = {1, 17, CAM_SMMU_REGION_IO};
        tbl.bufq[1].krefcount.refs = 2; rc = cam_mem_mgr_release_mem(&desc);
        printf("{\"rc\":%d,\"refs\":%d,\"active\":%d,\"hw_unmaps\":%d}\n", rc, tbl.bufq[1].krefcount.refs, tbl.bufq[1].active, hw_unmaps);
    } else if (!strcmp(argv[1], "memory_secondary_failure")) {
        struct cam_mem_mgr_memory_desc desc = {1, 17, CAM_SMMU_REGION_SECHEAP};
        unmap_result = 0x1000; rc = cam_mem_mgr_free_memory_region(&desc);
        printf("{\"rc\":%d,\"refs\":%d,\"active\":%d,\"puts\":%d}\n", rc, tbl.bufq[1].krefcount.refs, tbl.bufq[1].active, buffer_puts);
    } else if (!strcmp(argv[1], "memory_secondary_retained")) {
        struct cam_mem_mgr_memory_desc desc = {1, 17, CAM_SMMU_REGION_SECHEAP};
        tbl.bufq[1].krefcount.refs = 2; tbl.bufq[1].smmu_mapping_client = CAM_SMMU_MAPPING_KERNEL;
        rc = cam_mem_mgr_free_memory_region(&desc);
        printf("{\"rc\":%d,\"refs\":%d,\"secondary_unmaps\":%d,\"active\":%d}\n", rc, tbl.bufq[1].krefcount.refs, unmaps, tbl.bufq[1].active);
    } else return 3;
    if (bank.lock.held || tbl.m_lock.held) return 4;
    return 0;
}
