FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

RUN printf 'def deduplicate(items):\n    return list(set(items))\n' > /workspace/dedup.py

RUN printf 'import pytest\nfrom dedup import deduplicate\n\ndef test_basic_dedup():\n    assert deduplicate([1, 2, 2, 3]) == [1, 2, 3]\n\ndef test_preserves_order():\n    assert deduplicate([3, 1, 2, 1, 3]) == [3, 1, 2]\n\ndef test_strings():\n    assert deduplicate(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]\n\ndef test_empty():\n    assert deduplicate([]) == []\n\ndef test_no_duplicates():\n    assert deduplicate([1, 2, 3]) == [1, 2, 3]\n' > /workspace/test_dedup.py

RUN pip install pytest
RUN useradd -m -s /bin/bash agent 2>/dev/null || true && chown -R agent:agent /workspace
RUN git config --system safe.directory /workspace && git config --system safe.directory '*'
USER agent
WORKDIR /workspace
RUN git config --global user.name "Factory Agent" && \
    git config --global user.email "factory@agent.local" && \
    git config --global init.defaultBranch main && \
    git init && \
    printf "/proc\n/sys\n/dev\n/run\n/tmp\n/var\n/root\n/home\n/usr\n/bin\n/sbin\n/lib\n/lib64\n/etc\n/boot\n/mnt\n/opt\n/srv\n/media\n/logs\n" > .gitignore && \
    git add -A && git commit -m "initial state"
USER root
