# Quest 2 tracking delegate and runtime resource attribution

**The matched candidate API-glue object's optional delegate and user data were both
null at two samples.** Static tracing found no installation in the inspected
normal startup paths. The descriptor follow-up attributed 19 non-`O_PATH`
character-device FDs to five processes, including the sensors service and
trackingservice. Exact CRM session/stream owners and remote FastRPC bindings
remain unproven; these results do not validate shutdown or Linux handoff.

This follows the [service lifecycle audit](QUEST2_SERVICE_LIFECYCLE.md) and
[ownership audit](QUEST2_OWNER_AUDIT.md), on 2026-10-22 America/Denver. The target
was Hollywood build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. All 13 previously captured ELF files were
rehashed locally and against the current headset; their hashes matched the
private manifests. T below is the lifecycle audit's `trackingservice` artifact,
SHA-256 `36f21b23bff38c84cede286e754a7eb66674d54050cb02de9d364ba3813492f2`.
T addresses here are linked VAs equal to file offsets, not live addresses.

## Delegate installation trace

T's constructor zeros fields `+0x40..+0x7f` at `0x34f900` / `0x34f904`, including
delegate/function `+0x68` and user data `+0x70`. Setter `0x34fac0` stores supplied
function/user-data values; vtable entry `0x3d3448` exposes it at slot `+0x20` of
vptr `0x3d3428`. The callback loads the function at `0x350168`, skips a null
value at `0x35016c`, or tail-dispatches at `0x350180`. This boundary has no
checked release result or local deadline.

The one traced API-glue construction is main `0x14e700` → `0x16d1c0` →
`0x34f894`. Its retained pointer/control-block pair is stored at run-stack
`+0x1e0/+0x1e8` at `0x14e708`.

| Compiled startup branch | Consumers and registrations | Delegate result |
| --- | --- | --- |
| `persist.ovr.tracking.head.use_pal` true | Wrappers `0x34aad8` / `0x34ad38` register glue slots `+0x10`, `+0x28`, `+0x18` | No slot-`+0x20` installation established |
| Property false; inspected getter defaults false | Alternate wrapper `0x349fec`, called at main `0x14e874`, registers slot `+0x28` | No slot-`+0x20` installation established |

A whole-T direct `B/BL` search found no calls to `0x34fac0`; its vtable entry
was the only relocation targeting that setter. Fourteen broader constant-code
registration candidates using virtual slot `+0x20` were not tied to this
API-glue object. An identically numbered slot on a different interface is not
this setter. These are bounded static results, not proof against every later
indirect caller or permanent nullness. The property was not changed, and a
fresh property value would not independently prove its value at startup.

The action preceding the optional delegate remains provider dispatch
`0x350130/0x350164` → `0x351810`: state publication and consistency checks.
Neither that publication nor a null delegate supplies an observed camera/DSP
release acknowledgement.

## Bounded runtime candidate observation

The observation used ordinary userspace memory through read-only
`/proc/<pid>/mem`, with no debugger attachment, process suspension, injected
code, target writes or heap scan. It selected the main thread using the exact
process start identity, executable and an available blocked-syscall SP.

The locator was tied to this artifact's compiled frame layout. If `A` is the
stack address of saved return address `Tbase + 0x151ff4`, then:

```text
runSP = A + 0x18                    (16-byte aligned)
word(A - 8) = runSP + 0x8e0
word(runSP + 0x8e0 + 8) = Tbase + 0x17035c
API-glue pointer = word(runSP + 0x1e0)
```

These relationships follow run's prologue at `0x14cb50..0x14cb6c`, its call at
`0x151ff0`, callee saves at `0x39c674..0x39c680`, and main's call at `0x170358`.
The unique candidate lay above the sampled main SP; its object vptr matched
`Tbase + 0x3d3428`. Its provider at object `+0x18` matched vptr
`Tbase + 0x3d36b0`. Both selected object types, frame relationships, pointer
values, process identity and mappings agreed across the paired observations.

The stack window was **12 KiB**, under its 16 KiB cap. Six page-aligned reads
transferred **40 KiB** total, under a 64 KiB cap: two stack windows, two page
reads covering the 136-byte API-glue object, and two page reads covering the
provider vptr. Function `+0x68` and user data `+0x70` were zero in both object samples.
No non-null delegate or user data was dereferenced. Only necessary decoded
words and hashes were retained privately; unrelated raw memory was discarded.

Ranges required `rw-p`, zero file inode/device identification and normal
anonymous stack/heap mappings. Before/after mapping and `VmFlags` checks
rejected shared, file, device, executable, I/O, PFN, mixed, hugetlb and
userfault mappings and unknown flags. Each host command had a 20-second
timeout, with a 90-second budget for beginning/completing observation commands;
there was one live attempt and no automatic widening or retry.

This is a strongly checked **candidate object observation**, not an unwind from
the live x29 register or an atomic snapshot. Stale frame data, concurrent remaps
and ABA changes cannot be excluded absolutely by paired reads. Source review
also shows page faults, temporary references, page touching and COW can occur:
read-only access is not zero-impact metadata. Userspace deadlines do not bound
kernel lock/fault completion. The sampled null values do not prove lifetime
nullness, consumer acknowledgement, producer release or DMA quiescence.

## Runtime descriptor attribution

The existing passive collector sampled **698 processes** and **21,376 FD
symlinks**, with two process-coverage gaps and zero matching FDs discarded from
those gaps. A follow-up checked the 19 selected FDs using process start identity,
executable, init service PID properties, FD symlinks, `fdinfo` and followed
`stat` metadata. All 19 matched their character node's mode, device identity and
inode; all lacked `O_PATH`. The follow-up had no additional gaps.

| Verified executable / matching init label | Selected descriptors |
| --- | --- |
| `/odm/bin/hw/vendor.oculus.hardware.sensors@1.0-service` / `vendor.oculus.sensors-hal-1-0` | 15: `/dev/video0`, `/dev/video1`, `/dev/v4l-subdev0` through `12` |
| `/odm/bin/trackingservice` / `trackingservice` | One `/dev/adsprpc-smd-secure` |
| `/vendor/bin/adsprpcd` / `vendor.adsprpcd` | One `/dev/adsprpc-smd-secure` |
| `/vendor/bin/adsprpcd` / `vendor.adsprpcd_audiopd` | One `/dev/adsprpc-smd-secure` |
| `/vendor/bin/cdsprpcd` / `vendor.cdsprpcd` | One `/dev/adsprpc-smd-secure` |

The name inventory identifies `video0` as CRM, `video1` as camera sync and the
subdevices as CPAS, ISP/fastpath, CSIPHY and sensor nodes. This strengthens
sampled process/driver attribution beyond an unverified pathname. It still
does not identify the creator, unique shared open file, individual CRM sessions,
streams, FastRPC domain, remote-process handles or kernel-held references.
FD duplication/inheritance/transfer and reuse races remain possible. A stable
PID does not make an FD scan atomic or fill its coverage gaps.

`fdinfo` temporarily pins/releases the file and can delay a concurrent final
close; followed stat and symlink reads do not call the target driver's open.
The inspected FastRPC/V4L2 file operations have no custom `show_fdinfo` handler.
These are source-backed limits, not a claim of no kernel memory-management
effects or exact source equivalence to the running binary.

## Why diagnostic reports were left unopened

Only `/proc/adsprpc` entry names were inventoried: four client-named entries
and `global`. Names encode the creating thread's truncated name/TID and can
collide; their presence records an initialization attempt, not a successful
remote binding. No per-client or global report was read.

In the pinned source, per-client report generation dereferences `fl->sctx`
without a null check, although proc publication precedes domain validation and
session allocation. It traverses lists under locks even after its 3,072-byte
output buffer fills. An additional `inode->i_private` versus proc PDE-data
accessor inconsistency means the intended client report cannot be assumed to
select its client branch. These are source findings, not observed running-kernel
failures. The grouped FastRPC sysfs counters also acquire mapping/DMA-buffer
locks and do not identify remote ownership.

CRM `sessions_info` lacks PID/stream ownership, uses a shared small buffer and
blocking lock, and its dense-link iteration conflicts with sparse link removal
in the inspected source. It was not added as a live ownership probe. Reading
these reports would not supply the missing checked stop/release protocol.

All source references use kernel commit
[`6bde5fc06b252c97761bc0963dd13c1abcf377ab`](https://github.com/facebookincubator/oculus-linux-kernel/tree/6bde5fc06b252c97761bc0963dd13c1abcf377ab):
[FD metadata](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/proc/fd.c#L20),
[FastRPC reports](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4116),
[publication/session initialization](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4538),
[CRM report](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_debug.c#L53),
[proc memory reads](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/proc/base.c#L777),
[COW behavior](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/gup.c#L72), and
[VMA flags](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/proc/task_mmu.c#L678).
Critical source-member hashes were independently checked against the private
archive. Public source and the local build configuration do not prove exact
running-kernel implementation equivalence.

## Remaining ownership boundary

The candidate's sampled optional delegate is empty; its value supplies no release target.
The next required evidence is an owned-client observation boundary that records
successful camera session/link/device creation and remote FastRPC binding,
retains those handles, and checks their corresponding stop/release results.
It must identify the actual producer/client instance and prevent new work
before drain; descriptor counts and state publication cannot replace this.
Installing observation hooks or invoking those operations was not part of this
phase. Any such implementation needs review against the exact userspace ABI,
with bounded completion and recovery behavior before a live stop rehearsal.

All four capture groups agreed on boot identity. The owner scan and descriptor
follow-up agreed on CPUs `0-7`, 20 stock module names and taint `5124`; the memory
observation's own before/after basic-health checks also agreed. No service was
stopped/signalled, no Binder transaction or
device ioctl was issued, and no explicit module, DSP reference or hardware shutdown
operation was performed. There was no reboot. These basic-health observations
do not constitute a new subjective Home/tracking test. The native loader,
hardware/DMA/recovery gates and payload remain unchanged.

## Verification

The private locator's frame/type/bounds/VMA refusal checks and Python syntax
checks passed before its one live attempt. Independent review checked the
memory-read scope and refusal paths before execution. Descriptor receipts were
rehashed after collection. The public change is documentation only; all **246
existing host regression tests passed** using the configured host compiler.
Those tests do not establish hardware shutdown. Raw captures, process/device
identifiers, live pointers and proprietary binaries remain outside the repo.

## Matched userspace ownership decoders

`tools/quest2_csl_ownership.py` now decodes the matched camera library's inline
session, acquisition, sparse link and physical-device tables through a supplied
read callback. It checks full session/acquisition handles, slot identities,
creating process/thread fields, link membership and referenced physical FDs.
It does not attach to a process, open a device or perform a release. The caller
must verify the library hash, process identity, mappings, budgets, paired
stability and descriptor metadata. Eleven focused tests cover valid sparse
attribution and refusal paths. Independent review checked the shipped ABI and
zeroed initialization/release records against the decoder.

One private page-based observation subsequently refused at its fixed 1 MiB
allowance before a complete pair. All issued memory commands returned before
that host-side refusal. It produced no complete ownership result and was not
retried or given a larger allowance. Post-refusal identity, boot, CPU, module
names and taint checks agreed with the earlier CSL artifact capture; no new
subjective Home/tracking test is claimed. A more efficient scalar transport is
being developed offline, with the same aggregate byte limit.

`tools/native/quest2_csl_read.c` implements that transport as a small standalone
ARM64 executable. It validates the complete binary request plan and trailing
EOF before opening `/proc/PID/mem` read-only. Each field is at most 512 bytes,
all addresses must lie within the matched inline CSL object, and one plan is
limited to 16,384 fields and 1 MiB. The host must separately enforce the shared
allowance across views, verify mappings and hash the deployed executable.
There is no target pointer traversal, device ioctl or write to the observed
process. Fifteen host tests execute the production C parser and syscall paths
with injected syscall doubles. A static ARM64 cross-build and ELF inspection
passed; these results alone do not establish Android execution.

The manual owner-only `Quest 2 sparse reader Linux QEMU check` workflow builds
and runs the executable against its own CI process's known memory. It also
checks that malformed plans exit before opening process memory. Raw emulator
traces stay in memory; published evidence contains only reduced counts and
statuses. Its execution result is recorded separately from the host model
tests. A binary-safe duplex ADB transport with shell-v2 EOF support is required
for a later headset collector; `exec-out` cannot deliver the input plan.

`tools/quest2_fastrpc_ownership.py` separately decodes the matched FastRPC
library's 16 domain lists and legacy remote-handle list. It preserves raw
tagged handles, normalizes addresses only for reads/structural checks, and
rejects busy domains, malformed lists, repeated/overlapping nodes and an
exhausted aggregate node cap. Thirteen focused tests and independent review
passed. The separate local `mod_table_open` lists, registered buffers and other
FastRPC resources are outside this remote-handle inventory. No live FastRPC
memory observation has been performed for this decoder.

Both modules are offline interpretation tools. A matched userspace table is
still not an atomic kernel session inventory. Empty tables, negative FDs and
stream flags do not prove successful release or DMA quiescence. The optional
delegate's lifetime and a checked producer-stop/drain/release protocol remain
unproven. All hardware and handoff gates stay closed.
