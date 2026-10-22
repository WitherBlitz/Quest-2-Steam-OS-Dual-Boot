/* Host API doubles: source control flow only, never real SCM, MMIO or recovery. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef unsigned char u8;
typedef intptr_t ssize_t;
struct device { void *data; };
struct device_attribute { int unused; };
struct notifier_block { int unused; };
struct scm_desc { uint64_t args[10], ret[3]; unsigned int arginfo; };
struct msm_watchdog_data {
    struct notifier_block panic_blk;
    unsigned char *base;
    struct device *dev;
    bool irq_ppi, enabled;
    int bark_irq, wdog_cpu_dd, pet_timer, disable_lock;
};
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define PAGE_SIZE 4096
static long WDT_HZ = 32765;
#define WDT0_RST 0x04
#define WDT0_EN 0x08
#define WDT0_BARK_TIME 0x10
#define WDT0_BITE_TIME 0x14
#define SCM_SVC_BOOT 0x1
#define SCM_SVC_SEC_WDOG_DIS 0x7
#define SCM_SIP_FNID(s,c) (((((s)&0xFF)<<8)|((c)&0xFF))|0x02000000)
/* One value argument encodes only the argument count; no ABI/layout claim. */
#define SCM_ARGS(n) (n)
#define NOTIFY_DONE 0
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define dev_err(...) ((void)0)
static int enable = 1, panic_timeout = 5, panic_notifier_list;
static int lock_depth, scm_result, scm_calls, irq_frees, notifier_removes, timer_removes;
static uint64_t secure_result;
static unsigned int last_fn, last_arginfo;
static uint64_t last_argument;
static void *dev_get_drvdata(struct device *d) { return d->data; }
static void mutex_lock(int *p) { (void)p; lock_depth++; }
static void mutex_unlock(int *p) { (void)p; lock_depth--; }
static void mb(void) {}
static void smp_mb(void) {}
static void __raw_writel(uint32_t v, void *p) { *(uint32_t *)p = v; }
static void disable_percpu_irq(int irq) { (void)irq; }
static void free_percpu_irq(int irq, int data) { (void)irq; (void)data; irq_frees++; }
static void devm_free_irq(struct device *d, int irq, void *data) { (void)d; (void)irq; (void)data; irq_frees++; }
static void atomic_notifier_chain_unregister(int *p, struct notifier_block *n) { (void)p; (void)n; notifier_removes++; }
static void del_timer_sync(int *p) { (void)p; timer_removes++; }
static int kstrtou8(const char *s, unsigned int radix, u8 *v) {
    char *end;
    unsigned long x = strtoul(s, &end, radix);
    if (!*s || *end || x > 255) return -EINVAL;
    *v = (u8)x; return 0;
}
static int scm_call2(unsigned int fn, struct scm_desc *d) {
    scm_calls++; last_fn = fn; last_arginfo = d->arginfo; last_argument = d->args[0];
    d->ret[0] = secure_result; return scm_result;
}
/* SOURCE */
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)
int main(int argc, char **argv) {
    uint32_t regs[8] = {0};
    struct device dev = {0};
    struct msm_watchdog_data data = {.base=(unsigned char *)regs, .dev=&dev, .enabled=true};
    char buf[PAGE_SIZE];
    CHECK(argc == 2); dev.data = &data; regs[WDT0_EN/4] = 1;
    if (!strcmp(argv[1], "getter")) {
        CHECK(wdog_disable_get(&dev, NULL, buf) == 2 && !strcmp(buf, "0\n"));
        enable = 0;
        CHECK(wdog_disable_get(&dev, NULL, buf) == 2 && !strcmp(buf, "1\n"));
    } else if (!strcmp(argv[1], "scm_failure")) {
        scm_result = -1;
        CHECK(wdog_disable_set(&dev, NULL, "1", 1) == -EIO);
        CHECK(scm_calls == 1 && last_fn == 0x02000107 && last_arginfo == 1 && last_argument == 1);
        CHECK(enable && data.enabled && regs[WDT0_EN/4] == 1);
        CHECK(!irq_frees && !notifier_removes && !timer_removes);
    } else if (!strcmp(argv[1], "disable")) {
        CHECK(wdog_disable_set(&dev, NULL, "1", 1) == 1);
        CHECK(scm_calls == 1 && last_argument == 1 && last_arginfo == 1);
        CHECK(!enable && !data.enabled && !regs[WDT0_EN/4]);
        CHECK(irq_frees == 1 && notifier_removes == 1 && timer_removes == 1);
        CHECK(wdog_disable_set(&dev, NULL, "0", 1) == -EINVAL);
        CHECK(!enable && !regs[WDT0_EN/4] && scm_calls == 1);
    } else if (!strcmp(argv[1], "already_disabled")) {
        enable = 0; regs[WDT0_EN/4] = 0;
        CHECK(wdog_disable_set(&dev, NULL, "1", 1) == 1);
        CHECK(!scm_calls && !irq_frees && !notifier_removes && !timer_removes);
    } else if (!strcmp(argv[1], "secure_result")) {
        secure_result = 1;
        CHECK(wdog_disable_set(&dev, NULL, "1", 1) == 1);
        CHECK(!enable && !regs[WDT0_EN/4]);
        /* Driver ignores desc.ret[0]; this does not establish its firmware semantics. */
    } else if (!strcmp(argv[1], "panic_zero")) {
        panic_timeout = 0;
        CHECK(panic_wdog_handler(&data.panic_blk, 0, NULL) == NOTIFY_DONE);
        CHECK(!regs[WDT0_EN/4] && !regs[WDT0_RST/4]);
    } else if (!strcmp(argv[1], "panic_disabled")) {
        regs[WDT0_EN/4] = 0;
        CHECK(panic_wdog_handler(&data.panic_blk, 0, NULL) == NOTIFY_DONE);
        CHECK(!regs[WDT0_EN/4] && regs[WDT0_RST/4] == 1);
        CHECK(regs[WDT0_BARK_TIME/4] == WDT_HZ*15 && regs[WDT0_BITE_TIME/4] == WDT_HZ*15);
    } else if (!strcmp(argv[1], "panic_enabled")) {
        CHECK(panic_wdog_handler(&data.panic_blk, 0, NULL) == NOTIFY_DONE);
        CHECK(regs[WDT0_EN/4] == 1 && regs[WDT0_RST/4] == 1);
    } else return 2;
    CHECK(!lock_depth);
    return 0;
}
