#!/usr/bin/env python3
"""Offline checks for the Quest 2 BusyBox Linux boot profile; never permits handoff."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import struct
import zlib

from quest2_handoff_plan import (ALIGNMENT, block_argument, load_capture, payload_requirements,
                                plan, read_payload)


def parse_dtb(data,*,with_nodes=False):
    if len(data) < 40 or len(data) > 0x200000:
        raise ValueError("DTB size outside native bounds")
    magic,total,pos,strings,reserve,version,compat,_,slen,length = struct.unpack_from(">10I",data)
    if (magic != 0xd00dfeed or total != len(data) or version != 17 or compat > 17 or
            not 40 <= reserve < pos <= strings <= total or reserve % 8 or pos % 4 or
            length > strings-pos or slen > total-strings):
        raise ValueError("unsupported/malformed canonical v17 DTB header")
    while reserve+16 <= pos:
        address,size = struct.unpack_from(">QQ",data,reserve); reserve += 16
        if not address and not size:
            break
        if not size or address+size > 1 << 64:
            raise ValueError("overflowing DTB reserve map")
    else:
        raise ValueError("unterminated DTB reserve map")
    end, stack, nodes, children, result = pos+length, [], set(), set(), {}
    while pos+4 <= end:
        token = struct.unpack_from(">I",data,pos)[0]; pos += 4
        if token == 1:
            stop = data.find(b"\0",pos,end)
            if stop < 0 or len(stack) >= 64:
                raise ValueError("invalid DTB node name/depth")
            name = data[pos:stop].decode("ascii")
            if "/" in name or (not stack and (name or nodes)) or (stack and not name):
                raise ValueError("noncanonical DTB node name")
            if stack: children.add(tuple(stack))
            stack.append(name); node = tuple(stack)
            if node in nodes: raise ValueError("duplicate DTB node")
            nodes.add(node); pos = (stop+4)&-4
        elif token == 2:
            if not stack: raise ValueError("unbalanced DTB node")
            stack.pop()
        elif token == 3:
            if not stack or tuple(stack) in children or end-pos < 8:
                raise ValueError("DTB property follows child or is truncated")
            size,name = struct.unpack_from(">II",data,pos); pos += 8
            if size > end-pos or name >= slen: raise ValueError("invalid DTB property bounds")
            stop = data.find(b"\0",strings+name,strings+slen)
            if stop < 0: raise ValueError("unterminated DTB property name")
            prop = data[strings+name:stop].decode("ascii")
            if not prop or "/" in prop: raise ValueError("invalid DTB property name")
            key = "/".join(stack[1:]+[prop])
            if key in result: raise ValueError("duplicate DTB property")
            result[key] = data[pos:pos+size]; pos = (pos+size+3)&-4
        elif token == 9:
            if stack or not nodes or pos != end: raise ValueError("invalid DTB end")
            return (result,nodes) if with_nodes else result
        elif token != 4:
            raise ValueError("unknown DTB token")
    raise ValueError("missing DTB end")


def check_initramfs(data):
    compressed = data.startswith(b"\x1f\x8b")
    try:
        if compressed:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream: data = stream.read(0x4000001)
    except (OSError,EOFError,zlib.error) as exc:
        raise ValueError("invalid gzip initramfs") from exc
    if len(data) > 0x4000000: raise ValueError("expanded initramfs exceeds 64 MiB")
    entries, pos, trailer = {}, 0, False
    while pos+110 <= len(data):
        if data[pos:pos+6] != b"070701": raise ValueError("newc initramfs required")
        if any(c not in b"0123456789abcdefABCDEF" for c in data[pos+6:pos+110]):
            raise ValueError("invalid newc hex fields")
        try: fields = [int(data[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        except ValueError as exc: raise ValueError("invalid newc header") from exc
        mode,size,nlen = fields[1],fields[6],fields[11]
        if not 0 < nlen <= 4096 or pos+110+nlen > len(data): raise ValueError("invalid newc name")
        raw_name = data[pos+110:pos+110+nlen]
        if raw_name[-1:] != b"\0" or b"\0" in raw_name[:-1]: raise ValueError("invalid newc name")
        raw_path = raw_name[:-1].decode("utf-8")
        path = PurePosixPath(raw_path)
        if not raw_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe newc path")
        name = str(path)
        if name != "TRAILER!!!":
            if name == "." and not stat.S_ISDIR(mode): raise ValueError("newc root must be a directory")
            if not stat.S_ISDIR(mode) and fields[4] != 1: raise ValueError("unsupported newc hardlink metadata")
            for parent in path.parents:
                if str(parent) != "." and not stat.S_ISDIR(entries.get(str(parent),(0,b""))[0]):
                    raise ValueError("newc parent directory must precede its children")
        start = (pos+110+nlen+3)&-4
        if start+size > len(data): raise ValueError("truncated newc contents")
        if name in entries: raise ValueError("duplicate newc path")
        entries[name] = (mode,data[start:start+size]); pos = (start+size+3)&-4
        if name == "TRAILER!!!":
            trailer = True; break
    if not trailer or any(data[pos:]): raise ValueError("missing newc trailer or unsupported trailing archive")
    mode,init = entries.get("init",(0,b""))
    shebang = init.split(b"\n",1)[0]
    if not stat.S_ISREG(mode) or not mode & 0o111 or shebang not in (b"#!/bin/sh",b"#!/bin/busybox sh"):
        raise ValueError("expected executable /init shell script for the BusyBox Linux profile")
    name = "bin/sh" if shebang == b"#!/bin/sh" else "bin/busybox"
    for _ in range(8):
        mode,elf = entries.get(name,(0,b""))
        if not stat.S_ISLNK(mode): break
        target = elf.decode("utf-8")
        path = PurePosixPath(target.lstrip("/")) if target.startswith("/") else PurePosixPath(name).parent/target
        if ".." in path.parts: raise ValueError("unsupported shell symlink traversal")
        name = str(path)
    else: raise ValueError("shell symlink loop")
    if (not stat.S_ISREG(mode) or not mode & 0o111 or len(elf) < 64 or
            elf[:7] != b"\x7fELF\x02\x01\x01" or struct.unpack_from("<H",elf,18)[0] != 183):
        raise ValueError("/bin/sh must resolve to an executable ARM64 little-endian ELF")
    phoff = struct.unpack_from("<Q",elf,32)[0]
    size,count = struct.unpack_from("<HH",elf,54)
    if size != 56 or not count or phoff < 64 or phoff+size*count > len(elf):
        raise ValueError("invalid shell ELF program headers")
    if (struct.unpack_from("<H",elf,16)[0] not in (2,3) or
            struct.unpack_from("<I",elf,20)[0] != 1 or struct.unpack_from("<H",elf,52)[0] != 64):
        raise ValueError("unsupported shell ELF header")
    if any(struct.unpack_from("<I",elf,phoff+i*size)[0] == 3 for i in range(count)):
        raise ValueError("BusyBox shell must be static; PT_INTERP found")
    entry = struct.unpack_from("<Q",elf,24)[0]
    executable_entry = False
    for i in range(count):
        kind,flags,offset,va,_,filesz,memsz,alignment = struct.unpack_from("<IIQQQQQQ",elf,phoff+i*size)
        if kind != 1: continue
        if (filesz > memsz or offset+filesz > len(elf) or va+memsz >= 1 << 64 or va % 4096 != offset % 4096 or
                (alignment > 1 and (alignment & (alignment-1) or va % alignment != offset % alignment))):
            raise ValueError("invalid shell ELF load segment")
        executable_entry |= bool(flags & 1 and va <= entry < va+filesz)
    if not executable_entry: raise ValueError("shell ELF entry is not in executable file-backed memory")
    return {"profile":"executable shell init with static ARM64 shell", "entries":len(entries),
            "gzip":compressed, "scope":"Structure only; scripts and binaries were not executed"}


def check(runtime,iomem,image,initramfs,dtb,config,base,*,exclude_blocks=()):
    layout = payload_requirements(image,initramfs,dtb)
    target,nodes = parse_dtb(dtb,with_nodes=True)
    placement = plan(runtime,iomem,exclude_blocks=exclude_blocks,payload=(image,initramfs,dtb))
    findings = []
    try:
        target_placement = plan(target,iomem)
    except ValueError as exc:
        findings.append("Target DT placement rejected: "+str(exc))
        target_placement = {"ram":{"exclusions":[],"blockers":[]}}
    if (type(base) is not int or base < 0 or base % ALIGNMENT or
            base+layout["window_bytes"] >= 1 << 64): raise ValueError("invalid compact base")
    end = base+layout["window_bytes"]
    spans = []
    for row in iomem.splitlines():
        if row.strip().endswith(": System RAM"):
            lo,hi = row.strip().split()[0].split("-"); spans.append((int(lo,16),int(hi,16)+1))
    if not any(lo <= base < end <= hi for lo,hi in spans): findings.append("Copy window crosses System RAM resource boundaries")
    if any(base < ex["end"] and ex["start"] < end for ex in
           placement["ram"]["exclusions"]+target_placement["ram"]["exclusions"]):
        findings.append("Copy window overlaps a captured or caller-supplied exclusion")
    findings.extend(s for s in placement["ram"]["blockers"] if "Unbounded" in s)
    findings.extend(s for s in target_placement["ram"]["blockers"] if "Unbounded" in s)
    if layout["requires_loader_port"]: findings.append("Copy window exceeds native 64 MiB limit")
    for key in ("model","compatible","#address-cells","#size-cells"):
        if key not in runtime or target.get(key) != runtime[key]: findings.append("Target DT mismatch: "+key)
    for key,value in runtime.items():
        if key.startswith("reserved-memory/") and key.rpartition("/")[2] != "name" and target.get(key) != value:
            findings.append("Target DT reservation not preserved: "+key)
    def memory_map(properties):
        result = {}
        for key,value in properties.items():
            if key.rpartition("/")[2] != "device_type": continue
            if not value.endswith(b"\0") or value.count(b"\0") != 1:
                findings.append("Noncanonical device_type string: "+key)
            if value.split(b"\0",1)[0] == b"memory":
                node = key.rpartition("/")[0]
                result[node] = properties.get(node+"/linux,usable-memory",properties.get(node+"/reg"))
        return result
    memory, target_memory = memory_map(runtime),memory_map(target)
    if not memory or memory != target_memory: findings.append("Target DT memory map differs from capture")
    chosen = [n for n in ("chosen","chosen@0") if ("",n) in nodes]
    if len(chosen) != 1: findings.append("Exactly one chosen or chosen@0 node is required")
    else:
        start = base+layout["offsets"]["initramfs_address"]
        for prop,value in (("linux,initrd-start",start),("linux,initrd-end",start+len(initramfs))):
            raw = target.get(chosen[0]+"/"+prop,b"")
            if len(raw) not in (4,8) or int.from_bytes(raw,"big") != value: findings.append("Target DT mismatch: "+prop)
    config_values = dict(row.split("=",1) for row in config.splitlines() if row.startswith("CONFIG_") and "=" in row)
    for name in ("ARM64","ARM64_4K_PAGES","BLK_DEV_INITRD","DEVTMPFS","USB_CONFIGFS_ECM","BINFMT_ELF","BINFMT_SCRIPT"):
        if config_values.get("CONFIG_"+name) != "y": findings.append("Linux profile requires CONFIG_"+name+"=y")
    if config_values.get("CONFIG_ION_POOL_AUTO_REFILL","n") != "n": findings.append("Linux profile requires CONFIG_ION_POOL_AUTO_REFILL=n")
    if config_values.get("CONFIG_INITRAMFS_FORCE") == "y": findings.append("CONFIG_INITRAMFS_FORCE=y would ignore the supplied external initramfs")
    try:
        rd = check_initramfs(initramfs)
        if rd["gzip"] and config_values.get("CONFIG_RD_GZIP") != "y": findings.append("gzip initramfs requires CONFIG_RD_GZIP=y")
    except ValueError as exc:
        rd = {"profile":"REJECTED"}; findings.append("Initramfs profile rejected: "+str(exc))
    return {"schema_version":1,"status":"HANDOFF_BLOCKED","findings":findings,
            "layout":layout,"base":base,"end":end,"initramfs":rd,
            "kernel_patch_provenance_verified":False,
            "kernel_config_sha256":hashlib.sha256(config.encode("utf-8")).hexdigest(),
            "blockers":["Target kernel source/patch provenance, config-to-Image binding and script behavior need separate review",
                        "Capture age and firmware reservations absent from the runtime tree remain limits",
                        "Native allocator ownership, DMA/secure shutdown and physical boot remain unproven"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("capture","kernel-image","kernel-config","initramfs","dtb","output"):
        ap.add_argument("--"+name,type=Path,required=True)
    ap.add_argument("--base",type=lambda value:int(value,0),required=True)
    ap.add_argument("--exclude-block",type=block_argument,action="append",default=[])
    args = ap.parse_args(argv)
    try:
        report = check(*load_capture(args.capture),read_payload(args.kernel_image),
                       read_payload(args.initramfs),read_payload(args.dtb),
                       read_payload(args.kernel_config).decode("utf-8"),args.base,
                       exclude_blocks=args.exclude_block)
        with args.output.open("x",encoding="utf-8") as stream:
            json.dump(report,stream,indent=2); stream.write("\n")
    except (OSError,ValueError,KeyError) as exc:
        ap.exit(2,f"PAYLOAD_CHECK_FAILED; HANDOFF_BLOCKED: {exc}\n")
    print(f"PAYLOAD_CHECK_COMPLETE; HANDOFF_BLOCKED; {len(report['findings'])} findings")
    return 3


if __name__ == "__main__": raise SystemExit(main())
