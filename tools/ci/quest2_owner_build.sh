#!/usr/bin/env bash
# Sourced only after the unchanged matching build passed all stock ABI gates.
# Offline compilation only; neither Image is a flashable/verified boot package.
set -euo pipefail
: "${repo:?}" "${kernel:?}" "${out:?}" "${artifacts:?}"
config_check
stock_config_sha="$(sha256sum "$out/.config" | cut -d' ' -f1)"
make -C "$out" -j4 "${flags[@]}" M="$repo/module" QKX_EXPERIMENTAL=0 \
  QKX_OWNER_SELFTEST=1 modules 2>&1 | tee "$artifacts/owner-selftest-build.log"
config_check
python3 -B tools/ci/quest2_audit.py modules --symvers "$out/Module.symvers" \
  --config "$out/.config" --loader-command module/.loader.o.cmd \
  --output "$artifacts/owner-selftest-abi.json" module/owner_gate_selftest.ko
cp module/owner_gate_selftest.ko "$artifacts/"
cp module/.owner_gate_selftest.o.cmd "$artifacts/owner-selftest.compile.txt"
python3 -B tools/quest2_apply_owner_gate.py --source "$kernel" \
  --receipt "$artifacts/owner-source-patch.json"
"$kernel/scripts/config" --file "$out/.config" --disable QKX_OWNER_GATE
make -C "$kernel" O="$out" "${flags[@]}" olddefconfig
grep -Fx '# CONFIG_QKX_OWNER_GATE is not set' "$out/.config"
make -C "$kernel" O="$out" -j4 "${flags[@]}" Image \
  2>&1 | tee "$artifacts/owner-default-off-build.log"
off_config_sha="$(sha256sum "$out/.config" | cut -d' ' -f1)"
off_image_sha="$(sha256sum "$out/arch/arm64/boot/Image" | cut -d' ' -f1)"
"$kernel/scripts/config" --file "$out/.config" --enable QKX_OWNER_GATE
make -C "$kernel" O="$out" "${flags[@]}" olddefconfig
grep -Fx 'CONFIG_QKX_OWNER_GATE=y' "$out/.config"
make -C "$kernel" O="$out" -j4 "${flags[@]}" Image \
  2>&1 | tee "$artifacts/owner-enabled-build.log"
git -C "$kernel" add -N kernel/qkx_owner_gate.c include/linux/qkx_owner_gate.h
git -C "$kernel" diff --check
git -C "$kernel" diff --no-ext-diff --binary HEAD > "$artifacts/owner-driver.patch"
python3 - "$artifacts" "$out" "$stock_config_sha" "$off_config_sha" "$off_image_sha" <<'PY'
import hashlib, json, pathlib, subprocess, sys
artifacts, out = map(pathlib.Path, sys.argv[1:3])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
names = ('owner_gate_selftest.ko', 'owner-selftest-abi.json', 'owner-source-patch.json',
         'owner-driver.patch')
result = {
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'kernel_commit': '6bde5fc06b252c97761bc0963dd13c1abcf377ab',
    'stock_config_sha256': sys.argv[3], 'default_off_config_sha256': sys.argv[4],
    'default_off_image_sha256': sys.argv[5], 'enabled_config_sha256': sha(out/'.config'),
    'enabled_image_sha256': sha(out/'arch/arm64/boot/Image'),
    'enabled_image_bytes': (out/'arch/arm64/boot/Image').stat().st_size,
    'default_off_compilation_passed': True, 'enabled_compilation_passed': True,
    'selftest_scope': 'Isolated module state, real kernel locks, controlled test clock; no driver bindings',
    'driver_execution_validated': False, 'full_ownership_validated': False,
    'hardware_handoff_validated': False, 'deployment_performed': False,
    'installable': False, 'status': 'OFFLINE_ENTRY_ACCOUNTING_BUILD_HANDOFF_BLOCKED',
    'image_artifact_exported': False,
    'files': {n: {'bytes': (artifacts/n).stat().st_size, 'sha256': sha(artifacts/n)} for n in names}
}
(artifacts/'owner-build-provenance.json').write_text(json.dumps(result, indent=2)+'\n')
PY
git diff --exit-code
echo 'Driver entry-accounting builds complete. Patched Horizon execution remains unvalidated.'
