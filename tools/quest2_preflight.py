#!/usr/bin/env python3
"""Read-only readiness evidence for one exact Quest 2 firmware/kernel.

Collecting evidence never authorizes loading a module or booting Linux.
"""
import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType


PROBES = MappingProxyType({
    "device": ("shell", "getprop", "ro.product.device"),
    "build": ("shell", "getprop", "ro.build.version.incremental"),
    "kernel": ("shell", "uname", "-r"),
    "slot": ("shell", "getprop", "ro.boot.slot_suffix"),
    "verified_boot": ("shell", "getprop", "ro.boot.verifiedbootstate"),
    "vbmeta_state": ("shell", "getprop", "ro.boot.vbmeta.device_state"),
    "boot_complete": ("shell", "getprop", "sys.boot_completed"),
    "boot_reason": ("shell", "getprop", "ro.boot.bootreason"),
    "root_uid": ("shell", "su", "-c", "id"),
    "runtime_tree": ("exec-out", "su -c 'tar -C /proc/device-tree -cf - .'"),
    "iomem": ("exec-out", "su -c 'cat /proc/iomem'"),
    "cmdline": ("exec-out", "su -c 'cat /proc/cmdline'"),
    "config": ("exec-out", "su -c 'cat /proc/config.gz'"),
})
EXPECTED = MappingProxyType({
    "device": "hollywood",
    "build": "52242990024200150",
    "kernel": "4.19.325-cip128-st12-g49638c7a8637",
})
ARTIFACTS = MappingProxyType({
    "runtime_tree": "runtime-tree.tar",
    "iomem": "iomem.txt",
    "cmdline": "cmdline.txt",
    "config": "config.gz",
})
MAX_ARTIFACT = 64 * 1024 * 1024
MAX_CONFIG = 4 * 1024 * 1024


class CaptureError(Exception):
    """Evidence collection did not complete."""

    def __init__(self, message, *, identity=None, output_owned=False):
        super().__init__(message)
        self.identity = dict(identity or {})
        self.output_owned = output_owned


def adb_read(adb: str, serial: str, probe: str, *, runner=subprocess.run) -> bytes:
    if probe not in PROBES:
        raise ValueError("unknown read-only probe")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial):
        raise ValueError("an explicit USB serial is required")
    try:
        result = runner([adb, "-s", serial, *PROBES[probe]], shell=False,
                        capture_output=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise CaptureError(f"{probe}: process failed ({type(exc).__name__})") from exc
    if result.returncode != 0:
        raise CaptureError(f"{probe}: adb exit {result.returncode}")
    return result.stdout


def read_identity(adb, serial, keys, runner):
    identity = {}
    for key in keys:
        try:
            identity[key] = adb_read(adb, serial, key, runner=runner).decode("utf-8").strip()
        except (CaptureError, UnicodeError) as exc:
            raise CaptureError(f"{key}: identity unavailable", identity=identity) from exc
    return identity


def collect_identity(adb: str, serial: str, *, runner=subprocess.run) -> dict[str, str]:
    identity = read_identity(adb, serial, EXPECTED, runner)
    if any(identity[key] != value for key, value in EXPECTED.items()):
        raise CaptureError("target_identity_mismatch", identity=identity)
    identity.update(read_identity(adb, serial, (
        "slot", "verified_boot", "vbmeta_state", "boot_complete", "boot_reason"), runner))
    identity["root_uid"] = "unavailable"
    try:
        result = adb_read(adb, serial, "root_uid", runner=runner).decode("utf-8")
        uid = re.search(r"\buid=(\d+)\b", result)
        if uid:
            identity["root_uid"] = uid.group(1)
        if identity["root_uid"] != "0":
            raise CaptureError("root_unavailable")
    except (CaptureError, UnicodeError) as exc:
        raise CaptureError("root_unavailable", identity=identity) from exc
    return identity


def validate_tree(data):
    # tarfile tolerates a missing end marker; explicitly require both zero blocks.
    if len(data) % 512:
        raise CaptureError("runtime_tree: incomplete tar block")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            members = 0
            end = 0
            for member in archive:
                members += 1
                end = member.offset_data + ((member.size + 511) // 512) * 512
                if end > len(data):
                    raise CaptureError("runtime_tree: truncated member")
                if member.isfile():
                    stream = archive.extractfile(member)
                    remaining = member.size
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            raise CaptureError("runtime_tree: truncated contents")
                        remaining -= len(chunk)
            if not members or len(data) < end + 1024 or any(data[end:]):
                raise CaptureError("runtime_tree: missing or invalid tar end marker")
    except (tarfile.TarError, EOFError, ValueError) as exc:
        raise CaptureError("runtime_tree: invalid tar") from exc


def validate_config(data):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            config = stream.read(MAX_CONFIG + 1)
            if not config or len(config) > MAX_CONFIG:
                raise CaptureError("config: empty or exceeds 4 MiB expanded limit")
    except (OSError, EOFError, zlib.error) as exc:
        raise CaptureError("config: corrupt gzip") from exc


def validate_iomem(data):
    ranges = []
    for line in data.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(rb"\s*([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*(\S.*)", line)
        if not match:
            raise CaptureError("iomem: invalid memory-map line")
        lo, hi = (int(value, 16) for value in match.groups()[:2])
        if hi < lo:
            raise CaptureError("iomem: reversed memory range")
        ranges.append((lo, hi))
    if not ranges:
        raise CaptureError("iomem: no memory ranges captured")
    return "visible" if any(lo or hi for lo, hi in ranges) else "unproven"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_artifacts(adb: str, serial: str, out: Path, *, runner=subprocess.run
                      ) -> dict[str, dict[str, str | int]]:
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("output directory must be new and writable") from exc
    marker = out / "INCOMPLETE.json"
    identity = {}
    artifacts = {}
    try:
        write_json(marker, {"capture_status": "incomplete"})
        identity = collect_identity(adb, serial, runner=runner)
        for probe, filename in ARTIFACTS.items():
            data = adb_read(adb, serial, probe, runner=runner)
            if not data or len(data) > MAX_ARTIFACT:
                raise CaptureError(f"{probe}: empty or exceeds 64 MiB artifact limit")
            (out / filename).write_bytes(data)
            if probe == "runtime_tree":
                validate_tree(data)
            elif probe == "config":
                validate_config(data)
            artifacts[probe] = {
                "filename": filename, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            if probe == "iomem":
                artifacts[probe]["address_visibility"] = validate_iomem(data)
        final = read_identity(adb, serial, EXPECTED, runner)
        if any(final[key] != identity[key] for key in EXPECTED):
            raise CaptureError("identity_changed_during_capture")
        manifest = {"schema_version": 1, "capture_status": "complete",
                    "identity": identity, "artifacts": artifacts}
        write_json(out / "capture.tmp", manifest)
        marker.unlink()
        (out / "capture.tmp").replace(out / "capture.json")
    except (CaptureError, OSError, ValueError) as exc:
        failed_identity = getattr(exc, "identity", None) or identity
        write_json(marker, {"schema_version": 1, "capture_status": "incomplete",
                            "identity": failed_identity, "error": str(exc)})
        raise CaptureError(str(exc), identity=failed_identity, output_owned=True) from exc
    return artifacts


def assess(identity: Mapping[str, str], artifacts: Mapping[str, Mapping[str, str | int]]
           ) -> dict[str, object]:
    agreement = {key: identity.get(key) == value for key, value in EXPECTED.items()}
    root = identity.get("root_uid") == "0"
    complete = all(agreement.values()) and root and all(
        name in artifacts and all(field in artifacts[name] for field in ("filename", "bytes", "sha256"))
        for name in ARTIFACTS)
    blockers = ["matching_kernel_build_crc_unproven", "quest2_loader_unvalidated",
                "quest2_memory_layout_unvalidated", "recovery_unvalidated",
                "boot_state_transport_unvalidated", "vr_hardware_unvalidated"]
    if not all(agreement.values()):
        blockers.append("target_identity_mismatch")
    if not root:
        blockers.append("root_unavailable")
    if not complete:
        blockers.append("capture_incomplete")
    if artifacts.get("iomem", {}).get("address_visibility") != "visible":
        blockers.append("memory_map_addresses_unproven")
    if identity.get("boot_complete") != "1":
        blockers.append("android_boot_incomplete")
    if "panic" in identity.get("boot_reason", "").lower():
        blockers.append("startup_panic_unresolved")
    return {"schema_version": 1, "capture_status": "complete" if complete else "incomplete",
            "identity": dict(identity), "expected_identity": dict(EXPECTED),
            "identity_agreement": agreement, "identity_matches": all(agreement.values()),
            "artifacts": {key: dict(value) for key, value in artifacts.items()},
            "blockers": blockers, "handoff_ready": False, "auto_boot_allowed": False}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, help="path to the existing adb executable")
    parser.add_argument("--serial", required=True, help="explicit USB device serial")
    parser.add_argument("--out", required=True, type=Path, help="new host capture directory")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        artifacts = capture_artifacts(args.adb, args.serial, args.out, runner=subprocess.run)
        manifest = json.loads((args.out / "capture.json").read_text(encoding="utf-8"))
        report = assess(manifest["identity"], artifacts)
        write_json(args.out / "report.json", report)
    except (CaptureError, OSError, ValueError) as exc:
        if isinstance(exc, CaptureError) and exc.output_owned:
            report = assess(exc.identity, {})
            report["error"] = str(exc)
            try:
                write_json(args.out / "report.json", report)
            except OSError:
                print("Could not write failure report", file=sys.stderr)
        print(f"CAPTURE_FAILED; HANDOFF_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print("CAPTURE_COMPLETE; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
