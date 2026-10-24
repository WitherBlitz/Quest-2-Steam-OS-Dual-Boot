/* Deterministic host doubles; execute exact transformed driver bodies below. */
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef ECHRNG
#define ECHRNG 44
#endif
#ifndef ERESTARTSYS
#define ERESTARTSYS 512
#endif
#ifndef ESHUTDOWN
#define ESHUTDOWN 108
#endif
#ifndef EBADR
#define EBADR 53
#endif
#define ADSP_DOMAIN_ID 0
#define NUM_CHANNELS 4
#define FASTRPC_CTX_MAX 256
#define FASTRPC_CTX_MAGIC 0xbeeddeed
#define FASTRPC_ENOSUCH 39
#define FASTRPC_MODE_PARALLEL 1
#define FASTRPC_MODE_SERIAL 0
#define FASTRPC_STATIC_HANDLE_PROCESS_GROUP 1
#define FASTRPC_STATIC_HANDLE_DSP_UTILITIES 2
#define PROCESS_CREATE_DEFAULT 0
#define MINOR_NUM_DEV 0
#define MINOR_NUM_SECURE_DEV 1
#define SESSION_ID_INDEX 30
#define GFP_KERNEL 0
#define REMOTE_SCALARS_LENGTH(sc) (sc)
#define REMOTE_SCALARS_INBUFS(sc) (sc)
#define REMOTE_SCALARS_OUTBUFS(sc) 0
#define MINOR(dev) (dev)
#define VERIFY(err, val) do { if (!(val)) (err) = (err) ? (err) : -1; } while (0)
#define K_COPY_FROM_USER(err, kernel, dst, src, bytes) do { \
	if (copy_failure) (err) = -EFAULT; \
	else if (bytes) memmove((dst), (src), (bytes)); \
} while (0)
#define PERF(enb, cnt, ff) do { ff; } while (0)
#define PERF_END ((void)0)
#define GET_COUNTER(ptr, offset) ((int64_t *)NULL)
#define pr_err(...) ((void)0)
#define LOG_FASTRPC_GLINK_MSG(...) ((void)0)
#define trace_fastrpc_context_alloc(...) ((void)0)
#define trace_fastrpc_context_complete(...) ((void)0)
#define trace_fastrpc_context_restore(...) ((void)0)
#define trace_fastrpc_context_interrupt(...) ((void)0)
#define trace_fastrpc_rpmsg_send(...) ((void)0)
#define trace_fastrpc_context_free(...) do { physical_contexts++; if (locks_held) abort(); } while (0)
#define IS_ERR_OR_NULL(p) (!(p))
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define list_for_each_entry(pos, head, member) \
	for (struct list_head *item = (head)->next; \
	     item != (head) && ((pos) = container_of(item, __typeof__(*(pos)), member), 1); \
	     item = item->next)
static void list_add_tail(struct list_head *n, struct list_head *head)
{
	n->prev = head->prev; n->next = head; head->prev->next = n; head->prev = n;
}
static void list_del(struct list_head *n) { n->prev->next = n->next; n->next->prev = n->prev; }

struct hlist_node { struct hlist_node *next, **pprev; };
struct hlist_head { struct hlist_node *first; };
#define INIT_HLIST_HEAD(h) ((h)->first = NULL)
#define INIT_HLIST_NODE(n) do { (n)->next = NULL; (n)->pprev = NULL; } while (0)
#define hlist_for_each_entry_safe(pos, saved, head, member) \
	for (struct hlist_node *node = (head)->first; \
	     node && ((pos) = container_of(node, __typeof__(*(pos)), member), \
	              (saved) = node->next, 1); node = (saved))
static void hlist_add_fake(struct hlist_node *n) { n->pprev = &n->next; }
static void hlist_add_head(struct hlist_node *n, struct hlist_head *h)
{
	n->next = h->first; if (h->first) h->first->pprev = &n->next;
	h->first = n; n->pprev = &h->first;
}
static void hlist_del_init(struct hlist_node *n)
{
	if (n->pprev) {
		*n->pprev = n->next; if (n->next) n->next->pprev = n->pprev;
	}
	INIT_HLIST_NODE(n);
}

typedef struct { int held; } spinlock_t;
static void (*before_lock_hook)(spinlock_t *);
static int locks_held, physical_contexts, physical_files, put_args_calls;
static int warnings, alloc_calls, fail_allocation, copy_failure;
static void spin_lock(spinlock_t *lock)
{
	if (before_lock_hook) before_lock_hook(lock);
	if (lock->held) abort(); lock->held = 1; locks_held++;
}
static void spin_unlock(spinlock_t *lock)
{
	if (!lock->held) abort(); lock->held = 0; locks_held--;
}
#define DEFINE_SPINLOCK(name) spinlock_t name
#define spin_lock_init(lock) ((lock)->held = 0)
#define spin_lock_irqsave(lock, flags) do { (flags) = 0; spin_lock(lock); } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(flags); spin_unlock(lock); } while (0)
#define WARN_ON_ONCE(cond) ((cond) ? (++warnings, 1) : 0)
struct mutex { int held; };
static void mutex_init(struct mutex *m) { m->held = 0; }
static void mutex_lock(struct mutex *m) { if (m->held) abort(); m->held = 1; }
static void mutex_unlock(struct mutex *m) { if (!m->held) abort(); m->held = 0; }
struct kref { unsigned int count; };
static void kref_init(struct kref *r) { r->count = 1; }
static void kref_get(struct kref *r) { if (!r->count) abort(); r->count++; }
static int kref_get_unless_zero(struct kref *r) { if (!r->count) return 0; r->count++; return 1; }
static unsigned int kref_read(const struct kref *r) { return r->count; }
static void kref_put(struct kref *r, void (*release)(struct kref *))
{
	if (!r->count) abort(); if (!--r->count) release(r);
}
struct task_struct { int pid; };
static struct task_struct caller = { 10 }, worker = { 20 }, receiver = { 30 };
static struct task_struct *current = &caller;
struct completion { unsigned int done; };
static void init_completion(struct completion *c) { c->done = 0; }
static void complete(struct completion *c) { c->done++; }
struct work_struct { void (*fn)(struct work_struct *); bool pending; };
static struct work_struct *queue[512];
static unsigned int queue_count;
#define INIT_WORK(work, callback) do { (work)->fn = (callback); (work)->pending = false; } while (0)
static bool schedule_work(struct work_struct *work)
{
	if (work->pending) return false;
	if (queue_count == 512) abort();
	work->pending = true; queue[queue_count++] = work; return true;
}
static void run_one(void)
{
	struct task_struct *saved = current;
	struct work_struct *work;
	if (!queue_count) abort();
	work = queue[0]; memmove(queue, queue + 1, --queue_count * sizeof(*queue));
	work->pending = false; current = &worker; work->fn(work); current = saved;
}
static void drain(void) { unsigned int n = 0; while (queue_count && ++n < 100) run_one(); if (queue_count) abort(); }
static void *kzalloc(size_t bytes, int flags)
{
	(void)flags; if (locks_held) abort(); alloc_calls++;
	if (fail_allocation && alloc_calls == fail_allocation) return NULL;
	return calloc(1, bytes);
}
static void kfree(void *p) { if (locks_held) abort(); free(p); }

enum fastrpc_response_flags { NORMAL_RESPONSE, USER_EARLY_SIGNAL, EARLY_RESPONSE, COMPLETE_SIGNAL };
typedef struct { struct { void *pv; size_t len; } buf; } remote_arg_t;
typedef remote_arg_t remote_arg64_t;
struct fastrpc_ioctl_invoke { uint32_t handle, sc; remote_arg_t *pra; };
struct fastrpc_ioctl_invoke_crc { struct fastrpc_ioctl_invoke inv; int *fds; unsigned int *attrs; uint32_t *crc; };
struct fastrpc_file;
struct fastrpc_mmap { unsigned int ctx_refs; };
struct fastrpc_buf { uint64_t phys; };
struct overlap { uintptr_t start, end, mstart, mend, offset; };
struct smq_msg { int pid, tid; struct { struct { uint64_t ctx; uint32_t handle, sc; } header;
	struct { uint64_t addr, size; } page; } invoke; };
struct smq_invoke_ctx {
	struct hlist_node hn;
	struct completion work;
	int retval, pid, tgid;
	remote_arg_t *lpra;
	remote_arg64_t *rpra, *lrpra;
	int *fds;
	unsigned int *attrs;
	struct fastrpc_mmap **maps;
	struct fastrpc_buf *buf;
	size_t used;
	struct fastrpc_file *fl;
	uint32_t handle, sc;
	struct overlap *overs, **overps;
	struct smq_msg msg;
	uint32_t *crc;
	unsigned int magic;
	uint64_t ctxid;
	enum fastrpc_response_flags rspFlags;
	uint32_t earlyWakeTime;
	bool isWorkDone;
};
struct fastrpc_ctx_lst { struct hlist_head pending, interrupted; };
struct rpmsg_device { void *ept; };
struct fastrpc_session_ctx { struct { int faults; } smmu; };
struct fastrpc_channel_ctx {
	struct smq_invoke_ctx *ctxtable[FASTRPC_CTX_MAX];
	spinlock_t ctxlock;
	struct mutex smd_mutex, rpmsg_mutex;
	uint64_t ssrcount;
	struct rpmsg_device *rpdev;
	int secure;
};
struct fastrpc_apps { struct fastrpc_channel_ctx *channel; uint64_t jobid[NUM_CHANNELS];
	struct hlist_head drivers; spinlock_t hlock; };
struct fastrpc_file {
	struct hlist_node hn;
	spinlock_t hlock;
	struct hlist_head maps, cached_bufs, remote_bufs, perf;
	struct fastrpc_ctx_lst clst;
	struct fastrpc_session_ctx *sctx;
	struct fastrpc_buf *init_mem;
	struct fastrpc_apps *apps;
	struct kref refcount;
	int cid, tgid, pd, file_close, sessionid, dev_minor, mode, qos_request, dsp_proc_init;
	int dsp_process_state, profile;
	uint64_t ssrcount;
	struct mutex internal_map_mutex, map_mutex, perf_mutex, pm_qos_mutex;
	struct completion shutdown;
	void *procfs_file, *sysfs, *pm_qos_req;
};
struct inode { int i_rdev; };
struct file { void *private_data; };
struct timespec64 { int64_t tv_sec, tv_nsec; };
static struct fastrpc_channel_ctx gcinfo[NUM_CHANNELS];
static struct fastrpc_apps gfa;
static struct fastrpc_session_ctx session;
static struct rpmsg_device rpdev;
static int synchronous_response = -1, send_error, release_error, release_calls;
static int wait_loss, wait_interrupt, release_makes_context, release_loses_transport;
static int fastrpc_file_free(struct fastrpc_file *fl)
{
	if (locks_held) abort(); physical_files++; free(fl); return 0;
}
static int context_build_overlap(struct smq_invoke_ctx *ctx) { (void)ctx; return 0; }
static void fastrpc_mmap_free(struct fastrpc_mmap *map, unsigned int flags) { (void)map; (void)flags; }
static void fastrpc_buf_free(struct fastrpc_buf *buf, int cache) { (void)buf; (void)cache; }
static void fastrpc_pm_awake(struct fastrpc_file *fl, int secure) { (void)fl; (void)secure; }
static uint64_t buf_page_size(uint32_t n) { return n ? n : 4096; }
static void context_notify_user(struct smq_invoke_ctx *, int, uint32_t, uint32_t);
static void fastrpc_notify_users(struct fastrpc_file *);
static int rpmsg_send(void *endpoint, void *msg, size_t size)
{
	struct smq_invoke_ctx *ctx = container_of(msg, struct smq_invoke_ctx, msg);
	struct task_struct *saved = current;
	(void)endpoint; (void)size;
	if (synchronous_response >= 0) {
		spin_lock(&gcinfo[ctx->fl->cid].ctxlock);
		current = &receiver;
		context_notify_user(ctx, 0, (uint32_t)synchronous_response, 10);
		current = saved;
		spin_unlock(&gcinfo[ctx->fl->cid].ctxlock);
	}
	return send_error;
}
static int64_t *getperfcounter(struct fastrpc_file *fl, int key) { (void)fl; (void)key; return NULL; }
#define PERF_COUNT 0
static void ktime_get_real_ts64(struct timespec64 *t) { (void)t; }
static int get_args(uint32_t kernel, struct smq_invoke_ctx *ctx) { (void)kernel; (void)ctx; return 0; }
static void inv_args(struct smq_invoke_ctx *ctx) { (void)ctx; }
static int put_args(uint32_t kernel, struct smq_invoke_ctx *ctx, remote_arg_t *args)
{
	(void)kernel; (void)ctx; (void)args; put_args_calls++; return 0;
}
static void fastrpc_update_invoke_count(uint32_t handle, int64_t *counter, struct timespec64 *t)
{ (void)handle; (void)counter; (void)t; }
static void fastrpc_wait_for_completion(struct smq_invoke_ctx *ctx, int *interrupted, uint32_t kernel)
{
	(void)kernel;
	*interrupted = wait_interrupt;
	if (wait_loss) fastrpc_notify_users(ctx->fl);
}
static int pm_qos_request_active(void **req) { (void)req; return 0; }
static void pm_qos_remove_request(void **req) { (void)req; }
static void proc_remove(void *entry) { (void)entry; }
static void fastrpc_file_sysfs_put(void *sysfs) { (void)sysfs; }
static void context_list_ctor(struct fastrpc_ctx_lst *clst)
{ INIT_HLIST_HEAD(&clst->pending); INIT_HLIST_HEAD(&clst->interrupted); }
struct qkx_owner_token { bool counted; };
#define QKX_OWNER_FASTRPC 1
#define QKX_OWNER_LIFECYCLE 1
#define QKX_OWNER_SUBMIT 0
static int qkx_owner_enter(int domain, int kind, struct qkx_owner_token *token)
{ (void)domain; (void)kind; token->counted = true; return 0; }
static void qkx_owner_leave(struct qkx_owner_token *token) { token->counted = false; }

static inline int fastrpc_file_get(struct fastrpc_file *fl);
static void fastrpc_file_put(struct fastrpc_file *fl);
static void context_free(struct smq_invoke_ctx *ctx);

/* PRODUCTION */

static int fastrpc_release_current_dsp_process(struct fastrpc_file *fl)
{
	struct fastrpc_ioctl_invoke_crc inv = { .inv.handle = 10 };
	struct smq_invoke_ctx *ctx = NULL;
	release_calls++;
	if (release_loses_transport)
		fastrpc_notify_users(fl);
	if (release_makes_context) {
		if (context_alloc(fl, 1, &inv, &ctx)) abort();
		if (fastrpc_invoke_send(ctx, 1, inv.inv.handle)) abort();
		context_free(ctx);
	}
	return release_error;
}

static struct fastrpc_file *new_file(void)
{
	struct inode inode = {0};
	struct file file = {0};
	struct fastrpc_file *fl;
	if (fastrpc_device_open(&inode, &file)) abort();
	fl = file.private_data;
	fl->cid = 0; fl->sctx = &session; fl->tgid = 50;
	return fl;
}
static struct smq_invoke_ctx *new_ctx(struct fastrpc_file *fl)
{
	struct fastrpc_ioctl_invoke_crc inv = { .inv.handle = 10 };
	struct smq_invoke_ctx *ctx = NULL;
	if (context_alloc(fl, 1, &inv, &ctx)) abort();
	return ctx;
}
static void close_file(struct fastrpc_file *fl)
{
	struct file file = { .private_data = fl };
	struct inode inode = {0};
	if (fastrpc_device_release(&inode, &file) || file.private_data) abort();
}
static int check(int got, int want, const char *what)
{
	if (got == want) return 0;
	fprintf(stderr, "%s: got %d expected %d\n", what, got, want); return 1;
}
#define CHECK(got, want, label) do { if (check((got), (want), (label))) return 1; } while (0)
static void reply(struct smq_invoke_ctx *ctx, uint32_t flags)
{
	struct task_struct *saved = current;
	spin_lock(&gcinfo[ctx->fl->cid].ctxlock); current = &receiver;
	context_notify_user(ctx, 0, flags, 10);
	current = saved; spin_unlock(&gcinfo[ctx->fl->cid].ctxlock);
}

#ifdef CONFIG_QKX_OWNER_GATE
static struct fastrpc_file *loss_hook_file;
static unsigned int owner_lock_count;
static void loss_during_parent_acquisition(spinlock_t *lock)
{
	if (lock == &qkx_rpc_lock && ++owner_lock_count == 2) {
		before_lock_hook = NULL;
		fastrpc_notify_users(loss_hook_file);
	}
}
static void loss_before_pending_publication(spinlock_t *lock)
{
	if (lock == &loss_hook_file->hlock) {
		before_lock_hook = NULL;
		fastrpc_notify_users(loss_hook_file);
	}
}
#endif

int main(int argc, char **argv)
{
	struct fastrpc_file *fl;
	struct smq_invoke_ctx *ctx, *restored = NULL;
	struct fastrpc_ioctl_invoke_crc inv = { .inv.handle = 10 };
	if (argc != 2) return 100;
	(void)put_args_calls; (void)release_calls; (void)warnings;
	gfa.channel = gcinfo; gcinfo[0].rpdev = &rpdev;
	if (!strcmp(argv[1], "file_allocation_failure")) {
#ifdef CONFIG_QKX_OWNER_GATE
		struct inode inode = {0};
		struct file file = {0};
		fail_allocation = 2;
		CHECK(fastrpc_device_open(&inode, &file), -ENOMEM, "file sidecar allocation fails");
		CHECK(file.private_data == NULL, 1, "failed open has no external pointer");
		CHECK(gfa.drivers.first == NULL, 1, "failed open never publishes file");
		CHECK(qkx_rpc_files.next == &qkx_rpc_files, 1, "no leaked custodian");
#endif
		return 0;
	}
	fl = new_file();
	if (!strcmp(argv[1], "loss_during_registration") ||
	    !strcmp(argv[1], "loss_before_pending")) {
#ifdef CONFIG_QKX_OWNER_GATE
		loss_hook_file = fl;
		if (!strcmp(argv[1], "loss_during_registration")) {
			before_lock_hook = loss_during_parent_acquisition;
			CHECK(context_alloc(fl, 1, &inv, &ctx), -ESHUTDOWN, "registration rechecks sealed parent");
			CHECK(fl->clst.pending.first == NULL, 1, "rejected context not published");
			CHECK(kref_read(&fl->refcount), 2, "failed registration drops child parent pin");
		} else {
			before_lock_hook = loss_before_pending_publication;
			CHECK(context_alloc(fl, 1, &inv, &ctx), 0, "registered context can finish local allocation");
			CHECK(qkx_rpc_ctx_find(ctx)->unknown, 1, "registry invalidated before pending publication");
			CHECK(fastrpc_invoke_send(ctx, 1, 10), -ESHUTDOWN, "stale context cannot send");
			context_free(ctx);
		}
		CHECK(qkx_rpc_file_find(fl)->loss_error, -ECONNRESET, "loss reason retained");
		CHECK(fastrpc_file_get(fl), 0, "new acquisitions sealed after loss");
		close_file(fl); drain(); CHECK(physical_files, 0, "loss retains parent for recovery");
#endif
		return 0;
	}

	if (!strcmp(argv[1], "allocation_failure")) {
		int original = kref_read(&fl->refcount);
		fail_allocation = alloc_calls + 2;
		CHECK(context_alloc(fl, 1, &inv, &ctx), -ENOMEM, "sidecar allocation fails");
		CHECK(kref_read(&fl->refcount), original, "no leaked parent pin");
		CHECK(fl->clst.pending.first == NULL, 1, "no published context");
		close_file(fl); drain(); CHECK(physical_files, 1, "file retired");
		return 0;
	}
	if (!strcmp(argv[1], "copy_failure")) {
		copy_failure = 1;
		CHECK(context_alloc(fl, 1, &inv, &ctx) != 0, 1, "copy fails");
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_contexts, 0, "copy cleanup is deferred");
#endif
		drain(); CHECK(physical_contexts, 1, "unpublished context cleaned");
		close_file(fl); drain(); CHECK(physical_files, 1, "parent can close");
		return 0;
	}
	if (!strcmp(argv[1], "release_error")) {
		release_error = -EACCES; close_file(fl); drain();
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_files, 0, "failed remote release keeps parent");
		CHECK(qkx_rpc_file_find(fl)->release_error, -EACCES, "original error retained");
		CHECK(qkx_rpc_file_find(fl)->quarantined, 1, "failed phase not retried");
		CHECK(fastrpc_file_get(fl), 0, "quarantined file sealed");
#else
		CHECK(physical_files, 1, "original physical release");
#endif
		return 0;
	}
	if (!strcmp(argv[1], "release_loses_transport")) {
		release_loses_transport = 1;
		close_file(fl); drain();
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_files, 0, "loss during control phase prevents final release");
		CHECK(qkx_rpc_file_find(fl)->release_error, 0, "successful control RPC status preserved");
		CHECK(qkx_rpc_file_find(fl)->loss_error, -ECONNRESET, "concurrent loss reason preserved");
		CHECK(qkx_rpc_file_find(fl)->quarantined, 1, "owner stays sealed");
#else
		CHECK(physical_files, 1, "original parent release");
#endif
		return 0;
	}
	if (!strcmp(argv[1], "own_control_context")) {
		synchronous_response = NORMAL_RESPONSE; release_makes_context = 1;
		close_file(fl);
#ifdef CONFIG_QKX_OWNER_GATE
		run_one(); CHECK(release_calls, 1, "one control RPC");
		CHECK(physical_files, 0, "control context holds parent");
		CHECK(queue_count, 1, "context finalizer queued");
		run_one(); CHECK(physical_contexts, 1, "control context discharged");
		CHECK(queue_count, 1, "last child schedules parent again");
		drain(); CHECK(physical_files, 1, "parent finalizer progresses");
		CHECK(release_calls, 1, "control RPC not repeated");
#else
		CHECK(physical_files, 1, "original parent release");
#endif
		return 0;
	}
	if (!strcmp(argv[1], "loss_stack")) {
		wait_loss = 1;
		CHECK(fastrpc_internal_invoke(fl, FASTRPC_MODE_PARALLEL, 1, &inv) != 0, 1,
		      "unknown control invocation fails");
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(put_args_calls, 0, "loss never copies stack arguments");
		CHECK(physical_contexts, 0, "unknown retained after invocation returns");
		ctx = container_of(fl->clst.pending.first, struct smq_invoke_ctx, hn);
		reply(ctx, NORMAL_RESPONSE); drain();
		CHECK(put_args_calls, 0, "late callback never copies expired stack");
		CHECK(physical_contexts, 0, "late packet cannot authorize unknown cleanup");
		close_file(fl); drain(); CHECK(physical_files, 0, "unknown context holds parent");
#endif
		return 0;
	}
	ctx = new_ctx(fl);
	if (!strcmp(argv[1], "repeat_retire")) {
		context_free(ctx);
#ifdef CONFIG_QKX_OWNER_GATE
		context_free(ctx);
		CHECK(queue_count, 1, "duplicate retirement queues one owner worker");
#endif
		drain(); CHECK(physical_contexts, 1, "one physical finalizer");
		close_file(fl); drain(); CHECK(physical_files, 1, "one parent finalizer");
		return 0;
	}
	if (!strcmp(argv[1], "presend_loss")) {
		fastrpc_notify_users(fl);
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(fastrpc_invoke_send(ctx, 1, 10), -ESHUTDOWN, "loss before send seals context");
		context_free(ctx); drain();
		CHECK(physical_contexts, 0, "pre-send loss keeps uncertain generation");
		close_file(fl); drain(); CHECK(physical_files, 0, "pre-send loss holds parent");
#else
		CHECK(fastrpc_invoke_send(ctx, 1, 10), 0, "original send after loss");
		context_free(ctx); close_file(fl); drain();
#endif
		return 0;
	}
	if (!strcmp(argv[1], "unsubmitted")) {
		context_free(ctx);
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_contexts, 0, "operation unpin never destroys");
#endif
		drain(); CHECK(physical_contexts, 1, "driver worker destroys");
		close_file(fl); drain(); CHECK(physical_files, 1, "parent finalizer");
		return 0;
	}
	if (!strcmp(argv[1], "sync_reply")) synchronous_response = NORMAL_RESPONSE;
	if (!strcmp(argv[1], "sync_reply_send_error")) {
		synchronous_response = NORMAL_RESPONSE; send_error = -ECONNRESET;
	}
	if (!strcmp(argv[1], "send_error")) send_error = -ECONNRESET;
	CHECK(fastrpc_invoke_send(ctx, 1, 10), send_error, "transport result");
	if (!strcmp(argv[1], "sync_reply_send_error")) {
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(qkx_rpc_ctx_completed(ctx), 1, "synchronous terminal proof precedes send error");
		CHECK(qkx_rpc_ctx_find(ctx)->unknown, 0, "send error does not erase terminal proof");
#endif
		context_free(ctx); drain(); CHECK(physical_contexts, 1, "terminal context finalized");
		close_file(fl); drain(); return 0;
	}
	if (!strcmp(argv[1], "sync_reply")) {
		CHECK(ctx->isWorkDone, 1, "synchronous reply delivered");
		CHECK(physical_contexts, 0, "reply cannot end caller operation");
		drain(); CHECK(physical_contexts, 0, "no retirement before caller request");
		context_free(ctx); drain(); CHECK(physical_contexts, 1, "retired after caller");
		close_file(fl); drain(); CHECK(physical_files, 1, "parent retired");
		return 0;
	}
	if (!strcmp(argv[1], "terminal")) {
		context_free(ctx);
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_contexts, 0, "remote lease keeps context");
		reply(ctx, NORMAL_RESPONSE);
		CHECK(physical_contexts, 0, "RX never destroys");
		drain(); CHECK(physical_contexts, 1, "terminal worker destroys");
#else
		CHECK(physical_contexts, 1, "original free is immediate");
#endif
		close_file(fl); drain(); return check(physical_files, 1, "parent retired");
	}
	if (!strcmp(argv[1], "early") || !strcmp(argv[1], "hint")) {
		reply(ctx, !strcmp(argv[1], "early") ? EARLY_RESPONSE : USER_EARLY_SIGNAL);
		context_free(ctx); drain();
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_contexts, 0, "early packet is not completion");
		reply(ctx, COMPLETE_SIGNAL); drain(); CHECK(physical_contexts, 1, "terminal signal releases");
#endif
		close_file(fl); drain(); return check(physical_files, 1, "parent retired");
	}
	if (!strcmp(argv[1], "poll")) {
		reply(ctx, EARLY_RESPONSE);
#ifdef CONFIG_QKX_OWNER_GATE
		qkx_rpc_ctx_complete(ctx, false, true);
		CHECK(qkx_rpc_ctx_completed(ctx), 1, "verified memory marker terminal");
#endif
		context_free(ctx); drain(); CHECK(physical_contexts, 1, "poll completion retires");
		close_file(fl); drain(); return 0;
	}
	if (!strcmp(argv[1], "send_error") || !strcmp(argv[1], "loss") || !strcmp(argv[1], "pd_loss")) {
		if (!strcmp(argv[1], "loss")) fastrpc_notify_users(fl);
		if (!strcmp(argv[1], "pd_loss")) { ctx->msg.pid = 1; fastrpc_notify_users_staticpd_pdr(fl); }
		context_free(ctx); drain();
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(physical_contexts, 0, "unknown remote state is retained");
		reply(ctx, NORMAL_RESPONSE); drain();
		CHECK(physical_contexts, 0, "late response does not clear sticky loss");
		CHECK(qkx_rpc_ctx_find(ctx)->unknown, 1, "sticky loss recorded");
		close_file(fl); drain(); CHECK(physical_files, 0, "unknown holds parent");
#else
		CHECK(physical_contexts, 1, "original cleanup immediate");
		close_file(fl); drain();
#endif
		return 0;
	}
	if (!strcmp(argv[1], "interrupt_restore") || !strcmp(argv[1], "interrupt_close")) {
		context_save_interrupted(ctx);
		if (!strcmp(argv[1], "interrupt_close")) {
			reply(ctx, NORMAL_RESPONSE); close_file(fl); drain();
			CHECK(physical_files, 1, "close retires completed suspended context");
			CHECK(physical_contexts, 1, "suspended child finalized");
			return 0;
		}
		CHECK(context_restore_interrupted(fl, &inv, &restored), 0, "restore succeeds");
		CHECK(restored == ctx, 1, "same retained context");
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(qkx_rpc_ctx_find(ctx)->operation, 1, "restore owns operation");
#endif
		reply(ctx, NORMAL_RESPONSE); context_free(ctx); drain();
		close_file(fl); drain(); CHECK(physical_contexts, 1, "restored child retired");
		return 0;
	}
	if (!strcmp(argv[1], "restore_closed")) {
		context_save_interrupted(ctx);
#ifdef CONFIG_QKX_OWNER_GATE
		qkx_rpc_file_close(fl);
		CHECK(context_restore_interrupted(fl, &inv, &restored), -ESHUTDOWN, "closed file rejects restore");
		CHECK(restored == NULL, 1, "restore never returns unowned pointer");
		reply(ctx, NORMAL_RESPONSE); drain();
#else
		CHECK(context_restore_interrupted(fl, &inv, &restored), 0, "original restore");
		context_free(ctx);
#endif
		close_file(fl); drain(); CHECK(physical_files, 1, "parent retires after restore refusal");
		return 0;
	}
	if (!strcmp(argv[1], "close_blocks_get")) {
		close_file(fl);
#ifdef CONFIG_QKX_OWNER_GATE
		CHECK(fastrpc_file_get(fl), 0, "close seals observer gets");
		CHECK(physical_files, 0, "remote context pins closed parent");
		context_free(ctx); reply(ctx, NORMAL_RESPONSE); drain();
		CHECK(physical_files, 1, "last context finalizer releases parent");
#else
		/* The original implementation does not retain this parent. */
		CHECK(physical_files, 1, "original parent is already freed");
#endif
		return 0;
	}
	return 101;
}
