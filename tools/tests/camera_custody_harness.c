/* Host boundaries for pinned CRM queue/timer/core custody bodies. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#ifndef EUCLEAN
#define EUCLEAN 117
#endif
#ifndef ESHUTDOWN
#define ESHUTDOWN 108
#endif
#ifdef EWOULDBLOCK
#undef EWOULDBLOCK
#endif
#define EWOULDBLOCK 11
#ifdef EOVERFLOW
#undef EOVERFLOW
#endif
#define EOVERFLOW 75
#define GFP_KERNEL 0
#define __GFP_ZERO 0
#define CAM_CRM 0
#define CAM_CORE 0
#define CAM_REQ 0
#define WQ_UNBOUND 1
#define WQ_HIGHPRI 2
#define CAM_WORKQ_FLAG_HIGH_PRIORITY 1
#define CAM_WORKQ_FLAG_SERIAL 2
#define MAXIMUM_LINKS_PER_SESSION 7
#define MAX_LINKS_PER_SESSION 7
#define CAM_REQ_MGR_MAX_SESSION_HANDLES 8
#define CAM_REQ_MGR_MAX_HANDLES 32
#define CAM_REQ_MGR_MAX_HANDLES_V2 64
#define CRM_WORKQ_NUM_TASKS 60
#define CAM_REQ_MGR_LINK_ACTIVATE 1
#define CAM_REQ_MGR_LINK_DEACTIVATE 0
#define CAM_REQ_MGR_LINK_EVT_RESUME 1
#define CAM_REQ_MGR_LINK_EVT_PAUSE 0
#define CAM_REQ_MGR_WATCHDOG_TIMEOUT 1000
#define CAM_CRM_LINK_STATE_AVAILABLE 0
#define CAM_CRM_LINK_STATE_IDLE 1
#define CAM_CRM_LINK_STATE_READY 2
#define CAM_REQ_MGR_DEFAULT_HDL_VAL 0
#define CAM_REQ_MGR_SYNC_MODE_NO_SYNC 0
#define CRM_WORKQ_TASK_NOTIFY_FREEZE 9
#define CAM_IS_NULL_TO_STR(p) ""
#define CAM_DBG(...) do { } while (0)
#define CAM_WARN(...) do { } while (0)
#define CAM_ERR(...) do { } while (0)
#define CAM_ERR_RATE_LIMIT(...) do { } while (0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(n) struct list_head n={&n,&n}
#define INIT_LIST_HEAD(p) do {(p)->next=(p);(p)->prev=(p);}while(0)
static void list_add_tail(struct list_head*n,struct list_head*h){n->prev=h->prev;n->next=h;h->prev->next=n;h->prev=n;}
static void list_add(struct list_head*n,struct list_head*h){n->prev=h;n->next=h->next;h->next->prev=n;h->next=n;}
static void list_del(struct list_head*n){n->prev->next=n->next;n->next->prev=n->prev;}
static void list_del_init(struct list_head*n){list_del(n);INIT_LIST_HEAD(n);}
#define list_empty(p) ((p)->next==(p))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_first_entry(h,t,m) list_entry((h)->next,t,m)
#define list_for_each_entry(p,h,m) for(p=list_entry((h)->next,__typeof__(*p),m);&(p)->m!=(h);p=list_entry((p)->m.next,__typeof__(*p),m))
#define list_for_each_entry_safe(p,n,h,m) for(p=list_entry((h)->next,__typeof__(*p),m),n=list_entry((p)->m.next,__typeof__(*p),m);&(p)->m!=(h);p=n,n=list_entry((n)->m.next,__typeof__(*n),m))
typedef int spinlock_t;
struct mutex { int unused; };
typedef struct {int value;} atomic_t;
#define ATOMIC_INIT(v) {v}
static void atomic_set(atomic_t*a,int n){a->value=n;}
static int atomic_read(atomic_t*a){return a->value;}
static void atomic_add(int n,atomic_t*a){a->value+=n;}
static void atomic_sub(int n,atomic_t*a){a->value-=n;}
#define DEFINE_SPINLOCK(n) spinlock_t n
static int lock_depth;
static void (*unlock_hook)(void);
#define spin_lock_init(p) do{*(p)=0;}while(0)
#define spin_lock_bh(p) do{(void)(p);lock_depth++;}while(0)
#define spin_unlock_bh(p) do{(void)(p);lock_depth--;}while(0)
#define spin_lock(p) spin_lock_bh(p)
#define spin_unlock(p) spin_unlock_bh(p)
#define spin_lock_irqsave(p,f) do{(f)=0;spin_lock_bh(p);}while(0)
#define spin_unlock_irqrestore(p,f) do{(void)(f);spin_unlock_bh(p);if(unlock_hook)unlock_hook();}while(0)
#define mutex_init(p) ((void)(p))
#define mutex_destroy(p) ((void)(p))
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
struct work_struct {void(*fn)(struct work_struct*);int pending;};
struct workqueue_struct {int live;};
#define INIT_WORK(w,f) do{(w)->fn=(f);(w)->pending=0;}while(0)
#define DECLARE_WORK(n,f) struct work_struct n={f,0}
static int work_destroys, frees, alloc_fail, interrupt_context, timer_arms, timer_syncs, wakes;
static int alloc_fail_nth;
static void (*sync_hook)(void);
static void *zalloc(size_t n){if(alloc_fail||(alloc_fail_nth&&!--alloc_fail_nth)){alloc_fail=0;return NULL;}return calloc(1,n);}
#define kzalloc(n,g) zalloc(n)
#define kcalloc(n,z,g) zalloc((n)*(z))
/* Keep host allocations readable so stock UAF is detected by destruction receipts. */
static void kfree(void*p){if(p)frees++;}
static struct workqueue_struct *alloc_workqueue(const char*n,int f,int a,...){return zalloc(sizeof(struct workqueue_struct));}
static void destroy_workqueue(struct workqueue_struct*q){assert(lock_depth==0);work_destroys++;}
static bool queue_work(struct workqueue_struct*q,struct work_struct*w){bool old=w->pending;w->pending=1;return !old;}
static bool schedule_work(struct work_struct*w){bool old=w->pending;w->pending=1;wakes++;return !old;}
static void run_work(struct work_struct*w){if(w->pending){w->pending=0;w->fn(w);}}
static size_t strlcat(char*d,const char*s,size_t n){size_t a=strlen(d),b=strlen(s);if(a<n){size_t k=b<n-a-1?b:n-a-1;memcpy(d+a,s,k);d[a+k]=0;}return a+b;}
enum crm_task_priority {CRM_TASK_PRIORITY_0,CRM_TASK_PRIORITY_1,CRM_TASK_PRIORITY_MAX};
enum crm_workq_context {CRM_WORKQ_USAGE_NON_IRQ,CRM_WORKQ_USAGE_IRQ,CRM_WORKQ_USAGE_INVALID};
struct crm_workq_task {int32_t priority;void*payload;int32_t(*process_cb)(void*,void*);void*parent;struct list_head entry;uint8_t cancel;void*priv;int32_t ret;};
struct cam_req_mgr_core_workq {struct work_struct work;struct workqueue_struct*job;spinlock_t lock_bh;uint32_t in_irq;bool is_static_payload;struct {struct mutex lock;atomic_t pending_cnt,free_cnt;struct list_head process_head[CRM_TASK_PRIORITY_MAX],empty_head;struct crm_workq_task*pool;uint32_t num_task;}task;};
struct timer_list {void(*function)(struct timer_list*);int armed;};
struct cam_req_mgr_timer {int32_t expires;struct timer_list sys_timer;void*parent;void(*timer_cb)(struct timer_list*);bool pause_timer;};
struct kmem_cache {int unused;};
static struct kmem_cache cache;
struct kmem_cache *g_cam_req_mgr_timer_cachep=&cache;
#define timer_setup(t,f,flags) do{(t)->function=(f);(t)->armed=0;}while(0)
static unsigned long jiffies;
#define msecs_to_jiffies(n) (n)
static int mod_timer(struct timer_list*t,unsigned long when){t->armed=1;timer_arms++;return 0;}
static int del_timer_sync(struct timer_list*t){assert(lock_depth==0);timer_syncs++;if(sync_hook){void(*h)(void)=sync_hook;sync_hook=NULL;h();}t->armed=0;return 1;}
static int in_interrupt(void){return interrupt_context;}
#define kmem_cache_alloc(c,g) zalloc(sizeof(struct cam_req_mgr_timer))
#define kmem_cache_free(c,p) kfree(p)
struct cam_req_mgr_core_link;
struct cam_req_mgr_core_dev_link_setup {int link_enable,link_hdl,subscribe_event,dev_hdl;void*crm_cb;};
struct cam_req_mgr_link_evt_data {int evt_type,link_hdl,dev_hdl,req_id;};
struct device_ops {int(*link_setup)(struct cam_req_mgr_core_dev_link_setup*);int(*process_evt)(struct cam_req_mgr_link_evt_data*);};
struct cam_req_mgr_connected_device {int dev_hdl;void*parent;struct device_ops*ops;struct {char name[8];}dev_info;};
struct cam_req_mgr_core_session {int session_hdl;unsigned num_links;struct cam_req_mgr_core_link*links[MAXIMUM_LINKS_PER_SESSION];struct list_head entry;struct mutex lock;int sync_mode;};
struct cam_req_mgr_core_link {int link_hdl,num_devs,state,last_flush_id;struct cam_req_mgr_core_workq*workq;struct cam_req_mgr_timer*watchdog;struct cam_req_mgr_connected_device*l_dev;void*parent;struct mutex lock;spinlock_t link_state_spin_lock;atomic_t is_used;bool is_shutdown,skip_wd_validation;struct{void*in_q;struct mutex lock;}req;};
struct cam_req_mgr_core_device {struct list_head session_head;struct mutex crm_lock;};
struct cam_req_mgr_session_info {int session_hdl;};
struct cam_req_mgr_timer_notify {int link_hdl,state;};
struct crm_task_payload {int type;};
struct cam_req_mgr_unlink_info {int session_hdl,link_hdl;};
struct cam_req_mgr_link_info {int session_hdl,link_hdl,num_devices;};
struct cam_req_mgr_ver_info {union {struct cam_req_mgr_link_info link_info_v1,link_info_v2;}u;};
struct cam_create_dev_hdl {int session_hdl;void*priv;};
struct cam_req_mgr_link_control {int num_links,ops,link_hdls[7],init_timeout[7];};
static int handle_destroys, link_frees, link_info_frees, disconnect_rc, disconnect_calls;
static int handle_creates, create_handle_rc, destroy_handle_rc, link_reserves;
static struct cam_req_mgr_core_session *session_lookup;
static int cam_create_session_hdl(void*p){handle_creates++;if(create_handle_rc)return create_handle_rc;session_lookup=p;return 31;}
static struct cam_req_mgr_core_session *cam_get_session_priv(int h){return session_lookup&&session_lookup->session_hdl==h?session_lookup:NULL;}
static struct cam_req_mgr_core_link *cam_get_link_priv(int h);
static int cam_destroy_link_hdl(int h){handle_destroys++;return 0;}
static int cam_destroy_session_hdl(int h){handle_destroys++;if(destroy_handle_rc)return destroy_handle_rc;session_lookup=NULL;return 0;}
static void __cam_req_mgr_destroy_link_info(struct cam_req_mgr_core_link*l){link_info_frees++;}
static void __cam_req_mgr_destroy_subdev(struct cam_req_mgr_connected_device**d){kfree(*d);*d=NULL;}
static void __cam_req_mgr_free_link(struct cam_req_mgr_core_link*l){link_frees++;kfree(l->req.in_q);l->req.in_q=NULL;l->link_hdl=0;l->workq=NULL;l->parent=NULL;atomic_set(&l->is_used,0);}
static void __cam_req_mgr_unreserve_link(struct cam_req_mgr_core_session*s,struct cam_req_mgr_core_link*l){for(int i=0;i<MAXIMUM_LINKS_PER_SESSION;i++)if(s->links[i]==l){s->links[i]=NULL;s->num_links--;break;}__cam_req_mgr_free_link(l);}
static struct cam_req_mgr_core_link *__cam_req_mgr_reserve_link(struct cam_req_mgr_core_session*s);
static int __cam_req_mgr_create_subdevs(struct cam_req_mgr_connected_device**d,int n){*d=zalloc(sizeof(**d)*(n?n:1));return*d?0:-ENOMEM;}
static int __cam_req_mgr_setup_link_info(struct cam_req_mgr_core_link*l,struct cam_req_mgr_ver_info*i){l->num_devs=0;return 0;}
static int cam_create_link_hdl(struct cam_create_dev_hdl*d){return 100+link_reserves;}
static int __cam_req_mgr_setup_payload(struct cam_req_mgr_core_workq*q){struct crm_task_payload*p=zalloc(q->task.num_task*sizeof(*p));if(!p)return-ENOMEM;for(unsigned i=0;i<q->task.num_task;i++)q->task.pool[i].payload=&p[i];return 0;}
int cam_req_mgr_destroy_session(struct cam_req_mgr_session_info*,bool);
int cam_req_mgr_unlink(struct cam_req_mgr_unlink_info*);
void cam_req_mgr_custody_wake(void);
void cam_req_mgr_custody_retry(void);
int cam_req_mgr_custody_shutdown(void);
void cam_req_mgr_handle_core_shutdown(void);
int cam_req_mgr_custody_reopen(void);
void cam_req_mgr_process_workq(struct work_struct*);
void crm_timer_reset(struct cam_req_mgr_timer*);
void crm_timer_modify(struct cam_req_mgr_timer*,int32_t);
void crm_timer_exit(struct cam_req_mgr_timer**);
int crm_timer_init(struct cam_req_mgr_timer**,int32_t,void*,void(*)(struct timer_list*));
void cam_req_mgr_workq_destroy(struct cam_req_mgr_core_workq**);
int cam_req_mgr_workq_claim(struct cam_req_mgr_core_workq*,void(*)(void*),void*);
int cam_req_mgr_workq_close_owned(struct cam_req_mgr_core_workq*);
int cam_req_mgr_workq_hold_owned(struct cam_req_mgr_core_workq*);
void cam_req_mgr_workq_put_owned(struct cam_req_mgr_core_workq*);
int crm_timer_init_owned(struct cam_req_mgr_timer**,int32_t,void*,void(*)(struct timer_list*),struct cam_req_mgr_core_workq*);
int crm_timer_close_owned(struct cam_req_mgr_timer**);
static int sof_callbacks;
static int __cam_req_mgr_process_sof_freeze(void*p,void*d){sof_callbacks++;return 0;}
enum cam_subdev_rwsem {CAM_SUBDEV_LOCK,CAM_SUBDEV_UNLOCK};
static void cam_req_mgr_rwsem_write_op(enum cam_subdev_rwsem op){assert(lock_depth==0);}
struct file {void *private_data;};
struct v4l2_fh {int live;};
struct v4l2_subdev_fh {int live;};
struct v4l2_subdev;
struct internal_ops {int(*close)(struct v4l2_subdev*,struct v4l2_subdev_fh*);};
struct v4l2_subdev {struct list_head list;int flags;struct internal_ops*internal_ops;char name[8];};
struct v4l2_device {struct list_head subdevs;};
#define V4L2_SUBDEV_FL_HAS_DEVNODE 1
#define to_v4l2_subdev_fh(p) ((struct v4l2_subdev_fh*)(p))
static struct {int open_cnt;struct mutex cam_lock;spinlock_t cam_eventq_lock;struct v4l2_fh*cam_eventq;struct v4l2_device*v4l2_dev;}g_dev;
static int fh_deletes, fh_exits, handle_table_frees, memory_deinits;
static int v4l2_fh_open(struct file*f){f->private_data=zalloc(sizeof(struct v4l2_fh));return f->private_data?0:-ENOMEM;}
static void v4l2_fh_del(struct v4l2_fh*f){fh_deletes++;}
static void v4l2_fh_exit(struct v4l2_fh*f){fh_exits++;}
static int v4l2_fh_release(struct file*f){if(f->private_data){v4l2_fh_del(f->private_data);v4l2_fh_exit(f->private_data);kfree(f->private_data);f->private_data=NULL;}return 0;}
static int cam_req_mgr_util_free_hdls(void){handle_table_frees++;return 0;}
static int cam_mem_mgr_init(void){return 0;}
static void cam_mem_mgr_deinit(void){memory_deinits++;}
/* REAL_FUNCTIONS */
#ifndef QKX_CUSTODY_PATCHED
int cam_req_mgr_workq_claim(struct cam_req_mgr_core_workq*q,void(*cb)(void*),void*p){return 0;}
int cam_req_mgr_workq_close_owned(struct cam_req_mgr_core_workq*q){return 0;}
int cam_req_mgr_workq_hold_owned(struct cam_req_mgr_core_workq*q){return 0;}
void cam_req_mgr_workq_put_owned(struct cam_req_mgr_core_workq*q){}
int crm_timer_init_owned(struct cam_req_mgr_timer**t,int32_t e,void*p,void(*cb)(struct timer_list*),struct cam_req_mgr_core_workq*q){return crm_timer_init(t,e,p,cb);}
int crm_timer_close_owned(struct cam_req_mgr_timer**t){crm_timer_exit(t);return 0;}
void cam_req_mgr_custody_retry(void){}
int cam_req_mgr_custody_shutdown(void){cam_req_mgr_handle_core_shutdown();return 0;}
int cam_req_mgr_custody_reopen(void){return 0;}
#endif
static struct cam_req_mgr_core_link *cam_get_link_priv(int h){for(int i=0;i<MAXIMUM_LINKS_PER_SESSION;i++)if(g_links[i].link_hdl==h)return&g_links[i];return NULL;}
static int callbacks, callback_close, callback_pending, dispatch_destroy;
static struct cam_req_mgr_core_workq *active_q;
#ifndef QKX_CUSTODY_PATCHED
void cam_req_mgr_custody_wake(void){wakes++;}
#endif
static void wake_cb(void*p){assert(lock_depth==0);cam_req_mgr_custody_wake();}
static int cb(void*p,void*d){callbacks++;callback_pending=atomic_read(&active_q->task.pending_cnt);callback_close=cam_req_mgr_workq_close_owned(active_q);cam_req_mgr_workq_destroy(&active_q);dispatch_destroy=active_q==NULL;return -EIO;}
static struct cam_req_mgr_core_workq *new_q(void){struct cam_req_mgr_core_workq*q=NULL;assert(!cam_req_mgr_workq_create("test",3,&q,CRM_WORKQ_USAGE_NON_IRQ,2,true,cam_req_mgr_process_workq));assert(!cam_req_mgr_workq_claim(q,wake_cb,NULL));return q;}
static void timer_cb(struct timer_list*t){callbacks++;}
static struct cam_req_mgr_timer *active_timer;
static int self_cancel_result;
static void timer_self_cancel(struct timer_list*t){self_cancel_result=crm_timer_close_owned(&active_timer);}
static void reset_during_cancel(void){crm_timer_reset(active_timer);crm_timer_modify(active_timer,12);}
static int disconnect(struct cam_req_mgr_core_dev_link_setup*d){disconnect_calls++;return disconnect_rc;}
static struct device_ops ops={disconnect,NULL};
static void new_core(void){g_crm_core_dev=zalloc(sizeof(*g_crm_core_dev));INIT_LIST_HEAD(&g_crm_core_dev->session_head);}
static struct cam_req_mgr_core_link *__cam_req_mgr_reserve_link(struct cam_req_mgr_core_session*s){link_reserves++;for(int i=0;i<MAXIMUM_LINKS_PER_SESSION;i++)if(!atomic_read(&g_links[i].is_used)){struct cam_req_mgr_core_link*l=&g_links[i];memset(l,0,sizeof(*l));l->parent=s;l->req.in_q=zalloc(8);atomic_set(&l->is_used,1);for(int j=0;j<MAXIMUM_LINKS_PER_SESSION;j++)if(!s->links[j]){s->links[j]=l;s->num_links++;return l;}return NULL;}return NULL;}
static void new_link(void){new_core();struct cam_req_mgr_session_info s={0};assert(!cam_req_mgr_create_session(&s));session_lookup->num_links=1;struct cam_req_mgr_core_link*l=&g_links[0];memset(l,0,sizeof(*l));l->link_hdl=77;l->state=CAM_CRM_LINK_STATE_READY;l->parent=session_lookup;l->workq=new_q();l->req.in_q=zalloc(8);l->num_devs=1;l->l_dev=zalloc(sizeof(*l->l_dev));l->l_dev[0].dev_hdl=55;l->l_dev[0].ops=&ops;l->l_dev[0].parent=l;atomic_set(&l->is_used,1);session_lookup->links[0]=l;
#ifdef CONFIG_QKX_OWNER_GATE
#ifdef QKX_CUSTODY_PATCHED
qkx_link_publish(l);
#endif
#endif
}
static void root_init(struct file*f){g_dev.open_cnt=1;g_dev.v4l2_dev=zalloc(sizeof(*g_dev.v4l2_dev));INIT_LIST_HEAD(&g_dev.v4l2_dev->subdevs);v4l2_fh_open(f);g_dev.cam_eventq=f->private_data;}
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
static int race_when_idle, race_hits;
static void wake_at_worker_boundary(void){if((race_when_idle&&!qkx_finalize_running)||(!race_when_idle&&qkx_finalize_running&&!qkx_finalize_dirty)){unlock_hook=NULL;race_hits++;cam_req_mgr_custody_wake();}}
static void stop_at_publication_boundary(void){if(qkx_finalize_running){unlock_hook=NULL;qkx_finalize_stopping=true;qkx_finalize_job.pending=0;}}
#endif
int main(int argc,char**argv){
 if(argc!=2)return 2;const char*c=argv[1];
 if(!strcmp(c,"borrow")){struct cam_req_mgr_core_workq*q=new_q();struct crm_workq_task*t=cam_req_mgr_workq_get_task(q);int first=cam_req_mgr_workq_close_owned(q);cam_req_mgr_workq_destroy(&q);int kept=q!=NULL;int cancel=999,last=999;if(q){t->cancel=1;cancel=cam_req_mgr_workq_enqueue_task(t,NULL,0);last=cam_req_mgr_workq_close_owned(q);cam_req_mgr_workq_destroy(&q);}printf("{\"first\":%d,\"kept\":%d,\"cancel\":%d,\"last\":%d,\"destroyed\":%d}\n",first,kept,cancel,last,work_destroys);}
 else if(!strcmp(c,"running")){active_q=new_q();struct crm_workq_task*t=cam_req_mgr_workq_get_task(active_q);t->process_cb=cb;cam_req_mgr_workq_enqueue_task(t,NULL,0);int first=cam_req_mgr_workq_close_owned(active_q);run_work(&active_q->work);int last=active_q?cam_req_mgr_workq_close_owned(active_q):999;if(active_q)cam_req_mgr_workq_destroy(&active_q);printf("{\"first\":%d,\"pending_in_cb\":%d,\"close_in_cb\":%d,\"freed_in_cb\":%d,\"calls\":%d,\"ret\":%d,\"last\":%d,\"destroyed\":%d,\"wakes\":%d}\n",first,callback_pending,callback_close,dispatch_destroy,callbacks,t->ret,last,work_destroys,wakes);}
 else if(!strcmp(c,"duplicate")){struct cam_req_mgr_core_workq*q=new_q();struct crm_workq_task*t=cam_req_mgr_workq_get_task(q);t->cancel=1;int a=cam_req_mgr_workq_enqueue_task(t,NULL,0);int b=cam_req_mgr_workq_enqueue_task(t,NULL,0);int close=cam_req_mgr_workq_close_owned(q);printf("{\"first\":%d,\"duplicate\":%d,\"close\":%d,\"free\":%d}\n",a,b,close,atomic_read(&q->task.free_cnt));}
 else if(!strcmp(c,"closed")){struct cam_req_mgr_core_workq*q=new_q();int close=cam_req_mgr_workq_close_owned(q);printf("{\"close\":%d,\"denied\":%d}\n",close,cam_req_mgr_workq_get_task(q)==NULL);}
 else if(!strcmp(c,"timer")){struct cam_req_mgr_core_workq*q=new_q();struct cam_req_mgr_timer*t=NULL;int init=crm_timer_init_owned(&t,10,NULL,timer_cb,q);active_timer=t;int first=cam_req_mgr_workq_close_owned(q);int before=timer_arms;sync_hook=reset_during_cancel;int stopped=crm_timer_close_owned(&t);int last=cam_req_mgr_workq_close_owned(q);printf("{\"init\":%d,\"first\":%d,\"stop\":%d,\"last\":%d,\"extra_arms\":%d,\"syncs\":%d,\"null\":%d}\n",init,first,stopped,last,timer_arms-before,timer_syncs,t==NULL);}
 else if(!strcmp(c,"timer_irq")){struct cam_req_mgr_core_workq*q=new_q();struct cam_req_mgr_timer*t=NULL;crm_timer_init_owned(&t,10,NULL,timer_self_cancel,q);active_timer=t;interrupt_context=1;t->sys_timer.function(&t->sys_timer);printf("{\"stop\":%d,\"retained\":%d,\"syncs\":%d,\"close\":%d}\n",self_cancel_result,active_timer!=NULL,timer_syncs,cam_req_mgr_workq_close_owned(q));}
 else if(!strcmp(c,"parent")){new_link();struct cam_req_mgr_core_link*l=&g_links[0];struct crm_workq_task*t=cam_req_mgr_workq_get_task(l->workq);struct cam_req_mgr_session_info s={31};int first=cam_req_mgr_destroy_session(&s,false);int kept=session_lookup!=NULL;int slots=atomic_read(&l->is_used);int before=link_frees;if(kept){t->cancel=1;cam_req_mgr_workq_enqueue_task(t,NULL,0);cam_req_mgr_custody_retry();}printf("{\"first\":%d,\"parent_kept\":%d,\"slot_kept\":%d,\"early_free\":%d,\"parent_after\":%d,\"frees\":%d,\"destroyed\":%d}\n",first,kept,slots,before,session_lookup!=NULL,link_frees,work_destroys);}
 else if(!strcmp(c,"disconnect")){new_link();disconnect_rc=-EIO;struct cam_req_mgr_unlink_info u={31,77};int rc=cam_req_mgr_unlink(&u);struct cam_req_mgr_core_link*l=&g_links[0];printf("{\"rc\":%d,\"dev_kept\":%d,\"parent_kept\":%d,\"slot_kept\":%d,\"destroyed\":%d}\n",rc,l->l_dev&&l->l_dev[0].ops==&ops,session_lookup!=NULL,atomic_read(&l->is_used),work_destroys);}
 else if(!strcmp(c,"callback_lease")){new_link();
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
struct cam_req_mgr_core_link*l=qkx_link_get(77);struct cam_req_mgr_unlink_info u={31,77};int rc=cam_req_mgr_unlink(&u);int deny=qkx_link_get(77)==NULL;int kept=atomic_read(&l->is_used);qkx_link_put(l);cam_req_mgr_custody_retry();printf("{\"rc\":%d,\"denied\":%d,\"kept\":%d,\"after\":%d}\n",rc,deny,kept,atomic_read(&g_links[0].is_used));
#else
printf("{\"rc\":0,\"denied\":0,\"kept\":0,\"after\":0}\n");
#endif
 }
 else if(!strcmp(c,"job_missing")){struct cam_req_mgr_core_workq*q=new_q();struct crm_workq_task*t=cam_req_mgr_workq_get_task(q);q->job=NULL;int rc=cam_req_mgr_workq_enqueue_task(t,NULL,0);printf("{\"rc\":%d,\"free\":%d,\"close\":%d}\n",rc,atomic_read(&q->task.free_cnt),cam_req_mgr_workq_close_owned(q));}
 else if(!strcmp(c,"actual_callback")){new_link();struct cam_req_mgr_timer_notify n={77,0};int ready=cam_req_mgr_cb_notify_timer(&n);g_links[0].state=CAM_CRM_LINK_STATE_IDLE;int inactive=cam_req_mgr_cb_notify_timer(&n);
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
int users=qkx_links[0].users;qkx_link_close(&g_links[0]);int closed=cam_req_mgr_cb_notify_timer(&n);printf("{\"ready\":%d,\"inactive\":%d,\"users\":%d,\"closed\":%d}\n",ready,inactive,users,closed);
#else
printf("{\"ready\":%d,\"inactive\":%d,\"users\":0,\"closed\":%d}\n",ready,inactive,cam_req_mgr_cb_notify_timer(&n));
#endif
}
 else if(!strcmp(c,"watchdog")){new_link();struct file f={0};root_init(&f);struct cam_req_mgr_core_link*l=&g_links[0];struct cam_req_mgr_core_workq*q=l->workq;struct crm_task_payload*payload=calloc(3,sizeof(*payload));for(int i=0;i<3;i++)q->task.pool[i].payload=&payload[i];q->is_static_payload=false;crm_timer_init_owned(&l->watchdog,10,l,__cam_req_mgr_sof_freeze,q);interrupt_context=1;l->watchdog->sys_timer.function(&l->watchdog->sys_timer);interrupt_context=0;int first=cam_req_mgr_close(&f);int early=link_frees;int pending=atomic_read(&q->task.pending_cnt);run_work(&q->work);
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
run_work(&qkx_finalize_job);
#endif
printf("{\"first\":%d,\"early_free\":%d,\"pending\":%d,\"callbacks\":%d,\"syncs\":%d,\"parent_after\":%d,\"fh_cleanup\":%d}\n",first,early,pending,sof_callbacks,timer_syncs,session_lookup!=NULL,fh_deletes);}
 else if(!strcmp(c,"root_close")){new_link();struct file f={0},next={0};root_init(&f);struct cam_req_mgr_core_workq*q=g_links[0].workq;struct crm_workq_task*t=cam_req_mgr_workq_get_task(q);int first=cam_req_mgr_close(&f);int early=fh_deletes+handle_table_frees+memory_deinits;int kept=session_lookup!=NULL;int open=cam_req_mgr_open(&next);struct cam_req_mgr_session_info s={0};int create=cam_req_mgr_create_session(&s);if(kept){t->cancel=1;cam_req_mgr_workq_enqueue_task(t,NULL,0);
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
run_work(&qkx_finalize_job);
#endif
}printf("{\"first\":%d,\"file_detached\":%d,\"parent_kept\":%d,\"early_cleanup\":%d,\"open\":%d,\"create\":%d,\"fh_cleanup\":%d,\"handles\":%d,\"memory\":%d,\"parent_after\":%d}\n",first,f.private_data==NULL,kept,early,open,create,fh_deletes,handle_table_frees,memory_deinits,session_lookup!=NULL);}
 else if(!strcmp(c,"root_failure")){new_link();struct file f={0},next={0};root_init(&f);disconnect_rc=-EIO;int first=cam_req_mgr_close(&f);
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
run_work(&qkx_finalize_job);
#endif
printf("{\"first\":%d,\"parent_kept\":%d,\"fh_cleanup\":%d,\"handles\":%d,\"memory\":%d,\"open\":%d}\n",first,session_lookup!=NULL,fh_deletes,handle_table_frees,memory_deinits,cam_req_mgr_open(&next));}
 else if(!strcmp(c,"session_open_v1")||!strcmp(c,"session_open_v2")){new_core();struct cam_req_mgr_session_info s={0};assert(!cam_req_mgr_create_session(&s));struct cam_req_mgr_ver_info info={0};info.u.link_info_v1.session_hdl=s.session_hdl;int rc=!strcmp(c,"session_open_v1")?cam_req_mgr_link(&info):cam_req_mgr_link_v2(&info);printf("{\"rc\":%d,\"reserves\":%d,\"children\":%u,\"published\":%d}\n",rc,link_reserves,session_lookup->num_links,info.u.link_info_v1.link_hdl>0);}
 else if(!strcmp(c,"session_owner_handle_mismatch")){new_link();session_lookup->session_hdl=32;struct cam_req_mgr_ver_info info={0};info.u.link_info_v1.session_hdl=32;int a=cam_req_mgr_link(&info),b=cam_req_mgr_link_v2(&info);struct cam_req_mgr_session_info s={32};int close=cam_req_mgr_destroy_session(&s,false);printf("{\"v1\":%d,\"v2\":%d,\"close\":%d,\"parent\":%d,\"reserves\":%d}\n",a,b,close,session_lookup!=NULL,link_reserves);}
 else if(!strcmp(c,"session_admission_v1")||!strcmp(c,"session_admission_v2")){new_link();struct crm_workq_task*t=cam_req_mgr_workq_get_task(g_links[0].workq);struct cam_req_mgr_session_info s={31};int close=cam_req_mgr_destroy_session(&s,false);struct cam_req_mgr_ver_info info={0};info.u.link_info_v1.session_hdl=31;int rc=!strcmp(c,"session_admission_v1")?cam_req_mgr_link(&info):cam_req_mgr_link_v2(&info);printf("{\"close\":%d,\"link\":%d,\"reserves\":%d,\"children\":%u}\n",close,rc,link_reserves,session_lookup->num_links);(void)t;}
 else if(!strcmp(c,"session_last_child")){new_link();struct crm_workq_task*t=cam_req_mgr_workq_get_task(g_links[0].workq);struct cam_req_mgr_session_info s={31};int close=cam_req_mgr_destroy_session(&s,false);t->cancel=1;cam_req_mgr_workq_enqueue_task(t,NULL,0);struct cam_req_mgr_unlink_info u={31,77};int unlink=cam_req_mgr_unlink(&u);int before=session_lookup!=NULL;unsigned children=session_lookup?session_lookup->num_links:99;cam_req_mgr_custody_retry();printf("{\"close\":%d,\"unlink\":%d,\"before\":%d,\"children\":%u,\"after\":%d,\"handles\":%d}\n",close,unlink,before,children,session_lookup!=NULL,handle_destroys);}
 else if(!strcmp(c,"session_handle_failure")){new_core();struct cam_req_mgr_session_info s={0};assert(!cam_req_mgr_create_session(&s));destroy_handle_rc=-EIO;int close=cam_req_mgr_destroy_session(&s,false);struct cam_req_mgr_ver_info info={0};info.u.link_info_v1.session_hdl=31;int a=cam_req_mgr_link(&info),b=cam_req_mgr_link_v2(&info);cam_req_mgr_custody_retry();printf("{\"close\":%d,\"v1\":%d,\"v2\":%d,\"parent\":%d,\"reserves\":%d,\"destroy_calls\":%d}\n",close,a,b,session_lookup!=NULL,link_reserves,handle_destroys);}
 else if(!strcmp(c,"session_missing_owner")){new_core();session_lookup=zalloc(sizeof(*session_lookup));session_lookup->session_hdl=31;INIT_LIST_HEAD(&session_lookup->entry);list_add_tail(&session_lookup->entry,&g_crm_core_dev->session_head);struct cam_req_mgr_ver_info info={0};info.u.link_info_v1.session_hdl=31;int a=cam_req_mgr_link(&info),b=cam_req_mgr_link_v2(&info);struct cam_req_mgr_session_info s={31};int close=cam_req_mgr_destroy_session(&s,false);printf("{\"v1\":%d,\"v2\":%d,\"close\":%d,\"parent\":%d,\"reserves\":%d}\n",a,b,close,session_lookup!=NULL,link_reserves);}
 else if(!strcmp(c,"session_alloc_failure")){new_core();alloc_fail_nth=2;struct cam_req_mgr_session_info s={1234};int first=cam_req_mgr_create_session(&s);int untouched=s.session_hdl;int early=handle_creates;int owner_empty=-1;
#ifdef QKX_SESSION_CUSTODY
owner_empty=list_empty(&qkx_sessions);
#endif
alloc_fail_nth=0;int next=first?cam_req_mgr_create_session(&s):999;printf("{\"first\":%d,\"unpublished\":%d,\"early_handles\":%d,\"owner_empty\":%d,\"next\":%d,\"handles\":%d}\n",first,untouched,early,owner_empty,next,handle_creates);}
 else if(!strcmp(c,"session_unpublished_handle_failure")){new_core();create_handle_rc=-ENOSPC;struct cam_req_mgr_session_info s={1234};int first=cam_req_mgr_create_session(&s);int owner_empty=-1;
#ifdef QKX_SESSION_CUSTODY
owner_empty=list_empty(&qkx_sessions);
#endif
int freed=frees;create_handle_rc=0;int next=cam_req_mgr_create_session(&s);printf("{\"first\":%d,\"owner_empty\":%d,\"frees\":%d,\"next\":%d,\"handles\":%d}\n",first,owner_empty,freed,next,handle_creates);}
 else if(!strcmp(c,"session_epoch_overflow")){new_core();
#ifdef QKX_SESSION_CUSTODY
qkx_session_generation=(unsigned long)-1;
#endif
struct cam_req_mgr_session_info s={1234};int rc=cam_req_mgr_create_session(&s);printf("{\"rc\":%d,\"handle\":%d,\"created\":%d,\"published\":%d}\n",rc,s.session_hdl,handle_creates,session_lookup!=NULL);}
 else if(!strcmp(c,"session_stale_retry")){new_core();struct cam_req_mgr_session_info s={0};assert(!cam_req_mgr_create_session(&s));unsigned long generation=0;
#ifdef QKX_SESSION_CUSTODY
generation=qkx_session_find(session_lookup)->generation;
#endif
assert(!cam_req_mgr_destroy_session(&s,false));assert(!cam_req_mgr_create_session(&s));int rc=0;
#ifdef QKX_SESSION_CUSTODY
rc=qkx_session_destroy(&s,false,generation);
#else
rc=cam_req_mgr_destroy_session(&s,false);
#endif
printf("{\"rc\":%d,\"new_parent\":%d,\"destroy_calls\":%d}\n",rc,session_lookup!=NULL,handle_destroys);}
 else if(!strcmp(c,"session_control")){new_link();
#ifdef QKX_SESSION_CUSTODY
qkx_session_find(session_lookup)->closing=true;
#endif
struct cam_req_mgr_link_control control={0};control.num_links=1;control.ops=CAM_REQ_MGR_LINK_ACTIVATE;control.link_hdls[0]=77;int rc=cam_req_mgr_link_control(&control);printf("{\"rc\":%d,\"timer_arms\":%d}\n",rc,timer_arms);}
 else if(!strcmp(c,"wake_publish_stop")){
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
unlock_hook=stop_at_publication_boundary;cam_req_mgr_custody_wake();printf("{\"stopped\":%d,\"late_work\":%d,\"scheduled\":%d}\n",qkx_finalize_stopping,qkx_finalize_job.pending,wakes);
#else
printf("{\"stopped\":0,\"late_work\":1,\"scheduled\":1}\n");
#endif
}
 else if(!strcmp(c,"wake_stopped")){
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
qkx_finalize_stopping=true;cam_req_mgr_custody_wake();printf("{\"dirty\":%d,\"running\":%d,\"pending\":%d,\"scheduled\":%d}\n",qkx_finalize_dirty,qkx_finalize_running,qkx_finalize_job.pending,wakes);
#else
printf("{\"dirty\":0,\"running\":0,\"pending\":0,\"scheduled\":1}\n");
#endif
}
 else if(!strcmp(c,"wake_active")||!strcmp(c,"wake_idle")){
#if defined(CONFIG_QKX_OWNER_GATE) && defined(QKX_CUSTODY_PATCHED)
g_crm_core_dev=zalloc(sizeof(*g_crm_core_dev));INIT_LIST_HEAD(&g_crm_core_dev->session_head);race_when_idle=!strcmp(c,"wake_idle");cam_req_mgr_custody_wake();unlock_hook=wake_at_worker_boundary;run_work(&qkx_finalize_job);int pending=qkx_finalize_job.pending;run_work(&qkx_finalize_job);printf("{\"hits\":%d,\"pending_after_first\":%d,\"dirty_after\":%d,\"running_after\":%d,\"scheduled\":%d}\n",race_hits,pending,qkx_finalize_dirty,qkx_finalize_running,wakes);
#else
printf("{\"hits\":0,\"pending_after_first\":0,\"dirty_after\":0,\"running_after\":0,\"scheduled\":0}\n");
#endif
}
 else if(!strcmp(c,"timer_default")){
struct cam_req_mgr_core_workq*q=new_q();struct cam_req_mgr_timer*t=NULL;
int before=timer_arms;int init=crm_timer_init_owned(&t,10,NULL,NULL,q);
if(t)t->sys_timer.function(&t->sys_timer);
int arms=timer_arms-before;int stop=crm_timer_close_owned(&t);
int last=cam_req_mgr_workq_close_owned(q);cam_req_mgr_workq_destroy(&q);
printf("{\"init\":%d,\"arms\":%d,\"stop\":%d,\"last\":%d,\"null\":%d,\"destroyed\":%d}\n",init,arms,stop,last,t==NULL,work_destroys);
}
 else return 3;
 return 0;
}
