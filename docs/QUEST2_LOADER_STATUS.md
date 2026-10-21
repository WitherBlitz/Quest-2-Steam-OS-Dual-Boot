# Quest 2 loader development status

This branch contains a read-only readiness collector and source fixes for
`hollywood`, Horizon build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It is an experimental development branch.
The patched Android kernel module has **been rebuilt and its symbol versions
verified**. One RAM-only initialization attempt on the headset safely refused
the fixed destination with `-EBUSY`; no ownership was acquired or payload run.
Home and tracking remained normal. This branch does not provide a complete
SteamOS port, validated dual boot, or full tracking.

## Branch and build provenance

The fixes start at loader commit
`e61edbd7fbdf6dc4976a979c61cc545e04738422`, the exact source used for the
existing offline build. The upstream
[`quest2-port`](https://github.com/machachu56/quest-kexec-q2/tree/quest2-port)
branch has additional kernel, camera, Monado and userspace work. It remains
separate; these firmware-specific fixes do not replace that branch or claim to
validate its hardware changes.

The matching kernel source is
[`facebookincubator/oculus-linux-kernel`](https://github.com/facebookincubator/oculus-linux-kernel/tree/6bde5fc06b252c97761bc0963dd13c1abcf377ab)
at `6bde5fc06b252c97761bc0963dd13c1abcf377ab`. The prior offline build used
Android Clang/LLD 14.0.7, Android build 8508608 (`clang-r450784e`), from prebuilt
commit `425c8149f17c8d9914bf690b4d5abe45a13fb993`. The verified patched rebuild
is recorded below; the earlier unpatched artifacts are separate.

## Source fixes

- Syncboss selection requires the bound `syncboss_spi` driver and
  `meta,syncboss-spi` device-tree compatible. It no longer assumes a SPI bus
  number: on the audited headset, `spi0.0` is C-Media audio and Syncboss is
  `spi1.0`. Both suspend and resume callbacks must exist before proceeding.
- Requested AOP shutdown hooks are resolved and retained before freeze, GPU/USB
  suspension or watchdog MMIO. The audited kernel lacks `send_irq`; requesting
  that disconnect returns `-ENOENT` before hardware shutdown. This rejects an
  unsupported operation; it does not implement a replacement AOP protocol.
- A requested RPMh flush fails early when its symbol/device is absent, instead
  of silently continuing without the requested operation.
- `marker_read` refuses Hollywood-prefixed models and missing/unreadable model
  properties before mapping its fixed log region. Reads, clear and write-test
  operations are all blocked. The range overlaps ramoops pmsg on the audited
  firmware; no replacement persistent-log address is assumed. Legacy behavior
  on other model strings is unchanged.
- Default module builds permit staging only. The option gate also refuses CPU,
  MMU, reset, core-hang and secure-watchdog tests. `QKX_EXPERIMENTAL=1` is a
  developer build opt-in, not evidence that execution is safe. The target-kernel
  patches and their logging assumptions have not been ported by this change.
- A real jump now requires allocator ownership of the entire fixed 52 MiB
  destination window. Before staging, the loader checks mapped/nonreserved PFNs,
  one zone, isolation alignment and ordinary MOVABLE pageblocks, then calls the
  pinned kernel's `alloc_contig_range`. Missing helpers, CMA/other pageblock
  types or allocation failure abort without shutdown. A successful claim remains
  held until handoff or is freed on abort/unload. Rehearsals copy to separately
  owned scratch pages and do not claim the fixed window. The fixed range may
  fail allocation on a running headset; there is no fallback that overwrites
  busy memory. This establishes allocator ownership only, not DMA/secure safety.
- The freezer's wakeup/failure decisions are respected. The busy-loop thread
  that overwrote `pm_abort_suspend` has been removed. Reversible paths resume
  successful Syncboss suspension, then attempted GPU suspension, before thawing
  userspace. A failed Syncboss suspend is not resumed: its failure paths precede
  state changes and can indicate an earlier async resume is still running.
  A reported resume callback error stops with a panic. Syncboss queues async
  resume work and the GPU callback can hide internal errors, so callback success
  does not prove hardware recovery; that acceptance test remains outstanding.
- USB suspension is the explicit irreversible boundary. A later watchdog failure
  cannot return to apps with USB stopped; a requested RPMh flush failure cannot
  proceed to the physical trampoline. These paths panic; automatic recovery
  timing and real hardware behavior remain unvalidated.

## Host verification

The default no-jump build now also accepts an explicit `validate_ram=1`
diagnostic. It checks Kona/Hollywood, verifies System RAM coverage, makes one
native claim attempt for the fixed 52 MiB destination, releases successful
ownership immediately, and returns before any payload open or shutdown path.
It rejects combinations with hardware, CPU, reset and other diagnostic modes.
Allocation can migrate/reclaim pages and has no hard real-time deadline. This
mode tests RAM acceptance only; it cannot establish DMA or secure handoff safety.
The new build and live test results are recorded below.

Run with Python 3.11+ and a native GCC/Clang compiler:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
git diff --check
```

The full suite has 89 passing host tests: 21 readiness-collector tests, 27
loader regression tests, 17 RAM-ownership tests, 12 handoff-planner tests and
12 build-audit tests.
The RAM/shutdown regressions were observed failing before these changes.
The original loader failed 9
of the first 11 new regressions before the source fix. An independent source
review reproduced the 62-test suite before the final Syncboss suspend-abort
regression was added, and found no Critical or Important issue in that scope.
Subsequent pinned-source inspection found the duplicate-resume hazard on
Syncboss suspend failure; its regression was observed failing, then passing
with the correction, and the full 63-test suite passed.

The C harnesses compile the actual RAM claim/release and `qkx_execute` functions and the actual
initialization option/log guard prefixes, ending before their first external
action, against host API doubles. The tests check peripheral identity, changed
bus numbering, missing PM resume, missing requested AOP/RPMh dependencies,
Hollywood/missing-model log rejection, allocator failure/lifetime, rollback order,
irreversible failure paths and stage-only options. They do not
validate real kernel layouts, driver callbacks, DMA state or shutdown behavior.
Windows runs require Clang/LLD and configured Windows SDK/MSVC headers/libraries;
the host test compiler does not replace the pinned Android build compiler.

## Verified offline rebuild

### Placement strategy and Hollywood DMA gate

The [placement/shutdown strategy](QUEST2_HANDOFF_STRATEGY.md) adds an offline
capture-backed candidate planner, `ram_base` for the RAM-only diagnostic, and a
Hollywood hardware-transition refusal even in experimental builds. The saved
capture produced eight unclaimed windows above 4 GiB and 72 DT reference
clients; neither count proves ownership or DMA quiescence. The diagnostic
parameter cannot change the payload/DTB/entry layout. No headset changes or
candidate allocation attempt were performed in this phase.

All 19 new regressions were observed failing before their implementation;
the full 89-test host suite passes. The new `param_ops_ullong` CRC was derived
directly from the hash-pinned stock Image (`0x28916948`, CRC file offset
`0x19d6958`) and matches the prior verified generated symvers. A fresh source
review found no Critical or Important issue. Minor follow-ups remain for
inconsistent `phandle`/`linux,phandle` aliases and an aligned overflowing-base
test input; neither changes the hardware refusal gate.

[Archived Actions run 39567877831](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`ce5dfce20a5fb830632f291ae3a44844da9b2bd5`. All 89 tests passed on Linux and
the matching kernel and three modules built. Local checks reproduced the
transport digest, ZIP CRC/12-file allowlist, 11 checksums, source/compiler/config
pins, no-jump compile command and all 77 version records / 74 undefined-symbol
references against generated and stock references. Native `llvm-readelf`
separately confirmed ELF/vermagic and the `ram_base:ullong` parameter.

ZIP SHA-256: `ba9933bd6b85353a303410e2705bf8717839e07221e328cbfca66575e46af83e`.

`quest_kexec.ko`: 314,880 bytes, 42 version records, SHA-256
`fedfc4927497b0f10451adb9365cbcb2c8456a5b1ccc600b4525a3417555137b`.
The other two module hashes are unchanged from the prior rebuild. This native
build was not deployed; no high-window claim or DMA shutdown was attempted.

### RAM-ownership and shutdown rebuild

[Archived Actions run 39557599099](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from corrected source commit
`cdfc84a4bc30d9cfcc997ea5f4713bba3c6dfc02`. All 63 host tests passed on Linux,
and the matching kernel and three modules built without warning/error log lines.
The artifact was downloaded and locally checked against its transport digest,
12-file allowlist, all 11 checksum entries, pinned provenance, stage-only
compile command, ELF/vermagic metadata and generated/stock symbol CRCs.

ZIP SHA-256: `015fc4eea6c3d478212ee1c9c65fb1e197445addd83570e0bcdc92cc3a578fb3`.

| Module | Version records | SHA-256 |
| --- | ---: | --- |
| `quest_kexec.ko` | 39 | `562e6c33618b197299c8b4e68fbd8a3b2f91d2ba56efbb695a40c83d07b67cd1` |
| `marker_read.ko` | 15 | `251820730e1023e858e254c4af230070c4ba99cf658c583373159c0a11591605` |
| `usb_log.ko` | 20 | `d43889ccff99e01703836a8082c2df92da57d64c89c8d8ec0b3ca7e5b45f6753` |

All 74 version records and 71 undefined-symbol references passed the checks.
The two new built-in import CRCs (`pfn_valid`, `contig_page_data`) were also
independently rederived directly from the hash-pinned stock Image. All 12,362
`Module.symvers` records equal the baseline below after sorting; the rebuilt
kernel Image has the same SHA-256 as that baseline. Native `llvm-readelf`
separately confirmed the three AArch64 relocatable headers and exact vermagic.
No module was loaded. These remain offline checks, not a hardware boot test.

One minor test-coverage improvement remains: combined successful destination
claim followed by a staging failure; current tests cover those cleanup branches
separately. The initial run 39557198852 was intentionally cancelled before
completion when the Syncboss suspend-abort correction was identified.

### RAM-only rebuild and on-headset validation

The RAM-only diagnostic and its seven new regressions passed the host suite.
A fresh source review found no Critical or Important issue in this path.
[Archived Actions run 39562556932](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`6410b712e8a489b54d2d10c30bf004457844cf11`. All 70 tests passed on Linux and
the matching kernel and three modules built. Local checks reproduced the ZIP
digest, 12-file allowlist, 11 checksums, source/compiler/config pins, no-jump
compile command, ELF/vermagic metadata and all 76 version records / 73 undefined
references against generated and stock references.

ZIP SHA-256: `a67f14afe81cc8f309d1f6aac62bfbaf747dbde0b6c45f0c915d889b6b57f486`.

The tested `quest_kexec.ko` has 41 version records and SHA-256
`c88968617455a10b28f9fd8f70f7eca9ce669935578553973971b939284f6d62`.
The other two modules retain their hashes from the preceding rebuild and were
not loaded.

On 2026-10-21, exactly one `validate_ram=1` attempt returned:

```text
quest_kexec: RAM validation result=-16 elapsed_ns=6273646 owned=0
```

`insmod` returned "Device or resource busy". The result follows the model,
symbol and System RAM checks; no per-PFN refusal message appeared, indicating
the native allocator refused the fixed `[0x90000000, 0x93400000)` window. No
ownership was acquired. The precise busy page/owner is unresolved; earlier
non-atomic page metadata snapshots cannot establish the cause. No retry or
forced claim was made.

The boot ID, CPUs 0-7 and original 20 stock module names were unchanged.
Failed initialization left no project module loaded. The temporary module
file was hash-checked and removed. Taint changed from 1028 to 5124, exactly the
expected out-of-tree-module flag, which persists until reboot. The captured
log difference contained no matched panic, BUG, WARNING, OOM or fault patterns.
The user confirmed Home and tracking were normal. No payload open, shutdown,
CPU offlining, USB stop, reboot, flashing or boot-policy change occurred.

Passive topology, runtime device-tree and IRQ checks also completed. They
showed bound devices and activity in GPU, USB, Syncboss and CDSP IRQ paths;
camera-frame operation was not established. No GPU/Syncboss suspend rehearsal
was performed: the callbacks currently lack an adequate recovery oracle.

The pinned ARM SMMU, camera request-manager, camera SMMU and ADSP-loader driver
registrations lack shutdown callbacks. Generic device shutdown does not invoke
their remove/suspend paths or close frozen userspace camera handles. IOMMU
groups, runtime PM state and quiet IRQ counters therefore cannot establish DMA
quiescence. Parent/bus/domain effects still require tracing; an absent callback
alone does not prove DMA continues after all shutdown paths. An explicit
device-lifecycle and DMA-fencing design is still needed.

Minor review follow-ups remain for early refusal-stage logging and negative
walker, partial-coverage, incompatible-Kona and timing/log tests. These do not
change the observed allocator refusal or establish hardware readiness.

### Earlier baseline

[Archived Actions run 39551869780](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`3668c76984ee4159f19f4f2018957be3c8693f34`. It built the matching kernel Image and
modules, then the three patched out-of-tree modules with experimental jumping
disabled. All 47 host tests also passed on the Linux runner.

The downloaded ZIP's SHA-256 is
`11fe06971c7af7a558aefaac532af352eaf2fd9056c4c262aef6d9b897f3492b`.
Local verification reproduced all archive checksums, source/compiler/config
pins, AArch64 ELF/vermagic identity, undefined-symbol coverage, and all 72
version-record CRCs against the prior trusted built-in stock references:

| Module | Version records | SHA-256 |
| --- | ---: | --- |
| `quest_kexec.ko` | 37 | `1018b0a5c3c7e4166f026f8699d1b8cdc7b5ce8c8f6b4c27a3c4369224c867dd` |
| `marker_read.ko` | 15 | `251820730e1023e858e254c4af230070c4ba99cf658c583373159c0a11591605` |
| `usb_log.ko` | 20 | `d43889ccff99e01703836a8082c2df92da57d64c89c8d8ec0b3ca7e5b45f6753` |

All 12,362 full `Module.symvers` records equal the prior matching build; their
textual order differs. The configuration is unchanged. The kernel Image and
module binaries are not claimed byte-identical to previous builds. Native
`llvm-readelf` independently confirmed the three modules' ELF headers/vermagic.
These checks establish the stated offline build and symbol-version agreement;
they do not establish hardware readiness.

The manually triggered workflow uses an owner-provided private config secret
and publishes only allowlisted build outputs. It does not cache the config or
upload raw captures/config/firmware. Artifacts are retained for seven days.

## Reproducing the rebuild

Use a fresh module output/source copy and the existing fully built matching
Android kernel output. Preserve the original `.config`, `Module.symvers`,
kernel Image, source revisions and captured mitigations. For this firmware,
`REAL_CC=clang` is required by the downstream kernel Makefile.

```sh
make -C "$KDIR" M="$PWD/module" \
  ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang REAL_CC=clang LD=ld.lld \
  HOSTCC=clang HOSTCXX=clang++ CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LOCALVERSION=-g49638c7a8637 \
  QKX_EXPERIMENTAL=0 modules
```

This is an offline build command, not a deployment command. Require exit zero,
ELF64 little-endian relocatable AArch64 output, the exact release/vermagic,
version coverage for every undefined import, and CRC agreement with immutable
matching stock references. Confirm the effective loader compile command does
not define `QKX_ENABLE_EXPERIMENTAL_JUMP`.

The earlier local audit reported agreement for 11,137 built-in stock export
CRCs, 77 imports across the three original modules, and 33 selected private
structure offsets. These results refer to the previously inspected artifacts.
Raw captures, device identifiers, firmware files and private runtime evidence
are not published here, so that audit is not independently reproducible from
this source repository alone. Each subsequent rebuild needs its own recorded audit.

## Remaining work

The fixed RAM window was refused by the live allocator. A justified placement
or reservation strategy is needed while retaining that refusal; forcing busy
pages or guessing alternate addresses is not a solution. DMA quiescence,
secure-watchdog handling, CPU/MMU transition and recovery remain unproven.
Panicking after irreversible
failure prevents further handoff but does not prove safe recovery. The target
DT must preserve Android's secure/dynamic reservations; an allocator claim of
the copy window does not fence all target RAM. The broad `qcom,kona` guard is
not exact-board proof.
The scoped live initialization above exercised RAM validation only. No reboot,
flashing, partition change or boot-policy change occurred. Staging and RAM
validation allocate or migrate memory and use a temporary kprobe; they are not
read-only device tests.

The pinned shutdown implementation also sets these limits on future tests:

| Area | Source evidence | Still needed before hardware readiness |
| --- | --- | --- |
| RAM | The one live native claim refused the fixed window with `-EBUSY`. | A justified placement/reservation strategy and a target DT that preserves all Android secure/dynamic reservations. |
| GPU/Syncboss | PM callbacks can defer work or hide errors. | Completion and failure detection on this firmware, including rollback. |
| USB | Gadget suspend stops/disconnects gadget traffic. | Controller/glue DMA quiescence and target USB reprobe. |
| Storage | UFS shutdown drains requests and powers down the link, but returns zero even after logged errors. | Observed shutdown completion and target reinitialization; no shutdown bypass is implemented here. |
| CPUs/other devices | ARM64 `machine_shutdown` disables nonboot CPUs; generic `device_shutdown` invokes bus/driver hooks without an aggregate success result. | An audit of remaining DMA masters, firmware references and recovery. |

The eventual goal is Horizon/Linux switching with only one full OS running.
Display, tracking, controllers, audio, networking, suspend and a SteamOS runtime
require subsequent implementation and physical validation.

## Source basis for the ownership/shutdown changes

All links below refer to the matching immutable kernel source, not a newer API:

- [`mm/page_alloc.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/page_alloc.c):
  `alloc_contig_range` owns every PFN on success and pairs with `free_contig_range`.
- [`include/linux/gfp.h`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/include/linux/gfp.h):
  exact four-argument allocator ABI and single-zone requirement.
- [`mm/cma.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/cma.c):
  CMA has a separate ownership bitmap; bypassing it is not a valid reservation.
- [`kernel/power/process.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/power/process.c):
  failed `freeze_processes` already thaws userspace.
- [`drivers/usb/dwc3/gadget.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/usb/dwc3/gadget.c):
  gadget suspension disconnects/stops traffic; later failures cannot use the
  existing return-to-apps cleanup as a complete USB restoration protocol.
- [`kernel/reboot.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/reboot.c):
  `kernel_restart_prepare` invokes reboot notifiers and device shutdown.
- [`drivers/base/core.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/core.c),
  [`arch/arm64/kernel/process.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/kernel/process.c)
  and [`drivers/scsi/ufs/ufshcd.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/scsi/ufs/ufshcd.c):
  device/CPU shutdown coverage and UFS error-reporting limits in the table above.
- [`drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c)
  and [`drivers/gpu/msm/adreno.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/gpu/msm/adreno.c):
  deferred Syncboss resume and unreported GPU power-state errors limit what a
  successful PM callback establishes.
- [`drivers/base/platform.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/platform.c),
  [`drivers/iommu/arm-smmu.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/iommu/arm-smmu.c#L6228),
  [`cam_req_mgr_dev.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L861),
  [`cam_smmu_api.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_smmu/cam_smmu_api.c#L3938)
  and [`adsp-loader.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L474):
  platform shutdown calls the registered shutdown hook only; these driver
  registrations provide no such hook. Remove/suspend and camera-client close
  paths therefore cannot be assumed to run during generic shutdown.
