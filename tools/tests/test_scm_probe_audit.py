"""SCM ELF gates distinguish generated ABI from pending live-loader acceptance."""
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"tools/ci"))

CRCS = {"module_layout": 0x74b4ebeb, "param_ops_bool": 0x11223344,
        "scm_is_call_available": 0x22334455, "scm_call2_noretry": 0x33445566}
STOCK = {k: v for k, v in CRCS.items() if not k.startswith("scm_")}
MAGIC = "4.19.325-cip128-st12-g49638c7a8637 SMP preempt mod_unload modversions aarch64"


def fixture(*, imports=None, versions=None, parm=b"parmtype=execute:bool", constructor=False):
    imports = list(CRCS.keys()-{"module_layout"}) if imports is None else imports
    versions = CRCS if versions is None else versions
    names = b"\0.shstrtab\0.strtab\0.symtab\0.modinfo\0__versions\0.ctors\0"
    strings = b"\0"
    symbols = bytes(24)
    for name in imports:
        symbols += struct.pack("<IBBHQQ", len(strings), 0x10, 0, 0, 0, 0)
        strings += name.encode()+b"\0"
    rows = b"".join(struct.pack("<Q56s", crc, name.encode()) for name, crc in versions.items())
    sections = [("", 0, b"", 0, 0), (".shstrtab", 3, names, 0, 0),
                (".strtab", 3, strings, 0, 0), (".symtab", 2, symbols, 2, 24),
                (".modinfo", 1, b"vermagic="+MAGIC.encode()+b"\0license=GPL\0"+parm+b"\0", 0, 0),
                ("__versions", 1, rows, 0, 0)]
    if constructor:
        sections.append((".ctors", 1, bytes(8), 0, 0))
    data, headers = bytearray(64), []
    for name, kind, content, link, stride in sections:
        offset = len(data)
        data += content
        headers.append(struct.pack("<IIQQQQIIQQ", names.index(name.encode()+b"\0") if name else 0,
                                   kind, 0, 0, offset, len(content), link, 0, 1, stride))
    table = len(data)
    data += b"".join(headers)
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01\x01"+bytes(9),
                           1, 183, 1, 0, 0, table, 0, 64, 0, 0, 64, len(headers), 1)
    return bytes(data)


class ScmProbeAuditTests(unittest.TestCase):
    def module(self):
        path = ROOT/"tools/ci/quest2_scm_probe_audit.py"
        self.assertTrue(path.is_file(), "separate provisional SCM ABI auditor missing")
        spec = importlib.util.spec_from_file_location("scm_probe_audit", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generated_scm_imports_do_not_become_live_stock_proof(self):
        result = self.module().inspect_probe(fixture(), CRCS, STOCK)
        self.assertEqual(result["stock_unverified_imports"],
                         {k: f"0x{CRCS[k]:08x}" for k in ("scm_call2_noretry", "scm_is_call_available")})
        self.assertFalse(result["runtime_import_acceptance_validated"])
        self.assertFalse(result["secure_query_validated"])
        self.assertFalse(result["driver_execution_validated"])

    def test_captured_stock_scm_match_still_requires_runtime_and_query_checks(self):
        result = self.module().inspect_probe(fixture(), CRCS, CRCS)
        self.assertEqual(result["stock_unverified_imports"], {})
        self.assertFalse(result["runtime_import_acceptance_validated"])
        self.assertFalse(result["secure_query_validated"])

    def test_known_stock_or_generated_mismatch_is_rejected(self):
        module = self.module()
        for name in CRCS:
            for which in ("generated", "stock"):
                generated, stock = dict(CRCS), dict(CRCS)
                (generated if which == "generated" else stock)[name] ^= 1
                with self.subTest(name=name, which=which), self.assertRaises(ValueError):
                    module.inspect_probe(fixture(), generated, stock)

    def test_missing_ordinary_stock_import_is_not_deferred(self):
        with self.assertRaises(ValueError):
            self.module().inspect_probe(fixture(), CRCS, {"module_layout": CRCS["module_layout"]})

    def test_every_undefined_import_requires_its_own_version_record(self):
        module = self.module()
        for name in CRCS:
            versions = {k: v for k, v in CRCS.items() if k != name}
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.inspect_probe(fixture(versions=versions), CRCS, STOCK)

    def test_fixed_import_allowlist_rejects_other_valid_kernel_calls(self):
        crcs = dict(CRCS, kernel_restart=0x44556677)
        with self.assertRaises(ValueError):
            self.module().inspect_probe(fixture(imports=list(crcs), versions=crcs), crcs, crcs)

    def test_both_documented_scm_imports_must_be_real_references(self):
        with self.assertRaises(ValueError):
            self.module().inspect_probe(fixture(imports=["param_ops_bool", "scm_is_call_available"]), CRCS, STOCK)

    def test_non_boolean_extra_parameter_and_constructors_are_rejected(self):
        module = self.module()
        for data in (fixture(parm=b"parmtype=execute:int"),
                     fixture(parm=b"parmtype=execute:bool\0parmtype=id:uint"),
                     fixture(constructor=True)):
            with self.subTest(data=data[-24:]), self.assertRaises(ValueError):
                module.inspect_probe(data, CRCS, STOCK)


if __name__ == "__main__":
    unittest.main()
