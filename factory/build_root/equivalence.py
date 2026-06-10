"""Equivalence testing for rebuilt Java artifacts.

Compares rebuilt JARs against original artifacts from Maven Central
to verify semantic equivalence: same classes, same public API surfaces,
same method signatures.

Usage:
    from factory.build_root.equivalence import compare_jars, download_originals

    originals = download_originals(
        group="org.springframework",
        modules=["spring-core", "spring-beans"],
        version="3.0.0.RELEASE",
        dest="/tmp/originals"
    )
    report = compare_jars(originals, rebuilt_dir="/path/to/rebuilt/jars")
    print(report.summary())
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


MAVEN_CENTRAL = "https://repo1.maven.org/maven2"


@dataclass
class ModuleResult:
    module: str
    original_size: int = 0
    rebuilt_size: int = 0
    original_classes: list[str] = field(default_factory=list)
    rebuilt_classes: list[str] = field(default_factory=list)
    missing_classes: list[str] = field(default_factory=list)
    extra_classes: list[str] = field(default_factory=list)
    api_diffs: list[str] = field(default_factory=list)
    manifest_diffs: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def classes_match(self) -> bool:
        return not self.missing_classes and not self.extra_classes

    @property
    def api_match(self) -> bool:
        return not self.api_diffs

    @property
    def size_diff_pct(self) -> float:
        if self.original_size == 0:
            return 0.0
        return (self.rebuilt_size - self.original_size) / self.original_size * 100


@dataclass
class EquivalenceReport:
    modules: list[ModuleResult] = field(default_factory=list)
    java_home: str = ""
    timestamp: str = ""

    @property
    def total_classes(self) -> int:
        return sum(len(m.original_classes) for m in self.modules)

    @property
    def classes_match_count(self) -> int:
        return sum(1 for m in self.modules if m.classes_match)

    @property
    def api_match_count(self) -> int:
        return sum(1 for m in self.modules if m.api_match)

    def summary(self) -> str:
        lines = [
            "Equivalence Report",
            "=" * 60,
            f"Modules compared:      {len(self.modules)}",
            f"Class inventory match: {self.classes_match_count} / {len(self.modules)}",
            f"Public API match:      {self.api_match_count} / {len(self.modules)}",
            f"Total classes:         {self.total_classes}",
            "",
        ]
        for m in self.modules:
            status = "MATCH" if m.api_match else f"{len(m.api_diffs)} diffs"
            cls_status = "MATCH" if m.classes_match else f"-{len(m.missing_classes)}/+{len(m.extra_classes)}"
            lines.append(
                f"  {m.module:30s}  classes={cls_status:10s}  api={status:10s}  "
                f"size={m.size_diff_pct:+.1f}%"
            )

        if all(m.api_match for m in self.modules):
            lines.append("")
            lines.append("RESULT: All modules are API-equivalent.")
        else:
            lines.append("")
            lines.append("RESULT: Some modules have API differences:")
            for m in self.modules:
                if not m.api_match:
                    for diff in m.api_diffs:
                        lines.append(f"  {m.module}: {diff}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "java_home": self.java_home,
                "modules_compared": len(self.modules),
                "classes_match": self.classes_match_count,
                "api_match": self.api_match_count,
                "total_classes": self.total_classes,
                "modules": [
                    {
                        "module": m.module,
                        "classes_match": m.classes_match,
                        "api_match": m.api_match,
                        "original_classes": len(m.original_classes),
                        "rebuilt_classes": len(m.rebuilt_classes),
                        "missing_classes": m.missing_classes,
                        "extra_classes": m.extra_classes,
                        "api_diffs": m.api_diffs,
                        "size_diff_pct": round(m.size_diff_pct, 2),
                        "error": m.error,
                    }
                    for m in self.modules
                ],
            },
            indent=2,
        )


def download_originals(
    group: str,
    modules: list[str],
    version: str,
    dest: str | Path,
    repo_url: str = MAVEN_CENTRAL,
) -> dict[str, Path]:
    """Download original JARs from Maven Central (or another repo).

    Returns a dict mapping module name to the downloaded JAR path.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    result = {}
    group_path = group.replace(".", "/")

    for module in modules:
        jar_name = f"{module}-{version}.jar"
        url = f"{repo_url}/{group_path}/{module}/{version}/{jar_name}"
        jar_path = dest / jar_name
        if not jar_path.exists():
            urllib.request.urlretrieve(url, str(jar_path))
        result[module] = jar_path

    return result


def _list_classes(jar_path: Path) -> list[str]:
    """List all .class files in a JAR."""
    result = subprocess.run(
        ["jar", "tf", str(jar_path)],
        capture_output=True, text=True, timeout=30,
    )
    return sorted(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().endswith(".class")
    )


def _get_manifest(jar_path: Path) -> str:
    """Extract META-INF/MANIFEST.MF from a JAR."""
    result = subprocess.run(
        ["unzip", "-p", str(jar_path), "META-INF/MANIFEST.MF"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _javap_api(jar_path: Path, class_name: str) -> str:
    """Get the public API of a class using javap."""
    result = subprocess.run(
        ["javap", "-public", "-classpath", str(jar_path), class_name],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def compare_module(
    module: str,
    original_jar: Path,
    rebuilt_jar: Path,
) -> ModuleResult:
    """Compare a single module's original and rebuilt JARs."""
    result = ModuleResult(module=module)

    if not original_jar.exists():
        result.error = f"Original JAR not found: {original_jar}"
        return result
    if not rebuilt_jar.exists():
        result.error = f"Rebuilt JAR not found: {rebuilt_jar}"
        return result

    result.original_size = original_jar.stat().st_size
    result.rebuilt_size = rebuilt_jar.stat().st_size

    result.original_classes = _list_classes(original_jar)
    result.rebuilt_classes = _list_classes(rebuilt_jar)

    orig_set = set(result.original_classes)
    rebuilt_set = set(result.rebuilt_classes)
    result.missing_classes = sorted(orig_set - rebuilt_set)
    result.extra_classes = sorted(rebuilt_set - orig_set)

    common_classes = orig_set & rebuilt_set
    for cls_file in sorted(common_classes):
        cls_name = cls_file.replace("/", ".").removesuffix(".class")
        orig_api = _javap_api(original_jar, cls_name)
        rebuilt_api = _javap_api(rebuilt_jar, cls_name)
        if orig_api != rebuilt_api:
            orig_lines = orig_api.splitlines()
            rebuilt_lines = rebuilt_api.splitlines()
            for i, (a, b) in enumerate(zip(orig_lines, rebuilt_lines)):
                if a != b:
                    result.api_diffs.append(
                        f"{cls_name} line {i+1}: '{a.strip()}' vs '{b.strip()}'"
                    )
            if len(orig_lines) != len(rebuilt_lines):
                result.api_diffs.append(
                    f"{cls_name}: line count {len(orig_lines)} vs {len(rebuilt_lines)}"
                )

    return result


def compare_jars(
    originals: dict[str, Path],
    rebuilt_dir: str | Path,
    module_to_jar: Optional[dict[str, str]] = None,
) -> EquivalenceReport:
    """Compare all modules' original and rebuilt JARs.

    Args:
        originals: dict mapping module name to original JAR path
        rebuilt_dir: directory containing rebuilt JARs
        module_to_jar: optional mapping from module name to rebuilt JAR filename
                       (if different from Maven artifact naming)
    """
    import datetime

    rebuilt_dir = Path(rebuilt_dir)
    report = EquivalenceReport(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        report.java_home = java_home
    else:
        result = subprocess.run(
            ["java", "-version"], capture_output=True, text=True
        )
        report.java_home = result.stderr.splitlines()[0] if result.stderr else "unknown"

    for module, original_jar in originals.items():
        if module_to_jar and module in module_to_jar:
            rebuilt_name = module_to_jar[module]
        else:
            rebuilt_name = original_jar.name
        rebuilt_jar = rebuilt_dir / rebuilt_name

        module_result = compare_module(module, original_jar, rebuilt_jar)
        report.modules.append(module_result)

    return report


def run_comparison(
    group: str,
    modules: list[str],
    version: str,
    rebuilt_dir: str | Path,
    output_path: Optional[str | Path] = None,
    module_to_jar: Optional[dict[str, str]] = None,
    repo_url: str = MAVEN_CENTRAL,
) -> EquivalenceReport:
    """Full pipeline: download originals, compare, write report.

    Args:
        group: Maven group ID (e.g., "org.springframework")
        modules: list of Maven artifact IDs
        version: Maven version string
        rebuilt_dir: directory containing rebuilt JARs
        output_path: optional path to write the report JSON
        module_to_jar: optional mapping from module name to rebuilt JAR filename
        repo_url: Maven repository URL (default: Maven Central)
    """
    with tempfile.TemporaryDirectory(prefix="equiv-originals-") as tmpdir:
        originals = download_originals(group, modules, version, tmpdir, repo_url)
        report = compare_jars(originals, rebuilt_dir, module_to_jar)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.to_json())

    return report
