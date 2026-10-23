# Quest 2 Linux hardware checklist

**The first recoverable native Linux boot is blocked. No native Linux hardware
capability in this checklist has been demonstrated.** The Hollywood jump/DMA
gates remain closed; stock producer shutdown, hardware completion and recovery
are incomplete. This document records source preparation and future acceptance
criteria. It supplies no device procedure or authorization to bypass those gates.

Scope: Hollywood, Horizon `52242990024200150`, stock kernel
`4.19.325-cip128-st12-g49638c7a8637`, and the separate development target
`4.19.325-cip128-st12-qkx-q2-usb` (`startup-usb-child`). Stock Horizon operation,
read-only diagnostics, host tests and historical upstream results are not native
Linux functional evidence for this target.

## Status and evidence

Functional status uses these terms:

- **Working:** observed end-to-end operation and successful recovery on this
  exact native target. No row currently qualifies.
- **Partial:** observed native operation with an explicitly recorded limitation.
  No row currently qualifies; source availability does not qualify.
- **Blocked:** a known prerequisite is missing in the current payload or recovery
  contract, before functional acceptance can be attempted.
- **Untested:** source/configuration exists, but native behavior is unobserved.

Every row also inherits the global first-boot/recovery block. The status column
describes native functionality; the preparation column describes code and build
inputs only.

The original USB-rescue build is documented in
[startup patch review](QUEST2_STARTUP_PATCH_REVIEW.md#native-usb-rescue-development-profile).
Its target configuration has SHA-256
`dfa924d82aa1b63c639dff5f9f4bc70ef93c359658c49f5ed672e2682793c98d`.
The pinned kernel is
[`6bde5fc06b252c97761bc0963dd13c1abcf377ab`](https://github.com/facebookincubator/oculus-linux-kernel/tree/6bde5fc06b252c97761bc0963dd13c1abcf377ab).
The config source and upstream port comparison are
[`9c96c192d4a4a80f95ba0013294977e85c6eae11`](https://github.com/machachu56/quest-kexec-q2/tree/9c96c192d4a4a80f95ba0013294977e85c6eae11),
credited to machachu56 and the upstream contributors. This comparison does not
import their hardware results or remaining patches.

The retained initramfs contains 19 entries: static BusyBox, rescue init, runtime
directories/device nodes, account files and logwatch. It has no graphics
firmware, Mesa, gamescope, Monado, camera script or persistent rootfs.

## Functional checklist

| Hardware / behavior | Native functional status | Source preparation and missing dependencies |
| --- | --- | --- |
| Display scanout | Untested | Downstream SDE/DSI source exists. `ARCH_KONA=y` causes `techpack/display/Makefile` to include `config/konadisp.conf`, enabling effective `DRM_MSM/SDE/DSI` despite `DRM_MSM` being unset in target.config. Physical panel selection, dual-link DSC, regulators, reset and inherited state remain unvalidated. |
| Refresh rate | Untested | Hollywood DTS includes panel-specific 72/80/90/120 Hz variants and dynamic-rate properties. This is not an observed mode list. Validate the prepared DTB, selected panel and advertised KMS modes before selecting rates. |
| Brightness | Untested | SDE registers `panel%u-backlight`; DSI dispatches vendor-specific backlight controls. Some DTS variants couple illumination timing to refresh. Discover the selected panel's limits and restore its original level; no generic raw range is assumed. |
| GPU acceleration | Blocked | KGSL/IOMMU, ION and DMA-buffer support are enabled. A650 requests `a650_sqe.fw`, `a650_gmu.bin` and `a650_zap` firmware, absent from rescue userspace. Matching Turnip KGSL, compositor/KMS integration and checked GPU power ownership are missing. |
| Headset IMU | Blocked | Syncboss transport source is enabled; ordinary buffered IIO is not the implemented route. Packet decoding, units/axes, calibration, timestamp validity and a Linux consumer are missing from the rescue payload. Upstream Monado supplies 3DoF with fixed positions, not 6DoF. |
| Cameras and frame timestamps | Blocked | Spectra, V4L2/subdevice and four Hollywood camera nodes exist. Upstream replays a firmware-specific Android startup recording through Meta stream-mode APIs; that script is absent. Camera calibration, exposure handling, buffer ownership, checked stop/drain, IMU clock correlation and SLAM remain prerequisites. |
| Syncboss and Touch controllers | Blocked | `SYNCBOSS_SPI/POWERSTATE/NSYNC/MISCFIFO/STANDALONE=y`; timesync/direct-channel support is off. Pinned Hollywood DTS disables nsync and enables timesync, requiring compatible MCU firmware. Reconcile the actual prepared DTB and MCU firmware before selecting either mechanism. Radio/button/analog userspace is absent; upstream haptics are unimplemented. |
| Wi-Fi | Blocked | CNSS2/QCA6390 platform support is enabled, but `QCA_CLD_WLAN=m` is outside the Image. The exact target module/dependencies are prepared in a separate host bundle. Matching firmware/board data, regulatory inputs, network userspace and checked loading remain missing from rescue. A compiled CNSS platform driver alone is insufficient. |
| Bluetooth | Blocked | Bluetooth core and QCA6390 board/power declarations exist, but `BT_HCIUART` is unset. A suitable HCI transport, matching controller initialization/firmware and userspace are missing. Power-driver presence does not establish an HCI adapter. |
| Speakers / headphone output | Blocked | Hollywood uses CM710X plus AK4331; CM7120 is not the Quest 2 codec basis. Codec selection alone omits the forced Kona audio modules, DSP firmware and ALSA routes/mixer policy. CM710X requests `CM710X.bin`; userspace playback and endpoint routing are absent. |
| Microphones | Blocked | Shares the CM710X/Kona DSP/module chain. Genuine microphone calibration (`mic` firmware input), capture routes, channel mapping and Linux capture userspace are missing. ALSA card enumeration alone would not establish capture. |
| Storage and persistent rootfs | Blocked | UFS/QCOM and filesystem support exist. Controller/link/crypto state after handoff is unvalidated. Rescue init is RAM-only and mounts no userdata/rootfs. Read-only identity/geometry checks precede any separately authorized persistent-rootfs or write test. |
| USB, accessories and charging | Untested | DWC3 dual-role, xHCI, gadget and Type-C/charger source exists. Rescue init prepares RNDIS/ECM only; native enumeration is unobserved. Host accessories, role switching, power delivery and charging need separate validation. USB network/serial/audio accessory drivers include modules outside the retained Image. |
| Battery reporting | Untested | Power-supply, SMB5, FG and bq27xxx code exists; Hollywood DTS declares a TI bq27z561 and board battery profiles. Correct model/profile selection, units, temperature/current/SOC reporting and charger interaction remain unobserved. |
| Thermal control and fan | Blocked | TSENS, cooling support and the PWM/tach fan driver exist; DTS includes kernel fan/CPU cooling as well as `user_space` zones. An Android thermal daemon is not supplied. The USB rescue build now selects `powersave` instead of the retained build's `performance` default; this requests the policy minimum and does not demonstrate a clock or cooling response. Establish a complete Linux policy and measured cooling response before sustained load. |
| Physical buttons | Untested | `KEYBOARD_GPIO`, QPNP power-on and evdev support are enabled. Validate board mappings, press/release/repeat, wake semantics and recovery separately; an input node is not event evidence. |
| Proximity | Blocked | Syncboss powerstate source exists. Opening its consumer can enable MCU activity and reads genuine `PROX_PS_CAL_VERSION`, `PROX_PS_CANC`, `PROX_PS_THDL` and `PROX_PS_THDH` inputs. Those calibration/firmware dependencies and Linux policy are missing; opening a node is not a passive probe. |
| Power management | Untested | PM, wake locks, CPU-frequency and device PM code exist; `POWERSTATE_MANAGER=m` is outside the retained Image. Clock/regulator/runtime-PM transitions, wake-source ownership and actual low-power residency need observation. Successful sysfs writes/callbacks do not prove residency or power savings. |
| Suspend / resume | Blocked | Suspend/freezer code is enabled, but a surviving wake/recovery path and checked device restoration are unproven. No suspend request is part of this increment. Display, GPU, sensors, storage, radio, USB and thermal behavior must first have individual recovery evidence. |

## Module preparation boundary

The existing private prepared DTB was also checked offline: its Syncboss nsync
consumer is disabled and its timesync consumer is enabled, while the retained
target config selects nsync and omits timesync. This corroborates the source
finding against that stale capture; it does not establish current MCU firmware
compatibility. No timing driver was enabled or substituted by this increment.

The prior Image-only build omitted selected `=m` drivers, including WLAN, the separate
powerstate manager, USB audio/network/serial accessories, and the 12 effective
Kona audio module targets forced by the techpack build. Inspecting target.config
alone misses techpack configuration overrides.

A separate ABI-checked module bundle is now built for `startup-usb-child` only.
It contains all 28 installed target modules and 12 metadata files, bound to the
same Image, config, release and generated target symbol CRCs. Every module passes
ELF/vermagic/import-CRC checks and provider dependency-closure checks. Stock-loader
ABI checks retain their original stock default and remain separate.

Installed copies use Kbuild `INSTALL_MOD_STRIP=1` (`--strip-debug`); the packager
rejects remaining debug sections and retains its 128 MiB/file and 512 MiB/total
bounds. WLAN changes from 271,314,872 to 11,315,176 bytes after stripping. The
metadata allowlist includes the exact outputs in
[kmod v29's `depfiles[]`](https://github.com/kmod-project/kmod/blob/v29/tools/depmod.c)
plus Kbuild's order/builtin files. Unknown files and unexpected links are refused.
Packaging does not prove driver initialization.

The module-bundle increment did not change the target config, Image selection,
rescue init, autoload policy, firmware, calibration or deployment. A module
archive on the host does not make these modules available in the running rescue
image. Firmware and per-device calibration must retain their provenance and are
not supplied here.

## USB rescue CPU default

The `startup-usb-child` build selects the existing builtin `powersave` CPU
governor. Other development profiles retain their original defaults. The build
checks the effective config after `olddefconfig`, before compilation, and again
when writing provenance: CPU frequency support and the governor must be builtin,
with exactly one selected default. Missing, duplicate, conflicting or modular
defaults are refused. Provenance records `rescue_default_cpu_governor=powersave`
separately from `cpu_frequency_runtime_validated=false`.

The pinned kernel's [`cpufreq_powersave.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/cpufreq/cpufreq_powersave.c)
requests `policy->min` using `CPUFREQ_RELATION_L`; it discards the driver's return
value. The previous `performance` default requests `policy->max`. Configuration
and a successful build therefore establish a default request, not an actual
frequency, lower temperature, working cooling policy or recovery. No frequency,
temperature or fan value is guessed; trips, driver error handling, init,
autoload and handoff gates are unchanged. Hardware statuses remain unchanged.

Verification source: `f8ef9b2b74e1f2f87918acaf87a5ebb48216a224`.

- Windows: **13 focused tests and all 460 host tests passed**, with no skips;
  Python compilation, Bash syntax and whitespace checks passed.
- [Target build 40009277447](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40009277447):
  Image, all 28 modules and static ARM64 BusyBox built; **460 host tests and
  eight ARM64 rescue-init fixture tests passed**. The effective config differs
  from the retained build by exactly the two default-governor selections.
- Download verification checked all **17 files, 16 checksums**, ten
  provenance-bound outputs, source/compiler/license pins and Image header bounds.
  Reconstructing the module bundle rechecked every module's ELF/vermagic/import
  CRCs, provider closure, debug absence and all **40 archive file** hashes.
  The module archive is byte-identical to the retained bundle documented below;
  this does not imply that modules are deployed or loaded.
- New Image: **30,437,392 bytes**, SHA-256
  `6768e31a252a84dc04a22a45faa68fe8c92b3ba8d5ff72fdabb42018283a27c2`.
  Effective target.config SHA-256:
  `0bd6a87f17dfb1939eceb67bb0d498e38a7ca4d113e92a11a8323f639545758b`.
- New initramfs SHA-256:
  `981807a53bf12706f2f09eae47214b12f3691e3c2d3710a744e371c8c960694d`.
  Its only changed entry is BusyBox's build-time banner and GNU build ID;
  every other archive entry and functional BusyBox config setting agrees.
- [Ubuntu regression run 40007150450](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40007150450)
  passed **460 host tests and seven existing reader QEMU cases** at source
  `72fcbab` (the same runtime/test code, before the dependency-mirror fix).

These checks establish offline build consistency, not native hardware operation.
No headset command or boot test was performed; all 19 hardware statuses and
physical handoff/recovery gates remain unchanged.

## Dependency order

1. Close the [stock ownership/shutdown](QUEST2_CHECKED_SHUTDOWN.md), DMA/secure
   resource and [watchdog/recovery](QUEST2_WATCHDOG_RECOVERY.md) gates. Establish
   an observed independent recovery deadline and diagnostic retention across
   the actual shutdown boundaries.
2. Complete the [first native boot prerequisites](QUEST2_FIRST_LINUX_BOOT.md).
   Observe kernel entry, USB enumeration, network access and a RAM-only shell,
   then return to usable Horizon. Repeat before enabling automatic boot.
3. Verify the exact DTB, kernel/config/module ABI and dependency closure. Resolve
   Syncboss timing/MCU compatibility and inventory legitimate firmware and
   calibration before loading consumers. Do not copy an upstream replay blindly.
4. Establish battery/charger reporting, fan/cooling and a coherent thermal policy
   before sustained CPU/GPU/radio load. Validate buttons and a rescue input path.
5. Bring up display/brightness, GPU, audio and networking individually. Validate
   Syncboss/IMU/controllers before camera clock correlation; calibrated cameras
   and synchronized timestamps precede visual-inertial tracking.
6. Validate UFS read-only access before any persistent rootfs. Preserve the
   RAM-only rescue route while persistent storage and accessory roles are added.
7. Validate device runtime PM, then suspend/resume last. Each stage requires
   checked failure handling and recovery; later milestones cannot certify an
   earlier missing ownership or completion protocol.

## Bounded future acceptance

These are proposed test limits, not measured hardware performance: at most
10 seconds per discovery/start operation, a 60-second observation window and
120 seconds to reach usable Horizon during recovery. An independently validated
recovery mechanism must enforce the deadline. A host timeout cannot interrupt an
unbounded firmware call or prove hardware cleanup; such a test stays blocked.
Record the exact payload, firmware/calibration identity, selected mode, deadlines,
errors and observations. No automatic retries or error suppression qualify.

For every row, recovery means usable Horizon with head/controller tracking and
the affected subsystem restored, plus retained diagnostics and no new relevant
faults. Require three complete normal/failure/recovery cycles before automation;
no such native cycles have been demonstrated.

| Area | Normal acceptance within the observation window | Failure acceptance | Recovery acceptance |
| --- | --- | --- | --- |
| Display, refresh, brightness | Correct test pattern in both eyes; observed vsync at each advertised mode; minimum/mid/maximum discovered brightness and restoration. Modeset success alone is insufficient. | Missing panel/mode, rejected atomic commit or backlight error is reported; no timeout bypass or forced unsupported mode. | Restore original mode/brightness and verify Horizon display; include both successful and refused changes. |
| GPU | Identify driver/firmware; render a known workload and verify its KMS output for 60 seconds with no GPU/SMMU/HFI fault. | Missing firmware, allocation/submit error or stall ends the test with its first error preserved; no RSC success fabricated from a log. | Checked queues/resources stop before handoff; Horizon rendering and tracking return within the deadline. |
| IMU, cameras, Syncboss/controllers | Identify all sources; verify units/axes, both controllers' controls, four camera frame streams, loss counts and monotonic timestamps. Quantify cross-clock residual/drift against a declared tracking budget before claiming synchronized tracking. | Invalid offsets, malformed/truncated packets, absent script/calibration, disconnection or frame timeout invalidates affected tracking. Preserve STOP/RELEASE/drain errors. | No owned buffers/transfers/workers remain; Horizon head/controller and camera consumers recover. Frame delivery alone does not certify 6DoF or haptics. |
| Wi-Fi / Bluetooth | Wi-Fi scan/association/data exchange and BT adapter discovery/pairing/input are observed independently, with bounded reconnect. | Missing module/firmware/transport and association/pairing timeouts are explicit; no endless firmware fallback or retries. | Disconnect and stop consumers cleanly; Horizon radios and tracking resume. |
| Speakers / microphones | Low-level known stereo playback and channel-specific microphone capture demonstrate routing, level and timing; observe underrun/overrun counters. | Missing DSP/codec/calibration or route produces an error; no false success from PCM/card enumeration or silence. | Stop/drain PCM and DSP ownership; verify Horizon playback/capture restoration. |
| Storage / persistent rootfs | First verify read-only identity, geometry and known-file hashes. Persistent-rootfs/write acceptance requires a separate scoped test and a retained rescue path. | Wrong device, crypto mismatch, I/O error or link timeout refuses mounting/writing and preserves diagnostics. | Return without filesystem damage; verify Horizon data access. Do not unbind UFS as a recovery experiment. |
| USB/accessories/charging and battery | Observe rescue enumeration/data; separately validate each accessory role and charger attach/detach. Check plausible battery units/current direction/temperature/SOC and declared power contract. | Role/negotiation failure, missing accessory module or implausible telemetry is explicit; preserve charger protections and current limits. | Restore the rescue USB route and Horizon charging/reporting; successful gadget binding alone is insufficient. |
| Thermal / fan | Observe temperatures, fan tach and cooling state during idle and bounded light activity; confirm policy responds before approving sustained load. | Missing policy/sensor/tach or unexpected rise stops the workload. Use offline/synthetic failure cases before any live fault rehearsal; do not defeat trips or stall the fan. | Verify cooling and reporting in Horizon; no new thermal fault or uncontrolled load remains. |
| Buttons / proximity | Record all press/release events; verify proximity near/far after genuine calibration and confirmed MCU protocol, without conflating it with tracked pose. | Missing calibration, stuck input or lost stream produces an explicit unavailable state and bounded exit. | Release input/consumer ownership; verify normal Horizon buttons/proximity and tracking. |
| PM / suspend-resume | Measure actual residency/power and wake sources for runtime PM. Only after prior gates, observe a declared suspend state, wake within deadline and repeat all affected subsystem checks. | Busy device, pending wake, callback failure or missed deadline aborts with original error and restoration evidence. A successful write is insufficient. | Independent recovery survives the boundary; Horizon and every previously accepted Linux subsystem restore over repeated cycles. |

## Pinned source references

All kernel paths below refer to `6bde5fc06b252c97761bc0963dd13c1abcf377ab`.
They establish source behavior, not compatible firmware or working Linux hardware.

- Display: [`techpack/display/Makefile`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/display/Makefile),
  [`config/konadisp.conf`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/display/config/konadisp.conf),
  [`sde_connector.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/display/msm/sde/sde_connector.c),
  [`dsi_panel.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/display/msm/dsi/dsi_panel.c)
  and [`hollywood-panel.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood-panel.dtsi).
- GPU: [`adreno-gpulist.h`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/gpu/msm/adreno-gpulist.h)
  and [`adreno_a6xx_gmu.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/gpu/msm/adreno_a6xx_gmu.c).
  Upstream patch 0006's initial RSC wake can log and discard a wake error; it
  remains deferred under the [startup review](QUEST2_STARTUP_PATCH_REVIEW.md#other-upstream-changes).
- Sensors: [`hollywood-syncboss.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood-syncboss.dtsi),
  [`syncboss.h`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/include/uapi/linux/syncboss.h),
  [`syncboss_powerstate.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/mcu/syncboss/syncboss_powerstate.c),
  [`cam_defs.h`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/include/uapi/media/cam_defs.h)
  and [`cam_isp_context.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_isp/cam_isp_context.c).
  Upstream [`q2_driver.c`](https://github.com/machachu56/quest-kexec-q2/blob/9c96c192d4a4a80f95ba0013294977e85c6eae11/monado/drivers/quest2/q2_driver.c)
  uses heuristic arrival-time mapping, fixed positions and no distortion mesh;
  [`q2_cameras.c`](https://github.com/machachu56/quest-kexec-q2/blob/9c96c192d4a4a80f95ba0013294977e85c6eae11/monado/drivers/quest2/q2_cameras.c)
  feeds frame statistics and discards stop/release results. Neither supplies a
  checked lifecycle or calibrated visual-inertial tracking implementation.
- Networking: [`cnss2/main.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/net/wireless/cnss2/main.c),
  [`qcacld-3.0/Makefile`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/qcacld-3.0/Makefile),
  [`hci_qca.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/bluetooth/hci_qca.c)
  and [`hollywood-qca6390.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood-qca6390.dtsi).
- Audio/storage: [`konaauto.conf`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/config/konaauto.conf),
  [`hollywood-audio.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood-audio.dtsi),
  [`cm710x.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/sound/soc/codecs/cm710x.c),
  [`ak4331.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/sound/soc/codecs/ak4331.c)
  and [`ufshcd.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/scsi/ufs/ufshcd.c).
- Power/input: [`hollywood.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood.dtsi),
  [`qpnp-smb5.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/power/supply/qcom/qpnp-smb5.c),
  [`hollywood-thermal.dtsi`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/boot/dts/oculus/hollywood/hollywood-thermal.dtsi),
  [`pwm-tach-fan.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/pwm-tach-fan.c),
  [`powerstate_mgr.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/powerstate_mgr.c)
  and [`kernel/power/suspend.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/power/suspend.c).

Selected complete-file SHA-256 pins keep the configuration/ABI-sensitive findings
reproducible without treating a source hash as hardware evidence:

| Kernel source path | SHA-256 |
| --- | --- |
| `techpack/display/config/konadisp.conf` | `58374659ded4363e083c2330af7327ad52b189de6fea1bce5d09980ab4926fd6` |
| `techpack/audio/config/konaauto.conf` | `88e92fbcc7e6a0d3e6d9caa3e39663fc5bde4c8f72ac17153853fa0bd845bdc5` |
| `arch/arm64/boot/dts/oculus/hollywood/hollywood-syncboss.dtsi` | `f85307058f1d8811a4743388d453806560e57350b63dfbc41ca22e371c75035a` |
| `drivers/staging/oculus/include/uapi/linux/syncboss.h` | `4c87f74aa345dbce6bd183e601c7de3e4c4b5394c218a7ae4113a4fa10c3e3ce` |
| `techpack/camera/include/uapi/media/cam_defs.h` | `5720a787b5d90f06d4c4d0175fae48abfca8c7419f88c041037f7663684c9dce` |
| `sound/soc/codecs/cm710x.c` | `e231d9a61061ea69bc02254f1def215cd58df79e469848f27a05b7cb943d41c5` |
| `arch/arm64/boot/dts/oculus/hollywood/hollywood-thermal.dtsi` | `eff23bb007e0434a00a0005c8ea86f56fb74a26b5af75b613c33b5f46facd2de` |
| `drivers/scsi/ufs/ufshcd.c` | `55b09f1db71790b453de9114e13caa03a6ce6b82fc09f47efdd55042cb89f311` |

## Retained module-bundle verification

Build/test source revision:
`b56caa2a7e77969cf43cfa4c452a8380dfb79996`. The evidence below covers offline
preparation; it establishes no native hardware functionality.

- Windows: all **442 host tests passed**. Python AST parsing, Bash syntax and
  `git diff --check` passed. The new module suite has 30 tests, including the
  observed debug-section and complete kmod-metadata regression failures/fixes.
- [Ubuntu regression run 39945608678](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39945608678):
  **442 tests passed, no skips**, plus seven existing reader cases under QEMU
  against CI-owned memory. Its seven downloaded files, source/ELF/license hashes
  and source revision were independently checked.
- [Target build 39945605803](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39945605803):
  Image, all **28 modules**, depmod metadata and static ARM64 BusyBox built.
  BusyBox executed under QEMU; all eight rescue-init fixture tests passed there.
  These are emulated userspace tests, not headset enumeration or boot evidence.
- Download verification covered **17 allowlisted files, 16 checksums**, ten
  provenance-bound outputs, exact startup patch/source records and archived
  source bytes. Reconstructing the bundle inputs rechecked every module's ABI,
  provider closure, debug absence and all **40 archive file** hashes and paths.
  Installer build/source links are excluded; the archive contains regular files.
- `target-modules.tar.gz`: **4,058,904 bytes**, SHA-256
  `67b45a9d84c9f3b23e3cb8d777fa534e2cff037b8452ce78e517dcf621e40bc4`.
  All hardware-validation, handoff, deployment, autoload and device-access flags
  remain false; status is `OFFLINE_MODULES_VERIFIED_HANDOFF_BLOCKED`.
- Image and target.config are byte-identical to retained USB build 39909419709.
  The new initramfs has SHA-256
  `1fc877584ae86367cc7546107fc1fafce5a131ce25b8fe3d950ba39867e7bb95`.
  Its only changed CPIO entry is BusyBox: 26 changed ELF bytes comprise the
  20-byte GNU build ID and six timestamp digits in its build-time banner.
  Every other byte, archive entry and functional BusyBox config setting agrees.
  The compressed initramfs is one byte larger than the retained one.

The module-bundle increment did not change hardware drivers, target config,
rescue init or boot gates. Neither it nor the CPU-default increment performed
a headset command, module loading, Linux boot, shutdown/recovery test or
boot-policy change. The first-boot gates and all physical subsystem acceptance
tests above remain open.
