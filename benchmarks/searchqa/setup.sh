#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/benchmarks/searchqa-harbor"
TRAIN_LIMIT=400
VAL_LIMIT=200

echo "=== SearchQA Harbor Task Setup ==="
echo ""
echo "This script downloads the SearchQA dataset from HuggingFace"
echo "and generates Harbor-compatible task directories."
echo ""

# Generate train split
echo "--- Generating train split ($TRAIN_LIMIT tasks) ---"
python -m benchmarks.searchqa.generate_harbor_tasks \
    --download \
    --split train \
    --limit "$TRAIN_LIMIT" \
    --output "$OUTPUT_DIR/train"

echo ""

# Generate val split
echo "--- Generating val split ($VAL_LIMIT tasks) ---"
python -m benchmarks.searchqa.generate_harbor_tasks \
    --download \
    --split val \
    --limit "$VAL_LIMIT" \
    --output "$OUTPUT_DIR/val"

echo ""
echo "=== Setup Complete ==="

TRAIN_COUNT=$(find "$OUTPUT_DIR/train" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)
VAL_COUNT=$(find "$OUTPUT_DIR/val" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l)

echo "  Train tasks: $TRAIN_COUNT (in $OUTPUT_DIR/train/)"
echo "  Val tasks:   $VAL_COUNT (in $OUTPUT_DIR/val/)"
echo ""
echo "The searchqa-harbor directory is in .gitignore and will not be committed."
