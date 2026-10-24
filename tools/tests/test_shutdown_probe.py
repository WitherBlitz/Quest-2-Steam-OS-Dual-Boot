"""Mocked fixed-command collection; no real ADB/device operations."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import quest2_preflight as preflight


class ShutdownProbeTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_shutdown_probe.py"
        self.assertTrue(path.is_file(), "passive shutdown probe is missing")
        spec = importlib.util.spec_from_file_location("shutdown_probe", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def setup_capture(self, module, *, bad_identity=False, no_root=False, changed_boot=False,
                      bad_probe=None, oversize=False, remote_failure=False, missing_status=False,
                      changed_identity=False, changed_modules=False):
        calls, counts = [], {}
        mapping = {tuple(value): key for key, value in preflight.PROBES.items()}
        mapping.update({("exec-out", module.remote_command(key)): key for key in module.PROBES})
        values = {**preflight.EXPECTED, "slot": "_a", "verified_boot": "green", "vbmeta_state": "locked",
                  "boot_complete": "1", "boot_reason": "powerkey", "root_uid": "uid=0(root)",
                  "boot": "12345678-1234-1234-1234-123456789abc", "cpus": "0-7", "taint": "5124",
                  "modules": "stock_module 1 0 - Live 0x0\n", "panic": "5",
                  "watchdog": "disable=0\npet_time=4000\n", "dsp": "device=subsys0\nname=adsp\nstate=ONLINE\n",
                  "bindings": "MISSING\n", "camera": "MISSING\n", "iommu": "MISSING\n"}
        if bad_identity: values["device"] = "different"
        if no_root: values["root_uid"] = "uid=2000(shell)"

        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["adb-test", "-s", "USB-TEST"])
            self.assertFalse(kwargs.get("shell", False))
            key = mapping[tuple(command[3:])]; calls.append(key)
            counts[key] = counts.get(key, 0) + 1
            if key == bad_probe: raise subprocess.TimeoutExpired(command, 15)
            data = values[key]
            if key == "device" and changed_identity and counts[key] > 1: data = "different"
            if key == "modules" and changed_modules and counts[key] > 1: data += "new_module 1 0 - Live 0x0\n"
            if key == "boot" and changed_boot and counts[key] > 1:
                data = "12345678-1234-1234-1234-123456789abd"
            if key == "dsp" and oversize: data = "x" * (module.MAX_READ+1)
            if tuple(command[3:])[0] == "exec-out":
                if not missing_status:
                    data += "\nQKX_PASSIVE_STATUS=" + ("1" if remote_failure else "0") + "\n"
            return subprocess.CompletedProcess(command, 0, data.encode(), b"")

        return runner, calls, values

    def collect(self, module, out, runner):
        source = {"source_verified": True, "kernel_commit": module.KERNEL,
                  "stock_binary_equivalence_verified": False}
        return module.collect("adb-test", "USB-TEST", out, source, runner=runner)

    def test_complete_capture_remains_blocked_for_all_shutdown_and_recovery(self):
        module = self.module(); runner, calls, values = self.setup_capture(module)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "capture"; report = self.collect(module, out, runner)
            self.assertEqual(report["status"], "HANDOFF_BLOCKED")
            for key in ("handoff_ready", "dma_quiescence_validated", "recovery_validated",
                        "camera_shutdown_validated", "dsp_shutdown_validated"):
                self.assertIs(report[key], False)
            self.assertEqual(report["watchdog_hardware_state"], "unproven")
            self.assertTrue((out / "capture.json").is_file())
            self.assertFalse((out / "INCOMPLETE.json").exists())
            self.assertNotIn("USB-TEST", (out / "capture.json").read_text())
            self.assertTrue(set(module.PROBES).issubset(calls))

    def test_mismatched_identity_stops_before_root_or_subsystem_reads(self):
        module = self.module(); runner, calls, _ = self.setup_capture(module, bad_identity=True)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(preflight.CaptureError): self.collect(module, Path(temp)/"new", runner)
        self.assertNotIn("root_uid", calls); self.assertNotIn("watchdog", calls)

    def test_missing_root_stops_before_subsystem_reads(self):
        module = self.module(); runner, calls, _ = self.setup_capture(module, no_root=True)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(preflight.CaptureError): self.collect(module, Path(temp)/"new", runner)
        self.assertNotIn("watchdog", calls)

    def test_changed_boot_leaves_incomplete_evidence(self):
        module = self.module(); runner, _, _ = self.setup_capture(module, changed_boot=True)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"new"
            with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
            self.assertTrue((out/"INCOMPLETE.json").exists()); self.assertFalse((out/"capture.json").exists())

    def test_resident_scm_module_prevents_subsystem_reads(self):
        module = self.module(); runner, calls, values = self.setup_capture(module)
        values["modules"] += "scm_version_probe 1 0 - Live 0x0\n"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(preflight.CaptureError, "project module already resident"):
                self.collect(module, Path(temp)/"new", runner)
        self.assertNotIn("watchdog", calls)
        self.assertNotIn("dsp", calls)

    def test_timeout_or_oversize_cannot_publish_complete_capture(self):
        module = self.module()
        for options in ({"bad_probe": "dsp"}, {"oversize": True}):
            runner, _, _ = self.setup_capture(module, **options)
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"new"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertFalse((out/"capture.json").exists())

    def test_existing_output_is_preserved_without_device_operations(self):
        module = self.module(); runner, calls, _ = self.setup_capture(module)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(preflight.CaptureError): self.collect(module, Path(temp), runner)
        self.assertEqual(calls, [])

    def test_software_disabled_flag_never_becomes_a_recovery_oracle(self):
        module = self.module(); runner, _, values = self.setup_capture(module)
        values["watchdog"] = "disable=1\npet_time=4000\n"
        with tempfile.TemporaryDirectory() as temp:
            report = self.collect(module, Path(temp)/"new", runner)
        self.assertEqual(report["watchdog_hardware_state"], "unproven")
        self.assertFalse(report["recovery_validated"])

    def test_unknown_probe_is_rejected_without_running_a_command(self):
        module = self.module()
        with patch.object(module.subprocess, "run") as run:
            with self.assertRaises(ValueError): module.read("adb-test", "USB-TEST", "write_watchdog")
            run.assert_not_called()

    def test_root_transport_success_cannot_hide_remote_failure_or_missing_status(self):
        module = self.module()
        for options in ({"remote_failure": True}, {"missing_status": True}):
            runner, _, _ = self.setup_capture(module, **options)
            with self.subTest(options=options):
                with self.assertRaises(preflight.CaptureError):
                    module.read("adb-test", "USB-TEST", "dsp", runner=runner)

    def test_final_identity_or_module_change_prevents_complete_report(self):
        module = self.module()
        for options in ({"changed_identity": True}, {"changed_modules": True}):
            runner, _, _ = self.setup_capture(module, **options)
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"new"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertFalse((out/"capture.json").exists())

    def test_unknown_software_nodes_cannot_establish_hardware_shutdown(self):
        module = self.module(); runner, _, values = self.setup_capture(module)
        for key in ("dsp", "watchdog", "bindings", "camera", "iommu"): values[key] = "MISSING\n"
        with tempfile.TemporaryDirectory() as temp:
            report = self.collect(module, Path(temp)/"new", runner)
        self.assertFalse(report["handoff_ready"]); self.assertFalse(report["dma_quiescence_validated"])

    def test_unverified_source_prevents_all_device_calls_and_output_creation(self):
        module = self.module(); runner, calls, _ = self.setup_capture(module)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"new"
            with self.assertRaises(preflight.CaptureError):
                module.collect("adb-test", "USB-TEST", out, {}, runner=runner)
            self.assertFalse(out.exists())
        self.assertEqual(calls, [])


if __name__ == "__main__": unittest.main()
