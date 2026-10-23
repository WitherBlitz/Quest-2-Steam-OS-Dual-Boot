#!/usr/bin/env python3
"""Apply/audit pinned startup patches; profile validation never permits boot."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
KERNEL = "6bde5fc06b252c97761bc0963dd13c1abcf377ab"
STARTUP = "startup-gic-pending"
USB_CHILD = "startup-usb-child"
PATCH = "kernel/patches/quest2-startup/0001-gic-v3-clear-inherited-spi-pending-state.patch"
PATCH_SHA = "b0ac97dcbc1f8fc1797662fc87fa9222d9f4895025b1b4c6544671a035d491d6"
DRIVER = "drivers/irqchip/irq-gic-v3.c"
COMMON = "drivers/irqchip/irq-gic-common.c"
BASE = {DRIVER:"33179b0dc5ab596e9b43c6c3d58ae1727900783949bd4c859f52cd3e2ae9758a",
        COMMON:"2b976dae738afd22f434142593848eace6272bf7e187e863d5de7b3296bb2242"}
PATCHED = {**BASE,DRIVER:"119c3ed40d3cf1645a52a057fb3fa850f286b94b3626cee31012d1b0c9629abc"}
DWC3 = "drivers/usb/dwc3/dwc3-msm.c"
DWC3_BASE_SHA = "0806ec78c52917f57bbdd713b61c2117ba48d9da502b2043f7e85e75f521adca"
DWC3_PATCH = "kernel/patches/quest2-startup/0002-dwc3-msm-select-available-controller-child.patch"
DWC3_PATCH_SHA = "42211b7e8a219a4243bc10bef82ed9c0f0d66da5f99dbb4d221636052c22f45e"
DWC3_PATCHED_SHA = "7d9a391677a6571d5eee31468579a8b1c773b9be58ddb64c6ffd716d9020c91f"


def profile_record(profile):
    if profile not in ("baseline",STARTUP,USB_CHILD):
        raise ValueError("unknown target build profile")
    record = {"kernel_commit":KERNEL,"target_profile":profile,"baseline_only":profile == "baseline",
            "development_only":True,"hardware_handoff":"BLOCKED",
            "kernel_patches":[] if profile == "baseline" else [{"path":PATCH,"sha256":PATCH_SHA}],
            "kernel_source_files":dict(BASE if profile == "baseline" else PATCHED)}
    if profile == USB_CHILD:
        record["kernel_patches"].append({"path":DWC3_PATCH,"sha256":DWC3_PATCH_SHA})
        record["kernel_source_files"][DWC3] = DWC3_PATCHED_SHA
        record.update(hardware_validated=False,handoff_ready=False)
    return record


def validate_provenance(data):
    if not isinstance(data,dict) or data.get("kernel_commit") != KERNEL:
        raise ValueError("matching kernel target provenance required")
    profile = data.get("target_profile","baseline")
    if (profile == "baseline" and data.get("baseline_only") is True and
            data.get("kernel_patches") == [] and data.get("hardware_handoff","BLOCKED") == "BLOCKED"):
        return profile
    expected = profile_record(profile)
    if profile == USB_CHILD and any(type(data.get(key)) is not type(value) for key,value in expected.items()):
        raise ValueError("exact pinned startup patch/source provenance types required")
    if profile not in (STARTUP,USB_CHILD) or any(data.get(key) != value for key,value in expected.items()):
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
    wanted = set() if profile == "baseline" else {DRIVER}
    if profile == USB_CHILD: wanted.add(DWC3)
    if changed != wanted:
        raise ValueError("unexpected tracked kernel modifications")
    for path,sha in expected["kernel_source_files"].items():
        if hashlib.sha256((kernel/path).read_bytes()).hexdigest() != sha:
            raise ValueError("kernel source hash mismatch: "+path)
    return expected


def verify_fixtures(kernel, profile=STARTUP):
    for path,fixture,name in ((DRIVER,"gic_dist_init-6bde5fc.c","static void __init gic_dist_init"),
                              (COMMON,"gic_dist_config-6bde5fc.c","void gic_dist_config")):
        text = (kernel/path).read_text()
        start = text.index(name); end = text.index("\n}",start)+2
        expected = (ROOT/"tools/tests/fixtures"/fixture).read_text().split("\n",1)[1].strip()
        if text[start:end] != expected:
            raise ValueError("host test fixture differs from actual kernel function: "+path)
    if profile == USB_CHILD:
        raw = (kernel/DWC3).read_bytes()
        if hashlib.sha256(raw).hexdigest() != DWC3_BASE_SHA:
            raise ValueError("kernel source hash mismatch: "+DWC3)
        text = raw.decode()
        start = text.index("\t/* Assumes dwc3 is the first DT child of dwc3-msm */")
        end = text.index("\n\tmdwc->hs_phy",start)
        fixture = ROOT/"tools/tests/fixtures/dwc3_msm_probe-child-6bde5fc.c"
        if text[start:end] != fixture.read_text().split("\n",1)[1]:
            raise ValueError("host test fixture differs from actual kernel child block: "+DWC3)


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
            verify_fixtures(args.kernel,args.profile)
            patches = []
            for entry in profile_record(args.profile)["kernel_patches"]:
                patch = (ROOT/entry["path"]).read_bytes().replace(b"\r\n",b"\n")
                if hashlib.sha256(patch).hexdigest() != entry["sha256"]:
                    raise ValueError("startup patch differs from reviewed bytes: "+entry["path"])
                patches.append(patch)
            if patches:
                patch = b"\n".join(patches)
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
