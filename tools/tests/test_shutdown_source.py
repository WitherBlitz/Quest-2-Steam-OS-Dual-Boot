"""Pinned public-source behavior, not live watchdog/DMA or reset validation."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "watchdog-6bde5fc.c"


class ShutdownSourceTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_shutdown_source.py"
        self.assertTrue(path.is_file(), "pinned shutdown source verifier is missing")
        spec = importlib.util.spec_from_file_location("shutdown_source", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_exact_watchdog_function_fixture_matches(self):
        module = self.module()
        self.assertIsNone(module.verify_watchdog_fixture(FIXTURE.read_text()))

    def test_watchdog_fixture_mutation_refused(self):
        module = self.module()
        with self.assertRaisesRegex(ValueError, "fixture"):
            module.verify_watchdog_fixture(FIXTURE.read_text().replace('"%d\\n"', '"0\\n"'))

    def test_untrusted_archive_refused_before_tar_processing(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.tar.gz"; path.write_bytes(b"not trusted")
            with self.assertRaisesRegex(ValueError, "archive"):
                module.audit_sources(path)

    def test_camera_and_dsp_fixtures_match_and_mutations_are_refused(self):
        module = self.module()
        for fixture, verify in (("camera-close-6bde5fc.c", module.verify_camera_fixture),
                                ("subsystem-put-6bde5fc.c", module.verify_dsp_fixture)):
            source = (FIXTURE.parent/fixture).read_text()
            self.assertIsNone(verify(source))
            with self.assertRaisesRegex(ValueError, "fixture"):
                verify(source.replace("return;", "return ;").replace("return 0;", "return 1;"))


class WatchdogSourceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler: raise RuntimeError("native host compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-watchdog-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "watchdog.c"
        source.write_text(Path(__file__).with_name("watchdog_harness.c").read_text().replace("/* SOURCE */", FIXTURE.read_text()))
        cls.binary = Path(cls.temp.name) / ("watchdog.exe" if os.name == "nt" else "watchdog")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu11", "-Werror",
                                 str(source), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_getter_reports_software_flag_only(self): self.case("getter")
    def test_scm_failure_keeps_application_watchdog(self): self.case("scm_failure")
    def test_disable_tears_down_and_zero_does_not_reenable(self): self.case("disable")
    def test_already_disabled_does_not_verify_secure_state(self): self.case("already_disabled")
    def test_secure_result_payload_is_not_checked_by_driver(self): self.case("secure_result")
    def test_panic_zero_disables_application_watchdog(self): self.case("panic_zero")
    def test_nonzero_panic_does_not_arm_a_disabled_watchdog(self): self.case("panic_disabled")
    def test_nonzero_panic_preserves_existing_enable(self): self.case("panic_enabled")


class ClientSourceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler: raise RuntimeError("native host compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-clients-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/"clients.c"
        code = Path(__file__).with_name("shutdown_clients_harness.c").read_text()
        code = code.replace("/* CAMERA_SOURCE */", (FIXTURE.parent/"camera-close-6bde5fc.c").read_text())
        code = code.replace("/* DSP_SOURCE */", (FIXTURE.parent/"subsystem-put-6bde5fc.c").read_text())
        source.write_text(code)
        cls.binary = Path(cls.temp.name)/("clients.exe" if os.name == "nt" else "clients")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu11", "-Werror", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_nonfinal_camera_close_keeps_streams_and_write_lock(self): self.case("camera_shared")
    def test_final_camera_close_does_not_propagate_subdevice_error(self): self.case("camera_final_error")
    def test_invalid_camera_count_unlocks_and_refuses(self): self.case("camera_invalid")
    def test_shared_dsp_reference_remains_running(self): self.case("dsp_shared")
    def test_final_dsp_reference_requests_stop_and_memory_release(self): self.case("dsp_final")
    def test_invalid_dsp_reference_never_stops_or_frees(self): self.case("dsp_invalid")


if __name__ == "__main__": unittest.main()
