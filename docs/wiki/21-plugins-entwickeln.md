# Plugins entwickeln

Ein Plugin bindet Geräte oder einen Hub an ComfyOS. Es ist ein Ordner mit einem Manifest und etwas Python – ohne Build-Schritt und ohne Zugriff auf das Innere von ComfyOS. Die verbindlichen Regeln stehen in [`public/CONTRIBUTING.md`](../../public/CONTRIBUTING.md) (englisch). Diese Seite gibt den Überblick.

## In fünf Minuten

1. `public/plugins/_template/` nach `data/plugins/<deine_id>/` kopieren.
2. In `manifest.json` die `id` auf den Ordnernamen setzen und eigene `id_prefixes` wählen.
3. In ComfyOS unter *System → Plugins* auf **Neu suchen** tippen. Das Plugin erscheint als Kachel.
4. **Freigeben und einschalten.** ComfyOS fragt nach dem Passwort, denn ein Plugin ist Programmcode.
5. Die simulierte Lampe der Vorlage durch echte Aufrufe ersetzen.

## Aufbau

```
mein_plugin/
  manifest.json        Steckbrief: id, Version, Netzwerkziele, Einstellungen, Geheimnisse
  __init__.py          setup(context) und die Plugin-Klasse
  client.py            (optional) Verbindung zum Gerät oder Hub
  translate.py         (optional) reine Umrechnungen, ohne Netzwerk
  routes.py            (optional) eigene Endpunkte für die Kachel
  static/settings.js   (optional) eigener Inhalt der Kachel
  tests/test_*.py      Tests, die ohne ComfyOS laufen
  README.md            was das Plugin kann und braucht
```

Das Herz ist eine Klasse mit wenigen Methoden:

```python
from plugin_api import Device, Plugin, PluginContext


class MyPlugin(Plugin):
    async def start(self) -> None: ...                      # verbinden
    async def stop(self) -> None: ...                       # aufräumen
    async def devices(self) -> list[Device]: ...            # alle bekannten Geräte
    async def command(self, device_id, state, source): ...  # schalten
    def status(self) -> dict: ...                           # Zustand für die Kachel


def setup(context: PluginContext) -> Plugin:
    return MyPlugin(context)
```

## Was ein Plugin bekommt

Über `context` erhält das Plugin alles, was es braucht – und nur das:

| Bedarf | Weg |
|---|---|
| Einstellungen und Passwörter | `context.settings`, `context.secrets` |
| HTTP | `context.http("<ziel>")` mit einem im Manifest erklärten Ziel |
| TCP, UDP, Gerätesuche | `context.open_stream(…)`, `context.open_datagram(…)`, `context.discover(…)` |
| Eigene Dateien | `context.data_dir` |
| Änderungen melden | `context.device_changed(id, werte)`, `context.button_pressed(id, taste, druck)` |
| Fremde Geräte schalten | `context.command(…)` – nur mit Erlaubnis im Manifest |

## Die wichtigsten Regeln

1. **Netzwerk nur über den Context**, nie eigene Clients, Sockets oder Bibliotheken wie `requests` oder `paho`. Ein Protokoll, das nicht HTTP ist, spricht das Plugin selbst über den Stream (Vorbild: `plugins/mqtt/protocol.py`).
2. **Ziele im Manifest erklären.** `"scope": "lan"` heißt: nur Heimnetz. Ein Ziel im Internet erscheint mit Daten und Auslöser in der Oberfläche.
3. **Zugangsdaten nur in `context.secrets`** und nur an Adressen im Heimnetz (`context.is_local_host`).
4. **Geräte melden, was sie wirklich wissen.** ComfyOS erfindet keine Werte. Erreichbarkeit (`online`) aus der Quelle, Tastendrücke einmal und nur, wenn sie passieren.
5. **Verständliche Fehler** auf Deutsch: `PluginError` für „kann das nicht“, `DeviceUnreachable` für „antwortet nicht“.
6. **Nichts blockieren:** Netzwerk ist `async`.
7. **Kein eigener Prozess.** Braucht ein Plugin wirklich ein fremdes Programm, dann als eingesperrtes Beiprogramm über `context.start_helper` (Vorbild: Matter).

## Gut lesbar schreiben

Alle Plugins sollen sich gleich lesen: sprechende Namen, eine Docstring für jede Funktion, kurze Kommentare, wo etwas nicht offensichtlich ist. Wer ein Plugin nicht kennt, soll eine Datei von oben nach unten lesen und dann wissen, was sie tut. Das hilft Menschen beim Review – und Sprachmodellen beim Erweitern im selben Stil.

## Testen

Tests eines Plugins laufen ohne ComfyOS, mit `FakeContext` aus `public/sdk/testing.py` und einem nachgebauten Gerät:

```bash
python public/plugins/<id>/tests/test_<id>_plugin.py
```

## Vorbilder

| Plugin | Zeigt |
|---|---|
| `_template` | das Minimum, lauffähig |
| `network` | kleines Plugin mit eigener Kachel |
| `hue` | HTTP, Event-Stream, Kopplung |
| `mqtt` | eigenes Protokoll über TCP, Discovery |
| `fritzbox` | Abfrage im Takt, Anmeldung mit Challenge, Taster |
| `sonos` | UDP-Suche, Medien, Aktionen |
| `matter` | eingesperrtes Beiprogramm |

Plugins und SDK stehen unter der Apache-2.0-Lizenz.
