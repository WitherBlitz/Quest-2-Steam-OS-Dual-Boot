"""Bounded BTF/layout gates for the observational diagnostic, no device access."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[2]


def btf_fixture(*, count_offset=256, duplicate=False, duplicate_mutex=False):
    strings = b"\0int\0sample\0lock\0count\0mutex\0alias\0"
    no = lambda s: strings.index(s.encode()+b"\0")
    record = lambda name, kind, size, extra=b"", length=0: struct.pack("<III",no(name) if name else 0,kind<<24|length,size)+extra
    types = record("int",1,4,struct.pack("<I",1<<24|32))
    types += record("alias",8,1)
    types += record("mutex",4,32)
    members = struct.pack("<III",no("lock"),3,0)+struct.pack("<III",no("count"),2,count_offset)
    types += record("sample",4,64,members,2)
    if duplicate: types += record("sample",4,64,members,2)
    if duplicate_mutex: types += record("mutex",4,48)
    return struct.pack("<HBBIIIII",0xeb9f,1,0,24,0,len(types),len(types),len(strings))+types+strings


def elf_fixture(*, executable=True, shifted=False, bad_code=False):
    names=b"\0.shstrtab\0.strtab\0.symtab\0.BTF\0.text\0.data\0"
    strings=b"\0cam_req_mgr_is_open\0g_dev\0g_crm_core_dev\0"
    address=0xffffff8008b24518
    code=bytes.fromhex("fd7bbea9f44f01a9fd030091d49600b09452059193120091e00313aa8ad31394880240b9e00313aa1f010071f4079f1a98d31394e003142af44f41a9fd7bc2a8c0035fd6")
    if bad_code: code=b"\0"+code[1:]
    symbols=bytes(24)
    for name,section,value,size in [("cam_req_mgr_is_open",5,address,68),
                                   ("g_dev",6,address+0x12d8c00+int(shifted),112),
                                   ("g_crm_core_dev",6,address+0x12d8210,8)]:
        symbols+=struct.pack("<IBBHQQ",strings.index(name.encode()),0x10,0,section,value,size)
    sections=[("",0,b"",0,0,0),(".shstrtab",3,names,0,0,0),
              (".strtab",3,strings,0,0,0),(".symtab",2,symbols,2,24,0),
              (".BTF",1,btf_fixture(),0,0,0),(".text",1,code,0,0,address),
              (".data",1,bytes(8),0,0,address+0x12d8210)]
    data=bytearray(64);headers=[]
    for name,kind,blob,link,entsize,va in sections:
        offset=len(data);data+=blob
        headers.append(struct.pack("<IIQQQQIIQQ",names.index(name.encode()+b"\0") if name else 0,
                                   kind,0,va,offset,len(blob),link,0,1,entsize))
    table=len(data);data+=b"".join(headers)
    data[:64]=struct.pack("<16sHHIQQQIHHHHHH",b"\x7fELF\x02\x01\x01"+bytes(9),2 if executable else 1,
                         183,1,0,0,table,0,64,0,0,64,len(headers),1)
    return bytes(data)


class DiagnosticProfileTests(unittest.TestCase):
    def module(self):
        path = ROOT/"tools/quest2_diag_profile.py"
        self.assertTrue(path.is_file(), "diagnostic BTF profile gate is missing")
        spec = importlib.util.spec_from_file_location("diag_profile",path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_struct_member_offsets_and_typedef_width(self):
        module = self.module()
        shape = module.BTF(btf_fixture()).structure("sample",{"LOCK":"lock","COUNT":"count"})
        self.assertEqual(shape["size"],64)
        self.assertEqual(shape["fields"]["LOCK"]["offset"],0)
        self.assertEqual(shape["fields"]["LOCK"]["size"],32)
        self.assertEqual(shape["fields"]["COUNT"]["offset"],32)
        self.assertEqual(shape["fields"]["COUNT"]["size"],4)

    def test_wrong_magic_or_truncated_tables_refused(self):
        module = self.module(); valid = btf_fixture()
        for data in (b"BAD!"+valid[4:],valid[:20],valid[:-1]):
            with self.subTest(length=len(data)),self.assertRaises(ValueError): module.BTF(data)

    def test_missing_or_ambiguous_structure_refused(self):
        module = self.module()
        with self.assertRaises(ValueError): module.BTF(btf_fixture()).structure("absent",{})
        with self.assertRaises(ValueError): module.BTF(btf_fixture(duplicate=True)).structure("sample",{})

    def test_unaligned_or_out_of_bounds_member_refused(self):
        module = self.module()
        for offset in (257,1024):
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                module.BTF(btf_fixture(count_offset=offset)).structure("sample",{"COUNT":"count"})

    def test_layout_mismatch_refused(self):
        module = self.module()
        a=module.BTF(btf_fixture()).structure("sample",{"COUNT":"count"})
        b=module.BTF(btf_fixture(count_offset=288)).structure("sample",{"COUNT":"count"})
        with self.assertRaisesRegex(ValueError,"layout"):
            module.compare_layout({"SAMPLE":a},{"SAMPLE":b})

    def test_generated_header_binds_all_offsets(self):
        module = self.module()
        shape=module.BTF(btf_fixture()).structure("sample",{"COUNT":"count"})
        header=module.header({"SAMPLE":shape})
        self.assertIn("#define QCD_SAMPLE_COUNT 32",header)
        self.assertIn("#define QCD_SAMPLE_SIZE 64",header)

    def test_member_type_anchor_resolves_same_named_structures_without_guessing(self):
        module = self.module(); btf=module.BTF(btf_fixture(duplicate_mutex=True))
        with self.assertRaises(ValueError): btf.structure("mutex",{})
        self.assertEqual(btf.structure("sample.lock",{})["size"],32)

    def test_compiled_anchor_and_relative_static_mapping(self):
        btf,code=self.module().elf_data(elf_fixture())
        self.assertEqual(btf,btf_fixture())
        self.assertTrue(code["static_relative_addresses_match"])
        self.assertEqual(code["anchor_bytes"],68)

    def test_changed_compiled_code_or_static_relationship_refused(self):
        for kwargs in ({"bad_code":True},{"shifted":True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                self.module().elf_data(elf_fixture(**kwargs))

    def test_truncated_or_non_executable_vmlinux_refused(self):
        for data in (elf_fixture()[:-1],elf_fixture(executable=False)):
            with self.subTest(length=len(data)),self.assertRaises(ValueError): self.module().elf_data(data)


if __name__ == "__main__": unittest.main()
