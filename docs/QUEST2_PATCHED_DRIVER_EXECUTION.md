# Patched-driver execution investigation

**No safe hardware-bound execution route is established for the current locked
headset. The patched built-in drivers have not run on it.** This work adds a
repeatable read-only boot-chain identity check and narrows the remaining secure
firmware investigation. Passing this check never authorizes execution.

Target: Quest 2 Hollywood `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. The [custody increment](QUEST2_OBJECT_CUSTODY.md)
already passed host tests, native disabled/enabled builds and retained vendor
module ABI checks. Those results do not establish a boot path.

## Active-slot partition comparison

On 2026-10-24, the rooted USB headset reported slot `_a`, green verified boot,
locked vbmeta and completed Android startup. A single bounded capture found:

| Partition prefix | Bytes | SHA-256 |
| --- | ---: | --- |
| ABL | 274,432 | `75dc2174995fd39588bcbbd8b3455afc7bad4999a8914e0c40b9cc1509bcef5c` |
| TZ | 3,207,168 | `9384241f1169e4953992fc06723507e8ac217006a5dfb92e47826068500dc8d3` |

Both match the exact OTA images inspected offline. Resolved block-device
metadata was stable around each read. Boot ID, online CPUs, module names and
taint agreed before and after collection. No partition was written, module
loaded, secure call issued or reboot requested.
The metadata samples bracket separate reads; they do not attest continuous
symlink identity between those observations.

This identifies the bytes at the observed active-slot partition prefixes. It
does not measure the secure image currently executing, compare entire padded
partitions, inspect fuse/rollback floors or prove boot-ROM acceptance.

The [collector](../tools/quest2_boot_chain_probe.py) accepts only fixed ABL/TZ
reads for this firmware. It checks exact model/build/kernel, root, slot and boot
state first; rejects resident project modules; bounds commands and total host
time; verifies explicit remote status, exact lengths and pinned hashes; and
rechecks identity and observed health afterward. A failed capture leaves
`INCOMPLETE.json`. Existing output directories are refused.

```text
python -B tools/quest2_boot_chain_probe.py --adb <adb-path> --serial <USB-serial> --output <new-private-directory>
```

Completion prints `BOOT_CHAIN_PREFIXES_MATCH; EXECUTION_BLOCKED` and exits **3**.
Failure exits **2**. Raw firmware prefixes and health data stay in the private
output directory; do not commit or redistribute those files. The report keeps
`execution_ready`, `driver_execution_validated`, `recovery_validated` and
`running_secure_image_measured` false.

## Route assessment

| Route | Current evidence and missing prerequisite |
| --- | --- |
| Cold-start patched kernel through RAM boot | The [matching ABL audit](QUEST2_RESOURCE_LIFETIMES.md#exact-device-execution-investigation) found an explicit locked-state rejection before image authentication. An accepted nonpersistent boot path and independent recovery are still needed. |
| Replace camera/FastRPC with modules | Both drivers are built in. Their existing files, maps, callbacks and hardware state have no validated transfer transaction. A second namespaced driver instance would not inherit those resources. |
| Kexec into patched Horizon | Requires a proven shutdown/DMA/recovery boundary in the current stock kernel. Drivers that initialize in the next kernel cannot supply that missing old-kernel boundary. |
| Downgrade/unlock | Current upstream was rechecked at `7f34276ecef9021771e7bf4a2d53f10b26243334`. Its [Quest 2 profile](https://github.com/darknight1050/quest1-bootloader-unlocker-web/blob/7f34276ecef9021771e7bf4a2d53f10b26243334/src/data/profiles.ts#L214-L225) does not establish compatibility with this firmware's enforced floors. The procedure writes boot-chain partitions and wipes data; it is not a reversible execution test. |
| Current ABL signed-token unlock | A real signed, per-unit token path exists, but no valid token is available. Success erases user partitions and persists unlock state. It is not a RAM-only route. |

The unlocker's root-workflow build ceiling is not itself a measurement of this
unit's fuse state. Existing root does not resolve the image-family rollback
mapping or make its downgrade safe. No downgrade or unlock was attempted.

The current ABL's ordinary `oem unlock` handler at PE offset `0x11c30` prints the
unlock URL and returns status 3. Its displayed "Device unlock allowed" value
comes from a cached byte initialized to one, so that display is not FRP or
permission evidence. The special `unlock_token` flash target instead reaches a
signature verifier at `0x128d4`, checks an embedded RSA-2048 key, and binds the
script to the unit's unlock serial before accepting an unlock record. Successful
unlock reaches user-partition erasure before persistent device-state writes.
These paths were checked with 66 exact instruction assertions plus command,
string and public-key anchors in the matching extracted LinuxLoader. No valid
signer-issued token is available, and no token or unlock command was submitted.

## Exact unlock-state storage boundary

Host-only decoding of the exact current OTA XBL validated 123 firmware files and
88 PE images, including `VerifiedBootDxe` (61,440 bytes, SHA-256
`6c15a6b6a92ba4dd5680b7b764bf6e34b7c88707dfa11826ae1f686a92839aec`).
Its registration code binds the VerifiedBoot GUID to table `0xd160`; slot `+8`
is state operation `0x157c`, and slot `+0x20` is milestone operation `0x1a90`.
The state operation selects either Keymaster/RPMB or `devinfo` partition helpers
using secure firmware state. The active backend on this unit remains unmeasured;
finding a `devinfo` partition would not establish that it controls unlock state.

The matching OTA Keymaster image (253,952 bytes, SHA-256
`d47a59f743fa622f2f7a3733574c7b335d18661a6dbb3ea811b7abce1661533f`)
checks software fuse 1 before either state read `0x202` or write `0x203`.
Milestone `0x204` sets that software latch. The provider sends this milestone
and checks both transport and response status. ABL calls it before returning
success to its normal Linux-launch path; milestone failure does not take that
kernel-launch block. The three-image check passed 75 instruction assertions,
dynamic-symbol/PLT resolution, image signatures and indexed hash-table checks.
This establishes the expected boundary for the inspected normal boot path,
not a runtime measurement of this unit's latch or executing secure images.
It does not justify an exploratory Keymaster state request from Horizon.
The command dispatcher can also initialize persistent RPMB partitions before a
read handler on its cold path. Attaching through QSEECom changes application
reference state even when it avoids restarting the existing Keymaster app.

The standard secure-state INFO service also performs fuse-controller and clock
operations in the inspected TZ implementation. Its complete effect contract has
not been established here; no new probe or hardware-mutation-free claim follows
from its public API name. No storage, Keymaster or secure-state command was sent.

## Matching secure-firmware audit

Offline inspection now follows a genuine SMC exception path in the matching TZ
bytes: `0x1c019134` to `0x1c019200` to `0x1c01d5d4`. The dispatcher tests saved
`SCR_EL3.NS` at `0x1c01d630` and routes ordinary nonsecure requests through further
context handling. This locates a monitor path, not a permission check for the
image-metadata service.

A secure-origin initialization producer at `0x1c04b7e8`-`0x1c04b7f0` supplies
entry `0x1c119e98` through saved-register offset `0x58`. That entry reaches
`0x1c04bbd4`, then `0x1c067488`, then generated descriptor lookup `0x1c07bf0c`.
The generated lookup explains why the earlier literal/table searches were
inconclusive. The previously rejected 42-record controller table remains
excluded as service-registration evidence.

For normalized SIP/PIL IDs, the guard at `0x1c07c138`-`0x1c07c148` requires
`ID - 0x02000201 <= 14`. Metadata-verification ID `0x02000210` produces 15 and
returns unsupported before descriptor creation. Thus this candidate is
unregistered on the recovered normal-world SCM path in the matching TZ bytes.
That is a control-flow result, not an inference from missing literals or a
claim about every earlier boot-stage authentication interface.

Rollback ID `0x0200020f` has a real descriptor for handler `0x1c0540c8`, parameter
ID 1 and flags 1. Registration alone did not justify a device query. The subsequent
fixed-contract investigation is described below; earlier-stage signing and boot
acceptance remain separate questions. No experimental secure query has been performed.

The current running kernel configuration was separately read with matching
identity and boot observations. Function tracing is disabled and dynamic
function tracing/livepatch are absent; kprobes are enabled. The kprobe redirect
and nested-entry semantics do not themselves provide whole-driver consistency
or keep redirected module text alive after probe teardown. No redirect was
installed and no canary is presented as complete patched-driver execution.

## Fixed rollback-query prerequisite

The subsequent handler audit recovered a fixed scalar query contract for
`0x0200020f`, parameter ID 1. Inputs 0, 7 and 28 bypass specialized PIL paths
and read configured version-count records. On success, raw register 0 is zero,
register 1 carries the count and registers 2/3 are zero; the public
`scm_call2_noretry` API returns zero with that count in `desc.ret[0]`.
The other two descriptor returns must be zero. Outputs after failure are invalid.

The exact chip/config selector is `0x6008`. Its 22-record configuration maps
XBL input 0 and ABL input 28 to the same two 32-bit fuse-count fields (range
0-64), and TZ input 7 to a 17-bit field (range 0-17). The referenced exact OTA
devcfg has the matching selector and no override of these properties. A selector
or property lookup failure does not establish a zero count.

The fixed service path reads those fields and performs ordinary stack, response
and initialization-cache RAM writes. No fuse programming, flash, reset or
resource reassignment was found on that path. Its worker routing matches the
existing INFO availability/version query pattern. The public transport still
has **no hard deadline**: `noretry` disables busy retries, not interrupted-call
resumption or waiting for its mutex.

The optional [module](../module/scm_version_probe.c) defaults to no secure calls.
It checks model/kernel compatibility before its opt-in gate. The explicit query
path first checks service availability through the public helper, which internally
uses the retrying `scm_call2`. Only the subsequent three fixed counter requests
use `scm_call2_noretry`, with zeroed descriptors. It rejects unexpected status/output,
out-of-range counts or disagreement between the aliased XBL/ABL fields. There
are no caller buffers, arbitrary service/address parameters or asynchronous work.

The native build option is `scm_version_probe`; default builds omit the module.
Its [separate ELF audit](../tools/ci/quest2_scm_probe_audit.py) requires complete
version records, a fixed import/parameter allowlist and no constructors. Existing
strict stock-module gates are unchanged. Missing captured stock CRC evidence is
deferred only for the two named public SCM imports and remains explicit in the
report. Generated CRC agreement does not become running-kernel acceptance.

Native [run 40076155855](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/40076155855)
passed at source `ec8b9e450f3cc9f6e57f26678a61372ed14cb634`. Its 24-file artifact,
checksums, source/compiler/config pins, all five module audits and the compiled
default branch were independently checked. The probe contains all 14 required
version records; only the two SCM import CRCs initially lacked captured stock proof.

The exact probe then passed one normal, no-force, no-query load/unload on the
matching headset. The kernel accepted its complete import set; the expected
`dry_run=1 scm_calls=0` marker appeared, with no query marker. Boot ID, online
CPUs, module list and taint (`5124`) matched afterward. Its temporary file was
removed. This runs module initialization and changes transient module state.
User confirmation of Home/tracking after this test is pending. No counter query
has been issued; this loader check does not validate queries or patched drivers.

Even a successful query would describe configured fuse counts, not authenticate
an older image, establish every earlier boot-stage minimum or prove recovery.
Zero counts would not authorize downgrade/unlock/flash. The persistent objective
remains safe execution of the actual patched drivers on the headset.

## Signed metadata comparison

An offline comparison reconstructed five images from the exact current OTA and
compared them with the unlocker's pinned `quest2a12/16476800118700000.zip` archive
(SHA-256 `af25f24a975eab802065da0f1bc753d209af10d66164db28ebacdbedfa05e6a2`).
The signed OEM metadata reports:

| Image | Old archive anti-rollback version | Current OTA anti-rollback version |
| --- | ---: | ---: |
| XBL | 0 | 0 |
| XBL config | 0 | 0 |
| TZ | 0 | 0 |
| devcfg | 0 | 0 |
| ABL | 0 | 1 |

All 12 embedded authority hash-segment signatures (including TZ's QTI signatures)
verified using RSA-PSS/SHA-256, along with their 36 certificate signatures.
Corresponding old/current OEM intermediate and root certificates match exactly.
The signing-header layout was checked against the pinned
[vendor MBN schema](https://github.com/Rivko/android-firmware-qti-sdm670/blob/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271/aop_proc/tools/build/scons/sectools/sectools/common/parsegen/mbn/header.py).
All 102 indexed SHA-384 program-header hash slots across the ten images were
also checked against their corresponding segments or required zero slots. This
verifies the signed table, covered segment bytes and embedded chain consistency;
it does not establish hardware trust-anchor acceptance or boot-stage policy.

In particular, the current OTA's signed XBL version is also zero. A configured
TZ counter for the aliased XBL/ABL records cannot simply be promoted to proof
that PBL rejects version-zero XBL. Earlier-stage enforcement and image acceptance
still need their own evidence. No older firmware was installed or executed.

## Verification

`python -B -m unittest discover -s tools/tests -p 'test_*.py'` passed **656 tests,
no skips**, including 12 collector, nine fixed-query module and eight separate
ELF-audit tests. The collector tests include real shell byte-count/status framing,
binary data containing a status-like marker, regular-file refusal, truncated
reads, failed/missing status, hash mismatches, changed metadata/identity/health,
resident project modules, invalid inputs, host deadlines and existing outputs.
Five existing diagnostic admission guards now reject a resident SCM probe before
partition, ownership, pstore, subsystem or trace operations; all five regressions
failed before the fix and passed afterward. The affected suites passed 88 tests.
The native run above tested the earlier 654-test source; its module, CI and patch
inputs are unchanged by these later guard/test/documentation fixes.
The initial missing-collector tests failed before implementation.
The collector increment's independent review found no actionable issues and
reran 45 affected collector/preflight/shutdown tests successfully. That review
does not cover the subsequent query-module addition.

The single on-headset capture above completed with matching prefixes and stable
observations. Host-only inspection independently verified OTA manifest/operation/
partition hashes, 101 exact instruction anchors and six pointer/table assertions.
The fixed-query contract additionally passed 77 instruction assertions, all 22
property records, referenced partition hashes and exact kernel-source hashes.
Python/YAML/Bash syntax, documentation links and Git whitespace checks passed.
The earlier native custody result remains pinned to that implementation; a new
native query-module build and independent review are pending. No firmware
instructions were executed for the offline audit.

The new query-module host tests cover its actual production functions using
controlled public-API backends: default zero calls, fixed/zeroed requests,
profile/availability failures, each transport failure, unexpected outputs,
configured bounds and shared-field disagreement. Compilation fails without the
real SCM configuration. Separate synthetic ELF tests exercise strict coverage,
stock/generated CRC distinctions, unexpected imports, parameters and constructors.
These host tests do not execute a secure query or establish hardware safety.
