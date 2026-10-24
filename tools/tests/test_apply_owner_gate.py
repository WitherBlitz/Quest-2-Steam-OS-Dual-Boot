"""Offline owner-gate patching; no private archive or target access required."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SIGNATURES = {
    "submit": "static int submit(int value)",
    "observe": "static long observe(long value)",
    "complete": "static void complete(int value)",
    "sample": "static ssize_t sample(ssize_t value)",
}
BODIES = {
    "submit": '\n{\n\tconst char *s = "{ \\\" }"; /* } */\n\t(void)s;\n\tif (value) { return value + 7; }\n\treturn 7;\n}',
    "observe": "\n{\n\tchar c = '}'; /* { */\n\t(void)c;\n\treturn value - 3;\n}",
    "complete": " { total += value; }",
    "sample": "\n{\n#if 1\n\tif (value) { return value - 9; }\n#else\n\tif (value) { return value; }\n#endif\n\treturn -9;\n}",
}
SOURCE = ("/* #include <bad.h> and unmatched { */\n#include <stddef.h>\n"
          "#define UNUSED_OPEN {\ntypedef long ssize_t;\nstatic int total;\n" +
          SIGNATURES["submit"] + BODIES["submit"] + "\n#if 1\n" +
          SIGNATURES["observe"] + BODIES["observe"] + "\n#else\n"
          "static long alternate(long value) { return value; }\n#endif\n" +
          SIGNATURES["complete"] + BODIES["complete"] + "\n" +
          SIGNATURES["sample"] + BODIES["sample"] + "\n").encode()
MAKEFILE = b"# original kernel build\nobj-y += original.o\n"
KCONFIG = b'menu "Kernel hacking"\n\nendmenu # Kernel hacking\n'
HEADER = b"""#ifndef QKX_TEST_GATE
#define QKX_TEST_GATE
enum { QKX_OWNER_CAMERA, QKX_OWNER_FASTRPC };
enum { QKX_OWNER_SUBMIT, QKX_OWNER_LIFECYCLE };
struct qkx_owner_token { int counted; };
static int qkx_test_closed, qkx_test_leaves;
static int qkx_owner_enter(unsigned int domain, unsigned int kind,
                          struct qkx_owner_token *token)
{
    (void)domain;
    if (kind == QKX_OWNER_SUBMIT && qkx_test_closed) return -16;
    token->counted = 1;
    return kind == QKX_OWNER_LIFECYCLE ? -22 : 0;
}
static void qkx_owner_leave(struct qkx_owner_token *token)
{ if (token->counted) qkx_test_leaves++; }
#endif
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


class OwnerGateApplyTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / "tools/quest2_apply_owner_gate.py"
        self.assertTrue(path.is_file(), "offline source applier is missing")
        spec = importlib.util.spec_from_file_location("owner_gate_apply", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory(prefix="qkx-owner-apply-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        for name, data in {"drivers/test.c": SOURCE, "kernel/Makefile": MAKEFILE,
                           "lib/Kconfig.debug": KCONFIG}.items():
            dest = self.source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (self.source / "include/linux").mkdir(parents=True)
        self.manifest = {
            "schema_version": 1, "domain": "fastrpc", "files": [{
                "path": "drivers/test.c", "sha256": digest(SOURCE),
                "sites": [{"name": name, "signature": signature,
                           "function_sha256": digest((signature + BODIES[name]).encode()),
                           "args": ["value"], "result": result,
                           "policy": "reject" if name == "submit" else "observe"}
                          for name, signature, result in [
                              ("submit", SIGNATURES["submit"], "int"),
                              ("observe", SIGNATURES["observe"], "long"),
                              ("complete", SIGNATURES["complete"], "void"),
                              ("sample", SIGNATURES["sample"], "ssize_t")]]}]}
        self.manifest_path = self.base / "fastrpc-sites.json"
        self.save_manifest()
        self.header = self.base / "owner_gate.h"
        self.header.write_bytes(HEADER)
        self.core = self.base / "owner_gate.c"
        self.core.write_bytes(b"/* fixture core */\n")
        self.patch = mock.patch.multiple(
            self.module, MANIFEST_PATHS=(self.manifest_path,),
            BUILD_HASHES={"kernel/Makefile": digest(MAKEFILE),
                          "lib/Kconfig.debug": digest(KCONFIG)},
            CORE_FILES={"kernel/qkx_owner_gate.c": self.core,
                        "include/linux/qkx_owner_gate.h": self.header})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def original_tree(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes()
                for p in self.source.rglob("*") if p.is_file()}

    def test_prepare_is_read_only_and_wrappers_preserve_runtime_results(self):
        before = self.original_tree()
        changes = self.module.prepare_changes(self.source)
        self.assertEqual(self.original_tree(), before)
        self.assertEqual(set(changes), {"drivers/test.c", "kernel/Makefile", "lib/Kconfig.debug",
                                        "kernel/qkx_owner_gate.c", "include/linux/qkx_owner_gate.h"})
        for name, data in changes.items():
            (self.source / name).write_bytes(data)
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            self.fail("native host compiler required")
        main = b"""
int main(void) {
    if (submit(2) != 9 || observe(14) != 11 || sample(1) != -8) return 1;
    complete(5);
    if (total != 5 || qkx_test_leaves != 4) return 2;
    qkx_test_closed = 1;
    if (submit(2) != -16 || observe(14) != 11) return 3;
    complete(7);
    if (total != 12 || qkx_test_leaves != 6) return 4;
    return 0;
}
"""
        test_c = self.source / "drivers/test.c"
        test_c.write_bytes(test_c.read_bytes() + main)
        binary = self.base / ("case.exe" if os.name == "nt" else "case")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                                 "-I", str(self.source / "include"), str(test_c), "-o", str(binary)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_apply_verifies_every_written_hash_and_receipt_then_refuses_reuse(self):
        receipt_path = self.base / "receipt.json"
        receipt = self.module.apply(self.source, receipt_path)
        self.assertEqual(json.loads(receipt_path.read_text()), receipt)
        self.assertFalse(receipt["deployment"])
        self.assertFalse(receipt["full_ownership"])
        self.assertEqual(receipt["scope"], "entry_accounting_only")
        self.assertEqual(receipt["counts"]["sites"], 4)
        for name, info in receipt["files"].items():
            data = (self.source / name).read_bytes()
            self.assertEqual(info["patched_sha256"], digest(data))
            self.assertEqual(info["patched_bytes"], len(data))
        self.assertIn(b"\tdefault n\n", (self.source / "lib/Kconfig.debug").read_bytes())
        self.assertFalse(receipt_path.with_suffix(".json.incomplete").exists())
        with self.assertRaises((ValueError, OSError)):
            self.module.apply(self.source, receipt_path)

    def test_any_late_source_or_build_hash_mismatch_prevents_all_writes(self):
        for name in ["drivers/test.c", "kernel/Makefile", "lib/Kconfig.debug"]:
            with self.subTest(name=name):
                path = self.source / name
                old = path.read_bytes()
                path.write_bytes(old + b"\n/* changed */\n")
                before = self.original_tree()
                with self.assertRaisesRegex(ValueError, "hash"):
                    self.module.apply(self.source, self.base / "receipt.json")
                self.assertEqual(self.original_tree(), before)
                self.assertFalse((self.base / "receipt.json.incomplete").exists())
                path.write_bytes(old)

    def test_wrong_function_hash_signature_args_and_policy_are_refused(self):
        original = copy.deepcopy(self.manifest)
        cases = [("function_sha256", "0" * 64), ("signature", "static int submit(long value)"),
                 ("args", ["other"]), ("policy", "skip"), ("result", "void")]
        for key, value in cases:
            with self.subTest(key=key):
                self.manifest = copy.deepcopy(original)
                self.manifest["files"][0]["sites"][0][key] = value
                self.save_manifest()
                with self.assertRaises(ValueError):
                    self.module.prepare_changes(self.source)

    def test_path_traversal_absolute_and_backslash_paths_are_refused(self):
        for name in ["../outside.c", "/outside.c", "drivers/../test.c", "drivers\\test.c",
                     "C:/outside.c", "drivers//test.c"]:
            with self.subTest(name=name):
                self.manifest["files"][0]["path"] = name
                self.save_manifest()
                with self.assertRaisesRegex(ValueError, "path"):
                    self.module.prepare_changes(self.source)

    def test_schema_unknown_keys_duplicate_sites_and_reserved_args_are_refused(self):
        original = copy.deepcopy(self.manifest)
        for change in [lambda m: m.update(extra=True),
                       lambda m: m["files"][0]["sites"].append(copy.deepcopy(m["files"][0]["sites"][0])),
                       lambda m: m["files"][0]["sites"][0].update(args=["qkx_entry_token"]),
                       lambda m: m.update(schema_version=True)]:
            self.manifest = copy.deepcopy(original)
            change(self.manifest)
            self.save_manifest()
            with self.assertRaises(ValueError):
                self.module.prepare_changes(self.source)

    def test_actual_reserved_parameter_and_void_reject_are_refused(self):
        modified = SOURCE.replace(b"value", b"qkx_entry_token")
        (self.source / "drivers/test.c").write_bytes(modified)
        entry = self.manifest["files"][0]
        entry["sha256"] = digest(modified)
        for site in entry["sites"]:
            name = site["name"]
            site["signature"] = SIGNATURES[name].replace("value", "qkx_entry_token")
            site["args"] = ["qkx_entry_token"]
            site["function_sha256"] = digest(
                (SIGNATURES[name] + BODIES[name]).replace("value", "qkx_entry_token").encode())
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.module.prepare_changes(self.source)
        (self.source / "drivers/test.c").write_bytes(SOURCE)
        entry["sha256"] = digest(SOURCE)
        for site in entry["sites"]:
            name = site["name"]
            site["signature"] = SIGNATURES[name]
            site["args"] = ["value"]
            site["function_sha256"] = digest((SIGNATURES[name] + BODIES[name]).encode())
            if name == "complete":
                site["policy"] = "reject"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "void"):
            self.module.prepare_changes(self.source)

    def test_duplicate_json_keys_are_refused(self):
        raw = json.dumps(self.manifest)
        self.manifest_path.write_text(raw.replace('"schema_version": 1',
                                                  '"schema_version": 0, "schema_version": 1'),
                                      encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.module.prepare_changes(self.source)

    def test_duplicate_definitions_and_unterminated_literals_are_refused(self):
        for modified in [SOURCE + (SIGNATURES["submit"] + BODIES["submit"]).encode(),
                         SOURCE + b'\nconst char *broken = "unterminated;\n']:
            (self.source / "drivers/test.c").write_bytes(modified)
            self.manifest["files"][0]["sha256"] = digest(modified)
            self.save_manifest()
            with self.assertRaises(ValueError):
                self.module.prepare_changes(self.source)

    def test_multiline_macro_braces_do_not_change_function_scope(self):
        modified = b"#define WORKQ(x) do { \\\n    x; } while (0)\n" + SOURCE
        (self.source / "drivers/test.c").write_bytes(modified)
        self.manifest["files"][0]["sha256"] = digest(modified)
        self.save_manifest()
        changes = self.module.prepare_changes(self.source)
        self.assertTrue(changes["drivers/test.c"].startswith(modified.split(b"/*", 1)[0]))

    def test_non_ascii_comment_bytes_are_preserved(self):
        prefix = b"/* copyright \xc2\xa9 preserved */\n"
        modified = prefix + SOURCE
        (self.source / "drivers/test.c").write_bytes(modified)
        self.manifest["files"][0]["sha256"] = digest(modified)
        self.save_manifest()
        changes = self.module.prepare_changes(self.source)
        self.assertTrue(changes["drivers/test.c"].startswith(prefix))

    def test_existing_install_or_receipt_refuses_before_mutation(self):
        installed = self.source / "include/linux/qkx_owner_gate.h"
        installed.write_bytes(b"unrelated existing file")
        before = self.original_tree()
        with self.assertRaises(ValueError):
            self.module.apply(self.source, self.base / "receipt.json")
        self.assertEqual(self.original_tree(), before)
        installed.unlink()
        receipt = self.base / "receipt.json"
        receipt.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            self.module.apply(self.source, receipt)
        self.assertEqual(receipt.read_bytes(), b"existing")

    def test_receipt_parent_traversal_cannot_alias_generated_header(self):
        before = self.original_tree()
        aliased = self.source / "include/linux/../linux/qkx_owner_gate.h"
        with self.assertRaisesRegex(ValueError, "path|traversal"):
            self.module.apply(self.source, aliased)
        self.assertEqual(self.original_tree(), before)

    def test_symlinked_source_member_is_refused(self):
        path = self.source / "drivers/test.c"
        target = self.base / "outside.c"
        target.write_bytes(SOURCE)
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: " + str(exc))
        with self.assertRaisesRegex(ValueError, "symlink|junction"):
            self.module.prepare_changes(self.source)
        self.assertEqual(target.read_bytes(), SOURCE)

    def test_partial_filesystem_failure_preserves_incomplete_marker(self):
        receipt = self.base / "receipt.json"
        real_replace = os.replace
        calls = 0

        def fail_second_replace(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated disk failure")
            return real_replace(src, dst)

        with mock.patch.object(self.module.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.module.apply(self.source, receipt)
        self.assertFalse(receipt.exists())
        marker = receipt.with_suffix(".json.incomplete")
        self.assertTrue(marker.is_file())
        self.assertEqual(json.loads(marker.read_text())["status"], "INCOMPLETE")

    def test_relative_source_and_symlinked_receipt_parent_are_refused(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.module.prepare_changes(Path("relative"))
        linked = self.base / "linked"
        try:
            linked.symlink_to(self.base, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: " + str(exc))
        before = self.original_tree()
        with self.assertRaisesRegex(ValueError, "symlink|junction"):
            self.module.apply(self.source, linked / "receipt.json")
        self.assertEqual(self.original_tree(), before)


if __name__ == "__main__":
    unittest.main()
