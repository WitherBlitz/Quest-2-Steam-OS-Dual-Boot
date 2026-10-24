"""Execute pinned CRM queue/timer and parent-finalizer source bodies."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "patches/horizon-owner/camera-custody-edits.json"
FIXTURES = Path(__file__).with_name("fixtures")
PINNED = {
    "workq": "01d89acdcac645f0997902c3a81ae736bb79e07540b148a4c75cd7386d82b75a",
    "timer": "cbd3d3f4df4eeb82b8c49a2ae0c750cde8278a58fe792cd7aff8d2a432bf0d65",
    "core": "69c4c9b6de55118e735f53ef049f1d8903d7974994523ca5051755e4626215e1",
    "dev": "102fafbac93a11d561acbef292970094c3196c45a172e9b365dad64995f848a2",
}
PREFIX = "techpack/camera/drivers/cam_req_mgr/"


def function(source, name):
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: re.sub(r"[^\n]", " ", m.group()), source, flags=re.S)
    match = re.search(r"(?m)^(?:static )?(?:int|int32_t|void|long|struct crm_workq_task\s*\*)\s*" +
                      name + r"\s*\([^;{}]*\)\s*\{", masked)
    if not match:
        raise AssertionError("missing real function " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    return source[match.start():end]


class CameraCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("set HOST_TEST_CC to a native C compiler")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-camera-custody-")
        cls.addClassCleanup(cls.temp.cleanup)
        original = {}
        for name, expected in PINNED.items():
            data = (FIXTURES / f"camera-{name}-custody-6bde5fc.c").read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise AssertionError("pinned source fixture changed: " + name)
            original[PREFIX + f"cam_req_mgr_{name}.c"] = data.decode("latin-1")
        patched = dict(original)
        if MANIFEST.exists():
            for item in json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]:
                if item["path"] not in patched:
                    continue
                source = patched[item["path"]]
                if hashlib.sha256(source.encode("latin-1")).hexdigest() != item["sha256"]:
                    raise AssertionError("source pin mismatch")
                for edit in item["edits"]:
                    if source.count(edit["before"]) != 1:
                        raise AssertionError("nonunique fixture anchor " + edit["name"])
                    source = source.replace(edit["before"], edit["after"])
                patched[item["path"]] = source
        harness = Path(__file__).with_name("camera_custody_harness.c").read_text(encoding="utf-8")
        cls.binaries = {}
        for variant, sources, enabled in (("stock", original, False),
                                           ("disabled", patched, False),
                                           ("enabled", patched, True)):
            workq, timer, core, dev = (sources[PREFIX + f"cam_req_mgr_{n}.c"]
                                      for n in ("workq", "timer", "core", "dev"))
            pieces = [re.sub(r"(?m)^#include[^\n]*\n", "", workq),
                      re.sub(r"(?m)^#include[^\n]*\n", "", timer),
                      "static struct cam_req_mgr_core_device *g_crm_core_dev;\n"
                      "static struct cam_req_mgr_core_link g_links[MAXIMUM_LINKS_PER_SESSION];"]
            for marker in re.findall(r"/\* QKX_CAMERA_CUSTODY_CORE_BEGIN \*/(.*?)/\* QKX_CAMERA_CUSTODY_CORE_END \*/",
                                     core, flags=re.S):
                pieces.append(marker)
            for name in ("__cam_req_mgr_disconnect_link", "__cam_req_mgr_unlink",
                         "cam_req_mgr_destroy_session", "cam_req_mgr_unlink",
                         "cam_req_mgr_handle_core_shutdown", "cam_req_mgr_create_session",
                         "cam_req_mgr_cb_notify_timer", "__cam_req_mgr_sof_freeze",
                         "cam_req_mgr_process_workq_link_worker", "cam_req_mgr_link",
                         "cam_req_mgr_link_v2", "cam_req_mgr_link_control"):
                pieces.append(function(core, name))
            for marker in re.findall(r"/\* QKX_CAMERA_CUSTODY_DEV_BEGIN \*/(.*?)/\* QKX_CAMERA_CUSTODY_DEV_END \*/",
                                     dev, flags=re.S):
                pieces.append(marker)
            for name in ("cam_req_mgr_open", "cam_req_mgr_close"):
                pieces.append(function(dev, name))
            source = Path(cls.temp.name) / (variant + ".c")
            source.write_text(harness.replace("/* REAL_FUNCTIONS */", "\n\n".join(pieces)),
                              encoding="utf-8")
            binary = source.with_suffix(".exe" if os.name == "nt" else "")
            command = [compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []),
                       "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                       "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-variable",
                       "-Wno-unused-but-set-variable", "-Wno-sign-compare"]
            if enabled:
                command += ["-DCONFIG_QKX_OWNER_GATE"]
            if MANIFEST.exists() and variant != "stock":
                command += ["-DQKX_CUSTODY_PATCHED"]
            if enabled and "struct qkx_session_custody" in core:
                command += ["-DQKX_SESSION_CUSTODY"]
            result = subprocess.run(command + [str(source), "-o", str(binary)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[variant] = binary

    def case(self, name, variant="enabled"):
        result = subprocess.run([str(self.binaries[variant]), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_borrowed_ticket_retains_pool_until_cancel(self):
        self.assertEqual(self.case("borrow"),
                         {"first": -16, "kept": 1, "cancel": -108, "last": 0, "destroyed": 1})

    def test_running_ticket_retains_pool_after_pending_count_zero(self):
        result = self.case("running")
        self.assertEqual({k: v for k, v in result.items() if k != "wakes"},
                         {"first": -16, "pending_in_cb": 0, "close_in_cb": -16,
                          "freed_in_cb": 0, "calls": 1, "ret": -5, "last": 0, "destroyed": 1})
        self.assertGreater(result["wakes"], 0)

    def test_duplicate_return_quarantines_without_double_pool_return(self):
        self.assertEqual(self.case("duplicate"),
                         {"first": 0, "duplicate": -117, "close": -117, "free": 3})

    def test_closed_queue_rejects_new_borrow(self):
        self.assertEqual(self.case("closed"), {"close": 0, "denied": 1})

    def test_timer_registration_retains_queue_through_sync_cancel(self):
        self.assertEqual(self.case("timer"),
                         {"init": 0, "first": -16, "stop": 0, "last": 0,
                          "extra_arms": 0, "syncs": 1, "null": 1})

    def test_default_timer_callback_is_declared_and_rearms_owned_timer(self):
        self.assertEqual(self.case("timer_default"),
                         {"init": 0, "arms": 2, "stop": 0, "last": 0,
                          "null": 1, "destroyed": 1})

    def test_timer_cancel_from_interrupt_retains_registration(self):
        self.assertEqual(self.case("timer_irq"),
                         {"stop": -11, "retained": 1, "syncs": 0, "close": -16})

    def test_missing_job_returns_borrowed_ticket_once(self):
        self.assertEqual(self.case("job_missing"), {"rc": -22, "free": 3, "close": 0})

    def test_real_callback_returns_lease_on_ready_error_and_closed_paths(self):
        self.assertEqual(self.case("actual_callback"),
                         {"ready": 0, "inactive": -1, "users": 0, "closed": -22})

    def test_real_watchdog_dispatch_retains_link_until_queued_callback_finishes(self):
        self.assertEqual(self.case("watchdog"),
                         {"first": -16, "early_free": 0, "pending": 1, "callbacks": 1,
                          "syncs": 1, "parent_after": 0, "fh_cleanup": 1})

    def test_borrowed_task_retains_link_session_and_handle_until_retry(self):
        self.assertEqual(self.case("parent"),
                         {"first": -16, "parent_kept": 1, "slot_kept": 1,
                          "early_free": 0, "parent_after": 0, "frees": 1, "destroyed": 1})

    def test_failed_disconnect_preserves_registration_and_parent(self):
        self.assertEqual(self.case("disconnect"),
                         {"rc": -5, "dev_kept": 1, "parent_kept": 1,
                          "slot_kept": 1, "destroyed": 0})

    def test_callback_lookup_and_close_share_lease_lock(self):
        self.assertEqual(self.case("callback_lease"),
                         {"rc": -16, "denied": 1, "kept": 1, "after": 0})

    def test_root_close_retains_actual_fh_and_parents_until_system_finalizer(self):
        self.assertEqual(self.case("root_close"),
                         {"first": -16, "file_detached": 1, "parent_kept": 1,
                          "early_cleanup": 0, "open": -16, "create": -108,
                          "fh_cleanup": 1, "handles": 1, "memory": 1, "parent_after": 0})

    def test_failed_unregister_retains_root_fh_and_blocks_reopen(self):
        self.assertEqual(self.case("root_failure"),
                         {"first": -5, "parent_kept": 1, "fh_cleanup": 0,
                          "handles": 0, "memory": 0, "open": -16})

    def test_last_put_during_active_worker_is_consumed_by_recheck(self):
        self.assertEqual(self.case("wake_active"),
                         {"hits": 1, "pending_after_first": 0, "dirty_after": 0,
                          "running_after": 0, "scheduled": 1})

    def test_last_put_after_worker_idle_transition_requeues_worker(self):
        self.assertEqual(self.case("wake_idle"),
                         {"hits": 1, "pending_after_first": 1, "dirty_after": 0,
                          "running_after": 0, "scheduled": 2})

    def test_stopping_finalizer_rejects_late_notifier_before_cancel(self):
        self.assertEqual(self.case("wake_stopped"),
                         {"dirty": 0, "running": 0, "pending": 0, "scheduled": 0})

    def test_worker_publication_cannot_race_stopping_and_cancel(self):
        self.assertEqual(self.case("wake_publish_stop"),
                         {"stopped": 1, "late_work": 0, "scheduled": 1})

    def test_real_v1_link_rejects_parent_sealed_by_deferred_session_close(self):
        self.assertEqual(self.case("session_admission_v1"),
                         {"close": -16, "link": -108, "reserves": 0, "children": 1})

    def test_real_v2_link_rejects_parent_sealed_by_deferred_session_close(self):
        self.assertEqual(self.case("session_admission_v2"),
                         {"close": -16, "link": -108, "reserves": 0, "children": 1})

    def test_sealed_parent_retries_after_last_child_explicit_unlink(self):
        self.assertEqual(self.case("session_last_child"),
                         {"close": -16, "unlink": 0, "before": 1,
                          "children": 0, "after": 0, "handles": 2})

    def test_failed_session_handle_destruction_retains_seal_without_retry(self):
        self.assertEqual(self.case("session_handle_failure"),
                         {"close": -5, "v1": -108, "v2": -108, "parent": 1,
                          "reserves": 0, "destroy_calls": 1})

    def test_missing_session_owner_fails_closed(self):
        self.assertEqual(self.case("session_missing_owner"),
                         {"v1": -117, "v2": -117, "close": -117,
                          "parent": 1, "reserves": 0})

    def test_session_owner_requires_pointer_and_full_handle_match(self):
        self.assertEqual(self.case("session_owner_handle_mismatch"),
                         {"v1": -117, "v2": -117, "close": -117,
                          "parent": 1, "reserves": 0})

    def test_real_link_variants_still_admit_live_owned_session(self):
        for name in ("session_open_v1", "session_open_v2"):
            self.assertEqual(self.case(name),
                             {"rc": 0, "reserves": 1, "children": 1, "published": 1})

    def test_sidecar_allocation_failure_never_publishes_handle_or_session(self):
        self.assertEqual(self.case("session_alloc_failure"),
                         {"first": -12, "unpublished": 1234, "early_handles": 0,
                          "owner_empty": 1, "next": 0, "handles": 1})

    def test_unpublished_handle_failure_frees_session_and_sidecar(self):
        self.assertEqual(self.case("session_unpublished_handle_failure"),
                         {"first": -28, "owner_empty": 1, "frees": 2,
                          "next": 0, "handles": 2})

    def test_session_epoch_overflow_fails_before_handle_publication(self):
        self.assertEqual(self.case("session_epoch_overflow"),
                         {"rc": -75, "handle": 1234, "created": 0, "published": 0})

    def test_stale_session_retry_cannot_destroy_reused_handle(self):
        self.assertEqual(self.case("session_stale_retry"),
                         {"rc": -2, "new_parent": 1, "destroy_calls": 1})

    def test_real_link_control_rejects_parent_seal_before_child_close(self):
        self.assertEqual(self.case("session_control"), {"rc": -108, "timer_arms": 0})

    def test_disabled_paths_match_stock_destruction(self):
        for name in ("borrow", "closed", "timer", "parent", "disconnect", "root_close", "root_failure",
                     "session_open_v1", "session_open_v2", "session_control",
                     "session_unpublished_handle_failure"):
            self.assertEqual(self.case(name, "disabled"), self.case(name, "stock"))

    def test_real_wrappers_then_cleanup_then_custody_anchors(self):
        if not MANIFEST.exists():
            self.fail("camera custody production edits are absent")
        spec = importlib.util.spec_from_file_location("custody_applier", ROOT / "tools/quest2_apply_owner_gate.py")
        applier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(applier)
        entry = json.loads((ROOT / "patches/horizon-owner/camera-sites.json").read_text())
        custody = json.loads(MANIFEST.read_text())
        for item in custody["files"]:
            if not item["path"].endswith(".c"):
                continue
            name = Path(item["path"]).stem.removeprefix("cam_req_mgr_")
            data = (FIXTURES / f"camera-{name}-custody-6bde5fc.c").read_bytes()
            sites = next((f["sites"] for f in entry["files"] if f["path"] == item["path"]), [])
            transformed = applier._wrap_file(data, sites, "camera") if sites else data
            source = transformed.decode("latin-1")
            for edit in item["edits"]:
                self.assertEqual(source.count(edit["before"]), 1, edit["name"])
                source = source.replace(edit["before"], edit["after"])


if __name__ == "__main__":
    unittest.main()
