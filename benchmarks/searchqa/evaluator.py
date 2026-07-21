"""Standard QA evaluation metrics (SQuAD-style EM / F1)."""
from __future__ import annotations

import re
import string


def normalize_answer(s: str) -> str:
    """Lowercase, strip articles, punctuation, and extra whitespace."""
    s = s.lower()
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    s = "".join(c for c in s if c not in string.punctuation)
    s = " ".join(s.split())
    return s


def exact_match(prediction: str, gold: str) -> float:
    return float(normalize_answer(prediction) == normalize_answer(gold))


def f1_score(prediction: str, gold: str) -> float:
    pred_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not pred_tokens or not gold_tokens:
        return float(pred_tokens == gold_tokens)
    common = set(pred_tokens) & set(gold_tokens)
    if not common:
        return 0.0
    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def score_prediction(prediction: str, gold_answers: list[str]) -> dict[str, float]:
    """Score a prediction against one or more gold answers, returning best EM and F1."""
    if not gold_answers:
        return {"exact_match": 0.0, "f1": 0.0}
    best_em = max(exact_match(prediction, g) for g in gold_answers)
    best_f1 = max(f1_score(prediction, g) for g in gold_answers)
    return {"exact_match": best_em, "f1": best_f1}
