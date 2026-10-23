#!/usr/bin/env bash
# Offline target profiles. No device inputs, private config or deployment.
set -euo pipefail
profile="${QKX_TARGET_PROFILE:-baseline}"
case "$profile" in
  baseline) localversion=-qkx-q2-baseline; init_script=initramfs/quest2-baseline-init.sh ;;
  startup-gic-pending) localversion=-qkx-q2-startup; init_script=initramfs/quest2-baseline-init.sh ;;
  startup-usb-child) localversion=-qkx-q2-usb; init_script=initramfs/quest2-linux-init.sh ;;
  *) echo 'Unknown offline target profile' >&2; exit 2 ;;
esac
repo="$(pwd)"
work="${RUNNER_TEMP:?}/quest2-target"
artifacts="$repo/ci-target-artifacts"
kernel="$work/kernel-src"
out="$work/target-out"
compiler="$work/clang-r450784e"
mkdir "$work" "$artifacts"
mkdir "$out" "$compiler"
git init -q "$kernel"
git -C "$kernel" remote add origin https://github.com/facebookincubator/oculus-linux-kernel.git
git -C "$kernel" fetch --depth=1 origin 6bde5fc06b252c97761bc0963dd13c1abcf377ab
git -C "$kernel" checkout --detach FETCH_HEAD
test "$(git -C "$kernel" rev-parse HEAD^{tree})" = 62f9948a604fdb0c363382a9886b06f809d9ce53
python3 -B tools/ci/quest2_startup.py --kernel "$kernel" --profile "$profile" \
  --action apply --output "$work/target-profile.json"
curl --fail --location --retry 3 \
  https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86/+archive/425c8149f17c8d9914bf690b4d5abe45a13fb993/clang-r450784e.tar.gz \
  | tar -xz -C "$compiler"
printf '%s  %s\n' \
  41131b03d674645836c23635a7e2ae478bd869b3b3da98204365c06610ec5438 "$compiler/bin/clang" \
  858eaed0308ad1da1d21ff1df1616130cfe4b96f2502fd7099086f9cd48dbb41 "$compiler/bin/ld.lld" \
  | sha256sum --check
curl --fail --location --retry 3 -o "$out/.config" \
  https://raw.githubusercontent.com/machachu56/quest-kexec-q2/9c96c192d4a4a80f95ba0013294977e85c6eae11/kernel/patches/quest2/quest2_target_defconfig
printf '%s  %s\n' e814b47c1106d6516620ead5d9b7c8d73a0d8f44fcdef28d1f6daf9252a432a5 "$out/.config" | sha256sum --check
export PATH="$compiler/bin:$PATH"
unset KCFLAGS KCPPFLAGS CFLAGS CPPFLAGS CXXFLAGS MAKEFLAGS
"$kernel/scripts/config" --file "$out/.config" \
  --disable LOCALVERSION_AUTO --disable ION_POOL_AUTO_REFILL \
  --disable IKCONFIG --disable IKCONFIG_PROC --disable INITRAMFS_FORCE \
  --set-str INITRAMFS_SOURCE '' --set-str CMDLINE '' --set-str EXTRA_FIRMWARE '' \
  --enable DEVTMPFS --enable BINFMT_ELF --enable BINFMT_SCRIPT \
  --enable BLK_DEV_INITRD --enable RD_GZIP --enable USB_CONFIGFS_ECM
if [ "$profile" = startup-usb-child ]; then
  "$kernel/scripts/config" --file "$out/.config" \
    --enable USB_CONFIGFS_RNDIS --enable NET --enable INET --enable PACKET \
    --enable UNIX --enable UNIX98_PTYS --enable TMPFS --enable CONFIGFS_FS
fi
flags=(ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang REAL_CC=clang LD=ld.lld
  HOSTCC=clang HOSTCXX=clang++ CROSS_COMPILE=aarch64-linux-gnu-
  CLANG_TRIPLE=aarch64-linux-gnu- "LOCALVERSION=$localversion"
  "CFLAGS_pinctrl-lpi.o=-I$kernel/drivers/pinctrl"
  "CFLAGS_pinctrl-lpi-legacy.o=-I$kernel/drivers/pinctrl"
  "CFLAGS_pinctrl-wcd.o=-I$kernel/drivers/pinctrl"
  KBUILD_BUILD_USER=questbuilder KBUILD_BUILD_HOST=quest2-ci
  KBUILD_BUILD_TIMESTAMP=2026-10-21T00:00:00Z)
make -C "$kernel" O="$out" "${flags[@]}" olddefconfig
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v 2>&1 | tee "$artifacts/host-tests.log"
targets=(Image)
if [ "$profile" = startup-usb-child ]; then targets+=(modules); fi
make -C "$kernel" O="$out" -j4 "${flags[@]}" "${targets[@]}" 2>&1 | tee "$artifacts/target-build.log"
test "$(cat "$out/include/config/kernel.release")" = "4.19.325-cip128-st12$localversion"
if [ "$profile" = startup-usb-child ]; then
  stat -c 'Unstripped WLAN module: %s bytes' "$out/drivers/staging/qcacld-3.0/wlan.ko" | tee -a "$artifacts/target-build.log"
  make -C "$kernel" O="$out" "${flags[@]}" INSTALL_MOD_PATH="$work/modules" \
    INSTALL_MOD_STRIP=1 "STRIP=$compiler/bin/llvm-strip" \
    "DEPMOD=$(command -v depmod)" modules_install 2>&1 | tee -a "$artifacts/target-build.log"
  stat -c 'Installed WLAN module after debug stripping: %s bytes' \
    "$work/modules/lib/modules/4.19.325-cip128-st12$localversion/kernel/drivers/staging/qcacld-3.0/wlan.ko" | tee -a "$artifacts/target-build.log"
  python3 -B tools/ci/quest2_target_modules.py --kernel-out "$out" --staging "$work/modules" \
    --archive "$artifacts/target-modules.tar.gz" --output "$artifacts/target-modules.json"
  cp "$out/Module.symvers" "$artifacts/target.Module.symvers"
fi
env CC=aarch64-linux-gnu-gcc CROSS_COMPILE=aarch64-linux-gnu- \
  bash initramfs/busybox.sh "$work/busybox" 2>&1 | tee "$artifacts/busybox-build.log"
qemu-aarch64 "$work/busybox" sh -n "$init_script"
qemu-aarch64 "$work/busybox" echo 'static ARM64 BusyBox executed under qemu-user' | tee "$artifacts/busybox-execution.log"
if [ "$profile" = startup-usb-child ]; then
  qemu-aarch64 "$work/busybox" --list > "$work/busybox-applets"
  for applet in sh mount mkdir cat sleep ifconfig udhcpd telnetd dmesg reboot ln; do
    grep -qx "$applet" "$work/busybox-applets"
  done
  printf '#!/bin/sh\nexec qemu-aarch64 "%s" sh "$@"\n' "$work/busybox" > "$work/busybox-sh"
  chmod +x "$work/busybox-sh"
  HOST_TEST_SH="$work/busybox-sh" python3 -B -m unittest discover \
    -s tools/tests -p test_native_init.py -v 2>&1 | tee -a "$artifacts/busybox-execution.log"
fi
python3 -B initramfs/build.py --busybox "$work/busybox" \
  --init "$init_script" --output "$artifacts/initramfs.gz"
cp "$out/arch/arm64/boot/Image" "$artifacts/Image"
cp "$out/.config" "$artifacts/target.config"
cp LICENSE "$artifacts/LICENSE"
# Corresponding source = pinned base archive plus exact patches/build recipes
# in port-source.tar.gz, with applied source hashes recorded in provenance.
git -C "$kernel" archive --format=tar.gz --prefix=kernel/ HEAD > "$artifacts/kernel-source.tar.gz"
git archive --format=tar.gz --prefix=quest2-port/ HEAD > "$artifacts/port-source.tar.gz"
cp "$work/busybox-build/busybox-1.36.1.tar.bz2" "$artifacts/busybox-source.tar.bz2"
cp "$work/busybox-build/busybox-1.36.1/.config" "$artifacts/busybox.config"
python3 - "$artifacts" "$kernel" "$compiler" "$work/target-profile.json" "$init_script" <<'PY'
import hashlib, json, pathlib, struct, subprocess, sys
sys.path.insert(0, 'tools')
from quest2_payload_check import check_initramfs
artifacts, kernel, compiler, profile_path, init_source = map(pathlib.Path, sys.argv[1:])
target_profile = json.loads(profile_path.read_text())
cfg = (artifacts/'target.config').read_text()
for name in ('ARM64', 'ARM64_4K_PAGES', 'BLK_DEV_INITRD', 'DEVTMPFS', 'RD_GZIP', 'BINFMT_ELF', 'BINFMT_SCRIPT', 'USB_CONFIGFS_ECM'):
    assert f'CONFIG_{name}=y\n' in cfg, name
for name in ('ION_POOL_AUTO_REFILL', 'IKCONFIG', 'INITRAMFS_FORCE'):
    assert f'CONFIG_{name}=y\n' not in cfg, name
for name in ('CMDLINE', 'INITRAMFS_SOURCE', 'EXTRA_FIRMWARE'):
    assert f'CONFIG_{name}=""\n' in cfg, name
rescue = target_profile['target_profile'] == 'startup-usb-child'
if rescue:
    for name in ('NET','INET','PACKET','UNIX','UNIX98_PTYS','TMPFS','CONFIGFS_FS','USB_CONFIGFS_RNDIS'):
        assert f'CONFIG_{name}=y\n' in cfg, name
image = (artifacts/'Image').read_bytes()
rd = (artifacts/'initramfs.gz').read_bytes()
assert len(image) >= 64 and image[56:60] == b'ARM\x64'
offset, memory, flags = struct.unpack_from('<QQQ', image, 8)
assert len(image) <= memory <= 0x4000000 and offset == 0x80000 and flags == 0xa
profile = check_initramfs(rd)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
module_files = ('target-modules.tar.gz','target-modules.json','target.Module.symvers') if rescue else ()
if rescue:
    modules = json.loads((artifacts/'target-modules.json').read_text())
    assert modules['status'] == 'OFFLINE_MODULES_VERIFIED_HANDOFF_BLOCKED'
    assert modules['module_count'] >= 14
    assert modules['debug_sections_absent'] is True
    assert all(modules[key] is False for key in ('hardware_validated','handoff_ready',
               'deployment_performed','autoload_performed','device_access'))
    for output, source in (('Image','arch/arm64/boot/Image'),('target.config','.config'),
                           ('target.Module.symvers','Module.symvers')):
        assert modules['inputs'][source] == {'bytes': (artifacts/output).stat().st_size,
                                             'sha256': sha(artifacts/output)}
    assert modules['archive_sha256'] == sha(artifacts/'target-modules.tar.gz')
data = {
 'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'kernel_commit': subprocess.check_output(['git','-C',str(kernel),'rev-parse','HEAD'],text=True).strip(),
 **target_profile, 'deployment_performed': False,
 'config_source': 'machachu56/quest-kexec-q2@9c96c192d4a4a80f95ba0013294977e85c6eae11',
 'config_source_sha256': 'e814b47c1106d6516620ead5d9b7c8d73a0d8f44fcdef28d1f6daf9252a432a5',
 'compiler_commit': '425c8149f17c8d9914bf690b4d5abe45a13fb993',
 'clang_sha256': sha(compiler/'bin/clang'), 'lld_sha256': sha(compiler/'bin/ld.lld'),
 'busybox_source_sha256': 'b8cc24c9574d809e7279c3be349795c5d5ceb6fdf19ca709f80cde50e47de314',
 'files': {name: {'bytes': (artifacts/name).stat().st_size, 'sha256': sha(artifacts/name)}
           for name in ('Image','initramfs.gz','target.config','kernel-source.tar.gz',
                        'port-source.tar.gz','busybox-source.tar.bz2','busybox.config') + module_files},
 'image': {'text_offset': offset,'memory_bytes': memory,'flags': flags}, 'initramfs_profile': profile,
 'init_source': {'path': str(init_source), 'sha256': sha(init_source)},
 'native_usb_rescue': rescue,
 'target_modules_bundled': rescue, 'target_modules_loaded': False,
 'module_install_strip_debug': rescue,
 'llvm_strip_sha256': sha(compiler/'bin/llvm-strip') if rescue else None,
 'source_archive_scope': 'Unmodified kernel base plus pinned patches and recipes in port-source.tar.gz',
 'scope': ('Native USB rescue ' if rescue else 'Noninteractive ')+target_profile['target_profile']+' development build. Hardware handoff and physical boot unvalidated.'}
(artifacts/'target-build-provenance.json').write_text(json.dumps(data,indent=2)+'\n')
PY
python3 -B tools/ci/quest2_startup.py --kernel "$kernel" --profile "$profile" --action verify
git diff --exit-code
extra_sums=()
if [ "$profile" = startup-usb-child ]; then extra_sums+=(target.Module.symvers); fi
(cd "$artifacts" && sha256sum Image initramfs.gz target.config ./*.json ./*.log LICENSE ./*.tar.* busybox.config "${extra_sums[@]}" > SHA256SUMS)
echo "Offline Linux $profile built. No DTB, private config, device access or handoff."
