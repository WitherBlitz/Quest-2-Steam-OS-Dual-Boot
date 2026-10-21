/* External kernel operations only; no actual RAM reservation or device I/O. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
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
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
typedef unsigned int gfp_t;
struct zone { int id; };
struct page { unsigned long pfn; };
static struct zone normal, other;
static struct page pages[13312];
static struct page *allocations[2], **owned = allocations;
static unsigned int nr_owned;
static void *first_desc, *last_desc;
static bool destination_owned;
static void (*release_destination)(unsigned long, unsigned int);
static int no_alloc, no_free, no_flags, result, alloc_calls, free_calls, page_frees;
static int hole, reserved, cross_zone, cma, wrong_args;
static struct page *pfn_to_page(unsigned long pfn) { return &pages[pfn-0x90000]; }
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
    wrong_args |= start != 0x90000 || end != 0x93400 || mt != 2 || gfp != 0x10;
    return result;
}
static void free_range(unsigned long start, unsigned int n)
{ free_calls++; wrong_args |= start != 0x90000 || n != 13312; }
static uintptr_t lookup(const char *s)
{
    if (!strcmp(s, "alloc_contig_range")) return no_alloc ? 0 : (uintptr_t)alloc_range;
    if (!strcmp(s, "free_contig_range")) return no_free ? 0 : (uintptr_t)free_range;
    if (!strcmp(s, "get_pfnblock_flags_mask")) return no_flags ? 0 : (uintptr_t)get_flags;
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
