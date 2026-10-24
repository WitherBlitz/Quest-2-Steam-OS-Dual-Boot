#!/usr/bin/env bash
# Offline build only. Never contacts or changes a headset.
set -euo pipefail
repo="$(pwd)"
work="${RUNNER_TEMP:?}/quest2-build"
artifacts="$repo/ci-artifacts"
mkdir "$work" "$artifacts"
kernel="$work/kernel-src"
out="$work/android-out"
compiler="$work/clang-r450784e"
mkdir "$out" "$compiler"

git init -q "$kernel"
git -C "$kernel" remote add origin https://github.com/facebookincubator/oculus-linux-kernel.git
git -C "$kernel" fetch --depth=1 origin 6bde5fc06b252c97761bc0963dd13c1abcf377ab
git -C "$kernel" checkout --detach FETCH_HEAD
test "$(git -C "$kernel" rev-parse HEAD^{tree})" = 62f9948a604fdb0c363382a9886b06f809d9ce53
curl --fail --location --retry 3 \
  https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86/+archive/425c8149f17c8d9914bf690b4d5abe45a13fb993/clang-r450784e.tar.gz \
  | tar -xz -C "$compiler"
printf '%s  %s\n' \
  41131b03d674645836c23635a7e2ae478bd869b3b3da98204365c06610ec5438 "$compiler/bin/clang" \
  858eaed0308ad1da1d21ff1df1616130cfe4b96f2502fd7099086f9cd48dbb41 "$compiler/bin/ld.lld" \
  dfee93dc7eafbec5e1cf9c2ce9ad6ba48c610196602a8184355293d77af64b8b "$compiler/manifest_8508608.xml" \
  | sha256sum --check
export PATH="$compiler/bin:$PATH"
unset KCFLAGS KCPPFLAGS CFLAGS CPPFLAGS CXXFLAGS MAKEFLAGS
clang --version
ld.lld --version
python3 -B tools/ci/quest2_audit.py config "$out/.config"
unset Q2_CONFIG_GZ_B64
config_check() {
  printf '%s  %s\n' d91704d25163ec74a508273c3d7dea2f6ee69ebe810fd731b4068a984f386b8a "$out/.config" | sha256sum --check
}
flags=(ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang REAL_CC=clang LD=ld.lld
  HOSTCC=clang HOSTCXX=clang++ CROSS_COMPILE=aarch64-linux-gnu-
  CLANG_TRIPLE=aarch64-linux-gnu- LOCALVERSION=-g49638c7a8637
  "CFLAGS_pinctrl-lpi.o=-I$kernel/drivers/pinctrl"
  "CFLAGS_pinctrl-lpi-legacy.o=-I$kernel/drivers/pinctrl"
  "CFLAGS_pinctrl-wcd.o=-I$kernel/drivers/pinctrl"
  KBUILD_BUILD_USER=questbuilder KBUILD_BUILD_HOST=quest2-ci
  KBUILD_BUILD_TIMESTAMP=2026-10-20T00:00:00Z)
make -C "$kernel" O="$out" "${flags[@]}" olddefconfig
config_check
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v 2>&1 | tee "$artifacts/host-tests.log"
make -C "$kernel" O="$out" -j4 "${flags[@]}" Image modules 2>&1 | tee "$artifacts/kernel-build.log"
config_check
test "$(cat "$out/include/config/kernel.release")" = 4.19.325-cip128-st12-g49638c7a8637
cp "$out/Module.symvers" "$artifacts/Module.symvers"
aarch64-linux-gnu-readelf --file-header "$out/vmlinux"
python3 -B tools/quest2_diag_profile.py --build-vmlinux "$out/vmlinux" \
  --built-image "$out/arch/arm64/boot/Image" \
  --output "$artifacts/diagnostic-profile.json"
make -C "$out" -j4 "${flags[@]}" M="$repo/module" QKX_EXPERIMENTAL=0 modules 2>&1 | tee "$artifacts/loader-build.log"
config_check
python3 -B tools/ci/quest2_audit.py modules --symvers "$out/Module.symvers" \
  --config "$out/.config" --loader-command module/.loader.o.cmd \
  --output "$artifacts/module-audit.json" \
  module/quest_kexec.ko module/marker_read.ko module/usb_log.ko module/camera_dsp_diag.ko
cp module/quest_kexec.ko module/marker_read.ko module/usb_log.ko module/camera_dsp_diag.ko "$artifacts/"
cp module/.loader.o.cmd "$artifacts/loader.compile.txt"
cp module/.transition.o.cmd "$artifacts/transition.compile.txt"
llvm-objdump -dr module/transition.o > "$artifacts/transition.disassembly.txt"
cp module/.camera_dsp_diag.o.cmd "$artifacts/diagnostic.compile.txt"
cp module/.shutdown.o.cmd "$artifacts/shutdown.compile.txt"
cp module/.shutdown_io.o.cmd "$artifacts/shutdown-io.compile.txt"
cp LICENSE "$artifacts/"
python3 - "$kernel" "$compiler" "$out" "$artifacts" <<'PY'
import hashlib, json, pathlib, subprocess, sys
kernel, compiler, out, artifacts = map(pathlib.Path, sys.argv[1:])
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
data = {
    "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "kernel_commit": subprocess.check_output(["git", "-C", str(kernel), "rev-parse", "HEAD"], text=True).strip(),
    "compiler_commit": "425c8149f17c8d9914bf690b4d5abe45a13fb993",
    "clang_sha256": sha(compiler/"bin/clang"),
    "lld_sha256": sha(compiler/"bin/ld.lld"),
    "config_sha256": sha(out/".config"),
    "kernel_image_sha256": sha(out/"arch/arm64/boot/Image"),
    "module_symvers_sha256": sha(out/"Module.symvers"),
    "hardware_handoff": "unproven", "deployment_performed": False,
}
(artifacts/"build-provenance.json").write_text(json.dumps(data, indent=2)+"\n")
PY
git -C "$kernel" diff --exit-code
git diff --exit-code
if [[ "${QKX_SCM_VERSION_PROBE_BUILD:-0}" == 1 ]]; then
  source "$repo/tools/ci/quest2_scm_version_probe_build.sh"
elif [[ "${QKX_SCM_VERSION_PROBE_BUILD:-0}" != 0 ]]; then
  echo 'Invalid SCM version probe build selection' >&2
  exit 2
fi
if [[ "${QKX_OWNER_BUILD:-0}" == 1 ]]; then
  source "$repo/tools/ci/quest2_owner_build.sh"
elif [[ "${QKX_OWNER_BUILD:-0}" != 0 ]]; then
  echo 'Invalid owner build selection' >&2
  exit 2
fi
(cd "$artifacts" && sha256sum ./*.ko ./*.json ./*.log Module.symvers ./*.compile.txt ./*.disassembly.txt LICENSE > SHA256SUMS)
if [[ "${QKX_OWNER_BUILD:-0}" == 1 ]]; then
  (cd "$artifacts" && sha256sum owner-driver.patch owner-default-off.symvers owner-enabled.symvers >> SHA256SUMS)
fi
echo 'Offline matching-module build and ABI gates complete. No hardware handoff performed.'
