"""Offline placement must exclude uncertain reservations, never certify DMA."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "quest2_handoff_plan.py"
sys.path.insert(0, str(TOOL.parent))


def cells(*values):
    return struct.pack(">" + "I" * len(values), *values)


def tree():
    return {
        "#address-cells": cells(2), "#size-cells": cells(2),
        "reserved-memory/#address-cells": cells(2),
        "reserved-memory/#size-cells": cells(2), "reserved-memory/ranges": b"",
        "reserved-memory/secure/size": cells(0, 0x1000000),
        "reserved-memory/secure/alloc-ranges": cells(0, 0, 1, 0),
        "soc/smmu/phandle": cells(7), "soc/smmu/#iommu-cells": cells(1),
        "reserved-memory/firmware/phandle": cells(8),
        "reserved-memory/firmware/reg": cells(0, 0x86000000, 0, 0x100000),
        "soc/camera/iommus": cells(7, 42),
        "soc/camera/memory-region": cells(8),
    }


IOMEM = "80000000-10fffffff : System RAM\n"


class HandoffPlanTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.exists(), "offline placement planner is missing")
        spec = importlib.util.spec_from_file_location("quest2_handoff_plan", TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_dynamic_allowed_range_is_excluded_even_if_iomem_says_ram(self):
        p = self.tool.plan(tree(), IOMEM)
        self.assertEqual(p["ram"]["candidates"][0], {
            "base": 0x100000000, "end": 0x103400000, "ownership": "unclaimed"})
        self.assertEqual(p["status"], "HANDOFF_BLOCKED")

    def test_static_disabled_reservations_and_kernel_code_are_excluded(self):
        t = tree()
        t["reserved-memory/splash/reg"] = cells(1, 0, 0, 0x1000000)
        t["reserved-memory/splash/status"] = b"disabled\0"
        p = self.tool.plan(t, IOMEM + "  101000000-101ffffff : Kernel code\n")
        self.assertEqual(p["ram"]["candidates"][0]["base"], 0x102000000)

    def test_unbounded_dynamic_reservation_prevents_candidates(self):
        t = tree()
        del t["reserved-memory/secure/alloc-ranges"]
        p = self.tool.plan(t, IOMEM)
        self.assertEqual(p["ram"]["candidates"], [])
        self.assertTrue(p["ram"]["blockers"])

    def test_small_extents_are_not_joined_across_a_hole(self):
        p = self.tool.plan(tree(), "100000000-100ffffff : System RAM\n"
                                "102000000-102ffffff : System RAM\n")
        self.assertEqual(p["ram"]["candidates"], [])

    def test_at_most_eight_nonoverlapping_candidates(self):
        c = self.tool.plan(tree(), "100000000-1ffffffff : System RAM\n")["ram"]["candidates"]
        self.assertEqual(len(c), 8)
        self.assertEqual(c[1]["base"], 0x103400000)
        self.assertTrue(all(a["end"] <= b["base"] for a, b in zip(c, c[1:])))

    def test_missing_reserved_memory_identity_map_is_rejected(self):
        for value in (None, cells(0, 0, 0, 0, 0, 0)):
            with self.subTest(value=value):
                t = tree()
                if value is None:
                    del t["reserved-memory/ranges"]
                else:
                    t["reserved-memory/ranges"] = value
                with self.assertRaises(ValueError):
                    self.tool.plan(t, IOMEM)

    def test_overflow_zero_size_and_truncated_reg_are_rejected(self):
        for value in (cells(0xffffffff, 0xfffff000, 0, 0x2000),
                      cells(1, 0, 0, 0), cells(1, 0, 0)):
            with self.subTest(value=value):
                t = tree()
                t["reserved-memory/firmware/reg"] = value
                with self.assertRaises(ValueError):
                    self.tool.plan(t, IOMEM)

    def test_redacted_or_malformed_iomem_cannot_produce_candidates(self):
        for value in ("00000000-00000000 : System RAM\n", "bad map", "1-0 : System RAM\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.tool.plan(tree(), value)

    def test_dma_references_resolve_without_claiming_quiescence(self):
        p = self.tool.plan(tree(), IOMEM)
        m = next(m for m in p["dma"]["clients"] if m["node"] == "/soc/camera")
        self.assertEqual(m["iommus"], [{"provider": "/soc/smmu", "args": [42]}])
        self.assertEqual(m["memory_regions"], ["/reserved-memory/firmware"])
        self.assertEqual(m["state"], "unproven")
        self.assertFalse(p["dma"]["quiesced"])
        self.assertTrue(p["dma"]["blockers"])

    def test_missing_phandle_or_truncated_iommu_arguments_fail_closed(self):
        for value in (cells(999, 42), cells(7)):
            with self.subTest(value=value):
                t = tree()
                t["soc/camera/iommus"] = value
                with self.assertRaises(ValueError):
                    self.tool.plan(t, IOMEM)

    def test_duplicate_phandle_is_rejected(self):
        t = tree()
        t["soc/other/phandle"] = cells(7)
        with self.assertRaises(ValueError):
            self.tool.plan(t, IOMEM)

    def test_verified_capture_required_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as archive:
                for name, data in tree().items():
                    info = tarfile.TarInfo("./" + name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            payloads = {"runtime_tree": ("runtime-tree.tar", buf.getvalue()),
                        "iomem": ("iomem.txt", IOMEM.encode())}
            manifest = {"capture_status": "complete", "identity": {
                "device": "hollywood", "build": "52242990024200150",
                "kernel": "4.19.325-cip128-st12-g49638c7a8637", "root_uid": "0"},
                "artifacts": {}}
            for key, (name, data) in payloads.items():
                (root / name).write_bytes(data)
                manifest["artifacts"][key] = {"filename": name, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}
            (root / "capture.json").write_text(json.dumps(manifest))
            t, m = self.tool.load_capture(root)
            self.assertEqual(t, tree())
            self.assertEqual(m, IOMEM)
            (root / "iomem.txt").write_text(IOMEM + "  100000000-10fffffff : reserved\n")
            with self.assertRaises(ValueError):
                self.tool.load_capture(root)


if __name__ == "__main__":
    unittest.main()
