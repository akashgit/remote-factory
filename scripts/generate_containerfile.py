#!/usr/bin/env python3
"""Generate a Containerfile for build-root from parameters."""

import argparse
import sys


def generate(jdk_version: int = 11, build_system: str = "gradle") -> str:
    base_image = f"eclipse-temurin:{jdk_version}-jdk-jammy"

    lines = [
        f"FROM {base_image}",
        "",
        "RUN apt-get update && apt-get install -y --no-install-recommends \\",
        "    git curl unzip locales findutils \\",
        "    && rm -rf /var/lib/apt/lists/* \\",
        '    && sed -i "/en_US.UTF-8/s/^# //" /etc/locale.gen \\',
        "    && locale-gen",
        "",
        "ENV LANG=en_US.UTF-8 \\",
        "    LC_ALL=en_US.UTF-8 \\",
        "    TZ=UTC",
        "",
        "WORKDIR /workspace",
        "",
        "COPY gradle/init.d/*.gradle /root/.gradle/init.d/",
        "COPY local-repo/ /root/.m2/repository/",
        "",
        "COPY . /workspace",
        "",
    ]

    if build_system == "gradle":
        lines.append("RUN chmod +x gradlew 2>/dev/null || true")
        lines.append("")

    lines.append('CMD ["bash"]')
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Containerfile for build-root")
    parser.add_argument("--jdk-version", type=int, default=11)
    parser.add_argument("--build-system", default="gradle", choices=["gradle"])
    args = parser.parse_args()

    sys.stdout.write(generate(jdk_version=args.jdk_version, build_system=args.build_system))


if __name__ == "__main__":
    main()
