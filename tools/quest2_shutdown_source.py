#!/usr/bin/env python3
"""Verify pinned public shutdown sources/fixtures; no device or hardware proof."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile

KERNEL = "6bde5fc06b252c97761bc0963dd13c1abcf377ab"
ARCHIVE_BYTES = 204122036
ARCHIVE_SHA = "12b987d5672ffc09fe6d3ff7d7d926074305484fc896141824b5ece15d620bca"
SOURCE_HASHES = {
    "drivers/soc/qcom/watchdog_v2.c": "2c9a2cc119b6ad09e176138045f765b4dcca68b19780dbc6ae2c49ebbf0086d4",
    "techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c": "102fafbac93a11d561acbef292970094c3196c45a172e9b365dad64995f848a2",
    "techpack/audio/dsp/adsp-loader.c": "070195f7ddb51d1337b966f8767d5218403f8d1e3d3730cad1d24d25b9e3c5f9",
    "drivers/soc/qcom/subsystem_restart.c": "2c9b172257a4fef531da6595ff7c42c943bb5cea0c8859975dc7340fda78e455",
    "drivers/iommu/arm-smmu.c": "fb8f3228de72f5d4ca853f5654ab19a8fb98693354b096391a2ff841d9e2682e",
    "drivers/base/core.c": "79a0c6634c11fc1ba538483925af0b9e05d5dd48fcca4ffc86e2df71b96e57c0",
    "kernel/panic.c": "3cdb3d83d35019a4dacaea9e7fd6c2aaea090275985b02aa711e2a7e4dbf6999",
    "include/soc/qcom/watchdog.h": "aea46457a8309dd6676169ab756d694b26f70c6581612591f65b71484549648b",
    "drivers/power/reset/msm-poweroff.c": "2615b74b10809e8ff9b99f497bf2943efd1e696232a713ce9d1ab0f40d27533d",
}
WATCHDOG_FUNCTIONS = ("panic_wdog_handler", "wdog_disable", "wdog_disable_get", "wdog_disable_set")
CAMERA_FUNCTIONS = ("cam_req_mgr_rwsem_write_op", "cam_req_mgr_close")


def function(text, name):
    match = re.search(r"^(?:static )?(?:int|void|ssize_t)\s+" + re.escape(name) + r"\(", text, re.M)
    if not match:
        raise ValueError("missing source/fixture function: " + name)
    end = text.find("\n}", match.start())
    if end < 0:
        raise ValueError("unterminated source/fixture function: " + name)
    return text[match.start():end+2]


def verify_fixture(source, filename, names):
    fixture = (Path(__file__).parent / "tests/fixtures" / filename).read_text()
    for name in names:
        if function(source, name) != function(fixture, name):
            raise ValueError("fixture differs from pinned source: " + name)


def verify_watchdog_fixture(source):
    verify_fixture(source, "watchdog-6bde5fc.c", WATCHDOG_FUNCTIONS)


def verify_camera_fixture(source):
    verify_fixture(source, "camera-close-6bde5fc.c", CAMERA_FUNCTIONS)


def verify_dsp_fixture(source):
    verify_fixture(source, "subsystem-put-6bde5fc.c", ("subsystem_put",))


def audit_sources(path):
    path = Path(path)
    if path.stat().st_size != ARCHIVE_BYTES:
        raise ValueError("exact pinned source archive size required")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA:
        raise ValueError("exact pinned source archive hash required")
    sources = {}
    # Read selected regular files only; nothing is extracted to the filesystem.
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = member.name.removeprefix("kernel/")
            if member.name != "kernel/" + name or name not in SOURCE_HASHES:
                continue
            if name in sources or not member.isfile() or member.size > 2*1024*1024:
                raise ValueError("invalid pinned source archive member")
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != SOURCE_HASHES[name]:
                raise ValueError("pinned source hash mismatch: " + name)
            sources[name] = data
    if sources.keys() != SOURCE_HASHES.keys():
        raise ValueError("pinned source archive is missing audited files")
    verify_watchdog_fixture(sources["drivers/soc/qcom/watchdog_v2.c"].decode())
    verify_camera_fixture(sources["techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c"].decode())
    verify_dsp_fixture(sources["drivers/soc/qcom/subsystem_restart.c"].decode())
    return {"kernel_commit": KERNEL, "archive_sha256": digest,
            "source_files": dict(SOURCE_HASHES), "watchdog_fixture_functions": list(WATCHDOG_FUNCTIONS),
            "camera_fixture_functions": list(CAMERA_FUNCTIONS), "dsp_fixture_functions": ["subsystem_put"],
            "source_verified": True, "stock_binary_equivalence_verified": False,
            "scope": "Pinned public source and exact watchdog/camera/DSP function fixtures only",
            "hardware_shutdown_validated": False, "recovery_validated": False,
            "handoff_ready": False, "status": "HANDOFF_BLOCKED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("new output file required")
        report = audit_sources(args.kernel_source)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(2, f"SOURCE_AUDIT_FAILED; HANDOFF_BLOCKED: {exc}\n")
    print("SOURCE_VERIFIED; HANDOFF_BLOCKED")
    return 3


if __name__ == "__main__": raise SystemExit(main())
