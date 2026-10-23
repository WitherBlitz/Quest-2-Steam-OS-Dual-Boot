"""Exercise FastRPC structural decoding against explicit sparse memory."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = 0x7000000000
TABLE = 0x7100000000
LEGACY = BASE + 0x3fa90
NODE = 0x7200000000
TAG = 0xab00000000000000
ADSP_SHA256 = "47882286b2500dbe509660e28bd39c6d7c87f2dd418da25abbcf2a6e22787404"


class SparseMemory:
    def __init__(self):
        self.data = {}
        self.calls = []

    def put(self, address, fmt, *values):
        self.data.update((address + i, value) for i, value in
                         enumerate(struct.pack("<" + fmt, *values)))

    def read(self, address, count):
        self.calls.append((address, count))
        if address >= 1 << 56:
            raise AssertionError("tagged pointer passed to reader")
        try:
            return bytes(self.data[address + i] for i in range(count))
        except KeyError as exc:
            raise AssertionError("read outside declared scalar memory") from exc


def fixture(initialized=True):
    memory = SparseMemory()
    memory.put(BASE + 0x3fa28, "Q", TABLE if initialized else 0)
    memory.put(LEGACY, "QQ", LEGACY, LEGACY)
    if not initialized:
        return memory
    for index in range(16):
        domain = TABLE + index * 0x248
        memory.put(domain, "QQ", domain, domain)
        memory.put(domain + 0x60, "i", -1)
        memory.put(domain + 0x78, "I", 0)
        memory.put(domain + 0x84, "i", -1)
        memory.put(domain + 0x90, "i", -1)
        memory.put(domain + 0x1bc, "II", 0, 0)
    return memory


def add_list(memory, index, addresses, *, tagged=False, legacy=False):
    head = LEGACY if legacy else TABLE + index * 0x248
    tag = TAG if tagged else 0
    memory.put(head, "QQ", addresses[0] | tag, addresses[-1] | tag)
    for position, address in enumerate(addresses):
        following = addresses[position + 1] if position + 1 < len(addresses) else head
        previous = addresses[position - 1] if position else head
        memory.put(address, "QQQQQ", following | tag, previous | tag,
                   0 if legacy else head | tag,
                   0 if legacy else address | tag, 0x100 + position)


def adsp_fixture(initialized=True):
    memory = fixture(initialized)
    for address, size in ((BASE + 0x3fa28, 8), (LEGACY, 16)):
        for offset in range(size):
            del memory.data[address + offset]
    memory.put(BASE + 0x3ea28, "Q", TABLE if initialized else 0)
    memory.put(BASE + 0x3ea90, "QQ", BASE + 0x3ea90, BASE + 0x3ea90)
    return memory


class FastRPCOwnershipTests(unittest.TestCase):
    def module(self):
        path = ROOT / "tools/quest2_fastrpc_ownership.py"
        self.assertTrue(path.is_file(), "offline FastRPC decoder missing")
        spec = importlib.util.spec_from_file_location("fastrpc_ownership", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def collect(self, memory, **kwargs):
        return self.module().collect_fastrpc(memory.read, BASE, **kwargs)

    def test_all_sixteen_empty_domains_are_explicit_without_heap_scan(self):
        memory = fixture()
        result = self.collect(memory)
        self.assertTrue(result["initialized"])
        self.assertEqual(len(result["domains"]), 16)
        self.assertEqual(result["domains"][15]["domain_index"], 15)
        self.assertEqual(result["domains"][15]["fd"], -1)
        self.assertEqual(result["legacy_handles"], [])
        self.assertFalse(result["hardware_shutdown_validated"])
        self.assertLess(sum(size for _, size in memory.calls), 1024)

    def test_sparse_domains_multiple_nodes_and_legacy_are_attributed(self):
        memory = fixture()
        add_list(memory, 7, [NODE, NODE + 0x40])
        add_list(memory, 0, [NODE + 0x80])
        add_list(memory, 3, [NODE + 0xc0], legacy=True)
        memory.put(TABLE + 7 * 0x248 + 0x60, "i", 2)
        memory.put(TABLE + 7 * 0x248 + 0x78, "I", 1)
        memory.put(TABLE + 7 * 0x248 + 0x84, "i", 15)
        memory.put(TABLE + 7 * 0x248 + 0x90, "i", 3)
        result = self.collect(memory)
        domain = result["domains"][7]
        self.assertEqual((domain["mode"], domain["handle64_flag"], domain["fd"],
                          domain["secondary"]), (2, 1, 15, 3))
        self.assertEqual(domain["handles"], [
            {"node_pointer": NODE, "local_handle": NODE, "remote_handle": 0x100},
            {"node_pointer": NODE + 0x40, "local_handle": NODE + 0x40,
             "remote_handle": 0x101}])
        self.assertEqual(result["legacy_handles"], [
            {"node_pointer": NODE + 0xc0, "local_handle": 0, "remote_handle": 0x100}])

    def test_tagged_pointers_are_normalized_for_reads_and_raw_handles_preserved(self):
        memory = fixture()
        memory.put(BASE + 0x3fa28, "Q", TABLE | TAG)
        add_list(memory, 15, [NODE], tagged=True)
        add_list(memory, 3, [NODE + 0x40], tagged=True, legacy=True)
        result = self.collect(memory)
        self.assertEqual(result["table_pointer"], TABLE | TAG)
        self.assertEqual(result["domains"][15]["handles"][0]["local_handle"], NODE | TAG)
        self.assertEqual(result["legacy_handles"][0]["node_pointer"], (NODE + 0x40) | TAG)
        self.assertTrue(all(address < 1 << 56 for address, _ in memory.calls))

    def test_null_table_accepts_only_pristine_or_self_linked_empty_legacy(self):
        for pristine in (False, True):
            memory = fixture(False)
            if pristine:
                memory.put(LEGACY, "QQ", 0, 0)
            result = self.collect(memory)
            self.assertFalse(result["initialized"])
            self.assertEqual(result["domains"], [])
            self.assertEqual(result["legacy_handles"], [])
            self.assertEqual(len(memory.calls), 2)
        memory = fixture(False)
        memory.put(LEGACY, "QQ", NODE, NODE)
        with self.assertRaises(ValueError):
            self.collect(memory)
        self.assertEqual(len(memory.calls), 2)

    def test_zero_heads_are_not_initialized_empty_lists(self):
        for head in (TABLE, LEGACY):
            memory = fixture()
            memory.put(head, "QQ", 0, 0)
            with self.subTest(head=head), self.assertRaises(ValueError):
                self.collect(memory)

    def test_nonreciprocal_links_cycles_and_wrong_tail_are_rejected(self):
        for address, value in ((NODE + 8, NODE), (NODE, NODE),
                                (TABLE + 8, NODE + 0x40), (NODE, 0)):
            memory = fixture()
            add_list(memory, 0, [NODE])
            memory.put(address, "Q", value)
            with self.subTest(address=address, value=value), self.assertRaises(ValueError):
                self.collect(memory)

    def test_wrong_domain_full_self_and_remote_handle_width_are_rejected(self):
        for offset, value in ((0x10, TABLE + 0x248), (0x18, NODE + 0x100),
                              (0x20, 1 << 32)):
            memory = fixture()
            add_list(memory, 0, [NODE])
            memory.put(NODE + offset, "Q", value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.collect(memory)
        for offset in (0x10, 0x18):
            memory = fixture()
            add_list(memory, 3, [NODE], legacy=True)
            memory.put(NODE + offset, "Q", 1)
            with self.subTest(legacy_offset=offset), self.assertRaises(ValueError):
                self.collect(memory)

    def test_busy_or_deinitializing_domain_refuses_before_heap_traversal(self):
        for offset in (0x1bc, 0x1c0):
            memory = fixture()
            add_list(memory, 0, [NODE])
            memory.put(TABLE + 15 * 0x248 + offset, "I", 1)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "busy"):
                self.collect(memory)
            self.assertFalse(any(address == NODE for address, _ in memory.calls))

    def test_cap_applies_across_domains_and_legacy_before_extra_node_read(self):
        memory = fixture()
        add_list(memory, 0, [NODE])
        add_list(memory, 3, [NODE + 0x40], legacy=True)
        with self.assertRaisesRegex(ValueError, "cap"):
            self.collect(memory, node_cap=1)
        self.assertFalse(any(address == NODE + 0x40 for address, _ in memory.calls))
        self.assertEqual(self.collect(fixture(), node_cap=0)["legacy_handles"], [])

    def test_invalid_arguments_and_pointer_extent_fail_closed(self):
        module = self.module()
        for base, cap in ((True, 64), (-1, 64), (1 << 56, 64),
                           ((1 << 56) - 8, 64), (BASE, True), (BASE, -1), (BASE, 65)):
            memory = fixture()
            with self.subTest(base=base, cap=cap), self.assertRaises(ValueError):
                module.collect_fastrpc(memory.read, base, node_cap=cap)
            self.assertEqual(memory.calls, [])
        for pointer in (TAG, TABLE + 1, (1 << 56) - 8):
            memory = fixture()
            memory.put(BASE + 0x3fa28, "Q", pointer)
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                self.collect(memory)

    def test_terminal_sentinel_only_needs_its_own_sixteen_byte_extent(self):
        memory = fixture()
        high_base = (1 << 56) - 0x3faa0
        sentinel = (1 << 56) - 16
        memory.put(high_base + 0x3fa28, "Q", TABLE)
        memory.put(sentinel, "QQ", NODE, NODE)
        memory.put(NODE, "QQQQQ", sentinel, sentinel, 0, 0, 0x100)
        result = self.module().collect_fastrpc(memory.read, high_base)
        self.assertEqual(result["legacy_handles"][0]["remote_handle"], 0x100)

    def test_nodes_cannot_overlap_table_or_another_list_via_pointer_tags(self):
        memory = fixture()
        memory.put(TABLE, "QQ", TABLE - 8, TABLE - 8)
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.collect(memory)
        self.assertFalse(any(address == TABLE - 8 for address, _ in memory.calls))
        memory = fixture()
        add_list(memory, 0, [NODE])
        other = TABLE + 0x248
        memory.put(other, "QQ", NODE | TAG, NODE | TAG)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.collect(memory)

    def test_short_reads_and_mapping_refusal_are_errors(self):
        module = self.module()
        for value in (b"", b"\0" * 7, b"\0" * 9, "00000000", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.collect_fastrpc(lambda address, size: value, BASE)
        memory = fixture()
        add_list(memory, 0, [NODE])
        def short_node(address, size):
            raw = memory.read(address, size)
            return raw[:-1] if address == NODE else raw
        with self.assertRaises(ValueError):
            module.collect_fastrpc(short_node, BASE)
        def refused(address, size):
            raise OSError("mapping refused")
        with self.assertRaises(ValueError):
            module.collect_fastrpc(refused, BASE)

    def test_adsp_profile_decodes_tagged_domain_and_legacy_at_distinct_roots(self):
        memory = adsp_fixture()
        memory.put(BASE + 0x3ea28, "Q", TABLE | TAG)
        add_list(memory, 15, [NODE], tagged=True)
        sentinel = BASE + 0x3ea90
        memory.put(sentinel, "QQ", (NODE + 0x40) | TAG, (NODE + 0x40) | TAG)
        memory.put(NODE + 0x40, "QQQQQ", sentinel | TAG, sentinel | TAG, 0, 0, 0x321)
        module = self.module()
        result = module.collect_fastrpc(memory.read, BASE, artifact_sha256=ADSP_SHA256)
        self.assertEqual(result["artifact_sha256"], ADSP_SHA256)
        self.assertEqual(result["table_pointer"], TABLE | TAG)
        self.assertEqual(result["legacy_domain_index"], 0)
        self.assertEqual(result["domains"][15]["handles"][0]["local_handle"], NODE | TAG)
        self.assertEqual(result["legacy_handles"], [{"node_pointer": (NODE + 0x40) | TAG,
                         "local_handle": 0, "remote_handle": 0x321}])

    def test_adsp_null_table_uses_its_profile_address_extent(self):
        memory = SparseMemory()
        high_base = (1 << 56) - 0x3eaa0
        memory.put(high_base + 0x3ea28, "Q", 0)
        memory.put(high_base + 0x3ea90, "QQ", 0, 0)
        result = self.module().collect_fastrpc(memory.read, high_base,
                                              artifact_sha256=ADSP_SHA256)
        self.assertFalse(result["initialized"])
        self.assertEqual(len(memory.calls), 2)

    def test_adsp_busy_counters_and_bad_full_self_keep_shared_refusal_rules(self):
        module = self.module()
        for offset in (0x1bc, 0x1c0):
            memory = adsp_fixture()
            memory.put(TABLE + offset, "I", 1)
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "busy"):
                module.collect_fastrpc(memory.read, BASE, artifact_sha256=ADSP_SHA256)
        memory = adsp_fixture()
        add_list(memory, 1, [NODE], tagged=True)
        memory.put(NODE + 0x18, "Q", (NODE + 0x100) | TAG)
        with self.assertRaisesRegex(ValueError, "self"):
            module.collect_fastrpc(memory.read, BASE, artifact_sha256=ADSP_SHA256)

    def test_only_exact_pinned_hash_profiles_are_accepted_before_reads(self):
        module = self.module()
        profile = module.profile_for(ADSP_SHA256)
        self.assertEqual((profile["table_pointer_va"], profile["legacy_head_va"],
                          profile["legacy_domain_index"]), (0x3ea28, 0x3ea90, 0))
        with self.assertRaises(TypeError):
            profile["table_pointer_va"] = 0
        for sha in (None, True, 7, [], {}, "libadsprpc.so", "0" * 64, ADSP_SHA256.upper()):
            memory = fixture()
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                module.collect_fastrpc(memory.read, BASE, artifact_sha256=sha)
            self.assertEqual(memory.calls, [])


if __name__ == "__main__":
    unittest.main()
