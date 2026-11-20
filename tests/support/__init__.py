"""Shared scaffolding of the ComfyOS tests (not a test, is imported).

    boot.py           isolated environment for a test: own directories, no keyring
    homebase.py       mock Home Base for tests of the core (the Home Base is the plugin `qivicon`)
    world_hue.py      test world "Home Base + Hue Bridge" (linking, lamps, live messages)
    world_scenes.py   test world "light and scenes" (rooms with Hue lamps, effects)
    world_pairing.py  test world "pairing at the Home Base" (catalogue, search, inbox)
    attack.py         security tests: outside world cut off, raw requests, collecting findings
    ui.py             UI tests: app on a free port, browser runs, screenshots, image comparison

Every test runs in a process of its own (tests/run_all.py). A test therefore starts like this:

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # tests/
    from support import boot
    ROOT, tmp = boot.isolate("name")

Only after that is `app` imported – the app reads its paths at import.
"""
