#!/usr/bin/env bash
# Sourced after stock gates, before any ownership source/config changes.
# Build only: neither default module insertion nor a secure call occurs here.
set -euo pipefail
: "${repo:?}" "${kernel:?}" "${out:?}" "${artifacts:?}"
config_check
make -C "$out" -j4 "${flags[@]}" M="$repo/module" QKX_EXPERIMENTAL=0 \
  QKX_SCM_VERSION_PROBE=1 modules 2>&1 | tee "$artifacts/scm-version-probe-build.log"
config_check
python3 -B tools/ci/quest2_scm_probe_audit.py \
  --module module/scm_version_probe.ko --symvers "$out/Module.symvers" \
  --config "$out/.config" --compile-command module/.scm_version_probe.o.cmd \
  --output "$artifacts/scm-version-probe-abi.json"
cp module/scm_version_probe.ko "$artifacts/"
cp module/.scm_version_probe.o.cmd "$artifacts/scm-version-probe.compile.txt"
llvm-objdump -dr module/scm_version_probe.o > "$artifacts/scm-version-probe.disassembly.txt"
git diff --exit-code
echo 'Fixed-query module built; default-load acceptance and secure queries remain untested.'
