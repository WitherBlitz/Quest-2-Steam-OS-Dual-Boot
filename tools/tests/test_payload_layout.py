"""Compile the native layout, DTB and staging helpers against host doubles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_loader_safety import ROOT, function


class PayloadLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = (ROOT / "module/loader.c").read_text()
        cls.missing = [n for n in ("qkx_select_destination", "qkx_payload_layout", "qkx_check_dtb")
                       if n + "(" not in loader]
        if cls.missing:
            return
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to a host C compiler")
        names = ("qkx_select_destination", "qkx_payload_layout", "qkx_check_dtb", "qkx_page",
                 "qkx_stage")
        harness = Path(__file__).with_name("payload_layout_harness.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-payload-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "payload.c"
        source.write_text(harness.replace("/* PRODUCTION */", "\n".join(function(loader, n) for n in names)))
        cls.binary = Path(cls.temp.name) / ("payload.exe" if os.name == "nt" else "payload")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11", "-Werror", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        prefix = harness.split("/* PRODUCTION */")[0]
        fixture = harness[harness.index("static size_t make_dtb"):harness.index("int main(")]
        init_harness = Path(__file__).with_name("payload_init_harness.c").read_text()
        init_names = ("qkx_select_destination", "qkx_payload_layout", "qkx_check_dtb",
                      "qkx_open", "qkx_release", "qkx_dma_gate", "qkx_ram_cb", "qkx_init")
        code = prefix + init_harness.replace("/* FIXTURE */", fixture).replace(
            "/* PRODUCTION */", "\n".join(function(loader, n) for n in init_names))
        source.write_text(code)
        cls.init_binary = Path(cls.temp.name) / ("init.exe" if os.name == "nt" else "init")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11", "-Werror", "-DQKX_ENABLE_EXPERIMENTAL_JUMP",
                                 "-DQKX_TEST_WRITE_RACES",
                                 str(source), "-o", str(cls.init_binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        # Compile a temporary pre-exclusion negative control. Its matching old
        # cleanup must also omit allow_write_access: no exclusion was acquired.
        # Repository production files are never changed by this regression.
        original_open = function(loader, "qkx_open")
        old_open = original_open.replace("ret = deny_write_access(f);", "ret = 0;")
        if old_open == original_open:
            raise RuntimeError("write-exclusion negative control needs review")
        original_init = function(loader, "qkx_init")
        old_init = original_init
        for name in ("kf", "rf", "df"):
            release = f"\t\tallow_write_access({name});\n"
            if release not in old_init:
                raise RuntimeError("write-exclusion negative-control cleanup needs review")
            old_init = old_init.replace(release, "")
        source.write_text(code.replace(original_open, old_open).replace(original_init, old_init))
        cls.no_exclusion_binary = Path(cls.temp.name) / ("no-exclusion.exe" if os.name == "nt" else "no-exclusion")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11", "-Werror", "-DQKX_ENABLE_EXPERIMENTAL_JUMP",
                                 "-DQKX_TEST_WRITE_RACES", str(source), "-o", str(cls.no_exclusion_binary)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def case(self, name):
        self.assertFalse(self.missing, "missing native helpers: " + ", ".join(self.missing))
        p = subprocess.run([str(self.init_binary if name.startswith("init_") else self.binary), name],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_compact_addresses_and_envelope_match_offline_sizing(self):
        self.case("compact")

    def test_legacy_layout_is_preserved(self):
        self.case("legacy")

    def test_bad_image_offsets_flags_sizes_and_copy_overflow_fail(self):
        self.case("bad_layout")

    def test_dtb_initrd_addresses_must_match_relocated_payload(self):
        self.case("dtb")

    def test_truncated_dtb_blocks_tokens_and_property_names_fail(self):
        self.case("bad_dtb")

    def test_dtb_properties_cannot_follow_child_nodes(self):
        self.case("dtb_property_order")

    def test_dtb_chosen_alias_cannot_hide_the_checked_initrd(self):
        self.case("dtb_chosen_alias")

    def test_source_pages_avoid_selected_destination_above_four_gib(self):
        self.case("pages")

    def test_stage_zero_fills_memory_tail_and_uses_relocated_destinations(self):
        self.case("stage")

    def test_dtb_staging_uses_the_validated_buffer(self):
        self.case("stage_buffer")

    def test_read_or_descriptor_failure_aborts_staging(self):
        self.case("stage_fail")

    def test_full_init_stages_all_three_relocated_segments(self):
        self.case("init_compact")

    def test_wrong_dtb_addresses_abort_before_any_staging(self):
        self.case("init_bad_dtb")

    def test_failed_staging_after_claim_releases_destination_and_files(self):
        self.case("init_cleanup")

    def test_hollywood_compact_jump_stays_blocked_before_payload_open(self):
        self.case("init_gate")

    def test_all_payload_files_exclude_writers_through_staging(self):
        self.case("init_locked_staging")

    def test_competing_byte_writes_and_truncates_are_refused_until_cleanup(self):
        self.case("init_write_races")

    def test_failed_staging_releases_all_competing_writer_exclusions(self):
        self.case("init_write_races_cleanup")

    def test_pre_exclusion_baseline_admits_a_competing_mutation(self):
        result = subprocess.run([str(self.no_exclusion_binary), "init_write_races"],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("competing writer admitted", result.stderr)

    def test_existing_writer_refuses_before_header_or_staging(self):
        self.case("init_writer")

    def test_later_writer_refusal_releases_earlier_file_exclusions(self):
        self.case("init_later_writer")

    def test_open_failure_balances_prior_file_exclusion(self):
        self.case("init_open_failure")

    def test_nonregular_file_is_closed_without_write_exclusion(self):
        self.case("init_nonregular")

    def test_ram_walker_failure_is_preserved_before_staging(self):
        self.case("init_walk_error")

    def test_missing_ram_coverage_refuses_and_releases_file_exclusions(self):
        self.case("init_no_ram")


if __name__ == "__main__":
    unittest.main()
