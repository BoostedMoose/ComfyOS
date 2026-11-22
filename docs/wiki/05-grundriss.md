# Grundriss

Der Grundriss ist das Herz von ComfyOS. Er zeigt, wo was ist, und er bestimmt die Räume.

![Grundriss-Editor der Demo-Wohnung](../editor.png)

## Zeichnen

Tippe im Grundriss auf **Bearbeiten**. Änderungen werden sofort gespeichert.

| Werkzeug | So geht’s |
|---|---|
| **Wand** | Punkt für Punkt zeichnen. Jede rundum geschlossene Fläche wird ein Raum. Eine Wand auswählen und die Mitte ziehen macht einen Bogen. |
| **Tür, Fenster** | Auf eine Wand ziehen. Türen und Fenster lassen sich spiegeln und auf die andere Wandseite setzen. |
| **Durchbruch** | Eine Öffnung ohne Tür, etwa zwischen Küche und Wohnzimmer. |
| **Heizkörper** | Auf eine Wand ziehen, dann ein Thermostat daraufziehen. |
| **Treppe** | Von der untersten zur obersten Stufe ziehen. Breite und Richtung lassen sich danach ändern. |
| **Balkon** | Balkon oder Terrasse: Ecke für Ecke antippen, der erste Punkt schließt die Fläche. Sie liegt außen – Regen und Schnee fallen auch darauf. Als Boden wählst du Holz, Fliesen oder glatt. |
| **Auswählen** | Antippen bearbeitet. Rahmen aufziehen, Umschalt- oder Strg-Klick und **Alles** (Strg+A) wählen mehrere Teile – dann gemeinsam verschieben oder skalieren. |
| **Raster** | Einrasten am Raster an- und abschalten. An Wandecken rastet der Editor immer ein. |

**Fläche** legt die Größe der Zeichenfläche in Kästchen fest. Wie groß ein Kästchen ist, bestimmst du selbst – etwa ein halber Meter.

## Stockwerke

Ein Haus mit Keller, Obergeschoss oder Dach zeichnest du als mehrere Stockwerke. Gezeigt und bearbeitet wird immer eines.

- **Anlegen:** Im Editor **+ Stockwerk** antippen, die Lage wählen (ganz oben oder ganz unten) und entscheiden, ob der Grundriss des gezeigten Stockwerks übernommen wird oder du leer anfängst. Übernommen werden Wände, Türen, Fenster, Heizkörper und Balkone, keine Geräte.
- **Orientierung:** Beim Zeichnen scheint das Stockwerk darunter gestrichelt durch, und neue Punkte rasten an seinen Wänden ein. **Durchscheinen** und **Einrasten** schalten beides ab.
- **Wechseln:** Im Editor über die Leiste **Stockwerk**, sonst mit den zwei Pfeilen über dem Plan und im Vollbild. Jedes Gerät merkt sich, welches Stockwerk es zuletzt gezeigt hat – ein Tablet im Obergeschoss bleibt dort.
- **Treppen:** Eine Treppe erscheint im Stockwerk darüber von selbst als Abgang – mit Schatten, der nach unten tiefer wird. Antippen wechselt das Stockwerk: die Treppe führt nach oben, der Abgang nach unten. Treppen dürfen übereinander liegen wie in einem Treppenhaus; dann teilen sich Aufgang und Abgang die Fläche.
- **Name und Löschen:** Stehen im Editor rechts, solange nichts ausgewählt ist. Die Geräte eines gelöschten Stockwerks sind wieder unplatziert.
- **Geräte:** Ein Gerät steht immer nur auf einem Stockwerk. Zum Umziehen das Gerät im Editor antippen und unter **Stockwerk** ein anderes wählen.
- **Widget:** Das Grundriss-Widget wechselt das Stockwerk mit Knöpfen im Widget – oder zeigt ein festes. Für die ganze Übersicht legst du je Stockwerk ein Widget an.

Ein Keller zeigt kein Wetter. Weitere Stockwerke gehören zu Pro, Balkone und Terrassen zu Basic.

## Geräte platzieren

- Nicht platzierte Geräte stehen unter dem Plan. Antippen oder in den Plan ziehen.
- **Andocken:** Ein Fensterkontakt oder Rollladen auf einem Fenster oder einer Tür und ein Thermostat auf einem Heizkörper sitzen als Punkt mittig am Element. An einem Element haben bis zu vier Geräte Platz – etwa Kontakt und Rollladen am selben Fenster; ihre Punkte liegen nebeneinander. Wegziehen löst sie wieder.
- **Gerätegruppen:** Mehrere Geräte zu einem Punkt zusammenfassen, etwa die zwei Lampen über dem Sofa.
- **Symbole:** Jedes Gerät erscheint als Blase mit dem Zeichen seiner Art – der Rahmen ist grün, wenn es an oder aktiv ist. Geräte, die im Raum auffallen, bekommen ein eigenes Symbol in Draufsicht: Fernseher, Soundbar, HiFi-Anlage, Stehlampe, Kühlschrank, Sofa, Bett und rund 20 weitere. Ein farbiger Punkt an der Ecke zeigt den Zustand. Solche Symbole lassen sich drehen und in der Größe ändern – im Editor die Ecke des Rahmens ziehen –, damit sie zum Raum passen. Auch eine Gruppe kann ein Symbol tragen; es erscheint dann in der Gruppenblase.

Ein Gerät gehört zu dem Raum, in dem es steht. Mit Home Base schreibt ComfyOS Räume und Zuordnungen auch dorthin.

## Was der Grundriss zeigt

- **Zustände:** an/aus, offen, kein Kontakt, Batterie schwach, Gerätefehler, Alarm, gesperrt.
- **Temperaturen:** Name und Durchschnitt stehen in jedem Raum.
- **Heatmap:** Heizkörper leuchten nach gemessener Temperatur. Die Skala reicht vom niedrigsten bis zum höchsten Wert deines Heizplans. Ein offenes Fenster drängt die Wärme mit pulsierender kalter Luft zurück.
- **Schatten nach Sonnenstand:** Das Haus wirft tagsüber einen Schatten, nachts liegt ein weicher Hof darum. Dafür stellst du im Editor einmal ein, wo Norden ist.
- **Schallwellen:** Spielt ein Lautsprecher, gehen von ihm Schallwellen aus, die an den Wänden enden. Surround-Boxen, Sub und die zweite Box eines Stereopaars spielen mit, sobald ihr Raum spielt.
- **Wettereffekte:** Regen, Schnee, Hagel, Nebel, Wolken, Gewitter, Frost, Sonnenlicht und Wind als Strömung ums Haus. Alle Effekte folgen ihrem eigenen Messwert und mischen sich – Sonnenstrahlen bei einem Schauer, je nach Bewölkung, Dunst neben Regen, der Mond in Wolkenlücken. Tagsüber ist die Umgebung heller, Sonnenstrahlen kommen und gehen, wandern mit den Wolken und enden am Haus; ab 4 °C setzt ganz zart Raureif ein, der bis 0 °C dichter wird, in Pfützen spiegeln sich ziehende Wolken, und Tropfen schlagen Wellen hinein. Durch die Fenster fällt Licht: tagsüber ein heller Schein, bei Sonne ein Lichtfleck auf dem Boden – kurz bei hoher, lang bei tiefer Sonne –, in klaren Nächten je nach Mondphase ein schwacher Mondschein; ein Rollladen verkürzt den Lichtfleck und hält das Licht geschlossen ganz draußen, das Tageslicht breitet sich weich im Raum aus und fällt durch offene Türen in den Nachbarraum. Bei Hitze flimmert die Luft an den Wänden in der Sonne. Ein Pfeil oben rechts zeigt Windrichtung und Stärke. Schalter **Wetter** über dem Plan, zwei Stärken in den Einstellungen.

Für Wetter, Warnungen und Sonnenstand trägst du im Editor eine **Adresse** ein. Übertragen werden nur Postleitzahl oder Ort und auf etwa einen Kilometer gerundete Koordinaten, nie die Straße.

## Bedienen

- Antippen eines Geräts öffnet sein Menü, Antippen eines Raums seine Details.
- Plus/Minus, zwei Finger oder Strg+Mausrad zoomen. **Ansicht zurücksetzen** zeigt wieder alles.
- **Vollbild** zeigt den Plan bildschirmfüllend mit Wetterdetails.
- **Namen** blendet die Gerätenamen ein und aus, **Heatmap** die Wärme.

## Wetterwarnungen

Gibt der Deutsche Wetterdienst für deinen Ort eine amtliche Warnung aus, steht sie ganz oben auf der Startseite – mit Stufe, Zeitraum und dem Hinweis, die Fenster zu schließen. Mit Fensterkontakten nennt ComfyOS die offenen beim Namen. Nur für Orte in Deutschland, abschaltbar im Editor.
