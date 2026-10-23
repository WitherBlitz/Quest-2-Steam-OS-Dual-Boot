"""Offline scalar decoder for one matched Quest 2 CSL hardware-state layout.

The caller verifies artifact identity, mappings, process identity, read budgets,
paired stability and FD metadata. This module neither opens a process nor proves
kernel ownership, successful release or DMA quiescence. Negative FDs and raw
reference counters are observations, not release receipts.

Layout evidence: libcamxcsl.so SHA below; linked VAs 0x29ca8 (global state),
0x218b4..0x21c28 (16 sessions), 0x22ca8..0x22dd4 (128 acquisitions),
0x245e4..0x2463c (128 sparse links), 0x2af20..0x2b088 (physical FD binding),
and 0x2a5a4 (FD consumed by ioctl). All tables are inline BSS at 0x865f0.
"""
import struct

ARTIFACT_SHA256 = "14c88419e2cbbac05e7f2606d7af61cc736a9dc416c6ff51c3e1fd4233473588"
GLOBAL_VA = 0x865f0
SESSION_STRIDE = 0x1d8d8
ADDRESS_LIMIT = 1 << 56


def collect_csl(read, base, pid):
    """Return one non-atomic scalar snapshot; malformed/foreign state raises ValueError.

    ``read(address, count)`` must return exactly count bytes. No pointers from the
    target are followed or returned. Raw counts need not equal populated counts;
    the sparse link count alone is checked against its source-defined table.
    No artifact bytes are read here: ARTIFACT_SHA256 identifies the required ABI,
    and is not a claim that this function verified the caller's target library.
    """
    if (not callable(read) or type(base) is not int or base < 0
            or base + GLOBAL_VA + 0x1f58e0 > ADDRESS_LIMIT
            or type(pid) is not int or not 0 < pid < 1 << 31):
        raise ValueError("invalid CSL reader, base, PID or address extent")
    root = base + GLOBAL_VA

    def words(address, count=1, signed=False):
        size = count * 4
        if not 0 <= address < address + size <= ADDRESS_LIMIT:
            raise ValueError("CSL address outside supported range")
        try:
            raw = read(address, size)
        except (OSError, ValueError) as exc:
            raise ValueError("CSL memory read failed") from exc
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise ValueError("CSL memory read returned wrong type or length")
        return struct.unpack("<" + ("i" if signed else "I") * count, raw)

    def word(address, signed=False):
        return words(address, signed=signed)[0]

    global_state = {"state": word(root + 0x10),
                    "reference_count": word(root + 0x40),
                    "open_session_count": word(root + 0x44),
                    "physical_device_count": word(root + 0x1d8dc8)}
    physical_count = global_state["physical_device_count"]
    if (global_state["state"] != 1 or global_state["open_session_count"] > 16
            or physical_count > 128):
        raise ValueError("invalid CSL global state or count")
    sessions, physical_devices = [], {}
    for slot in range(16):
        address = root + 0x48 + slot * SESSION_STRIDE
        header = words(address + 0x10, 6)
        tail = words(address + 0x1cc28, 4)
        state, index, stream, references, open_references, handle = header
        link_count, retained_link, creator_pid, creator_tid = tail
        if handle == 0:
            if any(header) or any(tail):
                raise ValueError("nonempty CSL session without handle")
            continue
        if state not in (1, 4) or index != slot or handle & 0xff != slot:
            raise ValueError("invalid CSL session state or handle slot")
        if creator_pid != pid:
            raise ValueError("CSL session creator PID differs from selected process")
        if not 0 < creator_tid < 1 << 31 or link_count > 128:
            raise ValueError("invalid CSL session creator TID or link count")
        devices, links, acquired = [], [], {}
        for device_slot in range(128):
            device_address = address + 0x28 + device_slot * 0x188
            values = words(device_address + 0x10, 12)
            device_state, device_type, device_handle, session_handle = values[:4]
            if device_handle == 0:
                if any(values):
                    raise ValueError("nonempty CSL acquisition without handle")
                continue
            physical_index = values[8]
            if (device_state != 1 or device_handle & 0xff != device_slot
                    or session_handle != handle or physical_index >= physical_count):
                raise ValueError("invalid CSL acquisition handle, session or physical index")
            retained_index = values[5]
            if retained_index >= 1 << 31:
                retained_index -= 1 << 32
            device = {"slot": device_slot, "handle": device_handle,
                      "session_handle": session_handle, "state": device_state,
                      "type": device_type, "flag": values[4],
                      "retained_index": retained_index, "stream_on_order": values[6],
                      "stream_off_order": values[7], "physical_index": physical_index,
                      "attribute": values[9], "classification": word(device_address + 0x80)}
            devices.append(device)
            acquired[device_slot] = device_handle
            if physical_index not in physical_devices:
                physical = root + 0x1d94e0 + physical_index * 0x388
                physical_record = {"index": word(physical), "state": word(physical + 0x10),
                                   "type": word(physical + 0x48),
                                   "fd": word(physical + 0xc4, signed=True),
                                   "reference_count": word(physical + 0x2dc),
                                   "stream_flag": word(physical + 0x384)}
                if physical_record["index"] != physical_index or physical_record["state"] != 1:
                    raise ValueError("invalid CSL physical-device slot or state")
                physical_devices[physical_index] = physical_record
            if physical_devices[physical_index]["type"] != device_type:
                raise ValueError("CSL acquired and physical device types disagree")
        for link_slot in range(128):
            link_address = address + 0xc428 + link_slot * 0x210
            link_handle = word(link_address)
            count, off_flags, enabled = words(link_address + 0x204, 3)
            if link_handle == 0:
                if count or off_flags or enabled:
                    raise ValueError("nonempty CSL link without handle")
                continue
            if link_handle & 0xff != link_slot or not 1 <= count <= 128:
                raise ValueError("invalid CSL link handle slot or count")
            handles = list(words(link_address + 4, count))
            if len(set(handles)) != count:
                raise ValueError("duplicate CSL link acquisition")
            for device_handle in handles:
                if acquired.get(device_handle & 0xff) != device_handle:
                    raise ValueError("CSL link has missing or reused acquisition handle")
            links.append({"slot": link_slot, "handle": link_handle,
                          "device_count": count, "device_handles": handles,
                          "stream_off_flags": off_flags, "stream_enabled": enabled})
        if len(links) != link_count:
            raise ValueError("CSL sparse link count differs from populated slots")
        sessions.append({"slot": slot, "handle": handle, "state": state,
                         "slot_index": index, "stream_flag": stream,
                         "reference_count": references, "open_reference_count": open_references,
                         "creator_pid": creator_pid, "creator_tid": creator_tid,
                         "active_link_count": link_count, "retained_link_handle": retained_link,
                         "devices": devices, "links": links})
    crm = root + 0x1d8dd0
    crm_state = word(crm + 0x10)
    if crm_state not in (0, 1):
        raise ValueError("invalid CSL CRM device state")
    return {"artifact_sha256": ARTIFACT_SHA256, "global": global_state,
            "sessions": sessions,
            "physical_devices": [physical_devices[index] for index in sorted(physical_devices)],
            "crm_fd": word(crm + 0xc4, signed=True) if crm_state == 1 else None}
