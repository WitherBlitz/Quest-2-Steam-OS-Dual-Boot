# Quest 2 RAM placement and DMA shutdown strategy

Scope: Hollywood, Horizon `52242990024200150`, stock kernel
`4.19.325-cip128-st12-g49638c7a8637`. This is a staged development strategy,
not boot support. The original live fixed-window claim refused with `-EBUSY`.
Neither that result nor a free-page snapshot identifies which pages prevented
allocation.

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
   secure/DSP allocation limits exclude the lower address space. No candidate
   has been tested on the headset in this phase. Capture age and firmware-owned
   memory not represented by DT/resources remain limitations.
4. `validate_ram=1` optionally accepts `ram_base` for one selected window.
   Zero retains the original base. The module checks overflow, native isolation
   alignment, Hollywood identity, System RAM coverage, mapped/nonreserved PFNs,
   one zone and ordinary MOVABLE pageblocks before `alloc_contig_range`.
   CMA remains rejected. A successful claim is immediately released using its
   exact original PFNs; a refusal never frees unowned pages. No scan, fallback,
   retry, payload open or shutdown occurs. Allocation may migrate/reclaim pages
   and has no hard real-time deadline.
5. `ram_base` is rejected without `validate_ram`. It **does not relocate** the
   payload, initrd, DTB, staging exclusion or physical entry. Those still use the
   old fixed layout and must be ported together after a viable reservation is
   observed. An accepted diagnostic address cannot be used as a boot address
   merely by changing this parameter.

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

The new regressions exercise actual C claim/release and initialization/execute
control flow, including an experimental-build harness, and real offline planner
inputs. Host doubles do not validate kernel layouts, allocator acceptance or
hardware shutdown. A new matching native build and ABI audit are required before
any later on-headset diagnostic.
