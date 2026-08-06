# Windows development and verification

Use the installed PowerShell launcher. It discovers Codex Python without changing `PATH` or copying the runtime.

```powershell
$skill = "$env:USERPROFILE\.codex\skills\deepseek-subagent"
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\deepseek-subagent.ps1" credentials status --json
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\deepseek-subagent.ps1" status --json
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\deepseek-subagent.ps1" doctor --e2e --json
powershell -NoProfile -ExecutionPolicy Bypass -File "$skill\scripts\deepseek-subagent.ps1" bridge status --json
```

Create `%USERPROFILE%\.codex\skills\deepseek-subagent\.local\opencode-go.key` manually and place the OpenCode Go Key on exactly one line. The runtime never searches another credential source and never writes this file. Setup, repair, update, and managed-file synchronization must preserve it.

The stable localhost token is `%USERPROFILE%\.codex\skills\deepseek-subagent\.local\local-bridge-token.txt`. Bridge and Codex `auth.command` use that same file. On first v1.4.0 start, an existing runtime token is migrated without changing its generation or fingerprint; normal restart never rotates it.

`status` and `doctor --e2e` validate the static configuration, Key-file presence, bridge process, actual `auth.command`, localhost token authentication, and a minimal `/responses` inference. Only the complete chain returns `configured`; failures include the earliest stage and a stable error code.

For repository tests, call the selected interpreter explicitly and force UTF-8:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
& $python -m unittest discover -s .\deepseek-subagent\tests -v
```

The runtime has one package tree at `deepseek-subagent/runtime/deepseek_subagent`. Codex lifecycle code lives in `platforms/codex`; OpenCode Go bridge code lives in `bridges/opencode_go`.
