ARG BASE_IMAGE=programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom_v6
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git rsync curl procps ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && export PATH="/root/.local/bin:$PATH" \
    && uv tool install 'remote-factory @ git+https://github.com/akashgit/remote-factory.git'

RUN useradd -m -s /bin/bash agent 2>/dev/null || true \
    && chown -R agent:agent /workspace \
    && mkdir -p /home/agent/.claude /home/agent/.local /home/agent/.cargo \
    && cp -r /root/.local/* /home/agent/.local/ 2>/dev/null || true \
    && cp -r /root/.cargo/* /home/agent/.cargo/ 2>/dev/null || true \
    && chown -R agent:agent /home/agent
