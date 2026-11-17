#!/usr/bin/env bash
# Renders every Graphviz source in this directory to PNG and SVG beside it.
set -euo pipefail
cd "$(dirname "$0")"
for src in *.dot; do
  dot -Tpng -Gdpi=110 "$src" -o "${src%.dot}.png"
  dot -Tsvg "$src" -o "${src%.dot}.svg"
done
