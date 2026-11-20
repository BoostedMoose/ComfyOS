"""Secret scan: finds private keys, tokens, gateway ids and real e-mail addresses in the repo.

    python tools/check_secrets.py            # repo (tracked + new files that are not ignored)
    python tools/check_secrets.py <folder>   # any folder, e.g. an unpacked release package

Return value 0 = clean, 1 = hits. Runs in tests/run_all.py (tests/security/test_privacy.py) and in CI.
Hits are only printed shortened, so that a find does not end up in the log itself.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "data", "logs", "captures", "__pycache__", "node_modules", "shots", "dist"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".pyc", ".woff", ".woff2"}

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "GitHub-Token": re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack-Token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "gateway id": re.compile(r"\b[A-Z]{2}\d{5,}-\d{3,}\b"),
    "license secret": re.compile(r"\bOWNER_(?:TOKEN|SIGN_KEY)\s*=\s*[0-9a-fA-F-]{32,}"
                                   r"|\"type\"\s*:\s*\"owner\"\s*,\s*\"token\"\s*:\s*\"[^\"]{16,}\""),
    "e-mail address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}
EMAIL_OK = re.compile(r"@(?:example\.(?:com|de|org|net)|users\.noreply\.github\.com|noreply\.[a-z.]+)$|^noreply@", re.I)


def repo_files(root: Path) -> list[Path]:
    """Files that are checked: everything Git knows or would know (without ignored files) – without Git all files
    below the root. Binary formats, skipped folders and this script itself are left out."""
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=root, capture_output=True, text=True, check=True).stdout
        files = [root / f for f in out.splitlines() if f]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in root.rglob("*") if p.is_file()]
    return [f for f in files if f.is_file() and not (set(f.relative_to(root).parts) & SKIP_DIRS)
            and f.suffix.lower() not in SKIP_SUFFIXES and f.resolve() != Path(__file__).resolve()]


def scan(root: Path, files: list[Path] | None = None) -> list[tuple[str, str, str]]:
    """Search files for the patterns of secrets and personal data. Returns (file, kind, beginning of the
    find) – never the whole value."""
    hits = []
    for f in files if files is not None else repo_files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                value = m.group(0)
                if name == "e-mail address" and EMAIL_OK.search(value):
                    continue
                hits.append((str(f.relative_to(root)), name, value[:6] + "…"))
    return hits


def main() -> int:
    """Check the repo (without an argument) or a folder and print the finds. Returns 1 if there are finds."""
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    files = None if len(sys.argv) <= 1 else [p for p in root.rglob("*") if p.is_file()
                                             and not (set(p.relative_to(root).parts) & SKIP_DIRS)
                                             and p.suffix.lower() not in SKIP_SUFFIXES]
    hits = scan(root, files)
    for path, name, preview in hits:
        print(f"FUND  {path}: {name} ({preview})")
    print("Secret-Scan: " + (f"{len(hits)} Treffer" if hits else "sauber"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
