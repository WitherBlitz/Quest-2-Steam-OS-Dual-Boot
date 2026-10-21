# Repository migration

This standalone public repository continues the original work by
[machachu56](https://github.com/machachu56) in
[quest-kexec-q2](https://github.com/machachu56/quest-kexec-q2).
The upstream project and its ownership are unchanged. Original author history,
copyright notices and GPL-2.0 licensing are retained.

The former personal fork was `WitherBlitz/quest-kexec-q2`. Its three branch
tips are preserved in this repository:

| Former branch | Preserved branch | Exact original tip |
| --- | --- | --- |
| `main` | `legacy-main` | `e61edbd7fbdf6dc4976a979c61cc545e04738422` |
| `quest2-port` | `quest2-port` | `9c96c192d4a4a80f95ba0013294977e85c6eae11` |
| `quest2-52242990024200150-loader-safety` | `quest2-52242990024200150-loader-safety` | `b6cb5baf185a4877bdae32063c14853d36195f78` |

The new `main` branch continues from the latest firmware-specific work rather
than the older `main` tip. No old history was rewritten. The old fork had no
tags, releases, pull requests, issues, initialized wiki, repository variables,
environments, webhooks, deploy keys or rulesets to migrate.

GitHub does not transfer Actions run IDs/history into a different repository.
The [archived build evidence release](https://github.com/WitherBlitz/Quest-2-Steam-OS-Port/releases/tag/old-fork-build-evidence)
therefore preserves the five original run records and logs, all five available
artifact ZIPs with their original digests, a complete Git history bundle, and a
checksum inventory. Run `39557198852` was cancelled; its artifact is partial and
must not be treated as a successful build. The four successful original runs
are `39551869780`, `39557599099`, `39562556932` and `39567877831`.

Historical documentation links now point to that evidence archive, preserving
the original run numbers and source commits. New Actions runs belong to this
repository. The workflow's repository guard is updated and the private,
hash-pinned build configuration is provisioned as an Actions secret, not as a
public source or release asset.

The former fork is removed only after the branch histories, release assets,
README credit and a fresh native build in this repository have been verified.
This migration does not change loader behavior or establish hardware readiness.
