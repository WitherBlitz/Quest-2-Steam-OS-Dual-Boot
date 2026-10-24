"""Exact FastRPC source error semantics; no target execution or custody claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "fastrpc-lifetime-6bde5fc.c"
FIXTURE_SHA256 = "26219958db337e914e0fb0fa8a0f55a1a30a0f518fbe917cd28b3a1705911f89"
MANIFEST = ROOT / "patches/horizon-owner/fastrpc-lifetime-edits.json"


class FastRPCLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("set HOST_TEST_CC to a native host C compiler")
        fixture = FIXTURE.read_bytes()
        if hashlib.sha256(fixture).hexdigest() != FIXTURE_SHA256:
            raise RuntimeError("pinned FastRPC fixture differs")
        manifest = json.loads(MANIFEST.read_bytes())
        cls.source = fixture.decode("latin1")
        for entry in manifest["files"]:
            if entry["path"] != "drivers/char/adsprpc.c":
                raise RuntimeError("unexpected FastRPC source")
            for edit in entry["edits"]:
                if not edit["before"] or cls.source.count(edit["before"]) != 1:
                    raise RuntimeError("unique exact source edit required: " + edit["name"])
                cls.source = cls.source.replace(edit["before"], edit["after"])
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-fastrpc-lifetime-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binaries = {}
        harness = Path(__file__).with_name("fastrpc_lifetime_harness.c").read_text()
        for enabled in (False, True):
            source = Path(cls.temp.name) / ("enabled.c" if enabled else "disabled.c")
            source.write_text(harness.replace("/* SOURCE */", cls.source), encoding="utf-8")
            binary = source.with_suffix(".exe" if os.name == "nt" else "")
            linker = ["-fuse-ld=lld"] if os.name == "nt" else []
            config = ["-DCONFIG_QKX_OWNER_GATE=1"] if enabled else []
            # The original driver has intentionally unused compatibility args.
            result = subprocess.run([compiler, *linker, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                                     "-Wno-unused-parameter",
                                     *config, str(source), "-o", str(binary)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.binaries[enabled] = binary

    def case(self, name):
        for enabled, binary in self.binaries.items():
            with self.subTest(owner_gate=enabled):
                result = subprocess.run([str(binary), name], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_heap_rpc_failure_propagates(self):
        self.case("heap_error")

    def test_heap_rpc_failure_restores_caller_locks(self):
        self.case("heap_error_locked")

    def test_successful_heap_release_stays_successful(self):
        self.case("heap_success")

    def test_remote_heap_secure_return_failure_is_preserved(self):
        self.case("remote_heap_error")

    def test_successful_compensation_preserves_original_assignment_error(self):
        self.case("map_assign_rollback_ok")

    def test_failed_compensation_preserves_original_error_and_logs_both(self):
        self.case("map_assign_rollback_error")

    def test_initial_map_failure_does_not_assign_or_compensate(self):
        self.case("map_send_error")

    def test_successful_map_returns_address(self):
        self.case("map_success")


if __name__ == "__main__":
    unittest.main()
