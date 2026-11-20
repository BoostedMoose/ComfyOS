# Template for your own plugin

A complete, runnable example: a virtual lamp that only exists in ComfyOS. For trying things out and as a starting point for a plugin of your own.

## How it becomes your plugin

1. Copy this folder to `<data>/plugins/<your_id>/` (for an installation from the repo: `data/plugins/`).
2. In `manifest.json` set `id` to the folder name and adapt `name`, `description` and `id_prefixes`.
3. In ComfyOS tap **Neu suchen** under *System → Plugins* and **approve** the plugin.
4. In `__init__.py` replace the simulated lamp by real calls – for that, declare under `egress` in the manifest what the plugin talks to.

ComfyOS does not load folders whose name starts with `_`. That is why the template only runs as a copy.

## What the template shows

| Topic | Where |
|---|---|
| Entry point `setup(context)` | end of `__init__.py` |
| Reading settings from the manifest | `devices()` |
| Reporting a device with state and capabilities | `devices()` |
| Checking a command, carrying it out and reporting the new state | `command()` |
| A file in the plugin's own data folder | `_load_state()`, `_save_state()` |
| State for the tile | `status()` |
| An endpoint of its own | `routes()` |
| Tests without ComfyOS | `tests/test_example.py` |

Rules and style for plugins are in [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).
