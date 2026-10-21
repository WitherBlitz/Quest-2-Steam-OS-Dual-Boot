"""Host-only tests; subprocess doubles never contact a real headset."""
import importlib.util
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "quest2_preflight.py"
ADB = "adb-test"
SERIAL = "QUEST-TEST-ONLY"


def load_tool():
    if not TOOL.exists():
        return None
    spec = importlib.util.spec_from_file_location("quest2_preflight", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


q = load_tool()


def tree_fixture():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("../../MUST-NOT-EXTRACT")
        member.size = 11
        archive.addfile(member, io.BytesIO(b"test-tree\x00\x00"))
    return data.getvalue()


class FakeAdb:
    """Only the external process is replaced; validation and files are real."""
    def __init__(self, overrides=None):
        self.calls = []
        self.responses = {
            ("shell", "getprop", "ro.product.device"): b"hollywood\n",
            ("shell", "getprop", "ro.build.version.incremental"): b"52242990024200150\n",
            ("shell", "uname", "-r"): b"4.19.325-cip128-st12-g49638c7a8637\n",
            ("shell", "getprop", "ro.boot.slot_suffix"): b"_a\n",
            ("shell", "getprop", "ro.boot.verifiedbootstate"): b"green\n",
            ("shell", "getprop", "ro.boot.vbmeta.device_state"): b"locked\n",
            ("shell", "getprop", "sys.boot_completed"): b"1\n",
            ("shell", "getprop", "ro.boot.bootreason"): b"kernel_panic,bug\n",
            ("shell", "su", "-c", "id"): b"uid=0(root) gid=0(root) context=u:r:magisk:s0\n",
            ("exec-out", "su -c 'tar -C /proc/device-tree -cf - .'"): tree_fixture(),
            ("exec-out", "su -c 'cat /proc/iomem'"): b"80000000-bfffffff : System RAM\n",
            ("exec-out", "su -c 'cat /proc/cmdline'"): b"console=ttyMSM0\n",
            ("exec-out", "su -c 'cat /proc/config.gz'"): gzip.compress(b"CONFIG_MODULES=y\n"),
        }
        self.responses.update(overrides or {})

    def __call__(self, args, **kwargs):
        if args[:3] != [ADB, "-s", SERIAL]:
            raise AssertionError("unscoped or wrong-device ADB request")
        self.calls.append(tuple(args[3:]))
        response = self.responses[tuple(args[3:])]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, int):
            return subprocess.CompletedProcess(args, response, b"", b"denied")
        return subprocess.CompletedProcess(args, 0, response, b"")


class TransportTests(unittest.TestCase):
    def require_tool(self):
        self.assertIsNotNone(q, "serial-locked transport has not been implemented")

    def test_every_probe_targets_exact_serial(self):
        self.require_tool()
        # Dropping -s on any binary or text request must fail this test.
        probes = ("device", "build", "kernel", "slot", "verified_boot",
                  "vbmeta_state", "boot_complete", "boot_reason", "root_uid",
                  "runtime_tree", "iomem", "cmdline", "config")
        for probe in probes:
            calls = []

            def runner(args, **kwargs):
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, b"result", b"")

            self.assertEqual(q.adb_read(ADB, SERIAL, probe, runner=runner), b"result")
            self.assertEqual(calls[0][0][:3], [ADB, "-s", SERIAL])
            self.assertIs(calls[0][1]["shell"], False)
            self.assertEqual(calls[0][1]["timeout"], 15)
            self.assertIs(calls[0][1]["capture_output"], True)
            self.assertNotIn("text", calls[0][1])

    def test_unknown_probe_never_executes(self):
        self.require_tool()

        def never(*args, **kwargs):
            self.fail("invalid input reached the process runner")

        with self.assertRaises(ValueError):
            q.adb_read(ADB, SERIAL, "reboot", runner=never)
        for serial in ("", "-target", "two devices", "bad\nserial", "bad\x00serial"):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                q.adb_read(ADB, serial, "device", runner=never)

    def test_adb_failure_is_not_success(self):
        self.require_tool()

        def denied(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, b"partial", b"offline")

        with self.assertRaises(q.CaptureError):
            q.adb_read(ADB, SERIAL, "device", runner=denied)

    def test_timeout_is_not_success(self):
        self.require_tool()

        def timeout(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 15, output=b"partial")

        with self.assertRaises(q.CaptureError):
            q.adb_read(ADB, SERIAL, "runtime_tree", runner=timeout)


class SnapshotTests(unittest.TestCase):
    def require_snapshot(self):
        self.assertTrue(hasattr(q, "capture_artifacts"), "snapshot capture is missing")

    def test_expected_identity_is_parsed(self):
        self.require_snapshot()
        self.assertEqual(q.collect_identity(ADB, SERIAL, runner=FakeAdb()), {
            "device": "hollywood", "build": "52242990024200150",
            "kernel": "4.19.325-cip128-st12-g49638c7a8637", "slot": "_a",
            "verified_boot": "green", "vbmeta_state": "locked",
            "boot_complete": "1", "boot_reason": "kernel_panic,bug", "root_uid": "0",
        })

    def test_root_denial_preserves_identity_failure(self):
        self.require_snapshot()
        fake = FakeAdb({("shell", "su", "-c", "id"): 1})
        with self.assertRaises(q.CaptureError) as caught:
            q.collect_identity(ADB, SERIAL, runner=fake)
        self.assertEqual(caught.exception.identity["device"], "hollywood")
        self.assertEqual(caught.exception.identity["root_uid"], "unavailable")
        self.assertFalse(any(call[0] == "exec-out" for call in fake.calls))

    def test_runtime_tree_is_never_extracted(self):
        self.require_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "capture"
            with patch.object(tarfile.TarFile, "extract", side_effect=AssertionError), \
                    patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError):
                artifacts = q.capture_artifacts(ADB, SERIAL, out, runner=FakeAdb())
            manifest = json.loads((out / "capture.json").read_text())
            self.assertEqual(manifest["capture_status"], "complete")
            self.assertEqual(artifacts["runtime_tree"]["sha256"],
                             hashlib.sha256(tree_fixture()).hexdigest())
            self.assertEqual((out / "runtime-tree.tar").read_bytes(), tree_fixture())
            self.assertFalse((Path(tmp) / "MUST-NOT-EXTRACT").exists())
            self.assertFalse((out / "INCOMPLETE.json").exists())

    def assert_incomplete(self, overrides):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "capture"
            with self.assertRaises(q.CaptureError):
                q.capture_artifacts(ADB, SERIAL, out, runner=FakeAdb(overrides))
            self.assertTrue((out / "INCOMPLETE.json").exists())
            self.assertFalse((out / "capture.json").exists())

    def test_truncated_tree_is_rejected(self):
        self.require_snapshot()
        for data in (tree_fixture()[:517], tree_fixture()[:1024], b"not a tar"):
            with self.subTest(length=len(data)):
                self.assert_incomplete({("exec-out", "su -c 'tar -C /proc/device-tree -cf - .'"): data})

    def test_corrupt_config_is_rejected(self):
        self.require_snapshot()
        invalid_deflate = bytearray(gzip.compress(b"config"))
        invalid_deflate[10] = 7  # Reserved DEFLATE block type, valid gzip header.
        for data in (b"not gzip", gzip.compress(b"config")[:-4],
                     bytes(invalid_deflate),
                     gzip.compress(b"x" * (4 * 1024 * 1024 + 1))):
            with self.subTest(length=len(data)):
                self.assert_incomplete({("exec-out", "su -c 'cat /proc/config.gz'"): data})

    def test_existing_output_is_untouched(self):
        self.require_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "capture.json").write_bytes(b"old-evidence")
            fake = FakeAdb()
            with self.assertRaises(q.CaptureError):
                q.capture_artifacts(ADB, SERIAL, out, runner=fake)
            self.assertEqual((out / "capture.json").read_bytes(), b"old-evidence")
            self.assertEqual(fake.calls, [])
            self.assertEqual([p.name for p in out.iterdir()], ["capture.json"])

    def test_identity_change_invalidates_snapshot(self):
        self.require_snapshot()
        self.assert_incomplete({("shell", "getprop", "ro.build.version.incremental"):
                                [b"52242990024200150\n", b"changed\n"]})

    def test_oversized_artifact_is_rejected(self):
        self.require_snapshot()
        self.assert_incomplete({("exec-out", "su -c 'cat /proc/cmdline'"):
                                b"x" * (64 * 1024 * 1024 + 1)})


class AssessmentTests(unittest.TestCase):
    def require_assessment(self):
        self.assertTrue(hasattr(q, "assess"), "readiness assessment is missing")

    def make_report(self, overrides=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "capture"
            artifacts = q.capture_artifacts(ADB, SERIAL, out, runner=FakeAdb(overrides))
            identity = json.loads((out / "capture.json").read_text())["identity"]
            return q.assess(identity, artifacts)

    def test_correct_identity_does_not_authorize_kexec(self):
        self.require_assessment()
        report = self.make_report()
        self.assertIs(report["identity_matches"], True)
        self.assertEqual(report["capture_status"], "complete")
        self.assertIs(report["handoff_ready"], False)
        self.assertIs(report["auto_boot_allowed"], False)
        for blocker in ("quest2_loader_unvalidated", "recovery_unvalidated",
                        "matching_kernel_build_crc_unproven", "boot_state_transport_unvalidated",
                        "vr_hardware_unvalidated"):
            self.assertIn(blocker, report["blockers"])

    def test_wrong_device_build_or_kernel_is_rejected(self):
        for command in (("shell", "getprop", "ro.product.device"),
                        ("shell", "getprop", "ro.build.version.incremental"),
                        ("shell", "uname", "-r")):
            with self.subTest(command=command):
                fake = FakeAdb({command: b"wrong\n"})
                with self.assertRaises(q.CaptureError):
                    q.collect_identity(ADB, SERIAL, runner=fake)
                self.assertFalse(any("su" in call or call[0] == "exec-out" for call in fake.calls))

    def test_redacted_iomem_blocks_memory_proof(self):
        self.require_assessment()
        report = self.make_report({("exec-out", "su -c 'cat /proc/iomem'"):
                                   b"00000000-00000000 : System RAM\n"})
        self.assertIn("memory_map_addresses_unproven", report["blockers"])
        self.assertIs(report["handoff_ready"], False)

    def test_panic_reason_remains_visible(self):
        self.require_assessment()
        report = self.make_report()
        self.assertEqual(report["identity"]["boot_reason"], "kernel_panic,bug")
        self.assertIn("startup_panic_unresolved", report["blockers"])

    def test_missing_root_is_a_blocker(self):
        self.require_assessment()
        try:
            q.collect_identity(ADB, SERIAL, runner=FakeAdb({("shell", "su", "-c", "id"): 1}))
        except q.CaptureError as exc:
            identity = exc.identity
        else:
            self.fail("root denial was accepted")
        report = q.assess(identity, {})
        self.assertEqual(report["capture_status"], "incomplete")
        self.assertIn("root_unavailable", report["blockers"])
        self.assertIs(report["handoff_ready"], False)


class CliTests(unittest.TestCase):
    def run_cli(self, out, fake):
        self.assertTrue(hasattr(q, "main"), "CLI orchestration is missing")
        console = io.StringIO()
        with patch.object(q.subprocess, "run", side_effect=fake), \
                redirect_stdout(console), redirect_stderr(console):
            result = q.main(["--adb", ADB, "--serial", SERIAL, "--out", str(out)])
        return result, console.getvalue()

    def test_cli_complete_capture_stays_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "capture"
            result, console = self.run_cli(out, FakeAdb())
            self.assertEqual(result, 3)
            self.assertIn("CAPTURE_COMPLETE; HANDOFF_BLOCKED", console)
            self.assertTrue((out / "capture.json").exists())
            report = json.loads((out / "report.json").read_text())
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["capture_status"], "complete")
            self.assertIs(report["handoff_ready"], False)
            self.assertIs(report["auto_boot_allowed"], False)

    def test_cli_partial_capture_never_publishes_complete_manifest(self):
        scenarios = [
            {("exec-out", "su -c 'cat /proc/config.gz'"): 1},
            {("shell", "su", "-c", "id"): 1},
            {("exec-out", "su -c 'cat /proc/iomem'"):
             subprocess.TimeoutExpired([ADB], 15)},
        ]
        for overrides in scenarios:
            with self.subTest(overrides=list(overrides)), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "capture"
                result, console = self.run_cli(out, FakeAdb(overrides))
                self.assertEqual(result, 2)
                self.assertNotIn("CAPTURE_COMPLETE", console)
                self.assertFalse((out / "capture.json").exists())
                self.assertTrue((out / "INCOMPLETE.json").exists())
                report = json.loads((out / "report.json").read_text())
                self.assertEqual(report["capture_status"], "incomplete")
                self.assertIs(report["auto_boot_allowed"], False)

    def test_cli_refuses_existing_output_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "report.json").write_bytes(b"original")
            fake = FakeAdb()
            result, _ = self.run_cli(out, fake)
            self.assertEqual(result, 2)
            self.assertEqual((out / "report.json").read_bytes(), b"original")
            self.assertEqual(fake.calls, [])

    def test_cli_malformed_iomem_never_completes(self):
        for invalid in (b"Permission denied\n", b"\n", b"8000-7000 : System RAM\n",
                        b"8000-9000 : System RAM\nerror reading remaining map\n"):
            with self.subTest(data=invalid), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "capture"
                fake = FakeAdb({("exec-out", "su -c 'cat /proc/iomem'"): invalid})
                result, console = self.run_cli(out, fake)
                self.assertEqual(result, 2)
                self.assertNotIn("CAPTURE_COMPLETE", console)
                self.assertEqual((out / "iomem.txt").read_bytes(), invalid)
                self.assertTrue((out / "INCOMPLETE.json").exists())
                self.assertFalse((out / "capture.json").exists())
                report = json.loads((out / "report.json").read_text())
                self.assertEqual(report["capture_status"], "incomplete")
                self.assertIs(report["handoff_ready"], False)


if __name__ == "__main__":
    unittest.main()
