// SPDX-License-Identifier: GPL-2.0-only
/* Fixed configured fuse-count queries; never authenticates or boots an image. */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/string.h>
#include <linux/utsname.h>
#include <soc/qcom/scm.h>

#ifndef CONFIG_QCOM_SCM
#error The real public SCM implementation is required; stubs cannot validate queries.
#endif

static bool execute;
module_param(execute, bool, 0);
MODULE_PARM_DESC(execute, "Explicit fixed read-only version queries; default makes no secure calls");

static int __init scm_version_probe_init(void)
{
	static const u32 inputs[] = { 0, 7, 28 };
	static const u32 maximum[] = { 64, 17, 64 };
	u32 versions[ARRAY_SIZE(inputs)];
	const char *model;
	unsigned int i;
	int ret;

	if (strcmp(init_uts_ns.name.release,
		"4.19.325-cip128-st12-g49638c7a8637") || !of_root ||
	    of_property_read_string(of_root, "model", &model) ||
	    strncmp(model, "Hollywood", 9) || !of_machine_is_compatible("qcom,kona"))
		return -EINVAL;
	if (!execute) {
		pr_info("QKX_SCM_VERSION_PROBE dry_run=1 scm_calls=0\n");
		return 0;
	}
	ret = scm_is_call_available(SCM_SVC_PIL, 0x0f);
	if (ret != 1)
		return ret < 0 ? ret : -EOPNOTSUPP;
	for (i = 0; i < ARRAY_SIZE(inputs); i++) {
		struct scm_desc desc = { 0 };

		desc.arginfo = SCM_ARGS(1);
		desc.args[0] = inputs[i];
		/* No busy retry; the public transport still has no hard deadline. */
		ret = scm_call2_noretry(SCM_SIP_FNID(SCM_SVC_PIL, 0x0f), &desc);
		if (ret) {
			pr_err("QKX_SCM_VERSION_PROBE read_failed id=%u status=%d\n", inputs[i], ret);
			return ret < 0 ? ret : -EPROTO;
		}
		if (desc.ret[1] || desc.ret[2] || desc.ret[0] > maximum[i])
			return -EPROTO;
		versions[i] = (u32)desc.ret[0];
	}
	if (versions[0] != versions[2])
		return -ESTALE;
	pr_info("QKX_SCM_VERSION_PROBE xbl=%u tz=%u abl=%u query=1 queries=3 boot_acceptance=unproven\n",
		versions[0], versions[1], versions[2]);
	return 0;
}

static void __exit scm_version_probe_exit(void) { }
module_init(scm_version_probe_init);
module_exit(scm_version_probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Quest 2 fixed configured rollback-count query; no boot authorization");
