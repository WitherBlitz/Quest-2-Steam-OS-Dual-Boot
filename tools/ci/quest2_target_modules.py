#!/usr/bin/env python3
"""Package verified USB-rescue target modules offline; never installs or loads them."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tarfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci.quest2_audit import cstring, inspect_module

RELEASE = "4.19.325-cip128-st12-qkx-q2-usb"
VERMAGIC = RELEASE + " SMP preempt mod_unload modversions aarch64"
REQUIRED_MODULES = (
    "kernel/drivers/staging/qcacld-3.0/wlan.ko",
    "kernel/drivers/staging/oculus/powerstate_mgr.ko",
    "kernel/techpack/audio/soc/pinctrl_lpi_dlkm.ko",
    "kernel/techpack/audio/soc/snd_event_dlkm.ko",
    "kernel/techpack/audio/ipc/apr_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_dlkm.ko",
    "kernel/techpack/audio/dsp/adsp_loader_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_pdr_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_notifier_dlkm.ko",
    "kernel/techpack/audio/asoc/platform_dlkm.ko",
    "kernel/techpack/audio/asoc/machine_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/audio_ext_clk_up_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/stub_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/hdmi_dlkm.ko",
)
METADATA = {"modules.dep", "modules.dep.bin", "modules.alias", "modules.alias.bin",
            "modules.symbols", "modules.symbols.bin", "modules.softdep", "modules.devname",
            "modules.order", "modules.builtin", "modules.builtin.bin"}
REQUIRED_METADATA = {"modules.dep", "modules.alias", "modules.symbols",
                     "modules.order", "modules.builtin"}
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def digest(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def canonical(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or "\0" in name or
            path.is_absolute() or ".." in path.parts or str(path) != name):
        raise ValueError("noncanonical module path")
    return path


def debug_sections(data):
    # Called only after inspect_module has checked the ELF section bounds.
    table = struct.unpack_from("<Q", data, 40)[0]
    stride, count, names_index = struct.unpack_from("<HHH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, table + i * stride) for i in range(count)]
    names = sections[names_index]
    strings = data[names[4]:names[4] + names[5]]
    return [cstring(strings, section[0]) for section in sections
            if cstring(strings, section[0]).startswith((".debug", ".zdebug", ".gnu_debug",
                ".rel.debug", ".rela.debug", ".rel.zdebug", ".rela.zdebug"))]


def read_regular(root, relative, limit=MAX_FILE):
    path = root
    for part in canonical(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink in module input: " + relative)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("nonregular module input: " + relative)
        if info.st_size > limit:
            raise ValueError(f"oversized module input: {relative} ({info.st_size} > {limit} bytes)")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError("unreadable module input: " + relative) from exc
    if len(data) != info.st_size:
        raise ValueError("changing module input: " + relative)
    return data


def bundle(kernel_out, staging):
    kernel_out, staging = Path(kernel_out), Path(staging)
    if kernel_out.is_symlink() or staging.is_symlink():
        raise ValueError("symlink input directory")
    names = (".config", "arch/arm64/boot/Image", "Module.symvers",
             "include/config/kernel.release", "modules.order", "modules.builtin")
    limits = (1024 * 1024, 64 * 1024 * 1024, 16 * 1024 * 1024, 256,
              2 * 1024 * 1024, 2 * 1024 * 1024)
    inputs = {name: read_regular(kernel_out, name, limit) for name, limit in zip(names, limits)}
    if inputs["include/config/kernel.release"].decode().strip() != RELEASE:
        raise ValueError("wrong USB-rescue target kernel release")
    image = inputs["arch/arm64/boot/Image"]
    if len(image) < 64 or image[56:60] != b"ARM\x64":
        raise ValueError("target Image must have an ARM64 header")
    config = {}
    for line in inputs[".config"].decode().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            if name in config:
                raise ValueError("duplicate target config symbol")
            config[name] = value
    for name in ("ARCH_KONA", "SMP", "PREEMPT", "MODULES", "MODULE_UNLOAD", "MODVERSIONS"):
        if config.get("CONFIG_" + name) != "y":
            raise ValueError("target module ABI/config requires CONFIG_" + name + "=y")
    for name in ("QCA_CLD_WLAN", "POWERSTATE_MANAGER"):
        if config.get("CONFIG_" + name) != "m":
            raise ValueError("expected selected target module CONFIG_" + name + "=m")
    generated, providers = {}, {}
    for line in inputs["Module.symvers"].decode().splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[1] in generated:
            raise ValueError("malformed or duplicate target symvers record")
        generated[fields[1]] = int(fields[0], 16)
        providers[fields[1]] = fields[2]
    if not generated:
        raise ValueError("empty target symvers")
    order = inputs["modules.order"].decode().splitlines()
    if not order or len(order) > 512 or len(set(order)) != len(order):
        raise ValueError("empty, duplicate or oversized modules.order")
    for name in order:
        canonical(name)
        if not name.startswith("kernel/") or not name.endswith(".ko"):
            raise ValueError("unexpected modules.order entry")
    if not set(REQUIRED_MODULES) <= set(order):
        raise ValueError("selected WLAN/powerstate/audio module missing from modules.order")
    base = "lib/modules/" + RELEASE
    for name in ("lib", "lib/modules", base):
        if (staging / name).is_symlink() or not (staging / name).is_dir():
            raise ValueError("invalid installed module directory")
    if {p.name for p in (staging / "lib/modules").iterdir()} != {RELEASE}:
        raise ValueError("unexpected installed kernel release")
    root = staging / base
    files = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            if name in ("build", "source"):
                continue
            raise ValueError("unexpected installed symlink: " + name)
        if path.is_dir():
            continue
        if name not in METADATA and name not in order:
            raise ValueError("unexpected installed file: " + name)
        files[name] = read_regular(root, name)
        if sum(map(len, files.values())) > MAX_TOTAL:
            raise ValueError("target module archive exceeds size bound")
    if not set(order) | REQUIRED_METADATA <= files.keys():
        raise ValueError("missing installed module or dependency metadata")
    for name in ("modules.order", "modules.builtin"):
        if files[name] != inputs[name]:
            raise ValueError("installed metadata differs from target build: " + name)
    for name in files.keys() & METADATA:
        if not name.endswith(".bin"):
            if "\0" in files[name].decode():
                raise ValueError("invalid text module metadata: " + name)
    graph = {}
    for line in files["modules.dep"].decode().splitlines():
        name, separator, dependencies = line.partition(":")
        if not separator or name not in order or name in graph:
            raise ValueError("malformed or duplicate modules.dep record")
        deps = dependencies.split()
        if len(set(deps)) != len(deps) or not set(deps) <= set(order):
            raise ValueError("unknown or duplicate module dependency")
        graph[name] = set(deps)
    if graph.keys() != set(order):
        raise ValueError("incomplete modules.dep coverage")
    closures, active = {}, set()

    def closure(name):
        if name in active:
            raise ValueError("cyclic module dependency")
        if name not in closures:
            active.add(name)
            result = set(graph[name])
            for dep in graph[name]:
                result.update(closure(dep))
            active.remove(name)
            closures[name] = result
        return closures[name]

    modules = []
    for name in sorted(order):
        dependencies = closure(name)
        try:
            # The custom kernel uses its own generated CRCs, not stock ABI.
            abi = inspect_module(files[name], generated, generated, expected_vermagic=VERMAGIC)
            if debug_sections(files[name]):
                raise ValueError("target module still contains debug sections: " + name)
        except (IndexError, struct.error, UnicodeError) as exc:
            raise ValueError("malformed target module ELF: " + name) from exc
        for symbol in abi["undefined_symbols"]:
            provider = providers[symbol]
            if provider == "vmlinux":
                continue
            provider = "kernel/" + provider.removesuffix(".ko") + ".ko"
            canonical(provider)
            if provider != name and provider not in dependencies:
                raise ValueError("module import provider absent from dependency closure: " + symbol)
        modules.append({"path": name, **digest(files[name]), **abi})
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(base + "/" + name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=compressed, mode="wb", mtime=0) as stream:
        stream.write(raw.getvalue())
    packed = compressed.getvalue()
    report = {"schema_version": 1, "status": "OFFLINE_MODULES_VERIFIED_HANDOFF_BLOCKED",
              "target_profile": "startup-usb-child", "kernel_release": RELEASE,
              "module_count": len(modules), "modules": modules,
              "inputs": {name: digest(data) for name, data in inputs.items()},
              "files": {base + "/" + name: digest(data) for name, data in sorted(files.items())},
              "archive_bytes": len(packed), "archive_sha256": hashlib.sha256(packed).hexdigest(),
              "debug_sections_absent": True,
              "hardware_validated": False, "handoff_ready": False,
              "deployment_performed": False, "autoload_performed": False, "device_access": False,
              "scope": "Static target ELF/vermagic/generated-CRC and dependency checks only; source binding requires build provenance"}
    return packed, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel-out", "staging", "archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.archive.exists() or args.output.exists() or args.archive == args.output:
            raise ValueError("new distinct archive and report paths required")
        packed, report = bundle(args.kernel_out, args.staging)
        with args.archive.open("xb") as stream:
            stream.write(packed)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        parser.exit(2, "TARGET_MODULE_BUNDLE_FAILED; HANDOFF_BLOCKED: " + str(exc) + "\n")
    print("OFFLINE_TARGET_MODULES_VERIFIED; HANDOFF_BLOCKED; " + str(report["module_count"]) + " modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
