FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Create the sample project with a discount calculation bug
RUN printf 'def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item["price"]\n    return total\n' > /workspace/pricing.py

RUN printf 'import pytest\nfrom pricing import calculate_total\n\ndef test_basic_total():\n    items = [{"price": 10.0}, {"price": 20.0}]\n    assert calculate_total(items) == 30.0\n\ndef test_with_discount():\n    items = [{"price": 100.0, "discount": 0.1}, {"price": 50.0, "discount": 0.2}]\n    assert calculate_total(items) == 130.0  # 90 + 40\n\ndef test_no_discount():\n    items = [{"price": 25.0}, {"price": 75.0}]\n    assert calculate_total(items) == 100.0\n\ndef test_zero_discount():\n    items = [{"price": 50.0, "discount": 0.0}]\n    assert calculate_total(items) == 50.0\n' > /workspace/test_pricing.py

RUN pip install pytest

# Do NOT git init here — FactoryCeo.run() handles git initialization
RUN useradd -m -s /bin/bash agent 2>/dev/null || true && chown -R agent:agent /workspace
