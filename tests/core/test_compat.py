"""ComfyOS – old browsers: the interface runs from Safari 13 (iPadOS 13.4). Static check of CSS and scripts.

Promised is Safari 13.1; everything missing there is listed here as forbidden – or has a checked way out.

    1  CSS: nothing Safari 13 does not understand (`inset`, `:is()`, `dvh`, individual transform properties …)
    2  CSS: properties Safari only knows with a prefix are in the same rule with `-webkit-`
    3  Gaps in flex containers: compat.css matches app.css, compat.js recognises the need, no inline gaps
    4  Scripts: no syntax and no functions that do not exist in Safari 13

    python tests/core/test_compat.py [path-to-comfyos]

How this shows in the browser is checked by tests/ui/ui_compat.py (position of all elements with and without flex `gap`).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/ – shared scaffolding (support)
from support import boot  # noqa: E402

ROOT, tmp = boot.isolate("compat")
sys.path.insert(0, str(ROOT / "dev"))
import make_compat_css  # noqa: E402

STATIC = ROOT / "backend" / "app" / "static"
PLUGIN_STATIC = sorted((ROOT / "public" / "plugins").glob("*/static"))
CSS_FILES = [STATIC / "app.css"] + [f for d in PLUGIN_STATIC for f in sorted(d.rglob("*.css"))]
JS_FILES = sorted((STATIC / "js").glob("*.js")) + [STATIC / "sw.js"] + [f for d in PLUGIN_STATIC for f in sorted(d.rglob("*.js"))]


def without_comments(text: str) -> str:
    """Remove comments /* … */, the number of lines stays (for messages)."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def hits(files: list[Path], rules: dict[str, str]) -> list[str]:
    """Places where forbidden patterns were found as "file:line: what"."""
    found = []
    for file in files:
        text = without_comments(file.read_text(encoding="utf-8"))
        for what, pattern in rules.items():
            for m in re.finditer(pattern, text, flags=re.M):
                found.append(f"{file.relative_to(ROOT).as_posix()}:{text.count(chr(10), 0, m.start()) + 1}: {what}")
    return found


# 1) CSS Safari 13 does not understand – a rule with it fails there entirely or in part
CSS_FORBIDDEN = {
    "inset (from Safari 14.1)– write top/right/bottom/left": r"(?<![-\w])inset(-inline|-block)?(-start|-end)?\s*:",
    ":is()/:where() (from Safari 14)": r":(is|where)\(",
    ":has() (from Safari 15.4)": r":has\(",
    "dvh/svh/lvh (from Safari 15.4)": r"\d(dvh|svh|lvh|dvw|svw|lvw)\b",
    "color-mix() (from Safari 16.2)": r"color-mix\(",
    "@container (from Safari 16)": r"@container\b",
    "@layer (from Safari 15.4)": r"@layer\b",
    "nested rule with & (from Safari 16.5)": r"^\s*&",
    "translate/scale/rotate as a property of its own (from Safari 14.1)– write transform": r"(?<![-\w])(translate|scale|rotate)\s*:",
    "content-visibility (from Safari 18)": r"content-visibility\s*:",
    "text-wrap (from Safari 17.4)": r"text-wrap\s*:",
    "inline-size/block-size (from Safari 15)– write width/height": r"(?<![-\w])(min-|max-)?(inline|block)-size\s*:",
    "margin-/padding-inline/block (from Safari 14.1)": r"(?<![-\w])(margin|padding)-(inline|block)\b",
}
found = hits(CSS_FILES, CSS_FORBIDDEN)
assert not found, "CSS Safari 13 does not understand:\n  " + "\n  ".join(found)
css = without_comments((STATIC / "app.css").read_text(encoding="utf-8"))
ratio = len(re.findall(r"(?<![-\w(])aspect-ratio\s*:", css))
assert ratio == len(re.findall(r"@supports not \(aspect-ratio", css)), \
    "aspect-ratio (from Safari 15) needs a way out in every place: `@supports not (aspect-ratio: 1)`"
print(f"1 CSS: {len(CSS_FORBIDDEN)} bans kept, aspect-ratio {ratio}× with a way out")

# 2) Properties Safari only knows with -webkit-: the prefix is in the same rule
NEEDS_PREFIX = ("user-select", "backdrop-filter", "appearance")
missing = []
for file in CSS_FILES:
    text = without_comments(file.read_text(encoding="utf-8"))
    for rule in re.findall(r"[^{}]*\{[^{}]*\}", text):
        for prop in NEEDS_PREFIX:
            if re.search(rf"(?<![-\w]){prop}\s*:", rule) and f"-webkit-{prop}" not in rule:
                missing.append(f"{file.name}: {rule.split('{')[0].strip()[:60]} – {prop} without -webkit-{prop}")
assert not missing, "prefix missing:\n  " + "\n  ".join(missing)
print(f"2 CSS: {', '.join(NEEDS_PREFIX)} are everywhere also given with -webkit-")

# 3) Gaps in flex containers (Safari up to 14.0 does not know `gap` there)
wanted = make_compat_css.build((STATIC / "app.css").read_text(encoding="utf-8"))
current = (STATIC / "compat.css").read_text(encoding="utf-8")
assert current == wanted, "compat.css does not match app.css or the templates – run `python dev/make_compat_css.py`"
rules = current.count("{") - current.count("@media")
assert rules > 300 and "html.no-flexgap .row >" in current and "> .fg-a" in current
index = (STATIC / "index.html").read_text(encoding="utf-8")
scripts = re.findall(r'<script src="/static/js/([\w.]+)\?', index)
assert scripts[0] == "compat.js", "compat.js must load as the first script"
assert "compat.css" not in index, "compat.css is only loaded by those who need it (compat.js mounts it) – not in index.html"
compat_js = (STATIC / "js" / "compat.js").read_text(encoding="utf-8")
assert "no-flexgap" in compat_js and "flexGapWorks" in compat_js and "fg-a" in compat_js and "fg-b" in compat_js
assert "/static/compat.css" in compat_js and "loadCompatCss()" in compat_js, "compat.js must load compat.css"
# inline gaps get no fallback rule: gaps belong in classes (.row.g6, .row.g8 …)
INLINE_OK = {"margin-top:16px;flex-wrap:wrap;gap:8px"}     # head of the home page: the gap of .section-head applies there then
inline = []
for file in JS_FILES:
    for style in re.findall(r'style="([^"]*)"', file.read_text(encoding="utf-8")):
        if re.search(r"(?<![-\w])(gap|row-gap|column-gap)\s*:", style) and "grid" not in style and style not in INLINE_OK:
            inline.append(f"{file.name}: style=\"{style[:70]}\"")
assert not inline, "gap as an inline style (use a class such as .row.g8 instead):\n  " + "\n  ".join(inline)
print(f"3 flex gaps: compat.css up to date ({rules} rules), compat.js loads first and mounts it only when needed, no inline gaps")

# 4) Scripts: syntax and functions Safari 13 does not know (the rule of the project is ES2017 anyway)
JS_FORBIDDEN = {
    "optional chaining ?.": r"\?\.(?![\d])",
    "?? (Nullish)": r"\?\?",
    "logical assignment ||= &&= ??=": r"(\|\||&&|\?\?)=",
    "lookbehind in a regular expression (from Safari 16.4)": r"\(\?<[=!]",
    "private field #name (from Safari 14.1)": r"\bthis\.#\w",
    "class field `static x =` (from Safari 14.1)": r"^\s*static\s+\w+\s*=",
    "replaceChildren (from Safari 14)": r"\.replaceChildren\(",
    "Array.at() (from Safari 15.4)": r"\.at\(-?\d",
    "structuredClone (from Safari 15.4)": r"\bstructuredClone\(",
    "crypto.randomUUID (from Safari 15.4)": r"\brandomUUID\(",
    "matchMedia().addEventListener (from Safari 14)– use addListener": r"matchMedia\([^)]*\)\s*\.addEventListener",
    "replaceAll (ES2021)": r"\.replaceAll\(",
    "Object.hasOwn (from Safari 15.4)": r"Object\.hasOwn\(",
    "dialog.showModal (from Safari 15.4)": r"\.showModal\(",
    "toSorted/toReversed/findLast (from Safari 15.4/16)": r"\.(toSorted|toReversed|toSpliced|findLast|findLastIndex)\(",
    "BigInt (from Safari 14)": r"\bBigInt\(|\b\d+n\b",
    "AbortSignal.timeout (from Safari 16)": r"AbortSignal\.timeout",
    "canvas filter ctx.filter (from Safari 18)": r"\bctx\.filter\s*=",
    "OffscreenCanvas (from Safari 16.4)": r"\bOffscreenCanvas\b",
    "roundRect (from Safari 16)": r"\.roundRect\(",
    "requestIdleCallback (not available in Safari)": r"\brequestIdleCallback\b",
    "scrollTo({…}) (from Safari 14)– use HO.toTop()": r"\bscrollTo\(\s*\{",
    "Promise.any (from Safari 14)": r"Promise\.any\(",
    "WeakRef (from Safari 14.1)": r"\bWeakRef\b",
    "Intl.RelativeTimeFormat/ListFormat (from Safari 14/14.1)": r"Intl\.(RelativeTimeFormat|ListFormat|Segmenter|DisplayNames)",
}
found = hits(JS_FILES, JS_FORBIDDEN)
assert not found, "script that does not run in Safari 13:\n  " + "\n  ".join(found)
print(f"4 scripts: {len(JS_FILES)} files, {len(JS_FORBIDDEN)} bans kept")

print("OK test_compat")
