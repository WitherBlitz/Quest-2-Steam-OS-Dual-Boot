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
Nine complete source-file hashes and seven exact copied functions are checked;
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

There are 18 source/model tests and 12 passive-collector tests; the full suite
has 204 tests. Independent read-only review reproduced the source audit and
tests with no remaining actionable findings. Native module/kernel sources did
not change, so this phase requires no replacement module or target Image.

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
kernel build separately verifies its BTF, camera instruction SHA-256 and static
symbol relationships before publishing the module. This checks selected layout
and code agreement; it does not establish complete binary equivalence. Live
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
wrong targets and changed code/layouts. The full suite currently has 226 tests.
These tests do not simulate hardware or prove concurrent driver correctness.

```sh
python3 -B tools/quest2_diag_profile.py --btf /private/live-vmlinux.btf \
  --output /private/new-layout-check.json
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -q
```

The matching build and live module result are recorded separately after
verification. A successful observational snapshot will still report
`handoff_ready=0`: it cannot establish producer drain, DMA quiescence, secure
ownership, watchdog control or recovery.

The profile verifier accepts both executable and dynamically linked kernel ELF
formats. The approved ARM64 `CONFIG_RELOCATABLE` build uses `-shared` and produces
`ET_DYN`; this does not relax its AArch64, symbol, BTF or instruction checks.
Relocatable module objects (`ET_REL`) are rejected as `vmlinux` inputs.

### Remaining hardware prerequisites

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
