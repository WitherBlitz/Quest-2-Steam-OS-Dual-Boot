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

The unlocker's root-workflow build ceiling is not itself a measurement of this
unit's fuse state. Existing root does not resolve the image-family rollback
mapping or make its downgrade safe. No downgrade or unlock was attempted.

## Matching secure-firmware audit

Offline inspection now follows a genuine SMC exception path in the matching TZ
bytes: `0x1c019134` to `0x1c019200` to `0x1c01d5d4`. The dispatcher tests saved
`SCR_EL3.NS` at `0x1c01d630` and routes ordinary nonsecure requests through further
context handling. This locates a monitor path, not a permission check for the
image-metadata service.

A zero-initialized handoff slot at `0x1c028108` has a recovered writer that takes
saved-register offset `0x58`. Its downstream registration/dispatch contract is
still under investigation. Bounded literal/descriptor searches did not identify
the candidate service; generated constants and other layouts remain possible.
Negative searches do not prove service absence. The previously rejected
42-record controller table remains excluded as service-registration evidence.

The specific metadata-service handler, caller/boot-stage permissions, image-ID
mapping, enforced rollback domain and no-mutation contract are still unresolved.
A different-platform XBL caller or a generic monitor accepting SMC exceptions
cannot establish those facts. No experimental secure query is justified yet.

## Verification

`python -B -m unittest discover -s tools/tests -p 'test_*.py'` passed **637 tests,
no skips**. The 12 collector tests include real shell byte-count/status framing,
binary data containing a status-like marker, regular-file refusal, truncated
reads, failed/missing status, hash mismatches, changed metadata/identity/health,
resident project modules, invalid inputs, host deadlines and existing outputs.
The initial missing-collector tests failed before implementation.

The single on-headset capture above completed with matching prefixes and stable
observations. Host-only inspection independently verified OTA manifest/operation/
partition hashes and 29 exact monitor instruction anchors. Python syntax,
documentation links and Git whitespace checks passed. No new kernel code was
changed or native kernel build claimed; the earlier native result remains pinned
to the custody implementation. No firmware instructions were executed for the
offline audit.
