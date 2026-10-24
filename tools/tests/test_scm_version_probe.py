"""Execute actual fixed-query module functions against controlled public APIs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

HEADER = r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
/* The fixture executes Linux module code even when the host CRT is Windows. */
#undef EPROTO
#undef ESTALE
#undef EOPNOTSUPP
#define EPROTO 71
#define ESTALE 116
#define EOPNOTSUPP 95
#define CONFIG_QCOM_SCM 1
#define __init
#define __exit
#define module_param(a,b,c)
#define MODULE_PARM_DESC(a,b)
#define MODULE_LICENSE(a)
#define MODULE_DESCRIPTION(a)
#define module_init(a)
#define module_exit(a)
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define SCM_SVC_PIL 2
#define SCM_SIP_FNID(s,c) (0x02000000u | ((s)<<8) | (c))
#define SCM_ARGS(n) (n)
typedef uint32_t u32;
typedef uint64_t u64;
struct scm_desc { u32 arginfo; u64 args[10]; u64 ret[3]; u64 private_unused[8]; };
struct device_node { int unused; };
static struct device_node node;
static struct device_node *of_root = &node;
static struct { struct { char release[96]; } name; } init_uts_ns = {
    {"4.19.325-cip128-st12-g49638c7a8637"}
};
static const char *fake_model = "Hollywood";
static bool fake_compatible = true;
static unsigned int query_calls, availability_calls, fail_at;
static int available = 1, api_status, property_error;
static u64 versions[3] = {3, 2, 3}, extra1, extra2;
static char last_log[512];
static void fake_log(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vsnprintf(last_log, sizeof(last_log), format, args);
    va_end(args);
}
#define pr_info fake_log
#define pr_err fake_log
static int of_property_read_string(const struct device_node *n, const char *key, const char **out)
{
    if (n != &node || strcmp(key, "model")) abort();
    if (property_error) return property_error;
    *out = fake_model;
    return 0;
}
static bool of_machine_is_compatible(const char *name)
{
    if (strcmp(name, "qcom,kona")) abort();
    return fake_compatible;
}
static int scm_is_call_available(u32 service, u32 command)
{
    if (service != 2 || command != 15) abort();
    availability_calls++;
    return available;
}
static int scm_call2_noretry(u32 id, struct scm_desc *desc)
{
    const u32 inputs[] = {0, 7, 28};
    unsigned int i, index = query_calls++;
    if (index >= 3 || id != 0x0200020f || desc->arginfo != 1 || desc->args[0] != inputs[index]) abort();
    for (i=1; i<10; i++) if (desc->args[i]) abort();
    for (i=0; i<3; i++) if (desc->ret[i]) abort();
    for (i=0; i<8; i++) if (desc->private_unused[i]) abort();
    if (fail_at == index+1) return api_status;
    desc->ret[0] = versions[index]; desc->ret[1] = extra1; desc->ret[2] = extra2;
    return 0;
}
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    const char *name;
    int result;
    if (argc != 2) return 2;
    name = argv[1];
    if (strcmp(name, "dry")) execute = true;
    if (!strcmp(name, "max")) { versions[0]=64; versions[1]=17; versions[2]=64; }
    if (!strcmp(name, "zero")) versions[0]=versions[1]=versions[2]=0;
    if (!strcmp(name, "unavailable")) available=0;
    if (!strcmp(name, "availability_error")) available=-EBUSY;
    if (!strcmp(name, "availability_unexpected")) available=2;
    if (!strncmp(name, "api", 3)) { fail_at=(unsigned int)atoi(name+3); api_status=-EBUSY; }
    if (!strcmp(name, "positive_status")) { fail_at=1; api_status=1; }
    if (!strcmp(name, "extra1")) extra1=1;
    if (!strcmp(name, "extra2")) extra2=1;
    if (!strcmp(name, "xbl_overflow")) versions[0]=65;
    if (!strcmp(name, "tz_overflow")) versions[1]=18;
    if (!strcmp(name, "abl_overflow")) versions[2]=65;
    if (!strcmp(name, "wide_result")) versions[0]=0x100000000ull;
    if (!strcmp(name, "alias_mismatch")) versions[2]=4;
    if (!strcmp(name, "bad_release")) memcpy(init_uts_ns.name.release, "other", 6);
    if (!strcmp(name, "bad_model")) fake_model="other";
    if (!strcmp(name, "property_error")) property_error=-EINVAL;
    if (!strcmp(name, "no_root_node")) of_root=NULL;
    if (!strcmp(name, "bad_compatible")) fake_compatible=false;
    result = scm_version_probe_init();
    scm_version_probe_exit();
    printf("%d %u %u\n%s", result, query_calls, availability_calls, last_log);
    return 0;
}
'''


class ScmVersionProbeTests(unittest.TestCase):
    def run_case(self, name, *, real_scm=True):
        source = ROOT / "module/scm_version_probe.c"
        self.assertTrue(source.is_file(), "default-no-SMC fixed-query module missing")
        compiler = os.environ.get("HOST_TEST_CC") or shutil.which("clang")
        self.assertTrue(compiler, "host C compiler required")
        with tempfile.TemporaryDirectory(prefix="scm-query-host-") as temp:
            root = Path(temp)
            header = HEADER if real_scm else HEADER.replace("#define CONFIG_QCOM_SCM 1", "")
            (root/"host.h").write_text(header, encoding="utf-8")
            for name_part in ("linux/module.h", "linux/of.h", "linux/string.h", "linux/utsname.h", "soc/qcom/scm.h"):
                path = root/name_part
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#include "host.h"\n', encoding="utf-8")
            harness = root/"test.c"
            harness.write_text('#include "host.h"\n#include "'+source.as_posix()+'"\n'+HARNESS, encoding="utf-8")
            binary = root/("test.exe" if os.name == "nt" else "test")
            build = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                    "-I", str(root), str(harness), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            if not real_scm:
                self.assertNotEqual(build.returncode, 0)
                self.assertIn("The real public SCM implementation is required", build.stderr)
                return None
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(binary), name], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            fields, _, log = result.stdout.partition("\n")
            return tuple(map(int, fields.split())), log

    def test_default_dry_run_never_calls_secure_api(self):
        fields, log = self.run_case("dry")
        self.assertEqual(fields, (0, 0, 0))
        self.assertIn("dry_run=1", log)
        self.assertNotIn("query=1", log)

    def test_missing_real_scm_configuration_is_a_compile_error(self):
        self.run_case("dry", real_scm=False)

    def test_fixed_ids_and_zero_descriptor_success(self):
        fields, log = self.run_case("success")
        self.assertEqual(fields, (0, 3, 1))
        self.assertIn("xbl=3 tz=2 abl=3", log)
        self.assertIn("query=1", log)

    def test_bounds_allow_zero_and_maximum_counts(self):
        for name in ("zero", "max"):
            with self.subTest(name=name):
                self.assertEqual(self.run_case(name)[0], (0, 3, 1))

    def test_profile_mismatch_precedes_every_secure_call(self):
        for name in ("bad_release", "bad_model", "property_error", "no_root_node", "bad_compatible"):
            with self.subTest(name=name):
                self.assertEqual(self.run_case(name)[0], (-22, 0, 0))

    def test_availability_failure_short_circuits_queries(self):
        for name, error in (("unavailable", -95), ("availability_error", -16), ("availability_unexpected", -95)):
            with self.subTest(name=name):
                self.assertEqual(self.run_case(name)[0], (error, 0, 1))

    def test_each_transport_failure_stops_without_retry(self):
        for index in range(1, 4):
            with self.subTest(index=index):
                fields, log = self.run_case("api"+str(index))
                self.assertEqual(fields, (-16, index, 1))
                self.assertNotIn("query=1", log)

    def test_unexpected_status_and_response_are_not_success(self):
        for name in ("positive_status", "extra1", "extra2", "wide_result", "xbl_overflow"):
            with self.subTest(name=name):
                fields, log = self.run_case(name)
                self.assertEqual(fields, (-71, 1, 1))
                self.assertNotIn("query=1", log)
        self.assertEqual(self.run_case("tz_overflow")[0], (-71, 2, 1))
        self.assertEqual(self.run_case("abl_overflow")[0], (-71, 3, 1))

    def test_shared_fuse_domain_disagreement_is_not_accepted(self):
        fields, log = self.run_case("alias_mismatch")
        self.assertEqual(fields, (-116, 3, 1))
        self.assertNotIn("query=1", log)


if __name__ == "__main__":
    unittest.main()
