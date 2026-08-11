# Project handoff log

Use this reference for continuity-log naming, turn verification, successors, and failure handling.

## Identity and location

Use this deterministic identity tuple:

- Absolute project root: select the workspace root that must identify the log. It remains a required identity input even though logs are no longer stored inside it.
- Stable role: select a durable functional name, such as `arq-rx-reviewer`; do not depend on the generated service nickname.
- Scope: keep one normalized project/component description across successors.

Store the file under the installed Skill's canonical machine-local store:

```text
<installed-skill-root>/.local/handoffs/<stable-role-slug>--<12-char-fingerprint>.md
```

The 12-char fingerprint is SHA-256 over the normalized absolute project root (expanded, resolved, and on Windows casefolded with native separators) plus the normalized stable role and scope. It is collision-resistant across projects: the same role and scope in different projects always resolve to different files, while a successor with the same project root, role, and scope continues the same file. It is not a credential fingerprint. Preserve a compatible existing file byte-for-byte during initialization. Fail with `handoff_file_conflict` if the deterministic path contains an unrelated file.

## Legacy project-local logs

Versions before 1.6.3 wrote logs under `<project-root>/.deepseek-subagent/handoffs`. Those files are preserved byte-for-byte. `handoff-init` detects the legacy deterministic path and reports `legacy_handoff_detected`, `legacy_handoff_path`, `legacy_handoff_verified`, and `legacy_handoff_migrated=false`; it never copies, rewrites, or merges legacy content in this release. New and successor logs use only the canonical `.local/handoffs` store.

## First and later turns

For the first turn:

1. Run `prepare --json` and confirm `safe_to_spawn_send=true`.
2. Run `agents handoff-init` with the stable role, scope, and absolute project root.
3. Put `handoff_file` and `required_marker` into the initial spawn assignment.
4. Spawn the DeepSeek child and immediately register its returned id with the same role, scope, and project root.
5. Wait for the fresh reply.
6. Run `agents handoff-check` with the issued token, baseline size, and `baseline_sha256`.
7. Inspect the newest entry, then accept the result only when the check passes and the record is useful.

For every later turn, replace step 2 with `agents handoff-start --agent-id <id> --json`, then repeat assignment, reply, check, and content review.

## Required child record

Append one record; never rewrite prior records. Include the exact supplied marker and this content:

```markdown
### <UTC timestamp> — <short task title>
<!-- deepseek-subagent-turn token=<token supplied by parent> -->
- Request and prior context:
- Work performed and rationale:
- Evidence and artifact paths:
- Decisions and result:
- Open risks and next step:
```

Keep the rationale concise and outcome-oriented. Do not request or store private chain-of-thought. Do not store raw prompts, credentials, bridge tokens, full source files, or unbounded tool output. Link to canonical project artifacts instead of duplicating them.

Treat `handoff_update_verified` as proof that the complete byte prefix captured by `baseline_size` and `baseline_sha256` is unchanged and that the expected token marker was added after it. Do not treat it as proof that the prose is accurate or sufficient; review the newest record. Treat `handoff_history_modified` as an integrity failure: do not accept a larger file that rewrote, removed, or fabricated prior records.

## Successors after a restart

Treat full Codex exit, Windows restart, `shutdown`, and native `not_found` as terminal for the old child's operability. Do not call `resume_agent`.

When the user authorizes a new child:

1. Reuse the exact stable role, scope, and project root.
2. Run `handoff-init`; expect `created=false` and the existing file.
3. Put the file and new marker in the successor's first assignment.
4. Require the successor to read the log before working and to distinguish verified baseline facts from unresolved notes.
5. As soon as spawn returns the successor id, run `agents successor-register` with that id, `--previous-agent-id <old-id>`, and the exact same continuity identity. This atomically marks the old roster entry `superseded`, makes the new entry the sole `open` owner, and increments `handoff_generation`.
6. Verify the successor's first appended record with both baseline fields and inspect its content.

Call this “successor continuity from a durable handoff.” Never call it recovery of the old Agent, live context, or hidden model state. A different generated nickname is harmless.

## Ownership and failures

Do not run concurrent turns against one handoff file. Roster schema v3 enforces one global `open` owner for an actual deterministic handoff path, not merely one owner per root. Normal registration returns `handoff_active_owner_conflict` for the same root and `handoff_owned_by_other_parent` across roots. Only the user-authorized `successor-register` action may transfer that lease; it does not close or restore either native Agent.

Treat schema v1/v2 roster entries without `handoff_file` as `legacy_unconfigured`. Do not invent a log path from nickname or cwd. Initialize a project log with an explicit project root, then bind it through a new registration or authorized successor workflow.

If `handoff-check` reports `handoff_update_missing` or `handoff_turn_marker_missing`, do not accept the child result. Issue one correction turn with a new token, require the child to append both missing and correction markers in a complete record, and verify both. `handoff_history_modified` is stricter: preserve the evidence and stop instead of treating the rewritten file as an append. If the child or filesystem cannot complete the append, report the earliest original error; do not create or close an Agent automatically.
