"""Offline rpcmem and current local-module observations for pinned FastRPC ELFs.

CDSP code VAs equal file offsets. rpcmem malloc(0x28) at 0x2ea8c..0x2ea90,
fields at 0x2ef84..0x2efe0 and insertion at 0x2edbc..0x2edc4 establish its
list ABI. Free unlinks first (0x2f248..0x2f258) and ignores unregister/munmap/
close results. Init head/reference/status/FD: 0x2e750..0x2e78c, 0x2e8e8..0x2e8f8.
ADSP corresponding insertion/free: 0x2dd70..0x2dd78 / 0x2e1fc..0x2e20c.

The module pool is 20*0x100, selected at CDSP 0x2f874..0x2fbc4 and
0x35864..0x35948 (ADSP 0x34814 onward). Full keys can increment beyond 32 bits
(0x30050..0x30060); public IDs are their low 32 bits. Reference sharing is
explicit at 0x34b74..0x34b8c. PLS descriptor layout/allocation/selection is at
0x348c4..0x3497c (ADSP 0x33874..0x3392c). Application prev/next, key pointer,
key length, table pointer/count/tail/handle-offset are established by insertion
0x301a0..0x30390 and removal 0x25888..0x259b4; the PLS-specific insertion is
0x35ef0..0x36058 (ADSP 0x34ea0..0x35008). Only the application list is checked
here: bucket arrays, names, callback code and buffer payloads are unread.

Pool occupancy alone is not membership. Unlisted occupied slots remain raw
inventory observations, including possible transitions. No historical domain
ownership, release receipt, complete kernel inventory or shutdown is inferred.
The caller verifies artifact/process identity, mappings, budgets and paired
stability. TBI normalization alone authorizes no heap access.
"""
import struct
from types import MappingProxyType

try:
    from .quest2_fastrpc_ownership import (
        ARTIFACT_SHA256, ADSP_ARTIFACT_SHA256, ADDRESS_LIMIT, ADDRESS_MASK,
        profile_for as ownership_profile_for)
except ImportError:
    from quest2_fastrpc_ownership import (
        ARTIFACT_SHA256, ADSP_ARTIFACT_SHA256, ADDRESS_LIMIT, ADDRESS_MASK,
        profile_for as ownership_profile_for)


LAYOUTS = MappingProxyType({sha: MappingProxyType({
    "rpcmem_head_va": 0x419c0 + delta,
    "rpcmem_reference_va": 0x419ac + delta,
    "rpcmem_status_va": 0x3f9c8 + delta,
    "pls_root_va": 0x425d0 + delta,
    "module_pool_va": 0x42a28 + delta,
    "constructor_va": ctor, "destructor_va": dtor,
    "global_start_va": 0x3f930 + delta, "global_size": 0x5981})
    for sha, delta, ctor, dtor in (
        (ARTIFACT_SHA256, 0, 0x3c630, 0x3c644),
        (ADSP_ARTIFACT_SHA256, -0x1000, 0x3b5e0, 0x3b5f4))})


def profile_for(artifact_sha256):
    """Return immutable offsets for an exact verified artifact hash."""
    ownership_profile_for(artifact_sha256)
    return LAYOUTS[artifact_sha256]


def collect_fastrpc_allocations(read, base, *, artifact_sha256=ARTIFACT_SHA256,
                                node_cap=64, descriptor_cap=16):
    """Observe rpcmem, occupied pool slots and PLS application-list membership.

    node_cap (0..64) bounds rpcmem records; descriptor_cap (0..16) bounds PLS
    descriptors. Application traversal is confined to the exact 20 fixed slots.
    Returns JSON-compatible scalar records or raises ValueError on refusal.
    """
    p = profile_for(artifact_sha256)
    if (not callable(read) or type(base) is not int or base < 0 or base % 8
            or base + p["global_start_va"] + p["global_size"] > ADDRESS_LIMIT
            or type(node_cap) is not int or not 0 <= node_cap <= 64
            or type(descriptor_cap) is not int or not 0 <= descriptor_cap <= 16):
        raise ValueError("invalid FastRPC allocation reader, base or caps")

    def unpack(address, fmt):
        count = struct.calcsize("<" + fmt)
        if not 0 <= address < address + count <= ADDRESS_LIMIT:
            raise ValueError("FastRPC allocation read outside address bounds")
        try:
            raw = read(address, count)
        except (OSError, ValueError) as exc:
            raise ValueError("FastRPC allocation read failed") from exc
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != count:
            raise ValueError("FastRPC allocation read has wrong type or length")
        return struct.unpack("<" + fmt, raw)

    def pointer(raw, count):
        address = raw & ADDRESS_MASK
        if not address or address % 8 or address + count > ADDRESS_LIMIT:
            raise ValueError("invalid FastRPC allocation structural pointer")
        return address

    claimed = [(base + p["global_start_va"], p["global_size"])]

    def claim(address, count):
        if any(address < other + size and other < address + count
               for other, size in claimed):
            raise ValueError("FastRPC allocation cycle or overlapping structures")
        claimed.append((address, count))

    head = base + p["rpcmem_head_va"]
    head_links = unpack(head, "QQ")
    reference, = unpack(base + p["rpcmem_reference_va"], "i")
    status, allocator_fd = unpack(base + p["rpcmem_status_va"], "ii")
    if reference < 0:
        raise ValueError("invalid negative rpcmem initialization reference")
    result = {"artifact_sha256": artifact_sha256,
              "rpcmem_head_constructed": head_links != (0, 0),
              "rpcmem_initialization_reference": reference,
              "rpcmem_initialization_result": status, "rpcmem_allocator_fd": allocator_fd,
              "rpcmem_allocations": [], "module_pool_slots": [],
              "pls_reference_count": 0, "pls_raw_flag": 0,
              "catalog_descriptor_pointer": 0, "catalog_member_slots": [],
              "catalog_membership_scope": "PLS application list",
              "hardware_shutdown_validated": False}
    if head_links != (0, 0):
        first, tail = (pointer(value, 16) for value in head_links)
        previous, current_raw = head, head_links[0]
        while True:
            current = pointer(current_raw, 0x28)
            if current == head:
                if tail != previous:
                    raise ValueError("nonreciprocal rpcmem tail")
                break
            if len(result["rpcmem_allocations"]) >= node_cap:
                raise ValueError("rpcmem node cap exceeded")
            claim(current, 0x28)
            following, back, address, length, flags = unpack(current, "QQQiI")
            fd, = unpack(current + 0x24, "i")
            if pointer(back, 16) != previous or length < 0:
                raise ValueError("invalid rpcmem previous link or length")
            result["rpcmem_allocations"].append({
                "node_pointer": current_raw, "buffer_address": address,
                "length": length, "flags": flags, "fd": fd})
            previous, current_raw = current, following

    pool = base + p["module_pool_va"]
    occupied = {}
    for index in range(20):
        slot = pool + index * 0x100
        key, = unpack(slot + 0x18, "Q")
        if key:
            loader, = unpack(slot, "Q")
            context, = unpack(slot + 0x58, "Q")
            ref, = unpack(slot + 0x88, "i")
            record = {"slot_index": index, "slot_address": slot, "internal_key": key,
                      "public_module_id": key & 0xffffffff, "loader_handle": loader,
                      "context_handle": context, "reference_count": ref,
                      "catalog_member": False}
            occupied[slot] = record
            result["module_pool_slots"].append(record)

    next_descriptor, pls_ref, flag = unpack(base + p["pls_root_va"], "QII")
    result["pls_reference_count"], result["pls_raw_flag"] = pls_ref, flag
    if next_descriptor and not pls_ref:
        raise ValueError("PLS descriptors observed during inactive/deinitializing state")
    descriptor_count, module_head = 0, 0
    while next_descriptor:
        descriptor = pointer(next_descriptor, 0x20)
        if descriptor_count >= descriptor_cap:
            raise ValueError("PLS descriptor cap exceeded")
        claim(descriptor, 0x20)
        descriptor_count += 1
        following, ctor, key, dtor = unpack(descriptor, "QQQQ")
        if ctor & ADDRESS_MASK == base + p["constructor_va"] and key == 0:
            if (result["catalog_descriptor_pointer"]
                    or dtor & ADDRESS_MASK != base + p["destructor_va"]):
                raise ValueError("ambiguous or malformed open-module PLS descriptor")
            # This matching descriptor is a verified calloc(0x68) object.
            claim(descriptor + 0x20, 0x48)
            result["catalog_descriptor_pointer"] = next_descriptor
            module_head, = unpack(descriptor + 0x58, "Q")
        next_descriptor = following

    previous, current_raw, hash_table, tail_raw, item_count = 0, module_head, 0, 0, 0
    seen = set()
    public_ids = set()
    while current_raw:
        slot = pointer(current_raw, 0x100)
        if slot not in occupied or slot in seen:
            raise ValueError("catalog link is cyclic, empty or outside fixed module pool")
        record = occupied[slot]
        if record["reference_count"] < 1 or record["public_module_id"] in public_ids:
            raise ValueError("catalog reference or public ID is invalid/ambiguous")
        seen.add(slot)
        public_ids.add(record["public_module_id"])
        table_raw, = unpack(slot + 0x20, "Q")
        back, following = unpack(slot + 0x28, "QQ")
        key_pointer, key_length = unpack(slot + 0x48, "QI")
        if ((pointer(back, 0x100) if back else 0) != previous
                or pointer(key_pointer, 4) != slot + 0x18 or key_length != 4):
            raise ValueError("invalid catalog application link or key")
        table_address = pointer(table_raw, 0x40)
        if not hash_table:
            claim(table_address, 0x40)
            hash_table = table_address
            item_count, = unpack(hash_table + 0x10, "I")
            tail_raw, handle_offset = unpack(hash_table + 0x18, "QQ")
            if not 1 <= item_count <= 20 or handle_offset != 0x20:
                raise ValueError("invalid catalog item count or hash handle offset")
        elif table_address != hash_table:
            raise ValueError("catalog members have inconsistent hash tables")
        record["catalog_member"] = True
        result["catalog_member_slots"].append(record["slot_index"])
        previous, current_raw = slot, following
    if seen and (len(seen) != item_count or pointer(tail_raw, 0x38) != previous + 0x20):
        raise ValueError("catalog item count or tail does not match application list")
    return result
