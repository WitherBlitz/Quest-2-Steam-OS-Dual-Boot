#!/usr/bin/env python3
"""Verify private diagnostic layouts against BTF; no target execution or writes."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
SPEC = {
    "CAM": ("cam_req_mgr_device", {"OPEN":"open_cnt", "LOCK":"cam_lock", "SUBDEVS":"count", "SHUTDOWN":"shutdown_state"}),
    "CORE": ("cam_req_mgr_core_device", {"HEAD":"session_head", "LOCK":"crm_lock"}),
    "SESSION": ("cam_req_mgr_core_session", {"LINKS":"links", "ENTRY":"entry", "COUNT":"num_links"}),
    "LINK": ("cam_req_mgr_core_link", {"WORKQ":"workq", "REQUEST":"req", "LOCK":"lock", "OPEN":"open_req_cnt", "DEVS":"num_devs"}),
    "REQUEST": ("cam_req_mgr_req_data", {"QUEUE":"in_q", "LOCK":"lock"}),
    "QUEUE": ("cam_req_mgr_req_queue", {"COUNT":"num_slots", "READ":"rd_idx", "WRITE":"wr_idx", "APPLIED":"last_applied_idx"}),
    "WQ": ("cam_req_mgr_core_workq", {"WORK":"work", "PENDING":"task.pending_cnt.counter", "FREE":"task.free_cnt.counter", "TASKS":"task.num_task"}),
    "DSP": ("subsys_device", {"DEV":"dev", "NAME":"wlname", "WORK":"work", "RESTART_WORK":"device_restart_work", "COUNT":"count", "CRASH":"crash_count", "LEVEL":"restart_level", "TRACK":"track", "GROUP":"restart_order"}),
    "TRACK": ("subsys_device.track", {"STATE":"state", "PRIVATE":"p_state"}),
    "DEVICE": ("subsys_device.dev", {"LOCK":"mutex", "DRIVER":"driver"}),
    "DRIVER": ("subsys_device.dev.driver", {"NAME":"name"}),
    "MUTEX": ("cam_req_mgr_device.cam_lock", {}), "WORK": ("subsys_device.work", {}),
    "LIST": ("cam_req_mgr_core_device.session_head", {"NEXT":"next", "PREV":"prev"}),
}


class BTF:
    def __init__(self, data):
        if not 24 <= len(data) <= 16*1024*1024:
            raise ValueError("bounded complete BTF required")
        magic, version, flags, hdr, to, tl, so, sl = struct.unpack_from("<HBBIIIII", data)
        if (magic != 0xeb9f or version != 1 or flags or hdr < 24 or
                hdr+so+sl != len(data) or to+tl > so or not sl):
            raise ValueError("invalid BTF header/bounds")
        self.strings = data[hdr+so:]
        if self.strings[:1] != b"\0": raise ValueError("invalid BTF string table")
        self.types = [None]
        pos, end = hdr+to, hdr+to+tl
        while pos < end:
            if pos+12 > end or len(self.types) > 200000: raise ValueError("invalid BTF type bounds")
            name, info, size = struct.unpack_from("<III", data, pos); pos += 12
            kind, count, flag = info>>24&31, info&65535, info>>31
            lengths = {1:4, 2:0, 3:12, 4:count*12, 5:count*12, 6:count*8,
                       7:0, 8:0, 9:0, 10:0, 11:0, 12:0, 13:count*8, 14:4,
                       15:count*12, 16:0, 17:4, 18:0, 19:count*12}
            if kind not in lengths or pos+lengths[kind] > end:
                raise ValueError("unsupported or truncated BTF type")
            self.string(name)
            self.types.append((name,kind,size,flag,data[pos:pos+lengths[kind]]))
            pos += lengths[kind]

    def string(self, offset):
        if not 0 <= offset < len(self.strings): raise ValueError("invalid BTF name")
        end = self.strings.find(b"\0",offset)
        if end < 0: raise ValueError("unterminated BTF name")
        try: return self.strings[offset:end].decode("utf-8")
        except UnicodeError as exc: raise ValueError("invalid BTF name encoding") from exc

    def resolve(self, index):
        seen = set()
        while True:
            if not 0 < index < len(self.types) or index in seen:
                raise ValueError("invalid/cyclic BTF reference")
            seen.add(index); record = self.types[index]
            if record[1] not in (8,9,10,11,18): return index,record
            index = record[2]

    def describe(self, index, depth=0):
        if depth > 32: raise ValueError("BTF type nesting exceeds bound")
        _, (name,kind,size,flag,data) = self.resolve(index)
        result = {"kind":kind, "size":size}
        if kind == 2:
            result["size"] = 8
            result["target"] = self.string(self.resolve(size)[1][0]) if size else "void"
        elif kind == 3:
            element, _, count = struct.unpack("<III",data)
            child = self.describe(element,depth+1)
            result.update(size=child["size"]*count, count=count, element=child)
        elif kind == 1:
            encoding = struct.unpack("<I",data)[0]
            result.update(encoding=encoding>>24, bits=encoding&255)
        elif kind == 6:
            result["values"] = {self.string(n):v for n,v in struct.iter_unpack("<Ii",data)}
        elif kind not in (4,5,16): raise ValueError("unsupported diagnostic field type")
        return result

    def field(self, current, part):
        _,record = self.resolve(current)
        if record[1] != 4: raise ValueError("non-structure diagnostic field path")
        members = [(ty,bits) for no,ty,bits in struct.iter_unpack("<III",record[4]) if self.string(no)==part]
        if len(members) != 1: raise ValueError("missing/ambiguous diagnostic field: "+part)
        index,bits = members[0]
        if record[3] and bits>>24: raise ValueError("bitfields unsupported for diagnostic")
        bit_offset = bits&0xffffff if record[3] else bits
        if bit_offset%8: raise ValueError("unaligned diagnostic field")
        if bit_offset//8+self.describe(index)["size"] > record[2]:
            raise ValueError("out-of-bounds diagnostic field")
        return index,bit_offset//8

    def structure(self, name, fields):
        parts=name.split(".")
        matches = [i for i,t in enumerate(self.types) if t and t[1]==4 and self.string(t[0])==parts[0]]
        if len(matches) != 1: raise ValueError("missing/ambiguous BTF structure: "+name)
        root=matches[0]
        for part in parts[1:]:
            root=self.field(root,part)[0]
            for _ in range(32):
                root,record=self.resolve(root)
                if record[1]!=2: break
                root=record[2]
            else: raise ValueError("cyclic pointer type anchor")
        root,record=self.resolve(root)
        if record[1]!=4: raise ValueError("structure type anchor required")
        output = {}
        for key,path in fields.items():
            current, offset = root, 0
            for part in path.split("."):
                current,extra = self.field(current,part)
                offset += extra
            output[key] = {"offset":offset, **self.describe(current)}
        return {"type":name, "size":self.types[root][2], "fields":output}


def layout_profile(data):
    btf = BTF(data)
    return {key:btf.structure(name,fields) for key,(name,fields) in SPEC.items()}


def compare_layout(actual, expected):
    if actual != expected: raise ValueError("diagnostic layout differs from pinned stock BTF")


def header(layout):
    lines = ["/* Generated by tools/quest2_diag_profile.py from verified stock BTF. */",
             "#ifndef QCD_PROFILE_H", "#define QCD_PROFILE_H"]
    for key,record in layout.items():
        lines.append(f"#define QCD_{key}_SIZE {record['size']}")
        for field,meta in record["fields"].items():
            lines.append(f"#define QCD_{key}_{field} {meta['offset']}")
    return "\n".join(lines+["#endif", ""])


def elf_data(data, *, build_only=False, image=None):
    if (len(data)<64 or data[:7]!=b"\x7fELF\x02\x01\x01" or
            struct.unpack_from("<H",data,16)[0] not in (2,3) or struct.unpack_from("<H",data,18)[0]!=183):
        raise ValueError("AArch64 ELF64 little-endian vmlinux required")
    # arm64/Makefile links CONFIG_RELOCATABLE kernels with -shared (ET_DYN).
    elf_type="ET_EXEC" if struct.unpack_from("<H",data,16)[0]==2 else "ET_DYN"
    offset = struct.unpack_from("<Q",data,40)[0]
    stride,count,names = struct.unpack_from("<HHH",data,58)
    if stride!=64 or not count or names>=count or offset+stride*count>len(data):
        raise ValueError("invalid vmlinux section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ",data,offset+i*stride) for i in range(count)]

    def content(section):
        off,size = section[4:6]
        if off+size>len(data): raise ValueError("truncated vmlinux section")
        return data[off:off+size]

    strings=content(sections[names])
    def string(blob,start):
        end=blob.find(b"\0",start)
        if not 0<=start<len(blob) or end<0: raise ValueError("invalid ELF name")
        return blob[start:end].decode("ascii")
    named={string(strings,s[0]):s for s in sections}
    try: btf=content(named[".BTF"]); table=named[".symtab"]
    except KeyError as exc: raise ValueError("vmlinux BTF/symbols required") from exc
    if table[9]!=24 or table[5]%24 or table[6]>=count: raise ValueError("invalid vmlinux symbols")
    symbols={}; names=content(sections[table[6]]); functions={}
    for no,info,other,section,value,size in struct.iter_unpack("<IBBHQQ",content(table)):
        if not no or not 0<section<count: continue
        name=string(names,no)
        if info&15==2: functions.setdefault(value,[]).append(name)
        if name in ("cam_req_mgr_is_open","g_dev","g_crm_core_dev","_text"):
            if name in symbols: raise ValueError("ambiguous diagnostic vmlinux symbol")
            symbols[name]=(value,size,section)
    if not {"cam_req_mgr_is_open","g_dev","g_crm_core_dev"}<=symbols.keys():
        raise ValueError("missing diagnostic vmlinux symbols")
    address,size,index=symbols["cam_req_mgr_is_open"]; section=sections[index]
    relative=address-section[3]
    if relative<0 or relative>=section[5] or size>4096 or relative+size>section[5]:
        raise ValueError("invalid diagnostic code mapping")
    code=content(section)[relative:relative+size]
    window=content(section)[relative:relative+min(max(size,68),256)]
    code_sha=hashlib.sha256(code).hexdigest()
    same_code=len(code)==68 and code_sha=="5c9dab3747b8aabe38c43a3cbf33e24703759e9244ab784b70c4925055a796b0"
    deltas={name:symbols[name][0]-address for name in ("g_dev","g_crm_core_dev")}
    same_addresses=deltas=={"g_dev":0x12d8c00,"g_crm_core_dev":0x12d8210}
    if not build_only and not same_code:
        raise ValueError("compiled camera anchor differs from stock code")
    if not build_only and not same_addresses:
        raise ValueError("compiled camera static addresses differ from stock relationships")
    calls=[]
    for offset in range(0,len(window)-3,4):
        instruction=struct.unpack_from("<I",window,offset)[0]
        if instruction&0xfc000000==0x94000000:
            immediate=instruction&0x3ffffff
            if immediate&0x2000000: immediate-=0x4000000
            delta=offset+immediate*4
            calls.append({"offset":offset,"relative_target":delta,
                          "symbols":functions.get(address+delta,[])[:8]})
    extra={"calls":calls}
    if image is not None:
        if not build_only or "_text" not in symbols:
            raise ValueError("built Image cross-check requires build mode and _text")
        raw_offset=address-symbols["_text"][0]
        if raw_offset<0 or raw_offset+len(window)>len(image):
            raise ValueError("built Image code window is out of bounds")
        raw=image[raw_offset:raw_offset+len(window)]
        extra.update(getter_offset_from_text=raw_offset,
                     image_sha256=hashlib.sha256(image).hexdigest(),
                     image_entry_window_sha256=hashlib.sha256(raw).hexdigest(),
                     image_entry_window_matches_elf=raw==window)
    # A module imports the running kernel's export, not the rebuilt function's
    # code or static placement. Build mode gates layouts and records code for
    # inspection; it never supplies replacement runtime constants or readiness.
    return btf, {"elf_type":elf_type,"anchor_bytes":len(code),"anchor_sha256":code_sha,
                 "entry_window_hex":window.hex(),"static_relative_addresses":deltas,
                 "stock_anchor_bytes_match":same_code,"static_relative_addresses_match":same_addresses,
                 "compiled_evidence_only":build_only,"runtime_anchor_check_required":True,**extra}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--btf",type=Path); source.add_argument("--vmlinux",type=Path)
    source.add_argument("--build-vmlinux",type=Path,
                        help="Verify compiled BTF; record built code without claiming stock binary identity")
    parser.add_argument("--built-image",type=Path,help="Cross-check build-only ELF extraction against its raw Image")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        code={"runtime_anchor_check_required":True}
        if args.vmlinux: data,code=elf_data(args.vmlinux.read_bytes())
        elif args.build_vmlinux:
            data,code=elf_data(args.build_vmlinux.read_bytes(),build_only=True,
                               image=args.built_image.read_bytes() if args.built_image else None)
            print("DIAGNOSTIC_BUILD_CODE_EVIDENCE_UNASSESSED "+json.dumps(code,separators=(",",":")))
        else: data=args.btf.read_bytes()
        if args.built_image and not args.build_vmlinux:
            raise ValueError("built Image is only valid with --build-vmlinux")
        actual=layout_profile(data)
        if args.build_vmlinux:
            print("DIAGNOSTIC_BUILD_LAYOUT_EVIDENCE_UNVERIFIED "+json.dumps(actual,separators=(",",":")))
        expected=json.loads((ROOT/"module/diag_profile.json").read_text())
        compare_layout(actual,expected)
        if header(expected)!=(ROOT/"module/diag_profile.h").read_text():
            raise ValueError("compiled profile header differs from verified layout")
        report={"layouts":actual,"code":code,"handoff_ready":False,
                "scope":"Selected layouts verified; built code is evidence only. Stock runtime anchor remains required; no shutdown or full binary equivalence"}
        with args.output.open("x",encoding="utf-8") as stream: json.dump(report,stream,indent=2);stream.write("\n")
    except (OSError,ValueError,struct.error) as exc:
        parser.exit(2,f"DIAGNOSTIC_PROFILE_FAILED: {exc}\n")
    print("DIAGNOSTIC_LAYOUT_VERIFIED; RUNTIME_ANCHOR_REQUIRED; HANDOFF_BLOCKED")


if __name__ == "__main__": main()
