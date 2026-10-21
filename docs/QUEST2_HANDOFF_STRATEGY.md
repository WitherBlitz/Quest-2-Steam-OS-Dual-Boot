# Quest 2 RAM placement and DMA shutdown strategy

Scope: Hollywood, Horizon `52242990024200150`, stock kernel
`4.19.325-cip128-st12-g49638c7a8637`. This is a staged development strategy,
not boot support. Separate live claims refused the original fixed window and
the first planned high window with `-EBUSY`. Neither result nor a free-page
snapshot identifies which pages prevented allocation.

## RAM ownership

The stock kernel is already running when the loader starts. Adding a target DT
reservation or calling an early-boot memblock helper at that point cannot take
ownership away from Android. Persistent early reservation would require a
supported change to the stock boot chain, which this branch has not established.

The implemented path is conservative snapshot planning followed by a separate
native allocator diagnostic:

1. `tools/quest2_handoff_plan.py` requires a complete, matching rooted readiness
   capture and verifies its runtime-tree/iomem lengths and SHA-256 digests.
   It reads the archive without extracting it and writes only a new host report.
2. Candidate windows must fit a single captured System RAM resource. All
   overlapping non-RAM resources, kernel code/data and DT reserved ranges are
   excluded, including disabled reserved-memory nodes. An unresolved dynamic
   reservation excludes its entire `alloc-ranges`, not merely its `size`.
   A dynamic reservation without bounded allocation limits blocks generation.
   Unsupported address translation, malformed ranges and unresolved DT references
   fail closed.
3. The tool emits at most eight nonoverlapping 52 MiB windows aligned to 4 MiB.
   These are **unclaimed diagnostic candidates**, not free RAM or boot layouts.
   In the saved capture, the first candidate is above 4 GiB because the dynamic
   secure/DSP allocation limits exclude the lower address space. A fresh capture
   and one subsequent diagnostic at `[0x100000000, 0x103400000)` returned `-EBUSY`,
   `owned=0`, in 3,657,553 ns. The other seven candidates remain untested. See the
   [live result and cleanup evidence](QUEST2_LOADER_STATUS.md#selected-high-ram-candidate-on-headset-result).
   One later instrumented attempt on that same window identified an initial
   pageblock-isolation refusal and an unmovable block at
   `[0x102c00000, 0x103000000)`. It also acquired no RAM. The exact busy page and
   owner remain unknown; the [instrumented evidence](QUEST2_LOADER_STATUS.md#verified-build-and-instrumented-result)
   supports accounting for observed unmovable blocks before another placement
   design, rather than repeating this layout unchanged.
   Capture age and firmware-owned memory absent from DT/resources remain limits.
4. `validate_ram=1` optionally accepts `ram_base` and `ram_bytes` for one selected
   window. Zero retains the original base and 52 MiB length respectively.
   Lengths are bounded to 64 MiB. The module checks overflow, native isolation
   alignment, Hollywood identity, System RAM coverage, mapped/nonreserved PFNs,
   one zone and ordinary MOVABLE pageblocks before `alloc_contig_range`.
   CMA remains rejected. A successful claim is immediately released using its
   exact original PFNs; a refusal never frees unowned pages. No scan, fallback,
   retry, payload open or shutdown occurs. Allocation may migrate/reclaim pages
   and has no hard real-time deadline.
5. `ram_base` and `ram_bytes` are rejected without `validate_ram`. They **do not
   relocate** payloads and cannot combine with `payload_base`. An accepted
   diagnostic range does not become a boot layout by changing these parameters.
   Compact staging has its own opt-in contract below.
6. Optional `ram_diagnose=1` adds task/range-filtered return observations around
   the same single claim. All required probes must register before allocation;
   partial failure unwinds, and all observers are removed before init returns.
   It reports native stages and a first unmovable block, never an exact owner.
   Missed events and probe allocation/timing limit interpretation. See the
   [diagnostic scope and verification](QUEST2_LOADER_STATUS.md#opt-in-allocator-refusal-observation).

Offline use:

```sh
python3 -B tools/quest2_handoff_plan.py --capture /path/to/private-capture \
  --output /path/to/new-host-report.json
```

Successful planning intentionally exits 3 and prints `HANDOFF_BLOCKED`, matching
the readiness collector's distinction between complete evidence and boot safety.
There is no deployment or module-loading command in this tool.

The [matching ARM64 boot contract](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/Documentation/arm64/booting.txt#L125)
permits a compatible Image at `text_offset` from a 2 MiB aligned usable RAM
base, but also requires its effective image size, initrd constraints and all DMA
quiescence requirements to be satisfied. The target DT must preserve every
remaining secure/dynamic reservation; protecting the copy window alone does
not protect the rest of the target allocator's memory.

## Observed-block exclusions and compact copy proposals

The offline planner accepts repeatable `--exclude-block START:END` arguments.
These are half-open, 4 MiB aligned ranges inside one captured System RAM
resource. They join all existing exclusions for both diagnostic and compact
candidates. The report labels them caller-supplied observations that may be
stale; they neither reserve RAM nor establish a current page owner.

Optional `--kernel-image`, `--initramfs` and `--dtb` inputs must be supplied
together. Each file is bounded to 64 MiB. The tool requires a raw uncompressed
ARM64 Image with a nonzero declared memory size, supported little-endian/4 KiB
flags and placement freedom, a nonempty initramfs, and one exact-size DTB of at
most 2 MiB. It records lengths and SHA-256 digests. This is header/sizing
inspection, not validation of the initramfs or target DT contents.

The separate `payload` report sizes kernel memory from `text_offset + image_size`,
then page-aligns the initramfs and DTB and rounds the copy envelope to 4 MiB.
Candidates stay within one usable resource after exclusions and satisfy the
ARM64 kernel/initrd 1 GiB aligned window constraint of at most 32 GiB. At most
eight disjoint candidates are emitted. Missing inputs produce no compact
addresses. A smaller compact proposal may fit when no 52 MiB diagnostic extent
does; neither result establishes allocator acceptance.

```sh
python3 -B tools/quest2_handoff_plan.py --capture /path/to/private-capture \
  --exclude-block 0x102c00000:0x103000000 \
  --kernel-image /path/to/Image --initramfs /path/to/initramfs \
  --dtb /path/to/one-target.dtb --output /path/to/new-host-report.json
```

On 2026-10-21, an offline exercise used the saved matching capture and the
observed unmovable block above. The returned rebuilt Android Image was
30,369,808 bytes and declared `0x1dd1000` bytes of kernel memory with
`text_offset=0x80000`. The stock Android ramdisk was 2,676,670 bytes; a first DTB
of 385,801 bytes was extracted from its 1,150,513-byte multi-DTB bundle solely
as a sizing reference. This set is **not a validated target Linux payload or
selected runtime target DT**. It yielded:

| Report | First unclaimed range | Meaning |
| --- | --- | --- |
| Existing 52 MiB diagnostic | `[0x103000000, 0x106400000)` | Avoids the observed block; no allocation attempted. |
| Compact 36 MiB copy proposal | `[0x100000000, 0x102400000)` | Reference sizing fits before that block; requires a loader port. |

The reference ramdisk exceeds the legacy loader format's 2 MiB limit. At this
offline-planning stage the native compact port had not been implemented.
`ram.candidates` still reports the default **52 MiB** diagnostic contract;
it never automatically consumes `payload.candidates`. The report remains `HANDOFF_BLOCKED`,
every candidate is unclaimed, and DMA quiescence remains false. Copy-envelope
sizing does not account for total OS working RAM, source/staging allocations,
target-DT preservation or hardware shutdown. No native rebuild or headset
operation was performed in this host-only phase.

## Native variable-length diagnostic and compact staging

`ram_bytes` selects the RAM-only diagnostic's claim length. Zero retains 52 MiB;
a nonzero value must satisfy the pinned allocator's isolation alignment (4 MiB
on this kernel), be at most 64 MiB and not overflow with the base. Claim,
observation, System RAM coverage and release use that exact selected range.
Allocation refusal never frees unowned memory. This parameter does not open
payload files or change the staging layout.

For Hollywood staging, nonzero `payload_base` selects a separate compact layout
derived from the Image header and file lengths. Kernel memory, page-rounded
initramfs/DTB copies and the 4 MiB rounded ownership envelope must fit within
64 MiB. The legacy layout remains the default. Image and initramfs files are
bounded to 64 MiB each; DTBs remain bounded to 2 MiB. Oversize, unsupported
Image flags/offsets, unaligned bases and overflowing layouts fail before staging.

The selected envelope is used consistently for System RAM coverage, allocator
claim/release, exclusion of source/descriptor/control pages, copy destinations
and final entry/DTB arguments. Compact staging requires a canonical v17 DTB with
one `/chosen` or `/chosen@0` node whose `linux,initrd-start` and `linux,initrd-end`
match the computed addresses. Truncated structures, invalid strings/tokens,
duplicates, properties after child nodes and mismatched initrd values fail.
The validated DTB buffer supplies the staged bytes, avoiding a second file read
that could change the checked addresses.

These checks establish a copy-layout contract only. They do not validate the
target kernel patches, Linux userspace, complete DT reservations, secure owners
or hardware state. The host planner marks envelopes over 64 MiB as still needing
a loader port and all inputs as needing payload validation. The legacy preparer
`tools/prepare.py` still emits the old fixed layout and cannot prepare a compact
DTB. `payload_base` does not rewrite the DTB. No deployment command is provided.
The Hollywood hardware-transition gate remains enforced even in experimental
builds; this work does not enable a jump or a headset allocation attempt.

## DMA shutdown handling

The implemented board gate refuses Hollywood hardware transitions even with
`QKX_EXPERIMENTAL=1`. Initialization rejects PM/CPU/MMU, reset/watchdog and
shutdown actions before symbol resolution or hardware access. `qkx_execute`
also checks the gate before freeze or device operations, even when destination
RAM is owned. Missing model identity is refused. Plain staging, RAM diagnostics
and the inspection-only preflight remain available. Historical non-Hollywood
experimental behavior is retained; it is not certified by this work.

The planner resolves every captured `iommus` and `memory-region` reference into
providers, argument cells and memory nodes. It marks all clients unproven and
never reports DMA quiescence. DT references are not a complete list of DMA
masters, current mappings or secure ownership. The saved capture contains 72
nodes with these references; this is a dependency inventory, not 72 validated
devices.

The required shutdown ordering is:

1. Prevent new submissions from userspace clients, kernel workers, firmware
   IPC and subsystem restart/powerup work. Record recovery state before any
   disruption. A process freezer alone leaves descriptors, clients and firmware
   running; it does not close camera file handles.
2. Stop each DMA producer through its owning driver/subsystem while its clocks,
   interconnect and translation provider remain usable. Obtain a checked
   completion result for outstanding requests and asynchronous work. Callback
   return alone is insufficient for the observed GPU/Syncboss paths.
3. Account for all shared DSP references and remote processors before releasing
   their memory. Do not borrow another client's subsystem handle or decrement
   counts until zero. Releasing one reference does not prove processor shutdown.
4. After all producers are stopped and transactions drained, handle translation
   invalidation, secure assignments and memory ownership through the matching
   provider protocols. Keep SMMU mappings intact until their clients are stopped.
   Unbinding an SMMU first or blindly writing its disable register is not a
   replacement for stopping devices.
5. Require per-device completion, timeout/error reporting and a proven recovery
   path before entering USB shutdown or another irreversible operation. Missing
   support must fail before disruption; an error beyond that boundary must not
   return to Horizon with hardware partially stopped.

Pinned source evidence explains why directly reusing remove callbacks is not
implemented:

| Area | Matching source | Consequence |
| --- | --- | --- |
| Camera | [`cam_req_mgr_close`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L178) tears down core/subdevices only after the final client closes. | Freezing clients or invoking a platform shutdown wrapper does not run this lifecycle. Client and worker ownership need explicit handling. |
| ADSP | [`adsp_loader_remove`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L300) releases one handle; [`subsystem_put`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsystem_restart.c#L1030) stops only at the final reference. | A successful put is not a global shutdown/completion result. SSR also has restart/powerup paths that must be accounted for. |
| SMMU | [`arm_smmu_device_remove`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/iommu/arm-smmu.c#L6228) disables/powers down the provider; its driver has no shutdown hook. | Calling remove directly could destroy translations for active clients. No DMA fence is inferred from IOMMU group membership. |
| Generic shutdown | [`device_shutdown`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/core.c#L3599) invokes registered shutdown callbacks without an aggregate success result. | It cannot serve as an all-device DMA completion check. |

Until those protocols and recovery checks exist, the gate deliberately has no
override parameter. This phase implements planning and refusal handling; it does
not implement camera/DSP teardown, certify a usable RAM reservation, or enable
a Linux jump. No headset change is required to run the offline planner.

## Verification

Run the existing host suite with a configured native compiler:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
git diff --check
```

The full host suite has 136 tests, including 28 offline planner tests and 15
native payload/layout tests. The latter compile actual layout, DTB, staging and
initialization functions against host API doubles, including failed staging
after a successful claim and compact-jump refusal before opening payloads. The
planner regressions cover observed exclusions, declared-memory sizing, compact
and fixed-layout separation, input bounds, holes/reservations, the 32 GiB window
constraint and exclusive report output. No Python formatter/linter is configured;
Python syntax parsing and `git diff --check` supplement the tests.

The native-path regressions exercise actual C claim/release and initialization/execute
control flow, including an experimental-build harness, and real offline planner
inputs. Host doubles do not validate kernel layouts, allocator acceptance or
hardware shutdown. The matching native build and local ABI audit for this source
passed in [archived run 39567877831](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence);
see the [recorded checksums and scope](QUEST2_LOADER_STATUS.md#placement-strategy-and-hollywood-dma-gate).
That build phase was offline. The subsequent one-window diagnostic refused
ownership as recorded above; no shutdown protocol has been tested on hardware.
