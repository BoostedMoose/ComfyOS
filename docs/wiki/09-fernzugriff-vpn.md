# Fernzugriff per VPN

ComfyOS ist für das Heimnetz gebaut. Von unterwegs erreichst du es am sichersten über ein **VPN**: Dein Handy verbindet sich verschlüsselt mit deinem Heimnetz und verhält sich dann so, als wäre es zu Hause im WLAN.

> **Richte nie eine Portfreigabe für ComfyOS selbst ein** (Port 80, 443 oder 8000 ins Internet). ComfyOS wäre dann für jeden im Internet sichtbar, und jede Lücke wäre ein offenes Tor zu deinem Zuhause. Gleiches gilt für die Matter-Ports 5540 und 5541.

## Welcher Weg passt?

| Weg | Passt, wenn … | Portfreigabe nötig? |
|---|---|---|
| [1. VPN im Router](#1-vpn-im-router) | dein Router einen VPN-Server hat (FRITZ!Box und viele andere) | nein, das erledigt der Router |
| [2. WireGuard auf dem Pi](#2-wireguard-auf-dem-raspberry-pi) | dein Router kein VPN kann, du aber eine öffentliche IPv4- oder IPv6-Adresse hast | ja, **eine** – nur für das VPN |
| [3. Mesh-VPN wie Tailscale](#3-mesh-vpn-wie-tailscale) | du keine öffentliche Adresse hast (DS-Lite, Glasfaser, Kabel) oder nichts am Router ändern willst | nein |

**Habe ich eine öffentliche IPv4-Adresse?** Bei vielen Kabel- und Glasfaseranschlüssen nicht („DS-Lite“ oder „CGNAT“). Dann ist dein Heimnetz über IPv4 von außen nicht erreichbar, und Weg 1 und 2 klappen höchstens über IPv6. Ob das zutrifft, steht in der Übersicht deines Routers oder beim Anbieter. Im Zweifel ist Weg 3 der einfachste.

## 1. VPN im Router

Der Router nimmt die VPN-Verbindung an. Auf dem Pi ändert sich nichts.

**FRITZ!Box (ab FRITZ!OS 7.50):**

1. *Internet → Freigaben → VPN (WireGuard)* → **VPN-Verbindung hinzufügen** → „Vereinfachte Einrichtung“ für ein einzelnes Gerät.
2. Namen vergeben (etwa „Handy“). Die Box zeigt einen QR-Code.
3. Auf dem Handy die App **WireGuard** installieren, „Tunnel hinzufügen → QR-Code scannen“.
4. Tunnel einschalten und ComfyOS über seine IP-Adresse öffnen, zum Beispiel `http://192.168.178.20/`.

**Andere Router:** Viele Router haben einen VPN-Server eingebaut, meist WireGuard oder OpenVPN – etwa Geräte von ASUS, GL.iNet, Ubiquiti oder mit OpenWrt. Das Prinzip ist dasselbe: Im Router einen Zugang anlegen, die Konfiguration oder den QR-Code in die passende App auf dem Handy übernehmen. Wo die Einstellung liegt, steht im Handbuch des Routers. Viele Router der Internetanbieter bieten keinen VPN-Server – dann weiter mit Weg 2 oder 3.

## 2. WireGuard auf dem Raspberry Pi

Der Pi, auf dem ComfyOS läuft, wird selbst zum VPN-Server. Bewährte Helfer dafür sind [PiVPN](https://pivpn.io) und [wg-easy](https://github.com/wg-easy/wg-easy). Beide führen durch die Einrichtung und zeigen am Ende einen QR-Code für die WireGuard-App.

Damit das Handy den Pi von außen findet, brauchst du:

1. **Eine Portfreigabe für das VPN**, nicht für ComfyOS: UDP-Port `51820` im Router auf den Pi. WireGuard antwortet nur Geräten mit passendem Schlüssel und ist von außen praktisch unsichtbar.
2. **Einen festen Namen für deinen Anschluss**, weil sich die öffentliche Adresse ändert: einen DynDNS-Dienst. Viele Router bringen einen mit.
3. **Eine öffentliche Adresse** (siehe oben).

## 3. Mesh-VPN wie Tailscale

Dienste wie [Tailscale](https://tailscale.com) bauen die Verbindung von beiden Seiten nach außen auf. Deshalb brauchst du weder eine Portfreigabe noch eine öffentliche Adresse. Das funktioniert auch hinter DS-Lite.

1. Tailscale auf dem Pi installieren und anmelden (Anleitung auf der Website von Tailscale).
2. Tailscale auf dem Handy installieren und mit demselben Konto anmelden.
3. ComfyOS über die Tailscale-Adresse des Pi öffnen (beginnt mit `100.`), zum Beispiel `http://100.101.102.103/`.

**Was du wissen solltest:** Deine Daten laufen Ende-zu-Ende verschlüsselt direkt zwischen deinen Geräten. Der Dienst kennt aber deine Geräteliste und ihre Adressen, und du brauchst ein Konto dort. Wer das nicht möchte, betreibt mit [Headscale](https://github.com/juanfont/headscale) den Koordinationsserver selbst. Ähnliche Dienste sind ZeroTier und NetBird.

## ComfyOS im VPN öffnen

- **Über die IP-Adresse geht es immer.** Den Namen `comfyos` kennt dein Handy im VPN oft nicht, weil der Router dort nicht als Namensdienst dient.
- **Eigener Name?** Willst du ComfyOS unter einem anderen Namen öffnen (etwa dem Namen, den Tailscale vergibt, `comfyos.dein-netz.ts.net`), trage ihn einmal unter *System → Sicherheit → Weitere Adressen* ein. ComfyOS lehnt fremde Namen sonst zum Schutz vor manipulierten Webseiten ab.
- **HTTPS empfohlen** (*System → Erreichbarkeit*). Push-Benachrichtigungen und der Ton des PCs auf Sonos funktionieren nur über HTTPS.
- Leg dir ComfyOS über die VPN-Adresse auf den Home-Bildschirm. Mit eingeschaltetem VPN funktioniert die App dann zu Hause und unterwegs gleich.

## Checkliste

- [ ] Keine Portfreigabe auf ComfyOS (80, 443, 8000) und Matter (5540, 5541).
- [ ] VPN-Zugang je Gerät; ein verlorenes Handy sperrst du, indem du seinen Zugang löschst.
- [ ] ComfyOS-Passwort ist lang und nur hier im Einsatz.
- [ ] Unter *System → Sicherheit* ab und zu die angemeldeten Geräte prüfen.
