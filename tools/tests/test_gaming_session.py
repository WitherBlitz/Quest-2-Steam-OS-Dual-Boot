"""Run the session lifecycle with external compositor/probe fixtures, not a GPU."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/userspace/qkx-session"


class GamingSessionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "gaming session launcher is missing")
        self.temp = tempfile.TemporaryDirectory(prefix="qkx-session-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        self.shell = str(candidate) if os.name == "nt" else shutil.which("bash")
        self.assertTrue(self.shell, "Bash required for session tests")

    def run_session(self, body, arguments=("client",)):
        source = SCRIPT.read_text()
        marker = '\nqkx_main "$@"'
        self.assertEqual(source.count(marker), 1)
        harness = self.root / "session.sh"
        root = shlex.quote(str(self.root))
        fixtures = '''
weston() {
    printf '%s\\n' "$XDG_RUNTIME_DIR" > "$FIXTURE_ROOT/runtime"
    trap 'printf stopped > "$FIXTURE_ROOT/stopped"; exit 0' TERM
    printf started > "$FIXTURE_ROOT/started"
    while :; do sleep 0.02; done
}
wayland-info() { test -f "$FIXTURE_ROOT/started"; }
client() { printf '<%s>\\n' "$@"; printf client > "$FIXTURE_ROOT/client"; }
timeout() { shift; "$@"; }
'''
        harness.write_text(source.rsplit(marker, 1)[0] + "\nFIXTURE_ROOT=" + root +
                           "\n" + fixtures + body + '\nqkx_main "$@"\n')
        return subprocess.run([self.shell, str(harness), *arguments],
            env={**os.environ, "QKX_STARTUP_SECONDS": "1"}, capture_output=True,
            text=True, timeout=12)

    def assert_cleaned(self):
        self.assertEqual((self.root / "stopped").read_text(), "stopped")
        runtime = (self.root / "runtime").read_text().strip()
        result = subprocess.run([self.shell, "-c", 'test ! -e "$1"', "check", runtime], timeout=5)
        self.assertEqual(result.returncode, 0, "private runtime directory leaked")

    def test_empty_command_refused_without_starting_compositor(self):
        result = self.run_session("", ())
        self.assertEqual(result.returncode, 64)
        self.assertFalse((self.root / "started").exists())

    def test_arguments_remain_literal_and_owned_compositor_is_cleaned(self):
        result = self.run_session("", ("client", "two words", "$(touch injected);'"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["<two words>", "<$(touch injected);'>"])
        self.assert_cleaned()

    def test_client_failure_status_survives_cleanup(self):
        result = self.run_session("client() { return 23; }")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assert_cleaned()

    def test_dead_compositor_never_launches_client(self):
        result = self.run_session("weston() { return 9; }\nwayland-info() { return 1; }")
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertFalse((self.root / "client").exists())

    def test_readiness_timeout_never_launches_client_and_cleans_compositor(self):
        result = self.run_session("wayland-info() { return 1; }")
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertFalse((self.root / "client").exists())
        self.assert_cleaned()

    def test_termination_cleans_compositor_and_returns_signal_status(self):
        result = self.run_session('client() { kill -TERM "$$"; sleep 0.1; }')
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assert_cleaned()

    def test_invalid_deadline_refused_before_start(self):
        result = self.run_session("QKX_STARTUP_SECONDS=bad")
        self.assertEqual(result.returncode, 64)
        self.assertFalse((self.root / "started").exists())


if __name__ == "__main__": unittest.main()
