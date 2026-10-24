# Checked shutdown implementation

Scope: Hollywood, Horizon `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. This adds an offline-tested native
coordinator and read-only completion primitives. **No stock hardware backend is
bound, no physical shutdown is validated and Linux handoff remains blocked.**
`module/loader.c`, including the unconditional Hollywood gate, is unchanged.

## Native coordinator

`module/shutdown.c` provides `qkx_shutdown_validate()` and
`qkx_shutdown_run()`. Both require ten explicitly indexed owner backends:
camera, DSP, GPU, Syncboss, USB, UFS, other DMA, SMMU, secure assignments and AOP.
Validation calls only read-only inspection hooks. Every owner must establish a
complete inventory, exclusive ownership, stable ownership epoch, admission
control, prompt hooks, independently verified recovery and the capabilities
needed by the entire transaction before the first action is issued.

The fixed 26-step schedule closes admission for the seven producer classes and
outside AOP submitters first. It then drains the seven producer classes, stops
their controllers, releases SMMU translations and secure assignments, drains
remaining AOP control traffic and retires AOP. DRAIN must initiate the owner's
stream/producer quiescence before waiting for outstanding work. Dependencies
needed by that operation remain available; final STOP follows every producer's
DRAIN. AOP's owned teardown channel remains available after outside admission
closes. A backend must refuse a schedule it cannot support; this order alone
does not prove a complete hardware dependency graph.

Each completion binds the client, transaction, epoch, attempt, original attempt,
subsystem, action and direction. A zero callback result alone is insufficient.
The forward transaction has one shared elapsed-time budget and one finite poll
budget, including when the clock stops advancing. Time is checked before and
after each operation hook. Clock rollback and deadline overflow refuse further
progress. Hooks must themselves be nonblocking: these checks cannot interrupt a
blocking driver, stalled bus or CPU. Inspection hooks have the same prompt-return
contract; the coordinator cannot enforce it from outside the hook.

An irreversible action is marked **before** issuing it. A failure before that
boundary undoes every attempted operation, including a partially failed begin,
in reverse order under a separate shared rollback budget. Undo must prove the
original attempt quiesced and unable to complete later. Admission remains closed
until every undo completes. A separate final pass reopens admission. Failure
during that pass records admissions already reopened and requires recovery;
reopening is not atomic. The first forward error, raw backend return, raw receipt
error and separate rollback errors remain available in the result. A failure
after an irreversible issue, or an incomplete rollback, reports
`recovery_required`; it does not invoke panic, reset or a guessed recovery path.

## Read-only completion primitives

`module/shutdown_io.c` implements bounded register observations and pure AOP
result interpretation. It is linked into the stage-only module but has no live
MMIO adapter, platform lookup, exported control, module parameter or call from
the existing loader.

| Primitive | Implemented check | Limit |
| --- | --- | --- |
| DWC3 | Logical DSTS offset `0xc70c`, `DEVCTRLHLT` bit 22 | A halt observation cannot prove endpoint, GSI/DBM, role, glue or global DMA completion. An eventual `dwc->regs` adapter must use the source's logical-offset conversion. |
| UFS drain | TM doorbell `0x78` and transfer doorbell `0x58` both zero | Does not close admission or establish active power, EH quiescence, lock ownership or absence of later requests. |
| UFS disabled | HCE `0x34`, enable mask `1`, clear | Does not issue disable, prove link/power transition or make storage rollback safe. |
| AOP channel close | Original enqueue result, sticky transport, epoch, transaction, channel and fresh remote channel acknowledgement | An accepted IPCC token is not an acknowledgement. This helper does not generate or authenticate a firmware receipt. |
| AOP LINK_DOWN | Always `-EOPNOTSUPP`, preserving raw inputs | Matching bits or a caller assertion cannot enable the missing peer protocol. |

Polling requires an owner-established binding, closed admission, active power,
stable lock/lifetime protection and matching epoch, maintained throughout the
operation. Each poll retains partial samples and original errors, has a fixed
deadline and a finite read cap, and checks time and prerequisites after every
successful read. A failing read returns its original error immediately. These
functions write no registers and call no PM, work-flush or driver-stop helper.

## Hardware binding status

| Owner class | Evidence and remaining binding work |
| --- | --- |
| Camera producers/workers | The [ownership audit](QUEST2_OWNER_AUDIT.md) and [tracking resource observations](QUEST2_FASTRPC_RESOURCES.md) are incomplete ownership evidence. Establish source-owned stream stop, worker cancellation and outstanding-frame completion with actual errors. |
| ADSP/CDSP and restart activity | Track every owned client, session, mapping and SSR/restart producer. A single reference release or sampled ONLINE state cannot establish global shutdown. |
| GPU | Establish admission, submissions/fences, worker/IRQ completion and checked power transitions. A PM callback result alone is insufficient. |
| Syncboss | Establish owned transfers, work/IRQ quiescence and checked completion/restore semantics. |
| USB/controller/glue | The stock suspend path discards endpoint/run-stop errors. Its callers hold the DWC3 lock; the legacy loader call does not. Callback and GSI/DBM completion remain unbounded or unproven. No complete stock stop adapter is supplied. |
| UFS | The stock shutdown path uses unbounded waits and returns success after ignored errors; HCE-stop loses its poll error. Runtime holds/work flushing are not bounded by the later doorbell timeout. A driver-owned admission/power/EH protocol is required before the read-only checks can be used. |
| Other DMA | Identify every remaining master and its owned queue/IRQ/reset dependencies; an inventory count is not a DMA fence. |
| SMMU | Release only owned translations after every producer and late completion are excluded. Provider removal while contexts remain active is insufficient. |
| Secure assignments | Establish exact assignment ownership, supported release semantics and secure-world completion without guessing a private ABI. |
| AOP | The IPCC sender's accepted enqueue is distinct from remote acknowledgement. Preserve the selected IPCC/MMIO transport and original errors. The published channel protocol does not establish the legacy loader's LINK_DOWN extension. |

## FastRPC adapter boundary

The source verifier additionally pins `drivers/char/adsprpc.c`, SHA-256
`f479739662bd427b577e493b518240f3c1ec0b7eb0b1cdf0f3da769efea6d49b`.
In that source,
[`fastrpc_file_free()`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4026)
discards the DSP-process release result before removing bookkeeping.
[`fastrpc_device_release()`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4096)
marks the file closed, drops references and returns zero. A sysfs or concurrent
operation may still hold a reference; FD disappearance does not prove destruction.

A checked DSP adapter cannot wrap close/init-stop and accept zero. It needs
owner-controlled admission, original DSP-process release results, outstanding
invocation completion, per-mapping/secure-assignment results and subsystem/restart
ownership, captured before stock cleanup discards them. No verified cooperative
owner interface or kernel instrumentation supplies all those receipts here.
Building a target Linux kernel does not change the running Horizon driver.
The new kernel-client metadata observations establish none of those completions.

## Verification

The host harnesses execute the production C against nonblocking owner, clock
and register doubles. They cover all missing-owner preconditions, the 26-step
order, stale/malformed receipts, partial failures at every step, irreversible
marking, deadlines, frozen/backward/overflow clocks, rollback cancellation,
failed restoration and partial admission reopening. IO tests cover offsets,
read limits/errors, late samples, epoch changes and AOP enqueue/ack distinctions.
They do not simulate concurrent hardware or demonstrate a recovery mechanism.

```sh
HOST_TEST_CC=clang python3 -B -m unittest tools.tests.test_checked_shutdown tools.tests.test_shutdown_io
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py'
git diff --check
```

Both production sources also compile as GNU89 with warnings treated as errors;
the coordinator's host object checks a 1 KiB stack-frame limit. The matching
kernel build archives each new object's real compiler command alongside the
existing ABI/provenance checks. Native build evidence is separate from host
fixtures and physical validation.

### Verified matching native build

The [matching loader build](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39927475572)
passed at source `14a10d95895dd779389896cc041d673dab82e1c4`. The downloaded
artifact contains 17 allowlisted files; all 16 inner checksums matched. Kernel,
compiler and config pins matched the existing build contract. Both new objects'
actual compiler commands use GNU89/AArch64, and the module contains the
coordinator plus all three IO entry points. Effective loader compilation remains
stage-only, with no experimental-jump define.

Independent ELF/vermagic/generated-and-stock CRC checks passed for all four
modules, covering 42, 15, 20 and 24 versioned imports respectively. The loader is
391,792 bytes, SHA-256
`76d072bef847470ae90c2a4df5b2f788f1ef0d29b5aefa1901a1ecdcff6455f6`.
That native build passed 358 host tests. The subsequent
[Linux regression run](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39929565569)
passed all 377 tests, including the recovery collector additions. Native module
sources and their build script are unchanged between these two source revisions.
No rebuilt module was loaded on the headset and no hardware shutdown was issued.

## Pinned public source

All source references use kernel commit
`6bde5fc06b252c97761bc0963dd13c1abcf377ab` and the exact archive recorded in
[shutdown validation](QUEST2_SHUTDOWN_VALIDATION.md). The observations above use:

- [DWC3 suspend and endpoint stop](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/usb/dwc3/gadget.c#L4499), [locking callers](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/usb/dwc3/core.c#L1711), [register definitions](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/usb/dwc3/core.h#L147).
- [UFS shutdown](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/scsi/ufs/ufshcd.c#L10865) and [register definitions](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/scsi/ufs/ufshci.h#L55).
- [QMP notification](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/mailbox/msm_qmp.c#L214) and [IPCC sender](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/qcom_ipcc.c#L182).
