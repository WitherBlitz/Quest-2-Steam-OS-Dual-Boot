# Quest 2 release-readiness audit

**RELEASE_BLOCKED: this project is not a feature-complete Quest 2 port, official
SteamOS, an installable Linux/Horizon dual boot, or a validated recovery system.**
This audit covers the repository and retained evidence on 2026-10-23. It changes
build preparation and documentation only. No headset operation is part of it.

## Audit of all nine stages

| Stage | Verified preparation / evidence | Required remaining work and concrete blocker |
| --- | --- | --- |
| 1. Resource ownership | Matching-firmware passive collectors and bounded camera/DSP diagnostics; host-tested ownership models. [Ownership audit](QUEST2_OWNER_AUDIT.md), [runtime attribution](QUEST2_TRACKING_OWNERSHIP.md). | Identify every stock camera/tracking/FastRPC/firmware owner and bind checked stop adapters. Open FDs, reference counts and a null optional delegate do not establish complete ownership or release. |
| 2. Hardware shutdown | Native coordinator and read-only completion primitives compile; host fixtures exercise ordering, errors and rollback. [Checked shutdown](QUEST2_CHECKED_SHUTDOWN.md). | Bind real stock producer adapters; prove camera/DSP/GPU/USB/UFS/AOP DMA completion and error propagation. Several stock paths discard errors or wait without a proven hardware deadline. No complete shutdown has been observed. |
| 3. Watchdog / recovery | Pinned reset/pstore audit and schema-2 selector-bound passive captures/comparison. Later ordinary button-return observations include a changed boot and user-reported healthy Home/tracking; timing was unmeasured and root unavailable. [Recovery evidence](QUEST2_WATCHDOG_RECOVERY.md). | Record the exact physical-button sequence/timing and complete the privileged after-capture; prove an independent reset deadline and diagnostic survival across each actual shutdown boundary. Ordinary button recovery alone cannot close those boundaries. |
| 4. First Linux boot | Matching loader builds, one successful temporary 36 MiB claim/release, payload checks and production transition execution under QEMU EL1. [First-boot evidence](QUEST2_FIRST_LINUX_BOOT.md). | Close ownership/DMA/CPU/secure-firmware/recovery gates; then observe real kernel entry, USB enumeration, RAM-only shell and return to usable Horizon. **No first recoverable native Linux boot is recorded.** |
| 5. Linux hardware | Pinned Image and 28-module ABI/provider-checked bundle; USB rescue CPU default requests powersave. [19-row checklist](QUEST2_LINUX_HARDWARE.md): **12 Blocked, 7 Untested, 0 Working/Partial**. | Validate display, GPU, sensors, radios, audio, storage, USB/charging, battery, fan/thermal policy, inputs and power management on the native target. Firmware/calibration/userspace and the Syncboss timing/MCU contract remain missing or unresolved. |
| 6. VR / presentation | Source-tested controller clock/stick fixes. [22-row matrix](QUEST2_VR_TRACKING.md): **16 Blocked, 6 Untested, 0 Working/Partial**. | Implement calibrated head/controller 6DoF, common timestamps, optics/IPD, prediction/timing, loss/relocalization, recentering, haptics, pairing, boundary/safety, passthrough and hand tracking. The source's fixed positions are not positional tracking. |
| 7. Gaming environment | Separate development branch `codex/quest2-native-boot` at `f843fa1`; two unfinished edits remain there and are excluded from this release audit's source. | Integrate and validate the ARM64 userspace/runtime, compositor, graphics and input stack. No native gaming session, Steam/SteamVR integration or Windows-game compatibility has been demonstrated for this port. Do not treat that separate branch as a released gaming environment. |
| 8. Dual-boot experience | [Host selection reference](QUEST2_DUAL_BOOT.md): 28 focused tests, persistent defaults/one-shot requests, interrupted-attempt fallback and unconditional execution/auto-boot refusal. | Establish a supported shared store, privileged broker, two real switching buttons, checked startup integration and physical switching/cold-boot cycles. The model's `valid`, `ready` and `recover` inputs are simulations. |
| 9. Installation / reliability / release | This audit, pinned build provenance and the bounded reproducibility checks below. | A Quest 2 OS installer/updater/uninstaller, complete compiled-runtime/legacy-asset provenance and redistribution review, and the physical acceptance programme below remain release blockers. Development artifacts are prerequisites, not an OS installation. |

All stages remain incomplete against the requested end-to-end experience.
Earlier successful stock diagnostics and user-confirmed normal Horizon tracking
are evidence for those narrow tests; they are not native Linux support evidence.

## Build reproducibility and distribution boundary

Retained builds `39945605803` and `40009277447` had the same functional BusyBox
configuration, but different binaries because their banner embedded build time.
Their SHA-256 values were respectively
`cd4c024b1e02de00c52bd1471547359b74942bb3b4ee2ecda01c0922a03b3181`
and `47d9cfde3cd306f2ee0e11d2c4bcb2d57f1e20e8021add11aa61bff0ffe4198d`.
The differing banner and derived build ID were previously isolated.

The target workflow now passes `SOURCE_DATE_EPOCH=1792540800` (2026-10-21 UTC)
and `TZ=UTC` to BusyBox. The pinned BusyBox 1.36.1 configuration writer honors
that variable. It makes two clean sequential builds at the same path and
compares the actual binary and complete config. It also packs the same
initramfs inputs twice and compares the compressed outputs.

These gates are limited to a recorded environment/toolchain and those inputs.
They do not establish independent cross-machine, whole-kernel or complete
release-bundle rebuild reproducibility. Logs contain execution-specific data.
Provenance records this scope separately from `installable=false`,
`auto_boot_allowed=false` and the existing closed hardware handoff flags.

The port-source archive excludes seven inventoried upstream legacy prebuilts:
`initramfs/os/qkx-splash`, `module/ion_secmap.ko.off`,
`module/quest_kexec.ko.pre-adsp`, `tools/device/qkx_dm`,
`tools/device/qkx_fsmap`, `tools/device/qkx_rawcp` and
`tools/turnkey/QkxFrameworkOverlay.apk`. Their source-tree copies are unchanged;
exclusion is not a provenance or redistribution-rights clearance for them.
All remaining tracked source, build recipes, patches, tests and licenses are
retained. A release audit must be repeated for each new source revision.

Native CI artifacts contain the matching kernel base archive, port patches
and recipes, BusyBox source/config and build provenance. The project/kernel/
BusyBox GPL sources and license travel with their binaries; the controller
fixture/patch retains its separate Boost license. No Valve/Steam binaries,
device DTB, stock firmware, calibration, account data or private captures are
supplied. Do not add those to a public release without their own rights/privacy
review. The preserved README credit remains unchanged.

The manually attached development snapshot assets are **source-only**: filtered port
source, public BusyBox source, configs, build provenance, license and this audit.
It provides no built Image, modules, initramfs, Quest 2 OS installer or root APK. The
provenance describes the separately tested native CI outputs; it does not claim
that those binaries are included in the snapshot. The pinned build recipe fetches
its recorded kernel/compiler inputs from upstream.
GitHub's automatically generated source archives mirror the tagged Git tree and
include its preserved legacy prebuilts; the manually attached filtered port-source
archive is the audited snapshot described here.

Compiled userspace redistribution has not been cleared by this audit. The
retained static BusyBox includes glibc runtime code; exact system-library/toolchain
inputs and associated source/notice obligations need a complete distribution
review before shipping a compiled OS package. Retaining project/kernel/BusyBox
sources alone is not claimed to complete that review. Existing CI artifacts
remain development evidence, not an approved end-user distribution.

## Firmware compatibility and installation status

The development identity is **Quest 2 / Hollywood**, Horizon incremental
**52242990024200150**, stock kernel
**4.19.325-cip128-st12-g49638c7a8637**. A match permits only the specifically
documented diagnostic scope; it does not establish safe handoff or feature
compatibility. The target kernel is a separate development build. Other
firmware/kernel/model combinations require their own verification.

The existing read-only `tools/quest2_preflight.py` verifies this identity and
requires an explicitly selected, already-authorized ADB device and existing
root. It never roots, updates firmware, installs an OS or enables automatic boot.
See [the preflight instructions](../README.md#quest-2-read-only-preflight-host-tool).

- **Install:** no supported Quest 2 OS installer is provided. A development
  archive may be inspected on the host after checking its published SHA-256;
  extracting it is not an instruction to load its modules or jump to Linux.
  Legacy `qkx-install-package.sh` targets Quest Pro/seacliff and rejects Quest 2.
  Its presence in Git is not a Quest 2 installation route.
- **Update:** no tested OS updater, atomic rootfs update or rollback exists.
  Inspect a new development archive in a separate host directory; retain known
  source/hashes and keep private captures outside the repository. A port build
  does not authorize changing Horizon firmware or the root setup.
- **Uninstall:** there is no deployed Quest 2 OS from this workflow to remove.
  A host-only archive can be removed independently of the headset. Do not use
  the upstream Quest Pro uninstaller as an assumed Quest 2 rollback mechanism.

No persistent rootfs layout, encryption/key handling, safe shared state location,
storage-space requirement or preservation-aware partition change is established.
There is no authorised formatting, flashing, factory reset, userdata write or
boot-policy change in these development instructions. Preserve user data and
normal Horizon access while those designs and recovery tests remain open.

## Root persistence

Singularity lists **Root on Boot** and confirms the above Quest 2 incremental.
Its [official README](https://github.com/Lumince/singularity) and
[v1.0.7.9 release](https://github.com/Lumince/singularity/releases/tag/v1.0.7.9)
were checked for this audit. The user previously confirmed automatic rooting
after a restart; the current device/settings were not queried in this audit.

Automatic rerooting avoids manually pressing Root Now each time. It does not
make the runtime privilege change survive a full reboot. Fugu's
[ADB-root documentation](https://github.com/Henry1887/fuguquest#--adb-root--root-adb-shells-without-magisk)
explicitly describes that mode as RAM-only and cleared by reboot. No permanent
boot-image root or supported bootloader-unlock method for this headset/firmware
has been verified here. Installing Magisk after a runtime exploit alone does not
demonstrate that capability. This audit changes no root or update settings.

## Physical reliability and recovery acceptance

There are **zero recorded native Linux cold boots, Horizon/Linux switching
cycles, native tracking-loss recoveries, suspend/resume cycles or extended gaming
sessions** for this project. Native stability, battery drain, charging interaction,
thermal control/fan response, performance and loss recovery remain unmeasured.

The [recovery document](QUEST2_WATCHDOG_RECOVERY.md#external-recovery-and-staged-acceptance)
distinguishes Meta's published power/volume-button procedure from an observed,
independently timed recovery on this headset. The later ordinary return was
untimed; its exact completed sequence and privileged after-capture remain missing.
No watchdog/reset deadline or pstore survival
through actual hardware shutdown has been established. A host timeout cannot
supply that guarantee. No factory reset is part of recovery acceptance.

Close stock ownership, shutdown and independent recovery first. Then observe
manual RAM-only Linux entry and return to usable Horizon with normal head and
controller tracking. Repeat normal/failure/recovery cycles before adding hardware
consumers, persistent rootfs, switching, sustained workloads or automatic boot.
Record the exact source/payload/DTB/firmware identities, first error, external
timing, retained logs and user-observed health for every physical claim.

Release remains blocked until all required feature matrices and the complete
installation/update/uninstall/data-preservation and reliability programme have
physical evidence. Passing host tests or a labelled development prerelease
cannot change this status.

## Verification for this increment

- Windows and [Ubuntu regression CI 40042950174](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40042950174)
  each passed **488 host tests without skips**. Ubuntu additionally passed seven
  existing reader QEMU cases; these are not native switching tests.
- [Native CI 40042947805](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40042947805)
  built source `0e560fec46a572f4cbbef6a90531b47b5ea65697`, passed 488 host tests
  and eight ARM64 rescue-init fixture tests, and passed both clean BusyBox
  binary/config comparisons and both compressed initramfs-pack comparisons.
- The downloaded native artifact passed its GitHub ZIP digest, exact 17-file
  inventory, all 16 checksums and ten provenance-bound output hashes. All 188
  filtered port-source files matched their Git blobs, with exactly seven legacy
  prebuilts excluded and both license texts retained. The 28-module/40-file
  bundle passed ABI, import/provider, debug-section and reconstructed-bundle
  checks. These are offline checks, not module-loading results.
- Image, target config and the module archive were byte-identical to retained
  build `40009277447`. The initramfs difference was isolated to BusyBox's fixed
  `2026-10-21 00:00:00 UTC` banner and derived GNU build ID; all other CPIO
  entries and the functional BusyBox configuration matched.
- Bash syntax and `git diff --check` passed. Independent source review found no
  issues in the change; comprehensive compiled-runtime redistribution clearance
  and every physical capability above remain outside that result.

The attached source-only development snapshot records these bounded results.
It cannot establish physical completion or clear the release blockers above.
