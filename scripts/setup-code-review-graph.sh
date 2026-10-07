#!/usr/bin/env bash
# Reproduce the code-review-graph (CRG) setup on a fresh clone of this repo.
#
#   1) installs the CLI via uv (skip if code-review-graph is already on PATH)
#   2) builds the local structural graph -> .code-review-graph/graph.db (git-ignored)
#
# Optional per-machine agent wiring (MCP config, AGENTS.md instructions, skills, hooks):
#   code-review-graph install --platform hermes -y
#
# Requires: uv (https://docs.astral.sh/uv/). On Windows, run from Git Bash;
# or run the two real commands manually in PowerShell.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v code-review-graph >/dev/null 2>&1; then
  uv tool install --python 3.12 'code-review-graph[communities,enrichment]'
fi

code-review-graph build

echo "OK - graph ready at .code-review-graph/graph.db (git-ignored)."
echo "Agent wiring on this machine: code-review-graph install --platform hermes -y"
