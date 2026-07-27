#!/usr/bin/env bash
set -euo pipefail

# check-dark-mode.sh
# -------------------
# Ensures every light-mode color class in changed .tsx files has a
# corresponding dark: variant on the same element (same className string).
#
# Catches orphaned light-only color classes like:
#   className="bg-blue-500 text-white"   <- missing dark:bg-* and dark:text-*
#
# Checks both design-token classes (bg-*, text-*, border-*) and hardcoded
# bracket-notation hex (bg-[#...], text-[#...], border-[#...]).
#
# This check is project-agnostic and does not require design-baseline.json.
#
# Exit 0 = pass, Exit 1 = fail
# Use --score to get JSON output for eval integration.

SCORE_MODE=false
if [[ "${1:-}" == "--score" ]]; then
  SCORE_MODE=true
fi

# --- Gather changed .tsx files ---
CHANGED_FILES=$(git diff --name-only HEAD~1 2>/dev/null || true)
CHANGED_TSX=$(echo "$CHANGED_FILES" | grep -E '\.tsx$' || true)

if [[ -z "$CHANGED_TSX" ]]; then
  if $SCORE_MODE; then
    echo '{"score": 1.0, "details": "No changed .tsx files to check."}'
  else
    echo "PASS: No changed .tsx files to check."
  fi
  exit 0
fi

VIOLATIONS=""
VIOLATION_COUNT=0
TOTAL_COLOR_CLASSES=0
PAIRED_COUNT=0

# Color class prefixes we care about
COLOR_PREFIXES="bg|text|border"

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  [[ ! -f "$file" ]] && continue

  LINE_NUM=0
  while IFS= read -r line; do
    LINE_NUM=$((LINE_NUM + 1))

    # Extract className string values from the line
    # Handles className="..." and className={'...'} and className={`...`}
    CLASS_STRINGS=$(echo "$line" | grep -oE 'className=["{'\''`][^"'\''`]*["'\''`]' 2>/dev/null || true)

    [[ -z "$CLASS_STRINGS" ]] && continue

    while IFS= read -r class_attr; do
      [[ -z "$class_attr" ]] && continue

      # Extract just the class string value
      CLASS_VALUE=$(echo "$class_attr" | sed -E 's/className=["{'\''`]//;s/["'\''`]$//')

      # Find light-mode color classes:
      # 1. Bracket hex: bg-[#...], text-[#...], border-[#...]
      # 2. Common semantic: bg-white, bg-black, text-white, text-black
      # 3. Any color scale: bg-gray-*, text-blue-*, border-red-*, etc.
      # 4. Custom token classes: bg-*, text-*, border-* with non-numeric suffixes

      LIGHT_CLASSES=$(echo "$CLASS_VALUE" | tr ' ' '\n' | grep -E "^(${COLOR_PREFIXES})-(\[#|white|black|slate-|gray-|zinc-|neutral-|stone-|red-|orange-|amber-|yellow-|lime-|green-|emerald-|teal-|cyan-|sky-|blue-|indigo-|violet-|purple-|fuchsia-|pink-|rose-)" 2>/dev/null | grep -v '^dark:' || true)

      [[ -z "$LIGHT_CLASSES" ]] && continue

      while IFS= read -r light_class; do
        [[ -z "$light_class" ]] && continue
        TOTAL_COLOR_CLASSES=$((TOTAL_COLOR_CLASSES + 1))

        # Determine the prefix (bg, text, border)
        PREFIX=$(echo "$light_class" | sed -E 's/^(bg|text|border)-.*/\1/')

        # Check if a dark: variant with the same prefix exists in this className
        if echo "$CLASS_VALUE" | grep -qE "dark:${PREFIX}-"; then
          PAIRED_COUNT=$((PAIRED_COUNT + 1))
        else
          VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
          VIOLATIONS="${VIOLATIONS}  ${file}:${LINE_NUM}  \"${light_class}\" has no dark:${PREFIX}-* counterpart\n"
        fi
      done <<< "$LIGHT_CLASSES"
    done <<< "$CLASS_STRINGS"
  done < "$file"
done <<< "$CHANGED_TSX"

# --- Output ---
if $SCORE_MODE; then
  if [[ $TOTAL_COLOR_CLASSES -eq 0 ]]; then
    SCORE="1.0"
  else
    SCORE=$(awk "BEGIN { printf \"%.2f\", $PAIRED_COUNT / $TOTAL_COLOR_CLASSES }")
  fi
  DETAILS="Found ${PAIRED_COUNT}/${TOTAL_COLOR_CLASSES} color class(es) with dark: pair. ${VIOLATION_COUNT} orphaned."
  echo "{\"score\": ${SCORE}, \"details\": \"${DETAILS}\"}"
  if [[ $VIOLATION_COUNT -gt 0 ]]; then
    exit 1
  fi
  exit 0
fi

if [[ $VIOLATION_COUNT -gt 0 ]]; then
  echo "FAIL: ${VIOLATION_COUNT} light-mode color class(es) without dark: variant."
  echo ""
  echo "Violations:"
  echo -e "$VIOLATIONS"
  echo ""
  echo "Fix: Add a dark: variant for each color class. For example:"
  echo "  Before: className=\"bg-white text-gray-900\""
  echo "  After:  className=\"bg-white dark:bg-gray-900 text-gray-900 dark:text-white\""
  echo ""
  echo "Or use semantic design tokens that already handle both modes."
  exit 1
else
  if [[ $TOTAL_COLOR_CLASSES -eq 0 ]]; then
    echo "PASS: No color classes found in changed files."
  else
    echo "PASS: All ${TOTAL_COLOR_CLASSES} color class(es) have dark: variants."
  fi
  exit 0
fi
