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
DWC3_PATCH = ROOT / "kernel/patches/quest2-startup/0002-dwc3-msm-select-available-controller-child.patch"


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


class Dwc3ChildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-dwc3-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        path = root / "drivers/usb/dwc3/dwc3-msm.c"
        path.parent.mkdir(parents=True)
        path.write_bytes((FIXTURES / "dwc3_msm_probe-child-6bde5fc.c").read_text().encode())
        if DWC3_PATCH.is_file():
            patch = DWC3_PATCH.read_bytes().replace(b"\r\n", b"\n")
            for args in (("--check",), ()):
                subprocess.run(["git", "-c", "core.autocrlf=false", "apply", *args,
                                "--whitespace=error", "-"], input=patch, cwd=root,
                               check=True, capture_output=True)
        harness = Path(__file__).with_name("target_dwc3_child_harness.c").read_text()
        source = root / "dwc3-child.c"
        source.write_text(harness.replace("/* DRIVER */", path.read_text()))
        cls.binary = root / ("dwc3-child.exe" if os.name == "nt" else "dwc3-child")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler: raise RuntimeError("native compiler required for DWC3 tests")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11", "-Werror", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)

    def test_available_compatible_child_and_reference_lifetime(self):
        for case in ("controller-first", "unrelated-first", "disabled-first", "no-match",
                     "empty", "populate-failure", "lookup-missing"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.binary), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


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

    def test_usb_child_profile_is_strict_and_never_handoff_ready(self):
        data = self.tool.profile_record("startup-usb-child")
        self.assertEqual(self.tool.validate_provenance(data), "startup-usb-child")
        self.assertEqual(len(data["kernel_patches"]), 2)
        self.assertFalse(data["baseline_only"])
        self.assertIs(data["hardware_validated"], False)
        self.assertIs(data["handoff_ready"], False)
        self.assertEqual(data["hardware_handoff"], "BLOCKED")
        for key, value in (("hardware_validated", True), ("handoff_ready", True),
                           ("hardware_validated", 0), ("handoff_ready", 0),
                           ("development_only", False), ("hardware_handoff", "READY"),
                           ("kernel_source_files", {}), ("kernel_patches", [])):
            altered = copy.deepcopy(data); altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.tool.validate_provenance(altered)
        altered = copy.deepcopy(data)
        altered["kernel_patches"].append(dict(data["kernel_patches"][1]))
        with self.assertRaises(ValueError): self.tool.validate_provenance(altered)
        for entry in range(2):
            for key in ("path", "sha256"):
                altered = copy.deepcopy(data); altered["kernel_patches"][entry][key] = "wrong"
                with self.subTest(entry=entry, key=key), self.assertRaises(ValueError):
                    self.tool.validate_provenance(altered)
        for path in data["kernel_source_files"]:
            altered = copy.deepcopy(data); altered["kernel_source_files"][path] = "0" * 64
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.tool.validate_provenance(altered)
        altered = copy.deepcopy(data); altered["kernel_source_files"]["extra.c"] = "0" * 64
        with self.assertRaises(ValueError): self.tool.validate_provenance(altered)
        altered = copy.deepcopy(data); altered["kernel_patches"].reverse()
        with self.assertRaises(ValueError): self.tool.validate_provenance(altered)
        for key in ("hardware_validated", "handoff_ready", "kernel_source_files", "kernel_patches"):
            altered = copy.deepcopy(data); del altered[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.tool.validate_provenance(altered)

    def test_patch_application_keeps_lf_bytes_despite_windows_autocrlf_config(self):
        for driver,fixture,patch in (("drivers/irqchip/irq-gic-v3.c","gic_dist_init-6bde5fc.c",PATCH),
                                     ("drivers/usb/dwc3/dwc3-msm.c","dwc3_msm_probe-child-6bde5fc.c",DWC3_PATCH)):
            with self.subTest(driver=driver), tempfile.TemporaryDirectory(prefix="qkx-lf-") as temp:
                root=Path(temp);path=root/driver
                path.parent.mkdir(parents=True)
                path.write_bytes((FIXTURES/fixture).read_text().encode())
                subprocess.run(["git","init","-q",str(root)],check=True,capture_output=True)
                subprocess.run(["git","-C",str(root),"config","core.autocrlf","true"],check=True,capture_output=True)
                self.tool.git(root,"apply","--whitespace=error","-",input=patch.read_bytes().replace(b"\r\n",b"\n"))
                self.assertNotIn(b"\r\n",path.read_bytes(),"patch application changed reviewed Linux source bytes")


if __name__ == "__main__": unittest.main()
