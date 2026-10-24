"""Offline trace protocol tests; no device or real kernel probe is used."""
import base64
import gzip
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "0123456789abcdef"
GROUP = "qkxown_0123456789abcdef"
sys.path.insert(0, str(ROOT / "tools"))
import quest2_preflight as preflight
import quest2_shutdown_probe as passive


def wire(*, target=True, control=True, missed=0, overrun=0, done="0\t0\tcomplete"):
    records = []
    if control:
        records.append(f"sh-321 [000] .... 1.000001: {GROUP}_c: (caller <- __arm64_sys_getpid) ret=321")
    if target:
        records.extend((f"vendor-654 [001] .... 1.000002: {GROUP}_d: (caller <- fastrpc_release_current_dsp_process) ret=-110",
                        f"vendor-654 [001] .... 1.000003: {GROUP}_f: (caller <- fastrpc_device_release) ret=0"))
    n = len(records)
    trace = f"# tracer: nop\n# entries-in-buffer/entries-written: {n}/{n}\n" + "\n".join(records) + "\n"
    profile = f" {GROUP}_c {int(control)} {missed}\n {GROUP}_d {int(target)} 0\n {GROUP}_f {int(target)} 0\n"

    def encoded(label, data):
        return label + "\t" + base64.b64encode(data.encode()).decode()

    rows = ["QRT1\t"+TOKEN, "CONTROL\t321", encoded("TRACE", trace), encoded("PROFILE", profile)]
    rows.extend(encoded("CPU"+str(cpu), f"entries: 0\noverrun: {overrun}\ncommit overrun: 0\ndropped events: 0\n") for cpu in range(8))
    return ("\n".join(rows) + "\nDONE\t"+done+"\n").encode()


class ReleaseTraceTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_release_trace.py"
        self.assertTrue(path.is_file(), "bounded return observer is missing")
        spec = importlib.util.spec_from_file_location("release_trace", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_signed_returns_and_positive_control_do_not_certify_shutdown(self):
        module = self.module()
        result = module.parse_capture(wire(), TOKEN)
        self.assertEqual(result["positive_control_returns"], 1)
        self.assertEqual(result["returns"], [{"event": "dsp_release", "tid": 654, "cpu": 1,
                                            "timestamp": "1.000002", "return": -110},
                                           {"event": "file_release", "tid": 654, "cpu": 1,
                                            "timestamp": "1.000003", "return": 0}])
        for key in ("complete_return_coverage_proven", "ownership_proven", "dma_quiescence_validated",
                    "recovery_validated", "handoff_ready"):
            self.assertIs(result[key], False)

    def test_no_target_events_is_a_coverage_gap_not_quiescence(self):
        result = self.module().parse_capture(wire(target=False), TOKEN)
        self.assertEqual(result["returns"], [])
        self.assertIn("no_target_release_observed", result["coverage_gaps"])
        self.assertFalse(result["ownership_proven"])

    def test_missing_positive_control_refuses(self):
        module = self.module()
        with self.assertRaises(ValueError): module.parse_capture(wire(control=False), TOKEN)

    def test_probe_misses_and_ring_losses_remain_explicit(self):
        result = self.module().parse_capture(wire(missed=2, overrun=1), TOKEN)
        self.assertIn("reported_probe_misses", result["coverage_gaps"])
        self.assertIn("ring_buffer_loss", result["coverage_gaps"])
        self.assertFalse(result["complete_return_coverage_proven"])

    def test_partial_registration_and_cleanup_errors_preserve_failure_phase(self):
        module = self.module()
        for status, cleanup, phase in ((2, 0, "register_d"), (0, 1, "complete")):
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, phase):
                module.parse_capture(wire(done=f"{status}\t{cleanup}\t{phase}"), TOKEN)

    def test_wrong_namespace_unknown_rows_and_unfinished_frames_refuse(self):
        module = self.module()
        data = wire()
        for bad in (data.replace(TOKEN.encode(), b"fedcba9876543210", 1),
                    data.replace(b"CONTROL\t321", b"CONTROL\t0"),
                    data.replace(b"CPU0\t", b"CPU8\t"),
                    data.replace(b"PROFILE\t", b"UNKNOWN\t"), data[:-1],
                    data.replace(b"\nDONE", b"\nEXTRA\t0\nDONE"), b"x"*131073):
            with self.subTest(bad=bad[:32]), self.assertRaises(ValueError):
                module.parse_capture(bad, TOKEN)

    def test_invalid_token_cannot_construct_device_paths_or_probe_commands(self):
        module = self.module()
        for token in ("", "x"*16, "../0123456789abc", "0123456789abcd;f", True):
            with self.subTest(token=token), self.assertRaises(ValueError): module.remote_command(token)

    def test_malformed_base64_oversized_sections_and_foreign_events_refuse(self):
        module = self.module()
        rows = wire().splitlines()
        for index, payload in ((2, b"not base64!"),
                               (2, base64.b64encode(b"x"*65537)),
                               (3, base64.b64encode(b"x"*8193)),
                               (4, base64.b64encode(b"x"*2049))):
            bad = list(rows); label = bad[index].split(b"\t", 1)[0]
            bad[index] = label+b"\t"+payload
            with self.subTest(index=index), self.assertRaises(ValueError):
                module.parse_capture(b"\n".join(bad)+b"\n", TOKEN)
        trace = base64.b64decode(rows[2].split(b"\t", 1)[1])
        for old, new in ((b"_d:", b"_other:"), (b"ret=-110", b"ret=2147483648")):
            bad = list(rows); bad[2] = b"TRACE\t"+base64.b64encode(trace.replace(old, new))
            with self.assertRaises(ValueError): module.parse_capture(b"\n".join(bad)+b"\n", TOKEN)

    def fake_device(self, module, *, fault=None):
        identities = {tuple(v): k for k, v in preflight.PROBES.items()}
        health = {("exec-out", passive.remote_command(k)): k for k in ("boot", "cpus", "taint", "modules")}
        values = {**preflight.EXPECTED, "slot": "_a", "verified_boot": "green", "vbmeta_state": "locked",
                  "boot_complete": "1", "boot_reason": "powerkey", "root_uid": "uid=0(root)",
                  "boot": "12345678-1234-1234-1234-123456789abc\n", "cpus": "0-7\n",
                  "taint": "5124\n", "modules": "stock 1 0 - Live 0x0\n"}
        calls, states = [], 0

        def runner(command, **kwargs):
            nonlocal states
            self.assertEqual(command[:3], ["adb-test", "-s", "USB-TEST"])
            args = tuple(command[3:])
            if args == ("exec-out", module.remote_command(TOKEN)):
                calls.append("trace")
                if fault == "timeout":
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial frame", stderr=b"interrupted")
                data = wire(done="0\t1\tcomplete" if fault == "cleanup" else "0\t0\tcomplete")
            elif args == ("exec-out", module.state_command()):
                calls.append("state"); states += 1
                data = b"74726163\nnop\n" + (b"1\n" if fault == "global" and states == 2 else b"0\n")
                data += b"\nQRT_STATE_STATUS=0\n"
            elif args in health:
                key = health[args]; calls.append(key)
                value = values[key]
                if key == "modules" and fault == "resident": value += "quest_kexec 1 0 - Live 0x0\n"
                data = value.encode()+b"\nQKX_PASSIVE_STATUS=0\n"
            else:
                key = identities[args]; calls.append(key)
                if key == "config":
                    config = "CONFIG_KPROBES=y\nCONFIG_KRETPROBES=y\nCONFIG_KPROBE_EVENTS=y\nCONFIG_NR_CPUS=8\n"
                    if fault == "config": config = config.replace("CONFIG_KRETPROBES=y", "# CONFIG_KRETPROBES is not set")
                    data = gzip.compress(config.encode())
                else:
                    value = "different" if key == "device" and fault == "identity" else values[key]
                    if key == "root_uid" and fault == "root": value = "uid=2000(shell)"
                    data = value.encode()
            return subprocess.CompletedProcess(command, 0, data, b"")

        return runner, calls

    def collect(self, module, out, runner):
        source = {"source_verified": True, "kernel_commit": "6bde5fc06b252c97761bc0963dd13c1abcf377ab",
                  "archive_sha256": "12b987d5672ffc09fe6d3ff7d7d926074305484fc896141824b5ece15d620bca"}
        return module.collect("adb-test", "USB-TEST", out, source, runner=runner, token=TOKEN)

    def test_collector_records_instrumentation_mutation_and_verified_restoration(self):
        module = self.module(); runner, calls = self.fake_device(module)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"capture"
            report = self.collect(module, out, runner)
            self.assertTrue(report["instrumentation_mutation"])
            self.assertTrue(report["global_tracing_unchanged"])
            self.assertFalse(report["driver_control_performed"])
            self.assertFalse(report["handoff_ready"])
            self.assertFalse((out/"INCOMPLETE.json").exists())
            self.assertTrue((out/"capture.json").is_file())
            self.assertEqual(calls.count("trace"), 1)

    def test_wrong_identity_missing_root_resident_module_or_config_prevents_trace(self):
        module = self.module()
        for fault in ("identity", "root", "resident", "config"):
            runner, calls = self.fake_device(module, fault=fault)
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertNotIn("trace", calls)
                self.assertTrue((out/"INCOMPLETE.json").exists())

    def test_cleanup_failure_or_global_state_change_never_completes(self):
        module = self.module()
        for fault in ("cleanup", "global"):
            runner, calls = self.fake_device(module, fault=fault)
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"capture"
                with self.assertRaises(preflight.CaptureError): self.collect(module, out, runner)
                self.assertTrue((out/"INCOMPLETE.json").exists())
                self.assertFalse((out/"capture.json").exists())
                self.assertEqual(calls.count("trace"), 1)

    def test_unknown_remote_completion_is_retained_and_never_retried(self):
        module = self.module(); runner, calls = self.fake_device(module, fault="timeout")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"capture"
            with self.assertRaisesRegex(preflight.CaptureError, "unknown"):
                self.collect(module, out, runner)
            marker = json.loads((out/"INCOMPLETE.json").read_text())
            self.assertIn("unknown", marker["error"])
            self.assertFalse(marker["handoff_ready"])
            self.assertEqual(calls.count("trace"), 1)
            self.assertEqual((out/"trace-timeout-stdout.bin").read_bytes(), b"partial frame")
            self.assertEqual((out/"trace-timeout-stderr.bin").read_bytes(), b"interrupted")

    def test_real_shell_failed_instance_setup_cleans_only_owned_empty_instance(self):
        self.shell_failure(existing=False)

    def test_real_shell_existing_namespace_is_untouched(self):
        self.shell_failure(existing=True)

    def test_real_shell_signal_during_instance_acquisition_still_cleans(self):
        self.shell_failure(existing=False, signal="acquire")

    def test_real_shell_repeated_signal_cannot_abort_cleanup(self):
        self.shell_failure(existing=False, signal="cleanup")

    def shell_failure(self, *, existing, signal=None):
        module = self.module()
        bash = Path("C:/Program Files/Git/bin/bash.exe")
        if not bash.is_file():
            located = shutil.which("bash")
            if not located: self.skipTest("Bash unavailable")
            bash = Path(located)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"trace"
            (root/"instances").mkdir(parents=True)
            (root/"events").mkdir()
            (root/"current_tracer").write_text("nop\n")
            (root/"tracing_on").write_text("0\n")
            (root/"kprobe_events").write_text("unrelated probe preserved\n")
            owned = root/"instances"/GROUP
            if existing:
                owned.mkdir(); (owned/"keep").write_text("not ours")
            posix = "/"+root.drive[0].lower()+root.as_posix()[2:] if root.drive else root.as_posix()
            script = shlex.split(module.remote_command(TOKEN))[2].replace("/sys/kernel/tracing", posix)
            # Ordinary directories model tracefs metadata, never real kernel probes.
            script = "stat() { printf '74726163\\n'; }\n" + script
            if signal == "acquire":
                script = 'mkdir() { command mkdir "$@" && kill -TERM $$; }\n' + script
            elif signal == "cleanup":
                script = 'rmdir() { kill -TERM $$; command rmdir "$@"; }\n' + script
            result = subprocess.run([str(bash), "-c", script], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn(b"DONE\t1\t0\t"+(b"preflight" if existing else b"buffers"), result.stdout)
            self.assertEqual((root/"kprobe_events").read_text(), "unrelated probe preserved\n")
            self.assertEqual((root/"tracing_on").read_text(), "0\n")
            if existing:
                self.assertEqual((owned/"keep").read_text(), "not ours")
            else:
                self.assertFalse(owned.exists())


if __name__ == "__main__":
    unittest.main()
