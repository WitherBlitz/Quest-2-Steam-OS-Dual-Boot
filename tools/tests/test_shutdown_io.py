"""Compile and execute production read-only shutdown IO primitives on the host."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ShutdownIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("HOST_TEST_CC is required; native tests must not silently skip")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-shutdown-io-")
        cls.addClassCleanup(cls.temp.cleanup)
        include = Path(cls.temp.name) / "linux"
        include.mkdir()
        (include / "types.h").write_text(
            "#include <stdbool.h>\n#include <stdint.h>\ntypedef uint32_t u32;\ntypedef uint64_t u64;\n")
        errors = {"EINVAL": 22, "EOPNOTSUPP": 95, "EPERM": 1, "ESTALE": 116,
                  "EOVERFLOW": 75, "ERANGE": 34, "ETIMEDOUT": 110,
                  "EALREADY": 114, "EPROTO": 71, "EAGAIN": 11, "EXDEV": 18,
                  "ENOBUFS": 105, "EIO": 5}
        (include / "errno.h").write_text("#include <errno.h>\n" + "".join(
            f"#undef {name}\n#define {name} {value}\n" for name, value in errors.items()))
        (include / "string.h").write_text("#include <string.h>\n")
        syntax = subprocess.run([
            compiler, "-std=gnu89", "-Wall", "-Wextra", "-Werror",
            "-Wdeclaration-after-statement", "-fsyntax-only", "-I", cls.temp.name,
            "-I", str(ROOT / "module"), str(ROOT / "module/shutdown_io.c")],
            capture_output=True, text=True)
        if syntax.returncode:
            raise RuntimeError(syntax.stdout + syntax.stderr)
        cls.binary = Path(cls.temp.name) / ("io.exe" if os.name == "nt" else "io")
        linker = ["-fuse-ld=lld"] if os.name == "nt" else []
        result = subprocess.run([
            compiler, *linker, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-I", cls.temp.name, "-I", str(ROOT / "module"),
            str(ROOT / "module/shutdown_io.c"),
            str(Path(__file__).with_name("shutdown_io_harness.c")),
            "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prerequisites_and_epoch_refuse_before_io(self): self.case("prerequisites")
    def test_source_offsets_and_partial_masks(self): self.case("offsets")
    def test_deadline_never_resets_and_last_sample_can_match(self): self.case("deadline")
    def test_stuck_frozen_clock_stops_at_read_cap(self): self.case("read_cap")
    def test_original_read_error_and_raw_value_are_retained(self): self.case("read_error")
    def test_clock_after_each_read_precedes_acceptance(self): self.case("late_read")
    def test_clock_rollback_and_error_refuse(self): self.case("clock")
    def test_overflow_invalid_caps_and_reinitialization_refuse(self): self.case("invalid")
    def test_epoch_change_between_ufs_reads_refuses(self): self.case("epoch_change")
    def test_aop_original_enqueue_failure_without_fallback(self): self.case("aop_error")
    def test_aop_accepted_token_is_not_acknowledgement(self): self.case("aop_pending")
    def test_aop_ack_binds_epoch_transaction_channel_and_transport(self): self.case("aop_ack")
    def test_aop_link_down_remains_unsupported_even_with_matching_bits(self): self.case("aop_link")


if __name__ == "__main__":
    unittest.main()
