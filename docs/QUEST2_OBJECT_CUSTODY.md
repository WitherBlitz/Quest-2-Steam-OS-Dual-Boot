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

An operation, borrowed task, queued/running callback or outstanding invocation
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
documents metadata verification service `0x02000210`. This different-platform
source does not establish Hollywood's complete ABI, permissions, rollback domain
or side effects. Numeric records in the matching OTA TZ image are not recovered
handlers or proof of normal-world callability. This increment has not matched
the active unit's TZ image.

The next prerequisite is an offline audit of that matching implementation and
complete contract. No secure call, bootloader-mode cycle, flash, downgrade or
driver takeover was performed. **A safe hardware-bound execution route is still
unestablished.**

## Verification status

Implementation and independent verification are in progress. Final source counts,
host-test results, native build identity and review findings will be recorded
before publication as verified development work.
