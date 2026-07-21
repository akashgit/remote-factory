FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

RUN printf 'def to_csv(rows, headers):\n    lines = [",".join(headers)]\n    for row in rows:\n        lines.append(",".join(str(v) for v in row.values()))\n    return "\\n".join(lines) + "\\n"\n' > /workspace/exporter.py

RUN printf 'import csv\nimport io\nimport pytest\nfrom exporter import to_csv\n\ndef test_basic():\n    rows = [{"name": "Alice", "age": "30"}]\n    result = to_csv(rows, ["name", "age"])\n    reader = csv.reader(io.StringIO(result))\n    data = list(reader)\n    assert data == [["name", "age"], ["Alice", "30"]]\n\ndef test_comma_in_field():\n    rows = [{"name": "Smith, John", "age": "25"}]\n    result = to_csv(rows, ["name", "age"])\n    reader = csv.reader(io.StringIO(result))\n    data = list(reader)\n    assert data == [["name", "age"], ["Smith, John", "25"]]\n\ndef test_quote_in_field():\n    rows = [{"name": '"The Boss"', "age": "40"}]\n    result = to_csv(rows, ["name", "age"])\n    reader = csv.reader(io.StringIO(result))\n    data = list(reader)\n    assert data[1][0] == '"'"'"The Boss"'"'"'\n\ndef test_multiple_rows():\n    rows = [\n        {"name": "Alice", "age": "30"},\n        {"name": "Bob, Jr.", "age": "25"},\n    ]\n    result = to_csv(rows, ["name", "age"])\n    reader = csv.reader(io.StringIO(result))\n    data = list(reader)\n    assert len(data) == 3\n    assert data[2] == ["Bob, Jr.", "25"]\n' > /workspace/test_exporter.py

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
