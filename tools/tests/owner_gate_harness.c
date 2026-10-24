/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "owner_gate.h"
#ifndef ESTALE
#define ESTALE 116
#endif
#ifndef EOVERFLOW
#define EOVERFLOW 75
#endif
static u64 clock_now = 1000;
static u64 clock_on_lock;
static unsigned int locks;
#define DEFINE_SPINLOCK(name) unsigned int name
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags)=0; assert(!locks++); if (clock_on_lock) clock_now=clock_on_lock; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); assert(locks--==1); } while (0)
static u64 ktime_get_ns(void) { return clock_now; }
/* PRODUCTION */
#define QOG_FAILURE(line) fprintf(stderr, "Owner gate test failed at line %d\n", (line))
#include "owner_gate_cases.h"
int main(void)
{
	int cases = qog_cases();
	if (cases != 15) return 1;
	printf("OWNER_GATE_CASES=%d PASS\n", cases);
	return 0;
}
