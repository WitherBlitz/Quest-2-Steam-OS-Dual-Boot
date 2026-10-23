"""Pure decoder for the matched Quest 2 libcdsprpc.so process-state tables.

Evidence linked VAs: 0x17e14..0x17e28 allocates 16*0x248 and stores 0x3fa28;
0x17f04..0x17f90 initializes circular heads; 0x22a94..0x22af8 constructs
0x28-byte remote64 nodes; 0x22444..0x22494 constructs legacy remote32 nodes.
0x228d0..0x2292c and 0x20fbc establish deinit/in-progress counter meanings.

The caller must verify the actual artifact hash, process identity, mappings,
heap-read eligibility, byte/time budgets and paired stability. TBI normalization
alone authorizes no access. Empty lists and negative FDs prove no release or
shutdown: the stock close path can discard records even after remote failure.
"""
import struct

ARTIFACT_SHA256 = "1b5f80e8128b10e148f53aa61e47259f3becd648ca9efc23bc0fd1b5ef8a75a1"
ADDRESS_LIMIT = 1 << 56
ADDRESS_MASK = ADDRESS_LIMIT - 1


def collect_fastrpc(read, base, *, node_cap=64):
    """Decode one non-atomic snapshot or raise ValueError on refusal.

    The callback receives normalized, bounded addresses and must return exactly
    the requested bytes. Returned table/node/local-handle values preserve tags.
    Domain indices are raw table indices; no SDK domain encoding is inferred.
    node_cap is an analyst limit across all lists, including legacy entries.
    ARTIFACT_SHA256 identifies this decoder's ABI, not a hash checked here.
    """
    if (not callable(read) or type(base) is not int or base < 0
            or base + 0x3faa0 > ADDRESS_LIMIT or type(node_cap) is not int
            or not 0 <= node_cap <= 64):
        raise ValueError("invalid FastRPC reader, base, address extent or node cap")

    def pointer(raw, size):
        address = raw & ADDRESS_MASK
        if address == 0 or address % 8 or address + size > ADDRESS_LIMIT:
            raise ValueError("invalid FastRPC structural pointer or extent")
        return address

    def unpack(address, fmt):
        size = struct.calcsize("<" + fmt)
        if not 0 <= address < address + size <= ADDRESS_LIMIT:
            raise ValueError("FastRPC read outside supported address range")
        try:
            raw = read(address, size)
        except (OSError, ValueError) as exc:
            raise ValueError("FastRPC memory read failed") from exc
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise ValueError("FastRPC memory read returned wrong type or length")
        return struct.unpack("<" + fmt, raw)

    raw_table, = unpack(base + 0x3fa28, "Q")
    legacy = base + 0x3fa90
    legacy_head = unpack(legacy, "QQ")
    result = {"artifact_sha256": ARTIFACT_SHA256, "initialized": raw_table != 0,
              "table_pointer": raw_table, "domains": [], "legacy_domain_index": 3,
              "legacy_handles": [], "hardware_shutdown_validated": False}
    if raw_table == 0:
        # Pristine BSS is zero; initialized and subsequently emptied heads are
        # self-linked. No linked node can be attributed without its domain table.
        if legacy_head != (0, 0) and not all(
                pointer(value, 16) == legacy for value in legacy_head):
            raise ValueError("uninitialized FastRPC table has nonempty legacy list")
        return result
    table = pointer(raw_table, 16 * 0x248)
    heads = []
    for index in range(16):
        domain = table + index * 0x248
        heads.append(unpack(domain, "QQ"))
        deinit, operations = unpack(domain + 0x1bc, "II")
        if deinit or operations:
            raise ValueError("FastRPC domain is busy or deinitializing")
        result["domains"].append({
            "domain_index": index, "mode": unpack(domain + 0x60, "i")[0],
            "handle64_flag": unpack(domain + 0x78, "I")[0],
            "fd": unpack(domain + 0x84, "i")[0],
            "secondary": unpack(domain + 0x90, "i")[0],
            "deinit_in_progress": deinit, "operation_count": operations,
            "handles": []})
    visited = set()

    def walk(head, links, legacy_list=False):
        first, tail = (pointer(value, 16) for value in links)
        if first == head:
            if tail != head:
                raise ValueError("FastRPC empty head has nonreciprocal tail")
            return []
        previous, current_raw = head, links[0]
        records = []
        while True:
            current = pointer(current_raw, 16)
            if current == head:
                if tail != previous:
                    raise ValueError("FastRPC tail is not reciprocal")
                return records
            pointer(current_raw, 0x28)
            if (current < table + 16 * 0x248 and table < current + 0x28
                    or current < legacy + 16 and legacy < current + 0x28
                    or current < base + 0x3fa30 and base + 0x3fa28 < current + 0x28
                    or any(current < other + 0x28 and other < current + 0x28
                           for other in visited)):
                raise ValueError("FastRPC cycle, overlapping node or ambiguous list membership")
            if len(visited) >= node_cap:
                raise ValueError("FastRPC node cap exceeded")
            visited.add(current)
            following, back, owner, local, remote = unpack(current, "QQQQQ")
            if pointer(back, 16) != previous:
                raise ValueError("FastRPC node has nonreciprocal previous link")
            if legacy_list:
                if owner or local:
                    raise ValueError("malformed FastRPC legacy node")
            elif pointer(owner, 16) != head or pointer(local, 0x28) != current:
                raise ValueError("FastRPC node has wrong domain or full self handle")
            if remote > 0xffffffff:
                raise ValueError("FastRPC remote handle exceeds 32 bits")
            records.append({"node_pointer": current_raw, "local_handle": local,
                            "remote_handle": remote})
            previous, current_raw = current, following

    for index, links in enumerate(heads):
        result["domains"][index]["handles"] = walk(table + index * 0x248, links)
    result["legacy_handles"] = walk(legacy, legacy_head, legacy_list=True)
    return result
