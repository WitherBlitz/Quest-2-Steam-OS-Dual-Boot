# Selected object custody and asynchronous retirement

**Complete camera/FastRPC ownership and patched-driver headset execution remain
unvalidated.** This increment retains selected CRM queue/link/session objects
and FastRPC invocation/file objects across operations and callbacks. It does not
provide the complete producer, mapping, secure-memory or recovery protocol
required for Linux handoff.

Source: `6bde5fc06b252c97761bc0963dd13c1abcf377ab`. Development target: Hollywood
`52242990024200150`, kernel `4.19.325-cip128-st12-g49638c7a8637`.
`CONFIG_QKX_OWNER_GATE` defaults off and now requires camera and FastRPC built in.
Module conversion and deferred module-unload safety are outside this configuration.

## Custody contract

Within instrumented paths, an operation, borrowed task, queued/running callback
or outstanding invocation
keeps its object and required parent state alive. Client close requests retirement
without consuming another operation's custody. Returning a ticket does not run
a hardware destructor in that observer/callback context. Owning driver workers
perform selected finalization after its required tickets have returned. Failed
or ambiguous remote completion retains state.

Private sidecars preserve existing structure layouts and UAPI. No ownership/drain
controller or hardware-handoff capability is exported. Entry accounting remains
separate: its earlier isolated 15-case headset test does not validate these paths.

## Selected source coverage

| Area | Development behavior | Remaining boundary |
| --- | --- | --- |
| CRM tracking queues | Borrowed, queued and running task tickets participate in close admission and deferred destruction, preserving the selected pool and parent link/session. | Other camera queue users, device-request/mapping ownership and producer-specific DMA stop remain open. |
| CRM watchdog/close | Timer cancellation and deferred link/session/root close participate in the selected lifetime path. Failed disconnect preserves registration state. | Late callbacks carrying reused numeric handles need a producer/generation contract. Generic context, IRQ, sync and tasklet lifetimes remain open. |
| FastRPC invocations | A real file custodian precedes publication. Context custody covers caller activity, remote submission and callback execution; physical destruction runs in an owning worker. | Ordinary/global maps, cached/remote buffers, SMMU sessions, loaders and secure grants still need checked ownership. |
| FastRPC close/loss | Close seals ordinary acquisition. Parent retirement waits for children and checks the process-release RPC. Early hints and loss wakeups do not establish remote completion. | Unknown state has no automatic recovery. The original stack-backed control RPC remains unbounded; this is not a bounded close/drain controller. |

The earlier [cleanup prerequisites](QUEST2_RESOURCE_LIFETIMES.md) still apply.
Retaining an allocation does not prove hardware stopped using it. Remove/reset/
devm paths and proprietary consumers require separate coverage.

FastRPC transport/PDR loss seals the affected file, including contexts not yet
published on normal pending lists. Its custodian stays held without automatic
recovery. A close waiting on an incomplete child can remain parked indefinitely;
the process-release RPC is not an automatic cancellation of that unknown child.

CRM sessions have a private pointer/full-handle/generation identity established
before publication. Destruction seals that session before child retirement;
both link-creation variants and link control reject it afterward. The seal
survives zero remaining links and a failed handle destruction. Deferred retries
validate the captured generation under the CRM lock, and generation exhaustion
refuses publication. This does not resolve the separate producer callback/handle
contract mentioned above.

Link setup-failure cleanup, direct device-request mutation and root subdevice-close
results also remain outside this selected lifetime contract.

## Source application and tests

Custody manifests apply after the entry wrappers and checked-cleanup manifests.
All original whole-file hashes and unique anchors are checked before writing.
The custody phase additionally accepts only three named CRM headers. Their new
private declarations preserve existing layouts/signatures; cleanup manifests
remain restricted to C files.

Receipts retain `full_ownership: false` and `deployment: false`. LF attributes pin
manifest, copied-core and source-fixture bytes across Windows/Linux. Host fixtures
execute transformed production functions with controlled transport, allocation,
queue, timer and cleanup boundaries. They do not establish kernel concurrency,
remote DMA retirement or headset compatibility.

## Execution-route investigation

The [installed ABL audit](QUEST2_RESOURCE_LIFETIMES.md#exact-device-execution-investigation)
matched the active image and established its independent locked RAM-boot rejection.
Further static inspection covered its 48-command registration table; no
authentication-only command was identified there.

The rescue-signature handler accepts 256 bytes. Rescue flashing requires the
`firmware` target and verifies against an embedded RSA-2048 public key before
reaching a block-write callback. This is a signature-gated persistent flash path.
A successful RAM download alone would not establish image acceptance; see the
[AOSP protocol](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fastboot/README.md).

A public vendor [secure-call header](https://github.com/Rivko/android-firmware-qti-sdm670/blob/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271/trustzone_images/ssg/securemsm/trustzone/qsee/include/tz_syscall_pub.h#L1591-L1710)
documents metadata verification service `0x02000210`. Its
[XBL boot-stage caller](https://github.com/Rivko/android-firmware-qti-sdm670/blob/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271/boot_images/QcomPkg/XBLLoader/boot_extern_sec_img_interface.c#L80-L113)
supplies an eight-argument indirect request. That different-platform boot-stage
contract does not establish Hollywood's handler, normal-world permissions,
rollback domain or side effects. The suspected numeric records in the matching
OTA TZ image actually belong to a 42-record configuration table; they are
withdrawn as evidence of secure-service implementation. This increment has not
matched the active unit's TZ image.

The next prerequisite is an offline audit of that matching implementation and
complete contract. No secure call, bootloader-mode cycle, flash, downgrade or
driver takeover was performed. **A safe hardware-bound execution route is still
unestablished.**

## Verification status

Verified development source: `b8e2e2c5e0f3e8b1a9b3aa8436ca0c60b20f0800`.
The [native build](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40066672412)
passed the stock build and both default-off and enabled ownership builds. Their
retained compiler logs contain no errors or warnings. Only build and audit
outputs were exported; no installable kernel image was exported or deployed.

- `python -B -m unittest discover -s tools/tests -p 'test_*.py'`: **625 passed,
  no skips**, locally and in native CI. This includes 33 camera-custody,
  30 FastRPC-custody and 25 source-installer tests.
- Independent application to the pinned source verified all **44 generated-file
  hashes**: 150 entry sites, 54 cleanup edits and **56 custody edits across eight
  files**. The CI receipt matches that independent application.
- Independent ELF/version checks passed for five project modules. Across stock,
  disabled and enabled builds, all **12,362 existing export CRCs** were preserved.
  All 28 retained vendor modules matched **2,176 version records / 1,155 unique
  imports**, with zero CRC mismatches. Enabled support adds `qkx_owner_snapshot`.
- The 30-file CI artifact passed transport digest, archive allowlist, checksum,
  source/compiler/configuration identity and ABI-report checks. Its ZIP SHA-256
  is `1910db560b8ac1a2c1735718b2cbb7def070268a82539b430e75d6cf66f070bd`.

Independent review identified admission into a retiring CRM session and FastRPC
completion published after waking its waiter. Both were reproduced in failing
tests and corrected. Native compilation additionally exposed a wrongly typed
host parent double and an unsupported harness forward declaration; the harness
now reproduces those failures, and the production sources compile successfully.

These results establish source application, selected host behavior and native
build/ABI checks. They do not establish full object ownership, on-headset driver
execution, hardware shutdown or recovery. **The headset was unchanged by this
increment.**
