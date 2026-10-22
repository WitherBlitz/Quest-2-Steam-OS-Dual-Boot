/* MMIO state model only. No real controller, kernel or device access. */
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define __init
#define __iomem
#define GICD_CTLR 0x0000
#define GICD_IGROUPR 0x0080
#define GIC_DIST_ENABLE_CLEAR 0x0180
#define GICD_ICPENDR 0x0280
#define GIC_DIST_ACTIVE_CLEAR 0x0380
#define GIC_DIST_PRI 0x0400
#define GIC_DIST_CONFIG 0x0c00
#define GICD_IROUTER 0x6000
#define GICD_CTLR_ARE_NS (1U << 4)
#define GICD_CTLR_ENABLE_G1A (1U << 1)
#define GICD_CTLR_ENABLE_G1 (1U << 0)
#define GICD_INT_ACTLOW_LVLTRIG 0
#define GICD_INT_EN_CLR_X32 UINT32_MAX
#define GICD_INT_DEF_PRI_X4 0xa0a0a0a0U
static unsigned char registers[0x10000];
static struct {unsigned int irq_nr; void *dist_base;} gic_data;
static u32 pending[32], active[32], enabled[32], asserted[32];
static unsigned int cleared[32], waits, routes, distributor_enabled = 1;
static u32 mask(unsigned int word)
{
    unsigned int first = word * 32, bits;
    if (first >= gic_data.irq_nr) return 0;
    bits = gic_data.irq_nr - first;
    return bits >= 32 ? UINT32_MAX : UINT32_MAX >> (32 - bits);
}
static void writel_relaxed(u32 value, void *address)
{
    size_t offset = (unsigned char *)address - registers;
    unsigned int word;
    assert(offset < sizeof(registers) && offset % 4 == 0);
    if (offset == GICD_CTLR) {
        if (value) {
            for (word = 1; word * 32 < gic_data.irq_nr; word++)
                assert(cleared[word] == 1);
        }
        distributor_enabled = !!value;
    } else if (offset >= GICD_ICPENDR && offset < GICD_ICPENDR + 128) {
        word = (unsigned int)(offset - GICD_ICPENDR) / 4;
        assert(word > 0 && word * 32 < gic_data.irq_nr);
        assert(!distributor_enabled && waits == 2);
        assert(!active[word] && !enabled[word]);
        pending[word] = (pending[word] & ~(value & mask(word))) | asserted[word];
        cleared[word]++;
    } else if (offset >= GIC_DIST_ACTIVE_CLEAR && offset < GIC_DIST_ACTIVE_CLEAR + 128) {
        word = (unsigned int)(offset - GIC_DIST_ACTIVE_CLEAR) / 4;
        active[word] &= ~(value & mask(word));
    } else if (offset >= GIC_DIST_ENABLE_CLEAR && offset < GIC_DIST_ENABLE_CLEAR + 128) {
        word = (unsigned int)(offset - GIC_DIST_ENABLE_CLEAR) / 4;
        enabled[word] &= ~(value & mask(word));
    }
}
static void gic_dist_wait_for_rwp(void) { waits++; }
static int smp_processor_id(void) { return 0; }
static u64 cpu_logical_map(int cpu) { return (u64)cpu; }
static u64 gic_mpidr_to_affinity(u64 cpu) { return cpu; }
static void gic_write_irouter(u64 affinity, void *address)
{
    size_t offset = (unsigned char *)address - registers;
    assert(distributor_enabled && affinity == 0);
    assert(offset >= GICD_IROUTER + 32 * 8 && offset < GICD_IROUTER + gic_data.irq_nr * 8);
    routes++;
}
/* COMMON */
/* DRIVER */
int main(int argc, char **argv)
{
    unsigned int word;
    assert(argc == 3);
    gic_data.irq_nr = (unsigned int)strtoul(argv[1], NULL, 10);
    assert(gic_data.irq_nr >= 32 && gic_data.irq_nr <= 1020);
    gic_data.dist_base = registers;
    for (word = 0; word < 32; word++)
        pending[word] = active[word] = enabled[word] = mask(word);
    if (!strcmp(argv[2], "level") && gic_data.irq_nr > 32)
        asserted[1] = 1;
    gic_dist_init();
    assert(distributor_enabled && waits == 2 && routes == gic_data.irq_nr - 32);
    assert(pending[0] == UINT32_MAX && active[0] == UINT32_MAX && enabled[0] == UINT32_MAX);
    for (word = 1; word * 32 < gic_data.irq_nr; word++) {
        assert(pending[word] == asserted[word]);
        assert(!active[word] && !enabled[word] && cleared[word] == 1);
    }
    return 0;
}
