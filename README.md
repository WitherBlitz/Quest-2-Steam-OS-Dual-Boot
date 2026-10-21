**To improve and hopefully finish  the goat who made this, feel free to use my improvements on the main branch or this code in any other way machachu56.**

**To whoever is looking at this, this is MASSIVELY incomplete! Don't expect this to be in a week or anything either.**

# Quest 2 Steam OS Port

The original project, [quest-kexec-q2](https://github.com/machachu56/quest-kexec-q2),
was created by [machachu56](https://github.com/machachu56). This repository
continues that work with the Quest 2 port improvements.

The original Git history and branches are preserved. See the
[migration record and archived build evidence](docs/REPOSITORY_MIGRATION.md).

> **Quest 2 firmware-specific development branch:** default builds stage only;
> hardware tests and jumping are disabled. The matching rebuild and ABI checks
> passed; physical handoff remains unvalidated.
> Read [the port status and verification limits](docs/QUEST2_LOADER_STATUS.md)
> before using the legacy upstream build or boot instructions below.

A kernel module for Meta Quest headsets that allows you to load a custom kernel without unlocking the bootloader of the headset. Based on [kexec-at-home](https://github.com/provide-salad/kexec-at-home)

> **Note:** This project has only been developed and tested on Meta Quest Pro.
> Other Quest headsets require device specific kernel and loader work.
> **ROOT IS REQUIRED**

A modified kernel for this kexec for the Quest Pro is available [from the repository here](https://github.com/YusufOzmen01/oculus-linux-kernel)
The changes done to the kernel are in `kernel/patches/` as patches. 

## Basic build

Install Clang/LLD, an `aarch64-linux-gnu-` toolchain, Python 3, `adb`, `dtc`, `fdtput`, `curl` and `make`.

Build the kexec modules against the exact Android kernel running on the headset:

```sh
make module KDIR=/path/to/android-kernel-out LOCALVERSION=-g49638c7a8637
```

To build a basic initramfs that has busybox and USB ethernet (to get shell), run:

```sh
make initramfs
```

See [HOW_TO_BUILD.md](HOW_TO_BUILD.md) for preparing the Android and target
kernel trees.

## Basic usage

```sh
# Capture the current runtime device tree and kernel information
tools/capture.sh out/captured

# Prepare the target payload
tools/prep.sh out/captured /path/to/target/Image out/initramfs.gz out/payload

# Push, verify and boot it
tools/run.sh out/payload
```

Connect to the shell after USB Ethernet appears:

```sh
nmcli device connect <interface>
tools/shell.sh
```

Return to Android:

```sh
tools/return-android.sh
```

See [HOW_TO_USE.md](HOW_TO_USE.md) for more information.

## Quest 2 read-only preflight (host tool)

`tools/quest2_preflight.py` collects readiness evidence only for `hollywood`,
build `52242990024200150`, kernel `4.19.325-cip128-st12-g49638c7a8637`.
It requires Python 3.11+, an existing ADB executable and existing root access.
Every request includes the supplied USB serial; a different device/build/kernel
is rejected before requesting root or binary artifacts.

```sh
python3 -B tools/quest2_preflight.py --adb /path/to/adb \
  --serial YOUR_USB_SERIAL --out /path/to/new-capture
```

Use a new output directory. The tool saves a runtime device-tree tar (never
extracted), `/proc/iomem`, `/proc/cmdline` and `/proc/config.gz`; these captures
may contain device-specific data and should remain private. Transfers time out
after 15 seconds each. Empty or oversized artifacts (64 MiB) and invalid tar or
gzip data are rejected; expanded config is limited to 4 MiB. The size check is
after subprocess output is buffered, so it is not a peak-memory limit.

`capture.json` is published after validation and an end-of-capture identity
recheck. It records filenames, sizes and SHA-256 hashes. Failure leaves an
`INCOMPLETE.json` marker and no complete manifest. `report.json` keeps identity
agreement separate from unproven kernel-build/CRC, loader, memory, recovery,
shared boot-state and VR hardware requirements.

- Exit **2**: invalid input or incomplete/failed capture.
- Exit **3** with `CAPTURE_COMPLETE; HANDOFF_BLOCKED`: capture complete, Linux
  handoff still blocked. This is the expected Stage 1 result.

Stage 1 always reports `handoff_ready: false` and `auto_boot_allowed: false`.
It never reboots, exploits root, inserts modules or changes boot behavior.
A matching identity or successful collection does not establish Quest 2 kexec,
tracking, recovery or SteamOS support. The Quest Pro support statement above
continues to describe the upstream loader.

Host verification (no headset needed; Python 3.11+ and a native GCC/Clang C
compiler are required; set `HOST_TEST_CC` if it is not on PATH):

```sh
python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
python3 -m py_compile tools/quest2_preflight.py tools/tests/test_quest2_preflight.py
git diff --check
```

The suite includes host compilation of loader control flow with kernel API
doubles. It does not build a kernel module or validate hardware behavior.
There is no configured Python linter for this tool.

## Logs

Quest 2 retained-log access is blocked on this branch because the fixed address
overlaps ramoops pmsg. No replacement log region has been established.

Read a retained target-kernel log after Android returns:

```sh
tools/read-log.sh
```

Capture Android kernel logs through its current USB gadget:

```sh
tools/load-usb-log.sh 60
```

## Safety

A failed handoff can reboot or lock the headset. The supplied scripts only copy
files to `/data/local/tmp`; they do not flash, format or mount partitions.

Note: AI has been used heavily on this project

## License

GPL-2.0. See `LICENSE`.
