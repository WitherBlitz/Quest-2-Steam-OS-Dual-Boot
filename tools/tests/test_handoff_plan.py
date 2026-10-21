"""Offline placement must exclude uncertain reservations, never certify DMA."""
import hashlib
import contextlib
import importlib.util
import io
import inspect
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


def payload(memory=0x1dd1000, offset=0x80000, flags=0xa, ramdisk_size=2676670):
    image = bytearray(64)
    struct.pack_into("<QQQ", image, 8, offset, memory, flags)
    image[56:60] = b"ARM\x64"
    dtb = struct.pack(">10I", 0xd00dfeed, 72, 56, 72, 40, 17, 16, 0, 0, 16)
    dtb += bytes(16) + struct.pack(">4I", 1, 0, 2, 9)
    return bytes(image), bytes(ramdisk_size), dtb


def capture(root):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for name, data in tree().items():
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    manifest = {"capture_status": "complete", "identity": {
        "device": "hollywood", "build": "52242990024200150",
        "kernel": "4.19.325-cip128-st12-g49638c7a8637", "root_uid": "0"}, "artifacts": {}}
    for key, name, data in [("runtime_tree", "runtime-tree.tar", buf.getvalue()),
                            ("iomem", "iomem.txt", IOMEM.encode())]:
        (root / name).write_bytes(data)
        manifest["artifacts"][key] = {"filename": name, "bytes": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()}
    (root / "capture.json").write_text(json.dumps(manifest))


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

    def sized(self, parts=None):
        self.assertTrue(callable(getattr(self.tool, "payload_requirements", None)),
                        "payload memory sizing is missing")
        return self.tool.payload_requirements(*(payload() if parts is None else parts))

    def planned(self, *, exclude_blocks=(), parts=None, iomem=IOMEM, dt=None):
        signature = inspect.signature(self.tool.plan)
        self.assertIn("exclude_blocks", signature.parameters, "observed exclusions are missing")
        self.assertIn("payload", signature.parameters, "compact payload planning is missing")
        return self.tool.plan(tree() if dt is None else dt, iomem,
                              exclude_blocks=exclude_blocks, payload=parts)

    def test_observed_block_is_excluded_from_diagnostic_candidates(self):
        p = self.planned(exclude_blocks=[(0x102c00000, 0x103000000)])
        self.assertEqual(p["ram"]["window_bytes"], 0x3400000)
        self.assertEqual(p["ram"]["candidates"][0]["base"], 0x103000000)
        self.assertTrue(any("Caller-supplied" in e["reason"] for e in p["ram"]["exclusions"]))
        self.assertTrue(all(c["end"] <= 0x102c00000 or c["base"] >= 0x103000000
                            for c in p["ram"]["candidates"]))

    def test_overlapping_and_adjacent_exclusions_cannot_restore_memory(self):
        p = self.planned(exclude_blocks=[(0x102c00000, 0x103000000),
                                        (0x102c00000, 0x103400000)])
        self.assertEqual(p["ram"]["candidates"][0]["base"], 0x103400000)

    def test_invalid_or_outside_ram_blocks_are_rejected(self):
        for span in [(-0x400000, 0), (0x100000000, 0x100000000),
                     (0x100000001, 0x100400000), (0x100000000, (1 << 64)+0x400000),
                     (0x200000000, 0x200400000), (True, 0x100400000)]:
            with self.subTest(span=span):
                self.assertIn("exclude_blocks", inspect.signature(self.tool.plan).parameters)
                with self.assertRaises(ValueError):
                    self.tool.plan(tree(), IOMEM, exclude_blocks=[span])

    def test_payload_uses_declared_memory_and_hashes_real_bytes(self):
        parts = payload()
        p = self.sized(parts)
        self.assertEqual(p["image"]["memory_bytes"], 0x1dd1000)
        self.assertEqual(p["image"]["bytes"], 64)
        self.assertEqual(p["image"]["sha256"], hashlib.sha256(parts[0]).hexdigest())
        self.assertEqual(p["window_bytes"], 0x2400000)
        self.assertFalse(p["legacy_format_compatible"])
        self.assertTrue(p["requires_loader_port"])

    def test_compact_layout_fits_before_busy_block_without_changing_probe_size(self):
        p = self.planned(exclude_blocks=[(0x102c00000, 0x103000000)], parts=payload())
        c = p["payload"]["candidates"][0]
        self.assertEqual((c["base"], c["end"]), (0x100000000, 0x102400000))
        self.assertEqual(c["kernel_address"], c["base"]+0x80000)
        self.assertLessEqual(c["kernel_memory_end"], c["initramfs_address"])
        self.assertLessEqual(c["initramfs_end"], c["dtb_address"])
        self.assertLessEqual(c["dtb_end"], c["end"])
        self.assertEqual(c["dtb_address"] % 8, 0)
        self.assertEqual(c["ownership"], "unclaimed")
        self.assertTrue(p["payload"]["requires_loader_port"])
        self.assertEqual(p["ram"]["candidates"][0]["base"], 0x103000000)
        self.assertEqual(p["status"], "HANDOFF_BLOCKED")
        self.assertFalse(p["dma"]["quiesced"])

    def test_missing_payload_does_not_invent_compact_addresses(self):
        p = self.planned()
        self.assertEqual(p["payload"]["status"], "MISSING_INPUTS")
        self.assertEqual(p["payload"]["candidates"], [])
        self.assertTrue(p["payload"]["blockers"])

    def test_compact_plan_can_fit_when_fixed_diagnostic_does_not(self):
        p = self.planned(parts=payload(), iomem="100000000-102bfffff : System RAM\n")
        self.assertFalse(p["ram"]["candidates"])
        self.assertEqual(p["payload"]["candidates"][0]["base"], 0x100000000)

    def test_compact_plan_does_not_bridge_holes_or_ignore_unknown_reservations(self):
        p = self.planned(parts=payload(), iomem="100000000-100ffffff : System RAM\n"
                         "102000000-102ffffff : System RAM\n")
        self.assertEqual(p["payload"]["candidates"], [])
        t = tree()
        del t["reserved-memory/secure/alloc-ranges"]
        self.assertEqual(self.planned(parts=payload(), dt=t)["payload"]["candidates"], [])

    def test_unknown_truncated_or_unsupported_images_are_rejected(self):
        good = payload()
        bad_magic = bytearray(good[0]); bad_magic[56:60] = b"BAD!"
        for image in [good[0][:63], bytes(bad_magic), payload(memory=0)[0],
                      payload(memory=32)[0], payload(flags=0xb)[0], payload(flags=0xc)[0],
                      payload(flags=2)[0], payload(flags=0x1a)[0], payload(offset=3)[0],
                      payload(memory=(1 << 64)-1)[0]]:
            with self.subTest(header=image[:32]):
                self.assertTrue(callable(getattr(self.tool, "payload_requirements", None)))
                with self.assertRaises(ValueError):
                    self.tool.payload_requirements(image, good[1], good[2])

    def test_empty_initramfs_and_bad_or_oversize_dtb_are_rejected(self):
        good = payload()
        for rd, dtb in [(b"", good[2]), (good[1], b"bad"),
                        (good[1], good[2]+b"padding"), (good[1], bytes(0x200001))]:
            with self.subTest(lengths=(len(rd), len(dtb))):
                self.assertTrue(callable(getattr(self.tool, "payload_requirements", None)))
                with self.assertRaises(ValueError):
                    self.tool.payload_requirements(good[0], rd, dtb)

    def test_payload_candidate_limit_and_initrd_window(self):
        p = self.planned(parts=payload(), iomem="100000000-1ffffffff : System RAM\n")
        c = p["payload"]["candidates"]
        self.assertEqual(len(c), 8)
        self.assertTrue(all(a["end"] <= b["base"] for a, b in zip(c, c[1:])))
        for row in c:
            self.assertLessEqual(row["initramfs_end"]-row["initrd_window_base"], 32 << 30)
            self.assertEqual(row["initrd_window_base"] % (1 << 30), 0)

    def test_large_payload_skips_a_base_that_breaks_the_initrd_window(self):
        p = self.planned(parts=payload(memory=(31 << 30) + 0x400000),
                         iomem="13fc00000-fffffffff : System RAM\n")
        row = p["payload"]["candidates"][0]
        self.assertEqual(row["base"], 0x140000000)
        self.assertLessEqual(row["initramfs_end"] - row["initrd_window_base"], 32 << 30)
        with self.assertRaises(ValueError):
            self.sized(payload(memory=32 << 30))

    def test_payload_file_reader_rejects_empty_directory_and_oversize_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            empty = root/"empty"; empty.touch()
            big = root/"big"
            with big.open("wb") as stream:
                stream.truncate(self.tool.MAX_ARTIFACT + 1)
            for path in (root, empty, big):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.tool.read_payload(path)
            good = root/"small"; good.write_bytes(b"data")
            self.assertEqual(self.tool.read_payload(good), b"data")

    def test_cli_complete_set_is_host_only_and_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); capture(root)
            files = []
            for name, data in zip(("Image", "initramfs", "boot.dtb"), payload()):
                p = root/name; p.write_bytes(data); files.append(str(p))
            output = root/"plan.json"
            args = ["--capture", str(root), "--output", str(output),
                    "--exclude-block", "0x102c00000:0x103000000", "--kernel-image", files[0],
                    "--initramfs", files[1], "--dtb", files[2]]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = self.tool.main(args)
            self.assertEqual(result, 3)
            before = output.read_bytes()
            self.assertTrue(json.loads(before)["payload"]["candidates"])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                self.tool.main(args)
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(output.read_bytes(), before)

    def test_cli_partial_payload_set_fails_without_writing_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); capture(root)
            image = root/"Image"; image.write_bytes(payload()[0])
            output = root/"plan.json"
            error = io.StringIO()
            with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
                self.tool.main(["--capture", str(root), "--output", str(output),
                                "--kernel-image", str(image)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("must be supplied together", error.getvalue())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
