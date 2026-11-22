"""ComfyOS – structure: route snapshot (main.py + api/*), no shadowed routes, routers without reaching back to main,
release package without tests/docs/captures, no source code under .gitignore.

New or removed route = deliberate change: adapt tests/routes.json (sorted, "METHOD /path").

    python tests/core/test_structure.py [path-to-comfyos]
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("struct")

from app import main, updater  # noqa: E402

API = ROOT / "backend" / "app" / "api"

# 1) Routen exakt wie im Snapshot
routes = [r for r in main.routes() if getattr(r, "path", "").startswith("/api/")]
have = sorted(f"{m} {r.path}" for r in routes for m in (r.methods or set()) - {"HEAD"})
want = json.loads((ROOT / "tests" / "routes.json").read_text(encoding="utf-8"))
assert len(have) == len(set(have)), "duplicate route:" + str([x for x in have if have.count(x) > 1])
missing, extra = sorted(set(want) - set(have)), sorted(set(have) - set(want))
assert not missing and not extra, f"routes differ from the snapshot – missing: {missing}, new: {extra}"
print(f"1 {len(have)} API routes as in tests/routes.json")

# 2) No route with a fixed path is shadowed by a placeholder route that was mounted earlier
for j, later in enumerate(routes):
    if "{" in later.path:
        continue
    for earlier in routes[:j]:
        shared = (earlier.methods or set()) & (later.methods or set()) - {"HEAD"}
        assert not (shared and "{" in earlier.path and earlier.path_regex.match(later.path)), \
            f"{later.path} is shadowed by {earlier.path} – check the order of the routers in main.py"
print("2 no shadowed routes")

# 3) Routers depend only on core/stores, never on main (otherwise a circular import); every router is mounted
mods = sorted(p.stem for p in API.glob("*.py") if p.stem != "__init__")
for name in mods:
    src = (API / f"{name}.py").read_text(encoding="utf-8")
    assert not re.search(r"\bimport main\b|from \.\.main\b|from \.\. import [^\n]*\bmain\b", src), f"api/{name}.py imports main"
    assert "router = APIRouter()" in src, name
assert sorted(m.__name__.rsplit(".", 1)[1] for m in main.ROUTERS) == mods, "a router in api/ is not mounted"
print(f"3 {len(mods)} routers, all mounted, none imports main")

# 4) Release-Paket: App ja, Tests/Doku/Mitschnitte/Agenten-Konfiguration nein
files = updater.release_files(ROOT)
assert "backend/app/main.py" in files and "backend/app/core.py" in files and "backend/app/api/flows.py" in files and "VERSION" in files
assert "tools/reset_password.py" in files
assert "public/plugins/matter/bridge/dist/bridge.mjs" in files, "the bundled helper program of the Matter plugin is missing in the package"
assert not updater._is_preserved("public/plugins/matter/bridge/dist/bridge.mjs") and updater._is_preserved("dist/altes-paket.whl")
assert updater._is_preserved("public/plugins/matter/bridge/node_modules/esbuild/package.json")
bad = [f for f in files if f.startswith(("tests/", "docs/", "dev/", ".claude/", ".github/")) or f.lower().endswith((".md", ".har", ".env"))
       or "/captures/" in "/" + f or "mint_gift" in f or "build_secrets" in f]
assert not bad, bad
print(f"4 release package: {len(files)} files, without tests/docs/captures/licence tool")

# 5) Nothing that belongs to the source code falls under .gitignore – otherwise it is missing after the next clone
#    without anybody noticing here (that is how the sample files in tests/data/ went missing, because "data/" is ignored).
EXPECTED_IGNORED = ("__pycache__/", ".pytest_cache/", "tests/shots/", "tools/build_secrets.env", "tools/captures/", "/.venv/", "/venv/",
                    "tools/mint_gift.py",   # the licence generator stays private (the public repository never gets it)
                    "/node_modules/",     # only for building the Matter helper program; shipped is bridge/dist/bridge.mjs
                    "/dev/")              # new development tools stay private (.gitignore "dev/", e.g. the effect lab)
if shutil.which("git") and (ROOT / ".git").exists():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--others", "--ignored", "--exclude-standard", "--",
                             "backend", "tests", "tools", "dev", "public"], capture_output=True, text=True, timeout=120)
    assert listed.returncode == 0, listed.stderr[:300]
    lost = [line for line in listed.stdout.splitlines()
            if line and not line.endswith((".pyc", ".pyo")) and not any(part in "/" + line for part in EXPECTED_IGNORED)]
    assert not lost, "files fall under .gitignore and would never get into the repo:\n  " + "\n  ".join(lost[:30])
    print("5 no source code and no test file falls under .gitignore")
else:
    print("5 skipped: not a Git working directory")

print("OK test_structure")
