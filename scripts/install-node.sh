#!/usr/bin/env bash
# Render's Python image does not include Node. The v3.1 signal engines are
# JavaScript, so the build unpacks a Linux Node binary into .node/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/.node"
if [[ -x "$DEST/bin/node" ]]; then
  "$DEST/bin/node" --version
  exit 0
fi
VER="v22.21.1"
TMP="$(mktemp)"
curl -fsSL "https://nodejs.org/dist/${VER}/node-${VER}-linux-x64.tar.gz" -o "$TMP"
mkdir -p "$DEST"
tar -xzf "$TMP" -C "$DEST" --strip-components=1
rm -f "$TMP"
"$DEST/bin/node" --version
