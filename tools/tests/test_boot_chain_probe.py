"""Read-only boot-chain prefix capture; no real ADB or proprietary fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import quest2_preflight as preflight
import quest2_shutdown_probe as shutdown

BOOT = "12345678-1234-1234-1234-123456789abc"


class BootChainProbeTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_boot_chain_probe.py"
        self.assertTrue(path.is_file(), "fixed boot-chain collector missing")
        spec = importlib.util.spec_from_file_location("boot_chain_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, module, *, identity_change=None, late_identity_change=None,
                health_change=None, initial_health=None, fault=None):
        payloads = {"abl": b"A"*4096, "tz": b"T"*8192}
        module.IMAGES = {name: (len(data), hashlib.sha256(data).hexdigest())
                         for name, data in payloads.items()}
        identity_commands = {tuple(v): k for k, v in preflight.PROBES.items()}
        health_commands = {("exec-out", shutdown.remote_command(k)): k for k in module.HEALTH}
        values = {**preflight.EXPECTED, "slot": "_a", "verified_boot": "green",
                  "vbmeta_state": "locked", "boot_complete": "1", "boot_reason": "powerkey",
                  "root_uid": "uid=0(root)", "boot": BOOT, "cpus": "0-7",
                  "taint": "5124", "modules": "stock_module 1 0 - Live 0x0\n"}
        values.update(initial_health or {})
        metadata = b"/dev/block/sde14\n61b0 8 4e 123 7\n"
        commands = {}
        for name, data in payloads.items():
            commands[module.remote_command("metadata", name, "_a")] = ("metadata:"+name, metadata)
            commands[module.remote_command("image", name, "_a")] = ("image:"+name, data)
        calls, counts = [], {}

        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["adb-test", "-s", "USB-TEST"])
            self.assertFalse(kwargs.get("shell", False))
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 15)
            args = tuple(command[3:])
            if args in identity_commands or args in health_commands:
                key = (identity_commands | health_commands)[args]
                calls.append(key)
                counts[key] = counts.get(key, 0)+1
                value = values[key]
                if identity_change and key == identity_change[0]:
                    value = identity_change[1]
                if late_identity_change and key == late_identity_change[0] and counts[key] > 1:
                    value = late_identity_change[1]
                if health_change and key == health_change[0] and counts[key] > 1:
                    value = health_change[1]
                data = value.encode()
                if args in health_commands:
                    data += b"\nQKX_PASSIVE_STATUS=0\n"
                return subprocess.CompletedProcess(command, 0, data, b"")
            key, data = commands[args[1]]
            calls.append(key)
            counts[key] = counts.get(key, 0)+1
            status = b"0\n"
            if fault == "metadata_changed" and key.startswith("metadata:") and counts[key] > 1:
                data = data.replace(b" 123 ", b" 124 ")
            if fault == "not_block" and key.startswith("metadata:"):
                data = data.replace(b"61b0", b"81b0")
            if fault == "bad_path" and key.startswith("metadata:"):
                data = data.replace(b"/dev/block/sde14", b"/data/local/tmp/fake")
            if key.startswith("image:"):
                if fault == "timeout":
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if fault == "transport":
                    return subprocess.CompletedProcess(command, 1, data, b"failed")
                if fault == "short": data = data[:-1]
                if fault == "long": data += b"X"
                if fault == "hash": data = b"X" + data[1:]
                if fault == "remote": status = b"1\n"
                if fault == "malformed_status": status = b"0\ntrailing"
            if not (fault == "footer" and key.startswith("image:")):
                data += b"\nQKX_BOOT_CHAIN_STATUS=" + status
            return subprocess.CompletedProcess(command, 0, data, b"")

        return runner, calls, payloads

    def test_exact_published_pins(self):
        module = self.module()
        self.assertEqual(module.IMAGES, {
            "abl": (274432, "75dc2174995fd39588bcbbd8b3455afc7bad4999a8914e0c40b9cc1509bcef5c"),
            "tz": (3207168, "9384241f1169e4953992fc06723507e8ac217006a5dfb92e47826068500dc8d3"),
        })

    def test_complete_matches_never_authorize_execution(self):
        module = self.module()
        runner, calls, payloads = self.fixture(module)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"capture"
            report = module.collect("adb-test", "USB-TEST", out, runner=runner)
            self.assertEqual(report["capture_status"], "complete")
            self.assertEqual(report["scope"], "active_slot_partition_prefix_identity")
            for flag in ("execution_ready", "driver_execution_validated", "recovery_validated",
                         "device_mutation", "running_secure_image_measured"):
                self.assertIs(report[flag], False)
            self.assertEqual(report["status"], "EXECUTION_BLOCKED")
            self.assertFalse((out/"INCOMPLETE.json").exists())
            self.assertEqual(json.loads((out/"capture.json").read_text()), report)
            self.assertNotIn("USB-TEST", (out/"capture.json").read_text())
            for name, data in payloads.items():
                self.assertEqual((out/(name+".img")).read_bytes(), data)
                self.assertTrue(report["images"][name]["matches_pinned_ota_prefix"])
            self.assertEqual(calls.count("image:abl"), 1)
            self.assertEqual(calls.count("image:tz"), 1)
            self.assertEqual(calls[-1], "boot")

    def test_identity_admission_precedes_partition_reads(self):
        for key, value in (("device", "other"), ("build", "old"), ("kernel", "other"),
                           ("slot", "_c"), ("slot", "_a;reboot"), ("verified_boot", "orange"),
                           ("vbmeta_state", "unlocked"), ("boot_complete", "0"), ("root_uid", "uid=2000")):
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as temp:
                module = self.module()
                runner, calls, _ = self.fixture(module, identity_change=(key, value))
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", out, runner=runner)
                self.assertFalse(any(c.startswith(("metadata:", "image:")) for c in calls))
                self.assertTrue((out/"INCOMPLETE.json").exists())
                self.assertFalse((out/"capture.json").exists())

    def test_transport_frame_size_hash_and_metadata_fail_closed(self):
        for fault in ("timeout", "transport", "short", "long", "hash", "remote", "footer",
                      "malformed_status", "metadata_changed", "not_block", "bad_path"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                module = self.module()
                runner, calls, _ = self.fixture(module, fault=fault)
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", out, runner=runner)
                self.assertTrue((out/"INCOMPLETE.json").exists())
                self.assertFalse((out/"capture.json").exists())
                self.assertNotIn("image:tz", calls)

    def test_changed_health_refuses_complete_capture(self):
        changes = (("boot", BOOT[:-1]+"d"), ("cpus", "0-3"), ("taint", "5125"),
                   ("modules", "different_module 1 0 - Live 0x0\n"))
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                module = self.module()
                runner, _, _ = self.fixture(module, health_change=change)
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", out, runner=runner)
                self.assertFalse((out/"capture.json").exists())

    def test_invalid_input_and_existing_output_issue_no_commands(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for serial, budget in (("-x", 120), ("x;reboot", 120), ("ok", True),
                                   ("ok", 0), ("ok", float("inf")), ("ok", 301)):
                with self.subTest(serial=serial, budget=budget), patch.object(module.subprocess, "run") as runner:
                    with self.assertRaises(preflight.CaptureError):
                        module.collect("adb-test", serial, root/"new", runner=runner, overall_timeout=budget)
                    runner.assert_not_called()
                    self.assertFalse((root/"new").exists())
            (root/"sentinel").write_bytes(b"KEEP")
            with patch.object(module.subprocess, "run") as runner:
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", root, runner=runner)
                runner.assert_not_called()
            self.assertEqual((root/"sentinel").read_bytes(), b"KEEP")

    def test_late_slot_build_and_root_changes_leave_incomplete_evidence(self):
        for change in (("slot", "_b"), ("build", "different"), ("root_uid", "uid=2000")):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                module = self.module()
                runner, _, _ = self.fixture(module, late_identity_change=change)
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", out, runner=runner)
                self.assertTrue((out/"INCOMPLETE.json").exists())
                self.assertFalse((out/"capture.json").exists())

    def test_malformed_health_and_resident_project_modules_precede_partition_reads(self):
        changes = [{"boot": "-"*36}, {"cpus": "broken"}, {"taint": "-1"}]
        changes += [{"modules": name+" 1 0 - Live 0x0\n"} for name in (
            "quest_kexec", "marker_read", "usb_log", "camera_dsp_diag", "owner_gate_selftest", "scm_version_probe")]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                module = self.module()
                runner, calls, _ = self.fixture(module, initial_health=change)
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", Path(temp)/"capture", runner=runner)
                self.assertFalse(any(c.startswith(("metadata:", "image:")) for c in calls))

    def test_host_budget_applies_to_identity_as_well_as_partition_commands(self):
        module = self.module()
        for clock in (iter([0, 181]), iter([2, 1]), iter([float("inf")])):
            with self.subTest(clock=clock), tempfile.TemporaryDirectory() as temp:
                runner, calls, _ = self.fixture(module)
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError):
                    module.collect("adb-test", "USB-TEST", out, runner=runner,
                                   monotonic=lambda: next(clock))
                self.assertLessEqual(len(calls), 1)
                self.assertTrue((out/"INCOMPLETE.json").exists())
                self.assertFalse((out/"capture.json").exists())

    def test_metadata_parser_rejects_ambiguous_or_unbounded_block_identity(self):
        module = self.module()
        rows = (b"", b"\xff", b"/dev/block/sde14\n61b0 8 4e 123 7\nextra\n",
                b"/dev/block/sde14\n61b0 1000 4e 123 7\n",
                b"/dev/block/sde14\n61b0 8 100000 123 7\n",
                b"/dev/block/sde14\n61b0 8 4e 0 7\n",
                b"/dev/block/sde14\n61b0 8 4e -1 7\n")
        for data in rows:
            with self.subTest(data=data), self.assertRaises(preflight.CaptureError):
                module._metadata(data)

    def test_fixed_shell_commands_allow_no_arbitrary_paths_or_writes(self):
        module = self.module()
        for image in ("../tz", "boot", "tz;reboot", "", None):
            with self.subTest(image=image), self.assertRaises(ValueError):
                module.remote_command("image", image, "_a")
        for slot in ("a", "_c", "_a;reboot", "", None):
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                module.remote_command("image", "tz", slot)
        for probe in ("write", "flash", "", None):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                module.remote_command(probe, "tz", "_a")
        for image, blocks in (("abl", 67), ("tz", 783)):
            for slot in ("_a", "_b"):
                command = module.remote_command("image", image, slot)
                body = shlex.split(command)
                self.assertEqual(body[:2], ["su", "-c"])
                self.assertIn("/dev/block/by-name/"+image+slot, body[2])
                self.assertIn("bs=4096 count="+str(blocks), body[2])
                self.assertNotIn(" of=", body[2])
                self.assertNotIn("reboot", body[2])

    def test_actual_shell_binary_count_footer_and_regular_file_refusal(self):
        module = self.module()
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():
            bash = shutil.which("bash")
        self.assertTrue(bash, "Bash is required for the real shell fixture")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            posix = "/"+root.drive[0].lower()+root.as_posix()[2:] if root.drive else root.as_posix()
            payload = (b"\x00\xff\nQKX_BOOT_CHAIN_STATUS=7\n" + b"X"*4096)[:4096]
            (root/"abl_a").write_bytes(payload)
            module.IMAGES = {"abl": (len(payload), hashlib.sha256(payload).hexdigest())}
            block_fixture = False

            def runner(command, **kwargs):
                parts = shlex.split(command[-1])
                self.assertEqual(parts[:2], ["su", "-c"])
                script = parts[2].replace("/dev/block/by-name", posix)
                if block_fixture:
                    # The fixture supplies a regular file as the block backend;
                    # dd, byte counts, binary stdout and status framing stay real.
                    script = ('function [ { if builtin [ "$1" = "-b" ]; then '
                              'builtin [ -f "$2" ]; else builtin [ "$@"; fi; }\n' + script)
                return subprocess.run([str(bash), "-c", script], **kwargs)

            with self.assertRaises(preflight.CaptureError):
                module._read("unused", "USB-TEST", "image", "abl", "_a", runner)
            block_fixture = True
            self.assertEqual(module._read("unused", "USB-TEST", "image", "abl", "_a", runner), payload)
            metadata = module._read("unused", "USB-TEST", "metadata", "abl", "_a", runner)
            with self.assertRaises(preflight.CaptureError):
                module._metadata(metadata)
            (root/"abl_a").write_bytes(payload[:-1])
            with self.assertRaises(preflight.CaptureError):
                module._read("unused", "USB-TEST", "image", "abl", "_a", runner)
            with self.assertRaises(preflight.CaptureError):
                module._read("unused", "USB-TEST", "image", "abl", "_b", runner)


if __name__ == "__main__":
    unittest.main()
