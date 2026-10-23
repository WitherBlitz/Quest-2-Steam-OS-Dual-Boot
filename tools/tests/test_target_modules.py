"""Exercise offline target module packaging against real filesystem/ELF fixtures."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/ci/quest2_target_modules.py"
RELEASE = "4.19.325-cip128-st12-qkx-q2-usb"
VERMAGIC = RELEASE + " SMP preempt mod_unload modversions aarch64"
REQUIRED = (
    "kernel/drivers/staging/qcacld-3.0/wlan.ko",
    "kernel/drivers/staging/oculus/powerstate_mgr.ko",
    "kernel/techpack/audio/soc/pinctrl_lpi_dlkm.ko",
    "kernel/techpack/audio/soc/snd_event_dlkm.ko",
    "kernel/techpack/audio/ipc/apr_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_dlkm.ko",
    "kernel/techpack/audio/dsp/adsp_loader_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_pdr_dlkm.ko",
    "kernel/techpack/audio/dsp/q6_notifier_dlkm.ko",
    "kernel/techpack/audio/asoc/platform_dlkm.ko",
    "kernel/techpack/audio/asoc/machine_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/audio_ext_clk_up_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/stub_dlkm.ko",
    "kernel/techpack/audio/asoc/codecs/hdmi_dlkm.ko",
)
CONFIG_Y = ("ARCH_KONA", "SMP", "PREEMPT", "MODULES", "MODULE_UNLOAD", "MODVERSIONS")
CONFIG_M = ("QCA_CLD_WLAN", "POWERSTATE_MANAGER")
INPUTS = (".config", "arch/arm64/boot/Image", "Module.symvers",
          "include/config/kernel.release", "modules.order", "modules.builtin")
METADATA = ("modules.dep", "modules.alias", "modules.symbols", "modules.order", "modules.builtin")

spec = importlib.util.spec_from_file_location("target_module_elf_fixture", Path(__file__).with_name("test_ci_audit.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class TargetModulesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "offline target module bundler is not implemented")
        spec = importlib.util.spec_from_file_location("quest2_target_modules", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        temp_root = Path("D:/quest2-shutdown-test-temp") if os.name == "nt" else None
        if temp_root is not None:
            temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qkx-target-modules-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "kernel-out"
        self.stage = self.root / "staging"
        self.installed = self.stage / "lib/modules" / RELEASE
        self.installed.mkdir(parents=True)
        self.write_input(".config", "".join(f"CONFIG_{name}=y\n" for name in CONFIG_Y) +
                         "".join(f"CONFIG_{name}=m\n" for name in CONFIG_M))
        image = bytearray(128)
        struct.pack_into("<QQQ", image, 8, 0x80000, 128, 10)
        image[56:60] = b"ARM\x64"
        self.write_input("arch/arm64/boot/Image", bytes(image))
        self.write_input("include/config/kernel.release", RELEASE + "\n")
        self.write_input("Module.symvers", "0x11223344\tapi\tvmlinux\tEXPORT_SYMBOL\n"
                         "0x74b4ebeb\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        self.order = "".join(name + "\n" for name in REQUIRED)
        self.write_input("modules.order", self.order)
        self.write_input("modules.builtin", "kernel/drivers/input/keyboard/gpio_keys.ko\n")
        for name in ("modules.order", "modules.builtin"):
            self.write_stage(name, (self.out / name).read_bytes())
        self.dep = "".join(name + ":" + (" " + REQUIRED[1] if name == REQUIRED[0] else "") + "\n"
                           for name in REQUIRED)
        self.write_stage("modules.dep", self.dep)
        self.write_stage("modules.alias", "# Aliases extracted from modules themselves.\n")
        self.write_stage("modules.symbols", "# Aliases for symbols, used by symbol_request().\n")
        for name in REQUIRED:
            self.write_stage(name, fixtures.elf_fixture(vermagic=VERMAGIC))

    def write_input(self, name, data):
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    def write_stage(self, name, data):
        path = self.installed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    def bundle(self):
        return self.tool.bundle(self.out, self.stage)

    def cli(self, archive, report):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--kernel-out", str(self.out),
                               "--staging", str(self.stage), "--archive", str(archive),
                               "--output", str(report)], capture_output=True, text=True)

    def test_valid_bundle_is_deterministic_and_binds_all_inputs(self):
        archive, report = self.bundle()
        self.assertEqual(report["status"], "OFFLINE_MODULES_VERIFIED_HANDOFF_BLOCKED")
        self.assertIs(report["debug_sections_absent"], True)
        self.assertEqual(report["module_count"], 14)
        self.assertEqual(report["archive_bytes"], len(archive))
        self.assertEqual(report["archive_sha256"], hashlib.sha256(archive).hexdigest())
        for key in ("hardware_validated", "handoff_ready", "deployment_performed", "autoload_performed", "device_access"):
            self.assertIs(report[key], False, key)
        for name in INPUTS:
            data = (self.out / name).read_bytes()
            self.assertEqual(report["inputs"][name]["bytes"], len(data))
            self.assertEqual(report["inputs"][name]["sha256"], hashlib.sha256(data).hexdigest())
        prefix = "lib/modules/" + RELEASE + "/"
        expected = {prefix + name for name in (*REQUIRED, *METADATA)}
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as package:
            members = package.getmembers()
            files = {member.name: member for member in members if member.isfile()}
            self.assertEqual(set(files), expected)
            self.assertEqual(len(files), len([member for member in members if member.isfile()]))
            self.assertEqual(set(report["files"]), expected)
            for member in members:
                self.assertFalse(member.issym() or member.islnk())
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
            for name, member in files.items():
                data = package.extractfile(member).read()
                self.assertEqual(data, (self.stage / name).read_bytes())
                self.assertEqual(report["files"][name]["bytes"], len(data))
                self.assertEqual(report["files"][name]["sha256"], hashlib.sha256(data).hexdigest())
        rows = {row["path"]: row for row in report["modules"]}
        self.assertEqual(set(rows), set(REQUIRED))
        for name, row in rows.items():
            data = (self.installed / name).read_bytes()
            self.assertEqual(row["bytes"], len(data))
            self.assertEqual(row["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(row["vermagic"], VERMAGIC)
            self.assertEqual(row["undefined_symbols"], ["api"])
            self.assertEqual(row["crc_rows"], {"api": "0x11223344", "module_layout": "0x74b4ebeb"})
        for path in self.stage.rglob("*"):
            os.utime(path, (1234567890, 1234567890))
        self.assertEqual(self.bundle(), (archive, report))

    def test_missing_required_module_is_not_hidden_by_consistent_order(self):
        for name in REQUIRED:
            with self.subTest(module=name):
                path = self.installed / name
                data = path.read_bytes()
                path.unlink()
                shortened = self.order.replace(name + "\n", "")
                self.write_input("modules.order", shortened)
                self.write_stage("modules.order", shortened)
                self.write_stage("modules.dep", "".join(item + ":\n" for item in REQUIRED if item != name))
                with self.assertRaises(ValueError):
                    self.bundle()
                path.write_bytes(data)
                self.write_input("modules.order", self.order)
                self.write_stage("modules.order", self.order)
                self.write_stage("modules.dep", self.dep)

    def test_missing_or_extra_staged_module_is_rejected(self):
        path = self.installed / REQUIRED[0]
        data = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            self.bundle()
        path.write_bytes(data)
        self.write_stage("kernel/drivers/unexpected.ko", data)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_module_elf_identity_and_version_coverage_are_enforced(self):
        for blob in (fixtures.elf_fixture(machine=62, vermagic=VERMAGIC),
                     fixtures.elf_fixture(vermagic=fixtures.VM),
                     fixtures.elf_fixture(vermagic=VERMAGIC, api_crc=0x11223345),
                     fixtures.elf_fixture(vermagic=VERMAGIC, symbol="unversioned"),
                     fixtures.elf_fixture(vermagic=VERMAGIC)[:-32]):
            with self.subTest(blob_sha=hashlib.sha256(blob).hexdigest()):
                self.write_stage(REQUIRED[0], blob)
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_module_symvers_is_the_actual_generated_crc_authority(self):
        self.write_input("Module.symvers", "0x11223345\tapi\tvmlinux\tEXPORT_SYMBOL\n"
                         "0x74b4ebeb\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        with self.assertRaises(ValueError):
            self.bundle()

    def test_debug_bearing_module_is_rejected_without_relaxing_abi_checks(self):
        blob = fixtures.elf_fixture(vermagic=VERMAGIC, debug=True)
        abi = fixtures.audit.inspect_module(blob, fixtures.CRC, fixtures.CRC,
                                            expected_vermagic=VERMAGIC)
        self.assertEqual(abi["undefined_symbols"], ["api"])
        self.assertEqual(abi["vermagic"], VERMAGIC)
        self.write_stage(REQUIRED[0], blob)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_missing_build_input_is_rejected(self):
        for name in INPUTS:
            with self.subTest(input=name):
                path = self.out / name
                data = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises(ValueError):
                        self.bundle()
                finally:
                    path.write_bytes(data)

    def test_required_kernel_configuration_is_enforced(self):
        original = (self.out / ".config").read_text()
        for name, value in [(name, "y") for name in CONFIG_Y] + [(name, "m") for name in CONFIG_M]:
            with self.subTest(config=name):
                self.write_input(".config", original.replace(f"CONFIG_{name}={value}\n", f"# CONFIG_{name} is not set\n"))
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_wrong_kernel_release_is_rejected(self):
        self.write_input("include/config/kernel.release", "4.19.325-cip128-st12-qkx-q2-baseline\n")
        with self.assertRaises(ValueError):
            self.bundle()

    def test_wrong_installed_release_is_rejected(self):
        self.installed.rename(self.installed.with_name(RELEASE + "-wrong"))
        with self.assertRaises(ValueError):
            self.bundle()

    def test_installed_order_and_builtin_must_match_kernel_output(self):
        for name in ("modules.order", "modules.builtin"):
            with self.subTest(metadata=name):
                original = (self.installed / name).read_bytes()
                self.write_stage(name, original + b"kernel/drivers/additional.ko\n")
                with self.assertRaises(ValueError):
                    self.bundle()
                self.write_stage(name, original)

    def test_missing_required_depmod_metadata_is_rejected(self):
        for name in METADATA:
            with self.subTest(metadata=name):
                path = self.installed / name
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    self.bundle()
                path.write_bytes(data)

    def test_dependency_rows_cover_each_module_once_and_only_known_dependencies(self):
        lines = self.dep.splitlines(keepends=True)
        for dep in ("".join(lines[1:]), self.dep + lines[0],
                    self.dep.replace(REQUIRED[0] + ": " + REQUIRED[1], REQUIRED[0] + ": kernel/drivers/missing.ko"),
                    self.dep + "kernel/drivers/unexpected.ko:\n"):
            with self.subTest(dep=dep):
                self.write_stage("modules.dep", dep)
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_order_rejects_unsafe_and_duplicate_module_paths(self):
        for name in ("../escape.ko", "/absolute.ko", "C:/absolute.ko", "kernel\\drivers\\escape.ko",
                     "kernel/drivers/../escape.ko", "kernel//drivers/escape.ko", REQUIRED[0]):
            with self.subTest(path=name):
                value = self.order + name + "\n"
                self.write_input("modules.order", value)
                self.write_stage("modules.order", value)
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_dependency_paths_cannot_escape_the_install_tree(self):
        for name in ("../escape.ko", "/absolute.ko", "C:/absolute.ko", "kernel\\escape.ko"):
            with self.subTest(path=name):
                self.write_stage("modules.dep", self.dep.replace(REQUIRED[0] + ": " + REQUIRED[1], REQUIRED[0] + ": " + name))
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_cyclic_module_dependencies_are_rejected(self):
        self.write_stage("modules.dep", self.dep.replace(REQUIRED[1] + ":\n",
                                                        REQUIRED[1] + ": " + REQUIRED[0] + "\n"))
        with self.assertRaises(ValueError):
            self.bundle()

    def test_import_provider_must_be_in_dependency_closure(self):
        self.write_input("Module.symvers", "0x11223344\tapi\tdrivers/staging/oculus/powerstate_mgr\tEXPORT_SYMBOL\n"
                         "0x74b4ebeb\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        self.write_stage(REQUIRED[1], fixtures.elf_fixture(vermagic=VERMAGIC, symbol="module_layout"))
        with self.assertRaises(ValueError):
            self.bundle()
        dependencies = "".join(name + ":" + (" " + REQUIRED[1] if name != REQUIRED[1] else "") + "\n"
                               for name in REQUIRED)
        self.write_stage("modules.dep", dependencies)
        _, report = self.bundle()
        self.assertEqual(report["module_count"], 14)
        # A transitive provider is sufficient; a direct dependency is not required.
        dependencies = dependencies.replace(REQUIRED[0] + ": " + REQUIRED[1],
                                            REQUIRED[0] + ": " + REQUIRED[2])
        self.write_stage("modules.dep", dependencies)
        self.assertEqual(self.bundle()[1]["module_count"], 14)

    def test_unbundled_import_provider_is_rejected(self):
        self.write_input("Module.symvers", "0x11223344\tapi\tdrivers/missing_provider\tEXPORT_SYMBOL\n"
                         "0x74b4ebeb\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        with self.assertRaises(ValueError):
            self.bundle()

    def test_depmod_metadata_must_be_text(self):
        for blob in (b"\xff\xfe", b"alias\x00unexpected\n"):
            with self.subTest(blob=blob):
                self.write_stage("modules.alias", blob)
                with self.assertRaises(ValueError):
                    self.bundle()

    def test_unrecognized_metadata_is_not_silently_archived(self):
        self.write_stage("modules.unreviewed", "unexpected\n")
        with self.assertRaises(ValueError):
            self.bundle()

    def test_module_symlink_payload_is_rejected(self):
        path = self.installed / REQUIRED[0]
        outside = self.root / "outside.ko"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_metadata_symlink_payload_is_rejected(self):
        path = self.installed / "modules.alias"
        outside = self.root / "outside.alias"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_installer_build_and_source_symlinks_are_excluded(self):
        baseline = self.bundle()
        for name in ("build", "source"):
            (self.installed / name).symlink_to(self.out, target_is_directory=True)
        self.assertEqual(self.bundle(), baseline)

    def test_nested_source_symlink_is_not_an_installer_exception(self):
        (self.installed / "kernel/source").symlink_to(self.out, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_build_exception_does_not_hide_regular_payload(self):
        self.write_stage("build", "unexpected payload\n")
        with self.assertRaises(ValueError):
            self.bundle()

    def test_module_directory_symlink_is_rejected(self):
        directory = (self.installed / REQUIRED[0]).parent
        outside = self.root / "outside-modules"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.bundle()

    def test_cli_writes_reviewable_archive_and_report(self):
        archive, report = self.root / "modules.tar.gz", self.root / "modules.json"
        result = self.cli(archive, report)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(report.read_text())
        self.assertEqual(data["status"], "OFFLINE_MODULES_VERIFIED_HANDOFF_BLOCKED")
        self.assertEqual(data["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_cli_refuses_either_existing_output_before_writing(self):
        for existing in ("archive", "report"):
            with self.subTest(existing=existing):
                archive = self.root / (existing + ".tar.gz")
                report = self.root / (existing + ".json")
                chosen, absent = (archive, report) if existing == "archive" else (report, archive)
                chosen.write_bytes(b"keep existing bytes")
                result = self.cli(archive, report)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(chosen.read_bytes(), b"keep existing bytes")
                self.assertFalse(absent.exists())

    def test_cli_failed_validation_creates_neither_output(self):
        archive, report = self.root / "bad.tar.gz", self.root / "bad.json"
        self.write_stage(REQUIRED[0], b"not an ELF module")
        result = self.cli(archive, report)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(archive.exists())
        self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
