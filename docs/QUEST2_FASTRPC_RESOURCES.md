# Quest 2 FastRPC resource ownership boundaries

This continues the [tracking ownership investigation](QUEST2_TRACKING_OWNERSHIP.md).
The additional decoder identifies userspace bookkeeping in two exact libraries.
It cannot establish complete kernel/firmware ownership, successful release or DMA
quiescence. No shutdown or Linux handoff is enabled by these observations.

## Matched artifacts and layout

The verified Hollywood build is `52242990024200150`, kernel
`4.19.325-cip128-st12-g49638c7a8637`. Layout selection requires an exact SHA-256:

| Library | SHA-256 |
| --- | --- |
| `libcdsprpc.so` (R) | `1b5f80e8128b10e148f53aa61e47259f3becd648ca9efc23bc0fd1b5ef8a75a1` |
| `libadsprpc.so` (A) | `47882286b2500dbe509660e28bd39c6d7c87f2dd418da25abbcf2a6e22787404` |

Addresses below are linked VAs in those artifacts, never live process addresses.

| Bookkeeping root | R VA | A VA | Bounded layout |
| --- | --- | --- | --- |
| Domain table pointer | `0x3fa28` | `0x3ea28` | 16 domains, stride `0x248` |
| Local module cleanup IDs | `0x3faa0` | `0x3eaa0` | 16 circular heads, stride `0x10`; nodes `0x28` |
| Global buffer registrations | `0x3ffb8` | `0x3efb8` | One circular head; allocations `0x40` |
| Pending DMA registrations | `0x3fff0` | `0x3eff0` | 256 inline records, stride `0x10` |
| Per-domain mmap records | `0x40ff0` | `0x3fff0` | 16 heads, stride `0x38`; allocations `0x40` |
| DMA highwater bound | `0x41370` | `0x40370` | Signed count, range 0 through 256 |
| rpcmem allocation list | `0x419c0` | `0x409c0` | Circular head; nodes `0x28` |
| Local catalog PLS root | `0x425d0` | `0x415d0` | Bounded descriptor chain |
| Local module pool | `0x42a28` | `0x41a28` | 20 inline slots, stride `0x100` |

`tools/quest2_fastrpc_resources.py` is separate from the forward remote-handle
decoder. It reads the domain pointer, all domain busy/deinitializing counters,
fixed heads/counts, DMA records below the highwater bound and nodes reached from
those heads. Only 60 meaningful bytes of each buffer/mmap allocation are read.
It never reads the buffer payload, remote address, module code or mutex state.

An aggregate cap of 64 linked nodes applies across its resource lists. Unknown
hashes, invalid extents, nonreciprocal links, cycles, ambiguous membership,
overlapping records and malformed source-defined fields refuse decoding.
Pristine and constructed-empty initialization states are distinguished. The
caller must independently establish permitted memory mappings and read budgets.

Important bookkeeping semantics:

- Local module IDs are cleanup records, not proof that a module is still open.
  Normal module close does not remove the corresponding cleanup node.
- `remote_register_fd` can insert a buffer record with reference count zero.
  Signed buffer reference observations are preserved; zero is not a release.
- The DMA count is a highwater bound. Consumption clears `inuse`; holes remain
  below the bound. It is not a populated-record count or a count of kernel maps.
- An mmap record can legitimately retain reference zero after a failed unmap.
  Raw buffer/remote addresses and tagged list values are preserved as data;
  normalization is used only for structural reads and checks.

## Where release errors must be observed

| Operation | Exact inspected boundary | Consequence for a checked adapter |
| --- | --- | --- |
| Remote handle close | R `0x20c1c..0x20c28`, A `0x1fc10..0x1fc1c` selects the remote result only after successful transport; otherwise it retains transport failure. | Preserve this original result and the full owned handle before outer cleanup. |
| Remote64 cleanup | R `0x20f50..0x20f84`, A `0x1ff40..0x1ff74` can unlink/free local records despite a failed close. | A missing forward record cannot acknowledge release. |
| Buffer unregister | R `0x240b8..0x240d0` unlinks before unmap; `0x24290..0x24294` frees afterward. | Record individual unmap results before record destruction. |
| mmap unmap | R `0x2449c` clears the reference before the ioctl. Failure usually restores one, but EINVAL/ENOTTY paths can leave zero; success unlinks at `0x24500..0x24524`. | A zero reference is not a successful unmap receipt. |
| Domain deinit | R `0x190b8..0x19154` frees mmap records and clears associated registration flags. | Require actual mapping-release evidence, not the emptied userspace list. |
| Local module cleanup | R `0x19264..0x1927c` closes then frees tracked IDs regardless of close result. | Preserve the original result and separate tracked IDs from active module catalog entries. |

The direct mapping APIs are a separate coverage gap: R `remote_mem_map`
`0x24b08` and `remote_mmap64_internal` `0x24ef8` issue mapping ioctls without
inserting records in the lists above. Their unmap paths at `0x24d24` and
`0x251f4` likewise operate directly. Unmap can return public success when the
domain table or device FD is unavailable, without performing the operation;
other branches fall back to older ioctls after a failure. A checked receipt
must preserve the selected domain/valid FD, the branch taken, each original
ioctl result and errno, and the final API return. Public zero alone is not
proof that an unmap occurred.

The camera artifacts introduce additional result losses. In matched
`libcamxcsl.so`, the device-backend release at `0x2327c` can fail, then common
cleanup erases the acquisition at `0x23668` and returns zero. Stream-off calls
link control at `0x25a14` without preserving that result and changes stream
flags before final error propagation. Therefore even a public CSL success is
insufficient evidence for the corresponding lower-level operation.

In the matched fastpath camera implementation, `MonoCameraOculus::Stop(bool)`
at `0x13b730` has distinct reference and forced-stop paths. Worker notification,
exit and join must be recorded independently of outstanding frame returns.
`ReturnFrame` at `0x13b21c` changes per-buffer state and the outstanding counter;
joining the worker alone cannot prove that consumers returned their buffers.
`DestroyCamera` calls public CSL release at `0x13b058`, ignores its result and
clears its own handle. See the [service lifecycle audit](QUEST2_SERVICE_LIFECYCLE.md)
for artifact identity and the prior surrounding call trace.

## Required owned lifecycle contract

### Static camera object locator

The matched camera artifacts provide a source-bound candidate lookup chain.
This chain has not yet been observed on live objects. Using the lifecycle
audit's C/F/S artifact identities:

1. C's global weak pointer is `{HAL, control}` at `0x10a28/0x10a30`; its factory
   stores it at `0x6b48..0x6b50`, and `GetCameraHal` locks its weak control before
   returning the object. A read-only observer cannot perform that retention.
2. The HAL has F vptr `0x1fb1b0`. Its 15 retained camera shared-pointer pairs
   start at object `+0x10`, stride `0x10`, as checked by `GetCamera` at
   `0x1130f4..0x11311c`.
3. A matched Mono camera has F vptr `0x1fd9a8` and allocation size `0x260`.
   Fields `+0x128/+0x12c` hold the full CSL session/link handles;
   `+0x168/+0x16c/+0x170` hold three acquisition handles. Its worker-active byte
   is `+0x1d8`, outstanding frame count `+0x1f8`, start references `+0x254` and
   enabled state `+0x255`. Those fields are observations, not acknowledgements.
4. CameraBase `+0x80` can hold S's frame callable, with vptr `0x1c96b8` and the
   captured CameraSystemBase pointer at callable `+8`. The system's retained
   camera vector, reached through `*(system+0x1a8)`, supplies a reciprocal
   camera/control-pair membership check. This does not identify a tracking IPC
   client or establish authority over the camera.

C's `+0x30` field cannot serve as frame admission control: `onNewFrame` invokes
the frame callable at `0x63d8..0x63f0` before reading that field at `0x6400`.
F's worker checks its separate `+0x1d8` byte; observing it is not a supported
hook or a completed join/drain. Exact hashes, vptrs, object/control identities,
mapping bounds and paired reciprocal checks are needed before any candidate
runtime observation. Pointer/control reuse, ABA and successful creation history
cannot be established by this static lookup or read-only snapshots.

### Runtime control prerequisites

A future runtime adapter must bind each operation to the actual client/object
generation and retain full successful creation, acquisition and link handles.
It must close admission to new submissions before drain, retain frame/request
tokens, observe worker exit/join and every outstanding return, and preserve
per-device stop, link-control, backend release, transport and close results
before stock cleanup erases them. Errors, timeouts, missing completions, new
submissions or reused identities must prevent a drained/released result.

The live `CameraBase` to fastpath-object binding, creation provenance of existing
sessions, complete request/buffer ownership, admission control and runtime hook
ABI/CFI/thread-lifetime behavior are not established. No observation hook is
installed. Creating a new test client would establish only that client's
ownership; it would not give authority over existing Horizon resources.

The local module catalog and rpcmem allocations are separate from this resource
decoder. The additional allocation decoder below covers selected bookkeeping
for them. Shared kernel files, firmware clients
and kernel-held mappings also remain unproven. The userspace inventories do not
establish a global ownership fence. The Hollywood hardware/DMA/recovery gates stay closed.

## rpcmem and current local catalog decoder

`tools/quest2_fastrpc_allocations.py` addresses the separate allocation and
catalog bookkeeping. rpcmem insertion can bypass the registered-buffer list
when allocation flags contain `0x50000000`, so that earlier list cannot account
for every rpcmem allocation. The decoder walks at most 64 rpcmem records, reads
their structural header and allocation FD, and skips the unknown word at node
`+0x20`. Buffer addresses remain uninterpreted data.

The module pool contains 20 fixed slots. Nonzero full internal keys identify
occupied records; public cleanup IDs contain only their low 32 bits. Keys can
increment beyond 32 bits, records can be shared by multiple opens, and numeric
IDs can be reused. A current numeric match cannot establish the historical
domain/client that created a cleanup record.

At most 16 PLS descriptors are walked. The catalog descriptor must match the
source-bound constructor, zero key and destructor. Its application list is
confined to the exact pool slots, with reciprocal links, consistent table
identity, four-byte key pointers, item count, tail and hash-handle offset checks.
Hash bucket arrays are not read. Occupied slots outside that checked list are
retained as raw observations; they are not silently labelled live members.

The initialization result and allocator FD are the eight initialized file-data
bytes at R `0x3f9c8` / A `0x3e9c8`, initially `(-1,-1)`. They are inside the
matched writable ELF load's file extent, not its BSS. A runtime reader must
authorize this exact source-defined exception separately from BSS roots.

rpcmem free unlinks its record before unregister, host `munmap` and allocation
FD close, and discards those results. rpcmem deinitialization closes the allocator
FD without establishing release of every allocation. Local module close also
unlinks before callback/`dlclose` and clears occupancy despite failure. Preserve
the original callback result, `dlclose` result, output error and helper return
separately; no success meaning is assigned to the inspected raw helper `0x200`
path. An empty pool/list cannot serve as a release receipt.

### Separate allocation observation

One separately reviewed paired allocation observation subsequently completed
against the same four holders. Its distinct fresh passive scan retained four
process-disappearance coverage gaps; current identity, boot and observed RPC
holder identities matched. No previous scan or output receipt was overwritten.

| Selected executable | rpcmem allocations | Occupied pool slots | Checked PLS application-list members | Cleanup records with current ID matches |
| --- | --- | --- | --- | --- |
| `/vendor/bin/adsprpcd` (first instance) | 3 | 1 | 1 | 1 |
| `/vendor/bin/adsprpcd` (second instance) | 2 | 1 | 1 | 1 |
| `/vendor/bin/cdsprpcd` | 3 | 2 | 2 | 2 |
| `/odm/bin/trackingservice` | 2 | 2 | 2 | 3 |
| **Total** | **10** | **6** | **6** | **7** |

Each sampled rpcmem `{buffer address, length, FD}` tuple matched a registered
buffer tuple in the same process view. Those FDs remain raw scalar observations;
only the domain FDs received the shared collector's character-device metadata
checks. All initialization references were one, initialization results zero
and allocator FDs nonnegative. The three trackingservice cleanup records match
only two current catalog IDs, consistent with sharing/reuse; the observation
does not distinguish historical owners or establish a generation fence.

The collector transferred **263,936 bytes in 148 memory reads**: 50,880 bytes
across 88 fixed BSS transfers, 64 bytes across eight exact initialized-data
reads, and 212,992 bytes across 52 eligible heap pages. The unchanged shared
limits were 1 MiB, 256 reads and a 300-second host command budget. The combined
64-object cap included forward/resource/rpcmem nodes, PLS descriptors and hash
metadata; descriptors were separately limited to 16 and the fixed pool to 20.

Heap field authorization followed only decoded structural links and rejected
overlap with earlier nodes, domain tables, fixed globals or another object's
range. The initialized-data exception required the exact source fields, matched
file identity/offset and writable ELF load's file extent. Whole fixed pool
records were prefetched within the writable load's BSS, including uninterpreted
inline bytes; raw caches were transient. No buffer/name/callback/code pointer was
dereferenced and no hash bucket array was walked.

Paired selected values, mappings and flags agreed, as did library/process
identity and before/after basic health. The capture completed without a target
file, process stop/write, driver control, ioctl, module or boot change. These
are current userspace observations, not successful allocation/free history,
kernel ownership, release/DMA completion or a new subjective tracking test.

## Earlier bounded resource observation

The earlier paired resource observation on the matching headset completed. Its
fresh passive descriptor scan matched the same four previously verified FastRPC
holders, with five process-disappearance coverage gaps and zero matching FDs
discarded from those gaps. Those gaps remain in the private report; complete
system-wide ownership is not claimed.

| Selected executable | Library | Forward remote handles | Tracked local cleanup IDs | Registered buffers |
| --- | --- | --- | --- | --- |
| `/vendor/bin/adsprpcd` (first instance) | ADSP | 5 | 1 | 3 |
| `/vendor/bin/adsprpcd` (second instance) | ADSP | 4 | 1 | 2 |
| `/vendor/bin/cdsprpcd` | CDSP | 5 | 2 | 3 |
| `/odm/bin/trackingservice` | CDSP | 4 | 3 | 2 |
| **Total** | | **18** | **7** | **10** |

All ten sampled buffer reference counters were one. Each DMA highwater bound
was zero, with no pending inline registrations or listed per-domain mmap
records. Direct mapping paths and kernel-held references prevent interpreting
these empty lists as evidence of no mappings or DMA.

The collector transferred **189,856 bytes across 100 memory reads**: 56 reads
of fixed source-bound BSS spans and 44 eligible heap pages. The shared limits
were 1 MiB, 256 memory reads and a 300-second host command budget, with at most
64 dynamic nodes across forward and resource lists combined. Fixed spans were
cached only within one sample; pages and unrelated bytes were discarded.

File-backed partial-BSS roots required the matched library's path, inode,
device, offset relation and exact ELF writable-load BSS extent. Anonymous BSS
roots additionally required the expected adjacent file anchor and matching
mapping boundary. Heap reads excluded BSS and other named mappings outside the
allocator allowlist. Maps and VmFlags were checked around transfers and after
decoding. The additional decoder never followed a buffer/remote payload address.

Both samples agreed on selected tables, mappings and flags. Each of the four
positive domain FDs was corroborated as the matching non-`O_PATH` character
device. Library hashes and process identities were checked, and before/after
boot identity, CPUs, module names and taint agreed. No service was stopped,
process suspended/written, device opened for control, ioctl issued, module
loaded, target file created or boot policy changed. No new subjective
Home/tracking test is claimed.

Snapshots remain non-atomic; reuse/remap ABA, shared files, direct API mappings
and firmware ownership remain unresolved. Read-only proc-memory access can
fault, touch and COW pages; host deadlines do not bound kernel lock completion.
Raw identifiers, handles, addresses, captures and proprietary artifacts remain
private. This observation supplies no release, DMA, recovery or handoff receipt.

## Verification

Thirteen resource decoder tests cover both profiles, raw tags, legitimate zero
references, DMA holes, source limits and structural refusal paths. The existing
17 forward-decoder, 10 allocation-decoder and 11 CSL tests also pass, as do all
**322 host regression tests** with the configured compiler. Five private reader
tests exercise the real decoders, BSS/file-boundary splits, anchor refusal, allocator restrictions
and caps; nine shared-observer tests pass. Independent pre-execution review
checked both artifacts and fixed mapping fragments. These are interpretation
and observation tests, not a validated shutdown implementation.

The allocation adapter's 12 private tests additionally exercise both layouts,
initialized-data provenance, source-derived dynamic field admission, overlap
refusal even on cached pages, transient whole-pool prefetch and shared caps.
Independent review checked the allocation decoder, mapped data/BSS fragments,
fresh inventory selection and pre-execution observation limits.
