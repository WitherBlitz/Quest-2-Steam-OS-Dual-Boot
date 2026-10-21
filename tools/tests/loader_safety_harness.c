/* Host API doubles for actual loader control flow. No device I/O is possible. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <setjmp.h>
#ifndef _WIN32
#include <sys/types.h>
#endif
#define __maybe_unused
#define __init
#define __iomem
#define __noreturn
#define SZ_4K 4096
#define PAGE_SIZE 4096
#define QKX_MAX_PAGES 32768
#define QKX_ENTRY 0x90080000
#define QKX_DTB 0x93200000
#define QKX_LOG_SIZE 0x10000
#define QKX_CTXAREA_SIZE 0x3000
#define QKX_LOG_PAGES 16
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((intptr_t)(p) < 0)
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x) = (v))
#define read_sysreg(x) 4
#define pr_emerg(...) ((void)0)
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define cpu_relax() ((void)0)
#define msleep(n) ((void)0)
#define mb() ((void)0)
#define local_irq_disable() ((void)0)
#define preempt_disable() ((void)0)
#define __flush_dcache_area(a,b) ((void)0)
#define __flush_icache_range(a,b) ((void)0)
#define virt_to_phys(p) ((uintptr_t)(p))
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#ifdef _WIN32
typedef long long loff_t;
typedef long ssize_t;
#endif
typedef int atomic_t;
struct file;
struct resource;
struct page;
struct mbox_chan;
struct task_struct;
struct device_attribute;
struct device;
struct device_node { const char *compatible; };
struct dev_pm_ops { int (*suspend)(struct device *); int (*resume)(struct device *); };
struct device_driver { const char *name; const struct dev_pm_ops *pm; };
struct device { const char *name; struct device_driver *driver; struct device_node *of_node; void *data; };
struct bus_type { int unused; };
struct qkx_log_header { u32 magic, write_pos, wraps, reserved; };
static bool execute, preserve_watchdog, watchdog_recovery, watchdog_control;
static bool validate_ram;
static bool ram_diagnose;
static unsigned long long ram_base;
static bool test_transition, test_full_copy, post_shutdown_probe, warm_reset_test;
static bool flush_rpmh, suspend_syncboss, disconnect_qmp, write_test, clear;
static unsigned int test_cpus, phase_delay_ms, preflight_only = 1;
static unsigned int core_hang_control, secure_watchdog_control;
static atomic_t abort_suspend, qkx_suppress_wakeup_abort, *qkx_pm_abort_suspend;
static unsigned int nr_owned;
static struct page **owned;
static void *code_page, *pgd_page, *first_desc;
static char qkx_reloc_start[1], qkx_reloc_end[1];
static int destructive_calls, put_count, audio_released, missing_rpmh_device;
static int no_irq, no_qmp_shutdown, no_rpmh;
/* Historical PM rollback paths remain exercised on the legacy board. */
static const char *model = "Seacliff";
static u32 mode[2] = { 0xe11, 0xe11 };
static bool events_check;
static bool destination_owned;
static int freeze_ret, gpu_suspend_ret, sync_suspend_ret, sync_resume_ret, watchdog_ret = 1, rpmh_ret;
static int did_panic, did_enter;
static char trace[128];
static unsigned int trace_len;
static jmp_buf failure;
static void event(char e) { trace[trace_len++] = e; trace[trace_len] = 0; }
static struct bus_type platform, spi;
static int noop_pm(struct device *d) { destructive_calls++; return 0; }
static const struct dev_pm_ops pm = { noop_pm, noop_pm }, no_resume = { noop_pm, NULL };
static struct device_driver audio_driver = { "cm710x_spi", &pm };
static struct device_driver sync_driver = { "syncboss_spi", &pm };
static struct device_node audio_node = { "C-Media,cm710x-spi" };
static struct device_node sync_node = { "meta,syncboss-spi" };
static struct device gpu = { "gpu" }, watchdog = { "wdt" }, usb = { "usb" };
static struct device audio = { "spi0.0", &audio_driver, &audio_node };
static struct device syncboss = { "spi1.0", &sync_driver, &sync_node };
static struct device aop = { "aop" }, rpmh = { "rpmh" };
static struct device_node root_node, *of_root = &root_node;
static int freeze_users(void) { destructive_calls++; event('F'); return freeze_ret; }
static void thaw_users(void) { destructive_calls++; event('T'); }
static void sleep_lock(void) { destructive_calls++; event('L'); }
static void sleep_unlock(void) { event('U'); }
static int gpu_suspend(struct device *d) { destructive_calls++; event('G'); return gpu_suspend_ret; }
static int gpu_resume(struct device *d) { event('g'); return 0; }
static int sync_suspend(struct device *d) { event('S'); return sync_suspend_ret; }
static int sync_resume(struct device *d) { event('s'); return sync_resume_ret; }
static const struct dev_pm_ops sync_pm = { sync_suspend, sync_resume };
static int usb_suspend(void *d) { event('B'); return 0; }
static ssize_t wdt_disable(struct device *d, struct device_attribute *a, const char *s, size_t n)
{ event('W'); return watchdog_ret; }
static int rpmh_flush_stub(const struct device *d) { event('R'); return rpmh_ret; }
static void prepare_stub(char *s) { event('P'); }
static int of_property_read_string(struct device_node *n, const char *p, const char **out)
{ if (!model) return -EINVAL; *out = model; return 0; }
static int of_device_is_compatible(const struct device_node *n, const char *s)
{ return n && !strcmp(n->compatible, s); }
static int of_machine_is_compatible(const char *s) { return 1; }
static int qkx_validate_ram(void) { return 0; }
static const char *dev_name(const struct device *d) { return d->name; }
static void *dev_get_drvdata(struct device *d) { return d ? d->data : NULL; }
static void put_device(struct device *d) { if (d) put_count++; if (d == &audio) audio_released++; }
static struct device *find_by_name(struct bus_type *b, struct device *start, const char *name)
{
    if (!strcmp(name, "3d00000.qcom,kgsl-3d0")) return &gpu;
    if (!strcmp(name, "17c10000.qcom,wdt")) return &watchdog;
    if (!strcmp(name, "a600000.dwc3")) return &usb;
    if (!strcmp(name, "c300000.qcom,qmp-aop")) return &aop;
    if (!strcmp(name, "18200000.rsc:rpmh-regulator-mmcxlvl")) return missing_rpmh_device ? NULL : &rpmh;
    if (!strcmp(name, "spi0.0")) return &audio;
    return NULL;
}
static struct device *bus_find_device(struct bus_type *b, struct device *start,
                                     void *data, int (*match)(struct device *, void *))
{
    if (match(&audio, data)) return &audio;
    if (match(&syncboss, data)) return &syncboss;
    return NULL;
}
static bool not_stuck(void) { return false; }
static void destructive(void) { destructive_calls++; }
static uintptr_t lookup(const char *s)
{
    if (!strcmp(s, "__boot_cpu_mode")) return (uintptr_t)mode;
    if (!strcmp(s, "cpus_are_stuck_in_kernel")) return (uintptr_t)not_stuck;
    if (!strcmp(s, "platform_bus_type")) return (uintptr_t)&platform;
    if (!strcmp(s, "spi_bus_type")) return (uintptr_t)&spi;
    if (!strcmp(s, "bus_find_device_by_name")) return (uintptr_t)find_by_name;
    if (!strcmp(s, "events_check_enabled")) return (uintptr_t)&events_check;
    if (!strcmp(s, "pm_abort_suspend")) return (uintptr_t)&abort_suspend;
    if (!strcmp(s, "kexec_in_progress")) return 0;
    if (!strcmp(s, "send_irq") && no_irq) return 0;
    if (!strcmp(s, "qmp_shutdown") && no_qmp_shutdown) return 0;
    if (!strcmp(s, "rpmh_flush") && no_rpmh) return 0;
    if (!strcmp(s, "freeze_processes")) return (uintptr_t)freeze_users;
    if (!strcmp(s, "thaw_processes")) return (uintptr_t)thaw_users;
    if (!strcmp(s, "lock_system_sleep")) return (uintptr_t)sleep_lock;
    if (!strcmp(s, "unlock_system_sleep")) return (uintptr_t)sleep_unlock;
    if (!strcmp(s, "adreno_pm_suspend")) return (uintptr_t)gpu_suspend;
    if (!strcmp(s, "adreno_pm_resume")) return (uintptr_t)gpu_resume;
    if (!strcmp(s, "dwc3_gadget_suspend")) return (uintptr_t)usb_suspend;
    if (!strcmp(s, "wdog_disable_set")) return (uintptr_t)wdt_disable;
    if (!strcmp(s, "rpmh_flush")) return (uintptr_t)rpmh_flush_stub;
    if (!strcmp(s, "kernel_restart_prepare")) return (uintptr_t)prepare_stub;
    return (uintptr_t)destructive;
}
static void qkx_phase(unsigned int p, const char *s, bool b) { }
static void *ioremap(uintptr_t p, size_t s) { destructive_calls++; return NULL; }
static void iounmap(void *p) { destructive_calls++; }
static u32 readl_relaxed(void *p) { destructive_calls++; return 0; }
static void writel_relaxed(u32 n, void *p) { destructive_calls++; }
static void atomic_set(atomic_t *p, int n) { *p = n; }
static int atomic_read(atomic_t *p) { return *p; }
static int qkx_wakeup_suppressor(void *p) { return 0; }
static struct task_struct *kthread_run(int (*fn)(void *), void *p, const char *s)
{ destructive_calls++; abort_suspend = -1000000; event('K'); return (void *)1; }
static void kthread_stop(struct task_struct *t) { destructive_calls++; }
static int qkx_test_cpus(void) { destructive_calls++; return 0; }
static int qkx_test_transition(void) { destructive_calls++; return 0; }
static int qkx_disconnect_aop_qmp(struct device *d, ...) { destructive_calls++; return 0; }
static int num_online_cpus(void) { return 1; }
static void *page_address(struct page *p) { return NULL; }
static void qkx_enter(uintptr_t a, uintptr_t b, uintptr_t c, u64 d, u64 e)
{ did_enter++; longjmp(failure, 1); }
static void panic(const char *s, ...) { did_panic++; longjmp(failure, 1); }
/* PRODUCTION */
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char **argv)
{
    const char *which = argc > 1 ? argv[1] : "";
    int ret;
    usb.data = &usb; aop.data = &aop;
    if (!strcmp(which, "hollywood_options")) {
        bool *actions[] = { &execute, &test_transition, &test_full_copy, &warm_reset_test,
                            &suspend_syncboss, &disconnect_qmp, &flush_rpmh, &preserve_watchdog };
        unsigned int i;
        model = "Hollywood (DVT)";
        preflight_only = 0;
        for (i = 0; i < sizeof(actions)/sizeof(actions[0]); i++) {
            *actions[i] = true;
            CHECK(check_options() == -EOPNOTSUPP);
            *actions[i] = false;
        }
        test_cpus = 1; CHECK(check_options() == -EOPNOTSUPP); test_cpus = 0;
        core_hang_control = 1; CHECK(check_options() == -EOPNOTSUPP); core_hang_control = 0;
        secure_watchdog_control = 1; CHECK(check_options() == -EOPNOTSUPP);
    } else if (!strcmp(which, "hollywood_jump") || !strcmp(which, "hollywood_rehearsal") ||
               !strcmp(which, "unknown_board")) {
        model = !strcmp(which, "unknown_board") ? NULL : "Hollywood (DVT)";
        destination_owned = true;
        preflight_only = !strcmp(which, "hollywood_rehearsal") ? 2 : 0;
        ret = 999;
        if (!setjmp(failure)) ret = qkx_execute();
        CHECK(ret == (model ? -EOPNOTSUPP : -ENODEV));
        CHECK(!did_enter && !did_panic && !trace_len && !put_count);
    } else if (!strcmp(which, "unowned_jump")) {
        preflight_only = 0;
        ret = 999;
        if (!setjmp(failure)) ret = qkx_execute();
        CHECK(ret == -EPERM);
    } else if (!strcmp(which, "rehearsal_order") || !strcmp(which, "freezer_abort") ||
               !strcmp(which, "gpu_failure") || !strcmp(which, "syncboss_failure") ||
               !strcmp(which, "resume_failure") ||
               !strcmp(which, "watchdog_failure") || !strcmp(which, "rpmh_failure")) {
        destination_owned = true;
        sync_driver.pm = &sync_pm;
        suspend_syncboss = true;
        preflight_only = 2;
        if (!strcmp(which, "freezer_abort")) freeze_ret = -EBUSY;
        if (!strcmp(which, "gpu_failure")) gpu_suspend_ret = -EIO;
        if (!strcmp(which, "syncboss_failure")) sync_suspend_ret = -EBUSY;
        if (!strcmp(which, "resume_failure")) sync_resume_ret = -EIO;
        if (!strcmp(which, "watchdog_failure")) { preflight_only = 0; watchdog_ret = -EACCES; }
        if (!strcmp(which, "rpmh_failure")) { preflight_only = 0; flush_rpmh = true; rpmh_ret = -EIO; }
        ret = 999;
        if (!setjmp(failure)) ret = qkx_execute();
        CHECK(!did_enter);
        if (!strcmp(which, "rehearsal_order")) { CHECK(ret == 0); CHECK(!strcmp(trace, "LFGSsgTU")); }
        if (!strcmp(which, "freezer_abort")) { CHECK(ret == -EBUSY); CHECK(!strcmp(trace, "LFU")); }
        if (!strcmp(which, "gpu_failure")) { CHECK(ret == -EIO); CHECK(!strcmp(trace, "LFGgTU")); }
        if (!strcmp(which, "syncboss_failure")) { CHECK(ret == -EBUSY); CHECK(!strcmp(trace, "LFGSgTU")); }
        if (!strcmp(which, "resume_failure")) { CHECK(did_panic == 1); CHECK(!strchr(trace, 'T')); }
        if (!strcmp(which, "watchdog_failure")) { CHECK(did_panic == 1); CHECK(!strchr(trace, 'T')); }
        if (!strcmp(which, "rpmh_failure")) { CHECK(did_panic == 1); CHECK(strchr(trace, 'R')); }
        return 0;
    } else if (!strncmp(which, "syncboss", 8)) {
        suspend_syncboss = true;
        if (!strcmp(which, "syncboss_no_resume")) sync_driver.pm = &no_resume;
        if (!strcmp(which, "syncboss_wrong_driver")) audio_node.compatible = "meta,syncboss-spi";
        if (!strcmp(which, "syncboss_wrong_compatible")) audio_driver.name = "syncboss_spi";
        if (!strcmp(which, "syncboss_renumbered")) syncboss.name = "spi47.3";
        ret = qkx_execute();
        CHECK(ret == (!strcmp(which, "syncboss_no_resume") ? -ENODEV : 0));
        /* Audio must never be selected, even if its bus number looks plausible. */
        CHECK(audio_released == 0);
    } else if (!strcmp(which, "aop_no_irq") || !strcmp(which, "aop_no_shutdown")) {
        disconnect_qmp = true;
        no_irq = !strcmp(which, "aop_no_irq");
        no_qmp_shutdown = !strcmp(which, "aop_no_shutdown");
        CHECK(qkx_execute() == -ENOENT);
    } else if (!strcmp(which, "rpmh_no_symbol") || !strcmp(which, "rpmh_no_device")) {
        flush_rpmh = true;
        no_rpmh = !strcmp(which, "rpmh_no_symbol");
        missing_rpmh_device = !strcmp(which, "rpmh_no_device");
        CHECK(qkx_execute() == -ENOENT);
    } else if (!strcmp(which, "preflight")) {
        flush_rpmh = suspend_syncboss = disconnect_qmp = true;
        CHECK(qkx_execute() == 0);
    } else if (!strcmp(which, "log") || !strcmp(which, "log_no_model")) {
        model = "Hollywood (DVT)";
        if (!strcmp(which, "log_no_model")) model = NULL;
        CHECK(qkx_marker_read_init() < 0);
        clear = true;
        CHECK(qkx_marker_read_init() < 0);
        clear = false; write_test = true;
        CHECK(qkx_marker_read_init() < 0);
    } else if (!strcmp(which, "stage_only")) {
        bool *actions[] = { &execute, &test_transition, &test_full_copy, &warm_reset_test,
                            &suspend_syncboss, &disconnect_qmp, &flush_rpmh, &preserve_watchdog };
        unsigned int i;
        preflight_only = 0;
        for (i = 0; i < sizeof(actions) / sizeof(actions[0]); i++) {
            *actions[i] = true;
            CHECK(check_options() == -EOPNOTSUPP);
            *actions[i] = false;
        }
        test_cpus = 1; CHECK(check_options() == -EOPNOTSUPP); test_cpus = 0;
        core_hang_control = 1; CHECK(check_options() == -EOPNOTSUPP); core_hang_control = 0;
        secure_watchdog_control = 1; CHECK(check_options() == -EOPNOTSUPP);
    } else if (!strcmp(which, "ram_validation_options")) {
        bool *actions[] = { &execute, &test_transition, &test_full_copy, &warm_reset_test,
                            &suspend_syncboss, &disconnect_qmp, &flush_rpmh, &preserve_watchdog };
        unsigned int *modes[] = { &test_cpus, &core_hang_control, &secure_watchdog_control, &phase_delay_ms };
        unsigned int i;
        preflight_only = 0;
        validate_ram = true;
        ram_diagnose = true;
        CHECK(check_options() == 0);
        for (i = 0; i < sizeof(actions)/sizeof(actions[0]); i++) {
            *actions[i] = true; CHECK(check_options() == -EINVAL); *actions[i] = false;
        }
        for (i = 0; i < sizeof(modes)/sizeof(modes[0]); i++) {
            *modes[i] = 1; CHECK(check_options() == -EINVAL); *modes[i] = 0;
        }
    } else if (!strcmp(which, "staging")) {
        preflight_only = 0;
        CHECK(check_options() == 0);
    } else return 2;
    CHECK(destructive_calls == 0);
    return 0;
}
