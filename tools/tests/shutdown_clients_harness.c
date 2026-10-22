/* Exact camera/refcount control flow with host doubles, not DMA completion. */
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <errno.h>
static int rwsem_lock, mutex_depth, core_calls, memory_calls, close_calls, close_error;
enum cam_subdev_rwsem { CAM_SUBDEV_LOCK, CAM_SUBDEV_UNLOCK };
struct file { void *private_data; };
struct v4l2_fh { int unused; };
struct v4l2_subdev_fh { int unused; };
struct v4l2_subdev;
struct close_ops { int (*close)(struct v4l2_subdev *, struct v4l2_subdev_fh *); };
struct v4l2_subdev { unsigned int flags; struct close_ops *internal_ops; };
static struct v4l2_subdev sd_one;
static struct { int subdevs; } v4l2_dev;
static struct { int open_cnt, cam_lock, cam_eventq_lock; void *cam_eventq; typeof(v4l2_dev) *v4l2_dev; } g_dev;
#define V4L2_SUBDEV_FL_HAS_DEVNODE 1
#define CAM_WARN(...) ((void)0)
#define CAM_DBG(...) ((void)0)
#define list_for_each_entry(p,h,m) for ((p)=&sd_one; (p); (p)=NULL)
static void down_write(int *p) { (*p)++; }
static void up_write(int *p) { (*p)--; }
static void mutex_lock(int *p) { (void)p; mutex_depth++; }
static void mutex_unlock(int *p) { (void)p; mutex_depth--; }
static void spin_lock_bh(int *p) { (void)p; }
static void spin_unlock_bh(int *p) { (void)p; }
static struct v4l2_subdev_fh *to_v4l2_subdev_fh(struct v4l2_fh *p) { return (void *)p; }
static void cam_req_mgr_handle_core_shutdown(void) { core_calls++; }
static void v4l2_fh_release(struct file *p) { (void)p; }
static void cam_req_mgr_util_free_hdls(void) {}
static void cam_mem_mgr_deinit(void) { memory_calls++; }
static int subdev_close(struct v4l2_subdev *s, struct v4l2_subdev_fh *f) { (void)s; (void)f; close_calls++; return close_error; }
/* CAMERA_SOURCE */
struct device { int unused; };
struct subsys_desc { const char *poff_depends_on, *name; };
struct subsys_tracking { int lock; };
struct subsys_device { struct subsys_desc *desc; int count; bool do_ramdump_on_put; void *owner; struct device dev; };
static struct subsys_tracking track;
static int stops, frees, reference_releases;
#define IS_ERR_OR_NULL(p) (!(p))
#define WARN(c,...) (c)
static struct subsys_device *find_subsys_device(const char *name) { (void)name; return NULL; }
static struct subsys_tracking *subsys_get_track(struct subsys_device *s) { (void)s; return &track; }
static void subsys_stop(struct subsys_device *s) { (void)s; stops++; }
static void subsystem_ramdump(struct subsys_device *s, void *p) { (void)s; (void)p; }
static void subsystem_free_memory(struct subsys_device *s, void *p) { (void)s; (void)p; frees++; }
static void module_put(void *p) { (void)p; reference_releases++; }
static void put_device(struct device *d) { (void)d; reference_releases++; }
/* DSP_SOURCE */
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(int argc, char **argv) {
    struct file file = {0};
    struct close_ops ops = {.close=subdev_close};
    struct subsys_desc desc = {.name="adsp"};
    struct subsys_device dsp = {.desc=&desc};
    CHECK(argc == 2); g_dev.v4l2_dev = &v4l2_dev;
    sd_one.flags = V4L2_SUBDEV_FL_HAS_DEVNODE; sd_one.internal_ops = &ops;
    if (!strcmp(argv[1], "camera_shared")) {
        g_dev.open_cnt = 2;
        CHECK(cam_req_mgr_close(&file) == 0 && g_dev.open_cnt == 1);
        CHECK(!core_calls && !memory_calls && !close_calls);
        CHECK(rwsem_lock == 1); /* Published early return retains write lock. */
    } else if (!strcmp(argv[1], "camera_final_error")) {
        g_dev.open_cnt = 1; close_error = -EIO;
        CHECK(cam_req_mgr_close(&file) == 0);
        CHECK(core_calls == 1 && memory_calls == 1 && close_calls == 1 && !rwsem_lock);
    } else if (!strcmp(argv[1], "camera_invalid")) {
        CHECK(cam_req_mgr_close(&file) == -EINVAL && !rwsem_lock && !core_calls);
    } else if (!strcmp(argv[1], "dsp_shared")) {
        dsp.count = 2; subsystem_put(&dsp);
        CHECK(dsp.count == 1 && !stops && !frees && reference_releases == 2);
    } else if (!strcmp(argv[1], "dsp_final")) {
        dsp.count = 1; subsystem_put(&dsp);
        CHECK(!dsp.count && stops == 1 && frees == 1 && reference_releases == 2);
    } else if (!strcmp(argv[1], "dsp_invalid")) {
        subsystem_put(&dsp); subsystem_put(NULL);
        CHECK(!dsp.count && !stops && !frees && !reference_releases);
    } else return 2;
    CHECK(!mutex_depth);
    return 0;
}
