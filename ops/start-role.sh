#!/usr/bin/env bash
# Opens one role's agent in THIS terminal, with only that role's access.
#   ops/start-role.sh oncall|developer|capacity|support|data
# Reads (never prints) the acme-shop-ops GitHub token from ~/guidal-demo/.gh-ops-token.
set -euo pipefail
ROLE="${1:?usage: ops/start-role.sh oncall|developer|capacity|support|data}"
cd "$(dirname "$0")/.."
DEMO="$HOME/guidal-demo"
export GH_TOKEN="$(cat "$DEMO/.gh-ops-token")"
export OPS_REPO="$(cat "$DEMO/.ops-repo")" SHOP_URL="$(cat "$DEMO/.shop-url")" TAKE="$(cat "$DEMO/.take" 2>/dev/null || echo 1)"
export GUIDAL_ORG_ID="$(cat "$DEMO/.org-id")" GUIDAL_PROJECT_ID="$(cat "$DEMO/.project-id")" GUIDAL_BIN="${GUIDAL_BIN:-$HOME/.venvs/guidal-staging-demo/bin/guidal}"
unset GUIDAL_AGENT CLAUDECODE CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_ENTRYPOINT
# No SSH keys for agents: they never need a server, only Guidal and the ops repo.
unset SSH_AUTH_SOCK SSH_AGENT_PID
# Claude Code's sandbox blocks the macOS certificate check, so gh fails with
# "x509: OSStatus -26276" (CLAUDE.md, claude-safe). Use the prepared CA bundle.
[ -f "$HOME/.config/guidal/agent-ca-bundle.pem" ] && export SSL_CERT_FILE="$HOME/.config/guidal/agent-ca-bundle.pem"
# Wait up to 9 min per call: fewer polls, fewer tokens, agents less likely to give up.
export NEXT_TICKET_WAIT=540
# gh: no update check, no usage reporting (they call hosts the demo does not need).
export GH_NO_UPDATE_NOTIFIER=1 GH_TELEMETRY=false DO_NOT_TRACK=1
CARD="ops/roles/$ROLE.md"
[ -f "$CARD" ] || { echo "no role card $CARD"; exit 2; }

if [ "$ROLE" = oncall ]; then
  FIRST="/loop 1m Follow $CARD exactly for one round."
else
  FIRST="Read $CARD and follow it. Start now: run ops/next-ticket $ROLE. Keep going until I stop you: whenever it says no ticket yet, run it again at once, and never end your turn while waiting."
fi
case "$ROLE" in
  oncall) TITLE="On-call · Claude Code" ;; developer) TITLE="Developer · Cursor" ;;
  capacity) TITLE="Capacity · Codex" ;; support) TITLE="Support · Claude Code" ;; data) TITLE="Data · Claude Code" ;;
esac
printf '\033]0;%s\007' "$TITLE"  # terminal tab/pane title, so the role shows on camera
export CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1  # keep that title (Claude Code would replace it)
printf '\n=== %s ===\nFirst message to type (or paste):\n  %s\n\n' "$ROLE" "$FIRST"

case "$ROLE" in
  oncall|support|data)
    # Claude Code with the guidal MCP only (MCP config in .mcp.json); the CLI is not on PATH
    # and is denied in the settings, so these roles can use nothing but the agent tools.
    exec claude --settings "ops/claude/$ROLE.json" ;;
  developer)
    export PATH="$PWD/ops/bin:$PATH" GUIDAL_AGENT=cursor
    exec agent ;;
  capacity)
    export PATH="$PWD/ops/bin:$PATH" GUIDAL_AGENT=codex
    # CLI only: the guidal MCP is switched off for this session.
    exec codex --sandbox workspace-write --ask-for-approval on-request -c 'mcp_servers.guidal.enabled=false' -c 'sandbox_workspace_write.network_access=true' ;;
esac
