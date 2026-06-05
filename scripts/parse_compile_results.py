#!/usr/bin/env python3
"""CLI wrapper: parse Gradle compilation output from stdin."""

import json
import sys

from factory.build_root.gradle_parser import parse_compile


def main() -> None:
    text = sys.stdin.read()
    result = parse_compile(text)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
