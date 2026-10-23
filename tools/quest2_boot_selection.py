#!/usr/bin/env python3
"""Host-only boot-policy reference. No device access or execution permission."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


def initial():
    return {"schema": 1, "default_os": "linux", "one_shot": None,
            "pending_attempt": None, "recovery_latched": False}


def token(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{32}", value) is not None


def validate_state(state):
    if (not isinstance(state, dict) or state.keys() != initial().keys() or
            type(state["schema"]) is not int or state["schema"] != 1 or
            state["default_os"] not in ("linux", "horizon") or
            state["one_shot"] not in (None, "linux", "horizon") or
            type(state["recovery_latched"]) is not bool or
            (state["pending_attempt"] is not None and
             (not token(state["pending_attempt"]) or state["recovery_latched"]))):
        raise ValueError("invalid boot-selection state")
    return dict(state)


def idle(state):
    state = validate_state(state)
    if state["pending_attempt"] is not None:
        raise ValueError("unresolved Linux attempt; select fallback before further requests")
    return state


def request(state, target):
    state = idle(state)
    if target not in ("linux", "horizon"):
        raise ValueError("unknown requested OS")
    state["one_shot"] = target
    return state


def set_default(state, target):
    state = idle(state)
    if target not in ("linux", "horizon"):
        raise ValueError("unknown default OS")
    state["default_os"] = target
    return state


def report(reason, selected="horizon", attempt=None):
    return {"status": "SIMULATION_ONLY", "selected_os": selected,
            "reason": reason, "attempt": attempt,
            "execution_allowed": False, "auto_boot_allowed": False}


def select(state, payload_state, attempt):
    """Model one startup, with caller-simulated payload status and attempt ID."""
    state = validate_state(state)
    if payload_state not in ("valid", "missing", "corrupt", "unverified") or not token(attempt):
        raise ValueError("known simulated payload state and a fresh attempt token required")
    chosen = state["one_shot"] or state["default_os"]
    state["one_shot"] = None
    if state["pending_attempt"] is not None:
        state.update(pending_attempt=None, recovery_latched=True)
        return state, report("interrupted_linux_attempt")
    if state["recovery_latched"]:
        return state, report("recovery_required")
    if chosen == "horizon":
        return state, report("horizon_selected")
    if payload_state != "valid":
        state["recovery_latched"] = True
        return state, report("payload_" + payload_state)
    state["pending_attempt"] = attempt
    return state, report("linux_attempt_prepared", "linux", attempt)


def complete(state, attempt, outcome):
    """Simulated acknowledgment; cannot attest to real Linux boot or recovery."""
    state = validate_state(state)
    if not token(attempt) or state["pending_attempt"] != attempt or outcome not in ("ready", "failed"):
        raise ValueError("matching pending attempt and known simulated outcome required")
    state.update(pending_attempt=None, recovery_latched=outcome == "failed")
    return state


def recover(state):
    """Clear the reference latch explicitly; this is not physical recovery."""
    state = idle(state)
    if not state["recovery_latched"]:
        raise ValueError("no recovery latch to clear")
    state.update(recovery_latched=False, one_shot=None)
    return state


def encoded(state):
    raw = json.dumps(validate_state(state), sort_keys=True, separators=(",", ":")).encode()
    return (json.dumps({"state": state, "sha256": hashlib.sha256(raw).hexdigest()},
                       sort_keys=True, separators=(",", ":")) + "\n").encode()


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate state-record key")
        result[key] = value
    return result


def sync_directory(path):
    # Windows tests cover policy/atomic replacement, not power-loss durability.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


class Store:
    """Cooperating writers in a trusted, private HOST directory only."""
    def __init__(self, path):
        self.path = Path(path)
        self.record = self.path / "selection.json"
        self.lock = self.path / "selection.lock"
        self.temporary = self.path / "selection.tmp"

    def create(self):
        self.path.mkdir(mode=0o700)
        self.acquire()
        self.write(initial())
        self.release()
        sync_directory(self.path.parent)

    def acquire(self):
        if self.path.is_symlink() or not self.path.is_dir():
            raise ValueError("private host store directory required")
        descriptor = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        # Persist the interruption marker before replacing any record.
        sync_directory(self.path)

    def release(self):
        self.lock.unlink()
        sync_directory(self.path)

    def load(self, *, locked=False):
        if (self.path.is_symlink() or not self.path.is_dir() or
                (not locked and os.path.lexists(self.lock)) or os.path.lexists(self.temporary)):
            raise ValueError("unavailable or interrupted host selection store")
        info = self.record.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError("bounded regular selection record required")
        with self.record.open("rb") as stream: raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("selection record exceeds 4096 bytes")
        try:
            envelope = json.loads(raw, object_pairs_hook=unique_pairs)
        except RecursionError as exc:
            raise ValueError("selection record nesting limit exceeded") from exc
        if not isinstance(envelope, dict) or envelope.keys() != {"state", "sha256"}:
            raise ValueError("invalid selection record envelope")
        state = validate_state(envelope["state"])
        canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if envelope["sha256"] != hashlib.sha256(canonical).hexdigest():
            raise ValueError("selection record checksum mismatch")
        return state

    def write(self, state):
        raw = encoded(state)
        descriptor = os.open(self.temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(self.temporary, self.record)
        sync_directory(self.path)

    def transact(self, operation):
        self.acquire()
        try:
            updated, result = operation(self.load(locked=True))
            validate_state(updated)
        except ValueError:
            # Invalid input has not started a write; release only our own lock.
            self.release()
            raise
        # I/O failures retain the interruption marker and require investigation.
        self.write(updated)
        self.release()
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True, help="new/private host simulation directory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "show", "recover"): commands.add_parser(name)
    for name in ("request", "default"):
        commands.add_parser(name).add_argument("target", choices=("linux", "horizon"))
    choose = commands.add_parser("select")
    choose.add_argument("--payload-state", choices=("valid", "missing", "corrupt", "unverified"), required=True,
                        help="SIMULATED input; does not inspect a payload")
    done = commands.add_parser("complete")
    done.add_argument("--attempt", required=True)
    done.add_argument("--outcome", choices=("ready", "failed"), required=True)
    args = parser.parse_args(argv)
    store = Store(args.store)
    try:
        if args.command == "init":
            store.create()
            result = {**report("host_store_initialized"), "state": store.load()}
        elif args.command == "show":
            result = {**report("host_state_only"), "state": store.load()}
        elif args.command == "select":
            result = store.transact(lambda state: select(state, args.payload_state, uuid.uuid4().hex))
        else:
            def change(state):
                if args.command == "request": state = request(state, args.target)
                elif args.command == "default": state = set_default(state, args.target)
                elif args.command == "complete": state = complete(state, args.attempt, args.outcome)
                else: state = recover(state)
                return state, {**report("host_state_updated"), "state": state}
            result = store.transact(change)
    except (OSError, ValueError) as exc:
        result = {**report("state_unavailable"), "error": str(exc)}
        print(json.dumps(result, sort_keys=True))
        return 3 if args.command in ("show", "select") else 2
    print(json.dumps(result, sort_keys=True))
    return 3  # Expected: simulation recorded, physical handoff remains blocked.


if __name__ == "__main__": raise SystemExit(main())
