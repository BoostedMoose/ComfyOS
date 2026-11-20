"""Check inventory of the test suite: record every output line of every test and compare two states.

Every test prints its sections as lines ("1 87 Routen ohne Login -> 401"). These lines are the list
of the checks. Before a restructuring of the tests it is recorded, afterwards every line must show up again –
no matter in which file. That way no check is lost when tidying up.

    python dev/test_inventory.py run <name>            # run the backend tests, output to tests/shots/inventory-<name>.txt
    python dev/test_inventory.py run <name> --ui       # the same for the Playwright UI tests (Windows only)
    python dev/test_inventory.py compare <before> <after>   # which checks are missing in the second state?

Backend tests run on the Pi, UI tests on Windows (CLAUDE.md, section Testing). The files live in
tests/shots/ and thus never get into the repo.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "tests" / "shots"
MARK = "===== "


def find_tests(ui: bool) -> list[Path]:
    """All test files in the old (tests/*.py) and new layout (tests/<folder>/*.py), plus the tests of the plugins."""
    tests = ROOT / "tests"
    prefix = "ui_" if ui else "test_"
    found = sorted(tests.glob(f"{prefix}*.py")) + sorted(tests.glob(f"*/{prefix}*.py"))
    if not ui:
        found += sorted((ROOT / "public" / "plugins").glob("*/tests/test_*.py"))
    return found


def run(name: str, ui: bool) -> int:
    """Run every test in a process of its own and write its whole output to a file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"inventory-{name}.txt"
    failed = 0
    with target.open("w", encoding="utf-8", newline="\n") as out:
        for test in find_tests(ui):
            started = time.time()
            proc = subprocess.run([sys.executable, str(test), str(ROOT)], cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            text = (proc.stdout + proc.stderr).strip()
            ok = proc.returncode == 0 and not (ui and "UI OK" not in text)
            failed += not ok
            label = f"{test.parents[1].name}/{test.stem}" if "public" in test.parts else test.stem
            out.write(f"{MARK}{label} rc={proc.returncode} ok={int(ok)} {time.time() - started:.0f}s\n{text}\n")
            out.flush()
            print(f"{'OK    ' if ok else 'FAILED'} {label}  ({time.time() - started:.0f} s)", flush=True)
    print(f"\n{target}  –  {failed} fehlgeschlagen")
    return 1 if failed else 0


def normal(line: str) -> str:
    """Simplify a line so that it is recognised again after a restructuring: numbers, paths and whitespace do not count."""
    line = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+|/(?:tmp|home|var)/[^\s'\"]+", "<pfad>", line)
    line = re.sub(r"Einrichtungscode \S+", "Einrichtungscode <code>", line)      # a different one on every start
    line = re.sub(r"\d+", "#", line)
    return re.sub(r"\s+", " ", line).strip()


def read(name: str) -> dict[str, list[str]]:
    """Inventardatei lesen: Testname -> Ausgabezeilen."""
    tests: dict[str, list[str]] = {}
    current = None
    for line in (OUT_DIR / f"inventory-{name}.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith(MARK):
            current = line[len(MARK):].split(" ")[0]
            tests[current] = [line]
        elif current is not None and line.strip():
            tests[current].append(line)
    return tests


def compare(before: str, after: str) -> int:
    """Every line of the first state must occur in the second (in any test). What is missing is listed."""
    old, new = read(before), read(after)
    seen = {normal(line) for lines in new.values() for line in lines[1:]}
    missing = 0
    for test, lines in old.items():
        gone = [line for line in lines[1:] if normal(line) not in seen]
        if gone:
            missing += len(gone)
            print(f"\n{test}: {len(gone)} Zeile(n) fehlen")
            for line in gone[:40]:
                print("   " + line[:200])
    bad = [lines[0] for lines in new.values() if " ok=0 " in lines[0]]
    for head in bad:
        print("MISSING in the second state: " + head)
    total = sum(len(lines) - 1 for lines in old.values())
    print(f"\n{total} Zeilen vorher, {missing} fehlen, {len(bad)} Tests im zweiten Stand fehlgeschlagen")
    return 1 if missing or bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "run":
        sys.exit(run(args[1], "--ui" in args))
    if len(args) == 3 and args[0] == "compare":
        sys.exit(compare(args[1], args[2]))
    sys.exit(__doc__)
