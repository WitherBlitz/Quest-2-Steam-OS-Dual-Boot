# Quest 2 startup patch review

This phase ports one interrupt-initialization change. It does not establish a
working kexec target or enable a physical handoff. The loader's Hollywood
hardware gate is unchanged.

The source basis is kernel
`6bde5fc06b252c97761bc0963dd13c1abcf377ab` and the Quest 2 patch series in
[upstream commit 9c96c192](https://github.com/machachu56/quest-kexec-q2/tree/9c96c192d4a4a80f95ba0013294977e85c6eae11/kernel/patches/quest2).
The original work is credited to machachu56 and its upstream contributors. The
older generic patches in this repository are not applied by this build.

## Selected change

Upstream patch 0001 adds pending and active SPI clearing to `gic_dist_init`.
The matching kernel's existing
[`gic_dist_config`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/irqchip/irq-gic-common.c#L119)
already deactivates and disables SPIs, then waits for the relevant register
writes. Its caller
[`gic_dist_init`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/irqchip/irq-gic-v3.c#L497)
does not clear their pending bits before enabling the distributor.

The adapted patch clears only pending SPIs after that common helper and before
distributor enable. It uses the driver's mapped GIC base, starting at interrupt
32; no new physical address, private interrupt, redistributor, watchdog, memory
reservation or secure call is introduced. Duplicate active clearing and the
upstream stale-state reads/logging are omitted.

This clears inherited software-pending state. It cannot silence a peripheral
that continues asserting its interrupt or fence DMA. Arm's
[GICv3/v4 overview](https://developer.arm.com/-/media/Arm%20Developer%20Community/PDF/Learn%20the%20Architecture/GICv3_v4_overview.pdf?revision=65f91645-cd52-4795-952b-f01095ff5ef8)
describes the dependence of level interrupts on the peripheral's asserted
signal. The host model intentionally lets an asserted level source stay pending
after the clear. Producer shutdown remains a separate requirement.

## Other upstream changes

These are dispositions for this phase, not approvals of the remaining patches.
Only the GIC functions above received a source-matched implementation and test.

| Upstream area | Disposition and prerequisite |
| --- | --- |
| `head.S` raw AP-watchdog disable | Deferred. Establish the matching watchdog/secure ownership and recovery contract before adding a pre-MMU register write. |
| Retained-RAM console, boot watchdog thread and panic/warm-reset hooks in `init/main.c` | Deferred. Fixed log slots have no validated reservation on this firmware; diagnostic reset and worker behavior also require review. |
| GIC active-state reset | Existing common helper retained. Only the missing pending clear is adapted. |
| SMMU bus-registration failure changed to success; USB IOMMU deferral bypass | Not imported. These alter failure and translation behavior without proving hardware ownership or producer shutdown. |
| Deferred probing, QMP/AOP, bus votes, clocks, display, camera and USB inherited-state changes | Deferred for driver-specific review and hardware evidence. Timeout suppression or successful probe return is not a completion protocol. |
| CPU frequency accounting removal | Not imported. Disabling an accounting feature is not a demonstrated fix for this firmware's startup. |
| Patch 0002/0003 SPMI IRQ and SMB5 initial-status changes | Deferred until inherited interrupt/charger state and the required initialization protocol are established. |
| Patch 0004 HS-PHY DPDM behavior | Deferred until the USB shutdown/reprobe protocol is validated. |
| Patch 0005 watchdog/bus diagnostics | Deferred with the watchdog and bus protocols. |
| Patch 0006 GPU RSC wake | Deferred until GPU power/clock ownership and completion are verified. |
| Patch 0007/0008 retained-log/watchdog diagnostics | Deferred with RAM reservation and recovery. No alternative fixed log address is guessed. |

## Build and verification contract

The owner-only offline target workflow defaults to the unchanged `baseline`
profile. `startup-gic-pending` selects exactly this patch and uses release
`4.19.325-cip128-st12-qkx-q2-startup`. Unknown profiles and patch bytes are
refused. The helper checks the pinned kernel commit, original source hashes,
exact test functions, permitted tracked modification and final source hashes.
It repeats the source audit after compilation.

`kernel-source.tar.gz` supplies the unmodified base. `port-source.tar.gz`
supplies the exact patch, configs/build scripts and initramfs recipes needed to
reconstruct the modified source. Provenance names both this arrangement and
the applied patch/source hashes. The private DTB producer accepts the legacy
unmodified baseline or this exact startup profile; it refuses an arbitrary
patch list. Both remain development-only with `HANDOFF_BLOCKED`.

Tests apply the real patch to exact function fixtures, compile both stock common
and patched distributor functions, and execute them against an MMIO state model.
They check disable/deactivate/clear/enable ordering, SGI/PPI preservation,
interrupt-count boundaries and the still-asserted level-source limitation. The
fixtures are compared against the actual source before CI patch application.
This model does not validate real GIC timing, EL/security state, interrupts from
live devices, CPU/MMU entry, watchdogs, recovery or a Linux boot.

Further handoff work still requires an accepted RAM claim, all producer/DMA and
secure/watchdog shutdown protocols, diagnostic visibility and recovery. This
partial startup port provides no override for those missing prerequisites.
