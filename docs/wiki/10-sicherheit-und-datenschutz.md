# Sicherheit und Datenschutz

## Anmeldung

ComfyOS hat **ein** Passwort, das du beim ersten Aufruf festlegst (siehe [Erste Schritte](01-erste-schritte.md)).

- **Fehlversuche:** Nach fünf falschen Versuchen ist die Anmeldung von diesem Gerät 60 Sekunden gesperrt. Bei Wiederholung verdoppelt sich die Zeit, bis höchstens eine Stunde.
- **Angemeldet bleiben:** Ein Gerät bleibt angemeldet, solange es ComfyOS mindestens alle 30 Tage nutzt.
- **Sitzungen:** Unter *System → Sicherheit* siehst du alle angemeldeten Geräte und meldest einzelne ab. Ein Passwortwechsel meldet alle anderen ab.
- **Heikle Aktionen** wie die Freigabe eines Plugins fragen das Passwort erneut ab.

## Schutz vor Angriffen

- **Fremde Webseiten** können ComfyOS nicht fernsteuern. ComfyOS prüft Host und Herkunft jeder Anfrage und setzt strenge Sicherheits-Header. Deshalb antwortet es nur unter IP-Adressen, Namen im Heimnetz (`comfyos`, `*.local`, `*.fritz.box` …) und Namen, die du unter *System → Sicherheit → Weitere Adressen* einträgst.
- **Zugangsdaten bleiben im Heimnetz.** Die Passwörter von Home Base, FRITZ!Box und Hue Bridge gehen nur an Adressen im Heimnetz – auch wenn sie aus einem Backup stammen.
- **Plugins sind eingehegt.** Ein Plugin spricht nur mit den Zielen, die sein Manifest erklärt, und erreicht fremde Geräte nur mit erklärter Erlaubnis. Ein neues Plugin läuft erst nach deiner Freigabe.
- **Zugangsdaten** liegen im Schlüsselbund des Systems oder in einer Datei, die nur der ComfyOS-Benutzer lesen darf. In Logs, Exporten und Antworten sind Passwörter geschwärzt.

**Von unterwegs:** nur per VPN, nie per Portfreigabe. Siehe [Fernzugriff per VPN](09-fernzugriff-vpn.md).

## Datenschutz

ComfyOS sendet **keine** Telemetrie, keine Statistik und keine Absturzberichte. Der Browser lädt nichts von fremden Servern, keine Schriften, keine Skripte, keine Bilder.

Eine frische Installation baut **keine einzige** Verbindung ins Internet auf. Nur diese Funktionen verlassen das Heimnetz, jeweils nur, wenn du sie nutzt. Die aktuelle Liste mit Zustand steht in ComfyOS unter *System → Externe Verbindungen*.

| Funktion | Ziel | Gesendet | Wann |
|---|---|---|---|
| Wetter und Sonnenzeiten | Open-Meteo | Postleitzahl bzw. Ort (nie die Straße), Koordinaten auf ca. 1 km gerundet | mit Adresse und eingeschaltetem Wetter |
| Amtliche Wetterwarnungen | Deutscher Wetterdienst | Koordinaten auf ca. 1 km gerundet | zusätzlich mit eingeschalteten Warnungen |
| Updates | GitHub | installierte Version, IP-Adresse | nur auf Knopfdruck |
| Node.js für Matter | nodejs.org | IP-Adresse | einmalig beim Einrichten von Matter |
| Push-Benachrichtigungen | Push-Dienst des Browser-Herstellers (Apple, Google, Mozilla, Microsoft) | verschlüsselte Nachricht, die der Dienst nicht lesen kann | wenn ein Flow eine Push-Nachricht sendet |
| Flow: HTTP-Aufruf | von dir eingetragen | was du im Baustein einträgst | wenn der Flow auslöst |
| Gerätekatalog beim Anlernen | Telekom | Katalog-Zugang der Home Base, gewähltes Gerät | nur im Anlern-Assistenten |
| Magenta-Sync | Telekom | Telekom-Login, Heizpläne, Zuhause/Abwesend | nur wenn eingerichtet |
| Lizenzprüfung | Lizenzanbieter | Lizenzschlüssel, anonyme Geräte-Kennung | nur mit gekauftem Schlüssel, etwa alle 24 h |

Den Sonnenstand für die Schatten im Grundriss berechnet ComfyOS selbst. Lizenzdateien werden auf dem Gerät geprüft, ohne Internet.

**Nie gesendet werden:** Geräte-, Raum- und Grundrissdaten, Verläufe, Adressen im Heimnetz und die Kennung der Home Base.
