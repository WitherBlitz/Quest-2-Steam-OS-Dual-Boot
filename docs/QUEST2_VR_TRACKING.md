# Quest 2 VR tracking and presentation

**Full native VR is blocked. No calibrated 6DoF, controller tracking, optical
presentation or safety feature has been demonstrated under this Linux target.**
The [first Linux boot](QUEST2_FIRST_LINUX_BOOT.md), checked stock shutdown/DMA
ownership and independent recovery prerequisites remain open. There is no
integrated Monado runtime in the current RAM-only USB rescue image.

This increment fixes two controller source defects and records the complete
requested experience. It supplies a development patch against the existing
upstream driver, not a replacement tracker or completed Quest 2 runtime.

## Reference and scope

The reference is machachu56's
[`quest2-port` source at 9c96c192](https://github.com/machachu56/quest-kexec-q2/tree/9c96c192d4a4a80f95ba0013294977e85c6eae11),
with upstream Monado base `019d9d45869d6ae67c5195540793e5c43687c1dd`.
Primary inspected files are
[`q2_driver.c`](https://github.com/machachu56/quest-kexec-q2/blob/9c96c192d4a4a80f95ba0013294977e85c6eae11/monado/drivers/quest2/q2_driver.c),
[`q2_cameras.c`](https://github.com/machachu56/quest-kexec-q2/blob/9c96c192d4a4a80f95ba0013294977e85c6eae11/monado/drivers/quest2/q2_cameras.c),
the target builder, camera-trace/protocol notes, gamescope patches and
[`build-userspace.sh`](https://github.com/machachu56/quest-kexec-q2/blob/9c96c192d4a4a80f95ba0013294977e85c6eae11/tools/build-userspace.sh).
The original project and its contributors retain credit for this code.

Source availability, host tests and upstream demonstrations are distinct from
operation on this project's native target. **Working** requires measured native
operation and recovery; **Partial** requires observed native operation with a
stated limitation. No feature below qualifies for either status. **Blocked**
identifies a missing dependency/implementation; **Untested** identifies source
preparation without physical evidence. Every row also inherits the boot block.

## Feature matrix

| Requested feature | Native status | Source preparation and missing work |
| --- | --- | --- |
| Calibrated 6DoF head tracking | Blocked | Driver uses `m_imu_3dof` and a fixed `(0,1.6,0)` position; position tracking is explicitly disabled. No visual-inertial pose pipeline is connected. Genuine intrinsics/extrinsics, IMU calibration, synchronized observations, mapping and relocalization are required. |
| Head orientation | Untested | Inertial fusion and a stationary gyro-bias heuristic exist. Factory-axis assumptions, bias/scale, drift, gravity behavior and physical accuracy remain unvalidated. This is a 3DoF source experiment. |
| Camera/IMU synchronization | Blocked | Arrival-offset heuristic maps IMU time; camera `sof`/`ts` are copied into frame timestamps. No demonstrated common clock, calibrated offset/drift or camera-to-IMU correlation exists. The [Syncboss timing mismatch](QUEST2_LINUX_HARDWARE.md#module-preparation-boundary) also remains unresolved. |
| Controller position | Blocked | Hands are held at fixed left/right positions. No optical LED constellation solution, per-controller geometry/calibration or controller-to-world pose is supplied. |
| Controller orientation | Untested | Mirrored IMU-axis assumptions and 3DoF fusion exist. This patch fixes counter unwrapping; calibration, optical correction, drift and valid tracking state still need work. |
| Buttons | Untested | A/X, B/Y, stick click and menu/system bits are decoded. Physical mapping, pressed-state release on loss and correct sample timestamps remain unverified. |
| Sticks and triggers/grip | Untested | Packed analog values are decoded. The patch bounds both stick endpoints. Device calibration, deadzones, full travel and timing still need physical validation. |
| Capacitive touch | Blocked | Source substitutes presses/analog thresholds for touch; record `0x45` is not decoded. Presses cannot certify capacitive sensing. |
| Haptics | Blocked | Output callback returns success while sending no haptic command. Implement and acknowledge the actual protocol, cancellation and bounded duration before advertising support. |
| Pairing | Blocked | Starting the radio is not a pairing implementation. The Monado overlay selects by handedness and ignores controller identity/link fields. A verified pairing/identity lifecycle is required. |
| Reconnection | Blocked | No checked disconnect/reconnect state machine resets all clock/fusion/input state. Counter unwrapping alone does not identify controller replacement or counter resets. |
| Tracking-loss recovery | Blocked | Controller pose flags remain tracked before first motion and after loss; input timestamps use polling time. Needs freshness/validity policy, cleared stale inputs, relocalization and measured recovery. |
| Recentering / reference spaces | Blocked | No Quest 2 recenter action or recovery flow was found in the overlay. Generic reference-space APIs do not establish a usable recenter button or consistent standing/seated origin. |
| Lens distortion / chromatic correction | Blocked | Driver explicitly selects `u_distortion_mesh_set_none` with approximate 97°/93° FOV. Genuine optical calibration and a tested per-eye distortion model/mesh are absent. |
| Eye geometry / physical IPD | Blocked | Generic view-pose helper and hardcoded geometry are used. No calibrated eye transforms or physical IPD-setting integration was found. Controller/IMU calibration is not lens/IPD calibration. |
| Stereo rendering / eye assignment | Untested | Upstream prepares 3664×1920 side-by-side views and a gamescope panel-half mapping, including an eye-swap option. Correct per-eye scanout, rotation, geometry and optical presentation remain unobserved. |
| Frame timing / display rates | Blocked | Driver hardcodes nominal 120 Hz and launcher selects mailbox presentation. Actual KMS modes, vsync/scanout timing, pacing, missed frames and motion-to-photon latency are unmeasured. |
| Pose prediction | Blocked | HMD queries relation history at the requested time; controller callback ignores that time. No calibrated prediction horizon, sensor-age budget or accuracy evidence exists. |
| Reprojection | Untested | A generic Monado compositor is an available source dependency. This overlay supplies no end-to-end Quest 2 proof with calibrated optics, display timing and valid predicted poses. |
| Boundary / safety UI | Blocked | No native boundary/Guardian replacement is implemented. It needs reliable positional tracking, persistent coordinate handling, system-level overlays and tested behavior on loss; a drawn grid or fixed pose is insufficient. |
| Passthrough | Blocked | Camera frames currently feed statistics/dumps. No calibrated camera-to-eye projection, compositor path, latency handling or failure-state UI is implemented. Raw grayscale preview would not certify passthrough. |
| Hand tracking | Blocked | No hand-tracking pipeline is connected. Calibrated camera streams, compatible models/runtime, clock correlation and ARM64 performance validation are missing. |

OpenXR distinguishes a valid estimate from actively tracked data; a known or
inferred location can remain valid while tracking bits are cleared. A fixed
fallback position with `POSITION_VALID` therefore must not be reported as
measured positional tracking or 6DoF. See the
[OpenXR space-location rules](https://registry.khronos.org/OpenXR/specs/1.0-khr/html/xrspec.html#spaces).

## Controller source patch

[`0001-controller-clock-and-stick-bounds.patch`](../patches/monado/quest2/0001-controller-clock-and-stick-bounds.patch)
targets the exact reference `monado/drivers/quest2/q2_driver.c`. Apply it to that
upstream source before its userspace build copies the driver into Monado. The
current USB kernel/initramfs workflow does not build or deploy this driver.

- A raw 32-bit microsecond counter wraps every 4,294.967296 seconds. Previously
  the raw value jumped backward and the driver clamped fusion time to 1 ns steps.
  An initialized 64-bit counter now accumulates accepted modular deltas.
- Only `0 < delta < 2^31` is accepted after the first explicit timestamp.
  Duplicates, backwards/ambiguous samples and overflow in the extended
  microseconds-to-nanoseconds multiplication are refused before clock or fusion
  state is advanced. Zero is a valid first timestamp.
- Timestamp-free motion requires an established epoch. The existing assumed
  2,000 µs increment is retained, including across wrap; it is not measured
  camera synchronization. Packets cannot create an epoch from that assumption.
- `INT16_MIN` maps to exactly −1 for each stick axis. Other scaling is preserved.

The half-range rule cannot distinguish every reset from wrap. Gaps of at least
2,147.483648 seconds are ambiguous; establishing a new controller session/epoch
requires the still-missing identity/reconnection protocol. Existing arrival-time
mapping and its monotonic clamp remain heuristic. This patch adds no 6DoF,
freshness/loss handling, optical calibration, haptics or camera synchronization.

The byte-exact full upstream fixture retains its copyright and BSL-1.0 marking;
the accompanying [Boost license](../tools/tests/fixtures/LICENSE.BSL-1.0) applies
to that source and derived patch. It is not relicensed as project-authored code.

| Input | SHA-256 |
| --- | --- |
| Original `q2_driver.c` | `929e98f59a5f43604ca30f3ac2eddedf9f7fe7117991b9a484d2f0b8be96b9d7` |
| Patched `q2_driver.c` | `d00046ac41d69829ce0b2ef4d3403bbd260e5eb5624261611aa3cd4b7bf878e8` |
| Patch (LF form) | `a4d80b8a911d9d93e3b1d01d814a2cb83225abe24ee7f5e8a00c2b83db93aa41` |

## Dependencies and feasible replacement work

The proprietary Android tracking/camera stack and its recorded startup behavior
are not a portable native tracking implementation. The upstream notes reference
factory IMU and controller `TrackedObject` calibration, but those artifacts are
absent from its tree. Per-device camera, IMU, controller and optical calibration
must have known provenance and compatible formats; none is supplied or fabricated
by this change. A guessed or copied device calibration cannot establish accuracy.

An open visual-inertial implementation is a possible engineering path. Monado
documents Basalt-based inside-out tracking for other headsets on its
[developer site](https://monado.freedesktop.org/). Applying that path to Quest 2
would require calibrated camera/IMU plumbing, timestamp validation, mapping and
relocalization work; current support on other headsets is not Quest 2 support.

Monado's [Mercury hand-tracking documentation](https://monado.freedesktop.org/handtracking)
describes calibrated stereo inputs, OpenCV and ONNX Runtime dependencies. Its
listed hardware does not establish Quest 2 compatibility. The pinned upstream
Quest 2 build disables OpenCV. An ARM64 model/runtime integration and measured
performance would be new work, not a feature enabled by this patch.

An independent boundary system, calibrated grayscale passthrough and a lens
model are also separate implementations. They depend on trustworthy poses,
geometry, camera ownership and compositor timing. No replacement is claimed to
match Meta's proprietary safety behavior or complete feature set.

## Known source defects still open

- Camera script parsing can read a record kind after the buffer end, dereference
  a zero-length Syncboss blob and access operation payloads without sufficient
  size checks. `offset + len` can overflow in mapped-buffer checks.
- Camera image footprint bounds, thread synchronization/allocation failures and
  checked stop/release completion need work. `volatile` alone is not a thread
  synchronization mechanism. Frame delivery/statistics do not establish SLAM.
- Pose/input freshness, controller identity/reconnection, fake haptic success
  and capacitive decoding remain unresolved as described in the matrix.
- The upstream VR launcher collapses arguments into shell source, can continue
  after socket timeout and lacks a complete cleanup protocol. It is not imported
  or used by this increment.

## Physical validation still required

Close the [boot/recovery prerequisites](QUEST2_FIRST_LINUX_BOOT.md#remaining-gates-and-observed-state)
and [hardware dependencies](QUEST2_LINUX_HARDWARE.md) before native VR tests.
Use source-owned, bounded camera/DSP/Syncboss lifecycles and preserve original
errors. Firmware/calibration identity, payload hashes and the chosen display
mode must accompany each result. Proposed deadlines in the hardware checklist
are test limits, not measured device performance.

1. Validate sensor packet ordering, units, axes and clock residual/drift with
   independent reference observations. Record drops, resets and timestamp age.
2. Validate calibrated head/controller translations and rotations, stationary
   drift, controller controls/haptics and optical alignment. Report positional
   and angular errors against a declared acceptance budget. Fixed positions,
   frame counters and a working gyro do not satisfy this test.
3. Declare and measure refresh rate, prediction horizon, frame-time distribution
   and motion-to-photon latency. Validate both eyes, IPD settings and distortion;
   a compositor process or successful modeset is not optical evidence.
4. Exercise occlusion, low light, controller removal/reconnect, stale data and
   pipeline failure. Verify appropriate valid/tracked flags, stale-input release,
   recentering and consistent reference spaces before enabling safety overlays.
5. Validate boundary/passthrough/hand behavior and loss UI separately. Retain
   diagnostics and independently observe recovery to usable Horizon with normal
   head/controller tracking after each approved test. Repeated normal and failure
   cycles are required before automatic VR startup.

No headset tracking measurement, native VR session or recovery test has been
performed for this increment. Full prompt 06 remains blocked on those results.

## Verification for this increment

Twelve native C fixture cases compile and call the actual patched controller
structure, packet parser, motion handler and time mapper. Only the external
Monado fusion endpoint is replaced to observe delivered samples; no physical
fusion accuracy or pose is estimated by the harness. Ten cases failed against
the pristine source; all twelve pass with the patch. They cover wrap, missing/
zero epochs, duplicates/backwards/ambiguous gaps, independent controllers,
integer overflow, all 65,536 signed stick inputs and truncated decoded records.

Patch check/application/reverse-check also passed against bytes read directly
from the pinned upstream Git object, independently matching the fixture hash.
The Windows regression suite passed all **454 tests** with LLVM 23/MSVC. At
source commit `9a6f6be8197f410a438f762d09e5691a5103d965`,
[Ubuntu CI 39952790877](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39952790877)
also passed all **454 tests without skips**, including the twelve controller
cases. Its seven existing AArch64 reader/QEMU checks passed against CI-owned
memory; these exercise the reader, not a VR runtime. The downloaded seven-file
evidence bundle matched the source commit, reader source/ELF and license hashes.

Independent source/document review reported no Critical or Important findings.
Python syntax, relative document links, source/patch hashes, exact README credit
and staged whitespace checks passed. Unified-diff context prefixes follow the
existing patch attribute convention; `git apply --whitespace=error` checks the
patch's actual added code. No full Monado build, ARM64 VR execution, native
tracking accuracy, optics, safety behavior or headset recovery is validated by
these checks. The kernel/initramfs payloads and boot gates are unchanged.
