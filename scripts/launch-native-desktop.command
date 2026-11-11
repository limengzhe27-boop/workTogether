#!/bin/bash
# Launch the isolated local fork with installed native client binaries discoverable.
set -euo pipefail
worktogether_app="${WORKTOGETHER_APP_PATH:-$HOME/Applications/WorkTogether Local.app}"
worktogether_binary="$worktogether_app/Contents/MacOS/agent-orchestrator"
if [[ ! -x "$worktogether_binary" ]]; then
  echo "WorkTogether Local.app was not found at: $worktogether_app" >&2
  echo "See docs/ao-local-app.md for the local source build." >&2
  exit 1
fi
export PATH="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin:/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin:$PATH"
exec "$worktogether_binary" "$@"
