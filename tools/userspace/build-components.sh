#!/usr/bin/env bash
# Runs only inside the disposable ARM64 build stage.
set -euo pipefail
mkdir /build /evidence
cd /build
fetch() {
    local name=$1 url=$2 revision=$3
    git init -q "$name"
    git -C "$name" remote add origin "$url"
    git -C "$name" fetch --depth=1 origin "$revision"
    git -C "$name" checkout -q --detach FETCH_HEAD
    test "$(git -C "$name" rev-parse HEAD)" = "$revision"
    if [ "$name" = quest2 ]; then
        git -C "$name" archive --format=tar.gz HEAD LICENSE monado patches/monado > "/evidence/$name-source.tar.gz"
    else
        git -C "$name" archive --format=tar.gz HEAD > "/evidence/$name-source.tar.gz"
    fi
}
fetch monado https://gitlab.freedesktop.org/monado/monado.git 019d9d45869d6ae67c5195540793e5c43687c1dd
fetch quest2 https://github.com/machachu56/quest-kexec-q2.git 9c96c192d4a4a80f95ba0013294977e85c6eae11
fetch box64 https://github.com/ptitSeb/box64.git 2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a
fetch vulkan-headers https://github.com/KhronosGroup/Vulkan-Headers.git 9a0f3099c8a9607a7c0f3127d8abfdc19a93e8c5
cmake -S vulkan-headers -B headers-build -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --install headers-build
# Keep the full unmodified source, exact patches and source receipts together.
cp /controller.patch /evidence/controller.patch
for patch in quest2/patches/monado/*.patch; do
    git -C monado apply --check --whitespace=error "$PWD/$patch"
    git -C monado apply --whitespace=error "$PWD/$patch"
done
git -C quest2 apply --check --whitespace=error /controller.patch
git -C quest2 apply --whitespace=error /controller.patch
printf '%s  %s\n' d00046ac41d69829ce0b2ef4d3403bbd260e5eb5624261611aa3cd4b7bf878e8 quest2/monado/drivers/quest2/q2_driver.c | sha256sum --check
cp -r quest2/monado/drivers/quest2 monado/src/xrt/drivers/
cp quest2/monado/target_builder_quest2.c monado/src/xrt/targets/common/
cmake -S monado -B monado-build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib \
    -DVulkan_INCLUDE_DIR=/usr/local/include \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DXRT_FEATURE_SERVICE=ON \
    -DXRT_BUILD_DRIVER_QUEST2=ON -DXRT_HAVE_OPENCV=OFF -DXRT_HAVE_SDL2=OFF \
    -DXRT_HAVE_LIBUSB=OFF -DXRT_FEATURE_WINDOW_PEEK=OFF -DBUILD_TESTING=OFF \
    -DBUILD_DOC=OFF -DXRT_FEATURE_OPENVR=OFF -DXRT_FEATURE_STEAMVR_PLUGIN=OFF \
    -DXRT_FEATURE_SERVICE_SYSTEMD=OFF -DXRT_INSTALL_SYSTEMD_UNIT_FILES=OFF \
    -DXRT_OPENXR_INSTALL_ACTIVE_RUNTIME=OFF -DXRT_OPENXR_INSTALL_ABSOLUTE_RUNTIME_PATH=ON
cmake --build monado-build --parallel 2
cmake --install monado-build
grep -qx 'XRT_BUILD_DRIVER_QUEST2:BOOL=ON' monado-build/CMakeCache.txt
grep -qx 'XRT_FEATURE_SERVICE:BOOL=ON' monado-build/CMakeCache.txt
test -n "$(find monado-build -name q2_driver.c.o -print -quit)"
cmake -S box64 -B box64-build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DARM_DYNAREC=OFF -DBOX32=OFF
cmake --build box64-build --parallel 2
grep -qx 'ARM_DYNAREC:BOOL=OFF' box64-build/CMakeCache.txt
grep -qx 'BOX32:BOOL=OFF' box64-build/CMakeCache.txt
install -m 755 box64-build/box64 /usr/local/bin/box64
mkdir -p /usr/local/share/licenses/qkx
cp monado/LICENSE /usr/local/share/licenses/qkx/monado-BSL-1.0
cp box64/LICENSE /usr/local/share/licenses/qkx/box64-LICENSE
cp -r vulkan-headers/LICENSES /usr/local/share/licenses/qkx/vulkan-headers
cp vulkan-headers/LICENSE.md /usr/local/share/licenses/qkx/vulkan-headers/README.md
cp monado-build/CMakeCache.txt /evidence/monado-CMakeCache.txt
cp box64-build/CMakeCache.txt /evidence/box64-CMakeCache.txt
printf '%s\n' 'monado 019d9d45869d6ae67c5195540793e5c43687c1dd' \
    'quest2 9c96c192d4a4a80f95ba0013294977e85c6eae11' \
    'box64 2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a' \
    'vulkan-headers 9a0f3099c8a9607a7c0f3127d8abfdc19a93e8c5' > /evidence/revisions.txt
