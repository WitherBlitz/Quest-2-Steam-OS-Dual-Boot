# First Linux boot prerequisites

The first recoverable Linux boot is **blocked**. The original offline increment
fixed loader defects; the follow-up below executes the transition in a system
emulator. Neither enables a Quest jump nor deploys a module. The supported
development identity remains Hollywood,
Horizon `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`.

## Loader changes

- Opening a regular kernel, initramfs or DTB now acquires the pinned kernel's
  `deny_write_access()` exclusion before reading its size or header. An existing
  writer produces `-ETXTBSY`. Every successfully acquired exclusion remains held
  through staging and is balanced by `allow_write_access()` before close on
  success and failure. This uses the same inode protocol as the pinned
  `kernel_read_file()`; it does not claim protection against another kernel or
  firmware agent changing memory. Completed staging uses separately owned pages.
- Staging-initialization RAM-walker errors survive cleanup. Missing System RAM
  coverage still returns `-ERANGE`. Destination ownership remains held until
  abort/unload or handoff; staging/rehearsal modes do not gain permission to
  overwrite a fixed window.
- CPU tests preserve the first down/freeze/probe/up error while attempting all
  required restoration and the original affinity. A later online-mask mismatch
  does not replace an earlier failure. Restoration order matches the existing
  pinned-kernel convention. Online-mask observations do not prove physical CPU
  power-off: the pinned ARM64 teardown can log a `cpu_kill()` failure without
  propagating it, and missing PSCI affinity information is not a power-off receipt.
- Native `machine_shutdown()` receives the existing pending-wakeup evidence.
  The loader no longer clears `events_check_enabled` or `pm_abort_suspend` to
  avoid its refusal. Failed secondary shutdown still reaches the existing panic
  path before entry. This does not establish a physical recovery path from panic.
- The reversible ARM64 roundtrip saves DAIF in preserved `x28`, masks all four
  asynchronous exception classes before changing translation state, and restores
  DAIF after returning to the original high mapping and translation context.
- The copied physical probe materializes its SCTLR mask with MOVZ/MOVK. The old
  assembler could reuse the relocation loop's literal pool, leaving the probe
  reading outside its independently copied range. An assembler check rejects a
  future mask wider than 32 bits. Existing relocation cache/barrier and kernel
  entry-register sequencing is retained; no extra unproven startup workaround
  is introduced.
- The offline payload checker rejects overlap between any DTB header reserve-map
  entry and the entire rounded copy envelope, including its padding. Adjacent
  half-open ranges remain valid. Existing live-tree/static/dynamic exclusions
  and reserve-map preservation in DTB preparation remain required. Dynamic
  `alloc-ranges` constrain placement; they neither reveal a live secure allocation
  nor establish firmware ownership or all future target working-memory safety.

## QEMU EL1 execution fixture

`tools/ci/quest2_transition_el1.py` builds a freestanding test around production
`module/transition.S`, using the existing pinned-header assembler fixture. Passing
cases do not substitute privileged instructions. QEMU runs a single Cortex-A57
on `virt-6.2`, with no disk/network/USB input and private emulated RAM.

The fixture starts at EL1, establishes a high code/stack alias, and supplies a
separate identity page table. It checks the physical probe, null/empty descriptor
chains, one page and 256 pages crossing the 255-entry descriptor boundary. It
compares every source/destination byte and adjacent guard pages, checks restored
DAIF/TCR/TTBR0/SCTLR and a high-address return, then exercises `qkx_enter` into an
assertion shim checking MMU-off state, exception masking and the Linux x0-x3
register convention. The x0 value is a cookie, not a Linux DTB or boot proof.

Negative controls deliberately shorten the copy loop, corrupt an entry register
and hang a guest, checking assertion failure and bounded host termination.
The [EL1 execution run](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40004756850)
passed at source `0a9906dfb2f6a8813aac79edf400ab76b1f13d2e`, using Clang/LLD
18.1.3 and QEMU 8.2.2. Production execution reached every expected marker and
exited zero. Both faulty variants exited one at the specific expected assertion;
the hanging guest reached its deliberate loop and was killed by the two-second
host timeout. The same job passed all 457 host tests with no skips.

Downloaded verification checked the 12-file allowlist, all six archived source
hashes against the exact Git blobs, GPL license, generated assembly against the
unchanged production fixture, and ELF entry/load bounds in emulated RAM. The
production ELF SHA-256 is
`ec286fcd68939d07b7a46e49bd36614ada6d3135da7f4078cf27a6742371ea0c`.
Windows independently cross-built the fixture and passed all 457 host tests;
independent review found no blocking issue. No headset access, kernel-module change,
hardware adapter, startup bypass or boot-policy change is introduced.

This is an architectural execution check, not proof of Qualcomm/secure-firmware
behavior, cache coherency, secondary-CPU power-off, DMA shutdown or recovery.
QEMU's [`virt` board](https://www.qemu.org/docs/master/system/arm/virt.html) models
a generic virtual platform. [Semihosting](https://www.qemu.org/docs/master/about/emulation.html#semihosting)
uses only this trusted fixture's console/exit operations. An emulator exit is not
a Horizon recovery event. All first-boot gates below remain required.

```sh
python3 -B tools/ci/quest2_transition_el1.py --output /private/new-transition-check
```

Clang, LLD and system `qemu-system-aarch64` are required. `--build-only` reports
`BUILT_NOT_EXECUTED`, separately from `PASSED`. Each build command is bounded at
30 seconds and each ordinary guest at 20 seconds. These host limits do not
provide a watchdog/reset deadline for the headset. Receipt fields
`handoff_ready`, `auto_boot_allowed` and `device_access` remain false.

## Verification and limits

Regression tests first reproduced missing file exclusion, overwritten original
errors, suppressed pending wakes, missing DAIF protection, an external copied-probe
literal and five reserve-map overlap shapes. The corrected full host suite passes
411 tests on Windows with Clang. Tests extract actual production C functions;
the assembly tests assemble/disassemble the production body and inspect ELF
symbols, copy ranges, literal/branch targets, barriers and entry registers.

The writer fixture models the pinned inode-access protocol and tries byte writes
and truncation at size reads, before/after header reads and during staging. It
checks `-ETXTBSY`, unchanged bytes/sizes and balanced release on both success and
stage failure; writes become possible after cleanup. A temporary pre-exclusion
negative control admits mutation and fails the regression. These are deterministic
interleavings against host doubles, not live-filesystem concurrency tests.

The small assembly fixture substitutes pinned linkage/cache macros and the
disabled erratum workaround. It does not execute privileged instructions, model
enabled errata variants, validate MMU-off behavior or demonstrate hardware
shutdown. The final 411-test suite also passed without skips in
[Ubuntu run 39934839947](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39934839947)
at `6d55061f241055f3a20e00031642f33ef1cd48a7`; its seven existing reader/QEMU
cases and downloaded source/binary/license evidence passed. Native CI archives
the transition compile command and object disassembly alongside its existing
ABI/provenance evidence.

The first native build, run `39933690788`, compiled but correctly failed its
strict stock-CRC gate: file write exclusion introduced an atomic helper absent
from the curated reference. Offline extraction from the existing hash-pinned
stock Image found `__ll_sc___cmpxchg_case_mb_4`, CRC `0xdbd1083c`, at Image file
offset `0x19d2edc`. All 11,137 stock exports decoded, all 78 existing curated
CRCs agreed, and this helper matched the prior verified generated symvers.
Only that independently derived reference was added; no ABI comparison was
relaxed and no headset access was needed.

### Verified matching native build

[Run 39935894720](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39935894720)
passed from `cac46877ec608fd87aec61743188027f4f52258b`, including all 411 Linux
host tests without skips. Downloaded verification checked the 19-file allowlist,
all 18 inner checksums, config/compiler/source pins and five effective compile
commands. All four AArch64 modules passed an independent ELF/vermagic/import
audit against both generated and stock CRCs: 44/15/20/24 version records for
`quest_kexec`, `marker_read`, `usb_log` and `camera_dsp_diag` respectively.

Six instruction-order/containment checks were also run against the actual
matching-header `quest_kexec.ko`, rather than just the small assembler fixture.
Copied relocation/probe/return ranges are 176/44/12 bytes, fit the control page
and contain no ELF relocation entries. Literal/branch containment, complete SCTLR
mask, DAIF save/restore, cache/barrier order and entry-register checks passed.
No privileged instruction was executed by these checks.

The loader is 398,912 bytes, SHA-256
`efeda1054f05b1dad83ce1f5080eeac65b4d63006a2588791e93b33c917ec525`.
The rebuild remains stage-only; no effective command enables experimental
jumping. Compilation, static ABI agreement and disassembly do not prove live
insertion, physical CPU shutdown, MMU execution, Linux boot or recovery.

The retained `startup-usb-child` Image, initramfs and config from run
`39909419709` were checked again with the corrected tool against the existing
private capture. Preparation produced no structural/profile findings. All four
prepared payload-file lengths/hashes and target provenance passed; the captured
bootloader reserve map was preserved exactly. These inputs are stale offline
observations, unclaimed and private. `handoff_ready`, `auto_boot_allowed` and
`device_access` remain false; this does not refresh device state or execute PID 1.

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py'
git diff --check
```

## Remaining gates and observed state

1. Bind checked stock producer/owner adapters and demonstrate complete camera,
   tracking, DSP/FastRPC and secure-resource stop/drain completion. Sampled file
   descriptors or coordinator fixtures are insufficient.
2. Demonstrate DMA completion and the USB/UFS/AOP shutdown sequence using actual
   hardware backends. Preserve every timeout and irreversible boundary.
3. Establish watchdog ownership and an observed reset/log-retention/Horizon
   recovery mechanism that survives each shutdown boundary. The timed ordinary
   physical-button test requested in the preceding recovery step remains pending;
   even a successful ordinary test would not prove survival after shutdown.
4. With those gates satisfied, exercise bounded relocation/CPU/MMU tests and
   verify the target startup profile against retained evidence. The existing
   `startup-usb-child` development build has not booted on the headset. No blanket
   SMMU/watchdog/firmware error bypass is justified by the current evidence.
5. Only then attempt the minimal RAM-only Linux initramfs. Independently observe
   kernel entry, USB enumeration, network access and rescue-shell commands;
   verify a return to usable Horizon and working head/controller tracking.
   Repeat successful recovery cycles before automatic boot.

| Milestone | Result for this increment |
| --- | --- |
| Module deployed / boot attempted | No |
| Confirmed Linux kernel entry | No |
| Usable Linux / USB / network / rescue shell | No |
| Confirmed Horizon recovery from this payload | No |
| Automatic Linux boot | Disabled |

The default stage-only option gate and Hollywood DMA refusal remain closed.
No headset command or hardware test is part of this offline increment. Captures,
prepared device DTBs, process identifiers and raw runtime pointers stay private.

## Pinned source evidence

Kernel source: `6bde5fc06b252c97761bc0963dd13c1abcf377ab`.

- [File write exclusion and size/read sequencing](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/exec.c#L893),
  SHA-256 `e1b78264484889a193d586b8ac654a17818bcc16369c21a2dcf2e2b34768295e`.
- [Native ARM64 kexec checks and cache preparation](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/kernel/machine_kexec.c),
  SHA-256 `9a9992c5e4d71d22988fefba951eb2e9ed4150fc1551d991550b18e90717759f`.
- [Native relocation sequence](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/kernel/relocate_kernel.S),
  SHA-256 `6f60ba56bc469f1267361087e8ac839508591647ddf176c35e7f8bd3d9f011ab`.
- [ARM64 boot contract](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/Documentation/arm64/booting.txt),
  SHA-256 `534035888925ba7b1c6a4d9bd5ef01f3578eb9dde2d584e6b44c5abbf2c407d8`.

See the existing [checked shutdown](QUEST2_CHECKED_SHUTDOWN.md),
[watchdog/recovery](QUEST2_WATCHDOG_RECOVERY.md) and
[target startup review](QUEST2_STARTUP_PATCH_REVIEW.md) for the independent gates.
