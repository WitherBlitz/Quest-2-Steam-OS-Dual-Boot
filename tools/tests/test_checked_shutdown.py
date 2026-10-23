"""Execute the production shutdown coordinator with nonblocking host doubles."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CheckedShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("Set HOST_TEST_CC to a host C compiler")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-shutdown-")
        cls.addClassCleanup(cls.temp.cleanup)
        temp = Path(cls.temp.name)
        (temp / "linux").mkdir()
        (temp / "linux/types.h").write_text(
            "#include <stdint.h>\n#include <stdbool.h>\ntypedef uint64_t u64;\n")
        (temp / "linux/errno.h").write_text(
            "#include <errno.h>\n"
            "#ifndef EOPNOTSUPP\n#define EOPNOTSUPP 95\n#endif\n"
            "#ifndef EPROTO\n#define EPROTO 71\n#endif\n"
            "#ifndef ETIMEDOUT\n#define ETIMEDOUT 110\n#endif\n"
            "#ifndef EOVERFLOW\n#define EOVERFLOW 75\n#endif\n")
        cls.binary = temp / ("shutdown.exe" if os.name == "nt" else "shutdown")
        production = temp / "shutdown.o"
        command = [compiler, "-std=gnu89", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-Wdeclaration-after-statement", "-Wframe-larger-than=1024",
                   "-I", str(temp), "-I", str(ROOT / "module"), "-c",
                   str(ROOT / "module/shutdown.c"), "-o", str(production)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        command = [compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                   "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
                   "-I", str(ROOT / "module"), str(production),
                   str(Path(__file__).with_name("shutdown_harness.c")), "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_inspect_only_and_all_ten_required(self):
        self.case("validate")

    def test_every_missing_precondition_refuses_before_action(self):
        self.case("preconditions")

    def test_fixed_phase_order_and_explicit_pending_receipts(self):
        self.case("order")

    def test_zero_alone_and_advisory_evidence_are_not_completion(self):
        self.case("evidence")

    def test_receipts_bind_full_operation_identity(self):
        self.case("identity")

    def test_preserves_raw_backend_and_transport_errors(self):
        self.case("errors")

    def test_positive_inspection_return_preserves_exact_failing_subsystem(self):
        self.case("inspect_positive")

    def test_negative_inspection_return_preserves_exact_failing_subsystem(self):
        self.case("inspect_negative")

    def test_partial_failed_attempt_is_undone_before_reopening(self):
        self.case("rollback")

    def test_failed_restore_never_reopens_any_admission(self):
        self.case("rollback_failure")

    def test_late_completion_without_quiescence_blocks_reopen(self):
        self.case("late")

    def test_irreversible_marker_precedes_issue(self):
        self.case("irreversible")

    def test_clock_rechecked_after_completed_hook(self):
        self.case("after_hook")

    def test_shared_deadlines_and_separate_rollback_budget(self):
        self.case("deadline")

    def test_frozen_clock_has_finite_shared_poll_limit(self):
        self.case("frozen")

    def test_nonmonotonic_and_overflowing_clock_rejected(self):
        self.case("clock")

    def test_rollback_deadline_and_errors_require_recovery(self):
        self.case("rollback_deadline")

    def test_each_forward_partial_failure_reverses_all_attempts(self):
        self.case("each_failure")

    def test_finite_poll_budget_is_shared_across_steps(self):
        self.case("shared_polls")

    def test_rollback_pending_has_its_own_finite_poll_budget(self):
        self.case("rollback_pending")

    def test_partial_reopen_failure_preserves_completed_reopen_mask(self):
        self.case("reopen_failure")

    def test_rollback_clock_overflow_is_not_stale_backend_error(self):
        self.case("rollback_overflow")

    def test_inspect_clock_and_invalid_plan_errors(self):
        self.case("invalid")


if __name__ == "__main__":
    unittest.main()
