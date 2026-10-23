"""Execute only inside the disposable ARM64 development container, without devices."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time


def frame_count(trace):
    pending = set()
    completed = 0
    for line in trace.splitlines():
        requested = re.search(r"wl_surface@\d+\.frame\(new id wl_callback@(\d+)\)", line)
        if requested:
            pending.add(requested.group(1))
        done = re.search(r"wl_callback@(\d+)\.done\(", line)
        if done and done.group(1) in pending:
            pending.remove(done.group(1))
            completed += 1
    return completed


def elf(path):
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise RuntimeError(f"not ARM64 ELF64 little-endian: {path}")
    result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, timeout=20)
    if result.returncode or "not found" in result.stdout + result.stderr:
        raise RuntimeError(f"unresolved runtime dependencies: {path}: {result.stdout}{result.stderr}")
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "dependencies": result.stdout.splitlines()}


def main():
    proof = {"status": "FAILED", "scope": "ARM64 QEMU on CI host kernel; no Quest hardware or VR session",
             "host_kernel": os.uname().release}
    try:
        if os.getuid() == 0:
            raise RuntimeError("smoke test must run as the unprivileged gamer")
        manifest_path = Path("/usr/local/share/openxr/1/openxr_monado.json")
        manifest = json.loads(manifest_path.read_text())
        runtime = Path(manifest["runtime"]["library_path"])
        if not runtime.is_absolute():
            raise RuntimeError("expected an explicit installed OpenXR library path")
        proof["elf"] = [elf(Path("/usr/local/bin/monado-service")), elf(runtime),
                        elf(Path("/usr/local/bin/box64")), elf(Path("/usr/games/chocolate-doom"))]
        iwad = Path("/usr/share/games/doom/freedoom1.wad")
        if not iwad.is_file():
            raise RuntimeError("installed Freedoom IWAD is missing")
        proof["iwad"] = {"bytes": iwad.stat().st_size, "sha256": hashlib.sha256(iwad.read_bytes()).hexdigest()}
        guest = Path(sys.argv[1])
        data = guest.read_bytes()
        if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 62:
            raise RuntimeError("x86-64 guest fixture architecture differs")
        translated = subprocess.run(["box64", str(guest), "literal ; $(unused) '"],
            env={**os.environ, "BOX64_LOG": "0", "BOX64_NOBANNER": "1"},
            capture_output=True, text=True, timeout=30)
        if translated.returncode != 23 or translated.stdout != "QKX_X86_64_LIBC_OK\n":
            raise RuntimeError("Box64 libc/argv fixture failed: " + translated.stdout + translated.stderr)
        proof["box64_libc"] = {"status": 23, "stdout": translated.stdout,
                               "guest_sha256": hashlib.sha256(data).hexdigest(), "dynarec": False}
        with tempfile.TemporaryDirectory(prefix="qkx-gaming-smoke-") as temp:
            directory = Path(temp)
            output, errors = directory / "game.stdout", directory / "game.stderr"
            env = {**os.environ, "TMPDIR": temp, "WAYLAND_DEBUG": "client", "SDL_AUDIODRIVER": "dummy"}
            started = time.monotonic()
            with output.open("wb") as out, errors.open("wb") as err:
                session = subprocess.Popen(["qkx-session", "--", "qkx-freedoom",
                    "-nosound", "-nomusic", "-warp", "1", "1", "-skill", "1", "-nomonsters"],
                    env=env, stdout=out, stderr=err)
                try:
                    deadline = started + 40
                    while time.monotonic() < deadline:
                        if session.poll() is not None:
                            raise RuntimeError("native game/session exited before the bounded scene check")
                        trace = errors.read_text(errors="replace")
                        if frame_count(trace) >= 3:
                            break
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("native game did not deliver three Wayland frame callbacks")
                finally:
                    session.send_signal(signal.SIGTERM)
                    try:
                        session.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        session.kill()
                        session.wait(timeout=5)
                        raise RuntimeError("session did not perform bounded termination")
                    finally:
                        proof["wayland_trace"] = errors.read_text(errors="replace")
                        proof["game_stdout"] = output.read_text(errors="replace")
            trace = errors.read_text(errors="replace")
            if session.returncode != 143 or any(directory.glob("qkx-session.*")):
                raise RuntimeError("session status or runtime-directory cleanup failed")
            frames = frame_count(trace)
            commits = len(re.findall(r"wl_surface@\d+\.commit\(", trace))
            if frames < 3 or commits < 3:
                raise RuntimeError("game frame delivery evidence is insufficient")
            proof["native_game"] = {"application": "Chocolate Doom + Freedoom E1M1",
                "wayland_frames": frames, "surface_commits": commits, "session_exit": session.returncode,
                "runtime_directory_removed": True, "audio": "disabled; dummy SDL driver",
                "rendering": "SDL software / Weston headless pixman", "scene_check_seconds": time.monotonic() - started}
        proof["status"] = "PASSED"
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        proof["failure"] = str(exc)
    print(json.dumps(proof, indent=2))
    return 0 if proof["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
