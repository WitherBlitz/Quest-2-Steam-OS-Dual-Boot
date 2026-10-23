"""Inspect compiled production ARM64 code; never execute privileged instructions.

This small assembler fixture substitutes linkage declarations and the three
definitions below for kernel headers. It is not a full kernel/module build,
MMU/cache execution test, CPU-off receipt, or hardware/recovery validation.
Definitions are from kernel 6bde5fc06b252c97761bc0963dd13c1abcf377ab:
asm/assembler.h:345-350,613-617 (SHA256 6462cb1b7a5b2ac45636d07e32023a605d1274b8502c1b00b137b2801349043b),
asm/sysreg.h:453-460 (SHA256 f087b7ed2a6a11b8948897d1aa4cff227506fc80e544906335303a702a1c9cfb).
The pinned target has CONFIG_QCOM_FALKOR_ERRATUM_E1041 unset.
"""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCTLR_FLAGS = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 12) | (1 << 21)
DEFINITIONS = r"""
.equ SCTLR_ELx_FLAGS, ((1<<0)|(1<<1)|(1<<2)|(1<<3)|(1<<12)|(1<<21))
.macro raw_dcache_line_size, reg, tmp
    mrs \tmp, ctr_el0
    ubfm \tmp, \tmp, #16, #19
    mov \reg, #4
    lsl \reg, \reg, \tmp
.endm
.macro pre_disable_mmu_workaround
.endm
"""


def fixture(source):
    includes = re.findall(r"^#include\s+(.+)$", source, re.M)
    if includes != ["<linux/linkage.h>", "<asm/assembler.h>", "<asm/sysreg.h>"]:
        raise ValueError("transition assembler fixture needs header review")
    source = re.sub(r"^#include.*$", "", source, flags=re.M)
    source = re.sub(r"ENTRY\((\w+)\)", r".global \1\n.type \1,%function\n\1:", source)
    source = re.sub(r"ENDPROC\((\w+)\)", r".size \1, .-\1", source)
    return DEFINITIONS + source


def elf_symbols(blob):
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", blob)
    if blob[:6] != b"\x7fELF\x02\x01" or header[1:3] != (1, 183):
        raise ValueError("expected ELF64 little-endian AArch64 relocatable object")
    sections = [struct.unpack_from("<IIQQQQIIQQ", blob, header[6] + i * header[11])
                for i in range(header[12])]
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        names = sections[section[6]]
        strings = blob[names[4]:names[4] + names[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, _, _, index, value, size = struct.unpack_from("<IBBHQQ", blob, offset)
            if name:
                label = strings[name:strings.index(b"\0", name)].decode()
                symbols[label] = (value, size, index)
    return sections, symbols


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class TransitionAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to Clang for AArch64 assembly checks")
        compiler = Path(shutil.which(compiler) or compiler).resolve()
        cls.compiler = compiler
        sibling = compiler.with_name("llvm-objdump.exe" if os.name == "nt" else "llvm-objdump")
        objdump = os.environ.get("LLVM_OBJDUMP") or (str(sibling) if sibling.is_file()
                                                    else shutil.which("llvm-objdump"))
        if not objdump:
            raise RuntimeError("LLVM llvm-objdump is required beside Clang or on PATH")
        for tool, expected in ((str(compiler), "clang"), (str(objdump), "LLVM")):
            version = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=20)
            if version.returncode or expected not in version.stdout:
                raise RuntimeError("unexpected compiler/disassembler: " + version.stdout + version.stderr)
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-transition-")
        cls.addClassCleanup(cls.temp.cleanup)
        binary = Path(cls.temp.name) / "transition.o"
        source = fixture((ROOT / "module/transition.S").read_text())
        result = subprocess.run([str(compiler), "-target", "aarch64-linux-gnu", "-x", "assembler",
                                 "-c", "-o", str(binary), "-"], input=source, capture_output=True,
                                text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.blob = binary.read_bytes()
        cls.sections, cls.symbols = elf_symbols(cls.blob)
        result = subprocess.run([str(objdump), "-d", "--no-show-raw-insn", str(binary)],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.instructions = {}
        for line in result.stdout.splitlines():
            match = re.match(r"\s*([0-9a-f]+):\s+(.+)$", line)
            if match and not match[2].lstrip().startswith("."):
                cls.instructions[int(match[1], 16)] = re.sub(r"\s+", "", match[2].lower())

    def body(self, symbol):
        start, size, _ = self.symbols[symbol]
        return [op for address, op in sorted(self.instructions.items())
                if start <= address < start + size]

    def ordered(self, body, *patterns):
        position = -1
        for pattern in patterns:
            matches = [i for i, operation in enumerate(body)
                       if i > position and re.fullmatch(pattern, operation)]
            self.assertTrue(matches, f"missing/out-of-order {pattern}: {body}")
            position = matches[0]

    def test_roundtrip_saves_masks_and_restores_all_daif_after_translation(self):
        body = self.body("qkx_roundtrip")
        self.ordered(body, r"stpx27,x28,\[sp,#(?:0x40|64)\]", r"mrsx28,daif",
                     r"msrdaifset,#(?:0xf|15)", r"msrtcr_el1,x9", r"msrttbr0_el1,x0",
                     r"brx1", r"msrttbr0_el1,x25", r"msrtcr_el1,x24", r"isb",
                     r"msrdaif,x28", r"ldpx27,x28,\[sp,#(?:0x40|64)\]", r"ret")
        self.assertEqual(sum(op.startswith("msrdaif,") for op in body), 1)

    def test_translation_switch_uses_walk_disable_tlb_barriers(self):
        for symbol in ("qkx_enter", "qkx_roundtrip"):
            with self.subTest(symbol=symbol):
                self.ordered(self.body(symbol), r"msrtcr_el1,x9", r"isb", r"dsbishst",
                             r"tlbivmalle1", r"dsbish", r"isb", r"msrttbr0_el1,x0",
                             r"msrtcr_el1,x9", r"isb", r"brx1")
        self.ordered(self.body("qkx_roundtrip"), r"brx1", r"msrtcr_el1,x9", r"isb",
                     r"msrttbr0_el1,x25", r"dsbishst", r"tlbivmalle1", r"dsbish",
                     r"isb", r"msrtcr_el1,x24", r"isb")

    def test_production_entry_masks_daif_and_passes_linux_register_contract(self):
        self.ordered(self.body("qkx_enter"), r"msrdaifset,#(?:0xf|15)",
                     r"msrtcr_el1,x9", r"brx1")
        self.ordered(self.body("qkx_reloc_start"), r"movx17,x3", r"movx18,x4", r"movx19,x2",
                     r"msrsctlr_el1,x9", r"isb", r"movx0,x18", r"movx1,xzr",
                     r"movx2,xzr", r"movx3,xzr", r"brx17")

    def test_copy_ranges_fit_control_page_and_have_no_relocations(self):
        self.assertFalse([section for section in self.sections if section[1] in (4, 9) and section[5]])
        for start, end in (("qkx_reloc_start", "qkx_reloc_end"),
                           ("qkx_probe_phys_start", "qkx_probe_phys_end"),
                           ("qkx_reloc_return_start", "qkx_reloc_return_end")):
            lo, size, section = self.symbols[start]
            hi, _, end_section = self.symbols[end]
            self.assertEqual(section, end_section)
            self.assertEqual(size, hi - lo)
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 4096)
            self.assertFalse(any("sp" in op for op in self.body(start)))
        relocation = self.symbols["qkx_reloc_start"][1]
        self.assertLessEqual((relocation + 15) // 16 * 16 + self.symbols["qkx_reloc_return_start"][1], 4096)

    def test_copied_code_branches_and_literal_pools_are_self_contained(self):
        for symbol in ("qkx_reloc_start", "qkx_probe_phys_start", "qkx_reloc_return_start"):
            lo, size, section = self.symbols[symbol]
            hi = lo + size
            data = self.sections[section]
            literals = 0
            for pc in sorted(self.instructions):
                if not lo <= pc < hi:
                    continue
                word = struct.unpack_from("<I", self.blob, data[4] + pc)[0]
                target = None
                if word & 0x7C000000 == 0x14000000:
                    self.assertFalse(word & 0x80000000, "copied code must not call external code")
                    target = pc + 4 * signed(word & 0x3FFFFFF, 26)
                elif word & 0xFF000010 == 0x54000000 or word & 0x7E000000 == 0x34000000:
                    target = pc + 4 * signed((word >> 5) & 0x7FFFF, 19)
                elif word & 0x7E000000 == 0x36000000:
                    target = pc + 4 * signed((word >> 5) & 0x3FFF, 14)
                elif word & 0x3B000000 == 0x18000000:
                    self.assertEqual(word & 0xFF000000, 0x58000000, "unexpected literal load form")
                    target = pc + 4 * signed((word >> 5) & 0x7FFFF, 19)
                    self.assertTrue(lo <= target and target + 8 <= hi)
                    self.assertEqual(struct.unpack_from("<Q", self.blob, data[4] + target)[0], SCTLR_FLAGS)
                    literals += 1
                self.assertNotIn(word & 0x9F000000, (0x10000000, 0x90000000), "unexpected copied ADR/ADRP")
                self.assertNotEqual(word & 0xFFFFFC1F, 0xD63F0000, "unexpected copied BLR")
                if target is not None:
                    self.assertTrue(lo <= target < hi, (symbol, pc, target))
            self.assertEqual(literals, 1 if symbol == "qkx_reloc_start" else 0)

    def test_probe_materializes_the_complete_pinned_sctlr_mask(self):
        lo, size, section = self.symbols["qkx_probe_phys_start"]
        value = None
        for pc in sorted(self.instructions):
            if not lo <= pc < lo + size:
                continue
            word = struct.unpack_from("<I", self.blob, self.sections[section][4] + pc)[0]
            if word & 31 != 10:
                continue
            shift = 16 * ((word >> 21) & 3)
            immediate = (word >> 5) & 0xFFFF
            if word & 0xFF800000 == 0xD2800000:  # MOVZ X10
                value = immediate << shift
            elif word & 0xFF800000 == 0xF2800000:  # MOVK X10
                self.assertIsNotNone(value)
                value = (value & ~(0xFFFF << shift)) | (immediate << shift)
        self.assertEqual(value, SCTLR_FLAGS)

    def test_probe_refuses_a_future_mask_that_exceeds_32_bits(self):
        source = fixture((ROOT / "module/transition.S").read_text())
        source = re.sub(r"^\.equ SCTLR_ELx_FLAGS,.*$", ".equ SCTLR_ELx_FLAGS, (1<<32)",
                        source, count=1, flags=re.M)
        result = subprocess.run([str(self.compiler), "-target", "aarch64-linux-gnu", "-x", "assembler",
                                 "-c", "-o", str(Path(self.temp.name) / "too-wide.o"), "-"],
                                input=source, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("qkx probe SCTLR mask exceeds 32 bits", result.stderr)

    def test_relocation_cache_barriers_surround_mmu_off_copy(self):
        body = self.body("qkx_reloc_start")
        self.ordered(body, r"msrsctlr_el1,x9", r"isb", r"dcivac,x9", r"dsbsy",
                     r"ldpx9,x10,\[x22\],#(?:0x10|16)", r"stpx9,x10,\[x23\],#(?:0x10|16)",
                     r"dsbsy", r"iciallu", r"dsbsy", r"isb", r"brx17")
        for symbol in ("qkx_probe_phys_start", "qkx_reloc_return_start"):
            self.ordered(self.body(symbol), r"msrsctlr_el1,x26", r"isb", r"brx27")


if __name__ == "__main__":
    unittest.main()
