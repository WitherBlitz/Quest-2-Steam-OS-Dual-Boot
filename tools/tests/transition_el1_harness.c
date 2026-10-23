/* SPDX-License-Identifier: GPL-2.0-only */
/* Architectural execution in QEMU-owned RAM, never Quest/cache/firmware proof. */
typedef unsigned long u64;
_Static_assert(sizeof(u64) == 8, "AArch64 required");
#define HIGH_ALIAS 0xffffff8000000000UL
#define PAGE 4096
#define PAGES 256
#define COOKIE 0xfeedf00dUL
#define SCTLR_FLAGS 0x20100fUL

extern char qkx_reloc_start[], qkx_reloc_end[];
extern char qkx_probe_phys_start[], qkx_probe_phys_end[];
extern char qkx_reloc_return_start[], qkx_reloc_return_end[];
extern char simulated_kernel_entry[];
extern u64 qkx_roundtrip(u64, u64, u64, u64, u64);
extern void qkx_enter(u64, u64, u64, u64, u64) __attribute__((noreturn));

struct descriptor {
    u64 next, count;
    struct { u64 source, destination; } entries[255];
};
_Static_assert(sizeof(struct descriptor) == PAGE, "production descriptor geometry");
static u64 identity[512] __attribute__((aligned(PAGE)));
static unsigned char control[PAGE] __attribute__((aligned(PAGE)));
static struct descriptor descriptors[2] __attribute__((aligned(PAGE)));
static unsigned char source[PAGES][PAGE] __attribute__((aligned(PAGE)));
static unsigned char destination[PAGES + 2][PAGE] __attribute__((aligned(PAGE)));

static u64 physical(const void *pointer)
{
    return (u64)pointer - HIGH_ALIAS;
}

static void message(const char *text)
{
    register u64 operation __asm__("x0") = 4;
    register const char *argument __asm__("x1") = text;
    __asm__ volatile("hlt #0xf000" : "+r"(operation) : "r"(argument) : "memory");
}

static void stop(u64 status) __attribute__((noreturn));
static void stop(u64 status)
{
    u64 block[2] = {0x20026, status};
    register u64 operation __asm__("x0") = 0x18;
    register const u64 *argument __asm__("x1") = block;
    __asm__ volatile("hlt #0xf000" : "+r"(operation) : "r"(argument) : "memory");
    for (;;) __asm__ volatile("wfe");
}

static void require(int condition, const char *failure)
{
    if (!condition) {
        message(failure);
        stop(1);
    }
}

#define READ_REGISTER(name) ({ u64 value; __asm__ volatile("mrs %0, " #name : "=r"(value)); value; })

static void copy_code(const char *start, const char *end, unsigned offset)
{
    u64 length = end - start;
    require(length && offset + length <= PAGE, "FAIL control_range\n");
    for (u64 i = 0; i < length; i++) control[offset + i] = start[i];
}

static void synchronize(void)
{
    /* Use CTR_EL0, as the production cache macro does; QEMU has no real caches. */
    u64 ctr = READ_REGISTER(ctr_el0);
    u64 dline = 4UL << ((ctr >> 16) & 15), iline = 4UL << (ctr & 15);
    for (u64 address = (u64)control; address < (u64)control + PAGE; address += dline)
        __asm__ volatile("dc cvau, %0" :: "r"(address) : "memory");
    __asm__ volatile("dsb ish" ::: "memory");
    for (u64 address = (u64)control; address < (u64)control + PAGE; address += iline)
        __asm__ volatile("ic ivau, %0" :: "r"(address) : "memory");
    __asm__ volatile("dsb ish; isb" ::: "memory");
}

static u64 roundtrip(u64 head, u64 entry)
{
    u64 daif = READ_REGISTER(daif), tcr = READ_REGISTER(tcr_el1);
    u64 ttbr = READ_REGISTER(ttbr0_el1), sctlr = READ_REGISTER(sctlr_el1);
    u64 result = qkx_roundtrip(physical(identity), physical(control), head, entry, COOKIE);
    require(READ_REGISTER(daif) == daif && READ_REGISTER(tcr_el1) == tcr &&
            READ_REGISTER(ttbr0_el1) == ttbr && READ_REGISTER(sctlr_el1) == sctlr,
            "FAIL restored_state\n");
    u64 pc;
    __asm__ volatile("adr %0, ." : "=r"(pc));
    require(pc >= HIGH_ALIAS, "FAIL high_return\n");
    return result;
}

static unsigned char pattern(unsigned page, unsigned offset)
{
    return (page * 17 + offset * 13) ^ (offset >> 8);
}

static void reset_data(void)
{
    for (unsigned page = 0; page < PAGES; page++)
        for (unsigned i = 0; i < PAGE; i++) source[page][i] = pattern(page, i);
    for (unsigned page = 0; page < PAGES + 2; page++)
        for (unsigned i = 0; i < PAGE; i++) destination[page][i] = 0xa5;
    for (unsigned i = 0; i < sizeof(descriptors); i++) ((unsigned char *)descriptors)[i] = 0;
}

static void check_data(unsigned copied)
{
    for (unsigned page = 0; page < PAGES + 2; page++)
        for (unsigned i = 0; i < PAGE; i++)
            require(destination[page][i] == (page && page <= copied ? pattern(page - 1, i) : 0xa5),
                    "FAIL copy\n");
    for (unsigned page = 0; page < PAGES; page++)
        for (unsigned i = 0; i < PAGE; i++)
            require(source[page][i] == pattern(page, i), "FAIL source\n");
}

static void set_entry(unsigned descriptor, unsigned index, unsigned page)
{
    descriptors[descriptor].entries[index].source = physical(source[page]);
    descriptors[descriptor].entries[index].destination = physical(destination[page + 1]);
}

/* Called at physical addresses with translation disabled, by the entry shim. */
void entry_finish(u64 x0, u64 x1, u64 x2, u64 x3)
{
    require(x0 == COOKIE && !x1 && !x2 && !x3, "FAIL entry_registers\n");
    require(!(READ_REGISTER(sctlr_el1) & SCTLR_FLAGS) &&
            (READ_REGISTER(daif) & 0x3c0) == 0x3c0 && READ_REGISTER(CurrentEL) == 4,
            "FAIL entry_state\n");
    check_data(PAGES);
    message("PASS entry\nPASS all\n");
    stop(0);
}

void harness_main(void)
{
    require(READ_REGISTER(CurrentEL) == 4 && (READ_REGISTER(sctlr_el1) & 1), "FAIL bootstrap\n");
    message("QKX_EL1_READY\n");
#ifdef QKX_HANG
    message("QKX_EL1_HANG_READY\n");
    for (;;) __asm__ volatile("b .");
#endif
    /* Deliberately start with a non-default interrupt mask, and a distinct PGD. */
    __asm__ volatile("msr daif, %0" :: "r"(0x140UL) : "memory");
    identity[1] = 0x40000701;
    copy_code(qkx_probe_phys_start, qkx_probe_phys_end, 0);
    synchronize();
    require(roundtrip(0, 0) == 0, "FAIL physical_probe\n");
    message("PASS probe\n");
    copy_code(qkx_reloc_start, qkx_reloc_end, 0);
    unsigned offset = (qkx_reloc_end - qkx_reloc_start + 15) & ~15UL;
    copy_code(qkx_reloc_return_start, qkx_reloc_return_end, offset);
    synchronize();
    u64 return_entry = physical(control) + offset;
    reset_data();
    require(roundtrip(0, return_entry) == COOKIE, "FAIL null_cookie\n");
    check_data(0);
    message("PASS null\n");
    descriptors[0].next = physical(&descriptors[1]);
    require(roundtrip(physical(descriptors), return_entry) == COOKIE, "FAIL empty_cookie\n");
    check_data(0);
    message("PASS empty_chain\n");
    reset_data();
    descriptors[0].count = 1;
    set_entry(0, 0, 0);
    require(roundtrip(physical(descriptors), return_entry) == COOKIE, "FAIL single_cookie\n");
    check_data(1);
    message("PASS single\n");
    reset_data();
    descriptors[0].next = physical(&descriptors[1]);
    descriptors[0].count = 255;
    for (unsigned i = 0; i < 255; i++) set_entry(0, i, i);
    descriptors[1].count = 1;
    set_entry(1, 0, 255);
    require(roundtrip(physical(descriptors), return_entry) == COOKIE, "FAIL chain_cookie\n");
    check_data(PAGES);
    message("PASS descriptor_chain\n");
    /* Exercise production qkx_enter last. The target is an assertion shim,
     * not Linux; this guest intentionally exits instead of claiming recovery.
     */
    for (unsigned page = 1; page <= PAGES; page++)
        for (unsigned i = 0; i < PAGE; i++) destination[page][i] = 0xa5;
    qkx_enter(physical(identity), physical(control), physical(descriptors),
              physical(simulated_kernel_entry), COOKIE);
}
