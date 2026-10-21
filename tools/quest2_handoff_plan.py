#!/usr/bin/env python3
"""Offline Quest 2 RAM candidates and DT DMA references; never authorizes boot."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile

from quest2_preflight import CaptureError, EXPECTED, MAX_ARTIFACT, validate_iomem, validate_tree


WINDOW = 0x3400000
ALIGNMENT = 0x400000
LIMIT = 1 << 64
PAGE = 0x1000
GIB = 1 << 30


def align_up(value, alignment):
    return (value + alignment - 1) & -alignment


def payload_requirements(image, initramfs, dtb):
    """Size copy destinations only; this is not a payload or boot validator."""
    if any(not data or len(data) > MAX_ARTIFACT for data in (image, initramfs, dtb)):
        raise ValueError("payload inputs must be nonempty and at most 64 MiB each")
    if len(image) < 64 or image[56:60] != b"ARM\x64":
        raise ValueError("raw uncompressed ARM64 Image header required")
    offset, memory, flags = struct.unpack_from("<QQQ", image, 8)
    if (not memory or memory < len(image) or offset % PAGE or offset >= 0x200000 or
            flags & ~0xf or flags & 1 or flags & 6 not in (0, 2) or not flags & 8):
        raise ValueError("unsupported Image memory size, offset or flags")
    if (len(dtb) < 40 or len(dtb) > 0x200000 or
            struct.unpack_from(">II", dtb) != (0xd00dfeed, len(dtb))):
        raise ValueError("one exact-size DTB of at most 2 MiB required")
    kernel_end = offset + memory
    initrd_offset = align_up(kernel_end, PAGE)
    initrd_end = initrd_offset + len(initramfs)
    dtb_offset = align_up(initrd_end, PAGE)
    window = align_up(dtb_offset + len(dtb), ALIGNMENT)
    if window >= LIMIT or initrd_end > 32 * GIB:
        raise ValueError("overflowing copy layout or kernel/initrd span exceeds 32 GiB")

    def metadata(data):
        return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    return {"status": "SIZING_ONLY", "window_bytes": window,
            "alignment_bytes": ALIGNMENT,
            "image": {**metadata(image), "memory_bytes": memory,
                      "text_offset": offset, "flags": flags},
            "initramfs": metadata(initramfs), "dtb": metadata(dtb),
            "offsets": {"kernel_address": offset, "kernel_memory_end": kernel_end,
                        "initramfs_address": initrd_offset, "initramfs_end": initrd_end,
                        "dtb_address": dtb_offset, "dtb_end": dtb_offset + len(dtb)},
            "legacy_format_compatible": (offset == 0x80000 and memory <= 0x2f80000 and
                                         len(initramfs) <= 0x200000),
            "native_window_limit_bytes": 0x4000000,
            "requires_loader_port": window > 0x4000000,
            "requires_payload_validation": True,
            "scope": "Copy destinations only; headers/sizes do not validate target DT contents, "
                     "total OS working RAM, source staging, secure ownership or DMA shutdown"}


def candidates(ram, excluded, window, offsets=None):
    result = []
    last_end = 0
    for lo, hi in sorted(ram):
        parts = [(max(lo, last_end), hi)]
        for ex in excluded:
            parts = [(a, b) for start, end in parts for a, b in
                     ((start, min(end, ex["start"])), (max(start, ex["end"]), end)) if a < b]
        for start, end in sorted(parts):
            base = align_up(start, ALIGNMENT)
            while base + window <= end and len(result) < 8:
                row = {"base": base, "end": base + window, "ownership": "unclaimed"}
                if offsets is not None:
                    initrd_window = base & -GIB
                    if base + offsets["initramfs_end"] - initrd_window > 32 * GIB:
                        base = align_up(base + 1, GIB)
                        continue
                    row.update({key: base + value for key, value in offsets.items()})
                    row["initrd_window_base"] = initrd_window
                result.append(row)
                base += window
                last_end = base
        if len(result) == 8:
            break
    return result


def words(data):
    if len(data) % 4:
        raise ValueError("truncated DT cells")
    return list(struct.unpack(">" + "I" * (len(data) // 4), data))


def cell_count(tree, name):
    value = words(tree.get(name, b""))
    if len(value) != 1 or value[0] not in (1, 2):
        raise ValueError(f"unsupported/missing DT cell count: {name}")
    return value[0]


def ranges(data, ac, sc):
    w = words(data)
    width = ac + sc
    if not w or len(w) % width:
        raise ValueError("invalid DT address/size tuple")
    result = []
    for offset in range(0, len(w), width):
        values = w[offset:offset + width]
        start = int.from_bytes(struct.pack(">" + "I" * ac, *values[:ac]), "big")
        size = int.from_bytes(struct.pack(">" + "I" * sc, *values[ac:]), "big")
        if not size or start + size > LIMIT:
            raise ValueError("empty/overflowing DT reservation")
        result.append((start, start + size))
    return result


def dma_references(tree):
    handles = {}
    for key, data in tree.items():
        node, _, prop = key.rpartition("/")
        if prop not in ("phandle", "linux,phandle"):
            continue
        w = words(data)
        if len(w) != 1 or w[0] in (0, 0xffffffff):
            raise ValueError("invalid DT phandle")
        if w[0] in handles and handles[w[0]] != node:
            raise ValueError("duplicate DT phandle")
        handles[w[0]] = node
    clients = []
    nodes = sorted({k.rpartition("/")[0] for k in tree
                    if k.rpartition("/")[2] in ("iommus", "memory-region")})
    for node in nodes:
        client = {"node": "/" + node, "iommus": [], "memory_regions": [],
                  "state": "unproven"}
        pending = words(tree.get(node + "/iommus", b""))
        while pending:
            handle = pending.pop(0)
            if handle not in handles:
                raise ValueError("unresolved IOMMU phandle")
            provider = handles[handle]
            count = words(tree.get(provider + "/#iommu-cells", b""))
            if len(count) != 1 or count[0] > 8 or len(pending) < count[0]:
                raise ValueError("invalid IOMMU argument cells")
            client["iommus"].append({"provider": "/" + provider, "args": pending[:count[0]]})
            del pending[:count[0]]
        for handle in words(tree.get(node + "/memory-region", b"")):
            if handle not in handles:
                raise ValueError("unresolved memory-region phandle")
            client["memory_regions"].append("/" + handles[handle])
        clients.append(client)
    return {"clients": clients, "quiesced": False,
            "scope": "DT references only; not a complete inventory of DMA masters or live mappings",
            "blockers": ["Device-specific stop, completion, secure ownership and recovery remain unproven"]}


def plan(tree, iomem, *, exclude_blocks=(), payload=None):
    try:
        visibility = validate_iomem(iomem.encode())
    except CaptureError as exc:
        raise ValueError(str(exc)) from exc
    if visibility != "visible":
        raise ValueError("redacted iomem cannot establish placement")
    ac = cell_count(tree, "#address-cells")
    sc = cell_count(tree, "#size-cells")
    if (cell_count(tree, "reserved-memory/#address-cells") != ac or
            cell_count(tree, "reserved-memory/#size-cells") != sc or
            tree.get("reserved-memory/ranges") != b""):
        raise ValueError("reserved-memory must use the root identity address map")
    excluded, ram, blockers = [], [], []
    for line in iomem.splitlines():
        if not line.strip():
            continue
        m = re.fullmatch(r"\s*([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*(\S.*)", line)
        start, end = int(m[1], 16), int(m[2], 16) + 1
        if end > LIMIT:
            raise ValueError("overflowing iomem extent")
        if m[3] == "System RAM":
            ram.append((start, end))
        else:
            excluded.append({"start": start, "end": end, "reason": "iomem: " + m[3]})
    for start, end in exclude_blocks:
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= LIMIT or
                start % ALIGNMENT or end % ALIGNMENT or
                not any(lo <= start < end <= hi for lo, hi in ram)):
            raise ValueError("excluded block must be 4 MiB aligned and inside one System RAM extent")
        excluded.append({"start": start, "end": end,
                         "reason": "Caller-supplied observed block; may be stale, not a reservation"})
    nodes = sorted({k.rpartition("/")[0] for k in tree
                    if k.startswith("reserved-memory/") and k.count("/") == 2})
    for node in nodes:
        if node + "/reg" in tree:
            spans = ranges(tree[node + "/reg"], ac, sc)
            reason = "DT reserved: /" + node
        elif node + "/size" in tree:
            size = words(tree[node + "/size"])
            if len(size) != sc or not any(size):
                raise ValueError("invalid dynamic reservation size")
            if node + "/alloc-ranges" not in tree:
                blockers.append("Unbounded dynamic reservation: /" + node)
                continue
            spans = ranges(tree[node + "/alloc-ranges"], ac, sc)
            reason = "Unresolved dynamic allocation limits: /" + node
        else:
            raise ValueError("reserved-memory child has no reg or size")
        # Keep disabled reservations excluded as well: do not assume firmware
        # releases their memory merely because Linux does not probe a client.
        excluded.extend({"start": lo, "end": hi, "reason": reason} for lo, hi in spans)
    fixed = [] if blockers else candidates(ram, excluded, WINDOW)
    compact = {"status": "MISSING_INPUTS", "candidates": [],
               "blockers": ["Supply an Image, initramfs and one DTB for copy sizing"]}
    if payload is not None:
        compact = payload_requirements(*payload)
        compact["candidates"] = ([] if blockers else
                                 candidates(ram, excluded, compact["window_bytes"], compact["offsets"]))
        compact["blockers"] = blockers + [
            "Compact staging requires payload_base and a validated matching DTB; ram_base and "
            "ram_bytes select only a RAM diagnostic, not payload relocation",
            "Native RAM ownership, target DT preservation and hardware shutdown remain unproven"]
        if compact["requires_loader_port"]:
            compact["blockers"].append("Copy envelope exceeds the native loader's 64 MiB limit")
        if not compact["candidates"] and not blockers:
            compact["blockers"].append("No conservatively usable compact copy extent found")
    ram_blockers = blockers + ([] if fixed or blockers else
                               ["No conservatively usable 52 MiB extent found"])
    return {"schema_version": 1, "status": "HANDOFF_BLOCKED",
            "ram": {"window_bytes": WINDOW, "alignment_bytes": ALIGNMENT,
                    "candidates": fixed, "exclusions": excluded, "blockers": ram_blockers,
                    "scope": "Snapshot candidates only; native allocator claim still required"},
            "payload": compact,
            "dma": dma_references(tree)}


def load_capture(root):
    root = Path(root)
    manifest = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    if (manifest.get("capture_status") != "complete" or
            any(manifest.get("identity", {}).get(k) != v for k, v in EXPECTED.items()) or
            manifest.get("identity", {}).get("root_uid") != "0"):
        raise ValueError("complete matching rooted Quest 2 capture required")
    data = {}
    for key, filename in (("runtime_tree", "runtime-tree.tar"), ("iomem", "iomem.txt")):
        meta = manifest.get("artifacts", {}).get(key, {})
        if meta.get("filename") != filename or (root / filename).stat().st_size > MAX_ARTIFACT:
            raise ValueError("invalid capture artifact name/size")
        data[key] = (root / filename).read_bytes()
        if (len(data[key]) != meta.get("bytes") or
                hashlib.sha256(data[key]).hexdigest() != meta.get("sha256")):
            raise ValueError("capture artifact digest/length mismatch")
    try:
        validate_tree(data["runtime_tree"])
    except CaptureError as exc:
        raise ValueError(str(exc)) from exc
    tree = {}
    with tarfile.open(fileobj=io.BytesIO(data["runtime_tree"]), mode="r:") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if member.isdir():
                continue
            if (not member.isfile() or PurePosixPath(name).is_absolute() or
                    ".." in PurePosixPath(name).parts or name in tree):
                raise ValueError("invalid/duplicate DT archive property")
            tree[name] = archive.extractfile(member).read()
    return tree, data["iomem"].decode("ascii")


def read_payload(path):
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT:
        raise ValueError("payload must be a regular file of at most 64 MiB")
    with path.open("rb") as stream:
        data = stream.read(MAX_ARTIFACT + 1)
    if not data or len(data) > MAX_ARTIFACT:
        raise ValueError("empty/oversize payload input")
    return data


def block_argument(value):
    try:
        start, end = value.split(":")
        return int(start, 0), int(end, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected half-open START:END (e.g. 0x102c00000:0x103000000)") from exc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True, help="new host JSON report")
    ap.add_argument("--exclude-block", type=block_argument, action="append", default=[],
                    help="repeatable observed 4 MiB aligned block, half-open START:END")
    ap.add_argument("--kernel-image", type=Path, help="raw uncompressed ARM64 Image for sizing")
    ap.add_argument("--initramfs", type=Path)
    ap.add_argument("--dtb", type=Path, help="one DTB; not a concatenated bundle")
    args = ap.parse_args(argv)
    try:
        paths = (args.kernel_image, args.initramfs, args.dtb)
        if any(paths) and not all(paths):
            raise ValueError("--kernel-image, --initramfs and --dtb must be supplied together")
        payload = tuple(read_payload(path) for path in paths) if all(paths) else None
        report = plan(*load_capture(args.capture), exclude_blocks=args.exclude_block, payload=payload)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, KeyError) as exc:
        ap.exit(2, f"PLAN_FAILED; HANDOFF_BLOCKED: {exc}\n")
    print(f"PLAN_COMPLETE; HANDOFF_BLOCKED; {len(report['ram']['candidates'])} unclaimed candidates")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
