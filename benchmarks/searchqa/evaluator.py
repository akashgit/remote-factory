"""Standard QA evaluation metrics (SQuAD-style EM / F1 / sub-EM)."""
from __future__ import annotations

import re
import string
from collections import Counter


def normalize_answer(s: str) -> str:
    """Lowercase, strip articles, punctuation, and extra whitespace."""
    s = s.lower()
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = "".join(c for c in s if c not in string.punctuation)
    s = " ".join(s.split())
    return s


def exact_match(prediction: str, gold: str) -> float:
    return float(normalize_answer(prediction) == normalize_answer(gold))


def sub_em(prediction: str, gold: str) -> float:
    """Substring exact match — gold appears within prediction (after normalization)."""
    return float(normalize_answer(gold) in normalize_answer(prediction))


def f1_score(prediction: str, gold: str) -> float:
    """Counter-based token F1 (handles duplicate tokens correctly)."""
    pred_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not pred_tokens and not gold_tokens:
        return 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0
    pred_counter = Counter(pred_tokens)
    gold_counter = Counter(gold_tokens)
    common = sum((pred_counter & gold_counter).values())
    if common == 0:
        return 0.0
    precision = common / len(pred_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def extract_answer(text: str) -> str:
    """Extract answer from <answer> tags, falling back to last non-empty line."""
    match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line:
            return line
    return text.strip()


def score_prediction(prediction: str, gold_answers: list[str]) -> dict[str, float]:
    """Score a prediction against one or more gold answers, returning best EM, sub_em, and F1."""
    if not gold_answers:
        return {"exact_match": 0.0, "sub_em": 0.0, "f1": 0.0}
    best_em = max(exact_match(prediction, g) for g in gold_answers)
    best_sub_em = max(sub_em(prediction, g) for g in gold_answers)
    best_f1 = max(f1_score(prediction, g) for g in gold_answers)
    return {"exact_match": best_em, "sub_em": best_sub_em, "f1": best_f1}
