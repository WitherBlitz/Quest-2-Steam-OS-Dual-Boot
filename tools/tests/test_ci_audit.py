"""Offline tests for the build's input and binary verification gates."""
import base64
import gzip
import hashlib
import importlib.util
import struct
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ci/quest2_audit.py"
audit = None
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location("quest2_audit", SCRIPT)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)

VM = "4.19.325-cip128-st12-g49638c7a8637 SMP preempt mod_unload modversions aarch64"
CRC = {"api": 0x11223344, "module_layout": 0x74B4EBEB}
KRET = {"register_kretprobe": 0x861FCDB7, "unregister_kretprobe": 0x4093E955}


def elf_fixture(*, machine=183, vermagic=VM, symbol="api", api_crc=0x11223344, diagnostic=False):
    # Hand-built ELF64 LE relocatable with one real undefined global symbol.
    names = b"\0.shstrtab\0.strtab\0.symtab\0.modinfo\0__versions\0"
    strings = b"\0" + symbol.encode() + b"\0"
    symbols = bytes(24) + struct.pack("<IBBHQQ", 1, 0x10, 0, 0, 0, 0)
    versions = b"".join(struct.pack("<Q56s", value, name.encode()) for name, value in
                        [("api", api_crc), ("module_layout", 0x74B4EBEB)])
    sections = [("", 0, b"", 0, 0), (".shstrtab", 3, names, 0, 0),
                (".strtab", 3, strings, 0, 0), (".symtab", 2, symbols, 2, 24),
                (".modinfo", 1, b"vermagic=" + vermagic.encode() + b"\0" +
                 (b"parmtype=ram_diagnose:bool\0" if diagnostic else b""), 0, 0),
                ("__versions", 1, versions, 0, 0)]
    data = bytearray(64)
    headers = []
    for name, kind, content, link, entsize in sections:
        offset = len(data)
        data += content
        headers.append(struct.pack("<IIQQQQIIQQ", names.index(name.encode()+b"\0") if name else 0,
                                   kind, 0, 0, offset, len(content), link, 0, 1, entsize))
    table = len(data)
    data += b"".join(headers)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 1, machine, 1, 0, 0, table,
                            0, 64, 0, 0, 64, len(headers), 1)
    return bytes(data)


class CIAuditTests(unittest.TestCase):
    def require_audit(self):
        self.assertIsNotNone(audit, "CI config and ELF verification gates are not implemented")

    def test_valid_configuration_round_trip(self):
        self.require_audit()
        config = b"CONFIG_TEST=y\n"
        encoded = base64.b64encode(gzip.compress(config)).decode()
        self.assertEqual(audit.decode_config(encoded, hashlib.sha256(config).hexdigest()), config)

    def test_configuration_digest_mismatch_rejected(self):
        self.require_audit()
        encoded = base64.b64encode(gzip.compress(b"CONFIG_TEST=y\n")).decode()
        with self.assertRaises(ValueError):
            audit.decode_config(encoded, "0"*64)

    def test_corrupt_config_encoding_rejected(self):
        self.require_audit()
        for encoded in ["!not-base64!", base64.b64encode(b"not gzip").decode(), ""]:
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                audit.decode_config(encoded)

    def test_config_expansion_is_bounded(self):
        self.require_audit()
        payload = b"X" * (1024*1024+1)
        with self.assertRaises(ValueError):
            audit.decode_config(base64.b64encode(gzip.compress(payload)).decode(),
                                hashlib.sha256(payload).hexdigest())

    def test_matching_module_is_accepted(self):
        self.require_audit()
        result = audit.inspect_module(elf_fixture(), CRC, CRC)
        self.assertEqual(result["undefined_symbols"], ["api"])
        self.assertEqual(result["versioned_imports"], 2)

    def test_wrong_machine_is_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(machine=62), CRC, CRC)

    def test_wrong_vermagic_is_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(vermagic="different kernel"), CRC, CRC)

    def test_generated_crc_mismatch_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(api_crc=0x11223345), CRC, CRC)

    def test_stock_crc_mismatch_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(), CRC, {**CRC, "api": 0x11223345})

    def test_missing_stock_reference_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(), CRC, {"module_layout": 0x74B4EBEB})

    def test_unversioned_undefined_symbol_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture(symbol="unversioned"), CRC, CRC)

    def test_truncated_elf_is_rejected(self):
        self.require_audit()
        with self.assertRaises(ValueError):
            audit.inspect_module(elf_fixture()[:-32], CRC, CRC)

    def test_diagnostic_indirect_abi_crcs_are_recorded(self):
        result = audit.inspect_module(elf_fixture(diagnostic=True), {**CRC, **KRET}, {**CRC, **KRET})
        self.assertEqual(result.get("indirect_crc_rows"),
                         {name: f"0x{value:08x}" for name, value in KRET.items()})

    def test_diagnostic_indirect_abi_mismatch_is_rejected(self):
        for name in KRET:
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit.inspect_module(elf_fixture(diagnostic=True),
                                     {**CRC, **KRET, name: KRET[name] ^ 1}, {**CRC, **KRET})

    def test_diagnostic_missing_indirect_abi_reference_is_rejected(self):
        for name in KRET:
            for source in ("generated", "stock"):
                with self.subTest(name=name, source=source), self.assertRaises(ValueError):
                    generated, stock = {**CRC, **KRET}, {**CRC, **KRET}
                    (generated if source == "generated" else stock).pop(name)
                    audit.inspect_module(elf_fixture(diagnostic=True), generated, stock)


if __name__ == "__main__":
    unittest.main()
