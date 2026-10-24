#!/usr/bin/env python3
"""Read exact active-slot ABL/TZ prefixes; never boot, flash or authorize execution."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import time
from types import MappingProxyType

from quest2_preflight import CaptureError, collect_identity, write_json
from quest2_shutdown_probe import module_names, read as read_health

IMAGES = MappingProxyType({
    "abl": (274432, "75dc2174995fd39588bcbbd8b3455afc7bad4999a8914e0c40b9cc1509bcef5c"),
    "tz": (3207168, "9384241f1169e4953992fc06723507e8ac217006a5dfb92e47826068500dc8d3"),
})
HEALTH = ("boot", "cpus", "taint", "modules")
PROJECT_MODULES = {"quest_kexec", "marker_read", "usb_log", "camera_dsp_diag", "owner_gate_selftest"}
FOOTER = b"\nQKX_BOOT_CHAIN_STATUS="
BLOCK = 4096


def remote_command(probe, image, slot):
    if (probe not in ("metadata", "image") or not isinstance(image, str)
            or image not in IMAGES or slot not in ("_a", "_b")):
        raise ValueError("fixed image, probe and active slot required")
    path = "/dev/block/by-name/" + image + slot
    body = f'p={shlex.quote(path)}; [ -b "$p" ] || exit 3; '
    if probe == "metadata":
        body += 'readlink -f "$p"; stat -Lc "%f %t %T %i %d" "$p"'
    else:
        size = IMAGES[image][0]
        body += f'dd if="$p" bs={BLOCK} count={size // BLOCK} 2>/dev/null'
    framed = '( set -e; ' + body + ' ); qkx_status=$?; printf "\\nQKX_BOOT_CHAIN_STATUS=%s\\n" "$qkx_status"'
    return "su -c " + shlex.quote(framed)


def _read(adb, serial, probe, image, slot, runner):
    try:
        result = runner([adb, "-s", serial, "exec-out", remote_command(probe, image, slot)],
                        shell=False, capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("partition read completion unknown: " + image + ":" + probe) from exc
    if result.returncode or not isinstance(result.stdout, bytes):
        raise CaptureError("partition transport failed: " + image + ":" + probe)
    data, separator, status = result.stdout.rpartition(FOOTER)
    if not separator or status != b"0\n":
        raise CaptureError("partition remote completion missing or failed: " + image + ":" + probe)
    if probe == "image":
        valid_size = len(data) == IMAGES[image][0]
    else:
        valid_size = 0 < len(data) <= 4096
    if not valid_size:
        raise CaptureError("partition read length mismatch: " + image + ":" + probe)
    return data


def _metadata(data):
    try:
        rows = data.decode("ascii").splitlines()
        if len(rows) != 2 or not re.fullmatch(
                r"/dev/block/(?:sd[a-z]+[1-9][0-9]*|mmcblk[0-9]+p[1-9][0-9]*)", rows[0]):
            raise ValueError("unexpected resolved partition path")
        fields = rows[1].split()
        if (len(fields) != 5 or any(not re.fullmatch(r"[0-9a-fA-F]{1,8}", s) for s in fields[:3])
                or any(not re.fullmatch(r"[0-9]{1,20}", s) for s in fields[3:])):
            raise ValueError("invalid block metadata")
        mode, major, minor = (int(s, 16) for s in fields[:3])
        inode, device = map(int, fields[3:])
        if mode & 0o170000 != 0o060000 or major >= 1 << 12 or minor >= 1 << 20 or inode < 1:
            raise ValueError("not a bounded block-device identity")
        return dict(path=rows[0], mode=mode, major=major, minor=minor, inode=inode, device=device)
    except (ValueError, UnicodeError) as exc:
        raise CaptureError("invalid partition metadata") from exc


def collect(adb, serial, out, *, runner=subprocess.run, monotonic=time.monotonic, overall_timeout=180):
    if (not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial)
            or type(overall_timeout) not in (int, float) or not math.isfinite(overall_timeout)
            or not 0 < overall_timeout <= 300):
        raise CaptureError("explicit USB serial and finite host budget required")
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("new private output directory required") from exc
    marker = out / "INCOMPLETE.json"
    artifacts, images = {}, {}
    deadline, previous = None, None

    def remaining():
        nonlocal deadline, previous
        now = monotonic()
        if not math.isfinite(now) or previous is not None and now < previous:
            raise CaptureError("invalid host monotonic clock")
        if deadline is None:
            deadline = now + overall_timeout
        previous = now
        left = deadline - now
        if left <= 0:
            raise CaptureError("boot-chain capture host budget exceeded")
        return left

    def bounded(command, **kwargs):
        kwargs["timeout"] = min(kwargs["timeout"], remaining())
        result = runner(command, **kwargs)
        remaining()
        return result

    def save(name, data, suffix=".txt"):
        filename = name + suffix
        with (out / filename).open("xb") as stream:
            stream.write(data)
        artifacts[name] = {"filename": filename, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
        return data

    try:
        write_json(marker, {"capture_status": "incomplete"})
        identity = collect_identity(adb, serial, runner=bounded)
        if (identity["slot"] not in ("_a", "_b") or identity["verified_boot"] != "green"
                or identity["vbmeta_state"] != "locked" or identity["boot_complete"] != "1"):
            raise CaptureError("complete locked verified boot with a fixed active slot required")
        before = {key: save(key+"-before", read_health(adb, serial, key, runner=bounded)) for key in HEALTH}
        if (not re.fullmatch(rb"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", before["boot"].strip())
                or not re.fullmatch(rb"[0-9,-]+", before["cpus"].strip())
                or not re.fullmatch(rb"[0-9]{1,10}", before["taint"].strip())):
            raise CaptureError("valid boot/CPU/taint observations required")
        if set(module_names(before["modules"])) & PROJECT_MODULES:
            raise CaptureError("project module already resident")
        for name, (size, digest) in IMAGES.items():
            first = _metadata(save(name+"-metadata-before", _read(
                adb, serial, "metadata", name, identity["slot"], bounded)))
            data = save(name, _read(adb, serial, "image", name, identity["slot"], bounded), ".img")
            if hashlib.sha256(data).hexdigest() != digest:
                raise CaptureError("partition prefix differs from pinned OTA: " + name)
            last = _metadata(save(name+"-metadata-after", _read(
                adb, serial, "metadata", name, identity["slot"], bounded)))
            if first != last:
                raise CaptureError("partition device identity changed: " + name)
            images[name] = {"prefix_bytes": size, "sha256": digest, "block_device": first,
                            "metadata_stable": True, "matches_pinned_ota_prefix": True}
        after = {key: save(key+"-after", read_health(adb, serial, key, runner=bounded))
                 for key in HEALTH if key != "boot"}
        final = collect_identity(adb, serial, runner=bounded)
        after["boot"] = save("boot-after", read_health(adb, serial, "boot", runner=bounded))
        if (identity != final or any(before[k].strip() != after[k].strip() for k in ("boot", "cpus", "taint"))
                or module_names(before["modules"]) != module_names(after["modules"])):
            raise CaptureError("identity, boot or observed health changed during capture")
        report = {"schema_version": 1, "capture_status": "complete", "identity": identity,
                  "target_selector_sha256": hashlib.sha256(serial.encode("ascii")).hexdigest(),
                  "artifacts": artifacts, "images": images, "same_boot_cpus_module_names_taint": True,
                  "scope": "active_slot_partition_prefix_identity", "running_secure_image_measured": False,
                  "device_mutation": False, "nonpersistent_boot_acceptance": "unproven",
                  "rollback_floors": "unproven", "secure_service_permissions": "unproven",
                  "driver_execution_validated": False, "recovery_validated": False,
                  "execution_ready": False, "status": "EXECUTION_BLOCKED"}
        with (out / "capture.tmp").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
        marker.unlink()
        (out / "capture.tmp").replace(out / "capture.json")
        return report
    except (OSError, ValueError, TypeError, CaptureError) as exc:
        write_json(marker, {"capture_status": "incomplete", "artifacts": artifacts,
                            "error": str(exc), "execution_ready": False})
        raise CaptureError(str(exc), output_owned=True) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True, help="new private directory; firmware bytes stay here")
    args = parser.parse_args(argv)
    try:
        collect(args.adb, args.serial, args.output)
    except (OSError, ValueError, CaptureError) as exc:
        parser.exit(2, f"BOOT_CHAIN_CAPTURE_INCOMPLETE; EXECUTION_BLOCKED: {exc}\n")
    print("BOOT_CHAIN_PREFIXES_MATCH; EXECUTION_BLOCKED")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
