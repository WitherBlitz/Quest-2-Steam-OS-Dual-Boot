#!/usr/bin/env python3
"""Passive evidence for one Quest 2 firmware; never stops devices or tests reset."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from types import MappingProxyType

from quest2_preflight import CaptureError, collect_identity, write_json
from quest2_shutdown_source import KERNEL, audit_sources

MAX_READ = 2 * 1024 * 1024
PROBES = MappingProxyType({
    "boot": "cat /proc/sys/kernel/random/boot_id",
    "cpus": "cat /sys/devices/system/cpu/online",
    "taint": "cat /proc/sys/kernel/tainted",
    "modules": "cat /proc/modules",
    "panic": "cat /proc/sys/kernel/panic",
    "watchdog": 'for a in disable pet_time; do p=/sys/bus/platform/devices/17c10000.qcom,wdt/$a; printf "%s=" "$a"; if [ -r "$p" ]; then cat "$p"; else printf "MISSING\\n"; fi; done',
    "dsp": 'n=0; for p in /sys/bus/msm_subsys/devices/*; do [ -d "$p" ] || continue; n=$((n+1)); [ "$n" -le 64 ] || exit 2; printf "device=%s\\n" "${p##*/}"; for a in name state; do printf "%s=" "$a"; if [ -r "$p/$a" ]; then cat "$p/$a"; else printf "MISSING\\n"; fi; done; done; [ "$n" -ne 0 ] || printf "MISSING\\n"',
    "bindings": 'n=0; for p in /sys/bus/platform/devices/17c10000.qcom,wdt/driver /sys/bus/platform/devices/*cam*/driver /sys/bus/platform/devices/*smmu*/driver; do [ -L "$p" ] || continue; n=$((n+1)); [ "$n" -le 128 ] || exit 2; printf "%s=" "$p"; readlink "$p" || exit 2; done; [ "$n" -ne 0 ] || printf "MISSING\\n"',
    "camera": 'n=0; for p in /sys/class/video4linux/*/name; do [ -r "$p" ] || continue; n=$((n+1)); [ "$n" -le 64 ] || exit 2; printf "%s=" "$p"; cat "$p" || exit 2; done; [ "$n" -ne 0 ] || printf "MISSING\\n"',
    "iommu": 'n=0; for p in /sys/kernel/iommu_groups/*/devices/*; do [ -L "$p" ] || continue; n=$((n+1)); [ "$n" -le 256 ] || exit 2; printf "%s=" "$p"; readlink "$p" || exit 2; done; [ "$n" -ne 0 ] || printf "MISSING\\n"',
})


def remote_command(probe):
    if probe not in PROBES:
        raise ValueError("unknown passive shutdown probe")
    body = "( set -e; " + PROBES[probe] + ' ); qkx_status=$?; printf "\\nQKX_PASSIVE_STATUS=%s\\n" "$qkx_status"'
    return "su -c " + shlex.quote(body)


def read(adb, serial, probe, *, runner=subprocess.run):
    command = remote_command(probe)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial):
        raise ValueError("explicit USB serial required")
    try:
        result = runner([adb, "-s", serial, "exec-out", command], shell=False,
                        capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("passive read failed: " + probe) from exc
    if result.returncode or not result.stdout or len(result.stdout) > MAX_READ+128:
        raise CaptureError("empty, oversized or failed passive read: " + probe)
    data, separator, status = result.stdout.rpartition(b"\nQKX_PASSIVE_STATUS=")
    if not separator or status != b"0\n" or not data or len(data) > MAX_READ:
        raise CaptureError("missing/failed remote completion or oversized read: " + probe)
    try:
        data.decode("utf-8")
    except UnicodeError as exc:
        raise CaptureError("invalid passive text: " + probe) from exc
    return data


def module_names(data):
    return sorted(line.split()[0] for line in data.decode().splitlines() if line.strip())


def collect(adb, serial, out, source_report, *, runner=subprocess.run):
    if source_report.get("source_verified") is not True or source_report.get("kernel_commit") != KERNEL:
        raise CaptureError("pinned public source audit required")
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("new private output directory required") from exc
    marker = out / "INCOMPLETE.json"
    artifacts = {}

    def save(probe, label=None):
        data = read(adb, serial, probe, runner=runner)
        name = label or probe
        filename = name + ".txt"
        with (out / filename).open("xb") as stream: stream.write(data)
        artifacts[name] = {"filename": filename, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
        return data

    try:
        write_json(marker, {"capture_status": "incomplete"})
        identity = collect_identity(adb, serial, runner=runner)
        before = {key: save(key, key+"-before") for key in ("boot", "cpus", "taint", "modules")}
        if not re.fullmatch(rb"[a-fA-F0-9-]{36}\s*", before["boot"]):
            raise CaptureError("valid boot ID required")
        if set(module_names(before["modules"])) & {"quest_kexec", "marker_read", "usb_log"}:
            raise CaptureError("project module already resident")
        state = {key: save(key) for key in PROBES if key not in before}
        after = {key: save(key, key+"-after") for key in ("cpus", "taint", "modules")}
        final = collect_identity(adb, serial, runner=runner)
        after["boot"] = save("boot", "boot-after")
        if (identity != final or any(before[k].strip() != after[k].strip() for k in ("boot", "cpus", "taint"))
                or module_names(before["modules"]) != module_names(after["modules"])):
            raise CaptureError("boot, identity or passive health changed during collection")
        panic = state["panic"].decode().strip()
        if not re.fullmatch(r"-?\d+", panic):
            raise CaptureError("invalid kernel panic timeout")
        report = {"schema_version": 1, "capture_status": "complete", "identity": identity,
                  "source_audit": source_report, "artifacts": artifacts,
                  "panic_timeout_seconds": int(panic), "same_boot_cpus_modules_taint": True,
                  "scope": "Passive software/sysfs observations; no hardware state or recovery proof",
                  "device_mutation": False, "watchdog_hardware_state": "unproven",
                  "secure_watchdog_state": "unproven", "recovery_validated": False,
                  "camera_shutdown_validated": False, "dsp_shutdown_validated": False,
                  "dma_quiescence_validated": False, "handoff_ready": False, "status": "HANDOFF_BLOCKED"}
        with (out / "capture.tmp").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
        marker.unlink()
        (out / "capture.tmp").replace(out / "capture.json")
        return report
    except (OSError, ValueError, CaptureError) as exc:
        write_json(marker, {"capture_status": "incomplete", "error": str(exc)})
        raise CaptureError(str(exc), output_owned=True) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--kernel-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = audit_sources(args.kernel_source)
        collect(args.adb, args.serial, args.output, source)
    except (OSError, ValueError, CaptureError) as exc:
        parser.exit(2, f"PASSIVE_CAPTURE_INCOMPLETE; HANDOFF_BLOCKED: {exc}\n")
    print("PASSIVE_CAPTURE_COMPLETE; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__": raise SystemExit(main())
