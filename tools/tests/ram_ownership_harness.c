/* External kernel operations only; no actual RAM reservation or device I/O. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stddef.h>
#include <stdarg.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x) = (v))
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define QKX_LO 0x90000000ULL
#define QKX_HI 0x93400000ULL
#define PAGE_SHIFT 12
#define PAGE_SIZE 4096
#define MAX_ORDER_NR_PAGES 1024UL
#define pageblock_nr_pages 1024UL
#define MIGRATE_MOVABLE 2
#define PB_migrate_end 2
#define MIGRATETYPE_MASK 7
#define GFP_KERNEL 0x10
#define IS_ALIGNED(x,a) (!((x) & ((a)-1)))
#define max_t(t,a,b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
static char logs[8192];
static void test_log(const char *fmt, ...)
{ va_list ap; size_t n = strlen(logs); va_start(ap, fmt); vsnprintf(logs+n, sizeof(logs)-n, fmt, ap); va_end(ap); }
#define pr_err(...) test_log(__VA_ARGS__)
#define pr_info(...) test_log(__VA_ARGS__)
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
typedef uint64_t u64;
typedef unsigned int gfp_t;
static bool validate_ram, ram_diagnose, execute, preserve_watchdog, watchdog_recovery, watchdog_control;
static unsigned long long ram_base, ram_bytes, payload_base;
static u64 destination_lo = QKX_LO, destination_hi = QKX_HI;
static bool test_transition, test_full_copy, post_shutdown_probe, warm_reset_test;
static bool flush_rpmh, suspend_syncboss, disconnect_qmp;
static unsigned int test_cpus, phase_delay_ms, preflight_only;
static unsigned int core_hang_control, secure_watchdog_control;
struct resource { u64 start, end; };
struct device_node { int unused; };
static struct device_node root_node, *of_root = &root_node;
static const char *model = "Hollywood (DVT)";
static bool ram_found;
static int ram_missing, resolve_ret, resolve_calls;
static int of_property_read_string(struct device_node *n, const char *s, const char **v)
{ if (!model) return -EINVAL; *v = model; return 0; }
static int of_machine_is_compatible(const char *s) { return 1; }
static u64 ktime_get_ns(void) { return 1000; }
static int qkx_resolve(void) { resolve_calls++; return resolve_ret; }
static int walk_ram(u64 start, u64 end, void *arg, int (*cb)(struct resource *, void *))
{ struct resource r = { 0x8bf00000, 0x1ffffffff }; return ram_missing ? 0 : cb(&r, arg); }
struct zone { int id; };
struct page { unsigned long pfn; };
static struct zone normal, other;
static struct page pages[13312];
static struct page *allocations[2], **owned = allocations;
static unsigned int nr_owned;
static void *first_desc, *last_desc;
static bool destination_owned;
static void (*release_destination)(unsigned long, unsigned int);
static int no_alloc, no_free, no_flags, no_probe, result, alloc_calls, free_calls, page_frees;
static unsigned long allocated_start, released_start, allocated_end;
static unsigned int expected_pages = 13312;
static int hole, reserved, cross_zone, cma, wrong_args;
struct task_struct { int id; };
static struct task_struct caller, stranger, *current = &caller;
struct pt_regs { uintptr_t regs[31]; };
struct kretprobe_instance;
struct kretprobe {
    struct { const char *symbol_name; unsigned long nmissed; } kp;
    int (*handler)(struct kretprobe_instance *, struct pt_regs *);
    int (*entry_handler)(struct kretprobe_instance *, struct pt_regs *);
    int maxactive, nmissed;
    size_t data_size;
};
struct kretprobe_instance { struct kretprobe *rp; unsigned long data[1]; };
static unsigned long regs_return_value(struct pt_regs *regs) { return regs->regs[0]; }
static unsigned long page_to_pfn(struct page *page) { return page->pfn; }
static int register_calls, unregister_calls, fail_probe = -1;
static struct kretprobe *registered[4];
static int active_probes, wrong_unregister;
static int register_probe(struct kretprobe *probe)
{
    int i = register_calls++;
    wrong_args |= probe->maxactive != 16 || probe->data_size != sizeof(unsigned long) || !probe->handler || !probe->entry_handler;
    if (i == fail_probe) return -EINVAL;
    registered[active_probes++] = probe;
    return 0;
}
static void unregister_probe(struct kretprobe *probe)
{ unregister_calls++; wrong_unregister |= !active_probes || registered[--active_probes] != probe; }
static struct page *pfn_to_page(unsigned long pfn)
{ struct page *p = &pages[pfn-(destination_lo >> PAGE_SHIFT)]; p->pfn = pfn; return p; }
static int pfn_valid(unsigned long pfn) { return !hole || pfn != 0x933ff; }
static int PageReserved(struct page *p) { return reserved && p->pfn == 0x933ff; }
static struct zone *page_zone(struct page *p)
{ return cross_zone && p->pfn == 0x933ff ? &other : &normal; }
static int get_pageblock_migratetype(struct page *p)
{ return cma && p->pfn >= 0x93000 ? 4 : MIGRATE_MOVABLE; }
static unsigned long get_flags(struct page *p, unsigned long pfn,
                               unsigned long end, unsigned long mask)
{ wrong_args |= p->pfn != pfn || end != 2 || mask != 7; return get_pageblock_migratetype(p); }
static int alloc_range(unsigned long start, unsigned long end, unsigned int mt, gfp_t gfp)
{
    alloc_calls++;
    allocated_start = start;
    allocated_end = end;
    wrong_args |= start != (destination_lo >> PAGE_SHIFT) || end != (destination_hi >> PAGE_SHIFT) || mt != 2 || gfp != 0x10;
    return result;
}
static void free_range(unsigned long start, unsigned int n)
{ free_calls++; released_start = start; wrong_args |= start != (destination_lo >> PAGE_SHIFT) || n != expected_pages; }
static uintptr_t lookup(const char *s)
{
    if (!strcmp(s, "alloc_contig_range")) return no_alloc ? 0 : (uintptr_t)alloc_range;
    if (!strcmp(s, "free_contig_range")) return no_free ? 0 : (uintptr_t)free_range;
    if (!strcmp(s, "get_pfnblock_flags_mask")) return no_flags ? 0 : (uintptr_t)get_flags;
    if (!strcmp(s, "walk_system_ram_res")) return (uintptr_t)walk_ram;
    if (!strcmp(s, "register_kretprobe")) return no_probe == 1 ? 0 : (uintptr_t)register_probe;
    if (!strcmp(s, "unregister_kretprobe")) return no_probe == 2 ? 0 : (uintptr_t)unregister_probe;
    return 0;
}
static void __free_page(struct page *p) { page_frees++; }
static void vfree(void *p) { }
/* PRODUCTION */
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char **argv)
{
    const char *c = argc > 1 ? argv[1] : "";
    unsigned int i;
    int ret;
    for (i = 0; i < 13312; i++) pages[i].pfn = 0x90000+i;
    if (!strcmp(c, "length_without_validation")) {
        ram_bytes = 0x2400000;
        CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
        return 0;
    }
    if (!strcmp(c, "validate_payload_conflict")) {
        validate_ram = true; payload_base = 0x100000000ULL;
        CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
        return 0;
    }
    if (!strcmp(c, "validate_compact")) {
        validate_ram = true; ram_base = 0x100000000ULL; ram_bytes = 0x2400000;
        expected_pages = 9216;
        CHECK(init_validation() == 0 && alloc_calls == 1 && free_calls == 1 && !wrong_args);
        CHECK(allocated_start == 0x100000 && allocated_end == 0x102400 && released_start == allocated_start);
        CHECK(!destination_owned);
        return 0;
    }
    if (!strcmp(c, "validate_bad_length")) {
        const unsigned long long bad[] = {1, 0x2400001, 0x4400000, ~0ULL};
        validate_ram = true; ram_base = 0x100000000ULL;
        for (i = 0; i < sizeof(bad)/sizeof(bad[0]); i++) {
            ram_bytes = bad[i];
            CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
        }
        ram_bytes = 0x2400000; ram_base = ~0ULL - 0x3fffff;
        CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
        return 0;
    }
    if (!strcmp(c, "base_without_validation")) {
        ram_base = 0x100000000ULL;
        CHECK(init_validation() == -EINVAL);
        CHECK(!alloc_calls && !resolve_calls);
        return 0;
    }
    if (!strcmp(c, "diagnose_without_validation")) {
        ram_diagnose = true;
        CHECK(init_validation() == -EINVAL);
        CHECK(!alloc_calls && !resolve_calls);
        return 0;
    }
    if (!strncmp(c, "diagnose_", 9)) {
        ram_base = 0x100000000ULL;
        if (!strncmp(c, "diagnose_register_", 18)) {
            fail_probe = strrchr(c, '_')[1]-'0';
            ram_diagnose = validate_ram = true;
            CHECK(init_validation() == -EINVAL);
            CHECK(register_calls == fail_probe+1 && unregister_calls == fail_probe);
            CHECK(!alloc_calls && !active_probes && !nr_ram_probes && !ram_diagnostic_task && !wrong_unregister);
            return 0;
        }
        if (!strcmp(c, "diagnose_no_unregister")) {
            no_probe = 2; ram_diagnose = validate_ram = true;
            CHECK(init_validation() == -ENOENT);
            CHECK(!alloc_calls && !register_calls);
            return 0;
        }
        if (!strcmp(c, "diagnose_success") || !strcmp(c, "diagnose_busy") || !strcmp(c, "diagnose_guard")) {
            ram_diagnose = validate_ram = true;
            if (!strcmp(c, "diagnose_busy")) result = -EBUSY;
            if (!strcmp(c, "diagnose_guard")) { reserved = 1; ram_base = 0; }
            ret = init_validation();
            CHECK(ret == (!strcmp(c, "diagnose_success") ? 0 : -EBUSY));
            CHECK(alloc_calls == (strcmp(c, "diagnose_guard") != 0));
            CHECK(free_calls == (!strcmp(c, "diagnose_success") ? 1 : 0));
            CHECK(register_calls == 4 && unregister_calls == 4 && !active_probes && !nr_ram_probes);
            CHECK(!destination_owned && !ram_diagnostic_task && !wrong_unregister && !wrong_args);
            return 0;
        }
        destination_lo = ram_base; destination_hi = ram_base + QKX_HI-QKX_LO;
        CHECK(qkx_ram_diag_start() == 0);
        for (i = 0; i < QKX_RAM_STAGES; i++) {
            struct kretprobe_instance instance = { .rp = &qkx_ram_probes[i].probe };
            struct pt_regs regs = {0}, saved;
            regs.regs[0] = destination_lo >> PAGE_SHIFT;
            regs.regs[1] = destination_hi >> PAGE_SHIFT;
            if (i == QKX_RAM_UNMOVABLE) regs.regs[1] = (uintptr_t)pfn_to_page(destination_lo >> PAGE_SHIFT);
            if (i == QKX_RAM_FREE) { regs.regs[1] = destination_lo >> PAGE_SHIFT; regs.regs[2] = destination_hi >> PAGE_SHIFT; }
            if (!strcmp(c, "diagnose_filter")) {
                current = &stranger; CHECK(qkx_ram_probe_entry(&instance, &regs) == 1);
                current = &caller;
                if (i != QKX_RAM_UNMOVABLE) {
                    regs.regs[1]++;
                    CHECK(qkx_ram_probe_entry(&instance, &regs) == 1);
                } else {
                    struct page outside = { .pfn = (destination_hi >> PAGE_SHIFT) };
                    regs.regs[1] = (uintptr_t)&outside;
                    CHECK(qkx_ram_probe_entry(&instance, &regs) == 1);
                }
                CHECK(!qkx_ram_probes[i].calls);
            } else {
                saved = regs;
                CHECK(qkx_ram_probe_entry(&instance, &regs) == 0 && !memcmp(&saved, &regs, sizeof(regs)));
                regs.regs[0] = i == QKX_RAM_FREE ? 0 : i == QKX_RAM_UNMOVABLE ? 1 : (uint32_t)-EBUSY;
                saved = regs;
                CHECK(qkx_ram_probe_return(&instance, &regs) == 0 && !memcmp(&saved, &regs, sizeof(regs)));
                CHECK(qkx_ram_probes[i].calls == 1);
                CHECK(qkx_ram_probes[i].result == (unsigned long)regs.regs[0]);
                if (i == QKX_RAM_UNMOVABLE) {
                    CHECK(qkx_ram_probes[i].failed && qkx_ram_probes[i].failed_block == destination_lo >> PAGE_SHIFT);
                    *(unsigned long *)instance.data = (destination_lo >> PAGE_SHIFT) + pageblock_nr_pages;
                    CHECK(qkx_ram_probe_return(&instance, &regs) == 0);
                    CHECK(qkx_ram_probes[i].calls == 2 && qkx_ram_probes[i].failed_block == destination_lo >> PAGE_SHIFT);
                }
                qkx_ram_probes[i].probe.nmissed = 7;
                qkx_ram_probes[i].probe.kp.nmissed = 9;
            }
        }
        qkx_ram_diag_stop();
        CHECK(!active_probes && unregister_calls == 4 && !wrong_unregister && !ram_diagnostic_task);
        if (strcmp(c, "diagnose_filter")) CHECK(strstr(logs, "nmissed=7") && strstr(logs, "kprobe_nmissed=9") && strstr(logs, "result=-16") && strstr(logs, "exact busy page/owner unknown"));
        return 0;
    }
    if (!strncmp(c, "validate_", 9)) {
        validate_ram = true;
        if (!strcmp(c, "validate_high")) ram_base = 0x100000000ULL;
        if (!strcmp(c, "validate_bad_base")) {
            ram_base = 0x100000001ULL;
            CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
            ram_base = ~0ULL - 0x1000;
            CHECK(init_validation() == -EINVAL && !resolve_calls && !alloc_calls);
            return 0;
        }
        if (!strcmp(c, "validate_model")) model = "Seacliff";
        if (!strcmp(c, "validate_no_model")) model = NULL;
        if (!strcmp(c, "validate_no_ram")) ram_missing = 1;
        if (!strcmp(c, "validate_busy")) result = -EBUSY;
        if (!strcmp(c, "validate_resolve")) resolve_ret = -ENOENT;
        if (!strcmp(c, "validate_diagnose_missing")) { ram_diagnose = true; no_probe = 1; }
        ret = init_validation();
        if (!strcmp(c, "validate_success") || !strcmp(c, "validate_high")) {
            CHECK(ret == 0); CHECK(alloc_calls == 1 && free_calls == 1);
            CHECK(allocated_start == (!strcmp(c, "validate_high") ? 0x100000UL : 0x90000UL));
            CHECK(released_start == allocated_start);
        }
        else if (!strcmp(c, "validate_busy")) { CHECK(ret == -EBUSY); CHECK(alloc_calls == 1 && !free_calls); }
        else { CHECK(ret < 0); CHECK(!alloc_calls && !free_calls); }
        if (!strcmp(c, "validate_model") || !strcmp(c, "validate_no_model")) CHECK(ret == -ENODEV);
        if (!strcmp(c, "validate_no_ram")) CHECK(ret == -ERANGE);
        if (!strcmp(c, "validate_resolve")) CHECK(ret == -ENOENT);
        if (!strcmp(c, "validate_diagnose_missing")) CHECK(ret == -ENOENT);
        if (!strcmp(c, "validate_model") || !strcmp(c, "validate_no_model")) CHECK(resolve_calls == 0);
        CHECK(!destination_owned && !page_frees && !wrong_args);
        if (!ram_diagnose) CHECK(!register_calls && !unregister_calls);
        return 0;
    }
    if (!strcmp(c, "busy") || !strcmp(c, "staging_cleanup")) result = -EBUSY;
    if (!strcmp(c, "hole")) hole = 1;
    if (!strcmp(c, "reserved")) reserved = 1;
    if (!strcmp(c, "zone")) cross_zone = 1;
    if (!strcmp(c, "cma")) cma = 1;
    if (!strcmp(c, "missing")) {
        no_alloc = 1; CHECK(qkx_claim_destination() == -ENOENT);
        no_alloc = 0; no_free = 1; CHECK(qkx_claim_destination() == -ENOENT);
        no_free = 0; no_flags = 1;
    }
    if (!strcmp(c, "staging_cleanup")) nr_owned = 2;
    ret = qkx_claim_destination();
    if (!strcmp(c, "success")) { CHECK(ret == 0); CHECK(destination_owned); CHECK(alloc_calls == 1); }
    else CHECK(ret < 0 && !destination_owned);
    if (hole || reserved || cross_zone || cma || no_flags) CHECK(alloc_calls == 0);
    CHECK(free_calls == 0);
    qkx_release();
    CHECK(free_calls == (!strcmp(c, "success") ? 1 : 0));
    CHECK(!destination_owned);
    CHECK(page_frees == (!strcmp(c, "staging_cleanup") ? 2 : 0));
    qkx_release();
    CHECK(free_calls <= 1 && !wrong_args);
    return 0;
}
