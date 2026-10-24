#!/usr/bin/env python3
"""Apply pinned entry-accounting wrappers to an offline Horizon source tree.

No compiler, source-tree command, device access or deployment is performed.
Use a quiescent, disposable source copy: replacement is atomic per file, not
across the tree. An incomplete receipt marker requires inspection after failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "6bde5fc06b252c97761bc0963dd13c1abcf377ab"
MANIFEST_PATHS = tuple(PROJECT_ROOT / "patches/horizon-owner" / name
                       for name in ("camera-sites.json", "fastrpc-sites.json"))
BUILD_HASHES = {
    "kernel/Makefile": "297ffe866e344ee073cf09ab9b31fc66441940d1e129cbe8cd72e3e239963a4f",
    "lib/Kconfig.debug": "bd4d5f10bf66f6bc2d0a227919ef40405ed084f65614df843d9af640cd5c1e1b",
}
CORE_FILES = {
    "kernel/qkx_owner_gate.c": PROJECT_ROOT / "kernel/owner_gate.c",
    "include/linux/qkx_owner_gate.h": PROJECT_ROOT / "kernel/owner_gate.h",
}
IDENTIFIER = re.compile(r"[A-Za-z_]\w*", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}")
RESERVED_ARGS = {"qkx_entry_token", "qkx_entry_result"}
KCONFIG_ENTRY = b"""config QKX_OWNER_GATE
\tbool "Experimental camera/FastRPC entry accounting"
\tdefault n
\thelp
\t  Account only the explicitly annotated camera and FastRPC entries.
\t  This is not complete resource ownership, a checked drain controller,
\t  DMA quiescence or a shutdown/handoff implementation. Leave disabled
\t  unless testing a separately built development kernel.

"""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _keys(value, keys, what):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid " + what + " schema")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _relative(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        raise ValueError("invalid relative path")
    parts = name.split("/")
    devices = {"CON", "PRN", "AUX", "NUL"}
    devices.update("COM" + str(i) for i in range(1, 10))
    devices.update("LPT" + str(i) for i in range(1, 10))
    if any(not part or part in (".", "..") or part.endswith(".") or
           part.split(".")[0].upper() in devices for part in parts):
        raise ValueError("invalid relative path: " + name)
    return name


def _no_links(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("symlink/junction path refused: " + str(item))


def _root(path):
    root = Path(path)
    if not root.is_absolute():
        raise ValueError("absolute source directory required")
    if ".." in root.parts:
        raise ValueError("source path must not contain parent traversal")
    _no_links(root)
    if not root.is_dir():
        raise ValueError("existing source directory required")
    return root.resolve(strict=True)


def _member(root, name, *, new=False):
    path = root / _relative(name)
    _no_links(path)
    if not path.parent.is_dir():
        raise ValueError("missing source parent path: " + name)
    if new:
        if os.path.lexists(path):
            raise ValueError("new destination already exists: " + name)
    elif not path.exists() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("regular source file required: " + name)
    return path


def _mask_c(text):
    """Blank comments/literals without changing byte offsets or line breaks."""
    masked = list(text)
    pos = 0
    while pos < len(text):
        start = pos
        if text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = len(text) if end < 0 else end
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                raise ValueError("unterminated C comment")
            pos = end + 2
        elif text[pos] in ("'", '"'):
            quote = text[pos]
            pos += 1
            while pos < len(text):
                if text[pos] == "\\":
                    pos += 2
                elif text[pos] == quote:
                    pos += 1
                    break
                elif text[pos] in "\r\n":
                    raise ValueError("unterminated C literal")
                else:
                    pos += 1
            else:
                raise ValueError("unterminated C literal")
        else:
            pos += 1
            continue
        for index in range(start, min(pos, len(text))):
            if text[index] not in "\r\n":
                masked[index] = " "
    return "".join(masked)


def _events(masked, start=0, end=None):
    # Match whole logical directives so braces in macros are not C scopes.
    pattern = r"^[ \t]*\#(?:[^\n]*\\\n)*[^\n]*|[{}]"
    expression = re.compile(pattern, re.M)
    yield from expression.finditer(masked, start, len(masked) if end is None else end)


def _scope(events, *, stop_at_close=False):
    depth = 0
    branches = []
    for event in events:
        value = event.group()
        if value == "{":
            depth += 1
        elif value == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced C scope")
            if stop_at_close and depth == 0:
                if branches:
                    raise ValueError("function ends inside a conditional branch")
                return event.end()
        else:
            directive = re.match(r"\s*#\s*(\w+)", value)
            word = directive.group(1) if directive else ""
            if word in ("if", "ifdef", "ifndef"):
                branches.append([depth, [], False])
            elif word in ("elif", "else"):
                if not branches or branches[-1][2]:
                    raise ValueError("invalid C conditional scope")
                branch = branches[-1]
                branch[1].append(depth)
                depth = branch[0]
                branch[2] = word == "else"
            elif word == "endif":
                if not branches:
                    raise ValueError("unmatched C conditional")
                base, ends, has_else = branches.pop()
                ends.append(depth)
                if not has_else:
                    ends.append(base)
                if len(set(ends)) != 1:
                    raise ValueError("inconsistent C conditional brace scope")
                depth = ends[0]
    if stop_at_close:
        raise ValueError("unterminated C function")
    return depth, branches


def _site(site):
    _keys(site, ("name", "signature", "function_sha256", "args", "result", "policy"), "site")
    name, signature, args = site["name"], site["signature"], site["args"]
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name.endswith("_qkx_body"):
        raise ValueError("invalid function identifier")
    if not isinstance(signature, str) or signature != signature.rstrip():
        raise ValueError("invalid function signature whitespace")
    if not isinstance(site["function_sha256"], str) or not SHA256.fullmatch(site["function_sha256"]):
        raise ValueError("invalid function hash")
    if site["result"] not in ("int", "long", "void", "ssize_t") or site["policy"] not in ("reject", "observe"):
        raise ValueError("unsupported result or policy")
    if site["policy"] == "reject" and site["result"] == "void":
        raise ValueError("void entry cannot reject")
    if not isinstance(args, list) or any(not isinstance(a, str) or not IDENTIFIER.fullmatch(a) for a in args):
        raise ValueError("invalid function arguments")
    if len(set(args)) != len(args) or RESERVED_ARGS.intersection(args):
        raise ValueError("duplicate or reserved function arguments")
    pattern = (r"(?:(?:static|inline)\s+)*" + re.escape(site["result"]) +
               r"\s+" + re.escape(name) + r"\(([^;{}()]*)\)")
    match = re.fullmatch(pattern, signature)
    if not match:
        raise ValueError("unsupported function signature: " + name)
    params = match.group(1)
    if "..." in params or "[" in params or "]" in params:
        raise ValueError("unsupported function declarator: " + name)
    declared = [] if params.strip() == "void" else params.split(",")
    identifiers = [re.search(r"([A-Za-z_]\w*)\s*$", param, re.ASCII) for param in declared]
    if any(m is None for m in identifiers) or [m.group(1) for m in identifiers] != args:
        raise ValueError("signature argument order mismatch: " + name)


def _wrap_file(data, sites, domain):
    # Latin-1 is a byte-preserving view, including UTF-8/non-ASCII comments.
    text = data.decode("latin-1")
    masked = _mask_c(text)
    if "qkx_owner_gate.h" in text or "_qkx_body" in masked:
        raise ValueError("source already contains owner-gate integration")
    edits = []
    for site in sites:
        name, signature = site["name"], site["signature"]
        matches = list(re.finditer(r"(?m)^" + re.escape(signature) + r"\s*\{", masked))
        definitions = list(re.finditer(
            r"(?m)^[ \t]*(?:(?:static|inline)\s+)*(?:int|long|void|ssize_t)\s+" +
            re.escape(name) + r"\s*\([^;{}]*\)\s*\{", masked))
        if len(matches) != 1 or len(definitions) != 1:
            raise ValueError("exact unique function signature required: " + name)
        match = matches[0]
        if _scope(_events(masked, end=match.start()))[0] != 0:
            raise ValueError("function is not at file scope: " + name)
        opening = match.end() - 1
        end = _scope(_events(masked, start=opening), stop_at_close=True)
        original = text[match.start():end]
        if _sha(original.encode("latin-1")) != site["function_sha256"]:
            raise ValueError("function hash mismatch: " + name)
        renamed = re.sub(r"\b" + re.escape(name) + r"(?=\s*\()", name + "_qkx_body",
                         original, count=1)
        kind = "SUBMIT" if site["policy"] == "reject" else "LIFECYCLE"
        result = site["result"]
        lines = [signature + ";", "", renamed, "", signature, "{",
                 "\tstruct qkx_owner_token qkx_entry_token = { 0 };"]
        if result != "void":
            lines.append("\t" + result + " qkx_entry_result;")
        enter = ("qkx_owner_enter(QKX_OWNER_" + domain.upper() + ", QKX_OWNER_" + kind +
                 ", &qkx_entry_token)")
        if site["policy"] == "reject":
            lines.extend(["\tqkx_entry_result = " + enter + ";", "\tif (qkx_entry_result)",
                          "\t\treturn qkx_entry_result;"])
        else:
            lines.append("\t(void)" + enter + ";")
        call = name + "_qkx_body(" + ", ".join(site["args"]) + ");"
        lines.append("\t" + ("qkx_entry_result = " if result != "void" else "") + call)
        lines.append("\tqkx_owner_leave(&qkx_entry_token);")
        if result != "void":
            lines.append("\treturn qkx_entry_result;")
        lines.append("}")
        edits.append((match.start(), end, "\n".join(lines)))
    ordered = sorted(edits)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("overlapping function definitions")
    include = re.search(r"(?m)^[ \t]*#[ \t]*include\b[^\n]*(?:\n|$)", masked)
    if not include or _scope(_events(masked, end=include.start())) != (0, []):
        raise ValueError("unconditional first source include required")
    edits.append((include.start(), include.start(), "#include <linux/qkx_owner_gate.h>\n"))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode("latin-1")


def _plan(source_root):
    root = _root(source_root)
    originals, changes, manifests = {}, {}, {}
    counts = {"sites": 0, "source_files": 0, "reject": 0, "observe": 0}
    domains = set()
    for path in MANIFEST_PATHS:
        raw = Path(path).read_bytes()
        manifest = json.loads(raw, object_pairs_hook=_json_object)
        _keys(manifest, ("schema_version", "domain", "files"), "manifest")
        domain = manifest["domain"]
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
            raise ValueError("unsupported manifest schema version")
        if domain not in ("camera", "fastrpc") or domain in domains:
            raise ValueError("invalid or duplicate manifest domain")
        domains.add(domain)
        manifests[Path(path).name] = _sha(raw)
        if not isinstance(manifest["files"], list) or not manifest["files"]:
            raise ValueError("manifest files required")
        counts[domain + "_sites"] = 0
        for entry in manifest["files"]:
            _keys(entry, ("path", "sha256", "sites"), "file")
            name = _relative(entry["path"])
            if name in originals or name in BUILD_HASHES or name in CORE_FILES:
                raise ValueError("duplicate or reserved source path: " + name)
            if not isinstance(entry["sha256"], str) or not SHA256.fullmatch(entry["sha256"]):
                raise ValueError("invalid source hash: " + name)
            data = _member(root, name).read_bytes()
            if _sha(data) != entry["sha256"]:
                raise ValueError("original source hash mismatch: " + name)
            sites = entry["sites"]
            if not isinstance(sites, list) or not sites:
                raise ValueError("file sites required")
            names = set()
            for site in sites:
                _site(site)
                if site["name"] in names:
                    raise ValueError("duplicate function site: " + site["name"])
                names.add(site["name"])
                counts[site["policy"]] += 1
            originals[name] = data
            changes[name] = _wrap_file(data, sites, domain)
            counts["sites"] += len(sites)
            counts[domain + "_sites"] += len(sites)
            counts["source_files"] += 1
    for name, expected in BUILD_HASHES.items():
        data = _member(root, name).read_bytes()
        if _sha(data) != expected:
            raise ValueError("original build file hash mismatch: " + name)
        if b"QKX_OWNER_GATE" in data:
            raise ValueError("build integration already applied")
        originals[name] = data
        if name == "kernel/Makefile":
            changes[name] = data + b"\nobj-$(CONFIG_QKX_OWNER_GATE) += qkx_owner_gate.o\n"
        else:
            marker = b"endmenu # Kernel hacking"
            if data.count(marker) != 1:
                raise ValueError("unique pinned Kconfig menu marker required")
            changes[name] = data.replace(marker, KCONFIG_ENTRY + marker)
    for name, path in CORE_FILES.items():
        _member(root, name, new=True)
        _no_links(Path(path))
        originals[name] = None
        changes[name] = Path(path).read_bytes()
    counts["files"] = len(changes)
    report = {
        "schema_version": 1, "status": "SOURCE_PATCH_APPLIED", "source_commit": SOURCE_COMMIT,
        "source_root": str(root), "manifest_sha256": manifests, "counts": counts,
        "new_option": {"name": "CONFIG_QKX_OWNER_GATE", "type": "bool", "default": "n"},
        "scope": "entry_accounting_only", "deployment": False, "full_ownership": False,
        "files": {name: {"original_sha256": _sha(originals[name]) if originals[name] is not None else None,
                         "original_bytes": len(originals[name]) if originals[name] is not None else None,
                         "patched_sha256": _sha(data), "patched_bytes": len(data)}
                  for name, data in changes.items()},
    }
    return root, changes, originals, report


def prepare_changes(source_root):
    """Validate every input and return all generated files without writing."""
    return _plan(source_root)[1]


def _write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def apply(source_root, receipt):
    """Apply a complete in-memory plan, retaining a marker on partial failure."""
    receipt = Path(receipt).absolute()
    if ".." in receipt.parts:
        raise ValueError("receipt path must not contain parent traversal")
    _no_links(receipt)
    receipt = receipt.resolve(strict=False)
    marker = receipt.with_name(receipt.name + ".incomplete")
    _no_links(marker)
    if not receipt.parent.is_dir() or os.path.lexists(receipt) or os.path.lexists(marker):
        raise ValueError("new receipt and incomplete-marker paths required")
    root, changes, originals, report = _plan(source_root)
    stages = {name: _member(root, name + ".qkx-owner-stage", new=True) for name in changes}
    if receipt in stages.values() or marker in stages.values() or receipt in (root / n for n in changes) or marker in (root / n for n in changes):
        raise ValueError("receipt path overlaps source integration")
    incomplete = {"status": "INCOMPLETE", "source_commit": SOURCE_COMMIT,
                  "source_root": str(root), "intended_files": list(changes),
                  "deployment": False, "full_ownership": False}
    _write_new(marker, (json.dumps(incomplete, indent=2) + "\n").encode())
    # No catch removes the marker: a write/replace/hash failure remains explicit.
    for name, data in changes.items():
        _write_new(stages[name], data)
    for name, data in changes.items():
        target = _member(root, name, new=originals[name] is None)
        if originals[name] is not None and target.read_bytes() != originals[name]:
            raise ValueError("source changed after preflight: " + name)
        os.replace(stages[name], target)
    for name, data in changes.items():
        if _member(root, name).read_bytes() != data:
            raise ValueError("written source verification failed: " + name)
    _write_new(receipt, (json.dumps(report, indent=2) + "\n").encode())
    marker.unlink()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = apply(args.source, args.receipt)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, "OWNER_SOURCE_PATCH_FAILED; HANDOFF_BLOCKED: " + str(exc) + "\n")
    print("OWNER_SOURCE_PATCH_APPLIED; ENTRY_ACCOUNTING_ONLY; HANDOFF_BLOCKED; sites=" +
          str(report["counts"]["sites"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
