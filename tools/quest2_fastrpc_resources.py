"""Offline scalar decoder for additional hash-pinned FastRPC bookkeeping.

CDSP linked VAs (code VAs are file offsets): local cleanup IDs are allocated at
0x2f574, stored at node+0x18 and inserted at 0x2f5a4..0x2f5bc; heads initialize
at 0x17f1c. Buffer records initialize at 0x23f84..0x23fa4 and are inserted at
0x2409c..0x240ac. remote_register_fd (0x23cb0..0x23d20) inserts reference zero.
DMA records are written at 0x24adc..0x24ae8 and consumed at 0x1e540..0x1e57c:
the count is a highwater bound, not population. mmap records are constructed
at 0x24778..0x24830. munmap clears their reference before ioctl at 0x2449c;
EINVAL/ENOTTY failures can leave zero. Global buffer/mmap heads initialize at
0x17c6c..0x17cc0. ADSP equivalents: 0x2e528..0x2e570, 0x22f44..0x2306c,
0x22c78..0x22ce8, 0x23a98..0x23aa4, 0x1d538..0x1d574, 0x23738..0x237f0,
0x2345c onward, 0x16c6c..0x16cc0. Both layouts use identical field offsets.

These are bookkeeping observations, not release receipts or all resources.
Normal local-module close does not remove its cleanup ID. Buffer unregister
unlinks before unmapping; domain deinit discards mmap records. Actual remote
close errors survive at CDSP 0x20c28/ADSP 0x1fc1c even when records disappear.
The caller verifies artifact/process identity, permitted mappings, read budgets
and paired stability. Tags authorize no access; payload addresses are never read.
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
    "local_module_heads_va": 0x3faa0 + delta,
    "registered_buffer_head_va": 0x3ffb8 + delta,
    "dma_table_va": 0x3fff0 + delta,
    "dma_count_va": 0x41370 + delta,
    "mmap_heads_va": 0x40ff0 + delta})
    for sha, delta in ((ARTIFACT_SHA256, 0), (ADSP_ARTIFACT_SHA256, -0x1000))})


def profile_for(artifact_sha256):
    """Return only a verified, immutable resource layout."""
    ownership_profile_for(artifact_sha256)
    return LAYOUTS[artifact_sha256]


def collect_fastrpc_resources(read, base, *, node_cap=64,
                             artifact_sha256=ARTIFACT_SHA256):
    """Decode one non-atomic snapshot, refusing malformed or ambiguous records.

    node_cap (0..64) is shared across all linked resource lists. Fixed DMA slots
    are bounded separately at 256. Buffer reference counters are raw signed
    values, including zero/negative unregister observations. mmap reference zero
    is valid bookkeeping after certain failed unmaps. No payload is dereferenced.
    """
    profile = profile_for(artifact_sha256)
    owner = ownership_profile_for(artifact_sha256)
    if (not callable(read) or type(base) is not int or base < 0 or base % 8
            or base + profile["dma_count_va"] + 4 > ADDRESS_LIMIT
            or type(node_cap) is not int or not 0 <= node_cap <= 64):
        raise ValueError("invalid FastRPC resource reader, base or cap")

    def unpack(address, fmt):
        size = struct.calcsize("<" + fmt)
        if not 0 <= address < address + size <= ADDRESS_LIMIT:
            raise ValueError("FastRPC resource read outside address bounds")
        try:
            raw = read(address, size)
        except (OSError, ValueError) as exc:
            raise ValueError("FastRPC resource read failed") from exc
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise ValueError("FastRPC resource read has wrong type or length")
        return struct.unpack("<" + fmt, raw)

    def pointer(raw, size):
        address = raw & ADDRESS_MASK
        if not address or address % 8 or address + size > ADDRESS_LIMIT:
            raise ValueError("invalid FastRPC resource structural pointer")
        return address

    table_slot = base + owner["table_pointer_va"]
    raw_table, = unpack(table_slot, "Q")
    local = base + profile["local_module_heads_va"]
    buffer = base + profile["registered_buffer_head_va"]
    dma = base + profile["dma_table_va"]
    count_slot = base + profile["dma_count_va"]
    mmap = base + profile["mmap_heads_va"]
    head_addresses = ([local + i * 0x10 for i in range(16)] + [buffer]
                      + [mmap + i * 0x38 for i in range(16)])
    heads = [unpack(head, "QQ") for head in head_addresses]
    highwater, = unpack(count_slot, "i")
    if not 0 <= highwater <= 256:
        raise ValueError("invalid FastRPC DMA highwater count")
    result = {"artifact_sha256": artifact_sha256, "initialized": raw_table != 0,
              "table_pointer": raw_table, "domains": [], "registered_buffers": [],
              "dma_highwater_count": highwater, "pending_dma_registrations": [],
              "hardware_shutdown_validated": False}
    if raw_table == 0:
        # BSS before initialization is zero; constructed empty heads self-link.
        # Nonempty lists without their process domain table cannot be attributed.
        if highwater or any(links != (0, 0) and not all(
                pointer(value, 16) == head for value in links)
                for head, links in zip(head_addresses, heads)):
            raise ValueError("uninitialized FastRPC resource roots are nonempty")
        return result
    table = pointer(raw_table, 16 * 0x248)
    fixed = [(table, 16 * 0x248), (table_slot, 8), (local, 16 * 0x10),
             (buffer, 0x38), (dma, 256 * 0x10), (mmap, 16 * 0x38),
             (count_slot, 4), (base + owner["legacy_head_va"], 16)]
    for i, (start, size) in enumerate(fixed):
        if any(start < other + length and other < start + size
               for other, length in fixed[:i]):
            raise ValueError("overlapping FastRPC resource fixed roots")
    for index in range(16):
        deinit, operations = unpack(table + index * 0x248 + 0x1bc, "II")
        if deinit or operations:
            raise ValueError("FastRPC resource domain busy or deinitializing")
        result["domains"].append({"domain_index": index,
                                  "tracked_local_module_ids": [], "mmaps": []})
    for index in range(highwater):
        fd, length, inuse, attrs = unpack(dma + index * 0x10, "iiII")
        if inuse not in (0, 1):
            raise ValueError("invalid FastRPC DMA inuse flag")
        if inuse:
            if fd < 0 or attrs not in (0, 0x10):
                raise ValueError("invalid active FastRPC DMA registration")
            result["pending_dma_registrations"].append({
                "slot_index": index, "fd": fd, "length": length,
                "inuse": inuse, "attrs": attrs})
    visited = []

    def walk(head, links, kind):
        first, tail = (pointer(value, 16) for value in links)
        if first == head:
            if tail != head:
                raise ValueError("nonreciprocal FastRPC resource empty head")
            return []
        previous, current_raw = head, links[0]
        records = []
        size = 0x28 if kind == "local" else 0x40
        while True:
            current = pointer(current_raw, size)
            if current == head:
                if tail != previous:
                    raise ValueError("nonreciprocal FastRPC resource tail")
                return records
            if any(current < start + length and start < current + size
                   for start, length in fixed + visited):
                raise ValueError("FastRPC resource cycle, overlap or ambiguous membership")
            if len(visited) >= node_cap:
                raise ValueError("FastRPC resource aggregate node cap exceeded")
            visited.append((current, size))
            if kind == "local":
                following, back, unused, handle, unused2 = unpack(current, "QQQQQ")
                if unused or unused2 or handle > 0xffffffff:
                    raise ValueError("malformed tracked local module ID")
                record = {"node_pointer": current_raw, "local_module_id": handle}
            elif kind == "buffer":
                fields = unpack(current, "QQQiiIIi16B")
                following, back, address, length, fd, host_munmap, attrs, ref = fields[:8]
                mapped = list(fields[8:])
                if (not address or length < 0 or host_munmap not in (0, 1)
                        or any(flag not in (0, 1) for flag in mapped)):
                    raise ValueError("invalid registered buffer flags")
                record = {"node_pointer": current_raw, "buffer_address": address,
                          "length": length, "fd": fd, "host_munmap": host_munmap,
                          "attrs": attrs, "reference_count": ref,
                          "domain_mapped_flags": mapped}
            else:
                (following, back, fd, offset, low, high, address, length,
                 remote, ref) = unpack(current, "QQiIIIQQQi")
                if ref < 0:
                    raise ValueError("invalid negative mmap reference count")
                record = {"node_pointer": current_raw, "fd": fd, "offset": offset,
                          "flags_low16": low, "flags_high16": high,
                          "buffer_address": address, "length": length,
                          "remote_address": remote, "reference_count": ref}
            if pointer(back, 16) != previous:
                raise ValueError("nonreciprocal FastRPC resource previous link")
            records.append(record)
            previous, current_raw = current, following

    for index in range(16):
        result["domains"][index]["tracked_local_module_ids"] = walk(
            head_addresses[index], heads[index], "local")
    result["registered_buffers"] = walk(buffer, heads[16], "buffer")
    for index in range(16):
        result["domains"][index]["mmaps"] = walk(
            head_addresses[17 + index], heads[17 + index], "mmap")
    return result
