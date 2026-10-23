"""Execute native USB rescue setup with configfs/sysfs fixtures, not hardware."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "initramfs/quest2-linux-init.sh"


class NativeInitTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "native Linux rescue init is missing")
        self.temp = tempfile.TemporaryDirectory(prefix="qkx-native-init-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.sys = self.root / "sys"
        self.udcs = self.sys / "class/udc"
        self.udcs.mkdir(parents=True)
        self.gadget = self.config / "usb_gadget/quest2"
        shell = os.environ.get("HOST_TEST_SH")
        if not shell and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if candidate.is_file(): shell = str(candidate)
        self.shell = shell or shutil.which("bash")
        self.assertTrue(self.shell, "POSIX shell required for native init tests")

    def run_functions(self, body):
        text = SCRIPT.read_text()
        marker = '\nqkx_main "$@"'
        self.assertEqual(text.count(marker), 1, "init entrypoint must be unambiguous")
        functions = text.rsplit(marker, 1)[0]
        harness = self.root / "harness.sh"
        harness.write_text(functions + "\nsleep() { printf '%s\\n' \"$1\" >> " +
                           shlex.quote(str(self.root / "sleeps")) + "; }\n" + body + "\n")
        env = os.environ.copy()
        if os.name == "nt": env["MSYS"] = "winsymlinks:nativestrict"
        return subprocess.run([self.shell, str(harness)], capture_output=True,
                              text=True, timeout=10, env=env)

    def setup_usb(self, mode="ecm"):
        return self.run_functions("qkx_setup_usb " + " ".join(shlex.quote(str(p))
                                  for p in (self.config, self.sys, mode)))

    def test_ecm_configures_only_available_controller(self):
        (self.udcs / "a600000.dwc3").mkdir()
        result = self.setup_usb()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.gadget / "UDC").read_text().strip(), "a600000.dwc3")
        self.assertEqual((self.gadget / "strings/0x409/product").read_text().strip(),
                         "Quest 2 Linux rescue")
        self.assertEqual((self.gadget / "functions/ecm.usb0/dev_addr").read_text().strip(),
                         "02:51:4b:58:00:02")
        self.assertTrue((self.gadget / "configs/c.1/ecm.usb0").is_symlink())

    def test_rndis_publishes_windows_compatible_os_descriptors(self):
        (self.udcs / "a600000.dwc3").mkdir()
        result = self.setup_usb("rndis")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.gadget / "os_desc/use").read_text().strip(), "1")
        self.assertEqual((self.gadget / "os_desc/qw_sign").read_text().strip(), "MSFT100")
        self.assertEqual((self.gadget / "functions/rndis.usb0/os_desc/interface.rndis/compatible_id").read_text().strip(), "RNDIS")
        self.assertTrue((self.gadget / "os_desc/c.1").is_symlink())

    def test_missing_controller_has_finite_wait(self):
        result = self.setup_usb()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "sleeps").read_text().splitlines(), ["1"] * 10)
        self.assertFalse((self.gadget / "UDC").exists())

    def test_multiple_controllers_are_not_guessed(self):
        (self.udcs / "first").mkdir(); (self.udcs / "second").mkdir()
        result = self.setup_usb()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.gadget / "UDC").exists())

    def test_failed_descriptor_write_prevents_binding(self):
        (self.udcs / "a600000.dwc3").mkdir()
        (self.gadget / "idVendor").mkdir(parents=True)
        result = self.setup_usb()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.gadget / "UDC").exists())

    def test_unknown_mode_does_not_create_a_gadget(self):
        result = self.setup_usb("other")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.gadget.exists())

    def test_shell_is_bound_to_usb_ip_and_preserves_failure(self):
        result = self.run_functions("telnetd() { printf '%s\\n' \"$*\"; return 19; }\nqkx_shell_once")
        self.assertEqual(result.returncode, 19)
        self.assertIn("-b 10.42.0.2:23", result.stdout)
        self.assertNotIn("0.0.0.0", result.stdout)

    def test_build_profiles_select_their_actual_init(self):
        build = (ROOT / "tools/ci/quest2_target_build.sh").read_text()
        self.assertEqual(build.count('repo="$(pwd)"'), 1)
        selector = build.split('repo="$(pwd)"', 1)[0]
        harness = self.root / "selector.sh"
        harness.write_text(selector + '\nprintf "%s\\n" "$localversion" "$init_script"\n')
        expected = {"baseline": ("-qkx-q2-baseline", "quest2-baseline-init.sh"),
                    "startup-gic-pending": ("-qkx-q2-startup", "quest2-baseline-init.sh"),
                    "startup-usb-child": ("-qkx-q2-usb", "quest2-linux-init.sh")}
        for profile, (version, init) in expected.items():
            with self.subTest(profile=profile):
                result = subprocess.run([self.shell, str(harness)], capture_output=True, text=True,
                    env={**os.environ, "QKX_TARGET_PROFILE": profile}, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines(), [version, "initramfs/" + init])


if __name__ == "__main__": unittest.main()
