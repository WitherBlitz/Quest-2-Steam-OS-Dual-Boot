"""Ownership evidence must survive races without becoming shutdown proof."""
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import quest2_preflight as preflight
import quest2_shutdown_probe as passive


def stat(pid=42, start=100, comm="camera service"):
    return f"{pid} ({comm}) S " + " ".join(["0"] * 18 + [str(start), "0"])


def sample(*, after=None, error=False, target="/dev/video0"):
    rows = ["QOWN2", "P\t42\t" + stat(), f"F\t42\t7\t{target}",
            "N\t42\t2"]
    if error: rows.append("E\t42\tlisting_failed")
    rows += ["A\t42\t" + (after or stat()), "END\t1\t-"]
    return ("\n".join(rows) + "\n").encode()


class OwnerProbeTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_owner_probe.py"
        self.assertTrue(path.is_file(), "ownership collector missing")
        spec = importlib.util.spec_from_file_location("owner_probe", path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod

    def test_holder_is_not_session_reference_or_shutdown_proof(self):
        mod = self.module(); report = mod.parse_scan(sample())
        self.assertEqual(report["holders"][0]["comm"], "camera service")
        self.assertEqual(report["holders"][0]["fds"], [{"fd": 7, "target": "/dev/video0"}])
        self.assertTrue(report["scan_coverage_complete"])
        self.assertFalse(report["unique_open_files_known"])
        self.assertFalse(report["session_or_reference_owners_proven"])
        report = mod.parse_scan(sample(target="/dev/subsys_a650_zap"))
        self.assertEqual(report["holders"][0]["fds"][0]["target"], "/dev/subsys_a650_zap")

    def test_reused_pid_or_changed_name_cannot_be_attributed(self):
        mod = self.module()
        for after in (stat(start=101), stat(comm="replacement"), "MISSING"):
            with self.subTest(after=after):
                report = mod.parse_scan(sample(after=after))
                self.assertEqual(report["holders"], [])
                self.assertFalse(report["scan_coverage_complete"])
                self.assertEqual(report["unattributed_matching_fds"], 1)

    def test_access_failure_and_exited_process_are_explicit_gaps(self):
        mod = self.module(); report = mod.parse_scan(sample(error=True))
        self.assertFalse(report["scan_coverage_complete"])
        self.assertEqual(report["holders"], [])
        report = mod.parse_scan(b"QOWN2\nS\t42\tstat_before\nEND\t1\t-\n")
        self.assertFalse(report["scan_coverage_complete"])
        self.assertEqual(report["processes_seen"], 1)

    def test_duplicate_shared_descriptors_are_not_counted_as_unique_refs(self):
        mod = self.module(); raw = sample().replace(b"N\t42\t2", b"F\t42\t8\t/dev/video0\nN\t42\t2")
        report = mod.parse_scan(raw)
        self.assertEqual(len(report["holders"][0]["fds"]), 2)
        self.assertFalse(report["unique_open_files_known"])

    def test_malformed_or_truncated_protocol_fails_closed(self):
        mod = self.module()
        bad = [sample()[:-8], sample().replace(b"END\t1\t-", b"END\t2\t-"),
               sample().replace(b"N\t42\t2", b"N\t42\t0"),
               sample().replace(b"F\t42\t7", b"F\t43\t7"),
               sample().replace(b"N\t42\t2", b"F\t42\t7\t/dev/video1\nN\t42\t2"),
               sample(target="/dev/video0 (deleted)"), sample(target="/data/private"),
               sample().replace(b"A\t42\t42", b"A\t42\t43"),
               b"QOWN2\nEND\t0\t-\ntrailing\n", b"\xff"]
        for raw in bad:
            with self.subTest(raw=raw[:45]), self.assertRaises(ValueError): mod.parse_scan(raw)

    def test_limits_and_unknown_nodes_are_rejected(self):
        mod = self.module()
        for target in ("/dev/video9999999", "/dev/subsys_unknown", "/dev/adsprpc-smd/extra"):
            with self.subTest(target=target), self.assertRaises(ValueError): mod.parse_scan(sample(target=target))
        with self.assertRaises(ValueError): mod.parse_scan(b"x" * (mod.MAX_SCAN + 1))
        excessive = f"N\t42\t{mod.MAX_PROCESS_FDS + 1}".encode()
        with self.assertRaises(ValueError): mod.parse_scan(sample().replace(b"N\t42\t2", excessive))
        rows = ["QOWN2"]
        for pid in range(1, 18):
            rows += [f"P\t{pid}\t" + stat(pid=pid), f"N\t{pid}\t8192", f"A\t{pid}\t" + stat(pid=pid)]
        rows.append("END\t17\t-")
        with self.assertRaises(ValueError): mod.parse_scan(("\n".join(rows) + "\n").encode())

    def test_real_shell_filters_listing_and_preserves_failed_listing_gap(self):
        mod = self.module()
        bash = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
        if not bash or not Path(bash).is_file(): self.skipTest("POSIX shell unavailable")
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                proc = Path(temp)/"proc/42"; (proc/"fd").mkdir(parents=True)
                (proc/"stat").write_text(stat() + "\n")
                rows = ["total 0", "lrwx------ 1 0 0 64 2026-10-22 01:00 7 -> /dev/video0",
                        "lrwx------ 1 0 0 64 2026-10-22 01:00 8 -> /dev/adsprpc-smd-secure",
                        "lrwx------ 1 0 0 64 2026-10-22 01:00 9 -> /data/private-irrelevant",
                        "lrwx------ 1 0 0 64 2026-10-22 01:00 13 -> /data/private 7 -> /dev/video0"]
                fake_ls = "ls() { printf '%s\\n' " + " ".join(shlex.quote(row) for row in rows)
                fake_ls += "; return " + ("1" if failure else "0") + "; }\n"
                script = mod.SCAN_SCRIPT.replace("/proc/[0-9]*", shlex.quote((Path(temp)/"proc").as_posix()) + "/[0-9]*")
                result = subprocess.run([bash, "-c", fake_ls + script], capture_output=True, timeout=10, shell=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(b"private-irrelevant", result.stdout)
                report = mod.parse_scan(result.stdout)
                self.assertEqual(report["fd_symlinks_seen"], 4)
                self.assertEqual(report["scan_coverage_complete"], not failure)
                if not failure: self.assertEqual(len(report["holders"][0]["fds"]), 2)

    def test_process_vanishing_after_glob_expansion_remains_a_coverage_gap(self):
        mod = self.module()
        bash = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
        if not bash or not Path(bash).is_file(): self.skipTest("POSIX shell unavailable")
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)/"proc"
            for pid in (42, 43):
                (proc/str(pid)/"fd").mkdir(parents=True)
                (proc/str(pid)/"stat").write_text(stat(pid=pid) + "\n")
            source = (proc/"43").resolve(); destination = (proc/"gone").resolve()
            self.assertTrue(source.is_relative_to(Path(temp).resolve()))
            self.assertTrue(destination.is_relative_to(Path(temp).resolve()))
            fake_ls = "ls() { if [ -d " + shlex.quote(source.as_posix()) + " ]; then mv "
            fake_ls += shlex.quote(source.as_posix()) + " " + shlex.quote(destination.as_posix()) + "; fi; }\n"
            script = mod.SCAN_SCRIPT.replace("/proc/[0-9]*", shlex.quote(proc.as_posix()) + "/[0-9]*")
            result = subprocess.run([bash, "-c", fake_ls + script], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=10, shell=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = mod.parse_scan(result.stdout)
            self.assertEqual(report["processes_seen"], 2)
            self.assertFalse(report["scan_coverage_complete"])
            self.assertEqual(report["gaps"], [{"pid": 43, "reason": "stat_before"}])

    def test_large_fd_listing_never_becomes_an_external_printf_argument(self):
        mod = self.module()
        bash = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
        if not bash or not Path(bash).is_file(): self.skipTest("POSIX shell unavailable")
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)/"proc/42"; (proc/"fd").mkdir(parents=True)
            (proc/"stat").write_text(stat() + "\n")
            prefix = r'''printf() {
    for arg do
        if [ "${#arg}" -gt 131072 ]; then
            builtin printf 'printf: Argument list too long\n' >&2
            return 126
        fi
    done
    builtin printf "$@"
}
ls() {
    i=0
    while [ "$i" -lt 3000 ]; do
        builtin printf 'lrwx------ 1 0 0 64 2026-10-22 01:00 %s -> /data/private-long-irrelevant-path\n' "$i"
        i=$((i+1))
    done
    builtin printf 'lrwx------ 1 0 0 64 2026-10-22 01:00 3001 -> /dev/video0\n'
}
'''
            script = mod.SCAN_SCRIPT.replace("/proc/[0-9]*", shlex.quote((Path(temp)/"proc").as_posix()) + "/[0-9]*")
            result = subprocess.run([bash, "-c", prefix + script], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=10, shell=False)
            self.assertEqual(result.returncode, 0, result.stdout[:200])
            report = mod.parse_scan(result.stdout)
            self.assertEqual(report["fd_symlinks_seen"], 3001)
            self.assertEqual(report["holders"][0]["fds"], [{"fd": 3001, "target": "/dev/video0"}])

    def runner(self, mod, *, changed_boot=False, bad_identity=False, resident=False, scan_failure=False):
        calls, counts = [], {}
        mapping = {tuple(value): key for key, value in preflight.PROBES.items()}
        mapping.update({("exec-out", passive.remote_command(key)): key for key in passive.PROBES})
        mapping[("exec-out", mod.remote_command())] = "owners"
        values = {**preflight.EXPECTED, "slot": "_a", "verified_boot": "green", "vbmeta_state": "locked",
                  "boot_complete": "1", "boot_reason": "powerkey", "root_uid": "uid=0(root)",
                  "boot": "12345678-1234-1234-1234-123456789abc\n", "cpus": "0-7\n", "taint": "5124\n",
                  "modules": "stock 1 0 - Live 0x0\n", "camera": "MISSING\n", "dsp": "MISSING\n"}
        if bad_identity: values["device"] = "wrong"
        if resident: values["modules"] += "camera_dsp_diag 1 0 - Live 0x0\n"

        def run(cmd, **kwargs):
            self.assertEqual(cmd[:3], ["adb-test", "-s", "USB-TEST"])
            self.assertFalse(kwargs["shell"])
            key = mapping[tuple(cmd[3:])]; calls.append(key); counts[key] = counts.get(key, 0) + 1
            if key == "owners": data = sample() + b"\nQOWN_STATUS=" + (b"1\n" if scan_failure else b"0\n")
            else:
                data = values[key].encode()
                if key == "boot" and changed_boot and counts[key] > 1: data = data.replace(b"abc", b"abd")
                if cmd[3] == "exec-out": data += b"\nQKX_PASSIVE_STATUS=0\n"
            return subprocess.CompletedProcess(cmd, 0, data, b"")
        return run, calls

    def test_complete_capture_checks_health_but_keeps_handoff_blocked(self):
        mod = self.module(); runner, calls = self.runner(mod)
        with tempfile.TemporaryDirectory() as temp:
            report = mod.collect("adb-test", "USB-TEST", Path(temp)/"new", runner=runner)
            self.assertTrue(report["same_boot_cpus_modules_taint"])
            self.assertFalse(report["handoff_ready"])
            self.assertFalse(report["device_mutation"])
            self.assertTrue((Path(temp)/"new/capture.json").is_file())
            self.assertNotIn("USB-TEST", (Path(temp)/"new/capture.json").read_text())
            self.assertFalse((Path(temp)/"new/INCOMPLETE.json").exists())
        self.assertEqual(calls.count("owners"), 1)

    def test_wrong_identity_or_resident_module_prevents_owner_scan(self):
        mod = self.module()
        for options in ({"bad_identity": True}, {"resident": True}):
            runner, calls = self.runner(mod, **options)
            with tempfile.TemporaryDirectory() as temp, self.assertRaises(preflight.CaptureError):
                mod.collect("adb-test", "USB-TEST", Path(temp)/"new", runner=runner)
            self.assertNotIn("owners", calls)
            if options.get("bad_identity"): self.assertNotIn("root_uid", calls)

    def test_changed_boot_or_scan_failure_leaves_incomplete_capture(self):
        mod = self.module()
        for options in ({"changed_boot": True}, {"scan_failure": True}):
            runner, _ = self.runner(mod, **options)
            with tempfile.TemporaryDirectory() as temp:
                out = Path(temp)/"new"
                with self.assertRaises(preflight.CaptureError): mod.collect("adb-test", "USB-TEST", out, runner=runner)
                self.assertTrue((out/"INCOMPLETE.json").is_file())
                self.assertFalse((out/"capture.json").exists())

    def test_existing_output_or_invalid_serial_has_no_device_operations(self):
        mod = self.module(); runner, calls = self.runner(mod)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(preflight.CaptureError): mod.collect("adb-test", "USB-TEST", temp, runner=runner)
            with self.assertRaises(ValueError): mod.collect("adb-test", "bad serial", Path(temp)/"new", runner=runner)
        self.assertEqual(calls, [])

    def test_scan_transport_cannot_hide_timeout_missing_footer_or_remote_failure(self):
        mod = self.module()
        for data in (sample(), sample() + b"\nQOWN_STATUS=1\n",
                     b"x" * (mod.MAX_SCAN + 129)):
            def run(cmd, **kwargs): return subprocess.CompletedProcess(cmd, 0, data, b"")
            with self.subTest(data=data[:25]), self.assertRaises(preflight.CaptureError):
                mod.read_scan("adb-test", "USB-TEST", runner=run)
        def timeout(cmd, **kwargs): raise subprocess.TimeoutExpired(cmd, 45)
        with self.assertRaises(preflight.CaptureError): mod.read_scan("adb-test", "USB-TEST", runner=timeout)

    def test_malformed_live_scan_retains_private_bytes_and_final_health_for_diagnosis(self):
        mod = self.module(); runner, calls = self.runner(mod)
        def malformed(cmd, **kwargs):
            result = runner(cmd, **kwargs)
            if cmd[3:] == ["exec-out", mod.remote_command()]:
                result.stdout = b"QOWN2\nbad-row\n\nQOWN_STATUS=0\n"
            return result
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"new"
            with self.assertRaises(preflight.CaptureError): mod.collect("adb-test", "USB-TEST", out, runner=malformed)
            self.assertEqual((out/"scan.txt").read_bytes(), b"QOWN2\nbad-row\n")
            self.assertTrue((out/"health.json").is_file())
            self.assertEqual(calls.count("boot"), 2)
            self.assertFalse((out/"capture.json").exists())

    def test_failed_remote_scan_retains_bounded_transport_for_root_cause(self):
        mod = self.module(); runner, _ = self.runner(mod, scan_failure=True)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/"new"
            with self.assertRaises(preflight.CaptureError): mod.collect("adb-test", "USB-TEST", out, runner=runner)
            self.assertEqual((out/"transport.txt").read_bytes(), sample() + b"\nQOWN_STATUS=1\n")
            self.assertFalse((out/"capture.json").exists())


if __name__ == "__main__": unittest.main()
