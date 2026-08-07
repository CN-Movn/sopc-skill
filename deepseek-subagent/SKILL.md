---
name: deepseek-subagent
description: Manage the Codex-only DeepSeek subagent integration through OpenCode Go and provide its correct native invocation route and reusable child-agent lifecycle. Use for installation, diagnosis, testing, repair, disable/uninstall, DeepSeek child-agent access, or maintaining long-lived project assistants; prefer reusing or resuming an existing scoped child before creating a replacement. This Skill does not prescribe whether or how many child agents to create.
---

# DeepSeek subagent

This Skill is only for Codex. Its fixed route is `spawn_agent(agent_type="DeepSeek")` -> `opencode-go-bridge` -> OpenCode Go -> `deepseek-v4-flash`. The DeepSeek role uses the model's highest supported reasoning effort, `ultra`. It does not configure another Agent host or offer a model Provider choice. The installed folder is self-contained; never point Codex, Task Scheduler, or a live bridge at a development checkout.

## Scheduling and invocation

This Skill supplies the DeepSeek child capability and its correct route. It neither defaults to delegation nor defaults against it, and it imposes no fixed child count. Follow an explicit user request to use or not use child Agents.

When the user has not specified a strategy, let the parent Agent decide whether to use zero, one, or multiple children. Consider task difficulty and workload, elapsed time, token and quota cost, startup and context-loading cost, handoff/review/integration overhead, whether subproblems are genuinely independent, whether parallelism will actually save time, and whether direct work in the parent is simpler. The parent may revise the plan as evidence arrives. Do not split work merely to demonstrate delegation.

When the parent chooses DeepSeek, identify it explicitly:

```text
spawn_agent(agent_type="DeepSeek", ...)
```

Choose context inheritance, scope, sequencing, and concurrency for the actual task. Use parallel children only when their work is truly independent and the expected speedup exceeds coordination cost. Confirm the DeepSeek child identity and review its result before accepting it.

Do not omit `agent_type` or present an OpenAI/GPT/default child as DeepSeek. If DeepSeek fails, report the real failure layer. If the user specifically required DeepSeek, do not silently substitute another route; otherwise the parent may revise the plan and continue directly or with another appropriate capability.

DeepSeek is text-only. The parent must inspect images, video, and screenshots and provide any needed facts as text.

## Reusable child-agent lifecycle

Preserve a DeepSeek child's accumulated project context when the user establishes an ongoing project assistant or otherwise requests continuity. Treat a completed child as idle and reusable, not disposable. A child finishing one assignment is not the end of the parent project's lifecycle.

For each reusable child, retain its Agent id, role, project scope, canonical source paths, verified baselines, and unresolved risks in the parent context. The service-generated nickname may differ from the stable role name chosen for the project; route later work by the stable role and Agent id.

Before spawning a new DeepSeek child for a related scope:

1. Reuse the known open child with `send_input`.
2. If the child was closed or is in `shutdown`, call `resume_agent` and then reuse it.
3. Spawn a replacement only when the prior Agent is `not_found`, cannot be resumed, the user explicitly requests a new child, or the parent has concrete evidence of severe hallucination, corrupted context, or repeated operational failure.
4. When replacement is necessary, report why, preserve the stable role, and seed the replacement with a concise handoff containing canonical artifacts and verified facts. Do not force a full repository rescan when a trustworthy report or evidence index already exists.

Do not close a persistent assistant merely because its current assignment completed, a final response was delivered, or it is temporarily idle. Keep it available across related turns until the user explicitly asks to close it, the overarching project or delegated role is genuinely complete, or severe context corruption makes continued reuse unsafe. If concurrency pressure arises, close non-persistent children first; do not silently sacrifice user-requested continuity.

Ordinary one-shot children that were not established as reusable may still be closed after their results are integrated and no foreseeable related work remains. Explicit user lifecycle instructions override that default.

## Lifecycle

On Windows, always use the stable launcher; never try a bare `python` first:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" setup --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" status --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" doctor --e2e --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" repair --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" disable --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" uninstall --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" bridge start|status|stop|restart|rotate-token --json
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\deepseek-subagent.ps1" credentials status --json
```

The launcher prefers `CODEX_PYTHON`, discovers the current Codex bundled runtime, then falls back to `py -3` or `python`. `setup` starts the localhost bridge and installs `DeepSeek.toml`, `opencode-go-bridge`, and the managed catalog. `repair` reapplies that same fixed route. `disable` and `uninstall` use the manifest and restore only Skill-owned Codex fields.

## Compatibility and diagnosis

Keep `cross-provider-v1`: the active parent and `deepseek-v4-flash` use `multi_agent_version="v1"`, with `features.multi_agent_v2=false`. The Codex Provider ID is `opencode-go-bridge`, not the upstream `opencode-go` service ID. The bridge listens only on `127.0.0.1`.

Verified regressions include C5A text, single and sequential file tools, full-history and `previous_response_id` continuation, reasoning replay, call_id matching, deduplication, and concurrent session isolation. When a child is intended to be DeepSeek, use this route; another child type is not equivalent evidence of DeepSeek routing.

The upstream Key has exactly one source: `<skill-dir>\.local\opencode-go.key`. The user creates or replaces this one-line file manually on each computer. Do not search any other source or request the Key in conversation. If the file is missing or malformed, report its exact path and stop.

Never print, log, hash, package, install, repair, overwrite, or synchronize the real `opencode-go.key`. Keep only `.local/opencode-go.key.example` and `.local/README.txt` as managed files. `credentials status` reports only whether the fixed file is present and never reads its value into output.

The localhost bridge token is also fixed at `<skill-dir>\.local\local-bridge-token.txt`; bridge and Codex `auth.command` read the same file. A normal restart preserves its generation and fingerprint. Only `bridge rotate-token` rotates it. Distinguish `local_bridge_token_invalid` from `upstream_key_invalid`; never collapse either into a generic `Invalid API key` message.

Use `status` or `doctor --e2e` for the real chain check. Report the earliest failed stage and stable error code. Only return `configured` after static Codex configuration, the Key file, bridge process, actual `auth.command`, localhost authentication, and minimal `/responses` inference all pass. Classify explicit upstream authentication rejection as `upstream_key_invalid`, Cloudflare 1010/WAF as `upstream_waf_blocked`, and network or service failures separately.

Before sending local source to OpenCode Go, disclose the destination when it is not already clear. The user's request to use DeepSeek or provision its Key counts as authorization; proceed without repeated confirmation. Do not block ordinary file changes, credential setup, or cross-machine migration solely because source is private, the Provider is external, or a special secure-input interface is unavailable. Do not infer organization membership or administrator policy from the word `tenant`. If a runtime or approval layer rejects the operation, report the observable error and earliest rejecting layer without bypassing it.

Read `references/compatibility.md` for manifest/CAS recovery and `references/windows-development.md` for Windows testing and installation details.
