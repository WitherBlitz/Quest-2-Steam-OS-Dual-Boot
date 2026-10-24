# Watchdog, diagnostics and Horizon recovery

Scope: Hollywood, Horizon `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. This phase adds source verification and a
bounded read-only retained-log collector. **A watchdog/reset path that survives
hardware shutdown, retained-log survival and dependable Horizon recovery have
not been demonstrated.** No reset adapter is bound; the Hollywood gate is closed.

## Ownership and completion findings

The exact public kernel source is commit
`6bde5fc06b252c97761bc0963dd13c1abcf377ab`. The expanded
`tools/quest2_shutdown_source.py` verifier checks the existing exact archive,
17 complete source-file hashes and seven copied watchdog/camera/DSP functions.
The seven added pins cover SCM, ARM64 restart, pstore inode/RAM/platform code,
kernel stat and device-number encoding.
Source agreement does not establish secure-firmware behavior or physical reset.

| Mechanism | Source-backed behavior | Recovery limit |
| --- | --- | --- |
| Application watchdog | The driver owns its petting thread/timer, IRQ and panic notifier. Disable takes a mutex, requests secure disable with one SCM argument equal to one, then removes the IRQ, panic notifier and pet timer. It does not stop/join the petting thread. SCM failure becomes `-EIO`; an already-disabled software flag skips SCM. | Worker quiescence is unproven and no supported re-enable setter exists. A software flag or successful disable cannot establish secure ownership or a surviving deadline. |
| Secure calls | `scm_call2()` takes a mutex and retries busy responses. The low-level ARM64 SMC loop retries `SCM_INTERRUPTED` without a finite count; the atomic wrapper shares it. | A caller timeout or atomic/no-busy-retry wrapper cannot interrupt that loop or bound secure-firmware execution. No watchdog is disabled here. |
| Restart | Platform reset prepares PMIC mode/reason, may request a watchdog bite, attempts secure PS_HOLD and falls through to MMIO. Several intermediate results are discarded. ARM64 `machine_restart()` eventually loops forever on failure. | A reset request, callback return or source delay is not proof of completed reset or successful Horizon boot. No SCM/MMIO reset is attempted here. |
| Panic | The watchdog panic callback can change bark/bite and pet an enabled AP watchdog, but cannot enable an already-disabled watchdog. | It cannot replace the watchdog after teardown. Existing source-model tests cover this distinction; no panic is triggered. |
| Existing pstore file | Opening/reading an already-enumerated regular file copies its buffer. Unlink invokes erase; backend enumeration can zap malformed DMESG records. | Read only an existing mount. Never mount, remount, rebind, rescan or unlink records during collection. Availability is not retention proof. |

The legacy loader timing control advertises ten seconds but writes AP bark/bite
values corresponding to 19/20 seconds at the pinned source's `WDT_HZ=32765`.
No rate override was found in the captured command line. This is a source
mismatch in a blocked control path, not a measured physical deadline. The path
is unchanged and remains unavailable on Hollywood.

Captured ramoops spans `0x9ba00000–0x9ba90000`; its pmsg region is
`0x9ba70000–0x9ba90000`. The legacy marker's entire
`0x9ba80000–0x9ba90000` range overlaps pmsg. `marker_read` remains blocked;
no replacement retained-RAM address is guessed, reserved or written.

### Power-button reset configuration boundary, 2026-10-24

A separate source inspection verified the existing archive and these complete
files at the matching kernel commit. The existing 17-file source-verifier scope
is unchanged:

| File | SHA-256 |
| --- | --- |
| `drivers/input/misc/qpnp-power-on.c` | `09e121dc5da4d84ec7cba60dc78d948a120f4dee4c08dcf26b214174c1357ae1` |
| `arch/arm64/boot/dts/vendor/bindings/input/qpnp-power-on.txt` | `457bb21abbecd7d305f04f265e2f041713dca101f5c71efe39a7634353e2423d` |
| `arch/arm64/boot/dts/vendor/qcom/pm8150.dtsi` | `cfd2d5e3dfa29eb6019425efa714eb72a0735ac7e7b3ae90ec3e80e2cf776916` |

The matching source distinguishes missing reset properties from disabled reset:

- For the power and RESIN inputs, an absent `qcom,support-reset` leaves
  `config_reset` unset. The [configuration loop](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/input/misc/qpnp-power-on.c#L1714)
  then skips S2 reset configuration. An explicit zero instead requests disable;
  a nonzero value requests timer/type configuration. These are different cases.
- The [S3 configuration function](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/input/misc/qpnp-power-on.c#L2046)
  writes debounce/source only when the respective property is present. The
  driver documents the source register as write-once and possibly already set
  by the bootloader. Missing DT properties do not disclose the hardware values.
- The [cached reason getters](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/input/misc/qpnp-power-on.c#L492)
  return strings from state saved at probe, without a fresh PMIC transaction.
  These labels are not a complete cause-register dump or a measured reset
  deadline. Probe selects a reason using `ffs()`; unknown or simultaneous reason
  bits cannot be reconstructed from those strings alone.

The historical 2026-10-22 runtime-tree capture has power/volume-down key codes
116/114 at the primary PM8150 PON node, but no S2 reset parameters or S3
source/debounce. That does not prove the hardware reset is disabled or enabled.
A fresh fixed-node/cached-reason capture stopped before remote execution because
ADB reported no USB device. Its incomplete receipt and verified source files
remain private. No current PON state, physical reset, timing or survival across
shutdown is claimed; no PMIC register, watchdog or boot setting was changed.

## Bounded retained-log collection

```sh
python3 -B tools/quest2_recovery_probe.py \
  --adb /path/to/adb --serial YOUR_USB_SERIAL \
  --kernel-source /private/build/kernel-source.tar.gz \
  --output /private/new-recovery-before
```

The collector requires the exact source audit and firmware/root identity. It
checks boot ID, CPUs, module names and taint before/after and confirms an existing
`pstore` filesystem at `/sys/fs/pstore` through mountinfo. A directory alone is
insufficient. Missing mount/files are recorded as unavailable, never mounted or
treated as retention success.

Source/captured geometry supports `dmesg-ramoops-0/1`, optionally with `.enc.z`,
and `console-ramoops-0`/`pmsg-ramoops-0`. Only recognized regular, non-symlink
files are read; child mounts are refused and each file's stat device must match
the verified pstore mount. Unsupported names remain coverage gaps. Compressed records stay
opaque bytes. Reads have device-side byte bounds, host size checks, a finite
file count, 1 MiB per-file and 4 MiB aggregate limits, 15-second command timeouts
and a 300-second collection budget. Metadata and exact returned lengths are
checked. These observations do not lock out concurrent kernel record changes.
A host timeout does not prove the remote read terminated.
Pstore command output is bounded on the device. Inherited identity/health reads
use post-capture size checks; host capture is not a streaming memory bound.

Raw artifacts stay private with byte counts and SHA-256 hashes. Remote
completion framing is checked separately from ADB success. Unknown/failed
completion preserves an incomplete receipt without retrying. Existing output
directories are refused. No device file, service, mount, watchdog or boot setting
is changed; no module is loaded and `/dev/pmsg0` is never opened. Never commit raw logs or device
identifiers to the public repo.

After a separately performed external recovery rehearsal, collect into another
new directory and compare:

```sh
python3 -B tools/quest2_recovery_probe.py \
  --compare-before /private/new-recovery-before \
  --compare-after /private/new-recovery-after \
  --output /private/new-recovery-comparison.json
```

New captures and comparisons use schema version 2. Each capture records the
SHA-256 of the explicitly selected ADB serial; comparison refuses missing,
malformed or different selector hashes. Version 1 captures need fresh collection
before comparison; do not add a binding to old receipts by hand. The hash is a
private, stable identifier, not anonymization or authenticated hardware identity.
It prevents accidentally mixing selected targets with identical firmware, but
does not prove that a selector still refers to the same physical headset.

Comparison checks complete receipts and artifact paths, sizes and hashes. It can
report changed boot IDs, new/missing files and changed/unchanged bytes. Record
IDs are reused, and console/pmsg timestamps are unset in this source. Neither a
filename, changed boot nor matching hash attributes a record to an experiment or
proves reset causality, timing, healthy Horizon or tracking. Every report keeps
`recovery_validated=false` and `handoff_ready=false`. Complete collection or
comparison exits 3 (`HANDOFF_BLOCKED`); failure exits 2.

### Optional human observations

Comparison accepts `--rehearsal /private/button-observation.json` without sending
device commands. The JSON must contain exactly the following fields:

```json
{
  "schema_version": 1,
  "before_capture_sha256": "SHA256_OF_BEFORE_CAPTURE_JSON",
  "after_capture_sha256": "SHA256_OF_AFTER_CAPTURE_JSON",
  "sequence": null,
  "timing_method": "not_measured",
  "elapsed_seconds": null,
  "home_usable": true,
  "head_tracking_normal": true,
  "left_controller_tracking_normal": true,
  "right_controller_tracking_normal": true
}
```

Replace the hash placeholders with SHA-256 of the exact complete `capture.json`
files. A known sequence is 1-16 nonempty printable steps, at most 256 characters
each. For external stopwatch timing, use `external_stopwatch` and positive finite
seconds. Explicit null sequence/unmeasured time remain unknown. Missing/duplicate
fields, oversized/malformed input, wrong bindings or an unchanged boot are refused.

Output labels observations `human_report` and preserves reported health failures.
Even a complete healthy report leaves reset causality, independent deadlines,
watchdog ownership and machine-verified Horizon health unproven. It records
`shutdown_boundary_tested=false`, `recovery_validated=false`, `handoff_ready=false`.
Do not invent an after-capture when root is unavailable; preserve partial evidence
separately until a real complete capture is possible.

## External recovery and staged acceptance

Meta's published Quest 2 support procedure uses the physical power and volume
down buttons, held for about 30 seconds or until the boot screen appears, then
volume to choose **Boot Device** and power to select/confirm it. This documents
a procedure, not a measured recovery deadline on this headset.
[MetaStoreHelp's Quest 2 instructions](https://communityforums.atmeta.com/other-troubleshooting-14/quest-2-headset-power-button-173139?postid=1175057).

1. At ordinary usable Horizon Home, take the baseline capture. Have a person
   observe the display and independently time the physical-button sequence.
   Boot existing Horizon without a factory reset, update, flash or policy change.
2. Confirm usable Home, head/controller tracking and the expected build; take
   the second capture. Record external timing and observer confirmation
   separately. Boot-ID change and successful collection alone are insufficient.
3. Establish AP/secure watchdog ownership and an independently observed recovery
   mechanism that remains available through each shutdown boundary. A normal
   button test does not prove survival after AOP, PMIC, USB, storage, CPU or MMU
   changes.
4. After checked owner adapters and that recovery protocol exist, stage bounded
   partial shutdown and irreversible failure. Preserve diagnostics and verify
   Horizon return each time. Keep the existing watchdog and closed loader gate
   until a demonstrated replacement satisfies those requirements.

The [native coordinator tests](QUEST2_CHECKED_SHUTDOWN.md) exercise timeouts,
partial failure, rollback failure and irreversible-before-issue behavior with
host doubles. They report when recovery is required; they provide no physical
reset backend. No dependable automatic fallback to Horizon is claimed.

## Verification

### Passive headset baseline, 2026-10-23

One bounded collection completed normally on the matching rooted headset:
one existing retained record, 15 artifacts totaling 42,088 bytes and no
unsupported-name gaps. Existing-mount and stat-device checks passed. All artifact
lengths/hashes, 16 source pins and four private collector-source snapshots were
verified. Identity/root, boot ID, CPUs, module names and taint agreed before/after.
No project module was resident; no target file, mount, service, watchdog or boot
change occurred, and no device job remained afterward. Raw evidence stays private.

This demonstrates existing mounted-file collection only. The record is not
attributed to this experiment. No new physical reset, watchdog expiry,
retention-across-reset, tracking-health or Horizon-fallback test is claimed.

### Ordinary button-return observations, 2026-10-23 continuation

Fresh owner and retained-log baseline collections completed before the requested
physical-button rehearsal. No supported retained pstore records were available.
The user subsequently reported usable Home and normal head/controller tracking.
ADB observed a changed boot ID with the same expected firmware and kernel.
Elapsed time was **not measured**, and the exact completed button sequence/menu
label was not confirmed; neither is supplied by inference.

Existing root was unavailable afterward. A private unprivileged checkpoint retains
the identity/changed-boot observation. No privileged after-capture/comparison,
log survival, watchdog expiry, independently timed deadline or recovery after
hardware shutdown was established. At the user's request, root startup was left
alone. This increment issued no additional reboot, driver stop, watchdog change
or Linux handoff.

### Host verification

The extended observation comparison passed 29 focused recovery tests and all 495
host regression tests on Windows without skips. These use transport fixtures and
real file/JSON/hash validation, not physical recovery. The actual pinned archive
also passed all 17 source hashes and the existing exact function-fixture checks.
The [Ubuntu regression run](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40049811374)
at `9aec5c99ad1b37eb47a2103384ee6f9a7b707fe5` passed all 495 tests without skips
and seven existing reader QEMU cases. Downloaded source/commit, license and binary
hashes matched their receipts. Independent review found no functional issues;
its stale source-count wording was corrected here. Native module sources,
target build and loader gates are unchanged in this increment.

The schema-2 selector-binding update passed all 40 focused recovery/source tests
and all 457 host regression tests on Windows, with no skips. These are host
tests; this update issued no headset commands and demonstrates no physical reset.
The [Ubuntu run](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40001696994)
at source `92b52ec91a174b57502a44ec1ebb000ea648e338` also passed all 457 tests
without skips and the seven existing Linux/QEMU reader cases. Downloaded source
and binary hashes matched their receipts; this does not establish headset recovery.

```sh
HOST_TEST_CC=clang python3 -B -m unittest tools.tests.test_recovery_probe tools.tests.test_shutdown_source
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py'
git diff --check
```

The source audit reports `HANDOFF_BLOCKED` even after all 17 hashes and exact
function fixtures match. Record actual headset results separately from mocked
transport tests and public-source facts.

All 377 host tests passed on Windows and in the
[Ubuntu regression run](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39929565569)
at source `9c1c55cd741d15ed469735078d612fba2a1ac531`, with no skipped tests.
The downloaded Linux evidence also passed the existing seven sparse-reader QEMU
cases; its source and binary hashes matched. The
[matching native build](QUEST2_CHECKED_SHUTDOWN.md#verified-matching-native-build)
separately verified the unchanged native shutdown objects. Host transport tests,
QEMU and native compilation do not establish a reset or Horizon fallback.

## Pinned primary sources

- [Watchdog petting](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/watchdog_v2.c#L640), [disable](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/watchdog_v2.c#L276), [forced bite](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/watchdog_v2.c#L743).
- [SCM retry loop](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/scm.c#L97) and [locking/busy retry](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/soc/qcom/scm.c#L351).
- [Platform restart](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/power/reset/msm-poweroff.c#L582) and [ARM64 restart](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/kernel/process.c#L161).
- [Pstore read/erase](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/pstore/inode.c#L137), [names](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/pstore/inode.c#L338), [ramoops enumeration](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/pstore/ram.c#L249) and [backend enumeration](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/fs/pstore/platform.c#L710).
