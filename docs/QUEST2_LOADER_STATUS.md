# Quest 2 loader development status

This branch contains a read-only readiness collector and source fixes for
`hollywood`, Horizon build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It is an experimental development branch.
The patched Android kernel module has **been rebuilt and its symbol versions
verified**. It has not been loaded on the headset. This branch does not provide
a complete SteamOS port, validated dual boot, or full tracking.

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

Run with Python 3.11+ and a native GCC/Clang compiler:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
git diff --check
```

The full suite has 63 passing host tests: 21 readiness-collector tests, 22
loader regression tests, 8 RAM-ownership tests and 12 build-audit tests.
The RAM/shutdown regressions were observed failing before these changes.
The original loader failed 9
of the first 11 new regressions before the source fix. An independent source review also reproduced
the full passing suite and found no Critical or Important issue in this scope.

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

The RAM-ownership and shutdown changes above have passed host tests and source
review; their new matching rebuild is pending. The run below is the previously
verified baseline and does not contain those subsequent changes.

[Actions run 39551869780](https://github.com/WitherBlitz/quest-kexec-q2/actions/runs/39551869780)
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

Runtime RAM claim acceptance, DMA quiescence, secure-watchdog handling,
CPU/MMU transition and recovery remain unproven. Panicking after irreversible
failure prevents further handoff but does not prove safe recovery. The target
DT must preserve Android's secure/dynamic reservations; an allocator claim of
the copy window does not fence all target RAM. The broad `qcom,kona` guard is
not exact-board proof.
No loading, reboot, flashing, partition change or boot-policy change was part of
this source fix. Staging itself allocates memory and uses a temporary kprobe;
it is not a read-only device test.

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
- [`drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c)
  and [`drivers/gpu/msm/adreno.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/gpu/msm/adreno.c):
  deferred Syncboss resume and unreported GPU power-state errors limit what a
  successful PM callback establishes.
