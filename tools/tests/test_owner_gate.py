"""Run the production entry-accounting core with host lock/clock doubles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OwnerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ROOT / "kernel/owner_gate.c"

    def test_disabled_option_links_without_core_and_does_not_count(self):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        self.assertTrue(compiler, "set HOST_TEST_CC to a host C compiler")
        program = '''#include "owner_gate.h"
int main(void) {
    struct qkx_owner_token token = { .counted = true };
    struct qkx_owner_view view;
    if (qkx_owner_enter(QKX_OWNER_CAMERA, QKX_OWNER_SUBMIT, &token)) return 1;
    if (token.counted) return 2;
    qkx_owner_leave(&token);
    return qkx_owner_snapshot(QKX_OWNER_CAMERA, &view) == -EOPNOTSUPP ? 0 : 3;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "disabled.c"
            binary = Path(temp) / ("disabled.exe" if os.name == "nt" else "disabled")
            source.write_text(program)
            result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-DQKX_OWNER_HOST_TEST",
                "-I", str(ROOT / "kernel"), str(source), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(subprocess.run([str(binary)]).returncode, 0)

    def test_native_state_machine(self):
        self.assertTrue(self.source.is_file(), "driver entry-accounting implementation missing")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        self.assertTrue(compiler, "set HOST_TEST_CC to a host C compiler")
        production = self.source.read_text().split("/* QOG_HOST_BEGIN */", 1)[1].split("/* QOG_HOST_END */", 1)[0]
        harness = Path(__file__).with_name("owner_gate_harness.c").read_text()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "gate.c"
            binary = Path(temp) / ("gate.exe" if os.name == "nt" else "gate")
            source.write_text(harness.replace("/* PRODUCTION */", production))
            result = subprocess.run([compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-DQKX_OWNER_HOST_TEST",
                "-DCONFIG_QKX_OWNER_GATE", "-I", str(ROOT / "kernel"),
                "-I", str(ROOT / "tools/tests"), str(source), "-o", str(binary)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("OWNER_GATE_CASES=15 PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
