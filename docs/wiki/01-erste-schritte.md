# Erste Schritte

Vom ersten Aufruf bis zum fertigen Grundriss in fünf Schritten. Rechne mit einer halben Stunde, den Grundriss eingeschlossen.

## 1. ComfyOS öffnen und Passwort festlegen

Öffne ComfyOS im Browser:

| Wo ComfyOS läuft | Adresse |
|---|---|
| Raspberry Pi als Dienst | `http://comfyos/` |
| Rechner (Windows, Linux, macOS) | `http://<IP-des-Rechners>:8000/` |

Beim ersten Aufruf legst du **ein** ComfyOS-Passwort fest. Es hat nichts mit den Passwörtern von Home Base, FRITZ!Box oder Telekom zu tun.

- **Am Gerät selbst** (`localhost`) reicht das Passwort.
- **Von einem anderen Gerät** (Handy, PC) fragt ComfyOS zusätzlich nach dem **Einrichtungscode** (`XXXX-XXXX`). So kann niemand im WLAN dein ComfyOS übernehmen, bevor du es eingerichtet hast. Der Code steht im Konsolenfenster beim Start, beim Pi-Dienst im Log (`journalctl -u comfyos | grep Einrichtungscode`) und in der Datei `setup_code.txt` im Profilordner von ComfyOS (Linux `~/.config/ComfyOS/`, Windows `%APPDATA%\ComfyOS\`).

Der Code gilt nur bis zur Einrichtung und wird danach gelöscht.

## 2. Das erste Plugin einrichten

Geh zu *System → Plugins*. Jede Geräteanbindung hat dort eine Kachel. Richte ein, was du hast – eine Anleitung je Plugin steht unter [Plugins einrichten](03-plugins.md):

- **Hue Bridge:** Adresse eintragen, Link-Taste drücken.
- **FRITZ!Box:** Adresse, Benutzer und Kennwort.
- **Home Base:** Adresse und Passwort der QIVICON-Weboberfläche.
- **Zigbee2MQTT, Tasmota, ESPHome:** Adresse des MQTT-Brokers.

Oben auf jeder Seite zeigt ComfyOS den Zustand aller Plugins: **Online**, solange alles verbunden ist, **Verbindet** kurz nach dem Start, sonst **Fehler**. Antippen führt zu den Plugins.

## 3. Geräte übernehmen

Je nach Plugin erscheinen neue Geräte sofort (Home Base, FRITZ!Box) oder in der Kachel unter „Neu gefunden“ bzw. „Neu auf der Bridge“ (Hue, MQTT, Sonos, Matter). Dort übernimmst du sie einzeln oder alle auf einmal.

## 4. Grundriss zeichnen

Auf der Startseite tippst du im Grundriss auf **Bearbeiten**:

1. Mit **Wand** die Außen- und Innenwände zeichnen. Jede geschlossene Fläche wird ein Raum.
2. Räume antippen und benennen.
3. **Tür**, **Fenster**, **Durchbruch** und **Heizkörper** auf die Wände ziehen.
4. Geräte aus der Leiste „Nicht platziert“ in den Plan ziehen – oder **Geräte verteilen** nutzen: Geräte, deren Raum bekannt ist, landen dann in ihrem Raum.
5. Fensterkontakte auf Fenster, Thermostate auf Heizkörper ziehen: Sie docken an.

Keine Lust zu zeichnen? Kennt ein Plugin schon Räume (etwa die Home Base), bietet ein leerer Grundriss **Räume automatisch anlegen** an: ComfyOS legt dann je Raum ein Rechteck an und setzt die Geräte hinein. Mehr: [Grundriss](05-grundriss.md).

## 5. Räume zuordnen

Ein Gerät gehört zu dem Raum, in dem es im Grundriss steht. Alternativ wählst du den Raum im Gerätemenü. Ist eine Home Base verbunden, schreibt ComfyOS Räume und Zuordnungen auch dorthin – die Magenta-App zeigt dann dieselben Räume.

## Wie es weitergeht

- Adresse im Grundriss-Editor eintragen: Dann zeigt ComfyOS Wetter, Wetterwarnungen und den Schatten nach Sonnenstand.
- [Szenen](06-licht-und-szenen.md) Räumen zuordnen, einen [Heizplan](08-heizung-und-energie.md) anlegen, den ersten [Flow](07-flow.md) bauen.
- ComfyOS auf dem Handy zum Home-Bildschirm hinzufügen. Für unterwegs: [Fernzugriff per VPN](09-fernzugriff-vpn.md).
