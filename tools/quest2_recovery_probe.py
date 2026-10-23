#!/usr/bin/env python3
"""Collect existing mounted pstore bytes; never generate or validate recovery."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import time

from quest2_preflight import CaptureError, EXPECTED, collect_identity, write_json
from quest2_shutdown_probe import module_names, read as read_health
from quest2_shutdown_source import ARCHIVE_SHA, KERNEL, audit_sources

MAX_FILE = 1024 * 1024
MAX_TOTAL = 4 * MAX_FILE
MAX_NAMES = 16
MAX_RECORDS = 4
MAX_META = 64 * 1024
BLOCK = 4096
FOOTER = b"\nQKX_RECOVERY_STATUS="
HEALTH = ("boot", "cpus", "taint", "modules")
BOOT_RE = rb"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def record_identity(name):
    match = re.fullmatch(r"(dmesg|console|pmsg)-ramoops-([01])(\.enc\.z)?", name)
    if not match or match[1] != "dmesg" and (match[2] != "0" or match[3]):
        return None
    return match[1] + ":" + match[2]


def remote_command(probe, name=None, *, size=None):
    """Fixed read-only commands; no mount, pstore rescan, unlink or pmsg device."""
    if probe == "mountinfo" and name is None and size is None:
        body = "dd if=/proc/self/mountinfo bs=4096 count=17 2>/dev/null"
    elif probe == "entries" and name is None and size is None:
        body = ('[ -d /sys/fs/pstore ] && [ ! -L /sys/fs/pstore ] || exit 3; n=0; '
                'for p in /sys/fs/pstore/* /sys/fs/pstore/.[!.]* /sys/fs/pstore/..?*; do '
                '[ -e "$p" ] || [ -L "$p" ] || continue; n=$((n+1)); '
                f'[ "$n" -le {MAX_NAMES} ] || exit 4; name=${{p##*/}}; '
                '[ "${#name}" -le 255 ] || exit 4; printf "%s\\0" "$name"; done')
    elif probe in ("metadata", "record") and isinstance(name, str) and record_identity(name):
        path = shlex.quote("/sys/fs/pstore/" + name)
        body = f'p={path}; [ ! -L "$p" ] && [ -f "$p" ] || exit 3; '
        if probe == "metadata" and size is None:
            body += 'stat -c "%f %s %i %d %h %Y %Z" "$p"'
        elif probe == "record" and type(size) is int and 0 <= size <= MAX_FILE:
            body += f'dd if="$p" bs={BLOCK} count={size // BLOCK + 1} 2>/dev/null'
        else:
            raise ValueError("invalid bounded pstore read size")
    else:
        raise ValueError("unsupported pstore probe or record name")
    framed = '( set -e; ' + body + ' ); qkx_status=$?; printf "\\nQKX_RECOVERY_STATUS=%s\\n" "$qkx_status"'
    return "su -c " + shlex.quote(framed)


def _read(adb, serial, probe, runner, name=None, *, size=None):
    command = remote_command(probe, name, size=size)
    try:
        result = runner([adb, "-s", serial, "exec-out", command], shell=False,
                        capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("pstore command completion unknown: " + probe) from exc
    if result.returncode or not isinstance(result.stdout, bytes):
        raise CaptureError(f"pstore transport failed: {probe}; adb_return={result.returncode}")
    data, separator, status = result.stdout.rpartition(FOOTER)
    limit = size + BLOCK if probe == "record" else MAX_META
    if not separator or not re.fullmatch(rb"[0-9]{1,3}\n", status) or int(status) > 255:
        raise CaptureError("pstore remote completion missing/malformed: " + probe)
    if status != b"0\n" or len(data) > limit:
        raise CaptureError(f"pstore remote completion failed or oversized: {probe}; remote_status={int(status)}")
    return data


def _mount_device(device):
    if not re.fullmatch(r"(0|[1-9][0-9]{0,3}):(0|[1-9][0-9]{0,6})", device):
        raise CaptureError("malformed mount device major:minor")
    major, minor = map(int, device.split(":"))
    if major >= 1 << 12 or minor >= 1 << 20:
        raise CaptureError("mount device major:minor outside pinned kernel bounds")
    # Pinned Linux include/linux/kdev_t.h:39-55; fs/stat.c:307,447.
    return (minor & 0xff) | (major << 8) | ((minor & ~0xff) << 12)


def _mount(data):
    if not data or len(data) > MAX_META:
        raise CaptureError("missing or oversized mountinfo")
    matches = []
    for line in data.decode("utf-8").splitlines():
        left, separator, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if not separator or len(fields) < 6 or len(tail) < 3:
            raise CaptureError("malformed mountinfo")
        _mount_device(fields[2])
        if fields[4].startswith("/sys/fs/pstore/"):
            raise CaptureError("child mount beneath pstore refuses record provenance")
        if fields[4] == "/sys/fs/pstore":
            matches.append({"mount_id": fields[0], "device": fields[2], "root": fields[3],
                            "mountpoint": fields[4], "fstype": tail[0], "source": tail[1]})
    if len(matches) > 1:
        raise CaptureError("ambiguous pstore mount")
    return matches[0] if matches else None


def _names(data):
    if data and not data.endswith(b"\0"):
        raise CaptureError("incomplete pstore name listing")
    names = data[:-1].split(b"\0") if data else []
    if len(names) > MAX_NAMES or len(set(names)) != len(names) or any(not n or len(n) > 255 for n in names):
        raise CaptureError("invalid or excessive pstore name listing")
    accepted, gaps, ids = [], [], set()
    for raw in names:
        name = raw.decode("utf-8", errors="backslashreplace")
        identity = record_identity(name)
        if identity is None:
            gaps.append({"name_hex": raw.hex(), "reason": "unsupported_name_not_read"})
            continue
        if identity in ids or len(accepted) >= MAX_RECORDS:
            raise CaptureError("ambiguous or excessive pstore geometry IDs")
        ids.add(identity)
        accepted.append(name)
    return accepted, gaps


def _metadata(data):
    values = data.decode("ascii").split()
    if len(values) != 7 or not re.fullmatch(r"[0-9a-fA-F]+", values[0]) or any(
            not re.fullmatch(r"-?\d+", value) for value in values[1:]):
        raise CaptureError("invalid pstore metadata")
    mode = int(values[0], 16)
    size, inode, device, links, mtime, ctime = map(int, values[1:])
    if mode & 0o170000 != 0o100000 or not 0 <= size <= MAX_FILE or inode < 1 or device < 0 or links < 1:
        raise CaptureError("pstore record is not a bounded regular file")
    return dict(mode=mode, size=size, inode=inode, device=device, links=links, mtime=mtime, ctime=ctime)


def collect(adb, serial, out, source_report, *, runner=subprocess.run,
            monotonic=time.monotonic, overall_timeout=300):
    if (source_report.get("source_verified") is not True or source_report.get("kernel_commit") != KERNEL
            or source_report.get("archive_sha256") != ARCHIVE_SHA):
        raise CaptureError("exact pinned public source audit required")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial)
            or type(overall_timeout) not in (int, float) or not math.isfinite(overall_timeout)
            or not 0 < overall_timeout <= 300):
        raise CaptureError("explicit serial and finite host budget required")
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("new private output directory required") from exc
    marker = out / "INCOMPLETE.json"
    artifacts, records = {}, []
    total, last, deadline = 0, None, None

    def remaining():
        nonlocal last, deadline
        now = monotonic()
        if type(now) not in (int, float) or not math.isfinite(now) or last is not None and now < last:
            raise CaptureError("invalid/nonmonotonic host clock")
        last = now
        if deadline is None:
            deadline = now + overall_timeout
            if not math.isfinite(deadline):
                raise CaptureError("host deadline overflow")
        if now >= deadline:
            raise CaptureError("overall host capture budget exhausted; completion unproven")
        return deadline - now

    def bounded_runner(command, **kwargs):
        kwargs["timeout"] = min(15, kwargs.get("timeout", 15), remaining())
        result = runner(command, **kwargs)
        remaining()
        if (not isinstance(result.stdout, bytes) or len(result.stdout) > MAX_FILE + BLOCK + 128
                or not isinstance(result.stderr, bytes) or len(result.stderr) > MAX_META):
            raise CaptureError("host transport output exceeds bounded capture policy")
        return result

    def save(key, data, suffix="txt"):
        nonlocal total
        if total + len(data) > MAX_TOTAL:
            raise CaptureError("aggregate capture byte limit exceeded")
        filename = key.replace(":", "-") + "." + suffix
        with (out / filename).open("xb") as stream:
            stream.write(data)
        total += len(data)
        artifacts[key] = {"filename": filename, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}
        return data

    def probe(key, label, name=None, size=None):
        return save(label, _read(adb, serial, key, bounded_runner, name, size=size),
                    "bin" if key in ("record", "entries") else "txt")

    try:
        write_json(marker, {"capture_status": "incomplete", "recovery_validated": False, "handoff_ready": False})
        remaining()
        identity = collect_identity(adb, serial, runner=bounded_runner)
        before = {key: save(key + "-before", read_health(adb, serial, key, runner=bounded_runner)) for key in HEALTH}
        if not re.fullmatch(BOOT_RE + rb"\s*", before["boot"]):
            raise CaptureError("valid boot ID required")
        if set(module_names(before["modules"])) & {"quest_kexec", "marker_read", "usb_log", "camera_dsp_diag"}:
            raise CaptureError("project module already resident")
        mount = _mount(probe("mountinfo", "mountinfo-before"))
        usable = mount is not None and mount["fstype"] == "pstore" and mount["root"] == "/"
        gaps, accepted = [], []
        if usable:
            names = probe("entries", "entries-before")
            accepted, gaps = _names(names)
            for name in accepted:
                first = _metadata(probe("metadata", "metadata-before-" + name, name))
                if first["device"] != _mount_device(mount["device"]):
                    raise CaptureError("pstore record device differs from verified parent mount")
                if total + first["size"] > MAX_TOTAL:
                    raise CaptureError("aggregate capture byte limit exceeded")
                key = "record:" + name
                data = probe("record", key, name, first["size"])
                final = _metadata(probe("metadata", "metadata-after-" + name, name))
                if first != final or len(data) != first["size"]:
                    raise CaptureError("pstore metadata/length changed during read")
                records.append({"name": name, "record_id": record_identity(name), "artifact": key,
                                "compressed_opaque": name.endswith(".enc.z"), "remote_status": 0,
                                "metadata_before": first, "metadata_after": final})
            if names != probe("entries", "entries-after"):
                raise CaptureError("pstore enumeration changed during capture")
        if mount != _mount(probe("mountinfo", "mountinfo-after")):
            raise CaptureError("pstore mount changed during collection")
        after = {key: save(key + "-after", read_health(adb, serial, key, runner=bounded_runner)) for key in HEALTH[1:]}
        final_identity = collect_identity(adb, serial, runner=bounded_runner)
        after["boot"] = save("boot-after", read_health(adb, serial, "boot", runner=bounded_runner))
        if (identity != final_identity or any(before[key].strip() != after[key].strip() for key in HEALTH[:3])
                or module_names(before["modules"]) != module_names(after["modules"])):
            raise CaptureError("boot, identity or passive health changed during capture")
        remaining()
        report = {"schema_version": 1, "capture_status": "complete", "identity": identity,
                  "source_audit": source_report, "artifacts": artifacts, "records": records,
                  "coverage_gaps": gaps, "same_boot_cpus_modules_taint": True,
                  "pstore": {"availability": "available" if records else "unavailable", "mount": mount,
                             "reason": "records_captured" if records else "no_supported_records" if usable else "no_verified_pstore_mount"},
                  "scope": "Existing mounted regular-file bytes only; metadata checks do not prove race freedom or record causality",
                  "remote_completion": "QKX_RECOVERY_STATUS=0 required for every pstore command",
                  "device_mutation": False, "recovery_validated": False, "handoff_ready": False,
                  "status": "HANDOFF_BLOCKED"}
        with (out / "capture.tmp").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
        marker.unlink()
        (out / "capture.tmp").replace(out / "capture.json")
        return report
    except (OSError, ValueError, CaptureError) as exc:
        write_json(marker, {"capture_status": "incomplete", "error": str(exc),
                            "recovery_validated": False, "handoff_ready": False})
        raise CaptureError(str(exc), output_owned=True) from exc


def _load_capture(directory):
    directory = Path(directory)
    try:
        manifest = directory / "capture.json"
        if manifest.is_symlink() or not manifest.is_file() or (directory / "INCOMPLETE.json").exists():
            raise CaptureError("complete regular capture manifest required")
        with manifest.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise CaptureError("oversized capture manifest")
        report = json.loads(raw)
        if (report.get("schema_version") != 1 or report.get("capture_status") != "complete"
                or report.get("recovery_validated") is not False or report.get("handoff_ready") is not False
                or report.get("device_mutation") is not False or report.get("same_boot_cpus_modules_taint") is not True
                or any(report["identity"].get(key) != value for key, value in EXPECTED.items())
                or report["identity"].get("root_uid") != "0"):
            raise CaptureError("invalid/incomplete recovery capture")
        source = report["source_audit"]
        if (source.get("source_verified") is not True or source.get("kernel_commit") != KERNEL
                or source.get("archive_sha256") != ARCHIVE_SHA):
            raise CaptureError("capture lacks exact pinned source audit")
        artifacts, data, total, filenames = report["artifacts"], {}, 0, set()
        if not isinstance(artifacts, dict) or not 10 <= len(artifacts) <= 24:
            raise CaptureError("invalid capture artifact count")
        for key, item in artifacts.items():
            name, count, digest = item["filename"], item["bytes"], item["sha256"]
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
                    or name in filenames or type(count) is not int or not 0 <= count <= MAX_FILE
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise CaptureError("invalid capture artifact metadata/path")
            filenames.add(name)
            total += count
            path = directory / name
            if total > MAX_TOTAL or path.is_symlink() or not path.is_file():
                raise CaptureError("invalid/missing bounded capture artifact")
            with path.open("rb") as stream:
                content = stream.read(count + 1)
            if len(content) != count or hashlib.sha256(content).hexdigest() != digest:
                raise CaptureError("capture artifact length/hash mismatch")
            data[key] = content
        for key in HEALTH:
            if key == "modules":
                equal = module_names(data[key + "-before"]) == module_names(data[key + "-after"])
            else:
                equal = data[key + "-before"].strip() == data[key + "-after"].strip()
            if not equal:
                raise CaptureError("capture health envelope mismatch")
        boot = data["boot-before"].strip()
        mount = _mount(data["mountinfo-before"])
        if not re.fullmatch(BOOT_RE, boot) or mount != _mount(data["mountinfo-after"]):
            raise CaptureError("invalid capture boot/mount envelope")
        usable = mount is not None and mount["fstype"] == "pstore" and mount["root"] == "/"
        expected = {key + suffix for key in (*HEALTH, "mountinfo") for suffix in ("-before", "-after")}
        accepted, gaps = [], []
        if usable:
            if data["entries-before"] != data["entries-after"]:
                raise CaptureError("capture enumeration mismatch")
            accepted, gaps = _names(data["entries-before"])
            expected.update(("entries-before", "entries-after"))
        if report["coverage_gaps"] != gaps:
            raise CaptureError("capture coverage does not match enumerated names")
        records = {}
        if not isinstance(report["records"], list) or len(report["records"]) > MAX_RECORDS:
            raise CaptureError("invalid capture record count")
        if [record["name"] for record in report["records"]] != accepted:
            raise CaptureError("capture records do not match verified pstore enumeration")
        for record in report["records"]:
            name = record["name"]
            identity = record_identity(name)
            metadata = record["metadata_before"]
            key = "record:" + name
            first, final = "metadata-before-" + name, "metadata-after-" + name
            if (not identity or identity != record["record_id"] or identity in records
                    or type(record["remote_status"]) is not int or record["remote_status"] != 0
                    or record["compressed_opaque"] is not name.endswith(".enc.z")
                    or record["artifact"] != key or metadata != record["metadata_after"]
                    or metadata != _metadata(data[first]) or metadata != _metadata(data[final])
                    or metadata["device"] != _mount_device(mount["device"])
                    or len(data[key]) != metadata["size"]):
                raise CaptureError("invalid captured pstore record")
            expected.update((key, first, final))
            records[identity] = artifacts[key]["sha256"]
        pstore = {"availability": "available" if records else "unavailable", "mount": mount,
                  "reason": "records_captured" if records else "no_supported_records" if usable else "no_verified_pstore_mount"}
        if set(artifacts) != expected or report["pstore"] != pstore:
            raise CaptureError("capture artifact/pstore coverage mismatch")
        return report, boot.decode(), records
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CaptureError("malformed or unreadable recovery capture") from exc


def compare(before, after):
    """Compare complete hash-checked bytes; never infer which event caused them."""
    first, first_boot, old = _load_capture(before)
    second, second_boot, new = _load_capture(after)
    common = old.keys() & new.keys()
    return {"schema_version": 1, "boot_changed": first_boot != second_boot,
            "unchanged_record_ids": sorted(key for key in common if old[key] == new[key]),
            "changed_record_ids": sorted(key for key in common if old[key] != new[key]),
            "missing_after_record_ids": sorted(old.keys() - new.keys()),
            "new_after_record_ids": sorted(new.keys() - old.keys()),
            "coverage_gaps": {"before": first["coverage_gaps"], "after": second["coverage_gaps"]},
            "reset_causality": "unproven", "watchdog_ownership": "unproven",
            "recovery_deadline": "unproven", "healthy_horizon": "unproven",
            "recovery_validated": False, "handoff_ready": False, "status": "HANDOFF_BLOCKED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb")
    parser.add_argument("--serial")
    parser.add_argument("--kernel-source", type=Path)
    parser.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    comparing = args.compare_before is not None or args.compare_after is not None
    if comparing and (not args.compare_before or not args.compare_after or args.adb or args.serial or args.kernel_source):
        parser.error("comparison requires both captures and no device/source options")
    if not comparing and not (args.adb and args.serial and args.kernel_source):
        parser.error("collection requires --adb, --serial and --kernel-source")
    try:
        if comparing:
            if args.output.exists():
                raise CaptureError("new comparison output required")
            report = compare(args.compare_before, args.compare_after)
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2); stream.write("\n")
        else:
            collect(args.adb, args.serial, args.output, audit_sources(args.kernel_source))
    except (OSError, ValueError, CaptureError) as exc:
        parser.exit(2, f"RECOVERY_EVIDENCE_INCOMPLETE; HANDOFF_BLOCKED: {exc}\n")
    print("RECOVERY_OBSERVATIONS_COMPLETE; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
