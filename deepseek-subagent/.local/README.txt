Create opencode-go.key in this directory and put the OpenCode Go API Key on exactly one line.
The runtime reads no other upstream credential source.

local-bridge-token.txt and local-bridge-token-state.json are generated locally.
They are shared by the bridge and Codex auth.command and are preserved during updates.

Never commit, package, hash, log, or synchronize the three non-example local files.
