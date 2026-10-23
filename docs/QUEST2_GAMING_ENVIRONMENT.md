# Quest 2 gaming development userspace

**This is a custom ARM64 Linux development environment, not official SteamOS.
No game, Steam client or VR application has been validated on this Quest 2.**
Native boot, checked camera/DSP/DMA shutdown, independent recovery and the
[hardware](QUEST2_LINUX_HARDWARE.md) / [VR](QUEST2_VR_TRACKING.md) prerequisites
remain open. This work changes no kernel, initramfs, loader gate or boot default.

## Selected integration

The separate [container recipe](../tools/userspace/Containerfile) builds Debian
12 ARM64/glibc userspace. It uses the official ARM64 image digest
`991827bb788d78bbc130dcb851913beecff43296360f48838aca7cbdfae4375c` and signed
Debian main/security packages from snapshot `20261022T000000Z`. Package expiry is disabled
for that historical snapshot; package signature verification remains enabled.
Installed binary/source package versions are retained in `packages.tsv`.
This dated development base needs an update policy before normal use; it is
not an assertion that every installed package is currently supported or secure.

Weston supplies a small Wayland desktop with a Freedoom launcher. Its headless
pixman backend and SDL software rendering provide a device-free integration
check. This is not Steam Gaming Mode, gamescope, a headset display driver or
an accelerated desktop. Debian Mesa libraries are installed for development;
they do not establish the required Quest KGSL/Turnip Vulkan and Zink OpenGL path.
[Zink capabilities depend on the underlying Vulkan driver](https://docs.mesa3d.org/drivers/zink.html).
The separately built Vulkan headers are build interfaces, not a GPU driver.

The runtime build includes machachu56's Quest 2 Monado overlay and both upstream
Monado registration/presentation patches. It applies this repository's
[controller patch](../patches/monado/quest2/0001-controller-clock-and-stick-bounds.patch)
before compiling the complete service and OpenXR runtime. Driver selection,
the actual Quest controller object and installed ELF/dependency closure are
checked. Systemd activation, active-runtime registration, OpenVR and the SteamVR
plugin are disabled. The service is **not started** and no HMD is emulated.
Compilation does not cure the tracking/optics/safety defects in the VR matrix.

| Source | Exact revision |
| --- | --- |
| Monado | `019d9d45869d6ae67c5195540793e5c43687c1dd` |
| Quest 2 overlay and upstream patches | `9c96c192d4a4a80f95ba0013294977e85c6eae11` |
| Box64 v0.4.4 | `2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a` |
| Vulkan-Headers v1.4.328 | `9a0f3099c8a9607a7c0f3127d8abfdc19a93e8c5` |

Box64 is built with `ARM_DYNAREC=OFF` and `BOX32=OFF`, with both cache settings
checked. The proposed test is a real dynamically linked x86-64 ELF that calls
libc, receives a literal argument and returns a known nonzero status through
the actual ARM64 interpreter. This proves neither game performance nor 32-bit,
Steam, Wine, Proton or cross-architecture graphics/VR compatibility. No supported
minimum Quest kernel is inferred from Box64 running on the CI host kernel.
See [the pinned Box64 source](https://github.com/ptitSeb/box64/tree/2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a).

## Steam and compatibility boundaries

Valve now documents ARM64 native applications, SteamVR/OpenXR and Proton/FEX
for **Steam Frame**, including an ARM64 Steam Linux Runtime. Thus ARM64 Steam
support should not be described as universally absent. Those Frame execution
paths do not certify Quest 2 hardware, this kernel or this userspace.
See [Frame execution models](https://partner.steamgames.com/doc/steamhardware/steamframe/compatibility)
and [ARM64 runtime deployment](https://partner.steamgames.com/doc/steamhardware/steamframe/loadgames).
Quest 2 is not identified as a supported device in
[Valve's SteamOS installation policy](https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227).
No Valve image, Steam client, credentials or proprietary game content is bundled.

Current FEX requires Linux **5.15**; its previous floor was **5.0**.
The project's Linux target is **4.19.325**, so neither floor is met.
Installing FEX or supplying 4 KiB pages does not close that gap. See
[FEX's kernel requirement change](https://fex-emu.com/FEX-2501/).
Changing the kernel would require a separate board-driver/handoff/recovery port.

For x86-64 Linux applications, the Box64 lane must progress from libc to real
game libraries, Vulkan/OpenGL/SDL wrapping and application-specific tests.
Legacy 32-bit dependencies need their own proven path; they are disabled here.
Windows titles additionally require a compatible Wine/Proton environment,
graphics translation and any game-specific services. No such title is confirmed.
Native ARM64 OpenXR applications need a working ARM64 runtime and actual headset
integration. Translated OpenXR/OpenVR software needs a demonstrated ABI-correct
loader/wrapper or proxy path; assigning an ARM64 runtime path alone is not proof.

## Application and runtime matrix

CI evidence is distinct from headset compatibility. **No Quest row is passing.**
The initial build and software checks are pending until their run is recorded.

| Application / component | CI scope | Quest 2 result / remaining requirement |
| --- | --- | --- |
| Debian 12 ARM64 base | Pinned build recipe; pending integration run | Untested: boot, kernel API/configuration, init, storage and service integration |
| Weston / Wayland desktop | Headless pixman + readiness roundtrip; pending run | Untested: real display, input devices, stereo/optics and recovery |
| Chocolate Doom + Freedoom | Native ARM64 E1M1 software scene and frame callbacks; pending run | Untested: controls, audio, complete campaign, acceleration and performance |
| Box64 x86-64 libc fixture | Actual interpreter / dynamic ELF / literal argument / status; pending run | Untested: target kernel, real libraries, graphics and games |
| Monado Quest service / OpenXR library | Full build, architecture, manifest and dependency checks; pending run | Blocked: no native VR session, calibrated tracking, display timing or safety |
| Native ARM64 OpenXR game | Not launched | Blocked by the native VR prerequisites |
| Steam ARM64 / Steam Frame environment | Not installed | Unvalidated on Quest; Frame support is not transferable evidence |
| Legacy Steam x86 client / 32-bit dependencies | Not installed | No validated 32-bit path or client startup |
| FEX / Steam Frame Proton path | Not installed | Blocked by documented kernel minimum, plus remaining platform dependencies |
| Windows Wine/Proton games | Not installed | No tested Windows application or graphics/VR bridge |
| OpenVR / SteamVR / gamescope | Not built or launched | No tested compositor, ABI bridge or headset integration |

## Build and evidence

The owner-only manual [userspace workflow](../.github/workflows/quest2-userspace-build.yml)
builds the ARM64 image with official Ubuntu QEMU/binfmt packages. It can also be
called through the existing sparse-reader workflow using `gaming_userspace=true`;
the default remains false. The matching source commit must be selected.
On an existing Linux Docker host with ARM64 binfmt enabled:

```sh
bash tools/ci/quest2_userspace_build.sh
```

This builds an isolated development image and emits `ci-userspace-artifacts/`.
The compiled `userspace-overlay.tar.gz` contains `/usr/local`, **not a standalone
rootfs or installer**. Use the same recipe/base/packages; the overlay is not
portable to musl or arbitrary glibc environments. Source archives, original
licenses, the controller patch, CMake caches, package versions, hashes and build
logs accompany it. Debian package binaries/base image are not redistributed in
that overlay. It has no headset deployment or autostart command.

The runtime smoke test runs as an unprivileged container user, without networking
or host devices, on a read-only image with temporary writable directories.
It starts its own Weston instance, requires a Wayland readiness roundtrip,
launches the real game with literal arguments, observes at least three frame
callbacks/commits, then terminates and checks cleanup. Audio is disabled and
SDL's dummy audio driver is selected. Scene-check elapsed time is CI test
duration, not Quest FPS, latency, memory use, battery drain or thermal evidence.
Monado is inspected but never launched by this check.

The GUI launcher uses separate Bash job groups for its compositor and client,
with inherited groups inside each job and no terminal stdin. Cleanup terminates
ordinary descendants in those groups, escalates after a finite wait and keeps
the original client/signal status. Readiness probes have a hard kill timeout.
Only callbacks requested by `wl_surface.frame` count as delivered frames;
`wl_display.sync` callbacks are excluded, including reused callback IDs.

## Remaining on-headset validation

First establish native boot, source-owned shutdown/DMA/watchdog handling and
independently observed Horizon recovery. Then validate a reversible installation
and launch/exit lifecycle, real controller/keyboard/mouse mappings, audio routes,
networking/downloads, storage integrity, memory pressure and measured performance
and thermals under a complete cooling policy. Record firmware, kernel, payload,
application/version, settings, duration and recovery for every result. Confirm
accelerated APIs and native VR independently before attempting translated games.
No persistent rootfs, partition, userdata write, game benchmark or headset
operation is performed by this offline build recipe.

Full prompt 07 remains open until those physical results exist. Verification
evidence for this increment will be added after the actual build and review.
