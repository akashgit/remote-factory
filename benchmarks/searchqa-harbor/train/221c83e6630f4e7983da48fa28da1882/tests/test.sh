#!/bin/bash
set -e

ANSWER_FILE="/workspace/answer.txt"

if [ ! -f "$ANSWER_FILE" ]; then
  echo "FAIL: No answer found at $ANSWER_FILE"
  exit 1
fi

python3 -c "
import json, re, string, sys

def normalize(s):
    s = s.lower()
    s = re.sub(r'\\b(a|an|the)\\b', ' ', s)
    s = ''.join(c for c in s if c not in string.punctuation)
    return ' '.join(s.split())

answer = open('/workspace/answer.txt').read().strip()
m = re.search(r'<answer>(.*?)</answer>', answer, re.DOTALL | re.IGNORECASE)
if m:
    answer = m.group(1).strip()

gold = json.load(open('/workspace/.gold_answers.json'))
pred_norm = normalize(answer)
match = any(normalize(g) == pred_norm for g in gold)
print(f'Predicted: {answer}')
print(f'Gold: {gold}')
print(f'Match: {match}')
sys.exit(0 if match else 1)
"
