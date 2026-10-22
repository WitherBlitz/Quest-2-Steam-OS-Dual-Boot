#!/usr/bin/env python3
"""Passive Quest 2 FD-holder evidence; never closes clients or changes hardware."""
import argparse
import hashlib
from pathlib import Path
import re
import shlex
import subprocess

from quest2_preflight import CaptureError, collect_identity, write_json
import quest2_shutdown_probe as passive

MAX_SCAN = 2 * 1024 * 1024
MAX_PROCESSES = 2048
MAX_FDS = 131072
MAX_PROCESS_FDS = 16384
DEVICE_PATTERN = (r"/dev/((video|v4l-subdev|media)[0-9]{1,4}|"
                  r"subsys_(adsp|cdsp|venus|spss|cvpss|a650_zap|wlan|npu)|"
                  r"adsprpc-smd(-secure)?)")
# Only metadata is read. No target FD is opened, duplicated, closed or ioctl'ed.
# ls reads symlinks in each FD directory in one process, rather than launching a
# readlink process for every descriptor. Irrelevant targets never reach stdout.
SCAN_SCRIPT = r'''export LC_ALL=C
printf 'QOWN2\n'
n=0
for d in /proc/[0-9]*; do
    p=${d##*/}
    case "$p" in ''|*[!0-9]*) exit 2;; esac
    n=$((n+1))
    [ "$n" -le 2048 ] || exit 2
    if ! IFS= read -r before 2>/dev/null < "$d/stat"; then
        printf 'S\t%s\tstat_before\n' "$p"
        continue
    fi
    printf 'P\t%s\t%s\n' "$p" "$before"
    ( ls -ln "$d/fd" 2>/dev/null
      listed=$?
      printf 'QOWN_LIST_STATUS\t%s\n' "$listed"
    ) | {
        count=0
        listed=missing
        while IFS= read -r line; do
            case "$line" in
            QOWN_LIST_STATUS*)
                [ "$listed" = missing ] || exit 2
                listed=${line#*	}
                case "$listed" in ''|*[!0-9]*) exit 2;; esac
            ;;
            l*)
                count=$((count+1))
                [ "$count" -le 16384 ] || exit 2
                target=${line#* -> }
                case "$target" in
                    /dev/video[0-9]*|/dev/v4l-subdev[0-9]*|/dev/media[0-9]*|\
                    /dev/subsys_adsp|/dev/subsys_cdsp|/dev/subsys_venus|/dev/subsys_spss|\
                    /dev/subsys_cvpss|/dev/subsys_a650_zap|/dev/subsys_wlan|/dev/subsys_npu|\
                    /dev/adsprpc-smd|/dev/adsprpc-smd-secure)
                        fd=${line%% -> *}; fd=${fd##* }
                        printf 'F\t%s\t%s\t%s\n' "$p" "$fd" "$target"
                    ;;
                esac
            ;; esac
        done
        [ "$listed" != missing ] || exit 2
        [ "$listed" -eq 0 ] || printf 'E\t%s\tlisting_failed\n' "$p"
        printf 'N\t%s\t%s\n' "$p" "$count"
    } || exit 2
    if IFS= read -r after 2>/dev/null < "$d/stat"; then
        printf 'A\t%s\t%s\n' "$p" "$after"
    else
        printf 'A\t%s\tMISSING\n' "$p"
    fi
done
printf 'END\t%s\t-\n' "$n"
'''


def remote_command():
    return "su -c " + shlex.quote("( " + SCAN_SCRIPT +
        ' ); qown_status=$?; printf "\\nQOWN_STATUS=%s\\n" "$qown_status"')


def read_scan(adb, serial, *, runner=subprocess.run, capture_path=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial):
        raise ValueError("explicit USB serial required")
    try:
        result = runner([adb, "-s", serial, "exec-out", remote_command()],
                        shell=False, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("owner scan transport failed; remote completion unknown") from exc
    if result.returncode or len(result.stdout) > MAX_SCAN + 128:
        raise CaptureError("owner scan failed or exceeded output limit")
    if capture_path is not None:
        with Path(capture_path).open("xb") as stream: stream.write(result.stdout)
    data, separator, status = result.stdout.rpartition(b"\nQOWN_STATUS=")
    if not separator or status != b"0\n":
        raise CaptureError("owner scan remote completion missing or failed")
    return data


def process_stat(text, pid):
    match = re.fullmatch(r"([0-9]+) \(([^\x00-\x1f\x7f]*)\) (.+)", text)
    if not match or int(match[1]) != pid:
        raise ValueError("invalid process stat identity")
    fields = match[3].split()
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("missing process start time")
    return {"pid": pid, "comm": match[2], "start_ticks": int(fields[19])}


def parse_scan(data):
    if not data or len(data) > MAX_SCAN:
        raise ValueError("empty or oversized owner scan")
    lines = data.decode("utf-8").splitlines()
    if not lines or lines[0] != "QOWN2":
        raise ValueError("invalid owner scan header")
    holders, gaps, seen = [], [], set()
    active = None
    total = unattributed = 0
    ended = False
    for line_number, line in enumerate(lines[1:], 2):
        row = line.split("\t")
        kind = row[0]
        if ended or len(row) < 3 or not row[1].isdigit():
            raise ValueError("malformed owner scan row at line " + str(line_number))
        pid = int(row[1])
        if kind == "END":
            if active or len(row) != 3 or row[2] != "-" or pid != len(seen):
                raise ValueError("incomplete owner scan counts")
            ended = True
            continue
        if pid < 1 or pid > 4194304:
            raise ValueError("invalid process ID")
        if kind in {"P", "S"}:
            if active or pid in seen or len(seen) >= MAX_PROCESSES or len(row) != 3:
                raise ValueError("duplicate or unfinished process")
            seen.add(pid)
            if kind == "S":
                if row[2] != "stat_before": raise ValueError("unknown process gap")
                gaps.append({"pid": pid, "reason": row[2]})
            else:
                active = {**process_stat(row[2], pid), "fds": [], "count": None, "error": False}
            continue
        if not active or active["pid"] != pid:
            raise ValueError("row without matching process")
        if kind == "F":
            if (len(row) != 4 or active["count"] is not None or not row[2].isdigit()
                    or not re.fullmatch(DEVICE_PATTERN, row[3])):
                raise ValueError("invalid matching FD")
            fd = int(row[2])
            if fd > 1048576 or any(item["fd"] == fd for item in active["fds"]):
                raise ValueError("invalid or duplicate FD")
            active["fds"].append({"fd": fd, "target": row[3]})
        elif kind == "N":
            if len(row) != 3 or active["count"] is not None or not row[2].isdigit():
                raise ValueError("invalid process FD count")
            count = int(row[2])
            if count < len(active["fds"]) or count > MAX_PROCESS_FDS or total + count > MAX_FDS:
                raise ValueError("FD scan limit or inconsistent count")
            active["count"] = count
            total += count
        elif kind == "E":
            if len(row) != 3 or row[2] != "listing_failed" or active["error"]:
                raise ValueError("invalid listing error")
            active["error"] = True
        elif kind == "A":
            if len(row) != 3 or active["count"] is None:
                raise ValueError("unfinished process FD scan")
            after = None if row[2] == "MISSING" else process_stat(row[2], pid)
            before = {key: active[key] for key in ("pid", "comm", "start_ticks")}
            if after != before or active["error"]:
                gaps.append({"pid": pid, "reason": "listing_failed" if active["error"] else "process_changed_or_exited"})
                unattributed += len(active["fds"])
            elif active["fds"]:
                holders.append({**before, "fds": active["fds"]})
            active = None
        else:
            raise ValueError("unknown owner scan row")
    if not ended or active or not seen:
        raise ValueError("missing owner scan completion")
    return {"processes_seen": len(seen), "fd_symlinks_seen": total, "holders": holders,
            "gaps": gaps, "unattributed_matching_fds": unattributed,
            "scan_coverage_complete": not gaps, "unique_open_files_known": False,
            "session_or_reference_owners_proven": False,
            "scope": "Sampled path holders only; FDs can change during a stable-PID scan; dup/fork may share files"}


def collect(adb, serial, out, *, runner=subprocess.run):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial):
        raise ValueError("explicit USB serial required")
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("new private output directory required") from exc
    marker = out / "INCOMPLETE.json"
    try:
        write_json(marker, {"capture_status": "incomplete"})
        identity = collect_identity(adb, serial, runner=runner)
        before = {key: passive.read(adb, serial, key, runner=runner)
                  for key in ("boot", "cpus", "taint", "modules")}
        if not re.fullmatch(rb"[a-fA-F0-9-]{36}\s*", before["boot"]):
            raise CaptureError("valid boot ID required")
        if set(passive.module_names(before["modules"])) & {"quest_kexec", "marker_read", "usb_log", "camera_dsp_diag"}:
            raise CaptureError("project module already resident")
        scan = read_scan(adb, serial, runner=runner, capture_path=out / "transport.txt")
        with (out / "scan.txt").open("xb") as stream: stream.write(scan)
        inventory = {key: passive.read(adb, serial, key, runner=runner).decode()
                     for key in ("camera", "dsp")}
        after = {key: passive.read(adb, serial, key, runner=runner)
                 for key in ("cpus", "taint", "modules")}
        final = collect_identity(adb, serial, runner=runner)
        after["boot"] = passive.read(adb, serial, "boot", runner=runner)
        if (identity != final or any(before[key].strip() != after[key].strip() for key in ("boot", "cpus", "taint"))
                or passive.module_names(before["modules"]) != passive.module_names(after["modules"])):
            raise CaptureError("identity, boot or passive health changed during owner scan")
        write_json(out / "health.json", {"identity": identity, "same_boot_cpus_modules_taint": True,
                   "cpus": before["cpus"].decode().strip(), "taint": before["taint"].decode().strip(),
                   "modules": passive.module_names(before["modules"]),
                   "boot_sha256": hashlib.sha256(before["boot"]).hexdigest()})
        owners = parse_scan(scan)
        report = {"schema_version": 1, "capture_status": "complete", "identity": identity,
                  "owners": owners, "inventory": inventory, "scan_bytes": len(scan),
                  "scan_sha256": hashlib.sha256(scan).hexdigest(),
                  "same_boot_cpus_modules_taint": True,
                  "device_mutation": False, "camera_shutdown_validated": False,
                  "dsp_shutdown_validated": False, "dma_quiescence_validated": False,
                  "recovery_validated": False, "handoff_ready": False, "status": "HANDOFF_BLOCKED"}
        write_json(out / "capture.json", report)
        marker.unlink()
        return report
    except (OSError, ValueError, CaptureError) as exc:
        write_json(marker, {"capture_status": "incomplete", "error": str(exc)})
        raise CaptureError(str(exc), output_owned=True) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        collect(args.adb, args.serial, args.output)
    except (OSError, ValueError, CaptureError) as exc:
        parser.exit(2, f"OWNER_CAPTURE_INCOMPLETE; HANDOFF_BLOCKED: {exc}\n")
    print("OWNER_CAPTURE_COMPLETE; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__": raise SystemExit(main())
