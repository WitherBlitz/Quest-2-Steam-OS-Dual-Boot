# Camera/FastRPC resource lifetimes and patched-driver execution

**Complete resource ownership is not implemented or validated.** This increment
adds source-integrated prerequisites: selected failed camera cleanup retains
reachable backing resources, and two FastRPC paths preserve their original
errors. It does not supply a global ownership transaction, hardware shutdown or
an established way to run the modified built-in drivers on this locked Quest 2.

The target remains Hollywood `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`, public source commit
`6bde5fc06b252c97761bc0963dd13c1abcf377ab`.

## Implemented source changes

Both lifetime manifests apply after the existing 150 entry wrappers. Original
whole-file hashes, strict schema and unique literal anchors are checked before
any source writes. The combined receipt records 54 lifetime edits across three
driver files and 41 generated files. `full_ownership` remains false.

| Source | Enabled behavior |
| --- | --- |
| Camera SMMU | Short, zero or overreported selected shared/secondary-heap unmaps and IOVA-free failures retain the affected mapping/backing state. Private sticky cleanup state refuses blind retries and selected handle/map reuse; forced cleanup propagates its first failure. |
| Camera memory manager | Failed finalization retains the occupied slot and backing. Forced table deinit refuses live slots, pending deinit blocks selected new acquisition paths, and reinitialization cannot erase a live table. Secondary-heap release refuses retained references and preserves an unsuccessful SMMU release. |
| FastRPC | Heap release returns the remote invocation error instead of a shadowed zero. A failed secure assignment remains the primary result even when the compensating unmap succeeds or fails; both failures are still logged. |

Behavior changes are guarded by `CONFIG_QKX_OWNER_GATE`, which still defaults
to off. Its development label now includes selected cleanup behavior. This is
not an observer-only option when enabled. Existing published driver structures
and UAPI layouts are unchanged. No fence/drain controller is exported.

Quarantine does not finish cleanup. It deliberately retains uncertain state;
there is no automatic retry of a possibly unmapped prefix, reset or recovery
procedure in these changes. Existing driver-remove/reset/devm paths and races
with creators already admitted before deinit remain outside this protection.
Do not treat the pending flag or a poisoned context bank as complete admission
control, object custody or proof that the DMA producer has stopped.

## What complete ownership still requires

The owning driver must retain each object from allocation until checked physical
retirement, including objects temporarily detached from normal driver lists.
The following source findings prevent an inventory or extra reference alone
from establishing that contract:

- CRM sessions lack retained lookup references; seven static link slots and
  handle slots can be recycled. `cam_get_priv` returns an unpinned pointer after
  releasing its table lock. Generation identities and deferred finalization are
  required before exposing transaction snapshots.
- Camera context references protect pool reuse, while hardware release and
  forced context reset have separate paths. Requests, checked-out work slots,
  timers and sync callbacks need transferable custody. Callback deregistration
  returning `-ENOENT` can mean the callback is already queued, not completed.
- Camera memory references do not cover every forced cleanup, secure stage-2
  attachment, callback, producer or remove path. The new selected guards do not
  turn those existing references into a global transaction.
- FastRPC must track every file, ordinary map, global heap map, cached/remote
  buffer, invocation context, SMMU session and detached teardown object. Global
  heaps clear `map->fl`; walking only per-file lists misses them.
- FastRPC protocol counters are not independent custody. An observer's final
  `fastrpc_file_put` can run hardware destruction. Merely returning early from
  its void free functions can lose detached children or let parents be freed.
  Independent custody and an explicit checked close controller are required.
- Remote invocations can wait without a bound and may reference stack-backed
  kernel output buffers. Adding a timeout without moving all pending storage
  into retained ownership would create an unsafe return path.
- RPMsg, SSR/PDR, service-locator work, callback registration, IRQ/timer/work
  completion and ADSP/CDSP loader references need lifecycle control. A restart
  notification waking a waiter is not successful remote retirement.

The transaction must atomically fence new producer work while allowing required
completions, take pins under the owning lookup locks, retain parent/child and
channel-generation relationships, and defer destructive close/reuse. Ordinary
observer unpin must never invoke hardware cleanup. Ambiguous remote or secure
completion must retain custody until independently checked recovery. A blocking
global writer around existing unbounded RPCs is not a bounded acquisition.

## Exact-device execution investigation

The current drivers are built into Horizon. Loading a namespaced second module
does not transfer existing file contexts, global mappings, RPMsg endpoints or
camera callbacks. FastRPC suppresses platform bind attributes and lacks a
platform remove callback; its initialization separately owns character devices,
proc/class state, RPMsg and restart registrations. `driver_override` changes
future matching and does not unbind an existing driver. See the pinned
[FastRPC source](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c)
and [platform ABI](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/Documentation/ABI/testing/sysfs-bus-platform).

The exact OTA's ABL image is 274,432 bytes, SHA-256
`75dc2174995fd39588bcbbd8b3455afc7bad4999a8914e0c40b9cc1509bcef5c`.
A bounded root read of the active ABL partition prefix matched those bytes.
Firmware/kernel identity stayed equal, with a locked boot state; no partition
write or reboot occurred. Offline extraction of its ARM64 LinuxLoader found the
registered `boot` handler at PE offset `0x45168`. At `0x451b0` it calls the same
unlocked-state getter used by device-info reporting and returns 6 when locked,
before image authentication. This is a specific installed bootloader gate,
not an inference from generic Android behavior. Firmware bytes remain private.

The inspected public unlocker is frozen at commit
`7f34276ecef9021771e7bf4a2d53f10b26243334`. Its
[Hollywood profile](https://github.com/darknight1050/quest1-bootloader-unlocker-web/blob/7f34276ecef9021771e7bf4a2d53f10b26243334/src/data/profiles.ts#L214-L225)
and [initial gate](https://github.com/darknight1050/quest1-bootloader-unlocker-web/blob/7f34276ecef9021771e7bf4a2d53f10b26243334/src/lib/device.ts#L227-L239)
refuse this build. Its [root stage](https://github.com/darknight1050/quest1-bootloader-unlocker-web/blob/7f34276ecef9021771e7bf4a2d53f10b26243334/src/lib/root.ts#L216-L218)
can accept existing root, so that software ceiling does not prove boot-chain
hardware incompatibility. Singularity root satisfies a privilege prerequisite,
not the separate rollback/image-acceptance requirements.

The inspected unlock flow persistently writes 17 boot-chain partitions, changes
the active slot, boots the older chain, persists unlock and wipes data. The old
signed ABL has rollback version 0 while this matching ABL declares version 1.
The unit's enforced minimum is unmeasured. Neither accepting version 1 nor the
tool's support ceiling establishes whether version 0 will be accepted.

ABL exposes cached `arb-index:0` through `arb-index:31` software floors and an
`arb-fw-val:pil` value. No verified mapping establishes that the latter proves
acceptance of all old ABL/XBL/TZ images; no authentication dry-run query was
established. No device-mode cycle was performed solely to read these insufficient
values, and no unlock or downgrade was attempted.

Kexec still requires the current kernel's ownership/DMA shutdown, memory and
entry prerequisites plus independent recovery. New drivers in the target kernel
cannot supply missing ownership controls to the old drivers before that jump.
Thus **no hardware-bound patched-driver execution route has been validated for
this unit**. An independently established development boot path remains a
prerequisite for that test; source compilation cannot substitute for it.

## Verification

The source-level regressions execute exact pinned function bodies with controlled
IOMMU, DMA, reference, transport and secure-call backends. Fourteen camera tests
and eight FastRPC tests cover enabled changes and disabled stock behavior. They
do not attach real drivers, prove concurrent lifetime coverage or stop hardware.

All **558 Windows and native Linux host tests passed without skips**. The
[native build](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40059769642)
passed at source `a85dd9de5ff3936b50854f961e2b7262568919bd`: unchanged stock kernel
and module ABI gates, followed by patched `Image modules` builds with the option
off and on. All three changed driver objects compiled in both configurations,
without compiler errors. Effective config comparison allowed only the requested
ownership-option setting to differ from the approved normalized stock config.

The downloaded 30-file artifact matched GitHub's archive digest and every file
checksum. All 41 generated source outputs matched the independently applied
receipt. Independent ELF/vermagic/import checks reproduced all five project
module audits. Both patched symbol tables retained all **12,362 existing export
CRCs unchanged**; the enabled build added only `qkx_owner_snapshot`. All **2,176
version records / 1,155 unique imports across 28 retained stock vendor modules**
matched stock, disabled and enabled symbol tables, with zero missing/mismatched
CRCs. This is import-ABI evidence, not runtime lifetime or device compatibility.

One independent safety review found no Important/Critical new regressions,
passed 60 focused tests and rechecked all 41 applied-source hashes. Python AST,
Bash syntax, local documentation links and whitespace checks passed. There is
no configured Python linter. The review withheld judgment on complete custody,
concurrent admission/teardown, physical DMA retirement and actual driver
execution; those boundaries remain unresolved.

No new module was loaded on the headset for these cleanup changes. The earlier
isolated 15-case entry test is recorded separately in
[the entry-accounting evidence](QUEST2_DRIVER_ENTRY_ACCOUNTING.md). No boot-chain
write, unlock, downgrade or reboot was performed by this increment.
