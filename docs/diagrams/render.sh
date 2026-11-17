#!/usr/bin/env bash

# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

# Renders every Graphviz source in this directory to PNG and SVG beside it.
set -euo pipefail
cd "$(dirname "$0")"
for src in *.dot; do
  dot -Tpng -Gdpi=110 "$src" -o "${src%.dot}.png"
  dot -Tsvg "$src" | awk 'NR == 1 { print; print "<!-- Copyright OpenSearch Contributors"; print "     SPDX-License-Identifier: Apache-2.0 -->"; next } 1' > "${src%.dot}.svg.tmp"
  mv "${src%.dot}.svg.tmp" "${src%.dot}.svg"
done
