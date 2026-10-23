/* Host-only doubles. Source semantics: pinned 6bde5fc kernel/cpu.c
 * freeze_secondary_cpus always increments hotplug disable, even on failure;
 * enable_nonboot_cpus balances it but returns no per-CPU restoration result.
 * These masks cannot establish PSCI completion, cache drain or physical death.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define NR_CPUS 4
static int nr_cpu_ids = NR_CPUS;
struct cpumask { unsigned int bits; };
typedef struct cpumask cpumask_t;
struct task_struct { cpumask_t cpus_allowed; };
static struct task_struct task = { { 15 } }, *current = &task;
static cpumask_t online_mask = { 15 }, boot_mask = { 1 };
#define cpu_online_mask (&online_mask)
#define cpumask_of(cpu) (&boot_mask)
#define for_each_cpu(cpu, mask) for ((cpu) = 0; (cpu) < NR_CPUS; (cpu)++) if ((mask)->bits & (1U << (cpu)))
static void cpumask_copy(cpumask_t *a, const cpumask_t *b) { *a = *b; }
static void cpumask_clear(cpumask_t *a) { a->bits = 0; }
static void cpumask_set_cpu(int cpu, cpumask_t *a) { a->bits |= 1U << cpu; }
static bool cpumask_test_cpu(int cpu, const cpumask_t *a) { return !!(a->bits & (1U << cpu)); }
static bool cpumask_equal(const cpumask_t *a, const cpumask_t *b) { return a->bits == b->bits; }
static bool cpu_online(int cpu) { return cpumask_test_cpu(cpu, &online_mask); }
static unsigned int num_online_cpus(void)
{ unsigned int i, n = 0; for (i = 0; i < NR_CPUS; i++) n += cpu_online(i); return n; }
static unsigned int test_cpus = 2, preflight_only;
static int down_fail = -1, down_error = -EBUSY, up_error[NR_CPUS];
static int pin_error, affinity_error, affinity_calls, probe_error, probe_calls;
static int freeze_error, freeze_calls, restore_calls, hotplug_disabled;
static bool drop_restore, leave_online;
static unsigned int frozen_bits;
static char trace[64];
static unsigned int trace_len;
static void event(char c) { trace[trace_len++] = c; trace[trace_len] = 0; }
static int down(unsigned int cpu)
{ event((char)('0' + cpu)); if ((int)cpu == down_fail) return down_error;
  if (!leave_online) online_mask.bits &= ~(1U << cpu); return 0; }
static int up(unsigned int cpu)
{ event((char)('a' + cpu)); if (up_error[cpu]) return up_error[cpu];
  if (!drop_restore) online_mask.bits |= 1U << cpu; return 0; }
static int affinity(struct task_struct *t, const struct cpumask *m)
{ int ret; affinity_calls++; event(affinity_calls == 1 ? 'P' : 'A');
  ret = affinity_calls == 1 ? pin_error : affinity_error; if (!ret) t->cpus_allowed = *m; return ret; }
static int freeze(int cpu)
{ freeze_calls++; hotplug_disabled++; event('F'); frozen_bits = online_mask.bits & ~1U;
  online_mask.bits = freeze_error ? 5 : 1; return freeze_error; }
static void restore(void)
{ restore_calls++; hotplug_disabled--; event('R'); if (!drop_restore) online_mask.bits |= frozen_bits; }
static int qkx_test_transition(void) { probe_calls++; event('Q'); return probe_error; }
static uintptr_t lookup(const char *s)
{
    if (!strcmp(s, "freeze_secondary_cpus")) return (uintptr_t)freeze;
    if (!strcmp(s, "enable_nonboot_cpus")) return (uintptr_t)restore;
    if (!strcmp(s, "cpu_down")) return (uintptr_t)down;
    if (!strcmp(s, "cpu_up")) return (uintptr_t)up;
    if (!strcmp(s, "set_cpus_allowed_ptr")) return (uintptr_t)affinity;
    return 0;
}
/* PRODUCTION */
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s (ret=%d trace=%s)\n", __LINE__, #x, ret, trace); return 1; } } while (0)
int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    int ret, expected = 0;
    if (!strcmp(s, "partial")) { down_fail = 3; expected = -EBUSY; }
    else if (!strcmp(s, "down_error")) { down_fail = 2; up_error[1] = -EACCES; affinity_error = -EINVAL; expected = -EBUSY; }
    else if (!strcmp(s, "probe_error")) { preflight_only = 3; probe_error = -EINVAL; up_error[3] = -EACCES; expected = -EINVAL; }
    else if (!strcmp(s, "restore_error")) { up_error[1] = -EACCES; up_error[2] = -EBUSY; expected = -EACCES; }
    else if (!strcmp(s, "mask_mismatch")) { drop_restore = true; expected = -EIO; }
    else if (!strcmp(s, "freeze_error")) { test_cpus = 1; freeze_error = -EBUSY; drop_restore = true; expected = -EBUSY; }
    else if (!strcmp(s, "freeze_restore")) { test_cpus = 1; drop_restore = true; expected = -EIO; }
    else if (!strcmp(s, "probe_busy")) { preflight_only = 3; leave_online = true; expected = -EBUSY; }
    else if (!strcmp(s, "pin_error")) { pin_error = -EACCES; expected = -EACCES; }
    else if (!strcmp(s, "affinity_error")) { affinity_error = -EACCES; expected = -EACCES; }
    else if (!strcmp(s, "single")) { online_mask.bits = 1; }
    else if (strcmp(s, "bookkeeping_only")) return 2;
    ret = qkx_test_cpus();
    CHECK(ret == expected);
    if (!strcmp(s, "pin_error")) { CHECK(!strcmp(trace, "P")); CHECK(online_mask.bits == 15); return 0; }
    CHECK(affinity_calls == 2);
    if (!affinity_error) CHECK(task.cpus_allowed.bits == 15);
    if (!strcmp(s, "partial")) CHECK(!strcmp(trace, "P123bcA"));
    if (!strcmp(s, "probe_error")) { CHECK(probe_calls == 1); CHECK(!strcmp(trace, "P123QbcdA")); }
    if (!strcmp(s, "restore_error")) CHECK(!strcmp(trace, "P123bcdA"));
    if (!strcmp(s, "probe_busy")) CHECK(probe_calls == 0);
    if (test_cpus == 1) CHECK(freeze_calls == 1 && restore_calls == 1 && hotplug_disabled == 0);
    if (!strcmp(s, "single")) CHECK(!strcmp(trace, "PA"));
    if (!strcmp(s, "bookkeeping_only")) { CHECK(online_mask.bits == 15); CHECK(probe_calls == 0); }
    return 0;
}
