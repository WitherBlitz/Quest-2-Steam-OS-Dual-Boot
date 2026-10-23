#!/usr/bin/env python3
"""Build/execute the sparse reader on Linux/QEMU, using only this CI parent's memory.

No Quest, ADB, device inputs or system settings. QEMU guest traces stay in memory;
published proof contains syscall counts and statuses, never CI PIDs or addresses.
Passing this check establishes Linux/QEMU execution only, not Android readiness.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/native/quest2_csl_read.c"
OUTPUT = ROOT / "reader-ci-artifacts"
TIMEOUT = 20
MAGIC = b"QCSLR01\0"
GLOBAL = 0x865f0
EXTENT = 0x1d94e0 + 128 * 0x388
SYSCALLS = ("read", "write", "openat", "pread64", "close", "exit")
SCOPE = "Linux/QEMU with CI-owned parent-process memory only; Android and Quest execution unvalidated"


class CheckFailed(Exception):
    pass


def invoke(command, label, *, data=None):
    """Do not include a command, stderr or subprocess exception in public errors."""
    try:
        return subprocess.run(command, input=data, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CheckFailed(f"{label}: exceeded the 20-second subprocess timeout") from None
    except OSError as exc:
        raise CheckFailed(f"{label}: could not execute tool (errno={exc.errno})") from None


def build_step(command, label, proof, log):
    result = invoke(command, label)
    summary = {"step": label, "status": result.returncode,
               "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr)}
    proof["build_statuses"].append(summary)
    log.append(json.dumps(summary, sort_keys=True))
    if result.returncode:
        raise CheckFailed(f"{label}: failed with status {result.returncode}; raw tool output withheld")
    return result.stdout


def inspect_elf(path):
    blob = path.read_bytes()
    if len(blob) < 64 or blob[:6] != b"\x7fELF\x02\x01":
        raise CheckFailed("ELF check: expected a little-endian ELF64 executable")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", blob)
    if header[1:4] != (2, 183, 1) or header[8] != 64 or header[9] != 56:
        raise CheckFailed("ELF check: expected ET_EXEC EM_AARCH64 and canonical program headers")
    entry, offset, count = header[4], header[5], header[10]
    if not count or offset > len(blob) or count * 56 > len(blob) - offset:
        raise CheckFailed("ELF check: invalid program-header extent")
    segments = [struct.unpack_from("<IIQQQQQQ", blob, offset + i * 56) for i in range(count)]
    if any(row[0] in (2, 3) for row in segments):
        raise CheckFailed("ELF check: interpreter or dynamic segment present")
    if not any(row[0] == 1 and row[1] & 1 and row[3] <= entry < row[3] + row[5]
               for row in segments):
        raise CheckFailed("ELF check: entry is outside executable file-backed memory")
    stacks = [row for row in segments if row[0] == 0x6474e551]
    if len(stacks) != 1 or stacks[0][1] & 1:
        raise CheckFailed("ELF check: missing non-executable stack declaration")
    return {"native_elf_sha256": hashlib.sha256(blob).hexdigest(),
            "native_elf_bytes": len(blob), "elf_status": "static ELF64 little-endian AArch64 ET_EXEC; stack NX"}


def plan(records):
    return MAGIC + struct.pack("<I", len(records)) + b"".join(
        struct.pack("<QI", address, size) for address, size in records)


def run_case(binary, name, args, data, status, expected, opens, preads, proof, log):
    # -strace logs guest syscalls only. Parse it privately and publish counts.
    result = invoke(["qemu-aarch64", "-strace", str(binary), *args], name, data=data)
    calls = {call: 0 for call in SYSCALLS}
    for match in re.finditer(rb"(?m)^\s*\d+\s+([a-zA-Z0-9_]+)\(", result.stderr):
        call = match[1].decode("ascii")
        if call not in calls:
            raise CheckFailed(f"{name}: unexpected guest syscall; raw trace withheld")
        calls[call] += 1
    summary = {"case": name, "status": result.returncode, "stdout_bytes": len(result.stdout),
               "stderr_bytes": len(result.stderr), "guest_syscall_counts": calls}
    proof["execution_cases"].append(summary)
    log.append(json.dumps(summary, sort_keys=True))
    if result.returncode != status:
        meanings = {2: "CLI rejected", 3: "plan/stdin rejected", 4: "proc-memory open failed",
                    5: "pread failed or returned short data", 6: "stdout write failed", 7: "close failed"}
        meaning = meanings.get(result.returncode, "emulator or guest failure")
        raise CheckFailed(f"{name}: status {result.returncode} ({meaning}), expected {status}")
    if result.stdout != expected:
        raise CheckFailed(f"{name}: stdout differs from the known expected bytes")
    if calls["openat"] != opens or calls["pread64"] != preads or calls["close"] != opens or calls["exit"] != 1:
        raise CheckFailed(f"{name}: guest open/pread/close/exit counts differ from the required path")
    if not expected and calls["write"]:
        raise CheckFailed(f"{name}: guest wrote output for a rejected or empty plan")


def execute_checks(binary, proof, log):
    # An impossible/high PID is unnecessary: malformed plans must exit before
    # openat even when supplied a syntactically valid PID and base.
    args = ["1", "0"]
    cases = [
        ("invalid_cli", ["0", "0"], plan([]), 2),
        ("bad_magic", args, b"QCSLR02\0" + struct.pack("<I", 0), 3),
        ("truncated_plan", args, plan([(GLOBAL, 4)])[:-1], 3),
        ("trailing_data", args, plan([]) + b"x", 3),
        ("out_of_bounds", args, plan([(GLOBAL - 1, 1)]), 3),
        ("empty_plan", args, plan([]), 0),
    ]
    for name, argv, data, status in cases:
        run_case(binary, name, argv, data, status, b"", 0, 0, proof, log)

    pattern = bytes((i * 37 + 11) & 255 for i in range(128))
    buffer = ctypes.create_string_buffer(pattern)
    address = ctypes.addressof(buffer)
    base = (address - GLOBAL) & ~4095
    if not 0 <= base < base + GLOBAL <= address < address + len(pattern) <= base + GLOBAL + EXTENT < 1 << 56:
        raise CheckFailed("owned-memory setup: buffer does not fit the supported CSL structural extent")
    records = [(address + 3, 17), (address + 70, 37)]
    expected = pattern[3:20] + pattern[70:107]
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    # Yama exception applies only to this short-lived test parent. It changes
    # no sysctl, other process, repository setting or target-device state.
    option = 0x59616d61  # PR_SET_PTRACER
    if prctl(option, ctypes.c_ulong(-1).value, 0, 0, 0):
        raise CheckFailed(f"owned-memory setup: PR_SET_PTRACER_ANY failed (errno={ctypes.get_errno()})")
    try:
        run_case(binary, "owned_parent_memory", [str(os.getpid()), hex(base)], plan(records),
                 0, expected, 1, 2, proof, log)
        if buffer.raw[:len(pattern)] != pattern:
            raise CheckFailed("owned-memory check: parent buffer changed")
    finally:
        if prctl(option, 0, 0, 0, 0):
            raise CheckFailed(f"owned-memory cleanup: resetting PR_SET_PTRACER failed (errno={ctypes.get_errno()})")


def main():
    if OUTPUT.exists():
        print("READER_CI_CHECK_FAILED: new reader-ci-artifacts directory required", file=sys.stderr)
        return 2
    OUTPUT.mkdir()
    proof = {"scope": SCOPE, "status": "FAILED", "build_statuses": [], "execution_cases": []}
    build_log, execution_log = [], []
    result = 1
    try:
        if sys.platform != "linux":
            raise CheckFailed("Linux is required for the real owned-process-memory check")
        proof["source_commit"] = build_step(["git", "rev-parse", "HEAD"], "source_commit", proof, build_log).decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", proof["source_commit"]):
            raise CheckFailed("source commit check failed")
        proof["source_sha256"] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        shutil.copyfile(SOURCE, OUTPUT / "quest2_csl_read.c")
        shutil.copyfile(ROOT / "LICENSE", OUTPUT / "LICENSE")
        proof["versions"] = {}
        for tool in ("clang", "ld.lld", "qemu-aarch64"):
            raw = build_step([tool, "--version"], tool + "_version", proof, build_log)
            proof["versions"][tool] = raw.decode("utf-8", errors="replace").splitlines()[0]
        obj, binary = OUTPUT / "quest2_csl_read.o", OUTPUT / "quest2_csl_read"
        build_step(["clang", "-target", "aarch64-linux-gnu", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie",
                    "-c", str(SOURCE), "-o", str(obj)], "compile", proof, build_log)
        build_step(["ld.lld", "-m", "aarch64linux", "-static", "-e", "_start", "--build-id=none",
                    "-z", "max-page-size=4096", str(obj), "-o", str(binary)], "link", proof, build_log)
        proof.update(inspect_elf(binary))
        execute_checks(binary, proof, execution_log)
        proof["status"] = "PASSED"
        result = 0
    except CheckFailed as exc:
        proof["failure"] = str(exc)
    except (OSError, ValueError, IndexError, struct.error) as exc:
        proof["failure"] = f"check infrastructure failed ({type(exc).__name__}); raw exception withheld"
    finally:
        (OUTPUT / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
        (OUTPUT / "build.log").write_text("\n".join(build_log) + "\n")
        (OUTPUT / "execution.log").write_text("\n".join(execution_log) + "\n")
    print("READER_CI_CHECK_" + proof["status"] + ": " + SCOPE)
    if result:
        print(proof["failure"], file=sys.stderr)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
