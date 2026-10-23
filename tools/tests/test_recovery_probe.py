"""Mocked existing-pstore collection; never invokes ADB or a real device."""
import importlib.util
import hashlib
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
MOUNT = b"42 1 0:21 / /sys/fs/pstore rw - pstore pstore rw\n"


class RecoveryProbeTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_recovery_probe.py"
        self.assertTrue(path.is_file(), "recovery collector missing")
        spec = importlib.util.spec_from_file_location("recovery_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def setup_capture(self, module, *, files=None, mount=MOUNT, extra_names=(),
                      bad_mode=False, changed_metadata=False, changed_boot=False,
                      changed_identity=False, fault=None, payload_override=None, boot=BOOT,
                      late_boot_change=False, stat_device=21):
        files = {"dmesg-ramoops-0": b"old retained log\x00\xff"} if files is None else files
        identity_commands = {tuple(value): key for key, value in preflight.PROBES.items()}
        health_commands = {( "exec-out", shutdown.remote_command(key)): key
                           for key in ("boot", "cpus", "taint", "modules")}
        calls, counts = [], {}
        values = {**preflight.EXPECTED, "slot": "_a", "verified_boot": "green",
                  "vbmeta_state": "locked", "boot_complete": "1", "boot_reason": "powerkey",
                  "root_uid": "uid=0(root)", "boot": boot, "cpus": "0-7", "taint": "5124",
                  "modules": "stock_module 1 0 - Live 0x0\n"}
        mapping = {module.remote_command("mountinfo"): ("mountinfo", mount),
                   module.remote_command("entries"): ("entries", b"".join(
                       name.encode() + b"\0" for name in [*files, *extra_names]))}
        for index, (name, data) in enumerate(files.items()):
            metadata = f'{"a180" if bad_mode else "8180"} {len(data)} {index+100} {stat_device} 1 0 0\n'.encode()
            mapping[module.remote_command("metadata", name)] = ("metadata:" + name, metadata)
            if len(data) <= module.MAX_FILE:
                mapping[module.remote_command("record", name, size=len(data))] = ("record:" + name, data)

        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["adb-test", "-s", "USB-TEST"])
            self.assertFalse(kwargs.get("shell", False))
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 15)
            args = tuple(command[3:])
            if args in identity_commands or args in health_commands:
                key = (identity_commands | health_commands)[args]
                counts[key] = counts.get(key, 0) + 1
                calls.append(key)
                value = values[key]
                if key == "boot" and (changed_boot and counts[key] > 1 or
                                      late_boot_change and counts.get("root_uid", 0) > 1):
                    value = BOOT[:-1] + "d"
                if changed_identity and key == "device" and counts[key] > 1:
                    value = "different"
                data = value.encode()
                if args in health_commands:
                    data += b"\nQKX_PASSIVE_STATUS=0\n"
                return subprocess.CompletedProcess(command, 0, data, b"")
            key, data = mapping[args[1]]
            calls.append(key)
            counts[key] = counts.get(key, 0) + 1
            if changed_metadata and key.startswith("metadata:") and counts[key] > 1:
                data = data.replace(b" 0 0\n", b" 1 0\n")
            if payload_override is not None and key.startswith("record:"):
                data = payload_override
            if fault == "timeout" and key.startswith("record:"):
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            status = b"1" if fault == "remote" and key.startswith("record:") else b"0"
            if fault != "footer" or not key.startswith("record:"):
                data += b"\nQKX_RECOVERY_STATUS=" + status + b"\n"
            return subprocess.CompletedProcess(command, 0, data, b"")

        return runner, calls

    def collect(self, module, out, runner, **kwargs):
        return module.collect("adb-test", "USB-TEST", out,
                              {"source_verified": True, "kernel_commit": module.KERNEL,
                               "archive_sha256": module.ARCHIVE_SHA}, runner=runner, **kwargs)

    def test_complete_binary_and_compressed_records_remain_unproven(self):
        module = self.module()
        files = {"dmesg-ramoops-1.enc.z": b"\xff\x00opaque.compressed", "console-ramoops-0": b"console",
                 "pmsg-ramoops-0": b"pmsg"}
        runner, calls = self.setup_capture(module, files=files)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "capture"
            report = self.collect(module, out, runner)
            self.assertEqual(report["capture_status"], "complete")
            self.assertFalse(report["recovery_validated"])
            self.assertFalse(report["handoff_ready"])
            self.assertFalse((out / "INCOMPLETE.json").exists())
            for record in report["records"]:
                artifact = report["artifacts"][record["artifact"]]
                self.assertEqual((out / artifact["filename"]).read_bytes(), files[record["name"]])
                self.assertEqual(record["remote_status"], 0)
            self.assertTrue(report["records"][0]["compressed_opaque"])
        self.assertEqual(sum(key.startswith("record:") for key in calls), 3)

    def test_absent_or_wrong_mount_is_explicit_and_never_enumerated(self):
        module = self.module()
        for mount in (b"1 0 0:1 / / rw - rootfs rootfs rw\n",
                      MOUNT.replace(b" - pstore ", b" - tmpfs ")):
            runner, calls = self.setup_capture(module, mount=mount)
            with self.subTest(mount=mount), tempfile.TemporaryDirectory() as temp:
                report = self.collect(module, Path(temp) / "capture", runner)
                self.assertEqual(report["pstore"]["availability"], "unavailable")
                self.assertEqual(report["records"], [])
            self.assertNotIn("entries", calls)

    def test_empty_mount_and_unknown_names_record_coverage_gaps_without_reading(self):
        module = self.module()
        for names in ((), ("dmesg-ramoops-2", "unexpected; touch nope", "dmesg-ramoops-0.z")):
            runner, calls = self.setup_capture(module, files={}, extra_names=names)
            with tempfile.TemporaryDirectory() as temp:
                report = self.collect(module, Path(temp) / "capture", runner)
                self.assertEqual(report["records"], [])
                self.assertFalse(report["recovery_validated"])
                self.assertEqual(len(report["coverage_gaps"]), len(names))
            self.assertFalse(any(key.startswith(("metadata:", "record:")) for key in calls))

    def test_child_mount_and_invalid_device_refuse_before_enumeration(self):
        module = self.module()
        child = b"43 42 0:22 / /sys/fs/pstore/dmesg-ramoops-0 rw - tmpfs tmpfs rw\n"
        for mount in (MOUNT+child, MOUNT.replace(b"0:21", b"bad"),
                      MOUNT.replace(b"0:21", b"4096:21"), MOUNT.replace(b"0:21", b"0:1048576")):
            runner, calls = self.setup_capture(module, mount=mount)
            with self.subTest(mount=mount), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(preflight.CaptureError):
                    self.collect(module, Path(temp)/"capture", runner)
            self.assertNotIn("entries", calls)

    def test_file_device_must_match_source_encoded_parent_before_payload(self):
        module = self.module()
        for device in (22, 0x100000000):
            runner, calls = self.setup_capture(module, stat_device=device)
            with self.subTest(device=device), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(preflight.CaptureError):
                    self.collect(module, Path(temp)/"capture", runner)
            self.assertFalse(any(key.startswith("record:") for key in calls))
        for device, encoded in ((b"1:256", 0x100100), (b"4095:1048575", 0xffffffff)):
            runner, _ = self.setup_capture(module, mount=MOUNT.replace(b"0:21", device), stat_device=encoded)
            with self.subTest(device=device), tempfile.TemporaryDirectory() as temp:
                report = self.collect(module, Path(temp)/"capture", runner)
                self.assertEqual(report["records"][0]["metadata_before"]["device"], encoded)

    def test_compare_refuses_child_mount_and_wrong_device_with_rehashed_artifacts(self):
        module = self.module()
        for mutation in ("child", "device", "malformed"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"capture"
                runner, _ = self.setup_capture(module)
                report = self.collect(module, out, runner)
                keys = ("mountinfo-before", "mountinfo-after") if mutation != "device" else (
                    "metadata-before-dmesg-ramoops-0", "metadata-after-dmesg-ramoops-0")
                for key in keys:
                    item = report["artifacts"][key]
                    path = out/item["filename"]
                    raw = path.read_bytes()
                    if mutation == "child":
                        raw += b"43 42 0:22 / /sys/fs/pstore/dmesg-ramoops-0 rw - tmpfs tmpfs rw\n"
                    elif mutation == "device": raw = raw.replace(b" 21 1 ", b" 22 1 ")
                    else: raw = raw.replace(b"0:21", b"malformed")
                    path.write_bytes(raw)
                    item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                if mutation == "device":
                    for key in ("metadata_before", "metadata_after"):
                        report["records"][0][key]["device"] = 22
                elif mutation == "malformed":
                    report["pstore"]["mount"]["device"] = "malformed"
                (out/"capture.json").write_text(json.dumps(report))
                with self.assertRaises(preflight.CaptureError): module.compare(out, out)

    def test_compare_refuses_nonregular_manifest_without_opening(self):
        module = self.module()
        with patch.object(Path, "is_file", return_value=False), \
                patch.object(Path, "open", side_effect=AssertionError("nonregular manifest opened")):
            with self.assertRaises(preflight.CaptureError):
                module.compare("nonregular-before", "nonregular-after")

    def test_symlink_metadata_refuses_before_file_read(self):
        module = self.module()
        runner, calls = self.setup_capture(module, bad_mode=True)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "capture"
            with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
            self.assertTrue((out / "INCOMPLETE.json").exists())
        self.assertFalse(any(key.startswith("record:") for key in calls))

    def test_record_and_metadata_races_leave_incomplete(self):
        module = self.module()
        for options in ({"changed_metadata": True}, {"payload_override": b"short"}):
            runner, _ = self.setup_capture(module, **options)
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                out = Path(temp) / "capture"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertFalse((out / "capture.json").exists())
                self.assertTrue((out / "INCOMPLETE.json").exists())

    def test_remote_error_missing_footer_and_timeout_never_publish(self):
        module = self.module()
        for fault in ("remote", "footer", "timeout"):
            runner, _ = self.setup_capture(module, fault=fault)
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                out = Path(temp) / "capture"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertTrue((out / "INCOMPLETE.json").exists())
                if fault == "remote":
                    self.assertIn("remote_status=1", json.loads((out/"INCOMPLETE.json").read_text())["error"])

    def test_mountinfo_and_aggregate_limits_refuse_without_retry(self):
        module = self.module()
        for options in ({"mount": b"x" * (module.MAX_META+1)},
                        {"files": {name: b"x" * module.MAX_FILE for name in
                         ("dmesg-ramoops-0", "dmesg-ramoops-1", "console-ramoops-0", "pmsg-ramoops-0")}}):
            runner, calls = self.setup_capture(module, **options)
            with self.subTest(options=list(options)), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(preflight.CaptureError):
                    self.collect(module, Path(temp)/"capture", runner)
            self.assertLessEqual(calls.count("mountinfo"), 2)
            self.assertLessEqual(sum(key.startswith("record:") for key in calls), 3)

    def test_changed_boot_or_identity_refuses_complete_receipt(self):
        module = self.module()
        for option in ({"changed_boot": True}, {"changed_identity": True}, {"late_boot_change": True}):
            runner, _ = self.setup_capture(module, **option)
            with tempfile.TemporaryDirectory() as temp:
                out = Path(temp) / "capture"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertFalse((out / "capture.json").exists())

    def test_new_output_and_verified_source_are_required_before_device_calls(self):
        module = self.module()
        runner, calls = self.setup_capture(module)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(preflight.CaptureError): self.collect(module, Path(temp), runner)
            with self.assertRaises(preflight.CaptureError):
                module.collect("adb-test", "USB-TEST", Path(temp)/"new", {}, runner=runner)
        self.assertEqual(calls, [])

    def test_commands_reject_paths_and_bound_device_reads(self):
        module = self.module()
        for name in ("../dmesg-ramoops-0", "dmesg-ramoops-2", "pmsg-ramoops-1", "pmsg-ramoops-0.enc.z"):
            with self.assertRaises(ValueError): module.remote_command("record", name, size=1)
        for size in (-1, module.MAX_FILE + 1, True):
            with self.assertRaises(ValueError): module.remote_command("record", "dmesg-ramoops-0", size=size)
        command = module.remote_command("record", "dmesg-ramoops-0", size=4096)
        self.assertIn("bs=4096 count=2", command)
        self.assertNotIn("/dev/pmsg0", command)
        with self.assertRaises(ValueError): module.remote_command("rescan")

    def test_file_aggregate_and_listing_caps_refuse(self):
        module = self.module()
        for files, names in (({"dmesg-ramoops-0": b"x" * (module.MAX_FILE + 1)}, ()),
                             ({}, tuple("unknown" + str(i) for i in range(module.MAX_NAMES + 1)))):
            runner, _ = self.setup_capture(module, files=files, extra_names=names)
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises((ValueError, preflight.CaptureError)):
                    self.collect(module, Path(temp)/"capture", runner)

    def test_overall_host_budget_is_finite_and_nonresetting(self):
        module = self.module()
        runner, calls = self.setup_capture(module)
        ticks = iter([0, 0, 301])
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"capture"
            with self.assertRaises(preflight.CaptureError):
                self.collect(module, out, runner, monotonic=lambda: next(ticks))
            self.assertTrue((out/"INCOMPLETE.json").exists())
        self.assertLessEqual(len(calls), 1)

    def test_compare_reports_changed_boot_and_stale_bytes_without_causality(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp)/"before", Path(temp)/"after"
            runner, _ = self.setup_capture(module, files={"dmesg-ramoops-0": b"retained",
                "dmesg-ramoops-1": b"old", "console-ramoops-0": b"gone"})
            self.collect(module, before, runner)
            runner, _ = self.setup_capture(module, boot=BOOT[:-1]+"d",
                files={"dmesg-ramoops-0": b"retained", "dmesg-ramoops-1": b"new",
                       "pmsg-ramoops-0": b"added"})
            self.collect(module, after, runner)
            report = module.compare(before, after)
            self.assertEqual(report["unchanged_record_ids"], ["dmesg:0"])
            self.assertTrue(report["boot_changed"])
            self.assertEqual(report["changed_record_ids"], ["dmesg:1"])
            self.assertEqual(report["missing_after_record_ids"], ["console:0"])
            self.assertEqual(report["new_after_record_ids"], ["pmsg:0"])
            self.assertFalse(report["recovery_validated"])
            self.assertFalse(report["handoff_ready"])
            self.assertEqual(report["reset_causality"], "unproven")

    def test_compare_rejects_incomplete_corrupt_hash_and_unsafe_manifest_paths(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp)/"before", Path(temp)/"after"
            for out in (before, after):
                runner, _ = self.setup_capture(module)
                self.collect(module, out, runner)
            capture = after/"capture.json"
            original = json.loads(capture.read_text())
            for mutation in ("incomplete", "hash", "path", "missing", "source", "metadata", "opaque", "gaps"):
                report = json.loads(json.dumps(original))
                if mutation == "incomplete": report["capture_status"] = "incomplete"
                elif mutation == "missing": report["artifacts"] = {}
                elif mutation == "source": report["source_audit"]["archive_sha256"] = "0"*64
                elif mutation == "metadata":
                    report["records"][0]["metadata_before"]["inode"] += 1
                    report["records"][0]["metadata_after"]["inode"] += 1
                elif mutation == "opaque": report["records"][0]["compressed_opaque"] = True
                elif mutation == "gaps": report["coverage_gaps"] = [{"name_hex": "00", "reason": "made_up"}]
                else:
                    artifact = next(iter(report["artifacts"].values()))
                    artifact["sha256" if mutation == "hash" else "filename"] = "0"*64 if mutation == "hash" else "../outside"
                capture.write_text(json.dumps(report))
                with self.subTest(mutation=mutation), self.assertRaises(preflight.CaptureError):
                    module.compare(before, after)

    def test_actual_shell_runs_fixed_binary_read_metadata_and_remote_error(self):
        module = self.module()
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():
            available = shutil.which("bash")
            if not available:
                self.skipTest("Bash unavailable")
            bash = Path(available)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            posix = "/" + root.drive[0].lower() + root.as_posix()[2:] if root.drive else root.as_posix()
            name = "dmesg-ramoops-0.enc.z"
            payload = b"\x00\xffopaque\nQKX_RECOVERY_STATUS=7\n"
            (root/name).write_bytes(payload)

            def runner(command, **kwargs):
                parts = shlex.split(command[-1])
                self.assertEqual(parts[:2], ["su", "-c"])
                script = parts[2].replace("/sys/fs/pstore", posix)
                return subprocess.run([str(bash), "-c", script], **kwargs)

            metadata = module._metadata(module._read("unused", "unused", "metadata", runner, name))
            self.assertEqual(metadata["size"], len(payload))
            self.assertEqual(module._read("unused", "unused", "record", runner, name, size=len(payload)), payload)
            self.assertEqual(module._read("unused", "unused", "entries", runner), name.encode()+b"\0")
            with self.assertRaises(preflight.CaptureError):
                module._read("unused", "unused", "record", runner, "dmesg-ramoops-1", size=1)


if __name__ == "__main__":
    unittest.main()
