FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

RUN printf 'from datetime import datetime\n\ndef parse_date(date_str):\n    return datetime.strptime(date_str, "%%Y-%%m-%%d")\n' > /workspace/dateutil.py

RUN printf 'import pytest\nfrom datetime import datetime\nfrom dateutil import parse_date\n\ndef test_iso_format():\n    result = parse_date("2024-01-15")\n    assert result == datetime(2024, 1, 15)\n\ndef test_us_format():\n    result = parse_date("01/15/2024")\n    assert result == datetime(2024, 1, 15)\n\ndef test_eu_format():\n    result = parse_date("15-01-2024")\n    assert result == datetime(2024, 1, 15)\n\ndef test_invalid_raises():\n    with pytest.raises(ValueError):\n        parse_date("not-a-date")\n\ndef test_iso_with_time():\n    result = parse_date("2024-01-15T10:30:00")\n    assert result == datetime(2024, 1, 15, 10, 30, 0)\n' > /workspace/test_dateutil.py

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
