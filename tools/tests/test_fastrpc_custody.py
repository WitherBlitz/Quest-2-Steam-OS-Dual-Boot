"""Exact transformed FastRPC bodies with deterministic host transport/scheduling."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import quest2_apply_owner_gate as apply_gate

FIXTURE = Path(__file__).with_name("fixtures") / "fastrpc-custody-6bde5fc.c"
FIXTURE_SHA256 = "4143d459e17e191dae20042a5df19942a9ae4499bb14797ee1cbbde5b3f4b906"
MANIFEST = ROOT / "patches/horizon-owner/fastrpc-custody-edits.json"
FUNCTIONS = (
    "context_restore_interrupted", "context_alloc", "context_save_interrupted",
    "context_free", "context_notify_user", "fastrpc_notify_users",
    "fastrpc_notify_users_staticpd_pdr", "fastrpc_file_get", "fastrpc_file_destroy",
    "fastrpc_file_put", "fastrpc_invoke_send", "fastrpc_internal_invoke",
    "fastrpc_device_release", "fastrpc_device_open",
)


def definition(source, name):
    masked = apply_gate._mask_c(source)
    pattern = (r"(?m)^static\s+(?:inline\s+)?(?:int|void)\s+" +
               re.escape(name) + r"\s*\([^;{}]*\)\s*\{")
    matches = list(re.finditer(pattern, masked))
    if len(matches) != 1:
        raise ValueError("unique function definition required: " + name)
    match = matches[0]
    end = apply_gate._scope(apply_gate._events(masked, start=match.end() - 1),
                            stop_at_close=True)
    return source[match.start():end]


class FastRPCCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not cls.compiler:
            raise RuntimeError("set HOST_TEST_CC to a native host C compiler")
        raw = FIXTURE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
            raise RuntimeError("pinned FastRPC custody fixture differs")
        cls.original = raw.decode("latin1")
        entries = json.loads(MANIFEST.read_bytes())["files"]
        if len(entries) != 1 or entries[0]["path"] != "drivers/char/adsprpc.c":
            raise RuntimeError("unexpected source")
        sites = json.loads((MANIFEST.parent / "fastrpc-sites.json").read_bytes())["files"][0]["sites"]
        selected = [site for site in sites if site["name"] in FUNCTIONS]
        cls.wrapped_names = {site["name"] for site in selected}
        source = apply_gate._wrap_file(raw, selected, "fastrpc").decode("latin1")
        for edit in entries[0]["edits"]:
            if source.count(edit["before"]) != 1:
                raise RuntimeError("unique exact source edit required: " + edit["name"])
            source = source.replace(edit["before"], edit["after"])
        cls.transformed = source
        start = source.index("struct qkx_rpc_file_owner {")
        end = source.index("\n#endif\n\nstatic int context_restore_interrupted")
        cls.production = "#ifdef CONFIG_QKX_OWNER_GATE\n" + source[start:end] + "\n#endif\n"
        for name in FUNCTIONS:
            if name in cls.wrapped_names:
                wrapper = definition(source, name)
                cls.production += "\n" + wrapper.split("{", 1)[0].rstrip() + ";\n"
                cls.production += "\n" + definition(source, name + "_qkx_body") + "\n"
                cls.production += "\n" + wrapper + "\n"
            else:
                cls.production += "\n" + definition(source, name) + "\n"
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-fastrpc-custody-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.harness = Path(__file__).with_name("fastrpc_custody_harness.c").read_text()
        cls.binaries = {on: cls.compile(on, "enabled" if on else "disabled") for on in (False, True)}

    @classmethod
    def compile(cls, enabled, label, production=None):
        source = Path(cls.temp.name) / (label + ".c")
        binary = source.with_suffix(".exe" if os.name == "nt" else "")
        source.write_text(cls.harness.replace("/* PRODUCTION */", production or cls.production))
        result = subprocess.run([
            cls.compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
            "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            *(["-DCONFIG_QKX_OWNER_GATE=1"] if enabled else []),
            str(source), "-o", str(binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return binary

    def case(self, name, enabled_only=False):
        for enabled, binary in self.binaries.items():
            if enabled_only and not enabled:
                continue
            with self.subTest(owner_gate=enabled):
                result = subprocess.run([str(binary), name], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unsubmitted_context_finalization_runs_in_driver_worker(self):
        self.case("unsubmitted")

    def test_terminal_rx_releases_async_custody_after_operation(self):
        self.case("terminal")

    def test_normal_reply_publishes_terminal_before_waking_waiter(self):
        self.case("wake_terminal", enabled_only=True)

    def test_complete_signal_keeps_callback_lease_across_waiter_wake(self):
        self.case("wake_signal", enabled_only=True)

    def test_sync_terminal_rx_before_send_return_keeps_operation(self):
        self.case("sync_reply")

    def test_sync_terminal_proof_is_preserved_with_failed_send_return(self):
        self.case("sync_reply_send_error")

    def test_duplicate_retirement_has_one_physical_finalizer(self):
        self.case("repeat_retire")

    def test_early_response_keeps_remote_lease(self):
        self.case("early")

    def test_user_hint_keeps_remote_lease(self):
        self.case("hint")

    def test_memory_poll_marker_can_complete(self):
        self.case("poll")

    def test_send_error_retains_unknown_context_and_parent(self):
        self.case("send_error")

    def test_transport_loss_and_late_response_keep_unknown(self):
        self.case("loss")

    def test_pd_loss_and_late_response_keep_unknown(self):
        self.case("pd_loss")

    def test_loss_before_send_blocks_admission(self):
        self.case("presend_loss")

    def test_loss_during_parent_acquisition_is_rechecked_before_registration(self):
        self.case("loss_during_registration", enabled_only=True)

    def test_registry_loss_before_pending_publication_blocks_send(self):
        self.case("loss_before_pending", enabled_only=True)

    def test_interrupted_restore_claims_same_operation(self):
        self.case("interrupt_restore")

    def test_closed_file_refuses_interrupted_restore_before_pointer_return(self):
        self.case("restore_closed")

    def test_close_retires_completed_interrupted_context(self):
        self.case("interrupt_close", enabled_only=True)

    def test_close_seals_gets_while_child_retains_parent(self):
        self.case("close_blocks_get")

    def test_context_sidecar_failure_precedes_publication(self):
        self.case("allocation_failure", enabled_only=True)

    def test_file_sidecar_failure_precedes_publication(self):
        self.case("file_allocation_failure", enabled_only=True)

    def test_prepublication_copy_failure_uses_owner_worker(self):
        self.case("copy_failure")

    def test_failed_process_release_preserves_error_and_file(self):
        self.case("release_error")

    def test_concurrent_loss_during_control_phase_prevents_final_release(self):
        self.case("release_loses_transport")

    def test_control_context_and_last_child_worker_progress(self):
        self.case("own_control_context")

    def test_loss_and_late_callback_do_not_copy_expired_stack_args(self):
        self.case("loss_stack")

    def test_original_physical_free_body_fails_retention_contract(self):
        guard = "\tif (!qkx_rpc_ctx_retire(ctx))\n\t\treturn;"
        self.assertEqual(self.production.count(guard), 1)
        binary = self.compile(True, "original-free-body", self.production.replace(guard, ""))
        result = subprocess.run([str(binary), "terminal"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remote lease keeps context", result.stderr)

    def test_missing_presend_lease_fails_synchronous_callback_contract(self):
        guard = "\terr = qkx_rpc_ctx_send_begin(ctx);"
        self.assertEqual(self.production.count(guard), 1)
        binary = self.compile(True, "missing-presend-lease", self.production.replace(guard, "\terr = 0;"))
        result = subprocess.run([str(binary), "sync_reply"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synchronous reply delivered", result.stderr)

    def test_no_published_struct_layout_changes(self):
        entries = json.loads(MANIFEST.read_bytes())["files"]
        self.assertEqual(entries[0]["sha256"],
                         "f479739662bd427b577e493b518240f3c1ec0b7eb0b1cdf0f3da769efea6d49b")
        self.assertNotIn("struct fastrpc_file {", self.transformed)
        self.assertNotIn("struct smq_invoke_ctx {", self.transformed)
        self.assertIn("if (!qkx_rpc_ctx_completed(ctx))", self.transformed)
        self.assertIn("qkx_rpc_ctx_complete(ctx, false, true);", self.transformed)


if __name__ == "__main__":
    unittest.main()
