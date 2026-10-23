#!/usr/bin/env bash
# CI-owned ARM64 Docker userspace only. Never calls ADB or modifies a headset.
set -euo pipefail
artifacts="$PWD/ci-userspace-artifacts"
mkdir "$artifacts"
revision=$(git rev-parse HEAD)
image="qkx-q2-userspace:${revision}"
git archive --format=tar.gz HEAD > "$artifacts/integration-source.tar.gz"
x86_64-linux-gnu-gcc -std=c11 -O2 -Wall -Wextra -Werror tools/tests/gaming_x86_smoke.c -o "$artifacts/x86-libc-fixture"
docker build --platform linux/arm64 --progress plain -f tools/userspace/Containerfile \
    -t "$image" . 2>&1 | tee "$artifacts/build.log"
container=$(docker create "$image" /bin/true)
trap 'docker rm -f "$container" >/dev/null' EXIT
docker cp "$container:/evidence/." "$artifacts/"
docker image inspect "$image" --format '{{.Id}} {{.Architecture}} {{.Size}}' > "$artifacts/image.txt"
printf 'host_arch=%s\n' "$(uname -m)" >> "$artifacts/image.txt"
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --read-only --memory 2g --cpus 2 --tmpfs /tmp:rw,nosuid,nodev,size=256m \
    --tmpfs /home/gamer:rw,nosuid,nodev,size=64m \
    --mount "type=bind,src=$artifacts/x86-libc-fixture,dst=/fixture,readonly" \
    "$image" python3 /usr/local/share/qkx/smoke.py /fixture > "$artifacts/runtime-proof.json"
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v > "$artifacts/host-tests.log" 2>&1
printf '%s\n' "$revision" > "$artifacts/source-commit.txt"
cp LICENSE "$artifacts/LICENSE"
(cd "$artifacts" && sha256sum -- * > SHA256SUMS)
