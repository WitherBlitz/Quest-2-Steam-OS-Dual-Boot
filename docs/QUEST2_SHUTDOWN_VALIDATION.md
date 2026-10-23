# Quest 2 shutdown and recovery validation

Scope: Hollywood, Horizon `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. The 2026-10-21 America/Denver validation
adds a reproducible public-source audit, executable source models and one
passive headset capture. **Physical shutdown, DMA quiescence and recovery remain
unvalidated.** The loader's Hollywood gate is unchanged.

## Evidence and its limits

The audit uses kernel commit `6bde5fc06b252c97761bc0963dd13c1abcf377ab`.
It checks the exact 204,122,036-byte `kernel-source.tar.gz` from the
[verified startup build](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39714699023),
SHA-256 `12b987d5672ffc09fe6d3ff7d7d926074305484fc896141824b5ece15d620bca`.
The current verifier checks 16 complete source-file hashes and seven exact copied functions;
no archive files are extracted. A different archive is refused, including a
differently packed archive of the same revision. This is public-source evidence,
not proof that the proprietary installed binary implements every branch identically.

The host harnesses compile actual source functions against MMIO, SCM, lock and
subsystem API doubles. They establish the branches and calls below. They do not
execute a secure call, stop hardware, prove a reset, model concurrent clients or
validate private kernel structure layouts. The camera list has one simulated
subdevice; DSP dependency lookup returns no recursive dependency.

| Area | Verified source/model result | Consequence |
| --- | --- | --- |
| Camera close | Core/subdevice teardown starts only when the final client closes. The nonfinal branch returns while retaining the source's write lock. The final branch returns zero even when the simulated subdevice close returns an error. | A generic client-close call cannot be accepted as a checked stop-and-drain protocol. The retained lock is an observation of published source, not a demonstrated reachable stock-device defect. No client was closed on the headset. |
| DSP reference release | With two references, `subsystem_put` decrements to one without stop or memory release. At the final reference it requests stop and memory release; invalid/zero references do neither. | Releasing the ADSP loader's single handle cannot establish global DSP shutdown. The model does not establish whether stop callbacks succeed. |
| SMMU removal | The pinned remove function logs active normal/secure contexts, then continues to disable and power down the provider. | Removing translations before stopping producers is not a DMA fence. This path was inspected, not executed or certified by a hardware model. |
| Watchdog disable | The supported driver request is one SCM value argument equal to one. A nonzero `scm_call2` return aborts before application-watchdog teardown. Success removes the application IRQ, panic notifier and pet timer. An already-disabled software flag skips SCM entirely. | Availability, callback success or an already-disabled flag does not prove secure ownership. The setter accepts only disable=1 and supplies no matching re-enable operation. |
| Watchdog status | The getter reports the inverse of the software `enable` flag. | It is a software observation, not physical enable, secure-watchdog or reset proof. |
| Panic callback | Timeout zero disables the application watchdog. Nonzero timeout adjusts bark/bite and pets it, but does not enable an already-disabled watchdog. | The ordinary callback cannot establish recovery after watchdog teardown. |
| Full panic/reset | `kernel/panic.c` requests emergency restart for a nonzero timeout. `msm-poweroff.c` separately handles conditional force-bite, secure/PMIC actions and PS_HOLD. | A source restart request does not prove completion, timing, retained diagnostics or successful return to Horizon. No panic/reset test was run. |

The watchdog model also demonstrates that the driver does not separately inspect
`desc.ret[]`. Those fields' secure-firmware semantics were not established; this
is not a claim that a nonzero payload is an error or that another SCM signature
is valid. The loader's legacy signature-probing mode remains blocked on Hollywood.

## Passive headset result

The later [watchdog and recovery work](QUEST2_WATCHDOG_RECOVERY.md) adds SCM,
ARM64 restart and pstore source pins. It preserves the distinction between a
reset request, readable retained logs and demonstrated Horizon recovery.

The exact firmware/root identity matched before subsystem reads and again at
completion. All 14 private text artifacts passed their length/hash checks and
remote completion checks. Before/after boot ID, CPUs 0-7, the 20 stock module
names and taint 5124 agreed. No project module was loaded, and the collection
changed no device state.

- The bound application-watchdog driver is `msm_watchdog`; its software
  `disable` read was `0`. Optional `pet_time` was unavailable and remains unknown.
- Current kernel panic timeout was `-1`. The previously captured matching runtime
  config has `CONFIG_QCOM_FORCE_WDOG_BITE_ON_PANIC` disabled. Neither establishes
  a physical reset or a usable recovery deadline.
- ADSP, CDSP, Venus, SPSS, CVPSS, A650 ZAP, WLAN and NPU all reported `ONLINE`.
  These are sampled software states, not reference counts or continuous liveness.
- The capture contains 17 V4L camera/subdevice name entries, including two empty
  names, 49 IOMMU device links and 23 distinct selected driver bindings. The raw
  binding probe emits 30 rows because camera/SMMU globs overlap. This inventory
  does not prove frame activity, mappings, drained requests or all DMA masters.

No service stop, client close, ioctl, suspend, unbind, SCM/MMIO access, watchdog
write, allocation/module test, reset/reboot, tracing change or payload operation
occurred. Raw captures and device identifiers remain private. No new functional
tracking test is claimed by this passive collection.

## Reproducing the checks

Source-only audit, with the exact verified build archive above:

```sh
python3 -B tools/quest2_shutdown_source.py \
  --kernel-source /private/build/kernel-source.tar.gz \
  --output /private/new-source-audit.json
```

Passive collection requires existing authorized USB debugging and root:

```sh
python3 -B tools/quest2_shutdown_probe.py --adb /path/to/adb \
  --serial YOUR_USB_SERIAL --kernel-source /private/build/kernel-source.tar.gz \
  --output /private/new-shutdown-capture
```

Both commands intentionally exit 3 with `HANDOFF_BLOCKED` on complete evidence;
failure exits 2. Outputs must be new. The collector writes `capture.json` only
after final identity/boot/health checks; failures retain an incomplete marker.
Each fixed read has a 15-second timeout and an explicit root-shell completion
footer, so ADB transport success cannot hide a remote failure. Optional absent
sysfs nodes are recorded as `MISSING`, without inventing a hardware result.
The 2 MiB per-read check occurs after subprocess capture; it is not a streaming
memory bound. Snapshot checks do not establish uninterrupted subsystem stability.

Host verification uses Python 3.11+ and the existing native compiler setup:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_shutdown*.py' -v
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -q
git diff --check
```

That passive-only phase had 18 source/model tests and 12 passive-collector tests;
its full suite had 204 tests. Independent read-only review reproduced the source
audit and tests with no remaining actionable findings. That phase changed no
native module/kernel sources and required no replacement module or target Image.

## Required before a disruptive test

### Read-only camera/DSP module

`camera_dsp_diag.ko` is a separate, opt-in observational module. It requires
`observe=1`, the exact kernel release and a Hollywood/Kona board. It never calls
the blocking camera getter: it checks the exported getter's 68 instruction bytes
before using the stock-verified relative addresses of the camera roots. A
mismatch refuses initialization before private state is accessed. There are no
address, control, shutdown or watchdog parameters.

The selected private layouts in `module/diag_profile.json` were derived from
the running kernel's BTF. The generated header must agree exactly. The matching
kernel build separately verifies its BTF and records bounded compiled getter
bytes, size, SHA-256, call targets and relative symbol placement as build evidence.
ELF extraction is cross-checked against that build's raw Image. This checks
selected layouts; it does not establish complete binary equivalence. Live
symbol addresses were masked, so the runtime instruction check remains required;
kernel pointer visibility was not changed.

Camera enumeration holds a device reference and tries the device lock before
checking the bound driver. This excludes removal and root deallocation. It then
tries camera, CRM, link and request locks in source order; busy paths release
acquired locks and report missing observations. CRM ownership pins sessions and
links; link ownership pins workqueues. At most eight sessions and all seven
possibly sparse link slots per session are inspected. The module copies queue
indices and counts, without walking request payloads or camera frames. It never
takes the session mutex, avoiding workers' request-to-session lock order.

DSP enumeration pins each embedded device and copies bounded embedded fields,
without following provider descriptors or restart-group pointers. Reference
counts, public states, crash counts and `work_busy` bits are separate advisory
samples. Group tracking is deliberately unavailable: `embedded_state_advisory`
does not represent a restart group's private state. Work counters can change
during a callback; pending zero does not mean no running work. Output is buffered
until camera locks are released and contains no kernel addresses.

The build includes `diagnostic-profile.json`, `diagnostic.compile.txt` and the
fourth module in its existing checksum/provenance/import-CRC gates. Host tests
execute the actual snapshot and guard functions with API doubles. They cover
busy locks, cleanup, sparse links, read faults, bounded traversal, DSP capacity,
wrong targets and changed code/layouts. The full suite currently has 230 tests.
These tests do not simulate hardware or prove concurrent driver correctness.

```sh
python3 -B tools/quest2_diag_profile.py --btf /private/live-vmlinux.btf \
  --output /private/new-layout-check.json
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -q
```

The verified matching build and live result are recorded below. An observational
snapshot still reports
`handoff_ready=0`: it cannot establish producer drain, DMA quiescence, secure
ownership, watchdog control or recovery.

The profile verifier accepts both executable and dynamically linked kernel ELF
formats. The approved ARM64 `CONFIG_RELOCATABLE` build uses `-shared` and produces
`ET_DYN`; this does not relax its AArch64, symbol, BTF or instruction checks.
Relocatable module objects (`ET_REL`) are rejected as `vmlinux` inputs.

CI uses explicit `--build-vmlinux --built-image` mode: rebuilt code is evidence
for offline assessment, not replacement runtime code or constants. The module's
undefined getter import resolves against the running kernel. Its stock 68-byte
runtime fingerprint and stock-relative constants remain unchanged. Complete
rebuilt getter byte/placement identity is therefore not an ABI requirement.
Default `--vmlinux` mode retains the strict stock byte/placement checks. Selected
compiled/stock/live BTF, header, import CRC and vermagic checks remain hard gates.
Unassessed build evidence is printed before layout validation so a failed check
retains useful diagnostic data. No live load is allowed before its assessment.

### Native build and live result

On 2026-10-22 America/Denver, the
[matching native build](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39730002859)
passed at source `e5e357ca525b4c9f15a6793fb2192a9cedd5c955`. All 230 host tests,
selected BTF/header checks and four-module ABI audits passed. The diagnostic is
147,264 bytes, SHA-256
`84b1326f32036b1988be8b8180e37f7015b0c5f0e54f4dbe0e9b3d2ca81b0664`,
with 24 versioned CRC records. The independently checked build archive has
SHA-256 `ffcc826439ea3a35ff3a097a883830594d49823a626ddec0b0f6c70e1bdecaac`.
It contains no full kernel Image, vmlinux, private config or raw device capture.

Independent code assessment found that the rebuilt getter's 17 instructions
differ from stock only at offsets 12, 16, 28 and 48: address-bearing ADRP/ADD and
two BL instructions. Decoding resolves `g_dev+60` for `open_cnt`, `g_dev+64` for
`cam_lock`, and the expected mutex lock/unlock functions. ELF and raw Image
extractions agree. The compiled hash and placement remain different from stock;
the stock-match flags remain false. These bytes and placements are not loaded
into the headset. The module retains its stock runtime fingerprint/constants.

After artifact, assessment and fresh identity/boot/health gates, exactly one
`observe=1` insertion completed. Its stock runtime fingerprint passed before
private camera reads. Observations were:

- Camera status `sampled`, no read fault or truncation, open count **1**,
  registered subdevices **15**, sessions **5**, linked CRM slots **0**.
  No live request-queue/workqueue path was exercised because no link was present;
  those paths have host harness coverage only.
- All eight subsystem entries (ADSP, CDSP, Venus, SPSS, CVPSS, A650 ZAP, WLAN and
  NPU) sampled reference count **1**, embedded public state **ONLINE**, crash
  count **0**, restart level **1**, no restart group and work/restart-work bits
  **0**. These are individual advisory samples, not owned handles or DMA fences.
- Normal unload completed and the exact temporary module file was removed;
  absence was checked. Boot, root/build identity, CPUs 0-7, 20 stock module names,
  taint 5124, panic/watchdog reads and passive DSP/camera/binding/IOMMU inventories
  agreed before/after. No new matched fault patterns appeared in retained logs.
- The user confirmed that Home and head/controller tracking remained normal.

No client close, DSP handle release, service stop, suspend, unbind, SCM/MMIO,
watchdog write, reboot or Linux payload operation occurred. The diagnostic is no
longer resident; no diagnostic startup job was created. Raw logs and device identifiers stay
private. **Camera/DSP shutdown, DMA quiescence, watchdog recovery and Linux
handoff remain unproven.** No automatic boot or hardware gate was enabled.

### Remaining hardware prerequisites

The follow-up [ownership and stop-contract audit](QUEST2_OWNER_AUDIT.md) records
the passive process/device collector, source-backed attribution limits and the
proposed checked stop/drain order. It does not satisfy the hardware gates below.

1. Establish source/binary agreement for each called private API. Determine camera
   clients, active requests, workers, stream-stop completion and error reporting.
   Resolve the close-path lock/error limitations before relying on that path.
2. Account for DSP clients, dependency references and restart/powerup workers.
   Require an owned stop operation and a checked completion result; do not drain
   other clients' reference counts or borrow their handles.
3. Drain every producer while clocks/interconnect/translations remain available.
   Then validate IOMMU invalidation and secure ownership. Sysfs groups or quiet
   IRQ counters cannot substitute for those protocols.
4. Establish a recovery mechanism that survives the proposed shutdown boundary,
   with an independently observable reset deadline, preserved diagnostics and
   verified Horizon recovery. Keep an external recovery path before testing a
   state in which software rollback is unavailable.

GPU/Syncboss rollback, USB/UFS reinitialization, CPU/MMU entry and the remaining
target startup patches also remain prerequisites. The 36 MiB temporary RAM
success does not satisfy these requirements or retain memory for a future boot.

## Pinned primary sources

- [Camera lifecycle and locking](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L111)
- [ADSP loader removal](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L300)
  and [shared subsystem references](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/subsystem_restart.c#L1035)
- [SMMU removal](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/iommu/arm-smmu.c#L6228)
  and [generic device shutdown](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/core.c#L3599)
- [Watchdog panic and disable callbacks](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/watchdog_v2.c#L221),
  [panic restart request](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/panic.c#L327)
  and [platform restart path](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/power/reset/msm-poweroff.c#L599)
