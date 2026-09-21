#!/bin/bash
# Deploy the Content Agents Joint Agent (research preview) from WSL2 on host port 8400.
# Usage (from Windows):  wsl -d Ubuntu -- bash -l ./deploy_joint_agent_wsl.sh [backend] [vlm_model] [llm_model]
# Uses the provider keys preflight mirrored into the content-agents checkout's .env
# (run deploy_content_agents_wsl.sh first).
set -eu
export JA_VLM_BACKEND=${1:-openai}
export JA_VLM_MODEL=${2:-gpt-4.1-mini}      # per-part predictions (high volume)
export JA_LLM_BACKEND=$JA_VLM_BACKEND
export JA_LLM_MODEL=${3:-gpt-4.1}            # structure analysis (single call)
export JA_VLM_MAX_WORKERS=${JA_VLM_MAX_WORKERS:-4}
HERE=$(cd "$(dirname "$0")" && pwd)
export CAD2SIMREADY_DEPLOY_DIR="$HERE/deploy"
export JA_ROBOT_ID=${JA_ROBOT_ID:-}   # e.g. kuka_kr270_r2700_ultra; empty = match by asset/filename
UP=${CONTENT_AGENTS_UPSTREAM_ROOT:-$HOME/.omniverse-cad-to-simready/upstreams/content-agents}
[ -f "$UP/.env" ] || { echo "missing $UP/.env - run deploy_content_agents_wsl.sh first"; exit 1; }

mkdir -p ~/.docker-nocreds
[ -f ~/.docker-nocreds/config.json ] || echo "{}" > ~/.docker-nocreds/config.json
[ -d ~/.docker/cli-plugins ] && ln -sfn ~/.docker/cli-plugins ~/.docker-nocreds/cli-plugins
export DOCKER_CONFIG=~/.docker-nocreds

cd "$UP"
docker compose --env-file .env -p joint-agent \
  -f apps/joint_agent_service/docker-compose.yml \
  -f "$HERE/deploy/joint-agent.override.yml" up -d --build
for i in $(seq 1 60); do
  curl -fsS http://localhost:${JA_HOST_PORT:-8400}/health >/dev/null 2>&1 && break
  sleep 5
done
curl -fsS http://localhost:${JA_HOST_PORT:-8400}/health; echo
docker logs joint-agent-service 2>&1 | grep -m2 -iE "VLM|backend"
