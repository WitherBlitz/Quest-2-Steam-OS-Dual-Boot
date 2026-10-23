"""Host simulation: selection must never authorize actual OS execution."""
import copy
import contextlib
import importlib.util
import hashlib
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "quest2_boot_selection.py"
ATTEMPT = "1" * 32
OTHER = "2" * 32


class BootSelectionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), "boot-selection reference is missing")
        spec = importlib.util.spec_from_file_location("boot_selection", TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def select(self, state, payload="valid", attempt=ATTEMPT):
        original = copy.deepcopy(state)
        updated, decision = self.tool.select(state, payload, attempt)
        self.assertEqual(state, original, "selection mutated uncommitted input")
        self.assertIs(decision["execution_allowed"], False)
        self.assertIs(decision["auto_boot_allowed"], False)
        self.assertEqual(decision["status"], "SIMULATION_ONLY")
        return updated, decision

    def test_default_linux_prepares_a_pending_attempt_without_execution_permission(self):
        state, decision = self.select(self.tool.initial())
        self.assertEqual(decision["selected_os"], "linux")
        self.assertEqual(state["default_os"], "linux")
        self.assertEqual(state["pending_attempt"], ATTEMPT)

    def test_horizon_override_is_consumed_for_one_boot_only(self):
        state = self.tool.request(self.tool.initial(), "horizon")
        state, decision = self.select(state)
        self.assertEqual(decision["selected_os"], "horizon")
        self.assertIsNone(state["one_shot"])
        state, decision = self.select(state)
        self.assertEqual(decision["selected_os"], "linux")

    def test_linux_button_overrides_a_persisted_horizon_default_once(self):
        state = self.tool.set_default(self.tool.initial(), "horizon")
        state = self.tool.request(state, "linux")
        state, decision = self.select(state)
        self.assertEqual(decision["selected_os"], "linux")
        state = self.tool.complete(state, ATTEMPT, "ready")
        state, decision = self.select(state)
        self.assertEqual(decision["selected_os"], "horizon")

    def test_interrupted_attempt_latches_horizon_and_prevents_retry(self):
        state, _ = self.select(self.tool.initial())
        state, decision = self.select(state, attempt=OTHER)
        self.assertEqual(decision["reason"], "interrupted_linux_attempt")
        self.assertEqual(decision["selected_os"], "horizon")
        self.assertTrue(state["recovery_latched"])
        self.assertIsNone(state["pending_attempt"])
        state = self.tool.request(state, "linux")
        state, decision = self.select(state, attempt=OTHER)
        self.assertEqual(decision["selected_os"], "horizon")
        self.assertEqual(decision["reason"], "recovery_required")

    def test_payload_failures_latch_horizon_even_with_linux_override(self):
        for payload in ("missing", "corrupt", "unverified"):
            with self.subTest(payload=payload):
                state = self.tool.request(self.tool.initial(), "linux")
                state, decision = self.select(state, payload)
                self.assertEqual(decision["selected_os"], "horizon")
                self.assertTrue(state["recovery_latched"])
                self.assertIsNone(state["pending_attempt"])
                self.assertIsNone(state["one_shot"])

    def test_horizon_needs_no_linux_payload(self):
        state = self.tool.request(self.tool.initial(), "horizon")
        state, decision = self.select(state, "missing")
        self.assertEqual(decision["selected_os"], "horizon")
        self.assertFalse(state["recovery_latched"])

    def test_failed_completion_latches_and_ready_requires_exact_pending_token(self):
        state, _ = self.select(self.tool.initial())
        for token in (OTHER, "", None):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.tool.complete(state, token, "ready")
        failed = self.tool.complete(state, ATTEMPT, "failed")
        self.assertTrue(failed["recovery_latched"])
        ready = self.tool.complete(state, ATTEMPT, "ready")
        self.assertIsNone(ready["pending_attempt"])
        with self.assertRaises(ValueError):
            self.tool.complete(ready, ATTEMPT, "ready")

    def test_requests_and_defaults_cannot_erase_a_pending_attempt(self):
        state, _ = self.select(self.tool.initial())
        for operation in (self.tool.request, self.tool.set_default):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation(state, "horizon")
        with self.assertRaises(ValueError): self.tool.recover(state)

    def test_explicit_simulated_recovery_restores_selection_but_never_execution(self):
        state, _ = self.select(self.tool.initial(), "corrupt")
        state = self.tool.recover(state)
        state, decision = self.select(state)
        self.assertEqual(decision["selected_os"], "linux")
        self.assertFalse(state["recovery_latched"])

    def test_malformed_state_cannot_smuggle_authorization_or_a_second_pending_attempt(self):
        for key, value in (("schema", True), ("schema", 2), ("default_os", "other"),
                           ("one_shot", []), ("pending_attempt", "abc"),
                           ("recovery_latched", 1), ("auto_boot_allowed", True)):
            state = self.tool.initial(); state[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.tool.select(state, "valid", ATTEMPT)
        state, _ = self.select(self.tool.initial())
        state["recovery_latched"] = True
        with self.assertRaises(ValueError): self.tool.select(state, "valid", OTHER)

    def test_bad_operation_inputs_are_refused(self):
        for target in (None, True, [], "steamos", ""):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.tool.request(self.tool.initial(), target)
        for payload, token in (("unknown", ATTEMPT), (True, ATTEMPT), ("valid", "bad")):
            with self.subTest(payload=payload, token=token), self.assertRaises(ValueError):
                self.tool.select(self.tool.initial(), payload, token)


class BootStoreTests(unittest.TestCase):
    def setUp(self):
        BootSelectionTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(prefix="qkx-boot-policy-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "model"
        self.assertTrue(hasattr(self.tool, "Store"), "persistent selection store is missing")
        self.store = self.tool.Store(self.path)

    def update(self, state):
        return state, self.tool.report("test_transaction")

    def cli(self, *args, code=3):
        result = subprocess.run([sys.executable, "-B", str(TOOL), "--store", str(self.path), *args],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        decision = json.loads(result.stdout)
        self.assertIs(decision["execution_allowed"], False)
        self.assertIs(decision["auto_boot_allowed"], False)
        return decision

    def test_record_round_trip_and_second_process_use_the_persistent_selection(self):
        self.store.create()
        self.store.transact(lambda s: self.update(self.tool.request(s, "horizon")))
        self.assertEqual(self.tool.Store(self.path).load()["one_shot"], "horizon")
        envelope = json.loads((self.path / "selection.json").read_bytes())
        canonical = json.dumps(envelope["state"], sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(envelope["sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(self.cli("show")["state"]["one_shot"], "horizon")

    def test_init_refuses_existing_directory_without_changing_it(self):
        self.store.create()
        original = (self.path / "selection.json").read_bytes()
        with self.assertRaises(OSError): self.store.create()
        self.assertEqual((self.path / "selection.json").read_bytes(), original)

    def test_stale_lock_and_temporary_file_prevent_selection_and_preserve_record(self):
        self.store.create()
        original = (self.path / "selection.json").read_bytes()
        for name in ("selection.lock", "selection.tmp"):
            with self.subTest(name=name):
                (self.path / name).write_text("interrupted")
                with self.assertRaises((OSError, ValueError)): self.store.load()
                with self.assertRaises((OSError, ValueError)):
                    self.store.transact(lambda s: self.tool.select(s, "valid", ATTEMPT))
                self.assertEqual((self.path / "selection.json").read_bytes(), original)
                self.assertEqual(self.cli("select", "--payload-state", "valid")["selected_os"], "horizon")
                # Cleanup is test-owned; production must never repair this automatically.
                for leftover in ("selection.lock", "selection.tmp"):
                    (self.path / leftover).unlink(missing_ok=True)

    def test_corrupt_duplicate_oversized_and_missing_records_refuse_without_reset(self):
        self.store.create()
        record = self.path / "selection.json"
        valid = record.read_bytes()
        duplicate = b'{"state":{},' + valid[1:]
        for raw in (b"", valid.replace(b'"linux"', b'"other"'), duplicate,
                    b" " * 4097, b"[" * 1500 + b"0" + b"]" * 1500):
            with self.subTest(size=len(raw)):
                record.write_bytes(raw)
                with self.assertRaises(ValueError): self.store.load()
                decision = self.cli("select", "--payload-state", "valid")
                self.assertEqual(decision["reason"], "state_unavailable")
                self.assertEqual(decision["selected_os"], "horizon")
                self.assertEqual(record.read_bytes(), raw)
        record.unlink()
        self.assertEqual(self.cli("select", "--payload-state", "valid")["selected_os"], "horizon")
        self.assertFalse(record.exists())

    def test_symlink_record_is_rejected(self):
        self.store.create()
        record = self.path / "selection.json"
        target = self.path.parent / "outside.json"
        record.replace(target)
        record.symlink_to(target)
        with self.assertRaises(ValueError): self.store.load()

    def test_decoder_recursion_failure_reports_horizon_without_resetting_state(self):
        self.store.create()
        original = (self.path / "selection.json").read_bytes()
        output = io.StringIO()
        # Decoder depth behavior differs across supported Python versions.
        with mock.patch.object(self.tool.json, "loads", side_effect=RecursionError("decoder depth")):
            with contextlib.redirect_stdout(output):
                code = self.tool.main(["--store", str(self.path), "select", "--payload-state", "valid"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["selected_os"], "horizon")
        self.assertEqual((self.path / "selection.json").read_bytes(), original)

    def test_failed_replace_leaves_old_record_and_blocks_reuse(self):
        self.store.create()
        record = self.path / "selection.json"
        original = record.read_bytes()
        with mock.patch.object(self.tool.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaises(OSError):
                self.store.transact(lambda s: self.update(self.tool.request(s, "horizon")))
        self.assertEqual(record.read_bytes(), original)
        self.assertTrue((self.path / "selection.lock").exists())
        self.assertTrue((self.path / "selection.tmp").exists())
        with self.assertRaises(ValueError): self.store.load()

    def test_failed_directory_sync_after_replace_blocks_use_of_new_record(self):
        self.store.create()
        with mock.patch.object(self.tool, "sync_directory", side_effect=[None, OSError("injected sync failure")]):
            with self.assertRaises(OSError):
                self.store.transact(lambda s: self.update(self.tool.request(s, "horizon")))
        self.assertEqual(json.loads((self.path / "selection.json").read_bytes())["state"]["one_shot"], "horizon")
        self.assertTrue((self.path / "selection.lock").exists())
        with self.assertRaises(ValueError): self.store.load()

    def test_invalid_request_does_not_poison_an_unchanged_record(self):
        self.store.create()
        with self.assertRaises(ValueError):
            self.store.transact(lambda s: self.update(self.tool.request(s, "bad")))
        self.assertEqual(self.store.load()["default_os"], "linux")
        self.assertFalse((self.path / "selection.lock").exists())

    def test_overlapping_writer_cannot_overwrite_the_first_transaction(self):
        self.store.create()
        def overlap(state):
            with self.assertRaises(OSError):
                self.tool.Store(self.path).transact(lambda s: self.update(self.tool.request(s, "linux")))
            return self.update(self.tool.request(state, "horizon"))
        self.store.transact(overlap)
        self.assertEqual(self.store.load()["one_shot"], "horizon")

    def test_cli_switch_cycles_keep_default_linux_and_consume_horizon_once(self):
        self.cli("init")
        for _ in range(3):
            self.cli("request", "horizon")
            self.assertEqual(self.cli("select", "--payload-state", "valid")["selected_os"], "horizon")
            selected = self.cli("select", "--payload-state", "valid")
            self.assertEqual(selected["selected_os"], "linux")
            self.cli("complete", "--attempt", selected["attempt"], "--outcome", "ready")
        self.assertEqual(self.cli("show")["state"]["default_os"], "linux")

    def test_cli_interrupted_attempt_requires_explicit_simulated_recovery(self):
        self.cli("init")
        self.cli("select", "--payload-state", "valid")
        self.assertEqual(self.cli("select", "--payload-state", "valid")["reason"], "interrupted_linux_attempt")
        self.assertEqual(self.cli("select", "--payload-state", "valid")["reason"], "recovery_required")
        self.cli("recover")
        self.assertEqual(self.cli("select", "--payload-state", "valid")["selected_os"], "linux")

    def test_cli_linux_request_is_one_shot_with_a_persisted_horizon_default(self):
        self.cli("init")
        self.cli("default", "horizon")
        self.cli("request", "linux")
        selected = self.cli("select", "--payload-state", "valid")
        self.assertEqual(selected["selected_os"], "linux")
        self.cli("complete", "--attempt", selected["attempt"], "--outcome", "ready")
        self.assertEqual(self.cli("select", "--payload-state", "valid")["selected_os"], "horizon")

    def test_cli_stale_completion_does_not_clear_the_current_attempt(self):
        self.cli("init")
        selected = self.cli("select", "--payload-state", "valid")
        self.cli("complete", "--attempt", OTHER, "--outcome", "ready", code=2)
        self.assertEqual(self.cli("show")["state"]["pending_attempt"], selected["attempt"])
        self.assertEqual(self.cli("select", "--payload-state", "valid")["reason"], "interrupted_linux_attempt")


if __name__ == "__main__": unittest.main()
