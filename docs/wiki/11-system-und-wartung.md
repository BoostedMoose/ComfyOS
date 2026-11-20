# System und Wartung

Die Seite **System** hat zwei Reiter: **Einstellungen** und **Plugins**. Die Kacheln lassen sich in 1, 2 oder 3 Spalten anordnen und mit **Anordnen** verschieben.

## HTTPS

Unter *System → Erreichbarkeit* schaltest du HTTPS ein. ComfyOS erzeugt ein eigenes Zertifikat und läuft dann auf Port 443 (Pi-Dienst) bzw. 8443. Aufrufe über HTTP werden umgeleitet.

Der Browser warnt beim ersten Aufruf, weil das Zertifikat selbst ausgestellt ist. Das bestätigst du einmal je Gerät. HTTPS braucht es für Push-Benachrichtigungen und für den Ton des PCs auf Sonos.

## Benachrichtigungen

Unter *System → Benachrichtigungen* schaltest du Push für dieses Gerät ein. Danach erreichen dich Nachrichten aus Flows auch dann, wenn ComfyOS nicht geöffnet ist. Voraussetzungen: HTTPS, und auf iPhone und iPad muss ComfyOS zum Home-Bildschirm hinzugefügt sein.

## Als App installieren

ComfyOS lässt sich zum Home-Bildschirm hinzufügen (im Browser-Menü „Zum Home-Bildschirm“ bzw. „App installieren“). Es öffnet sich dann ohne Adressleiste wie eine eigene App.

## Backup und Umzug

Unter *System → Backup & Restore*:

- **Backup:** Grundriss, Räume, Szenen, Effekte, Heizroutinen, Flows, Widgets, Gerätesperren, Verbrauchszähler und Einstellungen als ZIP-Datei. Einspielen stellt alles wieder her.
- **Umzugspaket:** nimmt zusätzlich die Zugangsdaten deiner Plugins mit, verschlüsselt mit einem Passwort von mindestens 8 Zeichen. Gedacht für den Wechsel auf neue Hardware.

Lizenz und Push-Schlüssel ziehen nie mit um. Matter-Kopplungen auch nicht: Nach einem Umzug koppelst du Alexa & Co. neu.

## Update

Unter *System → Update* sucht **Nach Updates suchen** das neueste Release. ComfyOS prüft die Datei, legt vorher eine Sicherung an und kehrt automatisch zur alten Version zurück, wenn etwas schiefgeht. Ohne Internet lässt sich ein Update-Paket auch hochladen.

## Systemauslastung

Die Kachel **Systemauslastung** zeigt CPU, Arbeitsspeicher und Speicherplatz, solange die Seite offen ist. Ein schraffiertes Stück in jedem Balken zeigt den Anteil von ComfyOS selbst.

## Ältere Geräte und Wanddisplays

ComfyOS läuft in aktuellen Browsern und ab Safari 13 (iPadOS 13). Kommt ein Gerät mit der Darstellung nicht mit, zeichnet ComfyOS dort sparsamer: einfachere Schatten, keine Unschärfe, ruhigere Wettereffekte.

Ganz unten unter *System → Einstellungen* führt **Diagnose dieses Geräts** zu einer Seite, die zeigt, was der Browser kann und wie flüssig er zeichnet. Dort wählst du die Leistungsstufe je Gerät: automatisch, voll oder sparsam. Ist im Betriebssystem „Bewegung reduzieren“ an, zeigt ComfyOS ruhige Bilder statt Animationen.

## Raspberry Pi als Dienst

Unter *System → Serversetup* richtest du auf dem Pi per Knopfdruck den Systemdienst (Autostart, Port 80) und den Netzwerknamen `comfyos` ein – und nimmst beides auch wieder zurück.
