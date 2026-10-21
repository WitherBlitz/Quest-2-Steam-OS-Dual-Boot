"""Execute loader control flow with host kernel API doubles, never a headset.

This is not a kernel/module or hardware test. The harness compiles the actual
qkx_execute function and the option/log guards up to their first external action.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    start = source.rfind("static ", 0, source.index(name + "("))
    opening = source.index("{", source.index(name + "(", start))
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class LoaderSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to a host C compiler; safety tests must not silently skip")
        loader = (ROOT / "module/loader.c").read_text()
        marker = (ROOT / "module/marker_read.c").read_text()
        execute = function(loader, "qkx_execute")
        option_guard = function(loader, "qkx_init").split('\n\tif (!of_machine_is_compatible')[0]
        option_guard = option_guard.replace("qkx_init(void)", "check_options(void)") + "\nreturn 0;\n}"
        log_guard = function(marker, "qkx_marker_read_init").split('\n\tfor (i = 0;')[0]
        log_guard += "\nreturn 0;\n}"
        helpers = "\n".join(function(loader, name) for name in ("qkx_match_syncboss",)
                            if name + "(" in loader)
        harness = (Path(__file__).with_name("loader_safety_harness.c")).read_text()
        code = harness.replace("/* PRODUCTION */", "\n".join((helpers, execute, option_guard, log_guard)))
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-host-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "harness.c"
        cls.binary = Path(cls.temp.name) / ("harness.exe" if os.name == "nt" else "harness")
        source.write_text(code)
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu11", "-Werror", "-Wno-unused-variable",
                                 "-Wno-unused-function", str(source), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Host harness compilation failed:\n" + result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_audio_is_not_syncboss(self):
        self.case("syncboss")

    def test_missing_syncboss_resume_rejected(self):
        self.case("syncboss_no_resume")

    def test_syncboss_compatible_requires_correct_driver(self):
        self.case("syncboss_wrong_driver")

    def test_syncboss_driver_requires_correct_compatible(self):
        self.case("syncboss_wrong_compatible")

    def test_syncboss_bus_number_can_change(self):
        self.case("syncboss_renumbered")

    def test_missing_aop_irq_rejected_before_shutdown(self):
        self.case("aop_no_irq")

    def test_missing_aop_shutdown_rejected_before_shutdown(self):
        self.case("aop_no_shutdown")

    def test_missing_rpmh_symbol_rejected(self):
        self.case("rpmh_no_symbol")

    def test_missing_rpmh_device_rejected(self):
        self.case("rpmh_no_device")

    def test_available_dependencies_pass_preflight_without_suspending(self):
        self.case("preflight")

    def test_hollywood_log_read_clear_and_write_rejected(self):
        self.case("log")

    def test_missing_model_log_rejected(self):
        self.case("log_no_model")

    def test_stage_only_rejects_each_hardware_action(self):
        self.case("stage_only")

    def test_plain_staging_options_accepted(self):
        self.case("staging")

    def test_real_jump_requires_destination_ownership_before_shutdown(self):
        self.case("unowned_jump")

    def test_rehearsal_resumes_syncboss_then_gpu_before_userspace(self):
        self.case("rehearsal_order")

    def test_freezer_abort_is_respected_without_suppressing_wakeup(self):
        self.case("freezer_abort")

    def test_gpu_failure_restores_hardware_before_thaw(self):
        self.case("gpu_failure")

    def test_failed_syncboss_suspend_does_not_queue_duplicate_resume(self):
        self.case("syncboss_failure")

    def test_failed_hardware_resume_does_not_thaw_userspace(self):
        self.case("resume_failure")

    def test_watchdog_failure_after_usb_stop_cannot_return_to_apps(self):
        self.case("watchdog_failure")

    def test_requested_rpmh_failure_prevents_physical_entry(self):
        self.case("rpmh_failure")


if __name__ == "__main__":
    unittest.main()
