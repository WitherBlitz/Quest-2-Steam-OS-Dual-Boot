#!/usr/bin/env python3
"""Apply/audit one pinned startup patch; profile validation never permits boot."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
KERNEL = "6bde5fc06b252c97761bc0963dd13c1abcf377ab"
STARTUP = "startup-gic-pending"
PATCH = "kernel/patches/quest2-startup/0001-gic-v3-clear-inherited-spi-pending-state.patch"
PATCH_SHA = "b0ac97dcbc1f8fc1797662fc87fa9222d9f4895025b1b4c6544671a035d491d6"
DRIVER = "drivers/irqchip/irq-gic-v3.c"
COMMON = "drivers/irqchip/irq-gic-common.c"
BASE = {DRIVER:"33179b0dc5ab596e9b43c6c3d58ae1727900783949bd4c859f52cd3e2ae9758a",
        COMMON:"2b976dae738afd22f434142593848eace6272bf7e187e863d5de7b3296bb2242"}
PATCHED = {**BASE,DRIVER:"119c3ed40d3cf1645a52a057fb3fa850f286b94b3626cee31012d1b0c9629abc"}


def profile_record(profile):
    if profile not in ("baseline",STARTUP):
        raise ValueError("unknown target build profile")
    return {"kernel_commit":KERNEL,"target_profile":profile,"baseline_only":profile == "baseline",
            "development_only":True,"hardware_handoff":"BLOCKED",
            "kernel_patches":[] if profile == "baseline" else [{"path":PATCH,"sha256":PATCH_SHA}],
            "kernel_source_files":dict(BASE if profile == "baseline" else PATCHED)}


def validate_provenance(data):
    if not isinstance(data,dict) or data.get("kernel_commit") != KERNEL:
        raise ValueError("matching kernel target provenance required")
    profile = data.get("target_profile","baseline")
    if (profile == "baseline" and data.get("baseline_only") is True and
            data.get("kernel_patches") == [] and data.get("hardware_handoff","BLOCKED") == "BLOCKED"):
        return profile
    expected = profile_record(STARTUP)
    if profile != STARTUP or any(data.get(key) != value for key,value in expected.items()):
        raise ValueError("exact pinned startup patch/source provenance required")
    return profile


def git(kernel, *args, **kwargs):
    result = subprocess.run(["git","-c","core.autocrlf=false","-C",str(kernel),*args],capture_output=True,**kwargs)
    if result.returncode:
        raise ValueError("kernel git operation failed: "+result.stderr.decode(errors="replace")[:300])
    return result.stdout


def verify_kernel(kernel, profile):
    expected = profile_record(profile)
    if git(kernel,"rev-parse","HEAD").decode().strip() != KERNEL:
        raise ValueError("kernel checkout commit differs from pinned source")
    changed = set(git(kernel,"diff","--name-only","HEAD").decode().splitlines())
    if changed != (set() if profile == "baseline" else {DRIVER}):
        raise ValueError("unexpected tracked kernel modifications")
    for path,sha in expected["kernel_source_files"].items():
        if hashlib.sha256((kernel/path).read_bytes()).hexdigest() != sha:
            raise ValueError("kernel source hash mismatch: "+path)
    return expected


def verify_fixtures(kernel):
    for path,fixture,name in ((DRIVER,"gic_dist_init-6bde5fc.c","static void __init gic_dist_init"),
                              (COMMON,"gic_dist_config-6bde5fc.c","void gic_dist_config")):
        text = (kernel/path).read_text()
        start = text.index(name); end = text.index("\n}",start)+2
        expected = (ROOT/"tools/tests/fixtures"/fixture).read_text().split("\n",1)[1].strip()
        if text[start:end] != expected:
            raise ValueError("host test fixture differs from actual kernel function: "+path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kernel",type=Path,required=True)
    ap.add_argument("--profile",required=True)
    ap.add_argument("--action",choices=("apply","verify"),required=True)
    ap.add_argument("--output",type=Path)
    args = ap.parse_args(argv)
    try:
        profile_record(args.profile)
        if args.action == "apply":
            if not args.output or args.output.exists():
                raise ValueError("new profile output file required")
            verify_kernel(args.kernel,"baseline")
            verify_fixtures(args.kernel)
            if args.profile == STARTUP:
                patch = (ROOT/PATCH).read_bytes().replace(b"\r\n",b"\n")
                if hashlib.sha256(patch).hexdigest() != PATCH_SHA:
                    raise ValueError("startup patch differs from reviewed bytes")
                git(args.kernel,"apply","--check","--whitespace=error","-",input=patch)
                git(args.kernel,"apply","--whitespace=error","-",input=patch)
        record = verify_kernel(args.kernel,args.profile)
        if args.action == "apply":
            with args.output.open("x",encoding="utf-8") as stream:
                json.dump(record,stream,indent=2);stream.write("\n")
    except (OSError,ValueError) as exc:
        ap.exit(2,f"TARGET_PROFILE_FAILED; HANDOFF_BLOCKED: {exc}\n")
    print(f"TARGET_PROFILE_VERIFIED; HANDOFF_BLOCKED; {args.profile}")


if __name__ == "__main__": main()
