#!/usr/bin/env python3
"""Bounded return observations, with temporary tracefs instrumentation only.

No driver stop, target argument/memory fetch, watchdog change or Linux handoff.
Raw trace data contains process/kernel metadata and must remain private.
"""
import base64
import binascii
import argparse
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from quest2_preflight import CaptureError, adb_read, collect_identity, write_json
import quest2_shutdown_probe as passive
from quest2_shutdown_source import ARCHIVE_SHA, KERNEL, audit_sources

MAX_WIRE = 128 * 1024


def group_name(token):
    if type(token) is not str or not re.fullmatch(r"[0-9a-f]{16}", token):
        raise ValueError("invalid private trace namespace")
    return "qkxown_" + token


TRACE_SCRIPT = r'''g=@GROUP@
root=/sys/kernel/tracing
i=$root/instances/$g
owned=0
phase=preflight
cleanup() {
    status=$?
    trap - EXIT
    set +e
    clean=0
    if [ "$owned" -eq 1 ]; then
        if [ -e "$i/tracing_on" ]; then printf '0\n' > "$i/tracing_on" || clean=1; fi
        for suffix in c d f; do
            p=$i/events/$g/${g}_$suffix/enable
            if [ -e "$p" ]; then printf '0\n' > "$p" || clean=1; fi
        done
        if [ -d "$i" ]; then rmdir "$i" || clean=1; fi
        for suffix in c d f; do
            if [ -e "$root/events/$g/${g}_$suffix" ]; then
                printf '%s\n' "-:$g/${g}_$suffix" >> "$root/kprobe_events" || clean=1
            fi
        done
        [ ! -e "$i" ] || clean=1
        [ ! -e "$root/events/$g" ] || clean=1
    fi
    printf 'DONE\t%s\t%s\t%s\n' "$status" "$clean" "$phase"
    if [ "$status" -eq 0 ] && [ "$clean" -ne 0 ]; then status=3; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
set -e
printf 'QRT1\t@TOKEN@\n'
[ "$(stat -f -c %t "$root")" = 74726163 ]
[ "$(cat "$root/current_tracer")" = nop ]
[ "$(cat "$root/tracing_on")" = 0 ]
[ ! -e "$i" ] && [ ! -e "$root/events/$g" ] || exit 1
phase=instance
mkdir "$i"
owned=1
phase=buffers
[ -f "$i/tracing_on" ] && [ -f "$i/buffer_size_kb" ] || exit 1
printf '0\n' > "$i/tracing_on"
printf '64\n' > "$i/buffer_size_kb"
register_event() {
    phase=register_$1
    printf '%s\n' "r16:$g/${g}_$1 $2 ret=\$retval:s32" >> "$root/kprobe_events"
}
register_event c __arm64_sys_getpid
register_event d fastrpc_release_current_dsp_process
register_event f fastrpc_device_release
phase=enable
for suffix in c d f; do printf '1\n' > "$i/events/$g/${g}_$suffix/enable"; done
phase=observe
printf '1\n' > "$i/tracing_on"
control=$(/system/bin/sh -c 'echo $$')
printf 'CONTROL\t%s\n' "$control"
sleep 3
phase=freeze
printf '0\n' > "$i/tracing_on"
for suffix in c d f; do printf '0\n' > "$i/events/$g/${g}_$suffix/enable"; done
emit_value() {
    printf '%s\t' "$1"
    printf '%s' "$2" | base64 | tr -d '\n'
    printf '\n'
}
phase=trace
data=$(head -c 65537 "$i/trace")
emit_value TRACE "$data"
phase=profile
data=$(head -c 65537 "$root/kprobe_profile")
[ "${#data}" -le 65536 ]
data=$(printf '%s\n' "$data" | grep -E "^[[:space:]]*${g}_[cdf][[:space:]]")
emit_value PROFILE "$data"
phase=stats
for cpu in 0 1 2 3 4 5 6 7; do
    data=$(head -c 2049 "$i/per_cpu/cpu$cpu/stats")
    emit_value CPU$cpu "$data"
done
phase=complete
'''


def remote_command(token):
    group = group_name(token)
    return "su -c " + shlex.quote(TRACE_SCRIPT.replace("@GROUP@", group).replace("@TOKEN@", token))


def parse_capture(data, token):
    """Validate one completed transaction; return observations, never receipts."""
    group = group_name(token)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_WIRE or not data.endswith(b"\n"):
        raise ValueError("empty, oversized or unfinished trace transaction")
    try:
        rows = data.decode("ascii").splitlines()
        if not rows or rows[0] != "QRT1\t" + token:
            raise ValueError("wrong trace namespace/header")
        done = rows[-1].split("\t")
        if len(done) != 4 or done[0] != "DONE":
            raise ValueError("trace completion/cleanup unconfirmed")
        if done != ["DONE", "0", "0", "complete"]:
            raise ValueError("trace failed: " + "/".join(done[1:]))
        labels = ["CONTROL", "TRACE", "PROFILE", *("CPU"+str(n) for n in range(8))]
        if len(rows) != len(labels) + 2:
            raise ValueError("trace section count mismatch")
        sections = {}
        for row, label in zip(rows[1:-1], labels):
            fields = row.split("\t")
            if len(fields) != 2 or fields[0] != label:
                raise ValueError("trace section ordering/label mismatch")
            sections[label] = fields[1]
        if not re.fullmatch(r"[1-9][0-9]{0,9}", sections["CONTROL"]):
            raise ValueError("invalid control PID")
        control = int(sections.pop("CONTROL"))
        if control >= 1 << 31:
            raise ValueError("control PID outside supported range")
        for label, value in sections.items():
            raw = base64.b64decode(value, validate=True)
            cap = 65536 if label == "TRACE" else 8192 if label == "PROFILE" else 2048
            if not raw or len(raw) > cap:
                raise ValueError("trace section size exceeded: " + label)
            sections[label] = raw.decode("utf-8")
        trace = sections["TRACE"].splitlines()
        counts = [re.fullmatch(r"# entries-in-buffer/entries-written: ([0-9]{1,20})/([0-9]{1,20})(?:\s+.*)?", line) for line in trace]
        counts = [m for m in counts if m]
        if "# tracer: nop" not in trace or len(counts) != 1:
            raise ValueError("trace header/count missing")
        buffered, written = map(int, counts[0].groups())
        if not 0 <= buffered <= written < 1 << 64:
            raise ValueError("invalid trace entry counts")
        pattern = re.compile(r"\s*.+-([0-9]{1,10})\s+\[([0-9]{3})\]\s+(?:\S+\s+)?"
                             r"([0-9]{1,16}\.[0-9]{1,9}):\s+" + group +
                             r"_([cdf]):\s+\(.+\)\s+ret=(-?[0-9]{1,11})\s*")
        events, controls, returns = 0, 0, []
        names = {"d": "dsp_release", "f": "file_release"}
        for line in trace:
            if not line or line.startswith("#"):
                continue
            match = pattern.fullmatch(line)
            if not match:
                raise ValueError("unknown or truncated trace record")
            tid, cpu, stamp, event, value = match.groups()
            tid, cpu, value = int(tid), int(cpu), int(value)
            if tid >= 1 << 31 or cpu >= 8 or not -(1 << 31) <= value < 1 << 31:
                raise ValueError("invalid trace PID/CPU/signed return")
            events += 1
            if event == "c":
                controls += tid == control and value == control
            else:
                returns.append({"event": names[event], "tid": tid, "cpu": cpu,
                                "timestamp": stamp, "return": value})
        if events != buffered or not controls:
            raise ValueError("missing records or positive control return")
        profile = {}
        for line in sections["PROFILE"].splitlines():
            match = re.fullmatch(r"\s*" + group + r"_([cdf])\s+([0-9]{1,20})\s+([0-9]{1,20})\s*", line)
            if not match or match[1] in profile:
                raise ValueError("invalid or duplicate probe profile")
            profile[match[1]] = {"hits": int(match[2]), "reported_misses": int(match[3])}
        if set(profile) != {"c", "d", "f"} or not profile["c"]["hits"]:
            raise ValueError("incomplete probe profile/control")
        loss = buffered != written
        for cpu in range(8):
            stats = {}
            for line in sections["CPU"+str(cpu)].splitlines():
                key, separator, value = line.partition(":")
                if not separator or key in stats:
                    raise ValueError("malformed/duplicate ring statistics")
                stats[key] = value.strip()
            for key in ("overrun", "commit overrun", "dropped events"):
                if not re.fullmatch(r"[0-9]{1,20}", stats.get(key, "")):
                    raise ValueError("missing ring loss counter")
                loss |= int(stats[key]) != 0
        gaps = []
        if not returns:
            gaps.append("no_target_release_observed")
        if any(p["reported_misses"] for p in profile.values()):
            gaps.append("reported_probe_misses")
        if loss:
            gaps.append("ring_buffer_loss")
        return {"positive_control_returns": controls, "returns": returns,
                "probe_profile": profile, "coverage_gaps": gaps,
                "complete_return_coverage_proven": False, "ownership_proven": False,
                "dma_quiescence_validated": False, "recovery_validated": False,
                "handoff_ready": False, "instrumentation_cleanup_confirmed": True,
                "scope": "Uncorrelated kernel return observations only; clocks, missing calls and causality unproven"}
    except (UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid trace encoding") from exc


def state_command():
    body = ('set -e; stat -f -c %t /sys/kernel/tracing; '
            'cat /sys/kernel/tracing/current_tracer; cat /sys/kernel/tracing/tracing_on; '
            'head -c 65537 /sys/kernel/tracing/kprobe_events')
    return "su -c " + shlex.quote('( ' + body +
        ' ); status=$?; printf "\\nQRT_STATE_STATUS=%s\\n" "$status"')


def collect(adb, serial, out, source_report, *, runner=subprocess.run, token=None):
    """One temporary instrumentation transaction; never retry unknown completion."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", serial):
        raise CaptureError("explicit USB target required")
    if (source_report.get("source_verified") is not True or source_report.get("kernel_commit") != KERNEL
            or source_report.get("archive_sha256") != ARCHIVE_SHA):
        raise CaptureError("exact pinned source audit required")
    token = uuid.uuid4().hex[:16] if token is None else token
    group = group_name(token)
    out = Path(out)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise CaptureError("new private output directory required") from exc
    marker = out / "INCOMPLETE.json"
    deadline, calls = time.monotonic() + 90, 0
    artifacts = {}
    transaction_entered = False

    def limited(command, **kwargs):
        nonlocal calls
        remaining = deadline - time.monotonic()
        calls += 1
        if not math.isfinite(remaining) or remaining <= 0 or calls > 64:
            raise CaptureError("trace host budget exhausted; remote completion may be unknown")
        kwargs["timeout"] = min(kwargs.get("timeout", 15), remaining)
        return runner(command, **kwargs)

    def save(name, data):
        if not isinstance(data, bytes) or len(data) > 2 * 1024 * 1024:
            raise CaptureError("invalid/oversized private trace artifact")
        with (out / name).open("xb") as stream:
            stream.write(data)
        artifacts[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return data

    def state(label):
        result = limited([adb, "-s", serial, "exec-out", state_command()], shell=False,
                         capture_output=True, timeout=15)
        raw = save(label, result.stdout)
        data, separator, status = raw.rpartition(b"\nQRT_STATE_STATUS=")
        if (result.returncode or result.stderr or not separator or status != b"0\n"
                or not data.startswith(b"74726163\nnop\n0\n")
                or len(data.split(b"\n", 3)[-1]) > 65536):
            raise CaptureError("verified inactive tracefs/global state required")
        return data

    def health(suffix):
        return {key: save(key+suffix+".txt", passive.read(adb, serial, key, runner=limited))
                for key in ("boot", "cpus", "taint", "modules")}

    try:
        write_json(marker, {"capture_status": "incomplete", "namespace": group,
                            "instrumentation_cleanup_confirmed": False, "handoff_ready": False})
        identity = collect_identity(adb, serial, runner=limited)
        before = health("-before")
        if not re.fullmatch(rb"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\s*", before["boot"]):
            raise CaptureError("valid baseline boot ID required")
        if set(passive.module_names(before["modules"])) & {"quest_kexec", "marker_read", "usb_log", "camera_dsp_diag"}:
            raise CaptureError("project module already resident")
        config = save("stock-config.gz", adb_read(adb, serial, "config", runner=limited))
        with gzip.GzipFile(fileobj=io.BytesIO(config)) as stream:
            config = stream.read(4 * 1024 * 1024 + 1)
        if len(config) > 4 * 1024 * 1024:
            raise CaptureError("oversized stock configuration")
        settings = config.decode("utf-8").splitlines()
        if any(line not in settings for line in ("CONFIG_KPROBES=y", "CONFIG_KRETPROBES=y",
                                                 "CONFIG_KPROBE_EVENTS=y", "CONFIG_NR_CPUS=8")):
            raise CaptureError("required stock probe/CPU configuration unavailable")
        initial = state("trace-state-before.bin")
        if group.encode() in initial:
            raise CaptureError("trace namespace already present")
        command = remote_command(token)
        save("trace-command.txt", command.encode())
        transaction_entered = True
        result = limited([adb, "-s", serial, "exec-out", command], shell=False,
                         capture_output=True, timeout=30)
        raw = save("trace-transaction.bin", result.stdout)
        save("trace-stderr.bin", result.stderr)
        final_state = state("trace-state-after.bin")
        after = health("-after")
        final_identity = collect_identity(adb, serial, runner=limited)
        if (identity != final_identity or any(before[k].strip() != after[k].strip() for k in ("boot", "cpus", "taint"))
                or passive.module_names(before["modules"]) != passive.module_names(after["modules"])):
            raise CaptureError("identity/boot/CPU/module/taint changed during instrumentation")
        if initial != final_state:
            raise CaptureError("global trace definitions/state changed")
        if result.returncode or result.stderr:
            raise CaptureError("trace transaction failed; inspect private status/stderr")
        observation = parse_capture(raw, token)
        report = {**observation, "schema_version": 1, "capture_status": "complete",
                  "identity": identity, "target_selector_sha256": hashlib.sha256(serial.encode("ascii")).hexdigest(),
                  "source_audit": source_report, "artifacts": artifacts,
                  "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "namespace": group, "host_command_count": calls,
                  "instrumentation_mutation": True, "global_tracing_unchanged": True,
                  "same_boot_cpus_modules_taint": True, "driver_control_performed": False,
                  "status": "OBSERVATION_COMPLETE_HANDOFF_BLOCKED"}
        write_json(out / "capture.json", report)
        marker.unlink()
        return report
    except (OSError, ValueError, CaptureError, subprocess.TimeoutExpired) as exc:
        truncated = False
        if isinstance(exc, subprocess.TimeoutExpired):
            for label, data in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                if isinstance(data, bytes):
                    truncated |= len(data) > MAX_WIRE
                    save("trace-timeout-"+label+".bin", data[:MAX_WIRE])
        message = ("trace transport completion unknown: " + type(exc).__name__
                   if transaction_entered and isinstance(exc, (OSError, subprocess.TimeoutExpired)) else str(exc))
        write_json(marker, {"capture_status": "incomplete", "namespace": group, "error": message,
                            "artifacts": artifacts, "partial_output_truncated": truncated,
                            "instrumentation_cleanup_confirmed": False, "handoff_ready": False})
        raise CaptureError(message, output_owned=True) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--kernel-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        collect(args.adb, args.serial, args.output, audit_sources(args.kernel_source))
    except (OSError, ValueError, CaptureError) as exc:
        parser.exit(2, f"RELEASE_OBSERVATION_INCOMPLETE; HANDOFF_BLOCKED: {exc}\n")
    print("RELEASE_OBSERVATION_COMPLETE; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
