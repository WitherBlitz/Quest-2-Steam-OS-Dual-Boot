"""Execute pinned camera cleanup bodies with failing IOMMU/DMA boundaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
MANIFEST = ROOT / "patches/horizon-owner/camera-lifetime-edits.json"
SOURCES = {
    "techpack/camera/drivers/cam_smmu/cam_smmu_api.c": "camera-smmu-lifetime-6bde5fc.c",
    "techpack/camera/drivers/cam_req_mgr/cam_mem_mgr.c": "camera-memory-lifetime-6bde5fc.c",
}
FIXTURE_HASHES = {
    "camera-smmu-lifetime-6bde5fc.c": "45635af633e5e47ab5259659018d178c04fefd7fbf9bf9eff739eccacda722dd",
    "camera-memory-lifetime-6bde5fc.c": "a282546e706a066d131221251f8f7f018592e9578b7216eba773330979a1d027",
}


def function(source, name):
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda match: re.sub(r"[^\n]", " ", match.group()), source, flags=re.S)
    match = re.search(r"(?m)^(?:static )?(?:int|int32_t|void|long)\s+" + name +
                      r"\s*\([^;{}]*\)\s*\{", masked)
    if not match:
        raise AssertionError(f"missing real source function {name}")
    end, depth = match.end(), 1
    while depth:
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    return source[match.start():end]


class CameraLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("cc") or shutil.which("clang")
        if not compiler:
            raise RuntimeError("set HOST_TEST_CC to a native C compiler")
        cls.temp = tempfile.TemporaryDirectory(prefix="qkx-camera-lifetimes-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binaries = {}
        for name, expected in FIXTURE_HASHES.items():
            if hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() != expected:
                raise AssertionError(f"pinned camera source fixture changed: {name}")
        originals = {path: (FIXTURES / name).read_text(encoding="utf-8")
                     for path, name in SOURCES.items()}
        patched = dict(originals)
        if MANIFEST.is_file():
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            for item in manifest["files"]:
                source = patched[item["path"]]
                for edit in item["edits"]:
                    if source.count(edit["before"]) != 1:
                        raise AssertionError(f"nonunique fixture anchor: {edit['name']}")
                    source = source.replace(edit["before"], edit["after"])
                patched[item["path"]] = source
        smmu_names = ["cam_smmu_unmap_buf_and_remove_from_list", "cam_smmu_release_sec_heap",
                      "cam_smmu_clean_user_buffer_list", "cam_smmu_clean_kernel_buffer_list",
                      "cam_smmu_destroy_handle", "cam_smmu_create_add_handle_in_table"]
        memory_names = ["cam_mem_mgr_init", "cam_mem_mgr_cleanup_table", "cam_mem_mgr_deinit",
                        "cam_mem_util_unmap", "cam_mem_put_cpu_buf", "cam_mem_mgr_release",
                        "cam_mem_mgr_release_mem", "cam_mem_mgr_free_memory_region"]
        harness = Path(__file__).with_name("camera_lifetime_harness.c").read_text(encoding="utf-8")
        for variant, sources, enabled in (("stock", originals, False),
                                           ("disabled", patched, False),
                                           ("enabled", patched, True)):
            smmu, memory = (sources[path] for path in SOURCES)
            pieces = []
            for source, names, declaration in (
                    (smmu, smmu_names, "static struct cam_iommu_cb_set iommu_cb_set;"),
                    (memory, memory_names, "static struct cam_mem_table tbl;\n"
                     "static atomic_t cam_mem_mgr_state = ATOMIC_INIT(CAM_MEM_MGR_UNINITIALIZED);")):
                pieces.append(declaration)
                marker = re.search(r"/\* QKX_CAMERA_LIFETIME_BEGIN \*/(.*?)/\* QKX_CAMERA_LIFETIME_END \*/",
                                   source, flags=re.S)
                if marker:
                    pieces.append(marker.group(1))
                pieces.extend(function(source, name) for name in names)
            source = Path(cls.temp.name) / f"{variant}.c"
            binary = source.with_suffix(".exe" if os.name == "nt" else "")
            source.write_text(harness.replace("/* REAL_FUNCTIONS */", "\n\n".join(pieces)), encoding="utf-8")
            command = [compiler, *(["-fuse-ld=lld"] if os.name == "nt" else []), "-std=gnu11",
                       "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                       "-Wno-unused-parameter", "-Wno-unused-variable",
                       "-Wno-unused-but-set-variable", "-Wno-sign-compare"]
            if enabled:
                command += ["-DCONFIG_QKX_OWNER_GATE"]
            if MANIFEST.is_file() and variant != "stock":
                command += ["-DQKX_CLEANUP_PATCHED"]
            result = subprocess.run(command + [str(source), "-o", str(binary)], capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[variant] = binary

    def case(self, name, variant="enabled"):
        result = subprocess.run([str(self.binaries[variant]), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_shared_short_unmap_retains_mapping_and_never_retries_prefix(self):
        self.assertEqual(self.case("shared_short"),
                         {"rc": -5, "again": -5, "unmaps": 1, "puts": 0, "frees": 0, "linked": 1})

    def test_zero_and_overreported_unmap_quarantine(self):
        for name in ("shared_zero", "shared_over"):
            self.assertEqual(self.case(name),
                             {"rc": -5, "again": -5, "unmaps": 1, "puts": 0, "frees": 0, "linked": 1})

    def test_iova_allocator_failure_retains_mapping_without_retry(self):
        self.assertEqual(self.case("iova_failure"),
                         {"rc": -12, "again": -12, "unmaps": 1, "puts": 0, "frees": 0, "linked": 1})

    def test_successful_shared_cleanup_still_releases_exactly_once(self):
        self.assertEqual(self.case("shared_success"),
                         {"rc": 0, "puts": 1, "frees": 1, "linked": 0})

    def test_secondary_heap_short_unmap_preserves_backing_and_allocated_flag(self):
        self.assertEqual(self.case("secondary_short"),
                         {"rc": -5, "again": -5, "unmaps": 1, "puts": 0, "allocated": 1})

    def test_failed_forced_cleanup_preserves_handle_and_callbacks(self):
        self.assertEqual(self.case("destroy_failure"),
                         {"rc": -5, "handle": 17, "callbacks": 2, "puts": 0, "linked": 1})

    def test_poisoned_handle_cannot_be_acquired_again(self):
        self.assertEqual(self.case("reacquire_failure"), {"rc": -5, "handle": 17, "returned": -1})

    def test_deinit_preserves_live_table_and_reinit_cannot_erase_it(self):
        self.assertEqual(self.case("memory_deinit"),
                         {"state": 1, "active": 1, "bitmap": 1, "puts": 0, "lock_destroyed": 0,
                          "init_rc": -16})

    def test_memory_final_put_failure_preserves_slot_and_error(self):
        self.assertEqual(self.case("memory_release"),
                         {"rc": -5, "again": -5, "active": 1, "handle": 1, "puts": 0, "hw_unmaps": 1})

    def test_secondary_heap_free_refuses_retained_reference(self):
        self.assertEqual(self.case("memory_secondary_retained"),
                         {"rc": -16, "refs": 2, "secondary_unmaps": 0, "active": 1})

    def test_cpu_end_access_failure_does_not_replay_kunmap_or_release_backing(self):
        self.assertEqual(self.case("memory_cpu_failure"),
                         {"rc": -12, "again": -12, "active": 1, "cpu_address": 0,
                          "puts": 0, "hw_unmaps": 0, "cpu_unmaps": 1})

    def test_nonfinal_kernel_release_accepts_remaining_reference(self):
        self.assertEqual(self.case("memory_nonfinal_release"),
                         {"rc": 0, "refs": 1, "active": 1, "hw_unmaps": 0})
        self.assertEqual(self.case("memory_nonfinal_release", "stock")["rc"], -22)

    def test_secondary_heap_error_reaches_memory_caller_without_consuming_ref(self):
        self.assertEqual(self.case("memory_secondary_failure"),
                         {"rc": -5, "refs": 1, "active": 1, "puts": 0})

    def test_disabled_feature_preserves_original_failure_semantics(self):
        for name in ("shared_short", "secondary_short", "destroy_failure", "memory_deinit",
                     "memory_release", "memory_secondary_retained"):
            self.assertEqual(self.case(name, "disabled"), self.case(name, "stock"))
        self.assertEqual(self.case("shared_short", "stock")["puts"], 1)
        self.assertEqual(self.case("secondary_short", "stock")["allocated"], 0)
        self.assertEqual(self.case("destroy_failure", "stock")["handle"], -1)


if __name__ == "__main__":
    unittest.main()
