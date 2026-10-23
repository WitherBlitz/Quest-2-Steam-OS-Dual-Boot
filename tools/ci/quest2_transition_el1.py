#!/usr/bin/env python3
"""Execute production EL1 transition code in QEMU RAM, never on a headset."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/tests"))
from test_transition_assembly import fixture

SCOPE = "QEMU virt EL1 architectural execution only; Quest hardware, cache coherency, Linux boot and recovery unvalidated"
EXPECTED = ("QKX_EL1_READY\nPASS probe\nPASS null\nPASS empty_chain\n"
            "PASS single\nPASS descriptor_chain\nPASS entry\nPASS all\n")


def invoke(command, *, timeout=30):
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def build(args, output, variant, text):
    assembly = output / (variant + ".S")
    assembly.write_text(text, encoding="utf-8", newline="\n")
    objects = []
    for name, source in (("transition", assembly),
                         ("start", ROOT / "tools/tests/transition_el1_start.S"),
                         ("harness", ROOT / "tools/tests/transition_el1_harness.c")):
        obj = output / (variant + "-" + name + ".o")
        flags = ["-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-mgeneral-regs-only",
                 "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-pic", "-fno-pie"] if name == "harness" else []
        if variant == "hang" and name == "harness":
            flags.append("-DQKX_HANG=1")
        command = [args.clang, "-target", "aarch64-none-elf", *flags, "-c", str(source), "-o", str(obj)]
        result = invoke(command)
        if result.returncode:
            raise RuntimeError(f"{variant}/{name} compile failed: " + result.stderr.decode(errors="replace"))
        objects.append(str(obj))
    binary = output / (variant + ".elf")
    linker = [args.lld, "-flavor", "gnu"] if Path(args.lld).name in ("lld", "lld.exe") else [args.lld]
    result = invoke([*linker, "-m", "aarch64elf", "-static", "--no-relax", "--build-id=none",
                     "-T", str(ROOT / "tools/tests/transition_el1.ld"), *objects, "-o", str(binary)])
    if result.returncode:
        raise RuntimeError(variant + " link failed: " + result.stderr.decode(errors="replace"))
    return binary


def guest(qemu, binary, timeout=20):
    return invoke([qemu, "-machine", "virt-6.2,secure=off,virtualization=off", "-cpu", "cortex-a57",
                   "-accel", "tcg", "-smp", "1", "-m", "128M", "-nodefaults", "-display", "none",
                   "-serial", "none", "-monitor", "none", "-nic", "none", "-no-reboot",
                   "-semihosting-config", "enable=on,target=native", "-kernel", str(binary)], timeout=timeout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/transition-el1")
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--lld", default="ld.lld")
    parser.add_argument("--qemu", default="qemu-system-aarch64")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    proof = {"scope": SCOPE, "status": "FAILED", "handoff_ready": False,
             "auto_boot_allowed": False, "device_access": False, "cases": [], "sources": {}}
    result_code = 1
    try:
        commit = invoke(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        proof["source_commit"] = commit.stdout.decode().strip()
        if commit.returncode or not re.fullmatch(r"[0-9a-f]{40}", proof["source_commit"]):
            raise RuntimeError("source commit unavailable")
        for name in ("module/transition.S", "tools/tests/test_transition_assembly.py",
                     "tools/tests/transition_el1_start.S", "tools/tests/transition_el1_harness.c",
                     "tools/tests/transition_el1.ld", "tools/ci/quest2_transition_el1.py"):
            source = ROOT / name
            proof["sources"][name] = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source, args.output / source.name)
        shutil.copyfile(ROOT / "LICENSE", args.output / "LICENSE")
        proof["versions"] = {}
        linker = [args.lld, "-flavor", "gnu"] if Path(args.lld).name in ("lld", "lld.exe") else [args.lld]
        versions = [("clang", [args.clang, "--version"]), ("lld", [*linker, "--version"])]
        if not args.build_only:
            versions.append(("qemu", [args.qemu, "--version"]))
        for label, command in versions:
            version = invoke(command)
            if version.returncode:
                raise RuntimeError(label + " version check failed")
            proof["versions"][label] = version.stdout.decode(errors="replace").splitlines()[0]
        text = fixture((ROOT / "module/transition.S").read_text())
        production = build(args, args.output, "production", text)
        proof["binary_sha256"] = hashlib.sha256(production.read_bytes()).hexdigest()
        if args.build_only:
            proof["status"] = "BUILT_NOT_EXECUTED"
        else:
            positive = guest(args.qemu, production)
            output = (positive.stdout + positive.stderr).decode("utf-8", errors="replace")
            (args.output / "execution.log").write_text(output)
            if positive.returncode != 0 or output != EXPECTED:
                raise RuntimeError(f"production execution failed (exit={positive.returncode}): " + output)
            proof["cases"].append({"case": "production", "status": 0, "markers": output.splitlines()})
            # A deliberately faulty copy loop must fail the whole-page assertion.
            if text.count("mov x11, #256") != 1:
                raise RuntimeError("negative control needs source review")
            mutant = build(args, args.output, "short-copy", text.replace("mov x11, #256", "mov x11, #255", 1))
            negative = guest(args.qemu, mutant)
            output = (negative.stdout + negative.stderr).decode("utf-8", errors="replace")
            if negative.returncode != 1 or output != EXPECTED.split("PASS single")[0] + "FAIL copy\n":
                raise RuntimeError("short-copy negative control did not fail at the expected assertion: " + output)
            proof["cases"].append({"case": "short_copy_detected", "status": negative.returncode})
            if text.count("mov x1, xzr") != 1:
                raise RuntimeError("entry negative control needs source review")
            bad_entry = build(args, args.output, "bad-entry", text.replace("mov x1, xzr", "mov x1, #1", 1))
            negative = guest(args.qemu, bad_entry)
            output = (negative.stdout + negative.stderr).decode("utf-8", errors="replace")
            if negative.returncode != 1 or output != EXPECTED.split("PASS entry")[0] + "FAIL entry_registers\n":
                raise RuntimeError("bad-entry negative control did not fail at the expected assertion: " + output)
            proof["cases"].append({"case": "bad_entry_register_detected", "status": negative.returncode})
            hanging = build(args, args.output, "hang", text)
            try:
                guest(args.qemu, hanging, timeout=2)
            except subprocess.TimeoutExpired as exc:
                output = ((exc.stdout or b"") + (exc.stderr or b"")).decode(errors="replace")
                if output != "QKX_EL1_READY\nQKX_EL1_HANG_READY\n":
                    raise RuntimeError("timeout guest did not reach its deliberate hang") from None
                proof["cases"].append({"case": "host_timeout_terminated_guest", "timeout_seconds": 2})
            else:
                raise RuntimeError("deliberately hanging guest unexpectedly returned")
            proof["status"] = "PASSED"
        result_code = 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        proof["failure"] = str(exc)
    finally:
        (args.output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print("TRANSITION_EL1_" + proof["status"] + ": " + SCOPE)
    if result_code:
        print(proof["failure"], file=sys.stderr)
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
