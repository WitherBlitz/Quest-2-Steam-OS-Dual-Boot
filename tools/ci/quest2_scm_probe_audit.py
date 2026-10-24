#!/usr/bin/env python3
"""Offline fixed-query module audit; missing stock SCM CRC proof stays explicit."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from quest2_audit import CONFIG_SHA, cstring, inspect_module, load_symvers

SCM_APIS = {"scm_call2_noretry", "scm_is_call_available"}
ALLOWED_IMPORTS = SCM_APIS | {
    "module_layout", "__stack_chk_fail", "__stack_chk_guard", "bcmp", "memset",
    "init_uts_ns", "of_machine_is_compatible", "of_property_read_string", "of_root",
    "param_ops_bool", "printk", "strcmp", "strncmp",
}


def inspect_probe(data, generated, stock):
    # Reuse the ELF/generated-CRC checks. Captured stock evidence is checked
    # separately below; generated SCM CRCs must never be labelled stock proof.
    result = inspect_module(data, generated, generated)
    imports, rows = set(result["undefined_symbols"]), result["crc_rows"]
    if (not SCM_APIS <= imports or not imports <= ALLOWED_IMPORTS
            or set(rows) != imports | {"module_layout"}):
        raise ValueError("Unexpected fixed-probe imports or incomplete exact version coverage")
    pending = {}
    for name, text in rows.items():
        crc = int(text, 16)
        if name in SCM_APIS and name not in stock:
            pending[name] = text
        elif stock.get(name) != crc:
            raise ValueError("Missing or mismatching captured stock CRC: " + name)

    table = struct.unpack_from("<Q", data, 40)[0]
    stride, count, names_index = struct.unpack_from("<HHH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, table+i*stride) for i in range(count)]
    names_section = sections[names_index]
    names = data[names_section[4]:names_section[4]+names_section[5]]
    infos = []
    for section in sections:
        name = cstring(names, section[0])
        if name in (".ctors", ".init_array", ".preinit_array") and section[5]:
            raise ValueError("Fixed probe must not run constructors")
        if name == ".modinfo":
            infos.append(data[section[4]:section[4]+section[5]])
    if len(infos) != 1:
        raise ValueError("Unambiguous fixed-probe metadata required")
    info = infos[0].split(b"\0")
    if ([p for p in info if p.startswith(b"parmtype=")] != [b"parmtype=execute:bool"]
            or [p for p in info if p.startswith(b"license=")] != [b"license=GPL"]
            or any(p.startswith(b"depends=") and p != b"depends=" for p in info)):
        raise ValueError("Fixed bool parameter, GPL metadata and no module dependencies required")
    return {**result, "stock_unverified_imports": pending,
            "runtime_import_acceptance_validated": False, "secure_query_validated": False,
            "driver_execution_validated": False, "execution_ready": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--symvers", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--compile-command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    if sha(args.config) != CONFIG_SHA:
        raise ValueError("Probe build changed the approved stock configuration")
    if "-DQKX_ENABLE_EXPERIMENTAL_JUMP" in args.compile_command.read_text():
        raise ValueError("Experimental jump flag is forbidden")
    stock_path = Path(__file__).with_name("quest2_stock_crcs.json")
    stock = {k: int(v, 16) for k, v in json.loads(stock_path.read_text())["symbols"].items()}
    data = args.module.read_bytes()
    result = inspect_probe(data, load_symvers(args.symvers), stock)
    report = {"schema_version": 1,
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "kernel_commit": "6bde5fc06b252c97761bc0963dd13c1abcf377ab",
              "config_sha256": CONFIG_SHA, "module_symvers_sha256": sha(args.symvers),
              "stock_reference_sha256": sha(stock_path),
              "compile_command_sha256": sha(args.compile_command),
              "module": {"file": args.module.name, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest(), **result},
              "scope": "native_ELF_generated_CRC_and_available_captured_stock_CRC_checks",
              "default_load_tested": False, "secure_call_performed": False,
              "device_mutation": False, "execution_ready": False,
              "status": "OFFLINE_PROBE_BUILD_RUNTIME_VALIDATION_REQUIRED"}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print("Fixed-probe native ELF gates passed; runtime import acceptance and secure query remain unvalidated")


if __name__ == "__main__":
    main()
