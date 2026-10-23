# Quest 2 dual-boot selection

**Host reference only. Actual Linux boot, automatic startup, OS switching and
recovery are not validated. Nothing in this increment changes the headset.**

## Approved experience and current boundary

The intended persistent default is Linux. The supported stock boot chain still
starts Horizon and obtains root before a checked Linux handoff. Its duration is
unmeasured; this is not direct boot from an unlocked bootloader.

The Linux toolbar's **Switch to Horizon OS** action will persist one Horizon
override before rebooting through the stock boot chain. Horizon's startup
selector will consume that override and remain in Horizon for that boot.
A sideloaded Horizon app's single **Switch to Linux** button will request Linux
through the same controller. Neither button changes the persistent default.

The desired end state has one kernel/OS executing after handoff and both systems
on storage. No nested VM or Android container is part of this design. Termination
of stock CPUs/producers/DMA and independent recovery remain prerequisites; the
reference cannot establish absence of residual activity or OS overhead.

There is currently no validated shared writable store, startup integration,
privileged switching broker, toolbar button or sideloadable switching APK.
Those components are not implemented by this increment. The existing upstream
`qkx-switch` changes root into an Android system image; it is not this selector.

## Implemented host policy

`tools/quest2_boot_selection.py` is a Python 3.11+ reference for selection and
persistence in a **new private host directory**. Its JSON reports always retain
`execution_allowed=false` and `auto_boot_allowed=false`, even after a simulated
successful boot or recovery. It has no loader, ADB, reboot or startup backend.
Every successful simulation exits **3**, meaning physical handoff stays blocked.
Invalid mutation commands exit 2; unavailable state on show/select reports
Horizon and exits 3 without resetting the record.

- Persistent `default_os` starts as Linux. A one-shot request overrides it once.
- A modeled Linux selection first records a pending attempt token. A matching
  simulated completion clears it; a failed completion latches Horizon.
- An unresolved attempt encountered by another selection latches Horizon and
  consumes any override. It never automatically retries Linux. Ordinary requests
  and default changes cannot erase a pending attempt or bypass the latch.
- Missing, corrupt or unverified **simulated payload status** latches Horizon.
  The `--payload-state` argument does not inspect or validate actual files.
- Explicit `recover` clears the model's latch. It attests to no physical recovery
  and enables neither execution nor automatic boot.
- Attempt IDs correlate model events; callers must generate fresh IDs. They are
  not authentication, device identity, signed evidence or proof of kernel entry.

The store uses one bounded 4 KiB JSON/checksum envelope, exclusive writer lock,
exclusive temporary file, file fsync and atomic replacement. POSIX additionally
syncs directory entries, including the interruption marker before replacement.
Windows tests cover policy and replacement; they establish no power-loss
durability. Even POSIX tests do not simulate storage-controller power loss.
The SHA-256 checksum detects accidental damage, not a hostile writer.

Duplicate keys, malformed records, invalid fields, oversized/nonregular records,
checksum mismatch, abandoned locks and temporary files are refused. I/O failures
leave the interruption marker. There is no automatic stale-lock cleanup, record
reset, retry or repair command. Investigate an interrupted host store; preserve it
and initialize a different directory for another simulation. The cooperating
writer protocol assumes a trusted parent directory and is not a sandbox against
another process with the same filesystem privileges.

Example **host simulation**, with no connected headset needed:

```sh
python3 -B tools/quest2_boot_selection.py --store /private/new-host-model init
python3 -B tools/quest2_boot_selection.py --store /private/new-host-model request horizon
python3 -B tools/quest2_boot_selection.py --store /private/new-host-model select --payload-state valid
python3 -B tools/quest2_boot_selection.py --store /private/new-host-model select --payload-state valid
```

The first selection models Horizon; the second models Linux with a pending
token. Another selection without a matching `complete --attempt TOKEN --outcome
ready` models interruption and latches Horizon. These are separate process
invocations exercising persisted intent, not actual boot cycles.

## Remaining integration and physical acceptance

1. Close the [checked shutdown](QUEST2_CHECKED_SHUTDOWN.md),
   [watchdog/recovery](QUEST2_WATCHDOG_RECOVERY.md) and
   [manual first-boot](QUEST2_FIRST_LINUX_BOOT.md) gates.
2. Establish a supported shared state location with availability, encryption,
   permissions, filesystem durability and identity checked from both real OSes.
   Host directory behavior supplies no authority to mount or write userdata.
3. Bind the privileged controller to freshly validated payload hashes, firmware,
   loader gates and measured runtime readiness. A simulated `valid` or `ready`
   input must never become an execution authorization. Serialize all producers.
4. Implement the two buttons and checked broker/startup adapters. Persist a
   request before reporting it accepted; keep failures visible and preserve
   usable Horizon. A failed write must never trigger a reboot or handoff.
5. Establish recovery deadlines and retained diagnostics through actual shutdown
   and switch boundaries. Check missing/corrupt payloads, interrupted requests,
   unavailable shared state, lost completion and failed recovery independently.
6. Observe repeated manual Linux/Horizon cycles and cold boots on the exact
   headset, including normal Home/head/controller tracking after recovery.
   Record actual Horizon startup time and evidence that only the new OS remains
   executing. Only then consider enabling automatic startup.

**Physical evidence for this increment: none.** Host subprocess cycles and CI
results cannot close these requirements or certify usable dual boot.

## Verification

Run `python3 -B -m unittest tools.tests.test_boot_selection`, then the full host
suite documented in README. Tests exercise real temporary records and CLI
processes, plus injected replace/directory-sync failures. Python compilation
and `git diff --check` supplement behavioral tests; no Python linter is configured.
