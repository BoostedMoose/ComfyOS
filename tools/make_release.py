"""Build a release: write MANIFEST and create comfyos-<version>.zip (folder 'comfyos/').

    python tools/make_release.py [TARGET FOLDER]

Runs automatically through a GitHub Action on every tag v<version> (.github/workflows/release.yml).
By hand: put the ZIP next to the installed folder and run the installation in ComfyOS under
System -> Update.
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.updater import write_manifest  # noqa: E402


def main():
    """Build the release ZIP: write MANIFEST and pack all program files under `comfyos/`. The target is the folder
    from the first argument, otherwise the folder next to the repo."""
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent
    files = write_manifest(ROOT)
    version = (ROOT / "VERSION").read_text().strip()
    target = out_dir / f"comfyos-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(ROOT / rel, "comfyos/" + rel)
    print(f"{target}  ({len(files)} Dateien, Version {version})")


if __name__ == "__main__":
    main()
