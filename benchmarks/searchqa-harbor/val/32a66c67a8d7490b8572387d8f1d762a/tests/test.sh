#!/bin/bash
set -e

ANSWER_FILE="/workspace/answer.txt"
REWARD_FILE="/workspace/reward.txt"

if [ ! -f "$ANSWER_FILE" ]; then
  echo "FAIL: No answer found at $ANSWER_FILE"
  echo "0.0" > "$REWARD_FILE"
  exit 1
fi

python3 -c "
import json, re, string, sys

def normalize(s):
    s = s.lower()
    s = re.sub(r'\b(a|an|the)\b', ' ', s)
    s = ''.join(c for c in s if c not in string.punctuation)
    return ' '.join(s.split())

answer = open('/workspace/answer.txt').read().strip()
m = re.search(r'<answer>(.*?)</answer>', answer, re.DOTALL | re.IGNORECASE)
if m:
    answer = m.group(1).strip()

gold = json.load(open('/workspace/.gold_answers.json'))
pred_norm = normalize(answer)
match = any(normalize(g) == pred_norm for g in gold)
reward = 1.0 if match else 0.0
print(f'Predicted: {answer}')
print(f'Gold: {gold}')
print(f'Match: {match}')
with open('/workspace/reward.txt', 'w') as f:
    f.write(str(reward))
sys.exit(0 if match else 1)
"
