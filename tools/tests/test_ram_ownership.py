"""Run actual allocator-ownership/lifetime helpers with host page API doubles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_loader_safety import ROOT, function


class RamOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to a host C compiler")
        loader = (ROOT / "module/loader.c").read_text()
        # Before the feature exists the loader has no claim operation at all.
        claim = (function(loader, "qkx_claim_destination") if "qkx_claim_destination(" in loader
                 else "static int qkx_claim_destination(void) { return 0; }")
        release = function(loader, "qkx_release")
        validation = (function(loader, "qkx_validate_ram") if "qkx_validate_ram(" in loader
                      else "static int qkx_validate_ram(void) { return -ENOSYS; }")
        init = function(loader, "qkx_init").split('\n\tret = qkx_resolve();')[0]
        init = ("static int init_validation(void) { int ret;" + init[init.index('\n\tif (test_cpus'):]
                + "\nreturn -ENOSYS;\n}")
        ram = function(loader, "qkx_ram_cb")
        select = (function(loader, "qkx_select_destination") if "qkx_select_destination(" in loader
                  else "static int qkx_select_destination(unsigned long long a, unsigned long long b) { return -ENOSYS; }")
        harness = Path(__file__).with_name("ram_ownership_harness.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-ram-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "ram.c"
        gate = function(loader, "qkx_dma_gate")
        diagnostics = loader[loader.index("enum qkx_ram_stage {"):loader.index("static int qkx_claim_destination(")]
        source.write_text(harness.replace("/* PRODUCTION */", "\n".join((diagnostics, select, claim, release, ram, validation, gate, init))))
        cls.binary = Path(cls.temp.name) / ("ram.exe" if os.name == "nt" else "ram")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu11", "-Werror", str(source),
                                 "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_success_holds_full_window_until_release(self):
        self.case("success")

    def test_busy_range_is_never_owned_or_freed(self):
        self.case("busy")

    def test_missing_allocator_or_release_hook_rejected(self):
        self.case("missing")

    def test_unmapped_pfn_never_reaches_allocator(self):
        self.case("hole")

    def test_reserved_page_never_reaches_allocator(self):
        self.case("reserved")

    def test_cross_zone_range_rejected(self):
        self.case("zone")

    def test_cma_or_other_nonmovable_pageblock_rejected(self):
        self.case("cma")

    def test_failed_claim_does_not_leak_staging_allocations(self):
        self.case("staging_cleanup")

    def test_validation_init_claims_and_releases_without_payload_path(self):
        self.case("validate_success")

    def test_validation_rejects_wrong_model_before_resolving_symbols(self):
        self.case("validate_model")

    def test_validation_rejects_unreadable_model(self):
        self.case("validate_no_model")

    def test_validation_requires_system_ram_coverage(self):
        self.case("validate_no_ram")

    def test_validation_propagates_busy_without_freeing_unowned_pages(self):
        self.case("validate_busy")

    def test_validation_handles_symbol_resolution_failure(self):
        self.case("validate_resolve")

    def test_selected_high_window_is_claimed_and_released_without_truncation(self):
        self.case("validate_high")

    def test_compact_diagnostic_claims_and_releases_exactly_36_mib(self):
        self.case("validate_compact")

    def test_compact_length_is_bounded_aligned_and_cannot_overflow(self):
        self.case("validate_bad_length")

    def test_length_requires_ram_only_diagnostic(self):
        self.case("length_without_validation")

    def test_payload_base_cannot_mix_with_ram_only_diagnostic(self):
        self.case("validate_payload_conflict")

    def test_candidate_base_overflow_and_alignment_fail_before_lookup(self):
        self.case("validate_bad_base")

    def test_candidate_cannot_change_staging_or_execution_layout(self):
        self.case("base_without_validation")

    def test_diagnosis_requires_validation_before_resolving(self):
        self.case("diagnose_without_validation")

    def test_missing_probe_helpers_refuse_before_allocation(self):
        self.case("validate_diagnose_missing")

    def test_missing_unregister_helper_refuses_before_registration(self):
        self.case("diagnose_no_unregister")

    def test_each_registration_failure_removes_only_previous_probes(self):
        for index in range(4):
            with self.subTest(index=index):
                self.case(f"diagnose_register_{index}")

    def test_probe_entry_rejects_other_tasks_and_other_windows(self):
        self.case("diagnose_filter")

    def test_observers_preserve_registers_and_report_returns_and_misses(self):
        self.case("diagnose_records")

    def test_diagnosed_success_releases_ram_and_all_probes(self):
        self.case("diagnose_success")

    def test_diagnosed_busy_never_frees_unowned_ram_and_removes_probes(self):
        self.case("diagnose_busy")

    def test_pfn_guard_failure_also_removes_all_probes(self):
        self.case("diagnose_guard")
