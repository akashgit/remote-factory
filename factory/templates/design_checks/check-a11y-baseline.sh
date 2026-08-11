#!/usr/bin/env bash
set -euo pipefail

# check-a11y-baseline.sh
# ----------------------
# Baseline accessibility checks for changed .tsx files.
#
# This check is project-agnostic and does not require design-baseline.json.
#
# Catches:
#   a) Icon-only buttons without aria-label or sr-only text
#   b) <img> / <Image> tags without alt attribute
#   c) <svg> tags without aria-hidden or aria-label
#
# Exit 0 = pass, Exit 1 = fail
# Use --score to get JSON output for eval integration.

SCORE_MODE=false
if [[ "${1:-}" == "--score" ]]; then
  SCORE_MODE=true
fi

# --- Auto-detect source directory for full-scan mode ---
if [[ "${SCAN_MODE:-}" == "full" ]] && [[ -z "${SCAN_SRC_DIR:-}" ]]; then
  _BL=".factory/design-system/design-baseline.json"
  if [[ -f "$_BL" ]]; then
    SCAN_SRC_DIR=$(python3 -c "import json; print(json.load(open('$_BL')).get('source_root',''))" 2>/dev/null || true)
  fi
  if [[ -z "${SCAN_SRC_DIR:-}" ]]; then
    for _d in src app pages lib; do
      if [[ -d "$_d" ]]; then SCAN_SRC_DIR="$_d"; break; fi
    done
  fi
  SCAN_SRC_DIR="${SCAN_SRC_DIR:-.}"
fi

# --- Gather files to check ---
if [[ "${SCAN_MODE:-}" == "full" ]]; then
  CHANGED_TSX=$(find "$SCAN_SRC_DIR" -type f -name '*.tsx' 2>/dev/null | sort || true)
else
  CHANGED_FILES=$(git diff --name-only HEAD~1 2>/dev/null || true)
  CHANGED_TSX=$(echo "$CHANGED_FILES" | grep -E '\.tsx$' || true)
fi

if [[ -z "$CHANGED_TSX" ]]; then
  if [[ "${SCAN_MODE:-}" == "full" ]]; then
    _MSG="No .tsx files found in ${SCAN_SRC_DIR:-.}."
  else
    _MSG="No changed .tsx files to check."
  fi
  if $SCORE_MODE; then
    echo "{\"score\": 1.0, \"details\": \"${_MSG}\"}"
  else
    echo "PASS: ${_MSG}"
  fi
  exit 0
fi

VIOLATIONS=""
VIOLATION_COUNT=0
TOTAL_ELEMENTS=0

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  [[ ! -f "$file" ]] && continue

  CONTENT=$(cat "$file")
  LINE_NUM=0

  while IFS= read -r line; do
    LINE_NUM=$((LINE_NUM + 1))

    # -----------------------------------------------------------
    # Check (b): <img or <Image without alt attribute
    # -----------------------------------------------------------
    if echo "$line" | grep -qE '<(img|Image)([[:space:]]|/)'; then
      TOTAL_ELEMENTS=$((TOTAL_ELEMENTS + 1))
      # Check if alt is present on this line or within a reasonable multi-line span
      if ! echo "$line" | grep -qE '\balt\s*='; then
        # Could be multi-line; peek at next few lines from file
        CONTEXT=$(sed -n "${LINE_NUM},$((LINE_NUM + 5))p" "$file" | tr '\n' ' ')
        # Find the closing > or /> for this tag
        TAG_CONTENT=$(echo "$CONTEXT" | grep -oE '<(img|Image)[^>]*/?>|<(img|Image)[^>]*>' | head -1 || true)
        if [[ -n "$TAG_CONTENT" ]] && ! echo "$TAG_CONTENT" | grep -qE '\balt\s*='; then
          VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
          VIOLATIONS="${VIOLATIONS}  ${file}:${LINE_NUM}  <img>/<Image> missing alt attribute\n"
          VIOLATIONS="${VIOLATIONS}    Add alt=\"description\" or alt=\"\" for decorative images\n"
        fi
      fi
    fi

    # -----------------------------------------------------------
    # Check (c): <svg without aria-hidden or aria-label
    # -----------------------------------------------------------
    if echo "$line" | grep -qE '<svg([[:space:]]|>)'; then
      TOTAL_ELEMENTS=$((TOTAL_ELEMENTS + 1))
      # Check this line and a few following lines for the attributes
      CONTEXT=$(sed -n "${LINE_NUM},$((LINE_NUM + 3))p" "$file" | tr '\n' ' ')
      TAG_CONTENT=$(echo "$CONTEXT" | grep -oE '<svg[^>]*>' | head -1 || true)
      if [[ -n "$TAG_CONTENT" ]]; then
        if ! echo "$TAG_CONTENT" | grep -qE '(aria-hidden|aria-label)\s*='; then
          VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
          VIOLATIONS="${VIOLATIONS}  ${file}:${LINE_NUM}  <svg> missing aria-hidden or aria-label\n"
          VIOLATIONS="${VIOLATIONS}    Add aria-hidden=\"true\" for decorative SVGs, or aria-label for meaningful ones\n"
        fi
      fi
    fi

    # -----------------------------------------------------------
    # Check (a): Icon-only buttons without accessible label
    # -----------------------------------------------------------
    if echo "$line" | grep -qiE '<[Bb]utton([[:space:]]|>)'; then
      TOTAL_ELEMENTS=$((TOTAL_ELEMENTS + 1))

      # Gather multi-line context until closing tag or self-close
      BUTTON_BLOCK=$(sed -n "${LINE_NUM},$((LINE_NUM + 10))p" "$file" | tr '\n' ' ')

      # Extract from <Button/button to </Button>/</button> or />
      BUTTON_TAG=$(echo "$BUTTON_BLOCK" | grep -oE '<[Bb]utton[^>]*>.*<\/[Bb]utton>' | head -1 || true)
      if [[ -z "$BUTTON_TAG" ]]; then
        # Try self-closing
        BUTTON_TAG=$(echo "$BUTTON_BLOCK" | grep -oE '<[Bb]utton[^/]*/>' | head -1 || true)
      fi

      if [[ -n "$BUTTON_TAG" ]]; then
        # Check if the button has only icon children
        CHILDREN=$(echo "$BUTTON_TAG" | sed -E 's/<[Bb]utton[^>]*>//;s/<\/[Bb]utton>//')

        # Check if children contain only icon-like components and whitespace
        # Icon patterns: <SomeIcon, <Icon, <svg, no visible text
        STRIPPED_CHILDREN=$(echo "$CHILDREN" | sed -E 's/<[A-Z][a-zA-Z]*Icon[^>]*\/?>//g' | sed -E 's/<Icon[^>]*\/?>//g' | sed -E 's/<svg[^>]*\/?>.*<\/svg>//g' | sed -E 's/<svg[^>]*\/?>//g' | sed 's/[[:space:]]//g')

        # If after removing icon components, nothing meaningful remains -> icon-only button
        if [[ -z "$STRIPPED_CHILDREN" ]] || echo "$STRIPPED_CHILDREN" | grep -qE '^(<\/?[a-z][^>]*>)*$'; then
          # Now check if it has aria-label or sr-only span
          HAS_A11Y=false
          if echo "$BUTTON_TAG" | grep -qE 'aria-label\s*='; then
            HAS_A11Y=true
          fi
          if echo "$BUTTON_TAG" | grep -qE 'sr-only'; then
            HAS_A11Y=true
          fi
          if echo "$BUTTON_TAG" | grep -qE 'title\s*='; then
            HAS_A11Y=true
          fi

          # Only flag if children look like they are truly icon-only
          if echo "$CHILDREN" | grep -qE '<[A-Z][a-zA-Z]*Icon|<Icon|<svg'; then
            if ! $HAS_A11Y; then
              VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
              VIOLATIONS="${VIOLATIONS}  ${file}:${LINE_NUM}  Icon-only button without accessible label\n"
              VIOLATIONS="${VIOLATIONS}    Add aria-label=\"description\" or a <span className=\"sr-only\">text</span>\n"
            fi
          fi
        fi
      fi
    fi

  done < "$file"
done <<< "$CHANGED_TSX"

# --- Output ---
if $SCORE_MODE; then
  if [[ $TOTAL_ELEMENTS -eq 0 ]]; then
    SCORE="1.0"
  else
    SCORE=$(awk "BEGIN { s = 1 - ($VIOLATION_COUNT / $TOTAL_ELEMENTS); if (s < 0) s = 0; printf \"%.2f\", s }")
  fi
  DETAILS="Found ${VIOLATION_COUNT} a11y violation(s) across ${TOTAL_ELEMENTS} element(s)."
  DETAILS_ESC=$(printf '%s' "$DETAILS" | sed 's/\\/\\\\/g; s/"/\\"/g')
  echo "{\"score\": ${SCORE}, \"details\": \"${DETAILS_ESC}\"}"
  if [[ $VIOLATION_COUNT -gt 0 ]]; then
    exit 1
  fi
  exit 0
fi

if [[ $VIOLATION_COUNT -gt 0 ]]; then
  echo "FAIL: ${VIOLATION_COUNT} accessibility violation(s) found."
  echo ""
  echo "Violations:"
  echo -e "$VIOLATIONS"
  echo ""
  echo "Reference:"
  echo "  - Icon-only buttons MUST have aria-label or visually-hidden text"
  echo "  - Images MUST have alt text (use alt=\"\" for purely decorative images)"
  echo "  - SVGs MUST have aria-hidden=\"true\" (decorative) or aria-label (meaningful)"
  exit 1
else
  if [[ $TOTAL_ELEMENTS -eq 0 ]]; then
    echo "PASS: No interactive/media elements found in changed files."
  else
    echo "PASS: All ${TOTAL_ELEMENTS} element(s) meet baseline accessibility requirements."
  fi
  exit 0
fi
