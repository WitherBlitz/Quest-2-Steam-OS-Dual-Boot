# Quest 2 loader development status

This branch contains a read-only readiness collector and source fixes for
`hollywood`, Horizon build `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. It is an experimental development branch.
The patched Android kernel module has **been rebuilt and its symbol versions
verified**. Earlier 52 MiB RAM-only attempts were refused with `-EBUSY`.
A subsequent **36 MiB claim and release succeeded** at `0x100000000`; the
module was unloaded and its temporary file removed. Boot, CPUs and stock modules
were preserved; the user confirmed normal Home/tracking. A partial startup
kernel profile now builds and passes offline checks, but has not been booted.
This branch does not provide a complete SteamOS port, validated dual boot, or
full tracking.

## Branch and build provenance

The fixes start at loader commit
`e61edbd7fbdf6dc4976a979c61cc545e04738422`, the exact source used for the
existing offline build. The upstream
[`quest2-port`](https://github.com/machachu56/quest-kexec-q2/tree/quest2-port)
branch has additional kernel, camera, Monado and userspace work. It remains
separate; these firmware-specific fixes do not replace that branch or claim to
validate its hardware changes.

The matching kernel source is
[`facebookincubator/oculus-linux-kernel`](https://github.com/facebookincubator/oculus-linux-kernel/tree/6bde5fc06b252c97761bc0963dd13c1abcf377ab)
at `6bde5fc06b252c97761bc0963dd13c1abcf377ab`. The prior offline build used
Android Clang/LLD 14.0.7, Android build 8508608 (`clang-r450784e`), from prebuilt
commit `425c8149f17c8d9914bf690b4d5abe45a13fb993`. The verified patched rebuild
is recorded below; the earlier unpatched artifacts are separate.

## Source fixes

- Syncboss selection requires the bound `syncboss_spi` driver and
  `meta,syncboss-spi` device-tree compatible. It no longer assumes a SPI bus
  number: on the audited headset, `spi0.0` is C-Media audio and Syncboss is
  `spi1.0`. Both suspend and resume callbacks must exist before proceeding.
- Requested AOP shutdown hooks are resolved and retained before freeze, GPU/USB
  suspension or watchdog MMIO. The audited kernel lacks `send_irq`; requesting
  that disconnect returns `-ENOENT` before hardware shutdown. This rejects an
  unsupported operation; it does not implement a replacement AOP protocol.
- A requested RPMh flush fails early when its symbol/device is absent, instead
  of silently continuing without the requested operation.
- `marker_read` refuses Hollywood-prefixed models and missing/unreadable model
  properties before mapping its fixed log region. Reads, clear and write-test
  operations are all blocked. The range overlaps ramoops pmsg on the audited
  firmware; no replacement persistent-log address is assumed. Legacy behavior
  on other model strings is unchanged.
- Default module builds permit staging only. The option gate also refuses CPU,
  MMU, reset, core-hang and secure-watchdog tests. `QKX_EXPERIMENTAL=1` is a
  developer build opt-in, not evidence that execution is safe. The target-kernel
  patches and their logging assumptions have not been ported by this change.
- A real jump requires allocator ownership of the entire destination window
  (52 MiB in the default legacy layout; the compact source path derives its
  bounded envelope from payload sizes). Before staging, the loader checks mapped/nonreserved PFNs,
  one zone, isolation alignment and ordinary MOVABLE pageblocks, then calls the
  pinned kernel's `alloc_contig_range`. Missing helpers, CMA/other pageblock
  types or allocation failure abort without shutdown. A successful claim remains
  held until handoff or is freed on abort/unload. Rehearsals copy to separately
  owned scratch pages and do not claim the fixed window. The fixed range may
  fail allocation on a running headset; there is no fallback that overwrites
  busy memory. This establishes allocator ownership only, not DMA/secure safety.
- The freezer's wakeup/failure decisions are respected. The busy-loop thread
  that overwrote `pm_abort_suspend` has been removed. Reversible paths resume
  successful Syncboss suspension, then attempted GPU suspension, before thawing
  userspace. A failed Syncboss suspend is not resumed: its failure paths precede
  state changes and can indicate an earlier async resume is still running.
  A reported resume callback error stops with a panic. Syncboss queues async
  resume work and the GPU callback can hide internal errors, so callback success
  does not prove hardware recovery; that acceptance test remains outstanding.
- USB suspension is the explicit irreversible boundary. A later watchdog failure
  cannot return to apps with USB stopped; a requested RPMh flush failure cannot
  proceed to the physical trampoline. These paths panic; automatic recovery
  timing and real hardware behavior remain unvalidated.

## Host verification

The default no-jump build now also accepts an explicit `validate_ram=1`
diagnostic. It checks Kona/Hollywood, verifies System RAM coverage, makes one
native claim attempt for the selected destination (52 MiB by default), releases
successful ownership immediately, and returns before any payload open or shutdown path.
It rejects combinations with hardware, CPU, reset and other diagnostic modes.
Allocation can migrate/reclaim pages and has no hard real-time deadline. This
mode tests RAM acceptance only; it cannot establish DMA or secure handoff safety.
The new build and live test results are recorded below.

Run with Python 3.11+ and a native GCC/Clang compiler:

```sh
HOST_TEST_CC=clang python3 -B -m unittest discover -s tools/tests -p 'test_*.py' -v
git diff --check
```

The full suite has 204 passing host tests: 21 readiness-collector tests, 27
loader regression tests, 30 RAM-ownership tests, 28 handoff-planner tests,
15 native payload/layout tests, 16 offline payload-profile tests, 15 build-audit
tests, 14 private DTB producer tests, 8 startup patch/profile tests,
18 shutdown source/model tests and 12 passive shutdown-probe tests.
The RAM/shutdown regressions were observed failing before these changes.
The original loader failed 9
of the first 11 new regressions before the source fix. An independent source
review reproduced the 62-test suite before the final Syncboss suspend-abort
regression was added, and found no Critical or Important issue in that scope.
Subsequent pinned-source inspection found the duplicate-resume hazard on
Syncboss suspend failure; its regression was observed failing, then passing
with the correction, and the full 63-test suite passed.

The C harnesses compile the actual RAM claim/release and `qkx_execute` functions and the actual
initialization option/log guard prefixes, ending before their first external
action, against host API doubles. The tests check peripheral identity, changed
bus numbering, missing PM resume, missing requested AOP/RPMh dependencies,
Hollywood/missing-model log rejection, allocator failure/lifetime, rollback order,
irreversible failure paths and stage-only options. They do not
validate real kernel layouts, driver callbacks, DMA state or shutdown behavior.
Windows runs require Clang/LLD and configured Windows SDK/MSVC headers/libraries;
the host test compiler does not replace the pinned Android build compiler.

## Verified offline rebuild

### Opt-in allocator refusal observation

`ram_diagnose=1` is accepted only with `validate_ram=1`. It temporarily registers
return probes for the pinned kernel's `start_isolate_page_range`,
`has_unmovable_pages`, `test_pages_isolated` and `isolate_freepages_range`.
The callbacks filter the initiating task and selected PFN range, record return
values without changing registers, and log only after probes are removed.
Missing helpers or any registration failure abort before the claim and unwind
only successfully registered probes. Normal success and refusal also remove
all probes before initialization returns. The default diagnostic is unchanged.

An observed negative isolation/test return or zero free-page-collection return
can identify that attempt's failure stage. An unmovable result identifies a
block start, **not the exact busy PFN or owner**. Missing observations, compiler
inlining and nonzero kretprobe/kprobe missed counts prevent conclusions from
absence. Probe allocation and timing can perturb the attempt; its result cannot
automatically explain a previous uninstrumented refusal. No force, retry, alternate-address scan, global
tracing toggle, hardware shutdown or payload path is added.

The two new option/helper regressions and the caller/range filtering regression
were observed failing before their fixes. All 101 Windows host tests pass,
including partial-registration cleanup, success/refusal/guard cleanup,
unchanged target registers, first-block retention and both missed-event counters.
An independent review found an indirect ABI assurance gap. The build audit now
requires generated-versus-stock CRC agreement for both dynamically resolved
kretprobe exports, binding their additional structure/callback types. The CRCs
were decoded from the hash-pinned stock Image: `register_kretprobe=0x861fcdb7`
at raw offset `0x19dbf7c`, `unregister_kretprobe=0x4093e955` at `0x19dcf48`.
All 68 existing stock references agreed with the same decoded export table.
New missing/mismatch ABI regressions failed before the gate was implemented.
Follow-up review found no remaining Important findings and independently passed
all 101 host tests. The matching native build and one instrumented attempt are
recorded below. This remains a RAM diagnostic, not a deployable boot port.

#### Verified build and instrumented result

[Actions run 39646562317](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39646562317)
succeeded from `968b47a471fec9c4b0b592a35330ff10bca10334`, after updating the
repository guard for GitHub's canonical `Quest-2-Steam-OS-Dual-Boot` location.
All 101 tests passed on Linux. Local verification reproduced the transport
digest, ZIP CRC/12-file allowlist, all 11 checksums, source/compiler/config pins,
no-jump compile command and all 77 module version records / 74 undefined imports
against generated and stock references. Both indirect ABI CRC records also
matched, and the complete audit manifest matched the local recomputation.
Native LLVM confirmed ELF/vermagic and all three diagnostic parameters.

ZIP SHA-256: `aa01719a4fb2a9230a698aa6be22397be14979a4ef9217615043fc14ded3af17`.

Tested `quest_kexec.ko`: 331,104 bytes, 42 version records, SHA-256
`cf6aa48b66f3b91e238da76f886b440874895a028a92ecffc9a713f23831331a`.
The other two modules were audited offline and were not loaded.

On 2026-10-21, a fresh matching capture again selected the first high window.
After Home readiness and current root/boot/health checks, exactly one attempt
used `validate_ram=1 ram_diagnose=1 ram_base=0x100000000`. It reported:

```text
RAM validation result=-16 elapsed_ns=6137657 owned=0
start_isolate_page_range calls=1 result=-16
has_unmovable_pages calls=12 result=1
unmovable block starts at PFN 102c00
test_pages_isolated calls=0
isolate_freepages_range calls=0
```

Both missed counters were zero for all four observers. This identifies an
**initial pageblock-isolation refusal**. In the [pinned allocator](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/page_alloc.c),
that error returns before migration. The first reported unmovable block is
`[0x102c00000, 0x103000000)`: 1,024 pages / 4 MiB with this kernel configuration.
The exact busy PFN, notifier allowance and owning allocation remain unknown.

Non-atomic flag snapshots contained the same two SLAB-marked PFNs inside that
block before probe installation and after the attempt. This is consistent with
pre-existing kernel allocations obstructing isolation; it does not establish
which page the native predicate encountered or which subsystem owns it.
Probe allocation/timing remains an additional limitation.

No RAM was acquired. `insmod`'s inner exit status was 1 even though the root
transport returned 0. All four summaries were emitted after observer removal;
failed init left no project module resident. The unique temporary file was
hash-checked, removed and confirmed absent. The same boot, CPUs 0-7, 20 stock
module names, root access and taint 5124 were preserved. The captured log
difference contained no matched panic, BUG, WARNING, OOM, unhandled fault or
SMMU/KGSL fault/error. The user subsequently confirmed Home and head/controller
tracking remained normal. No retry, other window, payload, shutdown, reboot or
flash was attempted.

### Selected high-RAM candidate: on-headset result

On 2026-10-21, a fresh rooted capture matched the expected device, firmware and
kernel. The planner again produced eight unclaimed windows and 72 DT reference
clients. After the user confirmed Home usable and idle, exactly one attempt used
`validate_ram=1 ram_base=0x100000000`, selecting the 52 MiB range
`[0x100000000, 0x103400000)`:

```text
quest_kexec: RAM validation range=[100000000,103400000)
quest_kexec: RAM validation result=-16 elapsed_ns=3657553 owned=0
```

The model, symbol and System RAM checks passed, and no per-PFN refusal message
appeared. This indicates a native allocator refusal, not an accepted reservation.
The exact busy PFNs and ownership preventing migration remain unknown. No second
candidate, retry or forced claim was attempted. The other seven candidates
remain untested; two refusals do not establish that all RAM is unusable.

The ADB/root transport returned zero while `insmod` printed "Device or resource
busy". The kernel result and absent module establish the refusal; transport
exit status alone would incorrectly classify this test as successful.

The boot ID, online CPUs 0-7, original 20 stock module names and taint 5124 were
unchanged. Root remained available. The captured log difference had no matched
panic, BUG, WARNING, OOM, unhandled fault or SMMU/KGSL fault/error messages.
Failed initialization left no project module resident, so no unload was needed.
The exact temporary module was hash-checked, removed, and confirmed absent.
After the test, the user confirmed Home and head/controller tracking remained
normal. No payload, shutdown, CPU offlining, reboot, flashing or boot-policy
change occurred.

The module came from [new-repository Actions run 39570913751](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/actions/runs/39570913751),
source `88df1dbf1339efe512e0dac26ab42df771b03839`. Runtime source/tools/workflow
were unchanged at the test checkout `5d1f7de836be7c2415d68a9a61ff2df90d47d07a`.
The ZIP digest and all 11 inner checksums matched. Native LLVM confirmed ELF64
little-endian relocatable AArch64, exact vermagic and both diagnostic parameters.
The current-repo audit verified all 42 module version records and 41 undefined
imports against generated and recorded stock references; the compile command
did not enable experimental jumping. All 89 host tests passed on Windows.

ZIP SHA-256: `821256b8c16776539344585a19a2030c845a40518c1058a6892b934ad730ad5b`.

Tested `quest_kexec.ko`: 314,968 bytes, SHA-256
`1fa947fb6ee0b160215f2d0fb6ad66915468382d30707bea99e3278cf3b9a1a3`.

### Placement strategy and Hollywood DMA gate

The [placement/shutdown strategy](QUEST2_HANDOFF_STRATEGY.md) adds an offline
capture-backed candidate planner, `ram_base` for the RAM-only diagnostic, and a
Hollywood hardware-transition refusal even in experimental builds. The saved
capture produced eight unclaimed windows above 4 GiB and 72 DT reference
clients; neither count proves ownership or DMA quiescence. The diagnostic
parameter cannot change the payload/DTB/entry layout. No headset changes or
candidate allocation attempt were performed in this phase.

All 19 new regressions were observed failing before their implementation;
the full 89-test host suite passes. The new `param_ops_ullong` CRC was derived
directly from the hash-pinned stock Image (`0x28916948`, CRC file offset
`0x19d6958`) and matches the prior verified generated symvers. A fresh source
review found no Critical or Important issue. Minor follow-ups remain for
inconsistent `phandle`/`linux,phandle` aliases and an aligned overflowing-base
test input; neither changes the hardware refusal gate.

[Archived Actions run 39567877831](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`ce5dfce20a5fb830632f291ae3a44844da9b2bd5`. All 89 tests passed on Linux and
the matching kernel and three modules built. Local checks reproduced the
transport digest, ZIP CRC/12-file allowlist, 11 checksums, source/compiler/config
pins, no-jump compile command and all 77 version records / 74 undefined-symbol
references against generated and stock references. Native `llvm-readelf`
separately confirmed ELF/vermagic and the `ram_base:ullong` parameter.

ZIP SHA-256: `ba9933bd6b85353a303410e2705bf8717839e07221e328cbfca66575e46af83e`.

`quest_kexec.ko`: 314,880 bytes, 42 version records, SHA-256
`fedfc4927497b0f10451adb9365cbcb2c8456a5b1ccc600b4525a3417555137b`.
The other two module hashes are unchanged from the prior rebuild. This native
build was not deployed; no high-window claim or DMA shutdown was attempted.

### RAM-ownership and shutdown rebuild

[Archived Actions run 39557599099](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from corrected source commit
`cdfc84a4bc30d9cfcc997ea5f4713bba3c6dfc02`. All 63 host tests passed on Linux,
and the matching kernel and three modules built without warning/error log lines.
The artifact was downloaded and locally checked against its transport digest,
12-file allowlist, all 11 checksum entries, pinned provenance, stage-only
compile command, ELF/vermagic metadata and generated/stock symbol CRCs.

ZIP SHA-256: `015fc4eea6c3d478212ee1c9c65fb1e197445addd83570e0bcdc92cc3a578fb3`.

| Module | Version records | SHA-256 |
| --- | ---: | --- |
| `quest_kexec.ko` | 39 | `562e6c33618b197299c8b4e68fbd8a3b2f91d2ba56efbb695a40c83d07b67cd1` |
| `marker_read.ko` | 15 | `251820730e1023e858e254c4af230070c4ba99cf658c583373159c0a11591605` |
| `usb_log.ko` | 20 | `d43889ccff99e01703836a8082c2df92da57d64c89c8d8ec0b3ca7e5b45f6753` |

All 74 version records and 71 undefined-symbol references passed the checks.
The two new built-in import CRCs (`pfn_valid`, `contig_page_data`) were also
independently rederived directly from the hash-pinned stock Image. All 12,362
`Module.symvers` records equal the baseline below after sorting; the rebuilt
kernel Image has the same SHA-256 as that baseline. Native `llvm-readelf`
separately confirmed the three AArch64 relocatable headers and exact vermagic.
No module was loaded. These remain offline checks, not a hardware boot test.

One minor test-coverage improvement remains: combined successful destination
claim followed by a staging failure; current tests cover those cleanup branches
separately. The initial run 39557198852 was intentionally cancelled before
completion when the Syncboss suspend-abort correction was identified.

### RAM-only rebuild and on-headset validation

The RAM-only diagnostic and its seven new regressions passed the host suite.
A fresh source review found no Critical or Important issue in this path.
[Archived Actions run 39562556932](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`6410b712e8a489b54d2d10c30bf004457844cf11`. All 70 tests passed on Linux and
the matching kernel and three modules built. Local checks reproduced the ZIP
digest, 12-file allowlist, 11 checksums, source/compiler/config pins, no-jump
compile command, ELF/vermagic metadata and all 76 version records / 73 undefined
references against generated and stock references.

ZIP SHA-256: `a67f14afe81cc8f309d1f6aac62bfbaf747dbde0b6c45f0c915d889b6b57f486`.

The tested `quest_kexec.ko` has 41 version records and SHA-256
`c88968617455a10b28f9fd8f70f7eca9ce669935578553973971b939284f6d62`.
The other two modules retain their hashes from the preceding rebuild and were
not loaded.

On 2026-10-21, exactly one `validate_ram=1` attempt returned:

```text
quest_kexec: RAM validation result=-16 elapsed_ns=6273646 owned=0
```

`insmod` returned "Device or resource busy". The result follows the model,
symbol and System RAM checks; no per-PFN refusal message appeared, indicating
the native allocator refused the fixed `[0x90000000, 0x93400000)` window. No
ownership was acquired. The precise busy page/owner is unresolved; earlier
non-atomic page metadata snapshots cannot establish the cause. No retry or
forced claim was made.

The boot ID, CPUs 0-7 and original 20 stock module names were unchanged.
Failed initialization left no project module loaded. The temporary module
file was hash-checked and removed. Taint changed from 1028 to 5124, exactly the
expected out-of-tree-module flag, which persists until reboot. The captured
log difference contained no matched panic, BUG, WARNING, OOM or fault patterns.
The user confirmed Home and tracking were normal. No payload open, shutdown,
CPU offlining, USB stop, reboot, flashing or boot-policy change occurred.

Passive topology, runtime device-tree and IRQ checks also completed. They
showed bound devices and activity in GPU, USB, Syncboss and CDSP IRQ paths;
camera-frame operation was not established. No GPU/Syncboss suspend rehearsal
was performed: the callbacks currently lack an adequate recovery oracle.

The pinned ARM SMMU, camera request-manager, camera SMMU and ADSP-loader driver
registrations lack shutdown callbacks. Generic device shutdown does not invoke
their remove/suspend paths or close frozen userspace camera handles. IOMMU
groups, runtime PM state and quiet IRQ counters therefore cannot establish DMA
quiescence. Parent/bus/domain effects still require tracing; an absent callback
alone does not prove DMA continues after all shutdown paths. An explicit
device-lifecycle and DMA-fencing design is still needed.

Minor review follow-ups remain for early refusal-stage logging and negative
walker, partial-coverage, incompatible-Kona and timing/log tests. These do not
change the observed allocator refusal or establish hardware readiness.

### Earlier baseline

[Archived Actions run 39551869780](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
completed successfully from source commit
`3668c76984ee4159f19f4f2018957be3c8693f34`. It built the matching kernel Image and
modules, then the three patched out-of-tree modules with experimental jumping
disabled. All 47 host tests also passed on the Linux runner.

The downloaded ZIP's SHA-256 is
`11fe06971c7af7a558aefaac532af352eaf2fd9056c4c262aef6d9b897f3492b`.
Local verification reproduced all archive checksums, source/compiler/config
pins, AArch64 ELF/vermagic identity, undefined-symbol coverage, and all 72
version-record CRCs against the prior trusted built-in stock references:

| Module | Version records | SHA-256 |
| --- | ---: | --- |
| `quest_kexec.ko` | 37 | `1018b0a5c3c7e4166f026f8699d1b8cdc7b5ce8c8f6b4c27a3c4369224c867dd` |
| `marker_read.ko` | 15 | `251820730e1023e858e254c4af230070c4ba99cf658c583373159c0a11591605` |
| `usb_log.ko` | 20 | `d43889ccff99e01703836a8082c2df92da57d64c89c8d8ec0b3ca7e5b45f6753` |

All 12,362 full `Module.symvers` records equal the prior matching build; their
textual order differs. The configuration is unchanged. The kernel Image and
module binaries are not claimed byte-identical to previous builds. Native
`llvm-readelf` independently confirmed the three modules' ELF headers/vermagic.
These checks establish the stated offline build and symbol-version agreement;
they do not establish hardware readiness.

The manually triggered workflow uses an owner-provided private config secret
and publishes only allowlisted build outputs. It does not cache the config or
upload raw captures/config/firmware. Artifacts are retained for seven days.

## Reproducing the rebuild

Use a fresh module output/source copy and the existing fully built matching
Android kernel output. Preserve the original `.config`, `Module.symvers`,
kernel Image, source revisions and captured mitigations. For this firmware,
`REAL_CC=clang` is required by the downstream kernel Makefile.

```sh
make -C "$KDIR" M="$PWD/module" \
  ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang REAL_CC=clang LD=ld.lld \
  HOSTCC=clang HOSTCXX=clang++ CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LOCALVERSION=-g49638c7a8637 \
  QKX_EXPERIMENTAL=0 modules
```

This is an offline build command, not a deployment command. Require exit zero,
ELF64 little-endian relocatable AArch64 output, the exact release/vermagic,
version coverage for every undefined import, and CRC agreement with immutable
matching stock references. Confirm the effective loader compile command does
not define `QKX_ENABLE_EXPERIMENTAL_JUMP`.

The earlier local audit reported agreement for 11,137 built-in stock export
CRCs, 77 imports across the three original modules, and 33 selected private
structure offsets. These results refer to the previously inspected artifacts.
Raw captures, device identifiers, firmware files and private runtime evidence
are not published here, so that audit is not independently reproducible from
this source repository alone. Each subsequent rebuild needs its own recorded audit.

## Offline compact-placement follow-up

The planner now accepts observed-block exclusions and optional Image/initramfs/
single-DTB sizing inputs. The [placement strategy](QUEST2_HANDOFF_STRATEGY.md#observed-block-exclusions-and-compact-copy-proposals)
records validation, CLI use and the private saved-capture exercise. Excluding
`[0x102c00000, 0x103000000)` moved the first fixed 52 MiB diagnostic candidate
to `0x103000000`. Android reference input sizes produced a separate 36 MiB copy
proposal at `[0x100000000, 0x102400000)`, before that block. The reference
ramdisk exceeds the existing loader's 2 MiB limit; these inputs are not a
validated target Linux payload or selected target DT.

All 116 host tests pass, including 27 planner tests. This phase changes only
the host planner, its tests and documentation. The native loader, fixed staging
layout, 52 MiB diagnostic, ABI references and Hollywood transition gate are
unchanged. No new native build, RAM claim, module load or headset operation was
performed. Every proposed range remains unclaimed and handoff remains blocked.

## Native allocation length and payload relocation

The [native layout contract](QUEST2_HANDOFF_STRATEGY.md#native-variable-length-diagnostic-and-compact-staging)
adds diagnostic-only `ram_bytes` and a separate Hollywood `payload_base` compact
staging option. Claim/release lengths, source-page exclusion, System RAM coverage,
copy destinations and final entry/DTB arguments now use the selected layout.
Legacy defaults and the Hollywood hardware-transition gate remain enforced.
Compact staging rejects DTBs with malformed structures, hidden/duplicate chosen
nodes or initrd properties inconsistent with the computed addresses, and stages
the exact validated DTB buffer.

The initial four length/option tests and nine layout/staging tests failed before
implementation. Independent review then reproduced two FDT consumer mismatches:
properties after child nodes and a preceding unchecked `/chosen@0`. Both have
failing-then-passing regressions and are rejected by the corrected checker.
The full 136-test host suite passes, including whole-initialization cleanup and
experimental Hollywood gate coverage.

[Matching Actions run 39686582673](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39686582673)
completed successfully from native source
`9157b54e8bdc72dfa4c7dd0a5ef7caafba9ae3f0`. All 136 tests passed on Linux before
the later host-only profile checker was added. The matching kernel and three
modules built with no warning/error lines in the build logs. Local verification
reproduced the GitHub ZIP digest, 12-file allowlist, 11 inner checksums,
source/compiler/config pins, no-jump compile command, all 77 version records and
74 undefined imports, and the two indirect kretprobe helper CRCs. Native
`llvm-readelf` confirmed AArch64 ELF/vermagic and the new `ram_bytes:ullong` and
`payload_base:ullong` metadata. Subsequent commits leave native/runtime/build
sources unchanged. No module was loaded and no headset operation was performed.

ZIP SHA-256: `b64863767b629c54697479468214d192f524183288f495810d617359d5ac8eb9`.

| Module | Bytes | SHA-256 |
| --- | ---: | --- |
| `quest_kexec.ko` | 350,608 | `3d8818ba0165fcc3000994e97c5a1ba2899fc2a2fe676c9ca83f4a5c07c41276` |
| `marker_read.ko` | 158,832 | `95466ec1914182d71be8e92a6cf1d7e5c19f51e9c751763ac286efc34d9ff267` |
| `usb_log.ko` | 201,760 | `7224ae72bcac9d28421211d4976a642bacd9397b5b44b17ad1da60acc96e072b` |

The [offline Linux payload checker](QUEST2_HANDOFF_STRATEGY.md#offline-linux-payload-checks)
now records structural/profile findings against the capture and compact layout.
All 152 Windows host tests pass. A real run against the available Android
references completed with 15 findings and `HANDOFF_BLOCKED`; the actual native
DTB checker separately returned `-EINVAL`. The stock first DTB differs from the
runtime model and memory map, and its removed-region and ramoops `reg` properties
do not match. The Android config has `CONFIG_DEVTMPFS=n` and
`CONFIG_ION_POOL_AUTO_REFILL=y`; the ramdisk contains Android init rather than
the expected BusyBox shell init. These are rejected references, not a validated
Linux payload. The new offline checker's final independent review is outstanding;
the native source review was approved after the two FDT fixes above.

## Noninteractive Linux build baseline

The owner-only offline workflow now builds the matching kernel source without
target handoff patches, using the public Quest 2 config pinned to upstream
`9c96c192d4a4a80f95ba0013294977e85c6eae11`. Its config disables ION pool
auto-refill and embedded config/initramfs data, enables the external BusyBox
profile, and uses localversion `-qkx-q2-baseline`. The baseline init has no
interactive console or network listener. The source archives, resolved configs
and build recipes accompany the binaries. No raw DTB or device capture is
published.

The private [DTB producer](QUEST2_HANDOFF_STRATEGY.md#private-linux-baseline-dtb-preparation)
requires agreement between the live flattened FDT and captured runtime tree.
It preserves empty nodes, secure/dynamic reservations, bootloader reserve-map
bytes and boot CPU, then sets minimal bootargs and the compact initrd addresses.
Its 13 regressions and the full 165-test host suite pass; independent review of
this new producer found no remaining actionable findings after two placement
fixes. That review did not cover the prior profile checker internals or hardware.

A fresh passive capture and bounded live-FDT read completed on the known
matching headset. All non-`name` properties agree; the live bootloader reserve
map has no nonzero entries. The boot ID stayed unchanged across the FDT read.
No module, allocation, tracking/PM, settings, reboot or filesystem change was
performed on the headset during this phase. Device-specific files stay private.

The initial baseline CI run compiled the Image and passed all 152 tests in its
source revision, then failed building BusyBox because ARM64 libc development
headers were absent from the runner. The workflow now explicitly installs
`libc6-dev-arm64-cross`.
The [rebuild, run 39707716030](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39707716030),
completed successfully from `2c0f7891227f3bf1119d52ad7fb95116c7d592e6`.
Local verification matched its GitHub ZIP digest, 14-file allowlist and all
13 inner checksums, source/config/compiler pins, embedded kernel release and
static ARM64 initramfs profile. All 165 tests passed on Linux; BusyBox echo and
init script syntax executed under QEMU user mode. The kernel log contains one
missing WLAN defconfig warning and BusyBox has 58 upstream compiler/linker
warnings; neither log contains a matched compiler/build error. This is not a
warning-free build, and compilation does not establish working peripherals.

ZIP SHA-256: `9d0c488c2b706292e26e8abe610f2cd510afa64f0798cb6cedb714f065a39b0e`.

| Public baseline file | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 30,435,344 | `8ade16120b8b2990dad5cdd4a10d6d628b58ee3de215a13395cced43dedc059f` |
| `initramfs.gz` | 1,177,854 | `ab568dc18162b3e1f871da7a5928923dc206190aa406cd53d432c8499dc43436` |
| `target.config` | 153,624 | `80d470298cccdb792903fe6eb724a30f76e8dbf482f206228ffde4f31672143b` |

The private prepared DTB is 506,829 bytes. Its profile report has zero findings;
the actual native layout and DTB functions, compiled with host API doubles,
both returned zero against the same files. The resulting copy envelope is
36 MiB at `0x100000000`, avoiding the observed block beginning `0x102c00000`.
That address remains an unclaimed snapshot candidate. The prepared tree retains
the live node set and bootloader reserve-map bytes; the copied initrd addresses
agree with both host and native calculations. The hashes in the private
completion manifest bind these exact inputs. No physical staging or boot was
performed. A passing offline baseline does not validate the missing target
handoff patches, secure ownership, device shutdown or Linux hardware startup.

## Reviewed startup build

The [startup patch review](QUEST2_STARTUP_PATCH_REVIEW.md) selects one GICv3
pending-SPI clear after the stock disable/deactivate helper. Other inherited
hardware changes remain deferred. The owner-only workflow preserves the default
unmodified baseline and accepts only the exact reviewed `startup-gic-pending`
patch profile, with release `4.19.325-cip128-st12-qkx-q2-startup`.

[Actions run 39714699023](https://github.com/WitherBlitz/Quest-2-Steam-OS-Dual-Boot/actions/runs/39714699023)
passed from `d6c64f1c592d29a137fc637b1d251033ec186585`. All 174 host tests
passed on Linux. Local verification matched the archive digest/size, 14-file
allowlist, all 13 checksums, kernel/config/compiler pins, Image header/release
and static ARM64 initramfs profile. BusyBox echo and init script syntax ran
under QEMU user mode. The BusyBox build has 58 upstream compiler/linker warning
lines; neither saved build log contains a matched compiler/build error.
Reconstructing the patched source from the unmodified kernel archive and the
port archive reproduced the exact pinned driver hash; the common helper and
the archived build/init recipes also matched their source pins.

ZIP SHA-256: `8b88428b05d057e2be79be3641952301f47b32dc4b68e4d40d6c0e633c755e2a`.

| Public startup file | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 30,435,344 | `aab25dd416744e606959fce84bd9d1f7d43a4d1db690e8457d15e12e5e294c2a` |
| `initramfs.gz` | 1,177,854 | `1d807a4a1c9f6917b2500f62780c9154b9c3e01ad4ec975762d18e023ca30542` |
| `target.config` | 153,624 | `80d470298cccdb792903fe6eb724a30f76e8dbf482f206228ffde4f31672143b` |

A new matching passive capture/live FDT supplied a private prepared DTB of
506,829 bytes. All 3,044 live nodes, boot CPU and complete bootloader reserve-map
bytes were preserved. Ignoring generated `name` properties, only chosen bootargs
and the two initrd address properties changed. The structural/profile report
has zero findings, and actual native layout/DTB helpers compiled with host API
doubles both returned zero. The Image declares 32,251,904 bytes of memory with
`text_offset=0x80000`; the complete envelope is 36 MiB at
`[0x100000000, 0x102400000)`, avoiding the previously observed unmovable block.
These are offline checks; no Image, initramfs or DTB was staged on the headset.

## Compact 36 MiB RAM claim and release

On 2026-10-22 UTC, after the user confirmed idle usable Home, exactly one
instrumented RAM diagnostic used `validate_ram=1 ram_diagnose=1`,
`ram_base=0x100000000` and `ram_bytes=0x2400000`. The unchanged native module
from source `9157b54e8bdc72dfa4c7dd0a5ef7caafba9ae3f0` was reverified
against its archive/module hashes, source, no-jump build and ABI audit above.
The new target startup patch was not loaded or executed.

The native result was:

```text
RAM validation range=[100000000,102400000)
RAM validation result=0 elapsed_ns=36110368 owned=1
start_isolate_page_range calls=1 result=0
has_unmovable_pages calls=9 result=0
test_pages_isolated calls=1 result=0
isolate_freepages_range calls=1 result_pfn=102400
RAM validation released destination; no payload or shutdown
```

Both missed-event counters were zero for all four observers, whose summaries
were emitted after probe removal. All 9,216 pages were temporarily owned;
allocation took about 36.11 ms. The module was normally unloaded and its exact
hash-verified temporary file removed, with absence confirmed. Fresh passive
checks recorded the same boot, CPUs 0-7, stock module set and taint, no matched
new fault signatures, and resumed Home. The user confirmed **Home and tracking
are normal**. This scope made one claim attempt only. A host log parser failed
on ANSI-colored prefixes after cleanup; offline normalization of the saved
captures recovered the complete result without repeating the device operation.

This demonstrates allocator acceptance for that attempt. Ownership was released
inside initialization; there is no retained reservation, future allocation
guarantee, payload relocation result or DMA/secure ownership proof. Allocation
and probes can perturb runtime memory. No PM/device shutdown, CPU/MMU action,
watchdog operation, reboot, flashing or boot-policy change occurred. Raw device
captures and the prepared DTB remain private. `HANDOFF_BLOCKED` is unchanged.

## Shutdown and recovery evidence

The [shutdown validation record](QUEST2_SHUTDOWN_VALIDATION.md) adds an exact
nine-file source audit, seven copied function comparisons, source models and
one fixed-command passive capture. ADSP/CDSP and six other subsystem states
reported ONLINE; camera/SMMU drivers remained bound. Watchdog software disable
was zero and current panic timeout was -1. The capture preserved boot, identity,
CPUs, stock module names and taint without device mutation.

Host tests reproduce the published camera close path's nonfinal retained write
lock and discarded subdevice errors, shared DSP reference behavior, watchdog
disable failure/teardown and panic limits. These are source branch results,
not demonstrated stock-device defects or hardware completion. No shutdown,
watchdog/reset, recovery or Linux boot test was performed. All 204 host tests
pass; independent source/probe review found no remaining actionable findings.
The loader, kernel patches and hardware gate are unchanged by this phase.

## Remaining work

The original fixed window and first capture-planned high window were refused.
The instrumented attempt identifies initial pageblock isolation and an unmovable
4 MiB block. Offline planning now accounts for caller-supplied observed blocks
and supplied payload sizes. The native source now coordinates compact ownership
and relocation. The baseline and partial GIC startup Image/initramfs profiles
and private prepared DTBs pass offline profile/native checks. The compact
36 MiB diagnostic demonstrated one successful claim/release. Remaining target
handoff patches still need porting and review before this can become a handoff
target. Any subsequent live claim needs the verified native artifact, fresh
evidence and a justified scope; `ram_base`
alone retains the default 52 MiB length, while `ram_bytes` selects the test size.
Snapshots alone cannot identify the refusing owner or prove other windows free.
A reservation held through staging/handoff and runtime use of compact payload
relocation remain unproven.
Forcing busy pages or repeatedly guessing addresses is not a solution. DMA
quiescence, secure-watchdog handling, CPU/MMU transition and recovery remain unproven.
Panicking after irreversible
failure prevents further handoff but does not prove safe recovery. The target
DT must preserve Android's secure/dynamic reservations; an allocator claim of
the copy window does not fence all target RAM. The broad `qcom,kona` guard is
not exact-board proof.
The scoped live initialization above exercised RAM validation only. No reboot,
flashing, partition change or boot-policy change occurred. Staging and RAM
validation allocate or migrate memory and use a temporary kprobe; they are not
read-only device tests.

The pinned shutdown implementation also sets these limits on future tests:

| Area | Source evidence | Still needed before hardware readiness |
| --- | --- | --- |
| RAM | Earlier 52 MiB claims refused; one compact 36 MiB claim/release succeeded. The matching native build and baseline/partial-startup payloads pass offline checks. | Ownership held through staging/handoff, remaining target handoff patches and runtime preservation of secure/dynamic reservations. |
| GPU/Syncboss | PM callbacks can defer work or hide errors. | Completion and failure detection on this firmware, including rollback. |
| USB | Gadget suspend stops/disconnects gadget traffic. | Controller/glue DMA quiescence and target USB reprobe. |
| Storage | UFS shutdown drains requests and powers down the link, but returns zero even after logged errors. | Observed shutdown completion and target reinitialization; no shutdown bypass is implemented here. |
| CPUs/other devices | ARM64 `machine_shutdown` disables nonboot CPUs; generic `device_shutdown` invokes bus/driver hooks without an aggregate success result. | An audit of remaining DMA masters, firmware references and recovery. |

The eventual goal is Horizon/Linux switching with only one full OS running.
Display, tracking, controllers, audio, networking, suspend and a SteamOS runtime
require subsequent implementation and physical validation.

## Source basis for the ownership/shutdown changes

All links below refer to the matching immutable kernel source, not a newer API:

- [`mm/page_alloc.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/page_alloc.c):
  `alloc_contig_range` owns every PFN on success and pairs with `free_contig_range`.
- [`include/linux/gfp.h`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/include/linux/gfp.h):
  exact four-argument allocator ABI and single-zone requirement.
- [`mm/cma.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/mm/cma.c):
  CMA has a separate ownership bitmap; bypassing it is not a valid reservation.
- [`kernel/power/process.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/power/process.c):
  failed `freeze_processes` already thaws userspace.
- [`drivers/usb/dwc3/gadget.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/usb/dwc3/gadget.c):
  gadget suspension disconnects/stops traffic; later failures cannot use the
  existing return-to-apps cleanup as a complete USB restoration protocol.
- [`kernel/reboot.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/reboot.c):
  `kernel_restart_prepare` invokes reboot notifiers and device shutdown.
- [`drivers/base/core.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/core.c),
  [`arch/arm64/kernel/process.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/arch/arm64/kernel/process.c)
  and [`drivers/scsi/ufs/ufshcd.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/scsi/ufs/ufshcd.c):
  device/CPU shutdown coverage and UFS error-reporting limits in the table above.
- [`drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/staging/oculus/mcu/syncboss/syncboss_spi_core.c)
  and [`drivers/gpu/msm/adreno.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/gpu/msm/adreno.c):
  deferred Syncboss resume and unreported GPU power-state errors limit what a
  successful PM callback establishes.
- [`drivers/base/platform.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/base/platform.c),
  [`drivers/iommu/arm-smmu.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/drivers/iommu/arm-smmu.c#L6228),
  [`cam_req_mgr_dev.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_req_mgr/cam_req_mgr_dev.c#L861),
  [`cam_smmu_api.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/camera/drivers/cam_smmu/cam_smmu_api.c#L3938)
  and [`adsp-loader.c`](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/techpack/audio/dsp/adsp-loader.c#L474):
  platform shutdown calls the registered shutdown hook only; these driver
  registrations provide no such hook. Remove/suspend and camera-client close
  paths therefore cannot be assumed to run during generic shutdown.
