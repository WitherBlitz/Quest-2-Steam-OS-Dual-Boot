"""Compile/call actual pinned controller functions; no VR runtime or hardware."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "q2_driver-9c96c192.c"
PATCH = ROOT / "patches/monado/quest2/0001-controller-clock-and-stick-bounds.patch"
SOURCE_SHA = "929e98f59a5f43604ca30f3ac2eddedf9f7fe7117991b9a484d2f0b8be96b9d7"


def block(source, start, end):
    at = source.index(start)
    return source[at:source.index(end, at) + len(end)]


class VRControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
            raise RuntimeError("controller fixture differs from pinned upstream source")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-vr-controller-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        driver = root / "monado/drivers/quest2/q2_driver.c"
        driver.parent.mkdir(parents=True)
        driver.write_bytes(data)
        if PATCH.is_file():
            patch = PATCH.read_bytes().replace(b"\r\n", b"\n")
            for arguments in (("--check",), ()):
                subprocess.run(["git", "-c", "core.autocrlf=false", "apply", *arguments,
                                "--whitespace=error", "-"], cwd=root, input=patch,
                               capture_output=True, check=True)
        source = driver.read_text()
        controller = block(source, "struct q2_controller\n{", "\n};")
        functions = "\n\n".join(block(source, signature, "\n}") for signature in
            ("static int64_t\nmap_time(", "static void\nhandle_ctrl_motion(",
             "static void\nhandle_controller("))
        harness = Path(__file__).with_name("vr_controller_harness.c").read_text()
        harness = harness.replace("/* CONTROLLER */", controller).replace("/* FUNCTIONS */", functions)
        c_file = root / "controller.c"
        c_file.write_text(harness)
        cls.binary = root / ("controller.exe" if os.name == "nt" else "controller")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("native compiler required for controller source tests")
        result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                                 "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
                                 str(c_file), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_counter_wrap_preserves_millisecond_interval(self): self.run_case("wrap")
    def test_timestamp_free_sample_crosses_counter_wrap(self): self.run_case("implicit-wrap")
    def test_timestamp_free_sample_cannot_initialize_clock(self): self.run_case("missing-epoch")
    def test_zero_is_a_valid_first_explicit_timestamp(self): self.run_case("zero-epoch")
    def test_duplicate_motion_is_not_delivered(self): self.run_case("duplicate")
    def test_backwards_sample_preserves_accepted_clock(self): self.run_case("backwards")
    def test_half_range_gap_is_refused(self): self.run_case("ambiguous-gap")
    def test_large_counter_reset_is_not_guessed_as_reconnect(self): self.run_case("large-reset")
    def test_controller_clock_epochs_are_independent(self): self.run_case("independent-clocks")
    def test_extended_clock_stops_before_nanosecond_overflow(self): self.run_case("overflow")
    def test_all_signed_stick_inputs_remain_normalized(self): self.run_case("stick-range")
    def test_truncated_controller_records_do_not_update_state(self): self.run_case("truncated")


if __name__ == "__main__": unittest.main()
