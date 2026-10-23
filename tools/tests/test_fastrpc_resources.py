"""Resource decoding uses explicit sparse scalar memory, never buffer contents."""
import importlib
import struct
import unittest

BASE = 0x7000000000
TABLE = 0x7100000000
NODE = 0x7200000000
TAG = 0xab00000000000000
CDSP = "1b5f80e8128b10e148f53aa61e47259f3becd648ca9efc23bc0fd1b5ef8a75a1"
ADSP = "47882286b2500dbe509660e28bd39c6d7c87f2dd418da25abbcf2a6e22787404"


class SparseMemory:
    def __init__(self):
        self.data = {}
        self.calls = []

    def put(self, address, fmt, *values):
        self.data.update(enumerate(struct.pack("<" + fmt, *values), address))

    def read(self, address, size):
        self.calls.append((address, size))
        if address >= 1 << 56:
            raise AssertionError("tagged read")
        try:
            return bytes(self.data[address + i] for i in range(size))
        except KeyError as exc:
            raise AssertionError("read outside explicit scalar fixture") from exc


def roots(sha):
    delta = -0x1000 if sha == ADSP else 0
    return dict(table=0x3fa28 + delta, local=0x3faa0 + delta,
                buffer=0x3ffb8 + delta, dma=0x3fff0 + delta,
                count=0x41370 + delta, mmap=0x40ff0 + delta)


def fixture(sha=CDSP, initialized=True, pristine=False):
    memory = SparseMemory()
    r = roots(sha)
    memory.put(BASE + r["table"], "Q", TABLE if initialized else 0)
    memory.put(BASE + r["count"], "i", 0)
    for start, stride, count in ((r["local"], 0x10, 16),
                                 (r["buffer"], 0, 1), (r["mmap"], 0x38, 16)):
        for index in range(count):
            head = BASE + start + stride * index
            memory.put(head, "QQ", *( (0, 0) if pristine else (head, head)))
    if initialized:
        for index in range(16):
            memory.put(TABLE + index * 0x248 + 0x1bc, "II", 0, 0)
    return memory


def link(memory, head, address, kind, tagged=False, **values):
    tag = TAG if tagged else 0
    memory.put(head, "QQ", address | tag, address | tag)
    if kind == "local":
        memory.put(address, "QQQQQ", head | tag, head | tag, 0,
                   values.get("handle", 9), 0)
    elif kind == "buffer":
        memory.put(address, "QQQiiIIi16B", head | tag, head | tag,
                   values.get("va", 0xfe00000000000001), 4096,
                   values.get("fd", -2), 1, 0x80, values.get("ref", 0),
                   *values.get("mapped", [0] * 15 + [1]))
    else:
        memory.put(address, "QQiIIIQQQi", head | tag, head | tag,
                   8, 0, 2, 0, 0xff00000000000001, 4096,
                   0xee00000000000003, values.get("ref", 0))


class FastRPCResourcesTests(unittest.TestCase):
    def module(self):
        return importlib.import_module("tools.quest2_fastrpc_resources")

    def collect(self, memory, **kwargs):
        return self.module().collect_fastrpc_resources(memory.read, BASE, **kwargs)

    def test_empty_both_profiles_and_immutable_profiles(self):
        for sha in (CDSP, ADSP):
            result = self.collect(fixture(sha), artifact_sha256=sha)
            self.assertTrue(result["initialized"])
            self.assertEqual(len(result["domains"]), 16)
            self.assertEqual(result["registered_buffers"], [])
            self.assertFalse(result["hardware_shutdown_validated"])
            with self.assertRaises(TypeError):
                self.module().profile_for(sha)["dma_table_va"] = 0

    def test_active_sparse_lists_raw_tags_and_payloads_not_dereferenced(self):
        for sha in (CDSP, ADSP):
            memory = fixture(sha)
            r = roots(sha)
            memory.put(BASE + r["table"], "Q", TABLE | TAG)
            link(memory, BASE + r["local"] + 15 * 0x10, NODE, "local", True)
            link(memory, BASE + r["buffer"], NODE + 0x80, "buffer", True)
            link(memory, BASE + r["mmap"] + 7 * 0x38, NODE + 0x100, "mmap", True)
            result = self.collect(memory, artifact_sha256=sha)
            self.assertEqual(result["table_pointer"], TABLE | TAG)
            self.assertEqual(result["domains"][15]["tracked_local_module_ids"],
                             [{"node_pointer": NODE | TAG, "local_module_id": 9}])
            self.assertEqual(result["registered_buffers"][0]["buffer_address"],
                             0xfe00000000000001)
            self.assertEqual(result["registered_buffers"][0]["reference_count"], 0)
            self.assertEqual(result["registered_buffers"][0]["fd"], -2)
            self.assertEqual(result["domains"][7]["mmaps"][0]["remote_address"],
                             0xee00000000000003)
            self.assertEqual(result["domains"][7]["mmaps"][0]["reference_count"], 0)

    def test_dma_highwater_holes_and_stale_fields_are_not_population_count(self):
        memory = fixture()
        r = roots(CDSP)
        memory.put(BASE + r["count"], "i", 3)
        memory.put(BASE + r["dma"], "iiII", 3, 4096, 1, 0x10)
        memory.put(BASE + r["dma"] + 0x10, "iiII", 4, 64, 0, 0x10)
        memory.put(BASE + r["dma"] + 0x20, "iiII", 5, 0, 1, 0)
        result = self.collect(memory)
        self.assertEqual(result["dma_highwater_count"], 3)
        self.assertEqual([x["slot_index"] for x in result["pending_dma_registrations"]], [0, 2])

    def test_zero_and_initialized_null_table_lifecycle_has_no_heap_reads(self):
        for pristine in (True, False):
            memory = fixture(initialized=False, pristine=pristine)
            result = self.collect(memory)
            self.assertFalse(result["initialized"])
            self.assertEqual(result["domains"], [])
            self.assertTrue(all(address < TABLE for address, _ in memory.calls))
        memory = fixture(initialized=False)
        memory.put(BASE + roots(CDSP)["buffer"], "QQ", NODE, NODE)
        with self.assertRaises(ValueError):
            self.collect(memory)

    def test_unknown_profile_and_invalid_arguments_refuse_before_reads(self):
        for kwargs in ({"artifact_sha256": "bad"}, {"artifact_sha256": None},
                       {"node_cap": 65}, {"node_cap": True}):
            memory = fixture()
            with self.assertRaises(ValueError):
                self.collect(memory, **kwargs)
            self.assertEqual(memory.calls, [])
        with self.assertRaises(ValueError):
            self.module().collect_fastrpc_resources(lambda *_: b"", (1 << 56) - 1)

    def test_short_and_wrong_type_reads_refuse(self):
        for reader in (lambda *_: b"", lambda *_: "0" * 8):
            with self.assertRaises(ValueError):
                self.module().collect_fastrpc_resources(reader, BASE)

    def test_aggregate_cap_across_resource_lists(self):
        memory = fixture()
        r = roots(CDSP)
        link(memory, BASE + r["local"], NODE, "local")
        link(memory, BASE + r["buffer"], NODE + 0x80, "buffer")
        with self.assertRaises(ValueError):
            self.collect(memory, node_cap=1)
        self.assertEqual(len(self.collect(memory, node_cap=2)["registered_buffers"]), 1)

    def test_cycles_reciprocal_links_and_cross_list_overlap_refuse(self):
        for bad in ("cycle", "back", "overlap", "tail"):
            memory = fixture()
            r = roots(CDSP)
            head = BASE + r["local"]
            link(memory, head, NODE, "local")
            if bad == "cycle":
                memory.put(NODE, "Q", NODE)
            elif bad == "back":
                memory.put(NODE + 8, "Q", NODE)
            elif bad == "tail":
                memory.put(head + 8, "Q", NODE + 0x80)
            else:
                memory.put(BASE + r["buffer"], "QQ", NODE, NODE)
            with self.assertRaises(ValueError):
                self.collect(memory)

    def test_nodes_cannot_overlap_any_fixed_array_or_domain_table(self):
        for address in (TABLE, BASE + 0x3faa0, BASE + 0x3fff0,
                        BASE + 0x40ff0, BASE + 0x41370, BASE + 0x3fa90):
            memory = fixture()
            memory.put(BASE + roots(CDSP)["buffer"], "QQ", address, address)
            with self.assertRaises(ValueError):
                self.collect(memory)

    def test_busy_and_uninitialized_domain_heads_refuse(self):
        for offset in (0x1bc, 0x1c0):
            memory = fixture()
            memory.put(TABLE + 15 * 0x248 + offset, "I", 1)
            with self.assertRaises(ValueError):
                self.collect(memory)
        memory = fixture(pristine=True)
        with self.assertRaises(ValueError):
            self.collect(memory)

    def test_source_derived_scalar_limits(self):
        for count in (-1, 257):
            memory = fixture()
            memory.put(BASE + roots(CDSP)["count"], "i", count)
            with self.assertRaises(ValueError):
                self.collect(memory)
        memory = fixture()
        memory.put(BASE + roots(CDSP)["count"], "i", 1)
        memory.put(BASE + roots(CDSP)["dma"], "iiII", 3, 4, 2, 0)
        with self.assertRaises(ValueError):
            self.collect(memory)
        for kind, field, value in (("local", 0x18, 1 << 32),
                                    ("buffer", 0x2c, 2), ("mmap", 0x38, -1)):
            memory = fixture()
            r = roots(CDSP)
            head = BASE + r[{"local": "local", "buffer": "buffer", "mmap": "mmap"}[kind]]
            link(memory, head, NODE, kind)
            memory.put(NODE + field, "Q" if kind == "local" else "i", value)
            with self.assertRaises(ValueError):
                self.collect(memory)

    def test_buffer_signed_reference_is_preserved_without_population_claim(self):
        memory = fixture()
        link(memory, BASE + roots(CDSP)["buffer"], NODE, "buffer", ref=-1)
        self.assertEqual(self.collect(memory)["registered_buffers"][0]["reference_count"], -1)

    def test_only_source_permitted_active_dma_and_buffer_scalars(self):
        for fd, attrs in ((-1, 0), (3, 1)):
            memory = fixture()
            memory.put(BASE + roots(CDSP)["count"], "i", 1)
            memory.put(BASE + roots(CDSP)["dma"], "iiII", fd, -1, 1, attrs)
            with self.assertRaises(ValueError):
                self.collect(memory)
        for field, fmt, value in ((0x10, "Q", 0), (0x18, "i", -1)):
            memory = fixture()
            link(memory, BASE + roots(CDSP)["buffer"], NODE, "buffer")
            memory.put(NODE + field, fmt, value)
            with self.assertRaises(ValueError):
                self.collect(memory)


if __name__ == "__main__":
    unittest.main()
