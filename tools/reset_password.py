"""Reset the ComfyOS password (if you can no longer sign in).

Run as the user ComfyOS runs under (usually your normal
user, not root) – otherwise the change ends up in the wrong profile:

    python3 tools/reset_password.py            set a new password directly
    python3 tools/reset_password.py --setup    back to the first setup (with a setup code)

Both variants sign out all devices. Home Base and Telekom credentials,
floor plan, heating schedules and logs stay unchanged. A restart of ComfyOS is
not needed – the password is read fresh on every login.
"""
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import auth  # noqa: E402


def main() -> int:
    """Reset the password of ComfyOS: set a new one or (with --setup) delete it, so that the setup asks for it anew.
    All sessions are signed out along the way."""
    ap = argparse.ArgumentParser(description="ComfyOS-Passwort zurücksetzen")
    ap.add_argument("--setup", action="store_true",
                    help="Passwort löschen; ComfyOS fragt beim nächsten Aufruf nach einem neuen (Einrichtungscode nötig, außer am Pi selbst)")
    args = ap.parse_args()

    print(f"ComfyOS-Profil: {auth.AUTH_FILE.parent}")
    if not auth.AUTH_FILE.exists():
        print("Hier ist kein ComfyOS-Passwort gespeichert.\n"
              "Läuft ComfyOS evtl. unter einem anderen Benutzer? Dann mit dessen Rechten starten, z. B.:\n"
              f"  sudo -u <benutzer> -H python3 {Path(__file__).resolve()}")
        return 1

    if args.setup:
        with auth._lock:
            data = auth._load()
            data.pop("password", None)
            data["sessions"] = {}
            auth._save(data)
        code = auth.setup_code()
        print("Passwort gelöscht, alle Geräte abgemeldet.\n"
              "ComfyOS im Browser öffnen und ein neues Passwort festlegen:\n"
              "  - am Pi selbst (http://localhost:8000 bzw. http://localhost/): ohne Code\n"
              f"  - von Handy/PC im Heimnetz: Einrichtungscode  {code}")
        return 0

    while True:
        pw1 = getpass.getpass("Neues ComfyOS-Passwort (mind. 6 Zeichen): ")
        if len(pw1) < 6:
            print("Zu kurz.")
            continue
        if pw1 != getpass.getpass("Wiederholen: "):
            print("Stimmt nicht überein – nochmal.")
            continue
        break
    auth.set_password(pw1)
    n = auth.revoke_all()
    print(f"Passwort gesetzt. {n} Anmeldung(en) beendet – bitte überall neu anmelden.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen – nichts geändert.")
        sys.exit(1)
