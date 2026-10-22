#!/usr/bin/env python3
"""Prepare a private Linux-baseline DTB; never contacts a device or permits boot."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from quest2_handoff_plan import block_argument, candidates, load_capture, plan, read_payload
from quest2_payload_check import check, parse_dtb


def validate_reserve_map(data):
    if not data or len(data) % 16 or data[-16:] != bytes(16):
        raise ValueError("complete terminated bootloader reserve map required")
    for pos in range(0, len(data)-16, 16):
        address, size = struct.unpack_from(">QQ", data, pos)
        if not size or address+size >= 1 << 64:
            raise ValueError("empty or overflowing bootloader reservation")


def reserve_map(blob):
    parse_dtb(blob)
    start, end = struct.unpack_from(">I", blob, 16)[0], struct.unpack_from(">I", blob, 8)[0]
    for pos in range(start, end-15, 16):
        if blob[pos:pos+16] == bytes(16):
            data = blob[start:pos+16]
            validate_reserve_map(data)
            return data
    raise ValueError("missing bootloader reserve-map terminator")


def encode_dtb(properties, reservations, *, boot_cpu=0, empty_nodes=()):
    validate_reserve_map(reservations)
    nodes = {"": {}}
    for node in empty_nodes:
        if (not isinstance(node,str) or "\0" in node or node.startswith("/") or
                node.endswith("/") or "//" in node):
            raise ValueError("invalid runtime node path")
        nodes.setdefault(node,{})
        while node:
            node = node.rpartition("/")[0]
            nodes.setdefault(node,{})
    for key, value in properties.items():
        if not isinstance(key, str) or not key or "\0" in key or not isinstance(value, bytes):
            raise ValueError("invalid runtime property")
        node, _, name = key.rpartition("/")
        if not name or key.startswith("/") or "//" in key:
            raise ValueError("invalid runtime property path")
        nodes.setdefault(node, {})[name] = value
        while node:
            node = node.rpartition("/")[0]
            nodes.setdefault(node, {})
    strings, offsets = bytearray(), {}
    for name in sorted({name for values in nodes.values() for name in values}):
        offsets[name] = len(strings)
        strings.extend(name.encode("ascii")+b"\0")
    children = {}
    for node in sorted(nodes):
        if node:
            children.setdefault(node.rpartition("/")[0], []).append(node)
    body = bytearray()

    def emit(node, depth=0):
        if depth >= 64:
            raise ValueError("runtime tree exceeds native DTB depth limit")
        body.extend(struct.pack(">I", 1)+node.rpartition("/")[2].encode("ascii")+b"\0")
        body.extend(bytes(-len(body) % 4))
        for name, data in sorted(nodes[node].items()):
            body.extend(struct.pack(">III", 3, len(data), offsets[name])+data)
            body.extend(bytes(-len(body) % 4))
        for child in children.get(node, []):
            emit(child, depth+1)
        body.extend(struct.pack(">I", 2))

    emit("")
    body.extend(struct.pack(">I", 9))
    structure = 40+len(reservations)
    strings_offset = structure+len(body)
    size = strings_offset+len(strings)
    if size > 0x200000:
        raise ValueError("prepared DTB exceeds native 2 MiB limit")
    header = struct.pack(">10I", 0xd00dfeed, size, structure, strings_offset,
                         40, 17, 16, boot_cpu, len(strings), len(body))
    blob = header+reservations+body+strings
    parse_dtb(blob)
    return blob


def prepare(runtime, iomem, image, initramfs, live_fdt, config, *, exclude_blocks=()):
    """Select an unclaimed copy candidate and preserve the captured live tree."""
    live, live_nodes = parse_dtb(live_fdt,with_nodes=True)
    clean = lambda props: {k:v for k,v in props.items() if k.rpartition("/")[2] != "name"}
    if clean(live) != clean(runtime):
        raise ValueError("live flattened FDT and runtime capture properties differ")
    nodes = {"/".join(node[1:]) for node in live_nodes}
    nodes.update(key.rpartition("/")[0] for key in runtime)
    chosen = [node for node in ("chosen","chosen@0") if node in nodes]
    if len(chosen) != 1:
        raise ValueError("exactly one live chosen or chosen@0 node required")
    prefix = chosen[0]+"/"
    properties = clean(runtime)
    properties[prefix+"bootargs"] = b"rdinit=/init loglevel=7\0"
    for name in ("linux,initrd-start","linux,initrd-end"):
        properties[prefix+name] = bytes(8)
    reservations = reserve_map(live_fdt)
    boot_cpu = struct.unpack_from(">I",live_fdt,28)[0]
    placeholder = encode_dtb(properties,reservations,boot_cpu=boot_cpu,empty_nodes=nodes)
    placement = plan(runtime,iomem,exclude_blocks=exclude_blocks,payload=(image,initramfs,placeholder))
    layout = placement["payload"]
    if layout["requires_loader_port"]:
        raise ValueError("copy envelope exceeds native 64 MiB limit")
    spans = [struct.unpack_from(">QQ",reservations,pos) for pos in range(0,len(reservations)-16,16)]
    exclusions = placement["ram"]["exclusions"] + [
        {"start":address,"end":address+size} for address,size in spans]
    ram = []
    for row in iomem.splitlines():
        if row.strip().endswith(": System RAM"):
            lo,hi = row.strip().split()[0].split("-")
            ram.append((int(lo,16),int(hi,16)+1))
    available = ([] if not layout["candidates"] else
                 candidates(ram,exclusions,layout["window_bytes"],layout["offsets"]))
    if not available:
        raise ValueError("no offline candidate avoiding all capture/bootloader reservations")
    selected = available[0]
    for name,value in (("linux,initrd-start",selected["initramfs_address"]),
                       ("linux,initrd-end",selected["initramfs_end"])):
        properties[prefix+name] = struct.pack(">Q",value)
    blob = encode_dtb(properties,reservations,boot_cpu=boot_cpu,empty_nodes=nodes)
    if len(blob) != len(placeholder):
        raise ValueError("prepared DTB size changed after placement")
    report = check(runtime,iomem,image,initramfs,blob,config,selected["base"],
                   exclude_blocks=exclude_blocks)
    if report["findings"]:
        raise ValueError("prepared baseline rejected: "+"; ".join(report["findings"]))
    report.update({"baseline_only":True,"ownership":"unclaimed",
                   "bootloader_reserve_map_preserved":True,
                   "live_tree_properties_match":True,"device_access":False,
                   "handoff_ready":False,"auto_boot_allowed":False})
    return blob,report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("capture","kernel-image","initramfs","kernel-config","live-fdt", "build-provenance","output"):
        ap.add_argument("--"+name,type=Path,required=True)
    ap.add_argument("--exclude-block",type=block_argument,action="append",default=[])
    args = ap.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("new private output directory required")
        files = {"Image":read_payload(args.kernel_image),"initramfs.gz":read_payload(args.initramfs),
                 "target.config":read_payload(args.kernel_config)}
        live = read_payload(args.live_fdt)
        provenance = json.loads(read_payload(args.build_provenance))
        if (provenance.get("baseline_only") is not True or provenance.get("kernel_patches") != [] or
                provenance.get("kernel_commit") != "6bde5fc06b252c97761bc0963dd13c1abcf377ab"):
            raise ValueError("matching unmodified-kernel baseline provenance required")
        for name,data in files.items():
            if provenance.get("files",{}).get(name) != {
                    "bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}:
                raise ValueError("build provenance digest/length mismatch: "+name)
        files["boot.dtb"], report = prepare(*load_capture(args.capture),files["Image"],files["initramfs.gz"],
                                          live,files["target.config"].decode("utf-8"),
                                          exclude_blocks=args.exclude_block)
        manifest = {"status":"HANDOFF_BLOCKED","baseline_only":True,"ownership":"unclaimed",
                    "source_build":provenance,"source_live_fdt_sha256":hashlib.sha256(live).hexdigest(),
                    "exclude_blocks":args.exclude_block,"base":report["base"],
                    "files":{name:{"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
                             for name,data in files.items()},
                    "scope":"PRIVATE offline baseline; manifest hashes are not a signature or boot authorization"}
        args.output.mkdir()
        for name,data in files.items():
            with (args.output/name).open("xb") as stream: stream.write(data)
        with (args.output/"payload-check.json").open("x",encoding="utf-8") as stream:
            json.dump(report,stream,indent=2); stream.write("\n")
        # Completion manifest is last; an interrupted directory is incomplete.
        with (args.output/"manifest.json").open("x",encoding="utf-8") as stream:
            json.dump(manifest,stream,indent=2); stream.write("\n")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        ap.exit(2,f"DTB_PREPARE_FAILED; HANDOFF_BLOCKED: {exc}\n")
    print("PRIVATE_DTB_PREPARED; HANDOFF_BLOCKED; 0 structural/profile findings")
    return 3


if __name__ == "__main__": raise SystemExit(main())
