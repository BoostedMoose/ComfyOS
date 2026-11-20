"""All ComfyOS tests against the mocks – every test in a process of its own (isolated data/profiles).

    python tests/run_all.py                    # backend tests (tests/<folder>/test_*.py) and the tests of the built-in plugins
                                               # (public/plugins/*/tests/test_*.py – every plugin brings its own)
    python tests/run_all.py --group security   # only one folder: core, flow, plugins, security, public (several with a comma)
    python tests/run_all.py --only test_weather,test_privacy   # single tests by name
    python tests/run_all.py --ui-only          # only the Playwright UI tests (tests/ui/ui_*.py, screenshots in tests/shots/)
    python tests/run_all.py --ui               # backend and UI tests together (only if both should run on one machine)
    python tests/run_all.py --only ui_system   # single UI tests by name

Folders:
    core/      devices, lock, scenes, power, history, weather, push, licence, plans, structure, data files
    flow/      engine, nodes, ports
    plugins/   loader and interface, interplay of the plugins with the core
    security/  attacks by attack surface and privacy – must always be green
    ui/        interface in the browser, per page
    support/   shared scaffolding (not a test)

Backend tests run on the Pi, the UI tests on Windows – Playwright never on the Pi (CLAUDE.md, section Testing).
Runs with the Python it is started with – so start it with the fixed test environment of the respective machine.
Return value 0 = everything green.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # the Windows console (cp1252) otherwise aborts on umlauts/symbols
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GROUPS = ("core", "flow", "plugins", "security")
args = sys.argv[1:]


def option(name: str) -> list[str] | None:
    """Value of an option such as `--only a,b` as a list, otherwise None."""
    return args[args.index(name) + 1].split(",") if name in args else None


ui = "--ui" in args
ui_only = "--ui-only" in args
only = option("--only")
groups = option("--group")

backend = [t for g in GROUPS for t in sorted((HERE / g).glob("test_*.py"))]
public = sorted((ROOT / "public" / "plugins").glob("*/tests/test_*.py"))
ui_tests = sorted((HERE / "ui").glob("ui_*.py"))
if groups:
    tests = [t for t in backend if t.parent.name in groups] + (public if "public" in groups else []) + (ui_tests if "ui" in groups else [])
else:
    tests = ([] if ui_only else backend + public) + (ui_tests if ui or ui_only or only else [])
if only:
    tests = [t for t in tests if t.stem in only]
if not tests:
    sys.exit("Keine passenden Tests gefunden.")

results = []
for t in tests:
    t0 = time.time()
    print(f"\n===== {t.name}", flush=True)
    p = subprocess.run([sys.executable, str(t), str(ROOT)], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (p.stdout + p.stderr).strip()
    lines = out.splitlines()
    print("\n".join(lines[-25:]) if p.returncode else (lines[-1] if lines else ""))
    # UI-Tests melden Probleme als Text statt Exit-Code
    ok = p.returncode == 0 and not (t.stem.startswith("ui_") and "UI OK" not in out)
    if not ok and p.returncode == 0:
        print("\n".join(lines[-25:]))
    results.append((f"{t.parent.name}/{t.name}" if t.parent.name != "tests" else f"{t.parents[1].name}/{t.name}", ok, time.time() - t0))

print("\n===== Result")
for name, ok, dt in results:
    print(f"  {'OK    ' if ok else 'FAILED'} {name}  ({dt:.0f} s)")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
