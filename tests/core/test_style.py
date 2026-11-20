"""ComfyOS – style and construction rules of the core (static, without starting the app).

    1  every file, class, function and method has a description (functions inside functions do not need one) –
       in the core as well as in tests, tools, SDK and bundled plugins
    2  no silent error handling without a reason (`except …: pass/continue` needs a comment)
    3  input models of the routes: text, list and object fields carry a length limit
    4  facade: what tests and routes replace through `core.` or `flow_engine.` is never imported by name
    5  source code with LF line endings, without tabs and without trailing spaces
    6  interface: one source for live fields and controllability, blocks of the home page with a fixed contract
    7  interface: every script has a head, every larger function a description

    python tests/core/test_style.py [path-to-comfyos]
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("style")

APP = ROOT / "backend" / "app"
CORE_FILES = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts) + [ROOT / "backend" / "run.py"]
TREES = {p: ast.parse(p.read_text(encoding="utf-8")) for p in CORE_FILES}


def rel(path: Path) -> str:
    """Path relative to the root, for messages."""
    return path.relative_to(ROOT).as_posix()


def described(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """(name, node) of all functions and classes at module level and of all methods – without functions inside functions."""
    out: list[tuple[str, ast.AST]] = []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((prefix + node.name, node))
            elif isinstance(node, ast.ClassDef):
                out.append((prefix + node.name, node))
                visit(node.body, prefix + node.name + ".")
    visit(tree.body, "")
    return out


# 1) Descriptions
missing = []
for path, tree in TREES.items():
    if path.name == "__init__.py" and not tree.body:
        continue
    if not ast.get_docstring(tree):
        missing.append(f"{rel(path)}: head of the file")
    missing += [f"{rel(path)}: {name}" for name, node in described(tree) if not ast.get_docstring(node)]
assert not missing, f"{len(missing)} without a description:\n  " + "\n  ".join(missing[:40])
total = sum(len(described(t)) for t in TREES.values())
# … and the rest of the code: tests, tools, SDK and bundled plugins
OTHER_FILES = sorted(p for folder in ("tests", "tools", "dev", "public") for p in (ROOT / folder).rglob("*.py")
                     if "__pycache__" not in p.parts and p.stat().st_size)
others = 0
for path in OTHER_FILES:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if not ast.get_docstring(tree):
        missing.append(f"{rel(path)}: head of the file")
    names = described(tree)
    others += len(names)
    missing += [f"{rel(path)}: {name}" for name, node in names if not ast.get_docstring(node)]
assert not missing, f"{len(missing)} without a description:\n  " + "\n  ".join(missing[:40])
print(f"1 {len(TREES)} files in the core with {total} functions/classes/methods, {len(OTHER_FILES)} further files with {others} – all described")

# 2) Silent error handling only with a reason
silent, reasoned = [], 0
for path, tree in TREES.items():
    lines = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
            head, body = lines[node.lineno - 1], lines[node.body[0].lineno - 1]
            above = lines[node.lineno - 2].strip() if node.lineno >= 2 else ""
            if "#" in head or "#" in body or above.startswith("#"):
                reasoned += 1
            else:
                silent.append(f"{rel(path)}:{node.lineno}")
assert not silent, "an error is swallowed without saying why (add a comment or report it): " + ", ".join(silent)
print(f"2 {reasoned} errors skipped on purpose, each with a reason")

# 3) Eingabemodelle mit Grenzen
OPEN_TYPES = ("str", "list", "dict", "Any")
unbounded, fields = [], 0
for path, tree in TREES.items():
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and any(getattr(b, "id", "") == "BaseModel" for b in node.bases)):
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            fields += 1
            kind = ast.unparse(item.annotation)
            value = ast.unparse(item.value) if item.value is not None else ""
            if any(t in kind for t in OPEN_TYPES) and "Literal" not in kind and "max_length" not in value:
                unbounded.append(f"{rel(path)}: {node.name}.{ast.unparse(item.target)}")
assert not unbounded, "input field without a length limit (Field(max_length=…)): " + ", ".join(unbounded)
print(f"3 {fields} fields in input models, texts/lists/objects all limited")

# 4) Facade: replaceable names are read at run time through the module
#    Tests and routes replace e.g. core.publish or flow_engine.rand. Whoever fetches the name with `from … import`
#    holds on to the old function and does not notice the replacement.
PATCHED = {"core": {"publish", "all_devices", "merged_devices", "scene_step", "restart_soon", "room_names", "flow_devices"},
           "flow_engine": {"rand", "clock", "_sleep", "http_client", "in_window", "engine"}}
held = []
for path, tree in TREES.items():
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 1 and (node.module or "").split(".")[-1] in PATCHED:
            module = (node.module or "").split(".")[-1]
            names = {a.name for a in node.names} & PATCHED[module]
            if names and path.stem != module:
                held.append(f"{rel(path)}: from .{module} import {', '.join(sorted(names))}")
assert not held, "replaceable name imported by name (read it through the module instead): " + "; ".join(held)
control_src = (APP / "control.py").read_text(encoding="utf-8")
assert "from .core import" not in control_src and "from . import core" in control_src, "control.py must use core as a module"
core_tree = TREES[APP / "core.py"]
facade = {a.asname or a.name for n in core_tree.body if isinstance(n, ast.ImportFrom) and n.module == "control" for a in n.names}
used = {n.attr for path, tree in TREES.items() if path.parent.name == "api" for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "core"}
core_names = {n.name for n in core_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))} \
    | {t.id for n in core_tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)} \
    | {n.target.id for n in core_tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)} | facade
unknown = sorted(used - core_names)
assert not unknown, f"routes use core.<name> that does not exist there: {unknown}"
print(f"4 facade: {len(facade)} names from control reachable through core, {len(used)} used by routes, none held on to")

# 5) Line endings and whitespace
TEXT = (".py", ".js", ".css", ".html", ".json", ".md", ".sh", ".txt", ".webmanifest", ".yml", ".svg")
SKIP = {"__pycache__", "shots", "captures", ".git", "node_modules"}
dirty, checked = [], 0
for folder in ("backend", "tests", "tools", "dev", "public"):
    for path in (ROOT / folder).rglob("*"):
        if not path.is_file() or path.suffix not in TEXT or SKIP & set(path.parts):
            continue
        raw = path.read_bytes()
        checked += 1
        if b"\r" in raw:
            dirty.append(f"{rel(path)}: CR line ending")
        elif path.suffix == ".py" and (b"\t" in raw or any(line != line.rstrip() for line in raw.split(b"\n"))):
            dirty.append(f"{rel(path)}: tab or trailing space")
assert not dirty, "source code not clean:\n  " + "\n  ".join(dirty[:30])
print(f"5 {checked} text files with LF, Python without tabs and without trailing spaces")

# 6) Interface: what backend and scripts must know in common lives in one place each
import re  # noqa: E402

sys.path.insert(0, str(ROOT / "backend"))
from app import core  # noqa: E402

JS = ROOT / "backend" / "app" / "static" / "js"
core_js = (JS / "core.js").read_text(encoding="utf-8")
listed = re.search(r"HO\.LIVE_FIELDS = \[(.*?)\]", core_js, flags=re.S)
assert listed, "HO.LIVE_FIELDS is missing in core.js"
live_js = re.findall(r"'([a-z_]+)'", listed.group(1))
assert sorted(live_js) == sorted(core.LIVE_FIELDS), \
    f"live fields differ – backend {sorted(core.LIVE_FIELDS)}, interface {sorted(live_js)}"
scripts = {path.name: path.read_text(encoding="utf-8") for path in sorted(JS.glob("*.js"))}
copies = [name for name, text in scripts.items() if name != "core.js" and re.search(r"\['on', 'brightness', 'light', 'online'", text)]
assert not copies, f"the list of live fields was copied instead of using HO.LIVE_FIELDS: {copies}"
loose = [name for name, text in scripts.items() if name != "core.js"
         and re.search(r"\.online && \w+\.control_item && !\w+\.locked", text)]
assert not loose, f"controllability checked by hand instead of with HO.canControl: {loose}"
overview = scripts["overview.js"]
blocks = re.findall(r"HO\.blocks\.register\('([a-zA-Z]+)', \{\s*title: '([^']+)', icon: '([a-z-]+)'", overview)
home_blocks = re.search(r"const HOME_BLOCKS = \[(.*)\]", overview)
placed = re.findall(r"\['([a-zA-Z]+)', '#[A-Za-z]+'\]", home_blocks.group(1)) if home_blocks else []
assert len(blocks) >= 5 and sorted(b[0] for b in blocks) == sorted(placed), f"blocks of the home page: registered {blocks}, placed {placed}"
assert "innerHTML = heroHTML" not in overview and "innerHTML = devicesHTML" not in overview, \
    "a block of the home page is rewritten bypassing HO.blocks"
index = (ROOT / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
order = re.findall(r'<script src="/static/js/([\w.]+)\?', index)
assert order.index("core.js") < order.index("blocks.js") < order.index("overview.js"), "blocks.js must load after core.js and before overview.js"
print(f"6 interface: {len(live_js)} live fields as in the backend, HO.canControl in one place, {len(blocks)} blocks of the home page with a contract")

# 7) Interface: every script has a head, every named function from JS_MIN_LINES lines a description –
#    a comment directly above it or on its first line. Recognised are `function name(`, `const name = (…) => {`,
#    `const name = function` and methods `name(…) {`; the end of a function is the closing brace at its indentation.
JS_MIN_LINES = 5
JS_START = re.compile(r"^(\s*)(?:(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>\s*\{\s*$|"
                      r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function\b|(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{\s*$)")
JS_WORDS = {"if", "for", "while", "switch", "catch", "function", "return"}


def js_undescribed(text: str) -> tuple[int, list[str]]:
    """(number of checked functions, "line name" of all functions without a description) of a script."""
    lines = text.split("\n")
    count, missing = 0, []
    for i, line in enumerate(lines):
        found = JS_START.match(line)
        name = found and next((g for g in found.groups()[1:] if g), None)
        if not name or name in JS_WORDS or line.count("{") == line.count("}"):   # one-liner: nothing to describe
            continue
        indent = len(found.group(1))
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() and lines[j].lstrip().startswith(("}", ")"))
                    and len(lines[j]) - len(lines[j].lstrip()) <= indent), i)
        if end - i + 1 < JS_MIN_LINES:
            continue
        count += 1
        above = lines[i - 1].strip() if i else ""
        if not (above.startswith(("//", "/*", "*")) or above.endswith("*/") or "//" in line or "/*" in line):
            missing.append(f"{i + 1} {name}")
    return count, missing


js_total, js_missing, headless = 0, [], []
for name, text in scripts.items():
    if not text.lstrip().startswith(("/*", "//")):
        headless.append(name)
    count, missing = js_undescribed(text)
    js_total += count
    js_missing += [f"{name}:{entry}" for entry in missing]
assert not headless, f"script without a head comment: {headless}"
assert not js_missing, "function of the interface without a description:\n  " + "\n  ".join(js_missing[:40])
print(f"7 {len(scripts)} scripts with a head, {js_total} functions from {JS_MIN_LINES} lines – all described")

print("OK test_style")
