# Fehlerbehebung

## Anmeldung

**Das Passwort wird nicht angenommen.**
Browser-Passwortmanager verwechseln gern die Passwortfelder der Plugins (Home Base, FRITZ!Box, Telekom) mit der ComfyOS-Anmeldung. Prüfe den gespeicherten Eintrag.

**Passwort vergessen.**
Auf dem Rechner bzw. Pi, auf dem ComfyOS läuft, im Programmordner:

```bash
# A) neues Passwort direkt setzen
python3 tools/reset_password.py

# B) zurück zur Ersteinrichtung, dann im Browser neu festlegen (mit Einrichtungscode)
python3 tools/reset_password.py --setup
```

Als der Benutzer ausführen, unter dem ComfyOS läuft. Findet das Skript kein Passwort, läuft ComfyOS unter einem anderen Benutzer: `sudo -u <benutzer> -H python3 tools/reset_password.py`. Beide Varianten melden alle Geräte ab, Grundriss, Pläne und Zugangsdaten der Plugins bleiben erhalten.

**„Zugriff über die Adresse … ist nicht freigegeben“.**
ComfyOS antwortet nur unter IP-Adressen, Namen im Heimnetz und eingetragenen Namen. Öffne ComfyOS einmal über die IP-Adresse und trage den Namen unter *System → Sicherheit → Weitere Adressen* ein.

## Verbindung

**Oben steht „Fehler“.**
Mindestens ein eingeschaltetes Plugin ist nicht verbunden. Fahr mit der Maus über die Anzeige oder tippe darauf: Die Plugin-Kachel sagt, woran es liegt. Kurz nach dem Start steht dort bis zu 45 Sekunden „Verbindet“ – das ist normal.

**Ein Gerät zeigt „Kein Kontakt“.**
Das Plugin erreicht das Gerät nicht. Bei Funkgeräten ist oft die Batterie leer oder das Gerät zu weit von der Bridge entfernt. Die Bridge bzw. Box meldet das, ComfyOS zeigt es nur an.

**Die Hue Bridge meldet sich nicht.**
Adresse prüfen, die Bridge braucht eine feste IP im Router. Beim Koppeln die Link-Taste drücken, wenn die Kachel darum bittet.

**Sonos-Lautsprecher werden nicht gefunden.**
In der Sonos-App *Konto → Datenschutz & Sicherheit → UPnP* einschalten. Hat der Lautsprecher eine neue Adresse bekommen, hilft **Lautsprecher suchen** oder eine feste Adresse im Router.

**Alexa findet die Matter-Bridge nicht oder lehnt sie ab.**
Die Bridge ist nicht zertifiziert, und einzelne Echo-Modelle lehnen solche Bridges ab. Apple Home und Google Home koppeln in der Regel. Getrennte Netze (VLAN) brauchen IPv6.

**Die FRITZ!Box lehnt die Anmeldung ab.**
Benutzer und Kennwort prüfen, der Benutzer braucht das Recht „Smart Home“. Nach einer Ablehnung wartet ComfyOS mindestens eine Minute, bevor es neu versucht.

## Anzeige

**Die Seite ruckelt auf einem älteren Tablet.**
*System → Einstellungen → Diagnose dieses Geräts* öffnen und die Leistungsstufe auf „sparsam“ stellen.

**Wetter und Schatten fehlen im Grundriss.**
Im Grundriss-Editor eine Adresse eintragen und „Außentemperatur und Wetter anzeigen“ einschalten.

**Push-Benachrichtigungen kommen nicht an.**
Push braucht HTTPS. Auf iPhone und iPad muss ComfyOS zum Home-Bildschirm hinzugefügt sein, und die Benachrichtigungen müssen in dieser App eingeschaltet sein.

## Flows

**Ein Flow löst nicht aus.**
Im Editor den **▶-Knopf** am Auslöser drücken und den Weg verfolgen: Welche Bedingung zeigt „Nein“? Ist der Flow eingeschaltet? Ist ein Zielgerät gesperrt? Das Ereignisprotokoll nennt gesperrte Geräte.

**Ein Flow ist „veraltet“.**
Er stammt aus einem älteren Format. ComfyOS wandelt alte Daten nie um, sondern kennzeichnet sie. Lege den Flow neu an, den alten kannst du dann löschen.
