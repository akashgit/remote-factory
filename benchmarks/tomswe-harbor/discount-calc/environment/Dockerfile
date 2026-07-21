FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Create the sample project with a discount calculation bug
RUN printf 'def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item["price"]\n    return total\n' > /workspace/pricing.py

RUN printf 'import pytest\nfrom pricing import calculate_total\n\ndef test_basic_total():\n    items = [{"price": 10.0}, {"price": 20.0}]\n    assert calculate_total(items) == 30.0\n\ndef test_with_discount():\n    items = [{"price": 100.0, "discount": 0.1}, {"price": 50.0, "discount": 0.2}]\n    assert calculate_total(items) == 130.0  # 90 + 40\n\ndef test_no_discount():\n    items = [{"price": 25.0}, {"price": 75.0}]\n    assert calculate_total(items) == 100.0\n\ndef test_zero_discount():\n    items = [{"price": 50.0, "discount": 0.0}]\n    assert calculate_total(items) == 50.0\n' > /workspace/test_pricing.py

RUN pip install pytest

RUN useradd -m -s /bin/bash agent 2>/dev/null || true && chown -R agent:agent /workspace

# Mark /workspace as safe for ALL users (root writes during Harbor install
# phase cause git safe.directory ownership mismatches for the agent user).
RUN git config --system safe.directory /workspace && \
    git config --system safe.directory '*'

# Pre-initialize git as agent with full repo + initial commit.
USER agent
WORKDIR /workspace
RUN git config --global user.name "Factory Agent" && \
    git config --global user.email "factory@agent.local" && \
    git config --global init.defaultBranch main && \
    git init && \
    printf "/proc\n/sys\n/dev\n/run\n/tmp\n/var\n/root\n/home\n/usr\n/bin\n/sbin\n/lib\n/lib64\n/etc\n/boot\n/mnt\n/opt\n/srv\n/media\n/logs\n" > .gitignore && \
    git add -A && \
    git commit -m "initial state"
USER root
