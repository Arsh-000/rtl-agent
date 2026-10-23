#!/usr/bin/env bash
# Starts the rtl-agent web interface inside the Docker image.
#
#   cd ~/rtl-agent && bash scripts/docker-web.sh        then open http://localhost:8501
#
# What it does:
#   - runs the web server in the container and makes it reachable at http://localhost:8501
#     on THIS computer only (other computers on your network cannot open it),
#   - saves every run in ./webruns in the current folder, with YOUR user id (not root's),
#   - passes GROQ_API_KEY from your shell if it is set; otherwise paste the key in the page.
#     The key is never written to disk.
# Stop it with Ctrl+C. Use another port with: PORT=8600 bash scripts/docker-web.sh
set -euo pipefail
IMAGE=${RTL_AGENT_IMAGE:-rtl-agent}
PORT=${PORT:-8501}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not available in this terminal." >&2
  echo "  Windows: start Docker Desktop, then Settings > Resources > WSL integration > turn on your" >&2
  echo "  Ubuntu > Apply & restart, and open a new terminal. Check with: docker --version" >&2
  echo "  (Without Docker: install the tools yourself, README Option B, then: python3 -m rtl_agent web)" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Docker image '$IMAGE' not found. Build it first (once, 20-40 minutes):" >&2
  echo "    cd ~/rtl-agent && docker build -t rtl-agent ." >&2
  exit 1
fi

mkdir -p webruns
echo "Open http://localhost:$PORT in your browser. Runs are saved in $PWD/webruns. Ctrl+C to stop."

TTY=()
[ -t 0 ] && [ -t 1 ] && TTY=(-it)

exec docker run --rm --init "${TTY[@]}" \
  --user "$(id -u):$(id -g)" \
  -e GROQ_API_KEY -e OPENAI_API_KEY \
  -p "127.0.0.1:$PORT:8501" \
  -v "$PWD":/work -w /work \
  "$IMAGE" python3 -m rtl_agent web --host 0.0.0.0 --port 8501 --workdir /work/webruns
