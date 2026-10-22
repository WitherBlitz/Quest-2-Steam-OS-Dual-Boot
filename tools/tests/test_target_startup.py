"""Exercise the applied kernel patch against inherited interrupt-state models."""
import os
import copy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "kernel/patches/quest2-startup/0001-gic-v3-clear-inherited-spi-pending-state.patch"
FIXTURES = Path(__file__).with_name("fixtures")


class TargetStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-startup-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        path = root / "drivers/irqchip/irq-gic-v3.c"
        path.parent.mkdir(parents=True)
        path.write_bytes((FIXTURES / "gic_dist_init-6bde5fc.c").read_text().encode())
        if PATCH.is_file():
            patch = PATCH.read_bytes().replace(b"\r\n",b"\n")
            subprocess.run(["git","-c","core.autocrlf=false","apply","--check","--whitespace=error","-"],input=patch,cwd=root,check=True,capture_output=True)
            subprocess.run(["git","-c","core.autocrlf=false","apply","--whitespace=error","-"],input=patch,cwd=root,check=True,capture_output=True)
        harness = Path(__file__).with_name("target_startup_harness.c").read_text()
        harness = harness.replace("/* COMMON */",(FIXTURES / "gic_dist_config-6bde5fc.c").read_text())
        harness = harness.replace("/* DRIVER */",path.read_text())
        source = root / "startup.c"; source.write_text(harness)
        cls.binary = root / ("startup.exe" if os.name == "nt" else "startup")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler: raise RuntimeError("native compiler required for startup tests")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11","-Werror",str(source),"-o",str(cls.binary)],
                                capture_output=True,text=True)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)

    def run_case(self, irq_count, mode="edge"):
        result = subprocess.run([str(self.binary),str(irq_count),mode],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_stale_pending_clears_after_disable_deactivate_before_enable(self):
        self.run_case(96)

    def test_private_interrupts_and_interrupt_count_boundaries(self):
        for count in (32,33,64,1020):
            with self.subTest(count=count): self.run_case(count)

    def test_asserted_level_source_is_not_mistaken_for_dma_quiescence(self):
        self.run_case(96,"level")


class TargetProfileTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "tools/ci/quest2_startup.py"
        self.assertTrue(path.is_file(),"strict target profile verifier is missing")
        spec = importlib.util.spec_from_file_location("quest2_startup",path)
        self.tool = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.tool)

    def test_legacy_unmodified_baseline_provenance_remains_supported(self):
        baseline={"kernel_commit":"6bde5fc06b252c97761bc0963dd13c1abcf377ab",
                  "baseline_only":True,"kernel_patches":[]}
        self.assertEqual(self.tool.validate_provenance(baseline),"baseline")

    def test_only_exact_reviewed_startup_profile_is_accepted(self):
        data=self.tool.profile_record("startup-gic-pending")
        self.assertEqual(self.tool.validate_provenance(data),"startup-gic-pending")
        self.assertFalse(data["baseline_only"])
        self.assertEqual(len(data["kernel_patches"]),1)
        for key,value in (("kernel_commit","wrong"),("kernel_patches",[]),
                          ("baseline_only",True),("target_profile","anything"),
                          ("kernel_source_files",{})):
            altered=copy.deepcopy(data);altered[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.tool.validate_provenance(altered)

    def test_unknown_patch_bytes_or_extra_patch_are_refused(self):
        data=self.tool.profile_record("startup-gic-pending")
        altered=copy.deepcopy(data);altered["kernel_patches"][0]["sha256"]="0"*64
        with self.assertRaises(ValueError):self.tool.validate_provenance(altered)
        altered=copy.deepcopy(data);altered["kernel_patches"].append(dict(data["kernel_patches"][0]))
        with self.assertRaises(ValueError):self.tool.validate_provenance(altered)

    def test_unknown_profile_names_and_non_objects_are_refused(self):
        for name in ("","startup","../baseline"):
            with self.subTest(name=name),self.assertRaises(ValueError):self.tool.profile_record(name)
        for value in ([],None,"baseline"):
            with self.subTest(value=value),self.assertRaises(ValueError):self.tool.validate_provenance(value)

    def test_patch_application_keeps_lf_bytes_despite_windows_autocrlf_config(self):
        with tempfile.TemporaryDirectory(prefix="qkx-lf-") as temp:
            root=Path(temp);path=root/"drivers/irqchip/irq-gic-v3.c"
            path.parent.mkdir(parents=True)
            path.write_bytes((FIXTURES/"gic_dist_init-6bde5fc.c").read_text().encode())
            subprocess.run(["git","init","-q",str(root)],check=True,capture_output=True)
            subprocess.run(["git","-C",str(root),"config","core.autocrlf","true"],check=True,capture_output=True)
            self.tool.git(root,"apply","--whitespace=error","-",input=PATCH.read_bytes().replace(b"\r\n",b"\n"))
            self.assertNotIn(b"\r\n",path.read_bytes(),"patch application changed reviewed Linux source bytes")


if __name__ == "__main__": unittest.main()
