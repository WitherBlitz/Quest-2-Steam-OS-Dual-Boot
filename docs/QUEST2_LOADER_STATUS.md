# Quest 2 loader development status

This branch contains a read-only readiness collector and source fixes for
`hollywood`, Horizon build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It is an experimental development branch.
The patched Android kernel module has **not been rebuilt or loaded**. It does
not provide a complete SteamOS port, validated dual boot, or full tracking.

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
commit `425c8149f17c8d9914bf690b4d5abe45a13fb993`. A successful earlier build
does not establish a successful build of these changed sources.

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

## Host verification

Run with Python 3.11+ and a native GCC/Clang compiler:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
git diff --check
```

The full suite has 35 passing host tests: 21 readiness-collector tests and 14
loader regression tests. The original loader failed 9 of the first 11 new
regressions before the source fix. An independent source review also reproduced
the full passing suite and found no Critical or Important issue in this scope.

The C harness compiles the actual `qkx_execute` function and the actual
initialization option/log guard prefixes, ending before their first external
action, against host API doubles. The tests check peripheral identity, changed
bus numbering, missing PM resume, missing requested AOP/RPMh dependencies,
Hollywood/missing-model log rejection and stage-only options. They do not
validate real kernel layouts, driver callbacks, DMA state or shutdown behavior.
Windows runs require Clang/LLD and configured Windows SDK/MSVC headers/libraries;
the host test compiler does not replace the pinned Android build compiler.

## Required rebuild

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
this source repository alone. Rebuilt modules need their own recorded audit.

## Remaining work

RAM ownership, DMA quiescence, secure-watchdog handling, CPU/MMU transition and
recovery remain unproven. Failures after irreversible shutdown still need a
separate hardware design. The broad `qcom,kona` guard is not exact-board proof.
No loading, reboot, flashing, partition change or boot-policy change was part of
this source fix. Staging itself allocates memory and uses a temporary kprobe;
it is not a read-only device test.

The eventual goal is Horizon/Linux switching with only one full OS running.
Display, tracking, controllers, audio, networking, suspend and a SteamOS runtime
require subsequent implementation and physical validation.
