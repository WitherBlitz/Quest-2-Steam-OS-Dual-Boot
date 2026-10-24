# Bounded kernel release-return observation

This diagnostic continues stages 1-3 for Quest 2/Hollywood firmware
`52242990024200150`, kernel `4.19.325-cip128-st12-g49638c7a8637`.
**It is temporary kernel instrumentation, not checked shutdown, complete
ownership, DMA quiescence, recovery validation or permission to boot Linux.**

The running kernel has `KPROBES`, `KRETPROBES` and `KPROBE_EVENTS` enabled.
Function tracing is disabled; the kernel's return-probe event interface is used
instead. No module, binary patch, driver-stop API or target-memory reader is used.
Source references below describe the pinned public kernel, not stock binary
equivalence. The loader and its closed Hollywood gates are unchanged.

## What it does

`tools/quest2_release_trace.py` creates a fresh private tracefs instance and three
namespaced return probes with maximum active-instance count 16 each:

| Function | Captured observation |
| --- | --- |
| `__arm64_sys_getpid` | Harmless child-shell positive control: its reported PID must match a traced return and TID. |
| `fastrpc_release_current_dsp_process` | Signed 32-bit DSP-process release return, if an existing caller naturally executes it. |
| `fastrpc_device_release` | Signed 32-bit file-release return, if an existing caller naturally executes it. |

The tool never invokes either FastRPC function, closes a client's FD or stops a
service to manufacture activity. It observes a three-second window in its own
buffer, configured to 64 KiB per CPU for this eight-CPU target. That is a buffer
setting, not a measured peak-memory or overhead guarantee. No target arguments,
buffers, mapping contents or firmware payloads are fetched. Raw trace output
still contains process/kernel metadata and must remain private.

The source-verified tracefs magic, an inactive global `nop` tracer, stock probe
configuration, exact device identity and existing root are required. Resident
project modules are refused. The collector compares global tracer/probe
definitions and identity/boot/CPU/module/taint observations before and after.
It never clears another user's trace data or probe definitions.

## Invocation and cleanup boundary

```sh
python3 -B tools/quest2_release_trace.py --adb /path/to/adb \
  --serial YOUR_USB_SERIAL --kernel-source /private/kernel-source.tar.gz \
  --output /private/new-release-observation
```

The kernel source must pass the existing pinned archive audit. There are no
caller-selected symbols, register expressions, trace roots or device commands.
Namespace collisions are refused. A single device-side shell transaction has
an EXIT cleanup path for ordinary success, failure and handled signals: disable
its recording/events, remove its instance and unregister only its own probes.
Success requires the cleanup footer, absence checks and unchanged global state.
INT/TERM/HUP are briefly ignored while acquiring the instance and recording its
ownership, then handled normally. Cleanup also ignores them so a repeated signal
cannot interrupt removal. Signals during those narrow intervals do not cancel
the transaction; unhandled termination can still leave completion unknown.

The host has a 90-second aggregate command budget and at most 64 subprocesses.
The transaction subprocess has a 30-second timeout; other commands have at most
15 seconds. Output sections have device-side caps and host validation, including
128 KiB for the transaction. Buffered host subprocess checks are not streaming
peak-memory limits. A timeout cannot interrupt a stalled kernel, guarantee the
remote shell ended or guarantee its trap ran. Partial timeout bytes and a private
`INCOMPLETE.json` are retained; there is no automatic retry. SIGKILL, power loss
and kernel failure can leave cleanup unconfirmed. Establish the original job's
state and inspect its saved namespace before any separate cleanup; never clear
all `kprobe_events` or another instance as a shortcut.

Complete observation exits 3 (`HANDOFF_BLOCKED`); refused/incomplete capture
exits 2. The report explicitly records `instrumentation_mutation=true` and
`driver_control_performed=false`.

## Interpretation limits

Observed signed returns are preserved, including failures. No target events
become an explicit coverage gap, not evidence that a subsystem is idle. Probe
profile misses and ring-buffer losses remain explicit. In this pinned kernel,
`kprobe_profile` prints the nested kprobe's miss counter; it does not establish
complete kretprobe-instance coverage. Even zero reported misses and a successful
control cannot prove every target invocation returned or was recorded.

These records have no owner file/context/domain key or admission fence. TIDs,
caller locations, nearby timestamps and a later file-release zero cannot bind
two returns into one causal cleanup receipt. No cross-CPU latency or ordering
claim is made. Actual DSP/map/secure-assignment results, all outstanding work,
restart ownership and independently surviving recovery remain requirements for
the [checked shutdown adapter](QUEST2_CHECKED_SHUTDOWN.md#fastrpc-adapter-boundary).
All ownership, DMA, recovery and handoff proof flags remain false.

## Verification and current evidence

Sixteen focused host tests and all 511 Windows regression tests passed without
skips. Parser/transport fixtures exercise the real collector logic; four actual
Bash runs verify failed-setup cleanup and preservation of an existing namespace
on an ordinary filesystem, including acquisition/cleanup signal injection.
Those are not kernel probe executions. Generated
Bash syntax and Python syntax checks passed. Live observation is pending review.

The restored-root passive recovery capture completed against all 17 source pins.
It belongs to a different boot from the earlier unprivileged button-return
checkpoint. That earlier human health report is not attached to the new capture.
No supported retained pstore records were available, so no log-survival or
independent recovery-deadline claim follows.

## Pinned primary sources

- [Return-probe syntax, limits and profile](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/Documentation/trace/kprobetrace.rst).
- [Trace buffers and instances](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/Documentation/trace/ftrace.rst).
- [Profile miss-counter implementation](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/trace/trace_kprobe.c#L1036).
- [Instance teardown](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/trace/trace.c#L8034).
- [Control syscall](https://github.com/facebookincubator/oculus-linux-kernel/blob/6bde5fc06b252c97761bc0963dd13c1abcf377ab/kernel/sys.c#L896).
