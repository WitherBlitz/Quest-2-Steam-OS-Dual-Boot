"""Prepared DTBs must preserve the live bootloader reserve map and runtime tree."""
import importlib.util
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import unittest
import tempfile
from unittest.mock import patch

import test_payload_check as payload_fixture
from test_payload_check import CONFIG, fdt
from test_handoff_plan import IOMEM

TOOL = Path(__file__).resolve().parents[1] / "quest2_target_dtb.py"


class TargetDtbTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), "local target DTB preparer is missing")
        spec = importlib.util.spec_from_file_location("quest2_target_dtb", TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_serialization_preserves_properties_and_nonzero_bootloader_reservations(self):
        props = {"model": b"Hollywood\0", "chosen/bootargs": b"rdinit=/init loglevel=7\0",
                 "chosen/linux,initrd-start": struct.pack(">Q",0x100000000),
                 "chosen/linux,initrd-end": struct.pack(">Q",0x100100000)}
        reserve = struct.pack(">QQQQ",0x81000000,0x100000,0,0)
        blob = self.tool.encode_dtb(props,reserve)
        from quest2_payload_check import parse_dtb
        self.assertEqual(parse_dtb(blob),props)
        self.assertEqual(self.tool.reserve_map(blob),reserve)

    def test_reserve_map_requires_terminator_and_nonoverflowing_entries(self):
        for reserve in (b"",bytes(15),struct.pack(">QQ",1,1),
                        struct.pack(">QQQQ",(1<<64)-1,2,0,0)):
            with self.subTest(reserve=reserve),self.assertRaises(ValueError):
                self.tool.encode_dtb({"model":b"Hollywood\0"},reserve)

    def test_flattened_fdt_is_required_instead_of_inventing_a_reserve_map(self):
        with self.assertRaises(ValueError): self.tool.reserve_map(b"not a live FDT")

    def test_serialization_is_deterministic_and_keeps_node_order_valid(self):
        props={"z/child/value":b"data","z/root-value":b"root","a/value":b"a"}
        first=self.tool.encode_dtb(props,bytes(16))
        self.assertEqual(first,self.tool.encode_dtb(dict(reversed(list(props.items()))),bytes(16)))

    def test_overflowing_or_appended_live_fdt_is_refused(self):
        blob=fdt({"model":b"Hollywood\0"})
        with self.assertRaises(ValueError): self.tool.reserve_map(blob+b"extra")

    def test_empty_nodes_survive_serialization(self):
        from quest2_payload_check import parse_dtb
        blob = self.tool.encode_dtb({"model":b"Hollywood\0"},bytes(16),
                                    empty_nodes=("chosen@0","empty/child"))
        nodes = parse_dtb(blob,with_nodes=True)[1]
        self.assertIn(("","chosen@0"),nodes)
        self.assertIn(("","empty","child"),nodes)

    def inputs(self):
        runtime,image,rd,_ = payload_fixture.PayloadCheckTests.inputs(self)
        runtime["chosen/bootargs"] = b"androidboot.example=1\0"
        runtime["empty/name"] = b"empty\0"
        live = fdt({k:v for k,v in runtime.items() if not k.endswith("/name")},
                   empty_nodes=("empty",))
        return runtime,image,rd,live

    def test_prepare_preserves_tree_and_replaces_only_boot_parameters(self):
        runtime,image,rd,live = self.inputs()
        blob,report = self.tool.prepare(runtime,IOMEM,image,rd,live,CONFIG,
                                        exclude_blocks=[(0x102c00000,0x103000000)])
        from quest2_payload_check import parse_dtb
        props,nodes = parse_dtb(blob,with_nodes=True)
        self.assertEqual(props["chosen/bootargs"],b"rdinit=/init loglevel=7\0")
        self.assertEqual(report["findings"],[])
        self.assertEqual(report["status"],"HANDOFF_BLOCKED")
        self.assertEqual(report["base"],0x100000000)
        self.assertIn(("","empty"),nodes)
        self.assertNotIn("empty/name",props)
        self.assertEqual(self.tool.reserve_map(blob),self.tool.reserve_map(live))
        for key,value in runtime.items():
            if not key.startswith("chosen/") and not key.endswith("/name"):
                self.assertEqual(props[key],value)

    def test_prepare_excludes_bootloader_reservations(self):
        runtime,image,rd,live = self.inputs()
        reserve = struct.pack(">QQQQ",0x100000000,0x2400000,0,0)
        from quest2_payload_check import parse_dtb
        live = self.tool.encode_dtb(parse_dtb(live),reserve,empty_nodes=("empty",))
        blob,report = self.tool.prepare(runtime,IOMEM,image,rd,live,CONFIG)
        self.assertGreaterEqual(report["base"],0x102400000)
        self.assertEqual(self.tool.reserve_map(blob),reserve)

    def test_prepare_refuses_stale_live_tree_or_empty_chosen_alias(self):
        runtime,image,rd,live = self.inputs()
        wrong = dict(runtime); wrong["model"]=b"other\0"
        for blob in (fdt(wrong),fdt(runtime,empty_nodes=("chosen@0",))):
            with self.subTest(blob=len(blob)),self.assertRaises(ValueError):
                self.tool.prepare(runtime,IOMEM,image,rd,blob,CONFIG)

    def test_header_reservations_are_applied_before_eight_candidate_limit(self):
        runtime,image,rd,_ = self.inputs()
        runtime["memory@100000000/reg"] = struct.pack(">QQ",0x100000000,0x40000000)
        reserve=struct.pack(">QQQQ",0x100000000,0x10000000,0,0)
        live=self.tool.encode_dtb(runtime,reserve)
        _,report=self.tool.prepare(runtime,"80000000-13fffffff : System RAM\n",image,rd,live,CONFIG)
        self.assertEqual(report["base"],0x110000000)

    def test_compact_candidate_does_not_require_a_legacy_52_mib_extent(self):
        runtime,image,rd,live=self.inputs()
        _,report=self.tool.prepare(runtime,"100000000-101ffffff : System RAM\n",image,rd,live,CONFIG)
        self.assertEqual(report["base"],0x100000000)

    def test_prepare_refuses_bad_config_and_unbounded_reservation(self):
        runtime,image,rd,live = self.inputs()
        with self.assertRaises(ValueError):
            self.tool.prepare(runtime,IOMEM,image,rd,live,"")
        del runtime["reserved-memory/secure/alloc-ranges"]
        with self.assertRaises(ValueError):
            self.tool.prepare(runtime,IOMEM,image,rd,fdt(runtime),CONFIG)

    def test_cli_hash_binding_and_existing_output_are_enforced(self):
        runtime,image,rd,live = self.inputs()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            data={"Image":image,"initramfs.gz":rd,"target.config":CONFIG.encode(),"live.dtb":live}
            for name,value in data.items(): (root/name).write_bytes(value)
            provenance={"baseline_only":True,"kernel_patches":[],
                        "kernel_commit":"6bde5fc06b252c97761bc0963dd13c1abcf377ab",
                        "files":{name:{"bytes":len(data[name]),"sha256":hashlib.sha256(data[name]).hexdigest()}
                                 for name in ("Image","initramfs.gz","target.config")}}
            path=root/"provenance.json"; path.write_text(json.dumps(provenance))
            args=["--capture",str(root),"--kernel-image",str(root/"Image"),
                  "--initramfs",str(root/"initramfs.gz"),"--kernel-config",str(root/"target.config"),
                  "--live-fdt",str(root/"live.dtb"),"--build-provenance",str(path),
                  "--output",str(root/"prepared")]
            with patch.object(self.tool,"load_capture",return_value=(runtime,IOMEM)),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.tool.main(args),3)
                self.assertTrue((root/"prepared/manifest.json").is_file())
                with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as caught:
                    self.tool.main(args)
                self.assertEqual(caught.exception.code,2)
                args[-1]=str(root/"bad")
                (root/"Image").write_bytes(image+b"wrong")
                with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    self.tool.main(args)
                self.assertFalse((root/"bad").exists())


    def test_exact_startup_build_profile_is_accepted_but_tampered_profile_is_not(self):
        from ci.quest2_startup import profile_record
        runtime,image,rd,live = self.inputs()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            data={"Image":image,"initramfs.gz":rd,"target.config":CONFIG.encode(),"live.dtb":live}
            for name,value in data.items(): (root/name).write_bytes(value)
            provenance=profile_record("startup-gic-pending")
            provenance["files"]={name:{"bytes":len(data[name]),"sha256":hashlib.sha256(data[name]).hexdigest()}
                                 for name in ("Image","initramfs.gz","target.config")}
            path=root/"provenance.json";path.write_text(json.dumps(provenance))
            args=["--capture",str(root),"--kernel-image",str(root/"Image"),
                  "--initramfs",str(root/"initramfs.gz"),"--kernel-config",str(root/"target.config"),
                  "--live-fdt",str(root/"live.dtb"),"--build-provenance",str(path),
                  "--output",str(root/"prepared")]
            with patch.object(self.tool,"load_capture",return_value=(runtime,IOMEM)),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.tool.main(args),3)
                manifest=json.loads((root/"prepared/manifest.json").read_text())
                self.assertFalse(manifest["baseline_only"])
                self.assertTrue(manifest["development_only"])
                self.assertEqual(manifest["target_profile"],"startup-gic-pending")
                args[-1]=str(root/"bad")
                provenance["kernel_patches"][0]["sha256"]="0"*64
                for invalid in (provenance,[]):
                    path.write_text(json.dumps(invalid))
                    with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as caught:
                        self.tool.main(args)
                    self.assertEqual(caught.exception.code,2)
                    self.assertFalse((root/"bad").exists())


if __name__ == "__main__": unittest.main()
