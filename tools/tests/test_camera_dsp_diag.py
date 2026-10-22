"""Execute the actual observational C functions with host API doubles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CameraDspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to a host C compiler")
        production = (ROOT/"module/camera_dsp_diag.c").read_text()
        production = production.split("/* QCD_HOST_BEGIN */",1)[1].split("/* QCD_HOST_END */",1)[0]
        harness = Path(__file__).with_name("camera_dsp_harness.c").read_text()
        cls.temp = tempfile.TemporaryDirectory(prefix="qcd-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/"diag.c"
        source.write_text(harness.replace("/* PRODUCTION */", production))
        cls.binary = Path(cls.temp.name)/("diag.exe" if os.name=="nt" else "diag")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name=="nt" else []),
                                 "-std=gnu11", "-Werror", "-I", str(ROOT/"module"),
                                 str(source), "-o", str(cls.binary)], capture_output=True,text=True)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary),name],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_target_guards_and_anchor_refusal(self): self.case("guards")
    def test_camera_sparse_last_link_and_request_snapshot(self): self.case("camera")
    def test_busy_device_never_reads_private_camera(self): self.case("device_busy")
    def test_wrong_driver_never_reads_private_camera(self): self.case("driver")
    def test_camera_lock_failure_releases_device(self): self.case("camera_busy")
    def test_core_lock_failure_releases_outer_locks(self): self.case("core_busy")
    def test_link_busy_does_not_lock_request(self): self.case("link_busy")
    def test_request_busy_still_releases_link(self): self.case("request_busy")
    def test_read_fault_releases_all_locks(self): self.case("fault")
    def test_malformed_session_cycle_is_bounded(self): self.case("cycle")
    def test_dsp_embedded_advisory_fields_and_capacity(self): self.case("dsp")
