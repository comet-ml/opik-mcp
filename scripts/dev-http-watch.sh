#!/usr/bin/env bash
set -euo pipefail

TSC_PID=""

cleanup() {
  if [[ -n "$TSC_PID" ]]; then
    kill "$TSC_PID" >/dev/null 2>&1 || true
    wait "$TSC_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

# Initial build before starting watchers.
tsc

# Keep TypeScript compiling incrementally as files change.
tsc --watch --preserveWatchOutput &
TSC_PID=$!

# Restart the server when emitted JS under build/ changes.
node --watch build/cli.js serve --transport streamable-http
