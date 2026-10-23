"""Sparse scalar fixtures for rpcmem and current PLS application membership."""
import importlib
import struct
import unittest

BASE = 0x7000000000
NODE = 0x7100000000
DESC = 0x7200000000
HASH = 0x7300000000
TAG = 0xab00000000000000
CDSP = "1b5f80e8128b10e148f53aa61e47259f3becd648ca9efc23bc0fd1b5ef8a75a1"
ADSP = "47882286b2500dbe509660e28bd39c6d7c87f2dd418da25abbcf2a6e22787404"


class SparseMemory:
    def __init__(self):
        self.data = {}
        self.calls = []

    def put(self, address, fmt, *values):
        self.data.update(enumerate(struct.pack("<" + fmt, *values), address))

    def read(self, address, count):
        self.calls.append((address, count))
        if address >= 1 << 56:
            raise AssertionError("tagged read")
        try:
            return bytes(self.data[address + i] for i in range(count))
        except KeyError as exc:
            raise AssertionError("read outside explicit scalar fixture") from exc


def roots(sha=CDSP):
    delta = -0x1000 if sha == ADSP else 0
    return {"rpcmem_head_va": 0x419c0 + delta,
            "rpcmem_reference_va": 0x419ac + delta,
            "rpcmem_status_va": 0x3f9c8 + delta,
            "pls_root_va": 0x425d0 + delta,
            "module_pool_va": 0x42a28 + delta,
            "constructor_va": 0x3b5e0 if sha == ADSP else 0x3c630,
            "destructor_va": 0x3b5f4 if sha == ADSP else 0x3c644}


def fixture(sha=CDSP, pristine=False):
    memory = SparseMemory()
    p = roots(sha)
    head = BASE + p["rpcmem_head_va"]
    memory.put(head, "QQ", *((0, 0) if pristine else (head, head)))
    memory.put(BASE + p["rpcmem_reference_va"], "i", 0)
    memory.put(BASE + p["rpcmem_status_va"], "ii", -1 if pristine else 0, -1)
    memory.put(BASE + p["pls_root_va"], "QII", 0, 0 if pristine else 1, 0 if pristine else 1)
    for i in range(20):
        memory.put(BASE + p["module_pool_va"] + i * 0x100 + 0x18, "Q", 0)
    return memory


def allocations(memory, addresses, sha=CDSP, tagged=False):
    head = BASE + roots(sha)["rpcmem_head_va"]
    tag = TAG if tagged else 0
    memory.put(head, "QQ", addresses[0] | tag, addresses[-1] | tag)
    for i, address in enumerate(addresses):
        following = addresses[i + 1] if i + 1 < len(addresses) else head
        previous = addresses[i - 1] if i else head
        memory.put(address, "QQQiI", following | tag, previous | tag,
                   0xfe00000000000001 + i, 4096 + i, 0x50000000)
        memory.put(address + 0x24, "i", 7 + i)


def pool_slot(memory, index, key, sha=CDSP, ref=1):
    slot = BASE + roots(sha)["module_pool_va"] + index * 0x100
    memory.put(slot, "Q", 0xf100000000000001)
    memory.put(slot + 0x18, "Q", key)
    memory.put(slot + 0x58, "Q", 0xf200000000000003)
    memory.put(slot + 0x88, "i", ref)
    return slot


def catalog(memory, indices, sha=CDSP, tagged=False):
    p = roots(sha)
    tag = TAG if tagged else 0
    memory.put(BASE + p["pls_root_va"], "QII", DESC | tag, 1, 1)
    memory.put(DESC, "QQQQ", 0, (BASE + p["constructor_va"]) | tag, 0,
               (BASE + p["destructor_va"]) | tag)
    slots = [pool_slot(memory, i, 0x100000000 + 99 + i, sha, ref=2) for i in indices]
    memory.put(DESC + 0x58, "Q", slots[0] | tag if slots else 0)
    if slots:
        memory.put(HASH + 0x10, "I", len(slots))
        memory.put(HASH + 0x18, "QQ", (slots[-1] + 0x20) | tag, 0x20)
    for i, slot in enumerate(slots):
        memory.put(slot + 0x20, "Q", HASH | tag)
        memory.put(slot + 0x28, "QQ", slots[i - 1] | tag if i else 0,
                   slots[i + 1] | tag if i + 1 < len(slots) else 0)
        memory.put(slot + 0x48, "QI", (slot + 0x18) | tag, 4)
    return slots


class FastRPCAllocationsTests(unittest.TestCase):
    def module(self):
        return importlib.import_module("tools.quest2_fastrpc_allocations")

    def collect(self, memory, **kwargs):
        return self.module().collect_fastrpc_allocations(memory.read, BASE, **kwargs)

    def test_pristine_and_constructed_empty_both_profiles(self):
        for sha in (CDSP, ADSP):
            for pristine in (True, False):
                result = self.collect(fixture(sha, pristine), artifact_sha256=sha)
                self.assertEqual(result["rpcmem_allocations"], [])
                self.assertEqual(result["module_pool_slots"], [])
                self.assertEqual(result["catalog_member_slots"], [])
                self.assertFalse(result["hardware_shutdown_validated"])
                if pristine:
                    self.assertEqual(result["rpcmem_initialization_result"], -1)

    def test_rpcmem_multiple_tagged_allocations_omit_padding_and_payload(self):
        memory = fixture()
        allocations(memory, [NODE, NODE + 0x80], tagged=True)
        result = self.collect(memory)
        self.assertEqual(len(result["rpcmem_allocations"]), 2)
        record = result["rpcmem_allocations"][0]
        self.assertEqual(record, {"node_pointer": NODE | TAG,
                                 "buffer_address": 0xfe00000000000001,
                                 "length": 4096, "flags": 0x50000000, "fd": 7})
        self.assertNotIn((NODE + 0x20, 4), memory.calls)

    def test_tagged_pls_membership_and_full_internal_keys_both_profiles(self):
        for sha in (CDSP, ADSP):
            memory = fixture(sha)
            catalog(memory, [19, 2], sha, tagged=True)
            pool_slot(memory, 4, 0x123456789, sha, ref=0)
            result = self.collect(memory, artifact_sha256=sha)
            self.assertEqual(result["catalog_member_slots"], [19, 2])
            records = {x["slot_index"]: x for x in result["module_pool_slots"]}
            self.assertEqual(records[19]["internal_key"], 0x100000000 + 118)
            self.assertEqual(records[19]["public_module_id"], 118)
            self.assertTrue(records[19]["catalog_member"])
            self.assertFalse(records[4]["catalog_member"])
            self.assertEqual(records[4]["reference_count"], 0)
            self.assertEqual(result["catalog_membership_scope"], "PLS application list")

    def test_descriptor_selection_skips_other_constructor_without_object_reads(self):
        memory = fixture()
        catalog(memory, [1])
        memory.put(BASE + roots()["pls_root_va"], "QII", DESC + 0x100, 1, 1)
        memory.put(DESC + 0x100, "QQQQ", DESC, BASE + 0x100, 0, 0)
        self.assertEqual(self.collect(memory)["catalog_member_slots"], [1])

    def test_profile_and_limits_refuse_before_reads(self):
        for kwargs in ({"artifact_sha256": "unknown"}, {"artifact_sha256": None},
                       {"node_cap": 65}, {"descriptor_cap": 17},
                       {"descriptor_cap": True}):
            memory = fixture()
            with self.assertRaises(ValueError):
                self.collect(memory, **kwargs)
            self.assertEqual(memory.calls, [])
        with self.assertRaises(ValueError):
            self.module().collect_fastrpc_allocations(lambda *_: b"", (1 << 56) - 1)

    def test_rpcmem_node_cap_cycles_reciprocity_and_fixed_overlap(self):
        for bad in ("cap", "cycle", "previous", "tail", "fixed", "overlap"):
            memory = fixture()
            allocations(memory, [NODE, NODE + 0x80])
            if bad == "cycle":
                memory.put(NODE + 0x80, "Q", NODE)
            elif bad == "previous":
                memory.put(NODE + 8, "Q", NODE)
            elif bad == "tail":
                memory.put(BASE + roots()["rpcmem_head_va"] + 8, "Q", NODE)
            elif bad == "fixed":
                memory.put(NODE, "Q", BASE + roots()["module_pool_va"])
            elif bad == "overlap":
                memory.put(NODE, "Q", NODE + 8)
            with self.assertRaises(ValueError):
                self.collect(memory, node_cap=1 if bad == "cap" else 64)

    def test_descriptor_cap_cycle_overlap_and_duplicate_selection(self):
        for bad in ("cap", "cycle", "overlap", "duplicate"):
            memory = fixture()
            catalog(memory, [1])
            if bad == "cycle":
                memory.put(DESC, "Q", DESC)
            elif bad == "overlap":
                allocations(memory, [DESC])
            elif bad == "duplicate":
                memory.put(DESC, "Q", DESC + 0x100)
                memory.put(DESC + 0x100, "QQQQ", 0, BASE + roots()["constructor_va"],
                           0, BASE + roots()["destructor_va"])
            with self.assertRaises(ValueError):
                self.collect(memory, descriptor_cap=0 if bad == "cap" else 16)

    def test_catalog_rejects_nonpool_links_and_corrupt_key_count_tail_ref(self):
        for bad in ("outside", "previous", "key", "keylen", "count", "tail", "ref", "cycle", "table"):
            memory = fixture()
            slots = catalog(memory, [1, 7])
            address, fmt, value = {
                "outside": (slots[0] + 0x30, "Q", NODE),
                "previous": (slots[0] + 0x28, "Q", slots[1]),
                "key": (slots[0] + 0x48, "Q", slots[0]),
                "keylen": (slots[0] + 0x50, "I", 8),
                "count": (HASH + 0x10, "I", 3),
                "tail": (HASH + 0x18, "Q", slots[0] + 0x20),
                "ref": (slots[0] + 0x88, "i", 0),
                "cycle": (slots[1] + 0x30, "Q", slots[0]),
                "table": (slots[1] + 0x20, "Q", HASH + 0x100)}[bad]
            memory.put(address, fmt, value)
            with self.assertRaises(ValueError):
                self.collect(memory)

    def test_status_ref_and_partial_initialization_remain_explicit(self):
        memory = fixture(pristine=True)
        pool_slot(memory, 0, 1, ref=-1)
        result = self.collect(memory)
        self.assertEqual(result["module_pool_slots"][0]["reference_count"], -1)
        memory.put(BASE + roots()["pls_root_va"], "QII", DESC, 0, 1)
        with self.assertRaises(ValueError):
            self.collect(memory)

    def test_short_wrong_type_and_oserror_reads_refuse(self):
        def failed(*_):
            raise OSError("fixture")
        for reader in (lambda *_: b"", lambda *_: "12345678", failed):
            with self.assertRaises(ValueError):
                self.module().collect_fastrpc_allocations(reader, BASE)


if __name__ == "__main__":
    unittest.main()
