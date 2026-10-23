"""Execute actual CPU-test control flow with host-only kernel API doubles.

Mask equality establishes bookkeeping only, never physical CPU-off completion.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

try:
    from .test_loader_safety import function
except ImportError:
    from test_loader_safety import function

ROOT = Path(__file__).resolve().parents[2]


class CpuLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Host C compiler required; CPU tests must not silently skip")
        production = function((ROOT / "module/loader.c").read_text(), "qkx_test_cpus")
        harness = Path(__file__).with_name("cpu_lifecycle_harness.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-cpu-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "harness.c"
        cls.binary = source.with_suffix(".exe" if os.name == "nt" else "")
        source.write_text(harness.replace("/* PRODUCTION */", production))
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu89", "-Werror",
                                 "-Wno-unused-function", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_down_restores_only_successfully_removed_cpus(self):
        self.case("partial")

    def test_down_error_survives_restore_and_affinity_failures(self):
        self.case("down_error")

    def test_probe_error_survives_restore_failure(self):
        self.case("probe_error")

    def test_first_restore_error_survives_later_errors_and_mask_mismatch(self):
        self.case("restore_error")

    def test_successful_up_with_wrong_online_mask_is_failure(self):
        self.case("mask_mismatch")

    def test_partial_freeze_failure_still_balances_restore(self):
        self.case("freeze_error")

    def test_freeze_success_with_failed_restore_is_failure(self):
        self.case("freeze_restore")

    def test_probe_requires_single_online_cpu(self):
        self.case("probe_busy")

    def test_initial_affinity_failure_has_no_cpu_side_effects(self):
        self.case("pin_error")

    def test_restore_affinity_error_is_returned_after_cpu_restoration(self):
        self.case("affinity_error")

    def test_single_cpu_path_has_initialized_success_result(self):
        self.case("single")

    def test_matching_masks_do_not_establish_physical_cpu_off(self):
        self.case("bookkeeping_only")


if __name__ == "__main__":
    unittest.main()
