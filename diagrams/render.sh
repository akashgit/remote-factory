#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

D2_FLAGS=(
  --layout elk
  --theme 0
  --dark-theme 200
  --elk-nodeNodeBetweenLayers 30
  --elk-padding "[top=10,left=10,bottom=10,right=10]"
  --elk-edgeNodeBetweenLayers 20
)

for f in "$DIR"/*.d2; do
  name="$(basename "$f" .d2)"
  out="$DIR/${name}.svg"
  echo "Rendering $name..."
  d2 "${D2_FLAGS[@]}" "$f" "$out"
done

echo "Done. Rendered $(ls "$DIR"/*.d2 | wc -l | tr -d ' ') diagrams."
