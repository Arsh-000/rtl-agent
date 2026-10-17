#!/usr/bin/env bash
# Runs rtl-agent inside the Docker image, on the files in the CURRENT folder.
#
#   cd ~/rtl-agent                      && scripts/docker-run.sh run examples/dot_product/config.yaml
#   cd ~/my_designs/moving_avg          && ~/rtl-agent/scripts/docker-run.sh run config.yaml
#   scripts/docker-run.sh check-tools
#
# What it does:
#   - the current folder is visible inside the container as /work (results are written there),
#   - files are created with YOUR user id, not root's, so you can edit/delete them normally,
#   - GROQ_API_KEY (and OPENAI_API_KEY, if set) is passed in from your shell; it is never stored.
set -euo pipefail
IMAGE=${RTL_AGENT_IMAGE:-rtl-agent}

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Docker image '$IMAGE' not found. Build it first (once, 20-40 minutes):" >&2
  echo "    cd ~/rtl-agent && docker build -t rtl-agent ." >&2
  exit 1
fi

TTY=()
[ -t 0 ] && [ -t 1 ] && TTY=(-it)

exec docker run --rm "${TTY[@]}" \
  --user "$(id -u):$(id -g)" \
  -e GROQ_API_KEY -e OPENAI_API_KEY \
  -v "$PWD":/work -w /work \
  "$IMAGE" python3 -m rtl_agent "$@"
