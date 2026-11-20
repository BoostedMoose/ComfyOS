# Grundbegriffe

Die wichtigsten Begriffe in ComfyOS, jeweils in ein paar Sätzen.

| Begriff | Bedeutung |
|---|---|
| **Plugin** | Bindet ein System an: eine Bridge, einen Router, einen Broker. Jedes Plugin hat eine Kachel unter *System → Plugins*, lässt sich einzeln ausschalten und spricht nur mit den Zielen, die es erklärt. |
| **Gerät** | Alles, was ein Plugin meldet: Lampe, Steckdose, Thermostat, Kontakt, Bewegungsmelder, Sensor, Rollladen, Lautsprecher, Taster, Anwesenheit. Kennt die Home Base dieselbe Hue-Lampe wie die Hue Bridge, bleibt sie **ein** Gerät. |
| **Fähigkeit** | Was ein Gerät kann: schalten, dimmen, Farbe, Temperatur messen, Solltemperatur … Widgets, Flows und Szenen finden Geräte über ihre Fähigkeiten. Ein neues Plugin erscheint deshalb ohne weiteres Zutun überall, wo es passt. |
| **Raum** | Eine geschlossene Fläche im Grundriss. Geräte gehören zu einem Raum. Mit Home Base ist die Raumliste dieselbe wie in der Magenta-App. |
| **Gerätegruppe** | Mehrere Geräte als ein Punkt im Grundriss, etwa „Licht Sofa“. Lässt sich als Ganzes schalten und in Flows wählen. |
| **Szene** | Eine Farbpalette. ComfyOS verteilt ihre Farben auf die Lampen eines Raums. Eine Szene erscheint nur in den Räumen, denen sie zugeordnet ist. |
| **Raumvariante** | Eine Szene, für einen Raum abgewandelt und gespeichert. Beim nächsten Aufruf kommt genau dieser Zustand. |
| **Hausszene** | Eine Szene für alle zugeordneten Räume auf einmal. |
| **Effekt** | Bewegtes Licht wie Kerze oder Kaminfeuer, unabhängig von Szenen – sofern die Lampe es kann. |
| **Heizroutine** | Ein Wochenplan je Raum mit Schaltpunkten. Es gilt immer die aktive Routine. |
| **Zuhause/Abwesend** | Ein Schalter für das ganze Haus. Er wechselt auf Wunsch die Heizroutine und dient Flows als Bedingung. |
| **Flow** | Eine Automation aus Bausteinen: Auslöser, Bedingungen, Aktionen. |
| **Taster** | Ein Gerät mit Tasten (Hue-Dimmschalter, FRITZ!DECT 400, Zigbee-Fernbedienung). Jeder Druck kann Flows starten. |
| **Gerätesperre** | Ein gesperrtes Gerät schaltet ComfyOS nicht mehr – weder per Tippen noch per Flow, Szene oder Sprachassistent. Wandschalter und Hersteller-Apps schalten weiter. |
| **Basic/Pro** | Basic ist kostenlos und vollständig. Pro unterstützt die Entwicklung und bringt als Dank ein paar Extras. Bis 1.0 ist alles frei. |
| **Demo-Wohnung** | Eine Beispielwohnung zum Ausprobieren, nur für die eigene Sitzung. |
