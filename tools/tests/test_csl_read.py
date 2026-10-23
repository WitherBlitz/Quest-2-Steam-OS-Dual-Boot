"""Run the actual sparse reader with host syscall doubles; never open target memory.

The doubles replace only Linux syscalls. Plan parsing, validation, requested
addresses, byte counts, output and exit status execute the production C code.
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/native/quest2_csl_read.c"
BASE = 0x7000000000
LOW = BASE + 0x865f0
HIGH = LOW + 0x1d94e0 + 128 * 0x388
MAGIC = b"QCSLR01\0"

HARNESS = r'''
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
#define QCSL_HOST_TEST
#include "quest2_csl_read.c"
static int opens, closes, preads, writes, eof_seen, violation;
static const char *mode;
static int is_mode(const char *s) { return strcmp(mode, s) == 0; }
static qcs_sword qcs_syscall(qcs_word nr, qcs_word a0, qcs_word a1,
                             qcs_word a2, qcs_word a3)
{
    size_t count;
    if (nr == 63) {
        if (a0 != 0 || a3) violation = 1;
        if (is_mode("input_error")) return -5;
        if (is_mode("input_eintr")) return -4;
        count = (size_t)a2;
        if (is_mode("fragmented_input") && count > 3) count = 3;
        count = fread((void *)a1, 1, count, stdin);
        if (!count && feof(stdin)) {
            if (is_mode("trailer_error")) return -5;
            eof_seen = 1;
        }
        return (qcs_sword)count;
    }
    if (nr == 56) {
        opens++;
        if (a0 != (qcs_word)(qcs_sword)-100 || a2 != 02000000 || a3 || !eof_seen)
            violation = 1;
        fprintf(stderr, "path=%s\n", (const char *)a1);
        if (is_mode("open_error")) return -13;
        return 23;
    }
    if (nr == 67) {
        size_t i;
        preads++;
        if (a0 != 23 || !a2 || a2 > 512 || !eof_seen) violation = 1;
        fprintf(stderr, "pread=%llx:%llu\n", (unsigned long long)a3,
                (unsigned long long)a2);
        if (is_mode("pread_error")) return -5;
        if (is_mode("pread_eintr")) return -4;
        if (is_mode("pread_short")) return (qcs_sword)a2 - 1;
        for (i = 0; i < (size_t)a2; i++) ((unsigned char *)a1)[i] = (unsigned char)(a3 + i);
        return (qcs_sword)a2;
    }
    if (nr == 64) {
        writes++;
        if (a0 != 1 || a3 || !a2 || a2 > 512) violation = 1;
        if (is_mode("write_error")) return -32;
        if (is_mode("write_zero")) return 0;
        if (is_mode("write_eintr") && writes < 3) return -4;
        if (is_mode("write_eintr_forever")) return -4;
        count = (size_t)a2;
        if (is_mode("partial_write") && count > 3) count = 3;
        return (qcs_sword)fwrite((const void *)a1, 1, count, stdout);
    }
    if (nr == 57) {
        closes++;
        if (a0 != 23 || a1 || a2 || a3) violation = 1;
        return is_mode("close_error") ? -5 : 0;
    }
    violation = 1;
    return -38;
}
int main(int argc, char **argv)
{
    int result;
    (void)&qcs_syscall;
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    mode = getenv("QCSL_TEST_MODE");
    if (!mode) mode = "normal";
    result = qcs_main(argc, argv);
    fprintf(stderr, "open=%d close=%d preads=%d writes=%d violation=%d\n",
            opens, closes, preads, writes, violation);
    return result;
}
'''


def plan(records, *, count=None, magic=MAGIC, trailer=b""):
    return (magic + struct.pack("<I", len(records) if count is None else count)
            + b"".join(struct.pack("<QI", address, size) for address, size in records)
            + trailer)


class CSLReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file():
            raise AssertionError("native sparse reader is missing")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC; native reader tests must not silently skip")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-csl-read-")
        cls.addClassCleanup(cls.temp.cleanup)
        harness = Path(cls.temp.name) / "harness.c"
        harness.write_text(HARNESS)
        cls.binary = Path(cls.temp.name) / ("reader.exe" if os.name == "nt" else "reader")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([compiler, *linker, "-std=c11", "-O2", "-Wall", "-Wextra",
                                 "-Werror", "-I", str(SOURCE.parent), str(harness),
                                 "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Reader harness compilation failed:\n" + result.stdout + result.stderr)

    def run_reader(self, data, *, args=("42", "7000000000"), mode="normal", status=0):
        result = subprocess.run([str(self.binary), *args], input=data,
                                env={**os.environ, "QCSL_TEST_MODE": mode},
                                capture_output=True, timeout=10)
        trace = result.stderr.decode("ascii").replace("\r\n", "\n")
        self.assertEqual(result.returncode, status, trace)
        self.assertIn("violation=0", trace)
        return result.stdout, trace

    def refused(self, data, *, args=("42", "7000000000"), status=3, mode="normal"):
        output, trace = self.run_reader(data, args=args, status=status, mode=mode)
        self.assertEqual(output, b"")
        self.assertIn("open=0 close=0 preads=0 writes=0", trace)

    def test_exact_sparse_reads_preserve_order_and_object_edges(self):
        records = [(LOW, 1), (LOW + 0x9ff, 512), (HIGH - 4, 4), (LOW + 16, 4)]
        output, trace = self.run_reader(plan(records))
        expected = b"".join(bytes((address + i) & 255 for i in range(size))
                            for address, size in records)
        self.assertEqual(output, expected)
        self.assertIn("path=/proc/42/mem\n", trace)
        self.assertIn("open=1 close=1 preads=4", trace)
        self.assertEqual([line for line in trace.splitlines() if line.startswith("pread=")],
                         [f"pread={address:x}:{size}" for address, size in records])

    def test_fragmented_input_and_partial_stdout_are_completed(self):
        data = plan([(LOW + 16, 17)])
        for mode in ("fragmented_input", "partial_write", "write_eintr"):
            with self.subTest(mode=mode):
                output, _ = self.run_reader(data, mode=mode)
                self.assertEqual(output, bytes((LOW + 16 + i) & 255 for i in range(17)))

    def test_empty_plan_requires_eof_but_never_opens_memory(self):
        output, trace = self.run_reader(plan([]))
        self.assertEqual(output, b"")
        self.assertIn("open=0 close=0 preads=0 writes=0", trace)
        self.refused(plan([], trailer=b"x"))

    def test_bad_magic_count_and_trailer_refuse_before_open(self):
        for data in (plan([], magic=b"QCSLR02\0"), plan([], count=16385),
                     plan([(LOW, 4)], trailer=b"\0"), plan([(LOW, 4)], count=0)):
            with self.subTest(data=data[:20]):
                self.refused(data)

    def test_truncated_input_and_stdin_errors_refuse_before_open(self):
        data = plan([(LOW, 4)])
        for end in (0, 7, 8, 11, 12, 13, len(data) - 1):
            with self.subTest(end=end):
                self.refused(data[:end])
        for mode in ("input_error", "input_eintr", "trailer_error"):
            with self.subTest(mode=mode):
                self.refused(data, mode=mode)

    def test_whole_plan_is_validated_before_first_memory_open(self):
        for address, size in ((LOW - 1, 1), (HIGH, 1), (HIGH - 1, 2),
                              (LOW, 0), (LOW, 513), ((1 << 64) - 1, 512)):
            with self.subTest(address=address, size=size):
                self.refused(plan([(LOW, 4), (address, size)]))

    def test_one_mib_is_allowed_and_one_extra_byte_is_refused(self):
        records = [(LOW + 16, 512)] * 2048
        output, trace = self.run_reader(plan(records))
        self.assertEqual(len(output), 1048576)
        self.assertIn("open=1 close=1 preads=2048", trace)
        self.refused(plan(records + [(LOW, 1)]))

    def test_maximum_record_count_is_accepted(self):
        output, trace = self.run_reader(plan([(LOW, 1)] * 16384))
        self.assertEqual(output, bytes([LOW & 255]) * 16384)
        self.assertIn("preads=16384", trace)

    def test_invalid_cli_never_opens_memory(self):
        for args in ((), ("42",), ("42", "7000000000", "extra")):
            self.refused(plan([]), args=args, status=2)
        for pid in ("", "0", "4194305", "-1", "+42", " 42", "4x", "9999999999999999999999"):
            with self.subTest(pid=pid):
                self.refused(plan([]), args=(pid, "7000000000"), status=2)
        for base in ("", "0x", "-1000", "+1000", "1001", "xyz", "10000000000000000",
                     "fffffffffffff000", "100000000000000", "fffffffff00000"):
            with self.subTest(base=base):
                self.refused(plan([]), args=("42", base), status=2)

    def test_pid_limits_and_hex_prefix_are_supported(self):
        for pid in ("1", "4194304", "00042"):
            output, trace = self.run_reader(plan([(LOW, 4)]), args=(pid, "0X7000000000"))
            self.assertEqual(output, bytes((LOW + i) & 255 for i in range(4)))
            self.assertIn(f"path=/proc/{int(pid)}/mem\n", trace)
        self.run_reader(plan([(0x865f0, 1)]), args=("42", "0"))

    def test_highest_supported_aligned_base_checks_object_extent(self):
        base = ((1 << 56) - 0x865f0 - 0x1f58e0 - 1) & ~4095
        self.run_reader(plan([(base + 0x865f0, 1)]), args=("42", f"{base:x}"))
        self.refused(plan([]), args=("42", f"{base + 4096:x}"), status=2)

    def test_open_failure_does_not_pread_or_close_invalid_fd(self):
        output, trace = self.run_reader(plan([(LOW, 4)]), mode="open_error", status=4)
        self.assertEqual(output, b"")
        self.assertIn("open=1 close=0 preads=0 writes=0", trace)

    def test_pread_errno_eintr_and_short_read_fail_without_retry(self):
        for mode in ("pread_error", "pread_eintr", "pread_short"):
            with self.subTest(mode=mode):
                output, trace = self.run_reader(plan([(LOW, 4), (LOW + 16, 4)]),
                                                mode=mode, status=5)
                self.assertEqual(output, b"")
                self.assertIn("open=1 close=1 preads=1 writes=0", trace)

    def test_write_error_zero_and_repeated_eintr_fail_and_close(self):
        for mode in ("write_error", "write_zero", "write_eintr_forever"):
            with self.subTest(mode=mode):
                output, trace = self.run_reader(plan([(LOW, 4), (LOW + 16, 4)]),
                                                mode=mode, status=6)
                self.assertEqual(output, b"")
                self.assertIn("open=1 close=1 preads=1", trace)

    def test_close_failure_is_reported_after_output(self):
        output, trace = self.run_reader(plan([(LOW, 4)]), mode="close_error", status=7)
        self.assertEqual(output, bytes((LOW + i) & 255 for i in range(4)))
        self.assertIn("open=1 close=1 preads=1 writes=1", trace)


if __name__ == "__main__":
    unittest.main()
