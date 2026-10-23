"""Decode sparse, explicit CSL scalar memory without device access."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = 0x7000000000
GLOBAL = BASE + 0x865f0
PID = 42


class SparseMemory:
    def __init__(self):
        self.data = {}
        self.calls = []

    def zero(self, address, count):
        self.data.update((address + i, 0) for i in range(count))

    def word(self, address, value):
        for i, byte in enumerate(struct.pack("<I", value & 0xffffffff)):
            self.data[address + i] = byte

    def read(self, address, count):
        self.calls.append((address, count))
        try:
            return bytes(self.data[address + i] for i in range(count))
        except KeyError as exc:
            raise AssertionError("decoder read outside scalar fixture") from exc


def fixture(active=True):
    memory = SparseMemory()
    for offset in (0x10, 0x40, 0x44, 0x1d8dc8):
        memory.zero(GLOBAL + offset, 4)
    memory.word(GLOBAL + 0x10, 1)
    memory.zero(GLOBAL + 0x1d8dd0 + 0x10, 4)
    for slot in range(16):
        session = GLOBAL + 0x48 + slot * 0x1d8d8
        memory.zero(session + 0x10, 0x18)
        memory.zero(session + 0x1cc28, 0x10)
    if not active:
        return memory
    session = GLOBAL + 0x3b1f8  # slot 2, not the first session
    memory.word(GLOBAL + 0x40, 9)  # references deliberately differ from counts
    memory.word(GLOBAL + 0x44, 1)
    memory.word(GLOBAL + 0x1d8dc8, 6)
    for offset, value in ((0x10, 1), (0x14, 2), (0x18, 1),
                          (0x1c, 7), (0x20, 3), (0x24, 0x1202),
                          (0x1cc28, 1), (0x1cc2c, 0x3409),
                          (0x1cc30, PID), (0x1cc34, 43)):
        memory.word(session + offset, value)
    for slot in range(128):
        acquisition = session + 0x28 + slot * 0x188
        memory.zero(acquisition + 0x10, 0x30)
        link = session + 0xc428 + slot * 0x210
        memory.zero(link, 4)
        memory.zero(link + 0x204, 12)
    acquisition = session + 0x4c0
    for offset, value in ((0x10, 1), (0x14, 7), (0x18, 0x5603),
                          (0x1c, 0x1202), (0x20, 4), (0x24, -1),
                          (0x28, 2), (0x2c, 8), (0x30, 5),
                          (0x34, 11), (0x80, 0)):
        memory.word(acquisition + offset, value)
    link = session + 0xd6b8  # sparse link slot 9
    memory.word(link, 0x3409)
    memory.word(link + 4, 0x5603)
    memory.word(link + 0x204, 1)
    memory.word(link + 0x208, 1)
    memory.word(link + 0x20c, 1)
    physical = GLOBAL + 0x1da688  # physical slot 5
    for offset, value in ((0, 5), (0x10, 1), (0x48, 7),
                          (0xc4, 12), (0x2dc, 6), (0x384, 1)):
        memory.word(physical + offset, value)
    memory.word(GLOBAL + 0x1d8dd0 + 0x10, 1)
    memory.word(GLOBAL + 0x1d8dd0 + 0xc4, 7)
    return memory


class CSLOwnershipTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_csl_ownership.py"
        self.assertTrue(path.is_file(), "offline CSL decoder missing")
        spec = importlib.util.spec_from_file_location("csl_ownership", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def collect(self, memory):
        return self.module().collect_csl(memory.read, BASE, PID)

    def test_sparse_handles_bind_to_full_session_and_physical_fd(self):
        memory = fixture()
        result = self.collect(memory)
        self.assertEqual(result["global"], {"state": 1, "reference_count": 9,
                         "open_session_count": 1, "physical_device_count": 6})
        session, = result["sessions"]
        self.assertEqual((session["slot"], session["handle"], session["creator_pid"],
                          session["creator_tid"], session["reference_count"],
                          session["open_reference_count"]), (2, 0x1202, 42, 43, 7, 3))
        device, = session["devices"]
        self.assertEqual((device["slot"], device["handle"], device["session_handle"],
                          device["physical_index"], device["retained_index"]),
                         (3, 0x5603, 0x1202, 5, -1))
        self.assertEqual(session["links"], [{"slot": 9, "handle": 0x3409,
                         "device_count": 1, "device_handles": [0x5603],
                         "stream_off_flags": 1, "stream_enabled": 1}])
        self.assertEqual(result["physical_devices"], [{"index": 5, "state": 1,
                         "type": 7, "fd": 12, "reference_count": 6, "stream_flag": 1}])
        self.assertEqual(result["crm_fd"], 7)
        self.assertLess(sum(count for _, count in memory.calls), 12000)
        self.assertNotIn(str(GLOBAL), json.dumps(result))

    def test_empty_instance_avoids_device_and_link_memory(self):
        result = self.collect(fixture(False))
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["physical_devices"], [])
        self.assertIsNone(result["crm_fd"])

    def test_last_session_can_share_physical_device_without_count_inference(self):
        memory = fixture()
        first = GLOBAL + 0x3b1f8
        last = GLOBAL + 0x48 + 15 * 0x1d8d8
        for address, value in list(memory.data.items()):
            if first <= address < first + 0x1d8d8:
                memory.data[last + address - first] = value
        for offset, value in ((0x14, 15), (0x24, 0x120f),
                              (0x4c0 + 0x18, 0x7803), (0x4c0 + 0x1c, 0x120f),
                              (0xd6b8, 0x4509), (0xd6b8 + 4, 0x7803),
                              (0x1cc2c, 0x4509)):
            memory.word(last + offset, value)
        memory.word(GLOBAL + 0x44, 2)
        result = self.collect(memory)
        self.assertEqual([session["slot"] for session in result["sessions"]], [2, 15])
        self.assertEqual(len(result["physical_devices"]), 1)
        self.assertEqual(result["physical_devices"][0]["reference_count"], 6)

    def test_foreign_creator_is_explicitly_rejected(self):
        memory = fixture()
        memory.word(GLOBAL + 0x3b1f8 + 0x1cc30, 99)
        with self.assertRaisesRegex(ValueError, "creator"):
            self.collect(memory)

    def test_reused_full_handles_and_wrong_slots_are_rejected(self):
        session = GLOBAL + 0x3b1f8
        for offset, value in ((0x14, 1), (0x24, 0x1201),
                              (0x4c0 + 0x18, 0x5604),
                              (0x4c0 + 0x1c, 0x9902),
                              (0xd6b8, 0x3408), (0xd6b8 + 4, 0x9903)):
            memory = fixture()
            memory.word(session + offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.collect(memory)

    def test_invalid_counts_and_unresolvable_device_index_are_rejected(self):
        session = GLOBAL + 0x3b1f8
        cases = [(GLOBAL + 0x1d8dc8, 129), (GLOBAL + 0x44, 17),
                 (session + 0x1cc28, 129), (session + 0xd6b8 + 0x204, 129),
                 (session + 0xd6b8 + 0x204, 0),
                 (session + 0x4c0 + 0x30, 6),
                 (session + 0x4c0 + 0x30, 128),
                 (GLOBAL + 0x1da688, 4)]
        for address, value in cases:
            memory = fixture()
            memory.word(address, value)
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.collect(memory)

    def test_duplicate_link_devices_and_sparse_count_disagreement_are_rejected(self):
        session = GLOBAL + 0x3b1f8
        memory = fixture()
        memory.word(session + 0xd6b8 + 0x204, 2)
        memory.word(session + 0xd6b8 + 8, 0x5603)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.collect(memory)
        memory = fixture()
        memory.word(session + 0x1cc28, 2)
        with self.assertRaisesRegex(ValueError, "sparse link count"):
            self.collect(memory)

    def test_empty_handles_with_live_state_or_links_fail_closed(self):
        session = GLOBAL + 0x3b1f8
        for offset in (0x24, 0x4c0 + 0x18, 0xd6b8):
            memory = fixture()
            memory.word(session + offset, 0)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.collect(memory)

    def test_fd_values_are_signed_without_becoming_release_receipts(self):
        memory = fixture()
        memory.word(GLOBAL + 0x1da688 + 0xc4, -1)
        memory.word(GLOBAL + 0x1d8dd0 + 0xc4, -1)
        result = self.collect(memory)
        self.assertEqual(result["physical_devices"][0]["fd"], -1)
        self.assertEqual(result["crm_fd"], -1)
        self.assertFalse(result.get("hardware_shutdown_validated", False))

    def test_invalid_reader_results_and_io_errors_fail_closed(self):
        module = self.module()
        for value in (b"", b"\0" * 3, b"\0" * 5, "0000", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.collect_csl(lambda address, count: value, BASE, PID)
        def failed_read(address, count):
            raise OSError("unmapped")
        with self.assertRaises(ValueError):
            module.collect_csl(failed_read, BASE, PID)
        memory = fixture()
        def short_link_read(address, count):
            raw = memory.read(address, count)
            return raw[:-1] if address == GLOBAL + 0x3b1f8 + 0xd6bc else raw
        with self.assertRaisesRegex(ValueError, "length"):
            module.collect_csl(short_link_read, BASE, PID)

    def test_argument_types_and_address_extent_are_checked_before_read(self):
        module = self.module()
        memory = fixture(False)
        for base, pid in ((True, PID), (-1, PID), (1 << 56, PID),
                          ((1 << 56) - 0x100000, PID), (BASE, True),
                          (BASE, 0), (BASE, -1), (BASE, 1 << 31)):
            with self.subTest(base=base, pid=pid), self.assertRaises(ValueError):
                module.collect_csl(memory.read, base, pid)
        self.assertEqual(memory.calls, [])


if __name__ == "__main__":
    unittest.main()
