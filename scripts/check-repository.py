"""Conservative checks for accidentally committed secrets and private paths."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {
    "",
    ".css",
    ".env",
    ".example",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
STRONG_SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}
PRIVATE_PATH = re.compile(
    r"(?:[A-Za-z]:\\Users\\[^\\\s]+|/Users/[^/\s]+|/home/[^/\s]+)"
)
MANDATORY_CLOUD = re.compile(
    r"https?://(?:api|license|analytics)\.cluecdc\.com", re.IGNORECASE
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    tracked = tracked_files()
    if ROOT / ".env" in tracked:
        failures.append(".env is tracked")
    for path in tracked:
        if path == ROOT / "scripts" / "check-repository.py":
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        for name, pattern in STRONG_SECRET_PATTERNS.items():
            if pattern.search(value):
                failures.append(f"{relative}: possible {name}")
        if PRIVATE_PATH.search(value):
            failures.append(f"{relative}: personal filesystem path")
        if MANDATORY_CLOUD.search(value):
            failures.append(f"{relative}: mandatory ClueCDC cloud endpoint")
    if failures:
        print("Repository safety checks failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Repository safety checks passed for {len(tracked)} tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
