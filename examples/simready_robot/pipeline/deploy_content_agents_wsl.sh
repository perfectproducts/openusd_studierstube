#!/bin/bash
# Deploy Content Agents (Material + Physics) from WSL2 via the skill's preflight.
# OVRTX cannot run in Docker on WSL2 (no NVIDIA Vulkan/GL driver), so the renderer
# is provided: ovrtx_adapter.py on the Windows host, reached via host.docker.internal.
#
# Usage (from Windows):  wsl -d Ubuntu -- bash -l ./deploy_content_agents_wsl.sh [backend] [model]
#   backend: openai (default) | anthropic | gemini | nim     model: default gpt-5.5
# Provider keys are read from ~/.omniverse-cad-to-simready/secrets.env on the Windows side.
set -u
BACKEND=${1:-openai}
MODEL=${2:-gpt-5.5}
WINHOME=${WINHOME:-/mnt/c/Users/$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r')}
SKILL=$WINHOME/.claude/skills/omniverse-cad-to-simready/references/preflight/scripts/preflight.py
export RENDER_ENDPOINT=${RENDER_ENDPOINT:-http://host.docker.internal:8001}

# Docker Desktop's Windows credential helper is not always callable from WSL; public images need none.
mkdir -p ~/.docker-nocreds
[ -f ~/.docker-nocreds/config.json ] || echo "{}" > ~/.docker-nocreds/config.json
[ -d ~/.docker/cli-plugins ] && ln -sfn ~/.docker/cli-plugins ~/.docker-nocreds/cli-plugins
export DOCKER_CONFIG=~/.docker-nocreds

# Recreate agent containers so a backend/model change takes effect.
docker rm -f content-material-agent-service content-physics-agent-service >/dev/null 2>&1

ST=~/.omniverse-cad-to-simready/state
mkdir -p "$ST"
cd ~ && uv run --no-project --python 3.12 --with pyyaml python "$SKILL" \
  --targets content-agents \
  --content-agents-secret-env-file "$WINHOME/.omniverse-cad-to-simready/secrets.env" \
  --content-agents-vlm-backend "$BACKEND" --content-agents-vlm-model "$MODEL" \
  --content-agents-llm-backend "$BACKEND" --content-agents-llm-model "$MODEL" \
  --report "$ST/ca-preflight.json" --env-file "$ST/ca-preflight.env" \
  --markdown-report "$ST/ca-preflight.md" > "$ST/ca-preflight.log" 2>&1
rc=$?
sed -n '/Services/,$p' "$ST/ca-preflight.md"
docker logs content-material-agent-service 2>&1 | grep -m1 "VLM:"
exit $rc
