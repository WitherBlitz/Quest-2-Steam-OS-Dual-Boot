# Quest 2 sensors and tracking service lifecycle audit

This read-only audit maps startup, compiled cleanup paths and candidate
completion checkpoints for Hollywood build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It follows the
[descriptor ownership audit](QUEST2_OWNER_AUDIT.md).

The follow-up [tracking delegate/runtime attribution audit](QUEST2_TRACKING_OWNERSHIP.md)
found null delegate/user-data values in a matched candidate object at two samples
and corroborated 19 character-device FDs. Exact session/remote ownership and
checked hardware release remain unproven.

**No verified global service-stop/drain protocol was established.** Init control
success, process exit and the inspected shutdown logs cannot establish camera
or DSP quiescence. Camera shutdown can discard failures; tracking cleanup can
block, and its TERM handler has a competing re-raise path. No service was stopped
or signalled. Hardware shutdown, DMA quiescence, recovery and Linux handoff
remain unvalidated; the loader gates are unchanged.

## Evidence and limits

On 2026-10-22 America/Denver, fixed passive reads captured four matching init
files from 105 files in `/odm/etc/init`, `/vendor/etc/init` and `/system/etc/init`,
selected service properties, two stable process/executable/map identities, and
13 selected executable/library files. Each ELF copy matched remote size/hash
before and after transfer and its local SHA-256. All three ELF capture groups
preserved exact identity/root, boot, CPUs 0-7, 20 stock module names and taint
5124. This is basic health agreement, not a new subjective Home/tracking test.

The combined private init capture is 5,726 bytes, SHA-256
`28dc140225e1d3872ce032f7d4f8c35ba9a35187a463bc062369664e3c123c54`.
Raw configs, proprietary binaries, memory maps, PIDs, boot identity and device
identifiers remain private. Only standard paths, hashes and derived findings
are published.

Analysis used LLVM **23.1.3**: ELF headers/notes, dynamic symbols, relocations,
embedded `.gnu_debugdata` symbols, unwind metadata and bounded AArch64
disassembly. REA reported no usable native analysis provider on this host; no
engine or configuration was installed. The results below are ordinary LLVM
observations and analyst inferences, not REA decompiler or runtime observations.
Symbols and strings alone were not treated as executed cleanup.

Referenced artifact identities:

| ID | Device path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| I | `/system/bin/init` | 2714944 | `7e65e9b7c01052802bca2388e1d5ffe3c6e00c4fbce5f325931a7a03168a0a1b` |
| S | `/odm/bin/hw/vendor.oculus.hardware.sensors@1.0-service` | 1993064 | `707f20b804fb1ce6f7891b498143da7aa4c866c1a1ea3a9ddccf58a844e2d789` |
| T | `/odm/bin/trackingservice` | 4061560 | `36f21b23bff38c84cede286e754a7eb66674d54050cb02de9d364ba3813492f2` |
| O | `/vendor/lib64/libosutils.so` | 372192 | `732928eb8be6c6f1059aaacd212fd7c582d45d7a1dbb429e1cff071b20b39509` |
| C | `/odm/lib64/libcamerahal.so` | 68448 | `8e4db667762ea0298a48a2d97bc0bce39c0b0e3fba19b43d6d7646261b97669e` |
| F | `/vendor/lib64/libfastpathcamerahal.so` | 2155784 | `3731fb22bd4503241cfbad1ec599352a04ab393e13ad289fd02e7650f4d37d31` |
| E | `/odm/lib64/libtrackingengines.so` | 44258784 | `215611366c2732e2b6b8b358913cee5e1f7f4ceae76d34b1b8d5fa3f8105f1fb` |
| R | `/vendor/lib64/libcdsprpc.so` | 262984 | `1b5f80e8128b10e148f53aa61e47259f3becd648ca9efc23bc0fd1b5ef8a75a1` |

S Build ID is `e357fea4ef97e5f665923fc423791eca`; T is
`4f444b5ce3424f9dcfbe676741e0631981a764ba`; I is
`3abe4bea3d209562e83f160d72e0dd61`. Addresses below are **linked ELF VAs** in
the identified artifact, not live process/kernel addresses. The cited code
locations map VA to the same file offset; E's vtable entry below has a distinct
file offset. A runtime mapping base would still be required. These are evidence
locations, not supported hook interfaces.

## Startup and restart ownership

| Init label | Definition | Selected observed state |
| --- | --- | --- |
| `vendor.oculus.sensors-hal-1-0` | `/odm/etc/init/vendor.oculus.hardware.sensors@1.0-service.rc`; class `hal`, user `system`; exports camera, controller, IMU and other sensor interfaces | `running`; executable matched S |
| `trackingservice` | `/odm/etc/init/odm.trackingservice.rc`; class `hal`, user `system`, groups `system uhid inet audio` | `running`; executable matched T |
| `vendor.adsprpcd` / `vendor.adsprpcd_audiopd` | `/vendor/etc/init/vendor.qti.adsprpc-service.rc`; class `main`; second instance has `audiopd` argument | Both `running` |
| `vendor.cdsprpcd` | `/vendor/etc/init/vendor.qti.cdsprpc-service.rc`; class `main`; config includes a property-triggered init stop | `running`; trigger was not changed |

The sensors definition includes restart commands for `calibration_svr`,
`mrsystemservice`, `sensorproxy` and `trackingservice`, plus `shutdown delayed 20`.
The first two related service properties were `running`; `sensorproxy` was
empty/unreported, not established as stopped or absent. Neither captured main
service block declares `gentle_kill`, `oneshot` or `disabled`.

This is a selected definition/property map, not an exhaustive effective init
import/override graph or a read of private runtime service flags. A different
import, override or later policy action is not excluded by the file scan.

Restart commands must not be confused with intentional-stop behavior. In I,
`StopOrReset(1)` sets the disabled bit and clears restarting; `Reap` tests mask
`0x41` at `0x16b558–0x16b560` and bypasses restart actions on that stopped path.
The restart path executes the action at `0x16b9fc–0x16ba0c`. Explicit restart and
unexpected-exit restart can therefore invoke those commands; intentional init
stop suppresses them. Other clients, property triggers and service-start policy
were not exhaustively mapped, so global prevention of respawn is not proven.

## Init control is not a drain handshake

I `DoControlStop` calls `Service::Stop` at `0x195d50`; `Stop` at `0x16f630`
passes 1 to `StopOrReset` at `0x16f420`. Without flag `0x2000`, it reaches
`KillProcessGroup(SIGKILL)` at `0x16f500–0x16f50c`. `ParseGentleKill` sets that
flag at `0x19afc4`; even this option sends TERM, optionally waits 200 ms, then
reaches KILL. It is not a checked per-producer completion protocol.

`DoControlStop` constructs a success result at `0x195d54–0x195d68` after Stop
returns; the control response is sent at `0x193428`. No child-reap or hardware
drain check occurs in that success path. The imported KILL helper may itself do
process-group work; this audit does not label it purely asynchronous. Its
failure is not propagated into that control success result.

`Service::Terminate` at `0x16f640` is a different path: it sends TERM at
`0x16f680`, marks `stopping`, and contains no local reap wait or KILL escalation.
The custom delayed-shutdown configuration uses it in a selected whole-system
shutdown phase, then gives the sensors process **20 seconds** to exit. Parser
locations `0x19f1e4–0x19f20c` validate 0..60 and store the seconds value.
`DoReboot` sends TERM at `0x1c0af8`, waits/reaps with 100 ms polling at
`0x1c13f0–0x1c1418`, and invokes ordinary Stop at `0x1c1564` on expiry.

This classification depends on shutdown mode and `ro.init.shutdown_animation`:
the true-property path skips it at `0x1c07b8`. A fresh read found that property
empty; the inspected getter at `0x1c05a0–0x1c05dc` defaults false. Runtime total
service order and every possible reboot path remain unobserved. The option does
not change ordinary init stop, and process-exit allowance is not a DMA fence.

## Sensors: conditional cleanup and lost results

S main constructs/starts O's `ShutdownSignalHandler` at `0x134f08` / `0x134f50`.
O installs TERM among its handlers and starts a detached worker. Its worker
polls indefinitely for a signal notification, then invokes the supplied TERM
callback synchronously at `0x3b160–0x3b17c`.

S's callback at `0x13b5d0` first calls Syncboss producer shutdown at `0x13b6c0`.
It only calls `terminateLockedService` when bit zero of a captured atomic byte
is set (`0x13b6c8–0x13b6d8`). The flag's semantic identity and current value were
not established. O's helper at `0x3b380` locks a mutex and dispatches normal
`terminate` versus crash cleanup, then clears the shared pointer. The callback,
mutex and joins inspected here have no explicit local timeout. After callback
return, O exits or restores/re-raises the saved TERM action according to its
saved-handler state. This is not an unconditional global hardware-stop API.

The compiled camera candidate chain is:

`CameraProvider::Terminable::terminate` → camera-system virtual shutdown →
`SyncbossV2CameraSystem::shutdownCameras` → `CameraSetLifecycleManager::shutdown`
→ streaming shutdown → setup shutdown.

| Checkpoint | Static evidence | Limit |
| --- | --- | --- |
| Terminable camera loop | S `0x156e70`, virtual slot `+0x58` at `0x156e9c` | Current terminable/system object selection is not proven |
| Lifecycle shutdown | S `0x11f8ac` calls `0x6f710`; outer `onCamerasStopped` follows at `0x11f8b4` | That notification is not aggregate success |
| Streaming stop | S `0x762a8` calls `doStopHalCameras(true,false)` | False result logs at `0x763c0` and continues; per-camera stop errors log at `0x763e0` and continue |
| Setup deinit/disable | S `0x76530` / `0x76544` | Deinit return is ignored; failed disable logs at `0x76638` and continues |
| Backend stop dispatch | C `CameraBase::StopCamera` at `0x6210`, virtual slot `+0xf0` at `0x6280` | Table-compatible F target is `MonoCameraOculus::Stop(bool)` at `0x13b730`; runtime binding remains unproven |
| Host worker / stream-off | F joins at `0x13b89c`, calls `CSLStreamOff` at `0x13b8b0` and checks its result | Host-worker exit and stream-off success are separate from session/device release |
| Device destruction | F `DestroyCamera` calls `CSLReleaseDevice` at `0x13b058` | Handle is cleared at `0x13b05c` without checking that result |
| Final session close | F destructor calls `CSLClose` at `0x10f7e4` / `0x10f7f4`, then `CSLUninitialize` at `0x10f804` | Reaching that destructor from this termination chain is not proven |

F's normal Stop can also report success while other references remain
(`0x13b78c–0x13b7a8`); its true argument bypasses that remaining-reference path.
A success value must therefore be interpreted with the actual call/ownership
context, not just its name.

S `CameraStream::streamControl` at `0x7f820` has per-client stop/disconnect
paths: command 1 calls `stopClient` at `0x7fd20`, command 2 removes a stopped
client at `0x7fc94`. `stopClient` only invokes aggregate shutdown on the last
client transition (`0x14d2f0–0x14d32c`). This identifies an owned-stream lifecycle
candidate, not a global-stop transaction or permission to operate another
client's handle. Its scoped watchdog does not establish a TERM cleanup deadline.
Nor does camera cleanup alone account for every registered IMU/controller or
other DMA producer.

## Tracking: notification, join and engine boundary

T main at `0x1702a4` calls run at `0x170358`; run constructs/sets up its signal
handler at `0x14cc28` / `0x14cc70`. The detached worker at `0x39d0a0` waits
through an indefinite epoll helper, and calls cleanup for TERM or HUP at
`0x39d160–0x39d194`. TERM then reaches `exit(0)` at `0x39d1d0`; HUP loops rather
than establishing process/Binder-pool shutdown. USR2 takes a separate
`quick_exit(0)` path at `0x39d204`, bypassing this callback.

TERM also has a competing signal-thread path: setup sets a flag at `0x39cc64`;
the notification callback restores the prior TERM action at `0x39d074`, then
re-signals itself at `0x39d08c`. This branch contains no TERM cleanup-completion
wait. The saved prior action is unknown, so the worker's successful completion
before process death cannot be assumed. A nearby 50 ms wait belongs to the
ABRT/SEGV path, not TERM.

Cleanup at `0x39cdfc` locks an initialization mutex before dispatching the
service's virtual cleanup and releasing its shared pointer. Main holds that
mutex from `0x14cc84` to `0x151e68`, so a startup-time cleanup can block there.
The optional host stop target at `0x1d44c4`, when its active/thread-present path
is selected, clears its active byte, writes a wake byte at `0x1d4504` without
checking success, and joins the host thread at `0x1d4514` without a local timeout,
then resets descriptors. A null host pointer or inactive host can skip that
work. This is socket-monitor cleanup, not established FastRPC/DSP teardown.

After that optional host-stop path, the compiled cleanup dispatcher reaches
`0x170188`, then an object field `+0x14f0` / vtable slot `+0x60` at
`0x1701bc` / `0x1701e8`. Its
construction can be narrowed statically: T stores a queried
`InsideOutTrackingState` version-4 interface into that field
(`0x204900–0x204910`, query `0x208978` / `0x2089bc`, name at `0x85f81`).
The registry comes from T's explicit `dlopen("libtrackingengines.so", 2)` at
`0x1ba9c4`, then `capabilityRegistryCreateV3` resolution/call at
`0x1baa50` / `0x1baa74`, with ABI value `0x24`.

E contains a matching version-4 capability factory at `0xa74950`. Its candidate
State interface vtable slot `+0x60`, **VA `0x2983168`, file offset `0x297f168`**,
resolves to code `0x133fbc0`. That short dispatcher reads an optional stored
callable at State pointer `+0xe8`: null returns immediately, otherwise it calls
the callable's invoker. The identified raw-callback invoker at `0xc2b7f0`
forwards stored function/user-data values. This is a callback boundary, not a
direct FastRPC release or checked completion result.

The same E adapter registers `InsideOutTrackingApi` version 5; its setter at
`0x133faa0` populates that callable. T's API-glue constructor at `0x34f894`
queries that interface and registers concrete callback **T `0x3500f4`**, with
the glue object as user data, through slot `+0x28` at `0x34fa44` / `0x34fa84`.
This ties the matching adapter chain to an executable callback, rather than
leaving its compiled identity unknown.

That callback dispatches its captured provider at glue `+0x18`, slot `+0x10`.
The constructed provider resolves to T `0x351810`: it takes a shared mutex,
clears a state byte at `0x351864`, publishes state through `0x351dc8`, and updates
producer-consistency counters. The multiple-producer assertion here is not a
consumer or hardware acknowledgement. No checked DSP-release result was
established in this resolved path.

The callback then invokes an **optional delegate** at glue `+0x68`, with user
data at `+0x70`, if non-null (`0x350168–0x350180`). These fields initialize to
zero; setter T `0x34fac0` stores supplied function/user-data values. The supplied
delegate's installation and function identity were unresolved in this static
pass. The linked follow-up narrows the inspected startup graph and samples a
matched candidate object; it does not establish lifetime nullness or hardware release.

The explicit library load and matching registrations support the compiled
adapter chain. Actual registry population/instance selection and delegate
installation were not observed at runtime; E initializes a factory vector
through indirect calls at `0x884220`. E and R were captured and identified, but
strings such as `ipc_Shutdown` or Hexagon/FastRPC shutdown names do not establish
an externally supported global-stop transaction or completed remote release.
State publication and optional callback return still require separate producer
release proof.

Logs are useful ordering checkpoints when correlated with the specific cleanup
instance and path; they do not independently show which optional branches ran:

| Log / state | What its compiled position can establish | Missing proof |
| --- | --- | --- |
| Tracking `crashCleanup` at T `0x225e64` | On the inspected normal-cleanup chain, the preceding optional host-stop path returned | Whether the host/join branch ran; engine/DSP release |
| `Stop trackingservice` at T `0x226a90` | The preceding cleanup wrapper returned | Whether an engine callback was present/executed; success, owner release and DMA quiescence |
| Worker `Exiting` at T `0x39d1c8` | Worker callback returned; exit is imminent on that path | Competing TERM re-raise behavior and hardware completion |
| Init `stopped`, exited PID or missing FD | Process/descriptor lifetime changed | Successful orderly producer drain |

## Next checked boundary

Before a live stop rehearsal, resolve runtime camera/engine bindings, the
tracking delegate installation and the exact owned client/handle set. Record
each stop, worker join, `CSLStreamOff`,
hardware/device/session release and engine/FastRPC completion separately,
including errors currently discarded. Correlate those results with kernel
camera/DSP references and the actual producer/DMA state while mappings, clocks
and interconnect remain available. Prevent or account for new submissions and
policy-driven restarts.

An independently observable recovery deadline and proven Horizon return remain
prerequisites for a consequential shutdown test. No init property, signal,
Binder transaction or new diagnostic module is supplied as a verified stop
recipe by this audit. The existing [hardware prerequisites](QUEST2_SHUTDOWN_VALIDATION.md#remaining-hardware-prerequisites)
remain open.

## Reproducing and checking the audit

Use private copies of the exact hash-matching firmware artifacts. For example,
`llvm-readelf --file-header --notes --dynamic service.elf` checks the container,
Build ID and dependency metadata; `llvm-objdump -d --start-address=0x16f630
--stop-address=0x16f638 init.elf` inspects the cited Stop stub. Decompressing the
artifact's `.gnu_debugdata` section with LZMA and passing that ELF to
`llvm-nm -C -S -` recovers available embedded names; stripped functions also
require relocation/unwind and instruction analysis. No artifact is executed.

This phase changes documentation only. All **246 existing host regression tests
passed**; they do not exercise hardware shutdown. The 13 private ELF copies were
rehashed against their capture manifests. Independent static review found no
remaining actionable findings; relative document links, `git diff --check`
and privacy checks passed. A kernel/native module rebuild
cannot add shutdown proof to this read-only audit. Proprietary captures are not
included in the repository.
