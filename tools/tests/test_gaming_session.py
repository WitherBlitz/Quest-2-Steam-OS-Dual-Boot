"""Run the session lifecycle with external compositor/probe fixtures, not a GPU."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
import runpy

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
timeout() { [ "$1" != --signal=KILL ] || shift; shift; "$@"; }
'''
        harness.write_text(source.rsplit(marker, 1)[0] + "\nFIXTURE_ROOT=" + root +
                           '\nFIXTURE_ROOT=$(cd "$FIXTURE_ROOT" && pwd)\nexport FIXTURE_ROOT\n' +
                           fixtures + body + '\nqkx_main "$@"\n')
        return subprocess.run([self.shell, str(harness), *arguments],
            env={**os.environ, "QKX_STARTUP_SECONDS": "2"}, capture_output=True,
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

    def check_descendant(self, signal_parent):
        action = 'kill -TERM "$$"; wait' if signal_parent else 'return 23'
        result = self.run_session('client() { sleep 30 >/dev/null 2>&1 & '
            'printf "%s" "$!" > "$FIXTURE_ROOT/descendant"; ' + action + '; }')
        pid = (self.root / "descendant").read_text()
        try:
            self.assertEqual(result.returncode, 143 if signal_parent else 23)
            alive = subprocess.run([self.shell, "-c", 'kill -0 "$1" 2>/dev/null', "check", pid])
            self.assertNotEqual(alive.returncode, 0, "owned client descendant survived cleanup")
        finally:
            subprocess.run([self.shell, "-c", 'kill -KILL "$1" 2>/dev/null || :', "cleanup", pid])

    def test_wrapper_descendants_are_stopped_after_normal_exit(self):
        self.check_descendant(False)

    def test_wrapper_descendants_are_stopped_on_termination(self):
        self.check_descendant(True)

    def test_real_timeout_forces_an_unresponsive_readiness_probe_to_stop(self):
        binary = self.root / "bin"
        binary.mkdir()
        probe = binary / "wayland-info"
        probe.write_text('#!/usr/bin/env bash\ntrap "" TERM\nsleep 4\nexit 1\n')
        probe.chmod(0o755)
        started = time.monotonic()
        result = self.run_session('unset -f timeout wayland-info\nPATH="$FIXTURE_ROOT/bin:$PATH"')
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertLess(time.monotonic() - started, 3, "readiness probe exceeded its hard bound")
        self.assertFalse((self.root / "client").exists())
        self.assert_cleaned()


class GamingFrameEvidenceTests(unittest.TestCase):
    def setUp(self):
        functions = runpy.run_path(str(ROOT / "tools/userspace/smoke.py"))
        self.assertTrue("frame_count" in functions, "frame evidence must identify frame callbacks")
        self.count = functions["frame_count"]

    def test_display_sync_callbacks_cannot_be_counted_as_frames(self):
        trace = '\n'.join(f'wl_display@1.sync(new id wl_callback@{i})\n'
            f'wl_callback@{i}.done(10)\nwl_surface@8.commit()' for i in (2, 3, 4))
        self.assertEqual(self.count(trace), 0)

    def test_frame_completion_consumes_id_and_allows_later_id_reuse(self):
        trace = ('wl_surface@8.frame(new id wl_callback@4)\nwl_callback@4.done(10)\n'
                 'wl_callback@4.done(11)\nwl_surface@8.frame(new id wl_callback@4)\n'
                 'wl_callback@4.done(12)')
        self.assertEqual(self.count(trace), 2)

    def test_completion_before_a_frame_request_is_not_retroactive(self):
        self.assertEqual(self.count('wl_callback@4.done(1)\nwl_surface@8.frame(new id wl_callback@4)'), 0)

    def test_sync_reusing_a_completed_frame_id_is_ignored(self):
        trace = ('wl_surface@8.frame(new id wl_callback@4)\nwl_callback@4.done(10)\n'
                 'wl_display@1.sync(new id wl_callback@4)\nwl_callback@4.done(11)')
        self.assertEqual(self.count(trace), 1)


if __name__ == "__main__": unittest.main()
