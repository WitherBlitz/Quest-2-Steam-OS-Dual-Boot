#!/usr/bin/env python3
"""Strict host-only gates for the pinned Quest 2 offline build."""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import struct


CONFIG_SHA = "d91704d25163ec74a508273c3d7dea2f6ee69ebe810fd731b4068a984f386b8a"
VERMAGIC = "4.19.325-cip128-st12-g49638c7a8637 SMP preempt mod_unload modversions aarch64"


def decode_config(encoded, expected_sha=CONFIG_SHA):
    try:
        packed = base64.b64decode(encoded, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            config = stream.read(1024*1024+1)
    except (ValueError, OSError, EOFError) as exc:
        raise ValueError("Private config encoding is invalid") from exc
    if not config or len(config) > 1024*1024:
        raise ValueError("Private config size is invalid")
    if hashlib.sha256(config).hexdigest() != expected_sha:
        raise ValueError("Private config digest does not match the approved input")
    return config


def cstring(data, offset):
    if not 0 <= offset < len(data):
        raise ValueError("Invalid ELF string offset")
    return data[offset:data.index(b"\0", offset)].decode("ascii")


def inspect_module(data, generated, stock):
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("Expected ELF64 little-endian module")
    if struct.unpack_from("<HH", data, 16) != (1, 183):
        raise ValueError("Expected relocatable AArch64 module")
    table = struct.unpack_from("<Q", data, 40)[0]
    stride, count, names_index = struct.unpack_from("<HHH", data, 58)
    if stride != 64 or not count or names_index >= count or table+count*stride > len(data):
        raise ValueError("Invalid ELF section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, table+i*stride) for i in range(count)]

    def contents(section):
        offset, size = section[4:6]
        if offset+size > len(data):
            raise ValueError("Truncated ELF section")
        return data[offset:offset+size]

    names = contents(sections[names_index])
    by_name = {cstring(names, section[0]): section for section in sections}
    try:
        info = contents(by_name[".modinfo"])
        symbols = by_name[".symtab"]
        versions = contents(by_name["__versions"])
    except KeyError as exc:
        raise ValueError("Required ELF metadata section missing") from exc
    magic = [part[9:].decode("ascii").rstrip(" ") for part in info.split(b"\0")
             if part.startswith(b"vermagic=")]
    if magic != [VERMAGIC]:
        raise ValueError("Module vermagic differs from the pinned stock kernel")
    if symbols[9] != 24 or symbols[5] % 24 or symbols[6] >= count:
        raise ValueError("Invalid ELF symbol table")
    strings = contents(sections[symbols[6]])
    symdata = contents(symbols)
    undefined = set()
    for offset in range(0, len(symdata), 24):
        name, binding, _, section, _, _ = struct.unpack_from("<IBBHQQ", symdata, offset)
        if name and section == 0 and binding >> 4 in (1, 2):
            undefined.add(cstring(strings, name))
    if not versions or len(versions) % 64:
        raise ValueError("Invalid module version table")
    rows = {}
    for offset in range(0, len(versions), 64):
        crc = struct.unpack_from("<Q", versions, offset)[0]
        name = cstring(versions[offset+8:offset+64], 0)
        if not name or name in rows:
            raise ValueError("Empty or duplicate module version record")
        if generated.get(name) != crc or stock.get(name) != crc:
            raise ValueError("Missing or mismatching generated/stock CRC: " + name)
        rows[name] = f"0x{crc:08x}"
    if "module_layout" not in rows or not undefined <= rows.keys():
        raise ValueError("Undefined symbol lacks a version record or module_layout is missing")
    result = {"elf": "ELF64 LE REL AArch64", "vermagic": magic[0],
              "undefined_symbols": sorted(undefined), "versioned_imports": len(rows),
              "crc_rows": rows}
    if b"parmtype=ram_diagnose:bool" in info.split(b"\0"):
        indirect = {}
        for name in ("register_kretprobe", "unregister_kretprobe"):
            crc = stock.get(name)
            if crc is None or generated.get(name) != crc:
                raise ValueError("Missing or mismatching indirect ABI CRC: " + name)
            indirect[name] = f"0x{crc:08x}"
        result["indirect_crc_rows"] = indirect
    return result


def load_symvers(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split()
        if len(fields) < 3:
            raise ValueError("Malformed Module.symvers")
        crc, name = int(fields[0], 16), fields[1]
        if name in values and values[name] != crc:
            raise ValueError("Conflicting Module.symvers records")
        values[name] = crc
    if not values:
        raise ValueError("Empty Module.symvers")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    config = subs.add_parser("config")
    config.add_argument("destination", type=Path)
    audit = subs.add_parser("modules")
    audit.add_argument("--symvers", type=Path, required=True)
    audit.add_argument("--config", type=Path, required=True)
    audit.add_argument("--loader-command", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("modules", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "config":
        data = decode_config(os.environ.get("Q2_CONFIG_GZ_B64", ""))
        with args.destination.open("xb") as stream:
            stream.write(data)
        print("Private config digest verified")
        return
    if hashlib.sha256(args.config.read_bytes()).hexdigest() != CONFIG_SHA:
        raise ValueError("Build changed the approved config")
    command = args.loader_command.read_text()
    if "-DQKX_ENABLE_EXPERIMENTAL_JUMP" in command:
        raise ValueError("Experimental jump was enabled in the effective compile command")
    stock = json.loads(Path(__file__).with_name("quest2_stock_crcs.json").read_text())
    stock = {key: int(value, 16) for key, value in stock["symbols"].items()}
    generated = load_symvers(args.symvers)
    manifest = {"schema_version": 1, "config_sha256": CONFIG_SHA,
                "stage_only": True, "hardware_handoff": "unproven", "modules": []}
    for path in args.modules:
        data = path.read_bytes()
        result = inspect_module(data, generated, stock)
        manifest["modules"].append({"file": path.name, "bytes": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(), **result})
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print("Module ELF, vermagic, import coverage and generated/stock CRC gates passed")


if __name__ == "__main__":
    main()
