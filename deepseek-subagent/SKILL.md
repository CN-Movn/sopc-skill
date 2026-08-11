---
name: deepseek-subagent
description: Manage the Codex-only DeepSeek subagent integration through OpenCode Go: correct native current-process route, per-turn verified continuity log, and user-controlled child lifecycle. Use for installation, prepare/status/repair, DeepSeek child access, bridge recovery, handoff and successor continuity after Codex restarts, or disable/uninstall. Cross-Codex/Windows-restart recovery of an existing child is unsupported.
---

# DeepSeek subagent

This Skill is only for Codex. Fixed route: `spawn_agent(agent_type="DeepSeek")` -> `opencode-go-bridge` (localhost) -> OpenCode Go -> `deepseek-v4-flash` with the highest reasoning effort, `ultra`. The installed folder is self-contained; never point Codex or a live bridge at a development checkout.

## Scheduling and invocation

This Skill neither defaults to delegation nor defaults against it. Follow an explicit user request to use or not use child Agents. When the user has not specified a strategy, the parent decides between zero, one, or multiple children based on workload, quota cost, and coordination overhead. Do not split work merely to demonstrate delegation.

Identify the DeepSeek child explicitly:

```text
spawn_agent(agent_type="DeepSeek", ...)
```

DeepSeek is text-only: the parent inspects images, video, and screenshots and passes any needed facts as text. Do not present an OpenAI/GPT/default child as DeepSeek; if DeepSeek fails, report the real failure layer.

## Mandatory continuity log

Treat the handoff update as part of every child turn's definition of done. Use a stable role such as `arq-rx-reviewer`, never the service-generated nickname, as the continuity identity. Handoff logs are machine-local persistent user data stored under the installed Skill, never inside a project working directory:

```text
<skill-dir>\.local\handoffs\<stable-role>--<project-and-scope-fingerprint>.md
```

The deterministic identity binds the normalized absolute project root, the stable role, and the scope, so identical role and scope in different projects never share a log. A successor reuses the same role, scope, and project root to continue the same file. `.local\handoffs` is preserved across upgrades and syncs; never delete, clear, or overwrite it. `handoff-init` reports any pre-1.6.3 project-local log as `legacy_handoff_detected` (`legacy_handoff_migrated=false`) and leaves it untouched.

### First operation in a Codex task

Run the automatic preconfiguration gate once per Codex task/root before the first DeepSeek operation:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" prepare --json
```

Proceed only when it returns `ready_for_deepseek=true` and `safe_to_spawn_send=true` with the `multi_agent_v1` namespace. Then:

1. Run `agents handoff-init --stable-role <role> --scope <scope> --project-root <absolute-project-root> --json`.
2. Put the returned `handoff_file` and exact `required_marker` into the child assignment. Require the child to read the file before work, complete the task, then append one compact record containing the exact marker before replying (request context, work and rationale, evidence paths, decisions/result, open risks; never chain-of-thought, raw prompts, credentials, or large dumps).
3. Spawn the child, then as soon as `spawn_agent` returns the id run `agents register --agent-id <id> --stable-role <role> --scope <scope> --project-root <absolute-project-root> --nickname <nickname> --json` with the same identity.
4. After the fresh reply arrives, verify the append with `agents handoff-check --agent-id <id> --turn-token <token> --after-size <baseline_size> --baseline-sha256 <baseline_sha256> --json`; accept the result only on `status=handoff_update_verified, updated=true` after reviewing the newest record.

### Later turns (same child, same Codex process)

Do not run `prepare` again while the task and bridge stay healthy. Per turn:

1. Run `agents handoff-start --agent-id <id> --json`; include its file and marker in the assignment.
2. `send_input(...)` to the child.
3. Run `agents handoff-check ...` as above before accepting the result.

Never assign concurrent turns to Agents sharing one handoff file.

### Failures

- Missing update or marker: do not accept the result; send one correction turn via `handoff-start` requiring both the missing prior marker and the correction marker, then verify both. Report the earliest file or native error if correction cannot complete.
- `handoff_history_modified`: fail closed; never accept the rewritten file.
- `handoff_owned_by_other_parent`: stop and report it to the user. Never work around the single-owner rule by changing the stable role, scope, project identity, or inventing a new continuity key. Only the user decides: keep the old owner, run the explicit successor flow, or authorize a genuinely new role/scope.
- A `send_input` failure pointing to bridge/transport/config (bridge unreachable, provider auth failure, transport mismatch): run `prepare` once, then let the user decide whether to retry. Never loop repairs or restarts.
- Native `shutdown`/`not_found`: report `cross_restart_child_recovery_unsupported` with the original error; do not call `resume_agent`, create a replacement, or close the old child without the user deciding.

## Current-process child lifecycle

Keep every DeepSeek child open by default while its Codex process and root task remain active. Treat a completed child as idle and reusable, not disposable. Codex's active child registry is process-local: a child can be reused with `send_input` only while its original id remains in the current native registry and belongs to the current root task. A complete Codex exit, Windows restart, `shutdown`, or `not_found` is a terminal operability boundary; durability is not recoverability. Do not call `resume_agent` for a DeepSeek child after that boundary, and do not claim that the original child or its execution context was restored.

Run `agents list --json` only when choosing or creating an Agent (first delegation in a task), when the Codex/root lifecycle changed (new task, restart, `not_found`), when preparing a successor, when ownership is ambiguous, when the user asks, or when diagnosing an error. A normal later turn against a known active child of the current task needs no `agents list`: `handoff-start` already resolves the roster by the current root and rejects unknown or foreign entries. `agents list --all-parents --json` is diagnostic only: an entry owned by another root is never sendable, retirable, or replaceable. A legacy entry without parent evidence stays non-operable unless its own native rollout authoritatively resolves the parent; never guess from cwd, scope, role, nickname, or roster proximity.

The roster is an ownership and diagnostic index only: `roster=open`, parent match, a known id, or surviving rollout data never prove current operability, liveness, or context preservation. `agents list` reports `cross_restart_child_recovery_supported=false` and exposes no recovery action. The handoff log carries explicit continuity facts; the roster only points to it. The roster lives at `<skill-dir>\.local\agents.json` with the same lifecycle as `.local\handoffs`: normal restarts and upgrades preserve both; deleting the whole installation removes both.

For later work:

1. Reuse a known active or completed child with `send_input` only when the current native Codex registry still recognizes that id and native rollout evidence binds it to the current root task.
2. Require a fresh reply to the new turn before describing the child as `live`. Describe `context preserved` only when that fresh reply states a concrete, independently checkable fact from its prior assignment. `roster=open`, a known id, rollout presence, or parent match is never sufficient evidence. Do not persist transient liveness claims in the roster.
3. If the native operation returns `shutdown` or `not_found`, report `cross_restart_child_recovery_unsupported` at the native child-registry layer together with the original error. Do not call `resume_agent`, loop bridge repair as if it could restore the child role, create a replacement, or close the old child.
4. If there is concrete evidence of severe hallucination, corrupted context, or repeated operational failure, stop routing new work to that child and recommend closing it and creating a replacement. Ask the user to decide; do not close or replace it before the user decides.
5. After the user approves replacement, preserve the stable role, scope, and project root; initialize the existing handoff; create the successor; and immediately use `agents successor-register --agent-id <new-id> --previous-agent-id <old-id> --stable-role <role> --scope <scope> --project-root <absolute-project-root> --json` to atomically supersede the old roster owner. Retire the old entry only after a separately authorized native close; roster supersession is not native closure. Require the successor to read the log before work. Do not force a full repository rescan when the handoff identifies trustworthy artifacts and verified facts. Describe this as successor continuity from a durable handoff, never as recovery of the original Agent or its hidden model context.

Never call `close_agent` unless the user explicitly asks to close that child, role, or an unambiguous set of children. Task completion, final-response delivery, temporary idleness, an apparently finished project, lack of foreseeable work, context pressure, concurrency pressure, process exit, and `not_found` do not authorize closure. If capacity pressure requires releasing a child, report the pressure and ask the user which child to close.

After an explicitly authorized close succeeds, run `agents retire --agent-id <id> --json`. Never retire an entry merely because Codex restarted or a native operation returned `not_found`.

The user's explicit lifecycle instruction is authoritative.

## Bridge lifecycle

On Windows, always use the stable launcher; never try a bare `python` first:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" setup --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" status --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" doctor --e2e --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" repair --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" disable --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" uninstall --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" reinstall-prep --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" bridge start|status|stop|restart|rotate-token --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" credentials status --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" prepare --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" transport check --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents list --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents list --all-parents --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents handoff-init --stable-role <role> --scope <scope> --project-root <absolute-project-root> --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents register --agent-id <id> --stable-role <role> --scope <scope> --project-root <absolute-project-root> --nickname <nickname> --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents successor-register --agent-id <new-id> --previous-agent-id <old-id> --stable-role <role> --scope <scope> --project-root <absolute-project-root> --nickname <nickname> --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents handoff-start --agent-id <id> --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents handoff-check --agent-id <id> --turn-token <token> --after-size <baseline_size> --baseline-sha256 <baseline_sha256> --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" agents retire --agent-id <id> --json
```

The launcher prefers `CODEX_PYTHON`, discovers the current Codex bundled runtime, then falls back to `py -3` or `python`. `prepare` is the normal operation path: run it for the first DeepSeek operation of each task and again only when a send fails with a bridge/transport/config error. `setup` starts the localhost bridge and installs the managed role and catalog; `repair` force-reapplies the fixed route; `disable` and `uninstall` restore only Skill-owned Codex fields. `reinstall-prep` is for deleting the whole installation: it safely stops any verified managed bridge (authenticated shutdown only, fail-closed on unverified identity) and never deletes the Key, handoffs, roster, or user data. Normal version upgrades never require it. Bridge recovery never recovers an old process-local child registry.

For cross-computer installation, copy a clean source/release tree, never the active installed folder and never its `.local` directory. `.local` contains machine-bound credentials, ACL state, bridge PIDs and absolute project handoff paths. On the target computer, install the controlled tree, create that computer's Key file manually, run `prepare` once, and create a new Codex task for a project path that exists there. Do not move or delete the current task's workspace while Codex is using it.

## Compatibility and diagnosis

Keep `cross-provider-v1`: the configured parent and `deepseek-v4-flash` use `multi_agent_version="v1"` with `features.multi_agent=true` and `features.multi_agent_v2=false`. V1 is the only legal cross-provider transport; this Skill never decrypts `encrypted_content`. Never fall back to V2. Static config alone is not proof: the transport gate also verifies the current task's persisted `turn_context` and its actual parent model catalog entry. `prepare` cannot mutate a task already initialized as V2; in that case it prepares the same parent model for the next task and returns `configured_new_task_required` — create a new task. A newly created root task must not adopt children owned by the old root.

The upstream Key has exactly one source: `<skill-dir>\.local\opencode-go.key`. The user creates or replaces this one-line file manually on each computer; do not search another source or request it in conversation. Never print, log, hash, package, install, repair, overwrite, or synchronize the real Key. If the file is missing or malformed, report its exact path and stop. `credentials status` reports only whether the fixed file is present.

Use `status` or `doctor --e2e` for the real chain check; report the earliest failed stage and stable error code. Classify explicit upstream authentication rejection as `upstream_key_invalid`, Cloudflare 1010/WAF as `upstream_waf_blocked`, and network or service failures separately. ONLOGON task presence is not a readiness requirement.

Before sending local source to OpenCode Go, disclose the destination when it is not already clear. The user's request to use DeepSeek or provision its Key counts as authorization. If a runtime or approval layer rejects an operation, report the observable error and the earliest rejecting layer without bypassing it.

Read `references/compatibility.md` for runtime behavior details (prepare, bridge lifecycle, V1 gate, token/ACL, error classification), `references/handoff-log.md` for handoff details, and `references/windows-development.md` for Windows testing and installation details.
