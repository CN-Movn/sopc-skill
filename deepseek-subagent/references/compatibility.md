# Codex lifecycle compatibility

The only active route is Codex + `opencode-go-bridge` + OpenCode Go + `deepseek-v4-flash`.

## Managed files and state

- Codex config: `$CODEX_HOME/config.toml`
- Catalog: `$CODEX_HOME/models-with-deepseek.json`
- Role: `$CODEX_HOME/agents/DeepSeek.toml`
- State: `%LOCALAPPDATA%\deepseek-subagent\` on Windows, with `DEEPSEEK_SUBAGENT_STATE_HOME` available for isolated tests.

Schema v4 retains internal `platform="codex"` and `provider="opencode-go"` fields so existing transactions and backups remain readable. Older schemas and legacy state locations are read safely. Historical selection fields do not appear in normal status and do not choose a route; the next successful setup or repair writes a converged manifest.

## Ownership and recovery

The manager owns only its marked Provider block, `model_catalog_json`, `features.multi_agent_v2`, the managed catalog entry, and `DeepSeek.toml`. It records installed and previous values and restores fields with compare-and-swap semantics. Unrelated TOML content and comments are preserved.

`disable` is transactional and idempotent. `uninstall` first verifies the active or disabled hashes, runs a healthy disable, restores owned fields, then removes the managed manifest. Drift or a field conflict stops finalization and preserves recovery data.

For a partial uninstall, resolve the reported field conflict, then run:

```text
repair --json
disable --json
uninstall --json
```

Backups, old markers, old schema payloads, and the legacy state directory remain readable solely for safe rollback. They are not product choices.

## Protocol boundary

`cross-provider-v1`, Responses/SSE conversion, DSML promotion, reasoning replay, full-history replay, previous-response continuation, call_id correlation, deduplication, and SessionStore TTL/capacity/locking remain unchanged. v1.4.0 relocates the stable localhost token to the installed Skill's `.local` directory; migration preserves its generation and fingerprint, and restart still never rotates it. v1.4.1 raises the managed DeepSeek role's default reasoning effort from `high` to the highest supported level, `ultra`. v1.4.2 adds parent-side lifecycle guidance that preserves long-lived project assistants by preferring `send_input` and `resume_agent` over replacement. v1.4.3 makes every child persistent by default, reserves `close_agent` for explicit user decisions, and requires user approval before replacing a hallucinating, context-corrupted, or repeatedly failing child. It does not change the bridge protocol or managed Codex configuration.
