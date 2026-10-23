"""Offline target checks reject Android references and lost reservations."""
import gzip
import contextlib
import importlib.util
import io
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest

from test_handoff_plan import IOMEM, capture, cells, payload, tree
from quest2_target_dtb import encode_dtb

TOOL = Path(__file__).resolve().parents[1] / "quest2_payload_check.py"


def fdt(properties,empty_nodes=()):
    nodes = {"": {}}
    for name in empty_nodes: nodes[name] = {}
    for path, data in properties.items():
        node, _, name = path.rpartition("/")
        nodes.setdefault(node, {})[name] = data
        while node:
            node = node.rpartition("/")[0]
            nodes.setdefault(node, {})
    strings, offsets = bytearray(), {}
    for props in nodes.values():
        for name in props:
            if name not in offsets:
                offsets[name] = len(strings); strings += name.encode()+b"\0"
    body = bytearray()
    def emit(node):
        body.extend(cells(1) + node.rpartition("/")[2].encode()+b"\0")
        body.extend(bytes(-len(body)%4))
        for name, data in nodes[node].items():
            body.extend(cells(3,len(data),offsets[name])+data)
            body.extend(bytes(-len(body)%4))
        for child in sorted(n for n in nodes if n and n.rpartition("/")[0] == node): emit(child)
        body.extend(cells(2))
    emit(""); body.extend(cells(9))
    return struct.pack(">10I",0xd00dfeed,56+len(body)+len(strings),56,56+len(body),40,17,16,0,len(strings),len(body))+bytes(16)+body+strings


def initramfs(*,extra=(),dirs=True,hardlink=False,shebang=b"#!/bin/sh\n",unaligned_elf=False):
    elf = bytearray(128); elf[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI",elf,16,2,183,1)
    struct.pack_into("<Q",elf,32,64); struct.pack_into("<HHH",elf,52,64,56,1)
    struct.pack_into("<Q",elf,24,0x400078)
    struct.pack_into("<IIQQQQQQ",elf,64,1,5,0,0x400000,0x400000,128,128,4096)
    if unaligned_elf:
        elf.append(0);struct.pack_into("<Q",elf,72,1);struct.pack_into("<Q",elf,112,1)
    entries = ([("bin",stat.S_IFDIR|0o755,b"")] if dirs else []) + [
               ("init",stat.S_IFREG|0o755,shebang+b"echo test\n"),
               ("bin/sh",stat.S_IFLNK|0o777,b"busybox"),
               ("bin/busybox",stat.S_IFREG|0o755,bytes(elf)), *extra, ("TRAILER!!!",0,b"")]
    out = bytearray()
    for ino,(name,mode,data) in enumerate(entries,1):
        name = name.encode()+b"\0"
        fields=(ino,mode,0,0,2 if hardlink and name==b"bin/busybox\0" else 1,0,len(data),0,0,0,0,len(name),0)
        out.extend(b"070701"+"".join(f"{x:08x}" for x in fields).encode()+name)
        out.extend(bytes(-len(out)%4)); out.extend(data); out.extend(bytes(-len(out)%4))
    return gzip.compress(bytes(out))


CONFIG = "\n".join("CONFIG_"+k+"=y" for k in (
    "ARM64","ARM64_4K_PAGES","BLK_DEV_INITRD","RD_GZIP","DEVTMPFS","USB_CONFIGFS_ECM",
    "BINFMT_ELF","BINFMT_SCRIPT"))


class PayloadCheckTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), "offline Linux payload checker is missing")
        spec = importlib.util.spec_from_file_location("quest2_payload_check",TOOL)
        self.tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.tool)

    def inputs(self):
        runtime = tree(); runtime["model"] = b"Hollywood\0"; runtime["compatible"] = b"qcom,kona\0"
        runtime["memory@100000000/device_type"] = b"memory\0"
        runtime["memory@100000000/reg"] = cells(1,0,0,0x10000000)
        image = payload()[0]; rd = initramfs()
        target = dict(runtime)
        target["chosen/linux,initrd-start"] = struct.pack(">Q",0x101e51000)
        target["chosen/linux,initrd-end"] = struct.pack(">Q",0x101e51000+len(rd))
        return runtime,image,rd,target

    def check(self, runtime,image,rd,target,config=CONFIG):
        return self.tool.check(runtime,IOMEM,image,rd,fdt(target),config,0x100000000,
                               exclude_blocks=[(0x102c00000,0x103000000)])

    def test_structurally_matching_profile_stays_handoff_blocked(self):
        report=self.check(*self.inputs())
        self.assertEqual(report["findings"],[])
        self.assertEqual(report["status"],"HANDOFF_BLOCKED")
        self.assertFalse(report["kernel_patch_provenance_verified"])
        self.assertTrue(report["blockers"])

    def test_android_config_and_init_do_not_pass_linux_profile(self):
        runtime,image,rd,target=self.inputs()
        report=self.check(runtime,image,rd,target,config="CONFIG_BLK_DEV_INITRD=y\nCONFIG_ION_POOL_AUTO_REFILL=y")
        self.assertTrue(any("CONFIG_DEVTMPFS" in s for s in report["findings"]))
        with self.assertRaises(ValueError): self.tool.check_initramfs(b"Android ramdisk")

    def test_model_and_reservation_changes_are_reported(self):
        runtime,image,rd,target=self.inputs(); target["model"]=b"Seacliff\0"
        del target["reserved-memory/firmware/reg"]
        report=self.check(runtime,image,rd,target)
        self.assertTrue(any("model" in s for s in report["findings"]))
        self.assertTrue(any("reserved-memory/firmware/reg" in s for s in report["findings"]))

    def test_wrong_initrd_addresses_are_reported(self):
        runtime,image,rd,target=self.inputs(); target["chosen/linux,initrd-start"]=cells(0x93000000)
        self.assertTrue(any("initrd-start" in s for s in self.check(runtime,image,rd,target)["findings"]))

    def test_destination_exclusions_are_enforced(self):
        runtime,image,rd,target=self.inputs()
        report=self.tool.check(runtime,IOMEM,image,rd,fdt(target),CONFIG,0x102c00000,
                               exclude_blocks=[(0x102c00000,0x103000000)])
        self.assertTrue(any("exclusion" in s for s in report["findings"]))

    def test_dtb_truncation_duplicate_properties_and_aliases_fail(self):
        runtime,image,rd,target=self.inputs(); blob=fdt(target)
        with self.assertRaises(ValueError): self.tool.parse_dtb(blob[:-1])
        duplicate=bytearray(blob[:80]+blob[64:80]+blob[80:])
        for offset in (4,12,36): struct.pack_into(">I",duplicate,offset,struct.unpack_from(">I",duplicate,offset)[0]+16)
        with self.assertRaises(ValueError): self.tool.parse_dtb(bytes(duplicate))
        target["chosen@0/test"]=b""
        self.assertTrue(any("chosen" in s for s in self.check(runtime,image,rd,target)["findings"]))

    def test_initramfs_malformed_and_decompression_limit_fail(self):
        for data in (b"",gzip.compress(b"bad"),b"070701"+b"f"*104):
            with self.subTest(data=data[:6]),self.assertRaises(ValueError): self.tool.check_initramfs(data)
        with self.assertRaises(ValueError): self.tool.check_initramfs(gzip.compress(bytes(0x4000001)))

    def test_dtb_parser_round_trip_preserves_runtime_properties(self):
        runtime,_,_,_=self.inputs()
        self.assertEqual(self.tool.parse_dtb(fdt(runtime)),runtime)

    def test_kernel_generated_reserved_node_name_is_not_required_in_flat_dtb(self):
        runtime,image,rd,target=self.inputs(); runtime["reserved-memory/name"]=b"reserved-memory\0"
        self.assertEqual(self.check(runtime,image,rd,target)["findings"],[])

    def test_newc_aliases_missing_parents_and_hardlinks_fail(self):
        for rd in (initramfs(extra=[("././init",stat.S_IFREG|0o644,b"bad")]),
                   initramfs(dirs=False),initramfs(hardlink=True),initramfs(unaligned_elf=True)):
            with self.subTest(size=len(rd)),self.assertRaises(ValueError):self.tool.check_initramfs(rd)

    def test_repository_busybox_shebang_is_supported(self):
        self.assertEqual(self.tool.check_initramfs(initramfs(shebang=b"#!/bin/busybox sh\n"))["profile"],
                         "executable shell init with static ARM64 shell")

    def test_binary_loaders_and_external_initramfs_support_are_required(self):
        for name in ("BINFMT_ELF","BINFMT_SCRIPT"):
            with self.subTest(name=name):
                report=self.check(*self.inputs(),config=CONFIG.replace("CONFIG_"+name+"=y","# CONFIG_"+name+" is not set"))
                self.assertTrue(any(name in s for s in report["findings"]))
        self.assertTrue(any("INITRAMFS_FORCE" in s for s in
                            self.check(*self.inputs(),config=CONFIG+"\nCONFIG_INITRAMFS_FORCE=y")["findings"]))

    def test_linux_visible_memory_aliases_and_usable_memory_are_checked(self):
        for variant in ("extra","usable","unterminated"):
            runtime,image,rd,target=self.inputs()
            if variant == "extra":
                target["memory@200000000/device_type"]=b"memory\0ignored"
                target["memory@200000000/reg"]=cells(2,0,0,0x1000000)
            elif variant == "usable": target["memory@100000000/linux,usable-memory"]=cells(2,0,0,0x1000000)
            else: target["memory@100000000/device_type"]=b"memory"
            with self.subTest(variant=variant):
                self.assertTrue(any("memory" in s or "device_type" in s for s in self.check(runtime,image,rd,target)["findings"]))

    def test_empty_chosen_alias_is_still_rejected(self):
        runtime,image,rd,target=self.inputs()
        report=self.tool.check(runtime,IOMEM,image,rd,fdt(target,empty_nodes=("chosen@0",)),CONFIG,0x100000000)
        self.assertTrue(any("chosen" in s for s in report["findings"]))

    def test_added_target_reservation_cannot_overlap_copy_window(self):
        runtime,image,rd,target=self.inputs()
        target["reserved-memory/new/reg"]=cells(1,0,0,0x1000000)
        self.assertTrue(any("exclusion" in s for s in self.check(runtime,image,rd,target)["findings"]))

    def test_header_reserve_map_cannot_overlap_any_part_of_copy_window(self):
        runtime,image,rd,target=self.inputs()
        base=0x100000000
        end=self.check(runtime,image,rd,target)["end"]
        for address,size in ((base-1,2),(base,1),(base+0x1000,0x1000),
                             (end-1,2),(base-1,end-base+2)):
            with self.subTest(address=address,size=size):
                blob=encode_dtb(target,struct.pack(">QQQQ",address,size,0,0))
                report=self.tool.check(runtime,IOMEM,image,rd,blob,CONFIG,base,
                                       exclude_blocks=[(0x102c00000,0x103000000)])
                self.assertTrue(any("header reserve map" in s for s in report["findings"]))
                self.assertEqual(report["status"],"HANDOFF_BLOCKED")

    def test_header_reserve_map_adjacent_ranges_are_not_overlaps(self):
        runtime,image,rd,target=self.inputs()
        base=0x100000000
        end=self.check(runtime,image,rd,target)["end"]
        reservations=struct.pack(">QQQQQQ",base-0x1000,0x1000,end,0x1000,0,0)
        blob=encode_dtb(target,reservations)
        report=self.tool.check(runtime,IOMEM,image,rd,blob,CONFIG,base,
                               exclude_blocks=[(0x102c00000,0x103000000)])
        self.assertEqual(report["findings"],[])
        self.assertEqual(report["status"],"HANDOFF_BLOCKED")

    def test_cli_writes_findings_and_never_overwrites_an_existing_report(self):
        runtime,image,rd,target=self.inputs()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); capture(root)
            paths=[]
            for name,data in (("Image",image),("initramfs",rd),("boot.dtb",fdt(target)),("config",CONFIG.encode())):
                p=root/name;p.write_bytes(data);paths.append(str(p))
            output=root/"report.json"
            args=["--capture",str(root),"--kernel-image",paths[0],"--initramfs",paths[1],
                  "--dtb",paths[2],"--kernel-config",paths[3],"--base","0x100000000","--output",str(output)]
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(self.tool.main(args),3)
            before=output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised: self.tool.main(args)
            self.assertEqual(raised.exception.code,2);self.assertEqual(output.read_bytes(),before)


if __name__ == "__main__": unittest.main()
