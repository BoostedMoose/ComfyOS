# Architektur

ComfyOS soll für eine einzelne Person baubar und betreibbar bleiben. Daraus folgt ein bewusst schlichter Aufbau: solide nach Stand der Technik, aber ohne Teile, die man nicht allein warten kann.

## Stack

| Teil | Technik |
|---|---|
| Backend | Python 3.10+, FastAPI, httpx |
| Oberfläche | reines JavaScript (ES2017) und CSS, kein Framework, kein Build-Schritt |
| Daten | JSON- und JSONL-Dateien unter `data/` und `logs/`, atomar geschrieben, keine Datenbank |
| Live | ein Server-Sent-Events-Strom für die ganze App |
| Beiprogramm | Node.js nur für das Matter-Plugin, eingesperrt und vom Plugin gesteuert |

## Ordner

```
backend/app/          Core: App, Router, Zustand, Schaltwege, Stores, Flow-Engine, Plugin-Lader
backend/app/api/      HTTP-Routen
backend/app/static/   Oberfläche (index.html, js/, app.css, flow_nodes.json)
public/plugins/       mitgelieferte Plugins und die Vorlage _template/
public/sdk/           plugin_api.py (die einzige Schnittstelle für Plugins) und Testhilfen
tests/                Tests nach Thema: core, flow, plugins, security, ui
dev/                  Testskript, Messwerkzeuge, Bereichs-Doku (dev/docs/)
tools/                Release, Secret-Scan, Passwort zurücksetzen, Screenshots
```

## Prinzipien

**Der Core kennt kein Plugin beim Namen.** Plugins importieren nur `plugin_api` und nie einander. Was ein Plugin darf, steht in seinem Manifest: Netzwerkziele, Geheimnisse, Zugriff auf fremde Geräte, Beiprogramm. ComfyOS setzt das durch.

**Fähigkeiten statt Gerätetypen.** Jedes Gerät trägt eine Liste dessen, was es kann (`caps`). Oberfläche, Widgets, Flows, Szenen und Matter fragen danach, nie nach dem Plugin. Ein neues Plugin funktioniert deshalb überall, ohne dass der Core angepasst wird.

**Ein Schaltweg.** Jeder Befehl – aus der Oberfläche, einem Flow, einer Szene, Matter oder einem Plugin – läuft durch dieselbe Stelle im Core. Dort greifen Gerätesperre und Ereignisprotokoll.

**Fassade.** `core.py` ist die Schnittstelle des Backends. Befehle, Licht und Szenen (`control.py`) und die Flow-Bausteine (`flow_nodes.py`) hängen dahinter.

**Kein Umbau alter Daten.** Datendateien tragen ein Versionsfeld. Ändert sich ein Format, wird nichts konvertiert: Unpassendes wird in der Oberfläche als „veraltet“ gekennzeichnet und neu angelegt.

**Kein Nach-Hause-Telefonieren.** Jede Verbindung ins Internet steht in einem Register (`egress.py`) mit Ziel, Zweck, Daten und Auslöser und erscheint in der Oberfläche. Plugins öffnen keine eigenen Verbindungen, sondern bekommen sie vom Core. Der Browser lädt nichts von Dritten (CSP `default-src 'self'`).

**Basic ist nie eine Sperre.** Lizenzfehler bedeuten Basic. Gates sind monoton und verändern nie Daten.

## Datenfluss

```
Hue Bridge · FRITZ!Box · Home Base · MQTT-Broker · Sonos · …
                         │
                         ▼
Plugins        devices() · command() · device_changed() · button_pressed()
                         │
                         ▼
Core           Gerätestand zusammenführen (ein Gerät, auch aus zwei Quellen)
               Schaltweg mit Gerätesperre · Szenen · Heizplaner · Flow-Engine
               Stores (JSON) · Verlauf · Ereignisprotokoll · Egress-Register
                         │
           ┌─────────────┴──────────────┐
           ▼                            ▼
REST-API + Live-Strom (SSE)      Matter-Bridge (Plugin)
           │                            │
           ▼                            ▼
Oberfläche im Browser            Alexa · Apple Home · Google Home
```

## Invarianten

Einige Regeln gelten überall und werden von Tests bewacht. Eine Lücke wird behoben und bekommt einen eigenen Prüfpunkt.

| Regel | Test |
|---|---|
| Jeder Schaltweg prüft die Gerätesperre | `core/test_device_lock` |
| Jede ausgehende Verbindung läuft über das Egress-Register | `security/test_privacy` |
| Jede Route verlangt Anmeldung (bis auf eine feste Liste) | `security/test_access` |
| Secrets stehen nie in Logs, Antworten oder Backups | `security/test_secrets` |
| Datendateien nur atomar und mit festem Format | `core/test_stores` |
| Hintergrundaufgaben nur unter Aufsicht | `core/test_tasks` |
| Ein fehlerhaftes Plugin reißt nichts mit | `plugins/test_plugins` |
| Nichts im Frontend, was Safari 13 fehlt | `core/test_compat` |
| Die Demo-Wohnung berührt nichts Echtes | `core/test_demo` |

## Demo-Wohnung

Die Demo ist kein globaler Modus, sondern eine Markierung der Browser-Sitzung. Für deren Anfragen lenkt der Core Dateizugriffe nach `data/demo/` um, zeigt nur ein eingebautes Demo-Plugin und liefert Beispielwetter. Alles andere – Flow-Engine, Plugins, andere Sitzungen – läuft unberührt für die echte Wohnung weiter.

## Weiterlesen

Die Bereichs-Doku in `dev/docs/` beschreibt jeden Teil im Detail (englisch):

| Bereich | Datei |
|---|---|
| Stores, Routen, Hintergrundaufgaben, Demo | `dev/docs/backend.md` |
| Plugin-System und alle Plugins | `dev/docs/plugins.md` |
| Grundriss, Kamera, Wetter | `dev/docs/floorplan.md` |
| Licht, Szenen, Gerätesperre | `dev/docs/lights-scenes.md` |
| Flow und Push | `dev/docs/flow.md` |
| Lizenz, Basic/Pro | `dev/docs/license.md` |
| Startseite, Widgets, alte Browser | `dev/docs/interface.md` |
