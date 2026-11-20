# Mitentwickeln und Tests

## Wie gearbeitet wird

ComfyOS wird mit KI-Unterstützung entwickelt. Damit das über viele Versionen sauber bleibt, steht das Wissen in Dateien, nicht in Chatverläufen. Ein Leitfaden für die KI fasst die Regeln zusammen, die überall gelten; die wichtigsten stehen unten. Alles andere liegt hier:

| Datei | Inhalt |
|---|---|
| `dev/docs/*.md` | Aufbau und Regeln je Bereich (englisch) |
| `dev/README.md` | Testsuite, Prüfpunkte je Test, Werkzeuge (englisch) |
| `public/CONTRIBUTING.md` | Regeln und Stil für Plugins (englisch) |

Vor einer Änderung liest man die Bereichs-Doku der betroffenen Dateien. Wer etwas ändert, zieht die Doku im selben Schritt nach.

## Die wichtigsten Regeln

- **Sprache:** Code, Kommentare, Tests und Entwickler-Doku sind englisch. Alles, was Nutzer sehen, ist deutsch (Oberfläche, Fehlertexte, Ereignisprotokoll).
- **Keine neue Abhängigkeit, keine neue Verbindung ins Internet, keine Route ohne Anmeldung** ohne vorherige Entscheidung.
- **Keine Funktion still entfernen.** Nur gegen gleichwertigen Ersatz.
- **Keine echten Daten** in Code, Tests, Logs, Screenshots oder Commits. Beispieldaten kommen aus der Demo-Wohnung und den Testwelten.
- **Frontend bis Safari 13:** keine Syntax neuer als ES2017, neue Browser-APIs nur mit Ausweichweg.
- **Erst messen, dann optimieren** (`dev/measure.py`, Ergebnisse in `dev/perf.md`).

## Tests

Die Testsuite läuft gegen nachgebaute Geräte (Home Base, Hue Bridge, MQTT-Broker, Sonos-Haushalt, FRITZ!Box) und braucht keine echte Hardware.

| Ordner | Prüft |
|---|---|
| `tests/core/` | Stores, Geräte, Szenen, Heizplaner, Lizenz, Widgets, Demo, Stil, alte Browser |
| `tests/flow/` | Flow-Engine, Bausteine, Anschlüsse |
| `tests/plugins/` | Plugin-Lader, Zusammenspiel der Plugins, Matter mit echtem Node |
| `tests/security/` | Angriffe nach Angriffsfläche: ohne Anmeldung, fremde Webseite, Dateien, Secrets, SSRF, Eingaben, Fuzzing |
| `tests/ui/` | Oberfläche mit Playwright, je Seite, mit Screenshots |

```bash
# Backend (Linux/Pi)
bash dev/test.sh                          # alles
bash dev/test.sh --group security         # eine Gruppe
bash dev/test.sh --only test_weather      # einzelne Tests

# Oberfläche (mit Playwright)
python tests/run_all.py --ui-only
python tests/run_all.py --only ui_home,ui_widgets
```

Jeder Test läuft in einem eigenen Prozess mit eigenem Datenordner. `tests/security/` muss immer vollständig grün sein: Funde werden behoben, nie im Test ausgenommen.

## Bilder für README und Wiki

`tools/readme_screenshot.py` erzeugt alle Bilder unter `docs/` aus der Demo-Wohnung, ohne echte Daten und ohne Internet (braucht Playwright). Ändert sich das Aussehen sichtbar, werden die Bilder neu erzeugt und vor dem Commit angesehen.

## Releases

Eine Version ist ein Release: `VERSION` hochzählen, README und Roadmap nachziehen, gezielte Tests plus die vollständigen Sicherheitstests, dann Commit und Tag `v<version>`. Die GitHub Action baut daraus das Paket.
