# Quest 2 camera/DSP ownership and stop contracts

This audit covers Hollywood build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It adds passive FD-holder evidence and a
proposed checked shutdown sequence. **It does not implement or validate hardware
shutdown, DMA quiescence, watchdog recovery or Linux handoff.** The loader gate
remains closed.

## Passive collector

The subsequent [service lifecycle audit](QUEST2_SERVICE_LIFECYCLE.md) maps the
identified holders to startup definitions and compiled cleanup paths. It
distinguishes init/signal/process completion from producer release; the
shutdown proposal below remains gated on that missing proof.

```sh
python3 -B tools/quest2_owner_probe.py --adb /path/to/adb \
  --serial YOUR_USB_SERIAL --output /path/to/new-private-owner-capture
```

Use an existing root session and a new private directory outside the checkout.
Do not publish the capture directory: it contains process names, PIDs and boot
identity. `scan.txt` retains filtered raw metadata for diagnosis and replay;
`health.json` records the successful final identity/health comparison even if
the subsequent ownership parser refuses the transcript. Bounded `transport.txt`
also retains the remote status/footer when remote completion is rejected.
Success exits **3**, prints `OWNER_CAPTURE_COMPLETE; HANDOFF_BLOCKED`, and does
not authorize any later device operation. An error exits 2 and leaves an
`INCOMPLETE.json` marker. There is no automatic retry or background task.

The collector checks the exact device/build/kernel before root, refuses resident
project modules, then reads `/proc/<pid>/stat` and FD symlink metadata. It never
opens a target device FD or sends an ioctl. Only matching camera/media,
observed-subsystem and source-defined FastRPC paths are emitted by the remote
scan; other FD targets are discarded before transfer. Full process stat rows are
retained privately in `scan.txt`; the report saves matching holders and gaps.

The remote scan is capped at 2,048 processes and 16,384 FD symlinks per process.
The host ADB subprocess has a 45-second deadline; a timeout leaves remote
completion unknown. Listings stream through the filter, including an explicit status
from `ls`; they are never passed as a large argument to an external program.
The host refuses more than 131,072 counted FD symlinks or 2 MiB returned data.
Host limits are checked after buffered subprocess output and are not peak
memory limits. Other fixed reads have 15-second deadlines. Identity, boot, online
CPUs, module names and taint must agree before/after. Camera/DSP sysfs names are
also recorded.

Process disappearance, unreadable directories, changed start time/name and
partial listings become explicit coverage gaps. Matching FDs from such processes
are counted as unattributed. A stable PID/start time does **not** pin an FD or
make the whole scan atomic. The scan cannot exclude newly created processes,
short-lived FDs, alternate paths, other device nodes or kernel-only clients.
`scan_coverage_complete` describes the scanned directory samples only.

## Additional kernel-client observations, 2026-10-23

A fresh passive FD scan completed on the matching rooted headset: 739 sampled
processes, 9,917 FD symlinks and five matching holders. Seven process/listing gaps
remained, so `scan_coverage_complete=false`. Identity, boot, online CPUs, modules
and taint were unchanged. These samples do not close ownership.

After the separate ordinary button reboot, metadata-only inspection of
`/sys/devices/virtual/fastrpc/fastrpc/proc` found four TGID candidates. The listing,
each sampled node inode and each process start/name were stable across paired
observations. No FastRPC device, accounting attribute, process-memory interface
or ioctl was opened. The FD and kernel-client captures are from different boots;
their process identities must not be joined. Raw identifiers remain private.

Pinned `drivers/char/adsprpc.c` creates these nodes from `current->tgid` and shares
a node among file contexts with that TGID. This narrows attribution but supplies
no domain, complete map inventory, producer fence or release acknowledgement.
Its accounting show function walks live maps under locks and takes/drops file
references; it is not an inert scalar read. Accounting attributes were left unread.
The [source verifier](../tools/quest2_shutdown_source.py) now checks this file's
exact SHA-256, `f479739662bd427b577e493b518240f3c1ec0b7eb0b1cdf0f3da769efea6d49b`.
Public source matching remains separate from stock binary equivalence.
The [bounded kernel return observer](QUEST2_RELEASE_OBSERVATION.md) is a separate
instrumentation diagnostic; its return samples cannot close these ownership gaps.

## Driver ownership implementation boundary

The subsequent source and runtime feasibility check found no existing complete
ownership transaction to bind to the project's module. On the matching rooted
headset, `CONFIG_MSM_ADSPRPC=y` and `CONFIG_SPECTRA_CAMERA=y` build these drivers
into Horizon. Its reported boot state is locked; `CONFIG_KEXEC` is disabled and
`/sys/kernel/livepatch` is absent. No exported FastRPC ownership interface was
found. These observations establish the current implementation boundary, not
that every possible custom kernel or volatile patching route is impossible.

The missing facility is **driver-owned admission and lifetime control**, rather
than another inventory or a new coordinator assertion. A driver-wide transaction
could account for globally owned resources even where the creator is unknown;
creator attribution alone is not a prerequisite for all global shutdown designs.
It must cover the following demonstrated entry and lifetime paths:

| Driver boundary | Required implementation |
| --- | --- |
| FastRPC files and global heaps | Account for `gfa.drivers`, each file's maps/buffers/contexts, channel context tables, SMMU sessions, and **`gfa.maps`**. [Heap maps deliberately clear `map->fl`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L1166), so walking only per-file maps misses a resource class. Assign transaction-stable identities inside the owning driver. |
| FastRPC admission and final close | Atomically fence new opens/ioctls with active-operation accounting; include final release, accounting reads and RPMsg/restart/PDR lifetime changes. [The existing `file_close` test](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4887) releases its lock before executing the operation and does not revoke already-admitted work. Receive/completion handling must remain available while draining. |
| FastRPC retained references | Provide driver-owned transaction lifetime and deferred final closes. [The private get/put pair](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L1862) can make the observer's last put run hardware/resource destruction; exporting that pair would not make observation inert or ownership complete. Preserve mappings and contexts if a remote completion times out. |
| Camera userspace gateways | Cover [CRM root ioctl](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L306), generic and [fastpath subdev operations](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_core/cam_subdev_fastpath.c#L25), individual sensor/CSIPHY ioctls and their close/power paths. The existing CRM rwsem does not cover all of these entry points. |
| Camera kernel clients and callbacks | Fence exported [camera-memory acquisition](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_mem_mgr.c#L1186) and reserve APIs as well as new CRM callback work. Own and synchronize timers, workers and IRQ completion before retiring requests or buffers. A userspace ioctl fence alone does not fence these producers. |
| DSP kernel loaders | Transfer the ADSP loader's retained handle and [scheduled boot work](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L162), plus the independent [CDSP loader handle](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/qdsp6v2/cdsp-loader.c#L69), under their own lifecycle control. FastRPC ownership alone cannot transfer those references. |

Checked release must also retain the original stream-stop, unlink, hardware
close, mapping-unmap and secure-assignment results before existing cleanup
discards them. The error-loss paths below remain unresolved. A blocking global
writer lock around remote invocations is insufficient: those invocations can
wait indefinitely, so the writer cannot establish a bounded acquisition.

Implementing this contract requires changes inside the owning Horizon drivers,
followed by a verified way to run and test those changed drivers. Building a
separate target Linux kernel does not replace Horizon's built-in drivers. No
such deployment path or complete cooperative driver implementation has been
validated here. **Full camera/DSP ownership remains incomplete; no new ownership,
shutdown or handoff capability was enabled by this feasibility check.**

## What ownership evidence means

| Evidence | Supported interpretation | Limit |
| --- | --- | --- |
| `/dev/videoN`, `/dev/v4l-subdevN`, `/dev/mediaN` FD pathname | A sampled process holds a descriptor whose symlink names that path; compare the numbered node with the sysfs name inventory | No session creator, stream state or unique open-file identity |
| `/dev/subsys_<name>` FD pathname | A candidate userspace subsystem client: normal driver open acquires a reference and final file release puts it | `O_PATH` can name the node without driver open; duplicated, inherited or transferred FDs can share one file; pathname is not inode/major:minor verification |
| `/dev/adsprpc-smd[-secure]` FD pathname | FastRPC client candidate | Channel/domain is selected later; this name cannot identify ADSP versus CDSP or count subsystem references |
| Subsystem `refs=1`, `ONLINE` from the earlier diagnostic | Advisory subsystem state at that sample | No individual owner identity, stop acknowledgement or DMA proof |
| No matching FD | No match in the sampled paths/processes | Kernel clients, missing access, transient FDs and different interfaces remain possible |

The pinned CRM CREATE ioctl receives `file`/`fh` but passes only session data to
the global session allocator. Its session and handle structures contain no
PID/TGID/file owner, and lookup checks handle type/state/value without caller
identity. The root camera open is exclusive in this source, yet one open file
can have several holders and create several sessions. The earlier five-session
sample therefore cannot be assigned to five processes. Zero linked CRM slots
also cannot establish that all tracking/camera producers are idle.

The node filter comes from CRM's dynamically numbered
[`cam-req-mgr` registration](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L614),
its V4L2 subdevices, the
[`subsys_<desc->name>` creation rule](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsystem_restart.c#L1456)
applied to the eight earlier observed names, and FastRPC's
[`adsprpc-smd` / `adsprpc-smd-secure` definitions](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc_shared.h#L31)
and [device creation](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L5836).
FastRPC initially sets `cid=-1`; its
[later domain selection](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4589)
is not visible to this collector. Other channel labels in that source are not
additional character devices. The filter is a scoped candidate inventory, not
proof that every listed node exists or every producer uses one.

Kernel ownership also matters: the
[ADSP loader](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L128)
and [CDSP loader](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/qdsp6v2/cdsp-loader.c#L69)
retain subsystem handles and schedule boot work. Their sysfs unload stores call
void unload functions and return the input count, so a successful write would
not certify shutdown. APR contains another acquisition candidate, but no caller
was found in the audited public source; it is not identified as a live owner.

## Source-backed stop/completion limits

The source archive is pinned at
[`6bde5fc06b252c97761bc0963dd13c1abcf377ab`](https://github.com/facebookincubator/oculus-linux-kernel/tree/6bde5fc06b252c97761bc0963dd13c1abcf377ab),
204,122,036 bytes, SHA-256
`12b987d5672ffc09fe6d3ff7d7d926074305484fc896141824b5ece15d620bca`.
These are source contracts. They do not establish stock binary equivalence for
calling private functions; the previous diagnostic's fingerprint/BTF checks
cover only its observational fields and anchor.

| Operation | What the pinned implementation actually checks | Missing proof |
| --- | --- | --- |
| CRM create/lookup | Global handle/session allocation and type/state/value checks | Per-session process ownership: [CREATE](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L306), [session fields](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.h#L411), [lookup](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_util.c#L279) |
| CRM flush | Waits up to 1,000 ms and returns the raw wait result: zero on timeout, positive remaining jiffies on completion; zero links can return zero without a flush | Enqueue error is overwritten; device errors are overwritten/discarded and completion is not aggregate hardware success: [wait](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.c#L3955), [worker](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.c#L2205) |
| ISP STOP / link deactivate | Changes bookkeeping, cancels requests and signals remaining fences as errors | Normal ISP path ignores `hw_stop` result and returns initialized zero; link deactivate ignores device-event returns: [ISP](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_isp/cam_isp_context.c#L6123), [deactivate](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.c#L4060) |
| Sensor STOP / RELEASE | STOP requires START; RELEASE rejects START and linked sensors, then checks power-down | STOP can log stream-off I2C failure yet change state; state change alone is insufficient: [release](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_sensor_module/cam_sensor/cam_sensor_core.c#L1095), [stop](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_sensor_module/cam_sensor/cam_sensor_core.c#L1187) |
| CRM final close/unlink | Teardown, workqueue destruction, handle and memory cleanup | Earlier lock bug and discarded subdevice/disconnect/unmap errors prevent treating close as a checked drain; see [existing validation](QUEST2_SHUTDOWN_VALIDATION.md) |
| `/dev/subsys_*` final file release | Calls `subsystem_put`; only the last shared reference takes the stop path | No return status from `put`; never put another client's reference: [open/release](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsystem_restart.c#L1383) |
| `subsys_stop` | Attempts sysmon shutdown, invokes descriptor shutdown, then marks OFFLINE and disables IRQs | Sysmon failure is logged, descriptor return discarded; OFFLINE/refcount zero is not shutdown proof: [stop](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsystem_restart.c#L874) |
| PIL/PAS shutdown | Force-stop may wait on an IRQ acknowledgement; secure PAS call can return an error | Normal put uses `force_stop=false`; forced timeout warns and continues; PIL shutdown errors do not propagate to file close: [PIL](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsys-pil-tz.c#L835) |
| FastRPC release | Requests release of a remote process group, then frees local mappings/resources | Kernel invocation/restarting-channel waits can have no deadline; release results and selected secure-memory assignment results are ignored: [remote release](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L3240), [ignored result](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/char/adsprpc.c#L4037) |

Sysmon has separate QMI transaction and SHUTDOWN_READY acknowledgement stages:
a 500 ms transaction wait and a 10 s acknowledgement wait in this source. The
[acknowledgement can override a transaction timeout](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/sysmon-qmi.c#L416).
The optional PIL force-stop IRQ wait is 1,000 ms and only warns on timeout.
The secure PAS shutdown can fail, while the void
[PIL teardown](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/peripheral-loader.c#L1422)
retains failure in a field instead of propagating it to close. FastRPC secure
memory reassignment and context-bank/DMA cleanup need separate validation.

Selected source-member SHA-256 values, independently read from the pinned archive:

| File | SHA-256 |
| --- | --- |
| `techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.c` | `69c4c9b6de55118e735f53ef049f1d8903d7974994523ca5051755e4626215e1` |
| `techpack/camera/drivers/cam_req_mgr/cam_req_mgr_core.h` | `418e4cacf6ed26d1196085bbf061ebaedb9f6e7d7131475082b67781a7af3739` |
| `techpack/camera/drivers/cam_req_mgr/cam_req_mgr_util.c` | `1ee402aff84a4b92ab45719678fee0ac1cb1196db8927293fec594a970cedc93` |
| `techpack/camera/drivers/cam_isp/cam_isp_context.c` | `5e89d8baf21812f8018e434936d0fe35e31932224ba7d82b06515c78f6aa5075` |
| `drivers/soc/qcom/subsystem_restart.c` | `2c9b172257a4fef531da6595ff7c42c943bb5cea0c8859975dc7340fda78e455` |
| `drivers/soc/qcom/subsys-pil-tz.c` | `cfedc014cccf32e746c74bbe017074f375b179074e403126647ce8cf05118bf5` |
| `drivers/soc/qcom/peripheral-loader.c` | `4eb33dcd873f7b1089537ecdbf4eac3acc870c06a596756a87e7937875118be4` |
| `drivers/soc/qcom/sysmon-qmi.c` | `3260f87fae280cd131a6c1f2e538ea3bd2768116bd832196d2e701089ec73430` |
| `drivers/char/adsprpc.c` | `f479739662bd427b577e493b518240f3c1ec0b7eb0b1cdf0f3da769efea6d49b` |
| `drivers/char/adsprpc_shared.h` | `d586c6c648bb6e59f42503b3a4c8c5b5196e87a988f7d2cdb76f9d45b6278ae0` |

In particular, ioctl success, a signalled fence, an empty software queue,
workqueue destruction, an OFFLINE label and zero reference count each cover
different boundaries. None independently proves that a bus master has stopped
or that secure memory ownership was returned.

## Proposed checked stop/drain sequence — not executable yet

1. Establish externally observable recovery and a deadline that survives the
   shutdown boundary. Preserve diagnostics and verify return to Horizon before
   testing an irreversible part of teardown. Current software watchdog reads do
   not satisfy this prerequisite.
2. Identify the actual producer and owned client for every camera, tracking,
   FastRPC/DSP and other DMA path. Include kernel-held references/dependencies.
   Block new submissions through the owning service's supported lifecycle;
   there is no verified Horizon service-stop API in this audit.
3. Stop producer-specific streams while their clocks, interconnect and DMA
   translations still work. Record each callback result, expected hardware
   acknowledgement, timeout and fence outcome. Error fences are cancellation
   evidence, not successful completion. Resolve the source error-loss paths
   above before relying on them.
4. Drain owned requests and workers, accounting for queued IRQ, delayed and
   restart/powerup work. Use a bounded, checked completion for each producer.
   Stop on any missing owner, ambiguous result or timeout; keep resources held
   for recovery rather than proceeding to teardown.
5. Release only owned subsystem/client references after their producer drain.
   Correlate sysmon, stop IRQ, PIL/PAS result and secure ownership explicitly;
   never decrement shared counts until they happen to reach zero.
6. After all producers are proven stopped, perform and verify the matching
   IOMMU invalidation/detach and secure handoff protocols. Preserve required
   mappings until their final consumer has acknowledged completion. Quiet IRQ
   counts or sysfs group membership are insufficient.
7. Only then retire clocks/interconnect/interrupt paths and proceed toward the
   separately validated CPU/MMU and target-kernel entry. Retain the allocated
   payload reservation through this boundary. Any earlier failure aborts entry.

This is an ordering proposal, not a universal driver recipe. Supported owners,
binary agreement, producer-specific hardware acknowledgements, complete DMA
coverage and recovery are still required before a live shutdown rehearsal.

## Verification

### Matching-headset result

The final bounded capture completed on 2026-10-22 America/Denver: **705 processes,
21,814 FD symlinks, five matching process holders and 19 matching descriptors**.
Five process races were recorded (four changed/exited processes and one missing
initial stat); no matching descriptors were assigned to those processes. The
capture is complete as a bounded observation, while `scan_coverage_complete`
remains **false**. It is not an exhaustive ownership census.

Fixed follow-up `stat` / executable-symlink / `stat` reads matched each holder's
captured PID, start time and command name, resolving ambiguous Binder names:

| Executable holder | Sampled matching paths |
| --- | --- |
| `/odm/bin/hw/vendor.oculus.hardware.sensors@1.0-service` | 15 camera/media descriptors: `video0`, `video1`, and `v4l-subdev0` through `v4l-subdev12` |
| `/odm/bin/trackingservice` | One `adsprpc-smd-secure` descriptor |
| `/vendor/bin/adsprpcd` (two processes) | One `adsprpc-smd-secure` descriptor each |
| `/vendor/bin/cdsprpcd` | One `adsprpc-smd-secure` descriptor |

No selected `/dev/subsys_*` pathname matched. This does not exclude kernel-held
references or other userspace interfaces. The FastRPC paths cannot assign a DSP
domain, and none of these observations assigns an individual CRM session.

Exact identity/root, boot, CPUs 0-7, 20 stock module names and taint 5124 agreed
before/after the successful scan. The filtered scan was 288,910 bytes. Raw
process/transport data, PIDs, boot identity and executable correlation remain
private. No target-device open/ioctl, service stop, DSP get/put, module load,
tracing change, PM/MMIO/watchdog write or reboot occurred. No diagnostic job was
installed. These checks do not claim a new subjective Home/tracking test.

All **246 host tests passed**, including 16 ownership-probe tests. Independent
review reproduced and checked the shell race, merged-error, large-argument,
listing-status and cap boundaries. Python AST, embedded shell syntax and
`git diff --check` passed; all ten documented source-member hashes matched the
pinned archive. No native module rebuild was needed for this host-tool phase.

The next implementation boundary is the sensors service's camera lifecycle and
tracking service's FastRPC lifecycle, with checked producer-specific stop and
completion results. Their executable identities do not establish a supported
Horizon service-stop/drain protocol. That protocol, kernel-held references,
secure ownership, DMA quiescence and recovery remain unresolved.

### Reproducing host checks

Host tests cover stable/reused/exited PIDs, partial/inaccessible listings,
duplicate descriptors, malformed/truncated protocol, unsupported targets,
limits, remote failure/timeout, identity/module refusal and changed boot. A
real POSIX-shell fixture exercises the listing filter and its failure path.
No native module or target payload changes are part of this phase.

```sh
python3 -B -m unittest discover -s tools/tests -p 'test_owner_probe.py' -v
python3 -B -m unittest discover -s tools/tests -p 'test_*.py'
git diff --check
```

Python 3.11+ is required; the broader suite also requires a native GCC/Clang C
compiler (`HOST_TEST_CC` if necessary). No dependencies are added. There is no
configured Python linter; Python AST/compile checks and embedded-shell syntax
checks supplement the tests.

Android integration initially exposed merged shell stderr on process exit.
The first scan was refused without a report; an instrumented reproduction saved
the malformed metadata and confirmed stable basic health. The failing shell
input-open diagnostic appeared before its explicit process-gap record. A
merged stdout/stderr host fixture reproduced this failure; placing the stderr
redirect before each stat input redirect fixed the emitter. The parser remains
strict. Those two refused attempts remain private and are not successful
ownership captures.

A later scan exposed Android's external `printf` argument-size limit on a
large FD directory. A synthetic large-list fixture reproduced the failure with
an enforced per-argument limit. The emitter now streams each listing directly
through the filter and preserves its exit status. Protocol `QOWN2` validates the
final process count; the host totals and bounds the individual FD-count records.
The large-list fixture passes without increasing the output or time limits.

The first streamed attempts reached the deliberate 8,192-FD per-process cap;
retained transport evidence and a separate stable-PID metadata count found
12,280 descriptor-directory entries. The per-process cap was therefore raised
to 16,384. The 131,072 aggregate-FD, 2 MiB output and 45 s host transport limits
remain unchanged. An additional reference-derived spelling check uses
`a650_zap` for that subsystem's candidate node.
