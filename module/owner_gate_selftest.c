// SPDX-License-Identifier: GPL-2.0-only
/* Isolated controlled-clock accounting tests. No stock driver entry is bound. */
#include <linux/module.h>
#include <linux/ktime.h>
#include <linux/of.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/utsname.h>

static bool observe;
module_param(observe, bool, 0);
MODULE_PARM_DESC(observe, "Explicitly run isolated entry-accounting tests, no driver bindings");
static u64 clock_now = 1000, clock_on_lock;
static unsigned int locks;
static u64 qog_test_clock(void) { return clock_now; }
static void qog_test_lock(spinlock_t *lock, unsigned long *flags)
{
	spin_lock_irqsave(lock, *flags);
	locks++;
	if (clock_on_lock) clock_now = clock_on_lock;
}
static void qog_test_unlock(spinlock_t *lock, unsigned long flags)
{
	locks--;
	spin_unlock_irqrestore(lock, flags);
}
#undef spin_lock_irqsave
#undef spin_unlock_irqrestore
#define spin_lock_irqsave(lock, flags) qog_test_lock((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) qog_test_unlock((lock), (flags))
#define ktime_get_ns qog_test_clock
#define CONFIG_QKX_OWNER_GATE 1
#define QKX_OWNER_SELFTEST
#include "../kernel/owner_gate.c"
#define QOG_FAILURE(line) pr_err("QKX_OWNER_SELFTEST failure line=%d\n", (line))
#include "../tools/tests/owner_gate_cases.h"

static int __init qog_init(void)
{
	const char *model;
	int cases;

	if (!observe || strcmp(init_uts_ns.name.release,
		"4.19.325-cip128-st12-g49638c7a8637") || !of_root ||
	    of_property_read_string(of_root, "model", &model) ||
	    strncmp(model, "Hollywood", 9) || !of_machine_is_compatible("qcom,kona"))
		return -EINVAL;
	cases = qog_cases();
	if (cases != 15) return -EINVAL;
	pr_info("QKX_OWNER_SELFTEST cases=%d result=PASS driver_bindings=0 controlled_clock=1\n", cases);
	return 0;
}
static void __exit qog_exit(void) { }
module_init(qog_init);
module_exit(qog_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Quest 2 isolated ownership entry-accounting self-test; no driver controls");
