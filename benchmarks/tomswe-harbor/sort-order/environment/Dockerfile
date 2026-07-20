FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

RUN printf 'def sort_names(names):\n    return sorted(names)\n' > /workspace/sorter.py

RUN printf 'from sorter import sort_names\nimport unittest\n\nclass TestSorter(unittest.TestCase):\n    def test_basic(self):\n        self.assertEqual(sort_names(["banana", "apple"]), ["apple", "banana"])\n\n    def test_case_insensitive(self):\n        result = sort_names(["banana", "Apple", "cherry"])\n        self.assertEqual(result, ["Apple", "banana", "cherry"])\n\n    def test_empty(self):\n        self.assertEqual(sort_names([]), [])\n\n    def test_single(self):\n        self.assertEqual(sort_names(["only"]), ["only"])\n\nif __name__ == "__main__":\n    unittest.main()\n' > /workspace/test_sorter.py

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
