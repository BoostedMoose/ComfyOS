# Floor plan, weather effects, weather warnings

Area documentation of ComfyOS – part of the guide `CLAUDE.md` (which says when to read this file).
This file says how the area is built and what to watch out for when changing it; version notes ("since 0.x") name
when something was added.

## Since 0.30.2 – Hue effects over the pixels of a strip, spotlights

- **Effects on gradient strips:** a lamp row carries `segs` (`caps.gradient`, `indoorNow`). With a running effect and
  `segs` > 1 the lamp light is drawn from `HO.effectColors(effect, colour, n, t, phase)` (core.js) instead of the one
  colour the bridge reports: n = 2·segs − 1 within `EFFECT_PIXELS` [5, 9], t quantised to `EFFECT_RATE` 8 steps/s so
  the tinted image and the halo are rebuilt at most 8 times per second; the pixels blend (`gmodeOf` ignores the gradient
  mode while an effect runs) and the overall flicker is off (`flicker` returns 1). Candle, fire, sparkle, glisten and opal
  use the effect colour; prism, underwater, cosmos, sunbeam and enchant their own palettes. A simulation of the
  bridge's patterns, not compared with a real strip.
- **Symbols `spot-2`, `spot-3`** (`floorplan_symbols.js`): heads are bodies (the halo follows them), the rail is a body
  between them; stretching lengthens the rail, the heads stay round.

## Since 0.30.1 – fainter halo, stronger lamp light

- **Light of the lamps retuned (wish of the operator: the bright rim at the bubble read as a second lamp while the glow
  in the room was hardly there):** `weather_fx.LAMP_HALO` 0.42 → 0.16 (the ring along the outline, `haloOf`),
  `LAMP_LIGHT` 0.34 → 0.52 (the spreading light, its cap 0.5 → 0.7). Width, easing and the daylight factor are unchanged;
  `stats.lamps` still counts the lamps with a halo.
- **Pictures for README and wiki** (`tools/readme_screenshot.py`): the home page shows the lamps (scene "Polarlicht" in the
  living room, three more lamps on, the speaker playing) in a preview night with rain, the heat map off; the heat map has a
  picture of its own (`docs/heatmap.png`, wiki "Grundriss") by day without lamp light. Previews set through
  `HO.state.fxPreview`/`fxTime`, as the owner's `#home?fx=rain&time=night` does.

## Since 0.30 – stars only in reflections, finer frost with a sky mirror, multicolour lamps, calmer frames

- **Stars only in reflections (wish of the operator):** the stars – and the rare shooting star – no longer stand in
  the sky above the plan. They show where something mirrors: in the puddles (drawn inside the clip of each puddle,
  the twinkle and the places as before, scaled by the puddle's opacity) and in the hoarfrost. Seeding is unchanged
  (`stars` follow the clear sky and move with the plan); `animated()` counts them only while a reflecting surface is
  there. The shooting star is laid out once per frame (`shooting`) and drawn into every puddle it crosses.
- **Hoarfrost smaller and finer, with a faint sky mirror (wish of the operator):** patches at radius 1.2…2.4 instead
  of 2…3.8 (and more of them, `per` 7), thinner scratches, smaller splats and speckle (`rime`). `rime` returns
  `{img, mask}` now; the frame lays a mirror into each patch (`glaze` scratch canvas: the drifting cloud pattern of
  the puddles – one `mirror()` pattern and one drift for all surfaces – plus the stars at night, cut to the patch
  by `destination-in` with its mask). Far subtler than a puddle (`FROST_MIRROR`, `FROST_MIRROR_CLOUD`); skipped on
  economical devices and from self-regulation step 2 (`icy`).
- **Multicolour lamps (wish of the operator – "nicht nur eine Stellvertreterfarbe"):** the lamp light follows the
  gradient mode of a strip: `indoorNow` hands over `gmode` (`d.light.gmode`), `weather_fx.paint` draws
  `random_pixelated`/`segmented_palette` as hard colour bands (the strip really shows its colours side by side) and
  `interpolated_palette_mirrored` out and back; everything else blends as before. With a running effect every colour
  of a multicolour lamp flickers with a phase of its own (`segment`, quantised to twentieths so the tinted images
  are rebuilt rarely) – a light bar plays candle or sparkle along its length instead of breathing as one.
- **Smooth fade of the light (the operator saw dithering around the lamps):** `spreadFrom` lays the glow image out
  finer than the path grid (`SS` subpixels per cell, 3, economical devices 2): the path distance is read bilinearly
  between the cells and only then becomes opacity – one pixel per cell left visible steps. Computed once per layout
  and cached as before; window glow and lamp light share it. The halo (`weather_fx.haloOf`) is one single ring along
  the outline now (wish of the operator) – before, a stack of six rings faded outwards and its steps showed.
- **Calmer frames (the operator found the view laggy; browser numbers from `dev/measure.py frontend` are pending):**
  - `follow()` draws at the pace of the current step instead of once per pointer move – during a pan the full
    drawing ran at pointer rate (60–120/s) since the inside layer made it expensive.
  - a calm single image is repainted only when something it shows changed (`sigOf`/`calmSig` in `update()`:
    seeded weather, size, colours, sun and moon rounded, windows, lamps, speakers, bubbles/symbols) – before,
    every live message of the home page redrew the whole canvas every few seconds; hidden or out of sight nothing
    is painted at all (the observers kick a repaint when it comes back).
- **Tests:** `ui_home` (stars no longer asserted in the sky; everything else unchanged), `ui_widgets` (effects
  switch in the widget frame). The reference images of `ui_visual` change (night without stars) and are renewed on
  Windows.

## Since 0.29.1 – loads in any colour, bubbles with a symbol of their own, docked dots along their element

- **Light of a load like a lamp (wish of the operator):** a light among the loads shines in a white tone or in a colour
  of its own, at its brightness. "Lichtfarbe" offers Kerzenlicht (2200 K, new – the standard of candles, which before had
  it fixed), Warmweiß, Neutralweiß, Kaltweiß and "Eigene Farbe" (`<input type="color">`, live while picking): stored as
  `color` (`#rrggbb`, lower case) instead of `tone`. `loadTone`, `loadRgb` give the light to the lamp light. Every light
  kind has the choice now, candles too. The tone of its kind (`LOAD_KINDS[].tone`) is not stored.
- **Bubble with a symbol of its own:** besides the plan symbols (Pro) the picker of a load offers symbols for its bubble
  right after "Standard" (`LOAD_ICONS`: Glühbirne, Stecker, Lautsprecher, Flamme, Funkeln, Sonne, Strom), stored as `icon`
  – Basic like every bubble, only without a plan symbol (choosing one drops the other). `symbolPicker` takes such a list
  as `kinds`; a group still gets the symbols of the device kinds after the plan symbols. Without `custom_appearance` the
  panel says that the bubbles are free and the plan symbols Pro.
- **Store (additive):** `_load` keeps `color` (then no `tone`), `tone` `candle`, `icon` (`LOAD_ICON`, only without
  `furniture`); a plan of 0.29.0 reads as before (candles without a tone are candle light, as they were).
- **Docked dots move along their element (wish of the operator: dots of a window and the radiator below it lay on each
  other):** dragging a docked dot along the element it hangs on moves it (`slideDock`): it snaps to either end and to
  the middle (the snap zone grows with the dot, at least 0.2 cells), in between it stays where it is let go; Shift =
  no snapping. Stored as `devices[id].along` 0…1 (0 = start, 1 = end, drawn inside the ends at every zoom; the middle
  is not stored; the store keeps it only for a docked device). Docking at another element starts in the middle again
  (`dock`). `dockLayout`: every dot at its place, dots that would overlap form a row centred on the mean of their
  places, a row stays on the element – all in the middle gives the row of before. Dragging away still detaches.
- **Tests:** `test_stores` section 7 (colour, tone, symbol of the bubble) and 8 (place of docked dots), `ui_home` (the
  load shines in a colour of its own).

## Since 0.29 – loads ("Verbraucher"), smart stretching, free turn and a true shadow of the plan symbols

- **Smart stretching (wish of the operator: a light bar drawn wider got elliptical ends):** `floorplan_symbols.js`
  `HO.planSymbolShape(key, ax, ay)` lays a symbol out anew instead of scaling it on one axis. `symbolBox` returns the
  one scale `m` = min(k, kx) and the stretch `ax`/`ay` (≥ 1, one of them 1); the symbol is drawn with
  `rotate(rot) scale(m)`. On the stretched axis whatever lies within half the shorter side of the symbol of either
  end only moves outwards, the middle stretches (`axisMap`; a symbol as wide as tall opens at its centre line). A rect
  keeps its corner radius (the slot keeps its round ends), a circle stays a circle and moves – the body of the symbol
  across the stretch (no class, at least half the extent: round lamp, round table, fan) becomes a slot; paths move
  every point, arcs take the stretch of their ends. Every part comes back as an absolute path (`parts: [{d, cls}]`,
  `svg`), remembered per key and stretch; a drawing it cannot read is stretched plainly as before (`parts` null).
  Rule for new symbols: only `rect`, `circle`, `path` (commands M L H V C Q A Z), `rotate(a 24 24)` – the drawing
  rules at the top of the file.
- **One outline for everything:** the symbol, its shadow, the punch of the inside layer (`weather_fx.js`
  `symbolShape(key, ax, ay)` → `Path2D` kept on the shape) and the halo of a lamp (`haloOf` strokes its bodies) use
  the same parts. `indoor.symbols` = `{x, y, key, rot, m, ax, ay}` (before `kx`, `ky`); a lamp's `shape` carries
  `key, m, ax, ay`.
- **Shadow of a plan symbol = its own outline** (`devShadowSVG`, `sym`): before a disc in the middle, which fitted
  neither a long light bar nor a turned sofa. The parts in black, the strength on the group (`.fp-devshadow .sym`, no
  darker overlaps), the blur on an outer group without a transform (as soft as at a bubble); clipped to the room as
  before. Economical devices: no blur, fainter and a little wider.
- **Free turn:** `rot` in whole degrees (`floorplan_store.SYMBOL_TURN` 1, before steps of 45 – an older ComfyOS rounds
  down to 45°). Editor: a knob above the frame (`data-kind="symrot"`, drag mode `symrot`): points where the top of the
  symbol faces, snaps to a multiple of 45° within `SYMBOL_SNAP` (6°), with Shift in steps of 15°. Panel: "Drehung"
  −/+ to the next multiple of 45° in that direction (replaces "Drehen", which only turned one way).
- **Loads ("Verbraucher", wish of the operator):** appliances without intelligence of their own – a floor lamp on a
  plug, a string of lights, a radio – placed in the floor plan **only for the picture**: no device of ComfyOS, in no
  list, scene, flow or Matter share, nothing switches them. A first try as a plugin (devices of their own that follow
  a plug) was dropped before the release – the operator wants them in the floor plan editor alone.
  - **Stored per floor** (`floorplan_store`): `loads: [{id, x, y, kind, device_id, name, tone, brightness, furniture,
    size, rot, stretch}]`, **only when a floor has any** (an old plan reads and writes byte for byte as before; not in
    `CONTENT_KEYS`, `floors()` stays as it is). `_load` checks: `kind` from `LOAD_KINDS` (unknown = lamp), `device_id`
    ≤ 160, `name` ≤ 40, `tone` warm/neutral/cold (warm not stored), `brightness` 10…100 (100 not stored), the symbol
    by the rules of a device (`_symbol`, shared since 0.29). Ids unique across floors (`load_ids`), at most `MAX_LOADS`
    (80) per floor. Copying a floor takes no loads along.
  - **Kinds** (`floorplan.js` `LOAD_KINDS`): Lampe (light, white tone and brightness chosen), Lichterkette (light,
    `sparkle`), Kerzen und Kamin (light, 2200 K, `candle`), Radio und Musik, Fernseher (sound), Ventilator, Heizgerät,
    Anderes Gerät (only the picture). Each has the icon of its bubble and – where symbols are allowed – a symbol a new
    one starts with (floor lamp, light bar, hi-fi, TV, fan); changing the kind swaps a symbol that was the old default.
  - **On** = the device it hangs on (`device_id`, any row with a writable `on`: plug, actuator, lamp) is reachable and
    on (`loadOn`); its state class (`loadClass`) as `HO.devClass` – green when on, grey when the device is not reachable,
    neutral without one; the status dot only with a device, no marks.
  - **View:** drawn like a device (bubble with the icon of its kind or plan symbol, shadow, left free by the inside
    layer); `indoorNow` hands a light that is on to the lamp light (`lamps`, id `load:<id>`, fixed colour from its
    tone, level from its brightness, the effect of its kind) and a radio or TV to the sound waves (`speakers`, level
    0.5). Behind the button "Effekte" like every lamp. Tapping a load opens the device it hangs on; the tooltip names it.
  - **Editor:** tool "Verbraucher" (`load`, icon `light`): a tap places a lamp and selects it (`addLoad`; with
    `custom_appearance` it starts as a floor lamp, otherwise as a bubble). Selection type `load`: drag = move
    (`lmove`), the symbol frame with size, width, height and turn like a device (`symEntry`/`symEntryOf` resolve the
    selected device or load), panel `loadPanelHTML` (name, kind, "Hängt an", light colour, brightness, symbol picker,
    `symbolControlsHTML` – shared with the device panel). Part of the multi-selection (moves along, keeps its size),
    delete, resizing the area.
  - **Basic/Pro:** a load is Basic; as a plan symbol it counts towards `custom_appearance` like a device
    (`entitlements._appearance`).
  - **Demo home:** a reading lamp on the plug of the desk in the study.
- **Tests:** `test_stores` section 6 (turn in whole degrees) and 7 (loads), `test_entitlements` section 3 (a load as a
  bubble is Basic, as a symbol Pro).

## Since 0.28 – button "Effekte", light of the lamps, free scaling of plan symbols, a pass over the frame time

- **Button "Effekte"** (`data-act="fpEffects"` on the home page, `pvEffects` in the full screen, symbol `sparkle`) next to
  "Heatmap" and "Wetter": the effects of the devices – light of the lamps and sound waves. **Basic.** Remembered per
  browser, nothing in the plan (wish of the operator): `localStorage` `ho.fpEffects` (absent = on), read by every
  mounted floor plan (`st.effects`, `HO.floorplan.effectsOn()`), switched by `fp.toggleEffects()`. Without weather the
  canvas still runs while a lamp shines or a speaker plays (`INDOOR_ONLY`); off → no lamps, no speakers in `indoorNow`.
  The window light stays with "Wetter" (Pro `floorplan_weather`).
- **Light of the lamps (`indoorNow` → `lamps`, `drawIndoor` step 2):** every placed light that is on (`kind` light,
  online; not docked) and every group with such members. Colours from `HO.lightColors` (white tone, colour, the points
  of a gradient; a Home Base lamp without a light model = warm white), brightness 0.3…1 from `brightness`, `effect`
  = running Hue effect. Shape of the source: the bubble (`r`) or the turned box of the plan symbol (`rot`, `lw`, `lh`,
  `round`) – a wide symbol lights from its whole length.
  - **Spread:** the same path field as the window light (`lightGrid` once per floor and doors, `spreadFrom` = Dijkstra
    from seed cells; windows: along the glass, `lightFields`; lamps: the cells of the symbol or a small disc
    `LAMP_SEED` in the bubble, only cells of the lamp's own room, `lampFields`, `LAMP_HALF` 1.5, `LAMP_R` 7 cells).
    Walls stop it, open doors let it through (the thresholds get it back like the window glow). Cached per lamp place
    and shape (`lampCache`) – switching, dimming and colours never compute anew.
  - **Drawing:** the field tinted in the lamp's colours (`tintedLamp`; several colours = a linear gradient along the
    symbol – a gradient runs from one end of a light bar to the other –, at a bubble across it at 100° as
    `HO.lightGlow`), opacity `LAMP_LIGHT` × brightness × strength, a little less by day (`LAMP_DAY`); a halo at the edge
    of bubble or symbol (`haloOf`: six strokes around the outline, drawn once per colour, shape and scale into an
    image). Changes ease over `LAMP_EASE` (own clock `lampAt`, the first frame of an animation has no `dt`); a lamp
    switched off fades, but not on another floor (`mask.shape`). Effects: candle/fire flicker, sparkle/glisten
    twinkle, others breathe (`flicker`, phase from the id). Static light needs no frame: `lampsBusy()` lets the image
    run only while something eases or an effect runs (calm pace).
  - `stats.lamps` = lamps with a halo in the last frame.
- **Bubble and symbol always lie on top:** besides bubbles, docked dots, group bubbles (since 0.27.4) also the status
  dot and the marks (`markSpots`, the same places as `deviceMarks`) and **the plan symbols themselves** are punched out
  of the inside layer last (`indoor.symbols`, `symbolShape`: rect/circle/path of `floorplan_symbols.js` as `Path2D`,
  bodies filled, lines traced a little wider; without `Path2D` the box). Before 0.28 plan symbols stayed lit by the
  window light. `stats.symbols`.
- **Free scaling of plan symbols:** `devices[id].stretch` = width ÷ height against the drawing (0.25…8, 1 is not stored,
  only with a symbol; `floorplan_store.SYMBOL_STRETCH`, additive field – a plan without it reads as before, an older
  ComfyOS ignores it). `size` stays the height (0.5…4). `symbolBox` returns `kx`/`k` and the half sides before the
  turn `lw`/`lh`; the symbol is drawn with `scale(kx k)`, its lines keep their width (non-scaling stroke). Editor: the
  frame turns with the symbol – corner = size (proportion stays), middle right = width, middle bottom = height
  (`symscale` with `data-c` e|s|se, distance along the turned axis); panel: Größe, Breite, Höhe (−/+ in 5 % steps,
  `setSymbolSides`), "Seitenverhältnis zurücksetzen". Moving to another floor carries `stretch` along.
- **Frame time (the operator found the floor plan slow with every effect):** measured on the Pi only by reading the
  code – the browser numbers come from `dev/measure.py frontend` on Windows, which since 0.28 also prints what one
  frame costs in the script (`stats.drawMs`, of it the inside layer `stats.indoorMs`, moving averages). Changed without
  a visible difference:
  - sound waves alone no longer drive the image at the fast pace (30 frames/s) but at `SOUND_FPS` 20 (`fpsNow`) – the
    rings are slow since 0.27.1;
  - `update()` reads the colour tokens (`getComputedStyle`, forces a style computation) only when style or classes of
    the root element changed – before on every live message;
  - all door thresholds as one clip, the light laid in once (before once per door and light source);
  - the middle of the building once per outline instead of from all corners in every frame;
  - the halo of a lamp as an image instead of six wide strokes per frame.
- **Tests:** `test_stores` section 6 (`stretch`); `ui_home`: lamps count as light inside, button "Effekte" switches and
  remembers (`ho.fpEffects`), without weather the canvas runs only for lamps or sound.

## Since 0.27.5 – every effect blends with every other, window shafts live like the rays outside

- **No effect asks for the weather kind any more.** The kind (`fx.kind`) still names the weather; every effect follows
  its own value, so they blend and the picture follows the measurement:
  - **Backend (`weather.fx`):** rain and snow as measured in every kind (a shower from a bright sky falls; the codes
    only keep them visible – drizzle at least 0.15, showers 0.5, snow 0.25); new field `fog` 0…1 – 1 with the fog
    codes 45/48, otherwise a thin mist from the humidity (`MIST_RH` 95 % → 0, 100 % → `MIST_MAX` 0.4); hoarfrost
    from `FROST_C` in every kind, thinner the more falls (× (1 − max(rain, snow))); heat haze fades with the cloud
    cover (`HEAT_CLOUD_FULL` 0.4 → full, `HEAT_CLOUD_NONE` 0.9 → none) and with rain instead of ending with "partly".
    `fx` grows by one field (`fog`); cached weather without it is read as before (the kind "fog" stands for 1).
  - **Browser (`weather_fx.js`):** `veilOf()` = what rain, snow, fog and a thunderstorm put in front of sun and moon
    (0…1, `VEIL_RAIN|SNOW|FOG|THUNDER`); `SUNNY` and the yes/no `dull` are gone. Sunlight shows while the veil is
    below 0.95: gradient, number and strength of the rays, the patches behind the windows and the daylight wash
    follow `1 − veil` – so sun rays shine in a shower depending on the cloud cover. Moonlight likewise through gaps in
    any weather (below `MOON_TRACE` it is none). Cloud shadows in every kind; fog banks as many and as dense as
    `fogOf()`; stars × `1 − veil`. Previews: `BASE.fog` 0, the preview `fog` has `fog: 1`.
  - **Effect lab:** "Nebel" is a slider (0…1) instead of a flag; `kindOf` names it fog above 0.4.
- **Shafts behind the windows live like the rays outside:** every shaft is a ray of its own (`bornRay`, kept per window
  in `inRays`): it reaches out while it fades in, shimmers (`rayGlow`), breathes in width, sways, fades out and
  appears at another place of the glass in another shape; below clouds faster and with pauses; glow around the core and
  dust that drifts along it – the same code paths and numbers as outside. They do not wander with the clouds: the
  window stays where it is, passing clouds make its light swell and fade.
- **UI test `ui_home`:** moonlight also in the previews `drizzle` and `storm` (gaps in the cover).

## Since 0.27.4 – free bubbles, sound of further boxes, gentler frost

- **Bubbles stay free:** the effect canvas lies above the SVG, so since 0.27 the inside layer (window light, glow, sound
  waves) veiled the device bubbles – the plan seemed to shine through the symbol. `floorplan.js` collects every device
  bubble, group bubble and docked dot while drawing (`st.bubbles`, plan units) and hands them over as
  `indoor.bubbles`; `drawIndoor` punches them out last (`destination-out`, radius + 1.5 px). Plan symbols stay lit (until 0.28 – since
  then they are left free too, see there).
- **Further boxes play along:** a surround speaker, sub or second box of a stereo pair has no `playing` of its own. Its
  row names its main device (`part_of`, plugin API revision 8, resolved by `device_merge.merge` to the row id shown –
  the Home Base may lend it); `indoorNow` takes that device's `playing`, `muted` and `volume` for the box. No guessing
  from `media`.
- **Frost begins gently:** `weather.FROST_MIN` 0.05 (before 0.3) – at 4 °C only a hint, linear to the full frost at
  0 °C. In the browser number of patches (15…100 %), their opacity (25…100 %) and the glints (25…100 %) follow
  `frost_level`; at 0 °C everything is as before.

## Since 0.27.1 – light through doors, shafts in the window light, heat haze, worn ice, mirroring puddles

- **Less colour in the sunlight:** `sunColor` mixes only 5 % (noon) to 35 % (sun at the horizon) of `--fx-sun` into
  `--fx-light` (before 20–65 %) – almost white by day, a little warm in the morning and evening.
- **Continuous in the cloud cover:** number and strength of the sun rays, the cloud shadows, the stars, the sunlight
  gradient and the direct light through the windows follow `fx.cloud`, no longer steps by `fx.kind` (the kind still
  decides *whether* sunlight shows: `SUNNY` – gone since 0.27.5, see there). Rays fade away towards a closed cover.
- **Glow of the windows (indirect light, since 0.27.2 – before a directed patch at windows and doors that read as a
  second ray):** `floorplan.js` `lightFields()` measures for every window the path through
  the home on a coarse grid – the way the heat map spreads warmth (Dijkstra, walls stop it, the doors in
  `indoor.doors` let it through) – and returns a small image per window (`glow: {img, x, y, w, h}` in plan units)
  whose opacity falls off with the path (`LIGHT_HALF`, `LIGHT_R`). The cells under a wall are blocked for the path
  but get the light of their own side afterwards (since 0.27.3 – before a dark cushion stood between the light and
  every wall; the heat map still has it). Cached by plan and doors, never by weather or time. `drawIndoor` tints each image once and draws it as bright as its window is right now (`GLOW_SKY` × sky light
  × shutter + `GLOW_SUN` × direct light), clipped only to the rooms as a whole: a bright room lights the next one
  through its open door. Under any sky, also in rain and under 100 % clouds. **No ray belongs to it** – rays (the
  patch on the floor, the shafts) exist only at windows the sun or the moon shines into and follow its position; the
  direct patch gets softer with the clouds (`pane(color, soft)`, soft = 0…2).
- **Roller shutters shorten the patch:** the shutter comes down from the head of the window, so the far end of the
  patch moves towards the window (`deep × open`); the brightness stays until the shutter is almost closed.
- **Shafts of light in the room (`shafts`):** two to four of the outside's beam images per sunlit window, from the
  glass along the light to the patch, shimmering and wandering, with dust that glints. Their places come from a hash
  of the window index – nothing is stored. `beamsOf(tint)` keeps the beam images per tint for outside and inside.
- **Doors:** `indoorNow()` returns `doors` – doors and passages between two rooms (`n` into `rooms[0]`); a door whose
  docked contact reports it closed is left out, one without a contact counts as open. They open the grid of
  `lightFields()`; the wall punch would cut the glow out at the threshold again, so it is drawn there once more.
  `stats.doors` counts doors next to a lit room. `indoorNow(false)` (Basic, effects off) returns speakers only.
- **Heat haze (`fx.heat`):** `hotWalls()` finds the outer room edges that face the sun (no room just beyond them, at
  least 0.9 cells long); `shimmers` are waving lines, three behind each other, that detach from such a wall, drift
  away and dissolve. Not with reduced motion. `stats.haze`; UI test: exactly in the preview `heat` by day.
- **Hoarfrost reworked (`rime()`):** no ornament any more but worn ice – per pixel a cloudy layer from value noise
  (`lattice()`), thinning out softly towards an irregular outline, on it bundles of scratches, splats, speckle, a few
  scratches that take ice away. Patches are large, turned at random and overlap into one sheet, thinner on the sunny
  side of the house than in its shadow.
- **Frost glints (since 0.27.3):** few, small and rare – a crystal flashes now and then, no glitter.
- **Both stay in the background:** `FROST_ALPHA` and `PUDDLE_ALPHA` are the opacity of the frost patches and of the
  puddles with everything in them (water, mirror, waves, glints) – the floor plan is the subject, the effects only
  accompany it.
- **Puddles:** never overlap (`groundSpots` option `apart`). The water mirrors the sky: `mirror()` is a seamless tile
  of clouds, filled into every puddle as a pattern (clipped to `outline`) that drifts with the wind (`mirrorPan`);
  lightning brightens it (`flashNow`). A drop that lands in a puddle (`land`) adds a wave to it (`p.waves`, in radii
  of the puddle): in its ring the mirror image is dimmed and laid in again displaced, a dark trough in front of a
  bright crest. The glints (`gloss`) are drawn in strips of whole pixels that swing against each other.
- **Sound waves calmer:** rings, period, beat and reach go from the quietest to the loudest speaker (`SOUND_RINGS`,
  `SOUND_PERIOD`, `SOUND_BPM`, `SOUND_REACH` as pairs): few, small, slow rings when quiet, more and further when loud,
  slower than before in both cases. Speakers of different volume no longer pulse in step.
- **Effect lab:** the weather is always made from the sliders; a preset only sets them and the kind is derived as the
  backend derives it (`kindOf` in `dev/fxlab/lab.js`, new flag "Nebel" – a slider since 0.27.5). "Eigene Mischung" shows when a slider left
  the preset.

## Since 0.27 – light through the windows, sound waves, moon, frost flowers, mirroring puddles, effect lab

- **The inside layer (`weather_fx.js`, `drawIndoor`):** the weather still never gets inside – rooms and walls are punched
  out as before. Only afterwards the inside layer is drawn onto the same canvas: every part clipped to the room it
  belongs to, the walls cut out again (`destination-out` stroke), so nothing passes a wall. `floorplan.js` `indoorNow()`
  hands it over through `getIndoor()` in plan units (computed in `syncFx`, i.e. whenever the plan is drawn):
  `windows` = windows in an outer wall (`roomSideOf`; `n` into the room, `room` = index into `fxMask().polys`, `open` =
  share of light a docked roller shutter lets through – `position` 100 is closed), `speakers` = placed devices and
  groups that play.
- **Light through the windows:** by day the sky light glows in at every window (`skylight`: dimmer under clouds, dimmest
  in precipitation and fog) and every room gets a breath of light. If the sun stands on the side of a window, a patch
  of light lies on the floor (`pane()`: soft edges, the shadow of the bar in the middle, both softer with the distance):
  from where the light over the sill lands to where the light below the head lands (`WINDOW_SILL`, `WINDOW_HEAD` in
  cells ÷ tan(elevation), at most `WINDOW_REACH`) – short and close to the window at noon, long and deep in the room
  with a low sun, in the warm tint of the sunlight. Around it the room is brighter (bounce: a glow at the patch and a
  wash over the room), and the window blooms (a flat halo along the glass, the only part on top of the wall). Clouds
  let less through (`partly` 0.7 and slowly swelling, `overcast` 0.22).
- **Moon:** `HO.moonPos(date, lat, lon)` (low-precision formulas, local like `sunPos`) → azimuth, elevation, lit share.
  `sunNow()` returns it as `moon`, `getSun()` as `{el, dir, phase}`. In a clear or partly cloudy night without
  precipitation the moon lights like the sun, far weaker (`MOON_LIGHT`), by phase, elevation and cloud cover and in the
  cool token `--fx-moon`: a faint gradient outside and patches behind the windows. The preview `night` has a moon
  (az 150°, el 38°, 85 % lit); `HO.state.fxTime` also takes an object `{az, el, moon: {az, el, phase}}` (effect lab).
- **Sound waves:** a device with the capability `playing` that plays, is not muted and stands on the floor shown (or a
  group with such a member) sends rings (`SOUND_RINGS` under way at once, `SOUND_PERIOD` seconds each) in the token
  `--fx-sound` (accent). They fade on their way; how strong a ring is was decided by a beat (`SOUND_BPM`) at the moment
  it left the speaker, and the speaker pulses with it. The volume sets reach and strength. Speakers that play together
  (a group of rooms) pulse in step. While a speaker plays the canvas runs at the fast pace. **Without weather** (no
  address, a floor below the ground, Basic, switch "Wetter" off) the canvas is mounted for the sound alone:
  `fx = {kind: 'none', indoor_only: true}` – no daylight, no window light, no wind arrow. **Sound waves are Basic** and
  do not depend on the switch "Wetter" (`plan.fx`); the window light stays behind `floorplan_weather` and the switch
  (`syncFx()` empties `indoor.windows` otherwise).
- **Brighter day:** the daylight wash is about twice as strong (clear 0.16, less under clouds, 0.075 in dull weather).
- **Sun rays with more life:** lengths from a third of the frame to beyond it (many short, a few very long), each ray
  reaches out while it fades in (`grow`), they fan out a little (`RAY_FAN`), sway, carry a soft glow around their core,
  add up where they cross (`lighter`) and dust motes drift and glint in them. The shaft images hold fine filaments of
  their own length, so the end of a ray frays. Counts: 11 clear, 9 partly, 4 overcast, 3 storm.
- **Hoarfrost from 4 °C (`weather.FROST_C`), `fx.frost_level`:** additive field 0…1 – 0.05 at 4 °C (`FROST_MIN`, before 0.27.4 0.3), 1 from 0 °C
  (`FROST_FULL_C`); fewer and paler patches and fewer glints in mild cold. The patches are frost flowers now (`rime`:
  six ferns around one point, side branches at 60° that branch once more, small ice stars, needles; drawn bold because
  the image is shown far smaller than built), the glints small four-pointed stars.
- **Puddles mirror (`puddle` → `{base, gloss}`):** irregular with small pools beside them; `base` darkens the wet
  ground, `gloss` is a faint sheen, a few narrow bright streaks in one direction for all puddles and a fine line along
  the edge – brighter by day. Still nothing that shades a body (no gradient across the area, no rim that is dark on one
  side and bright on the other): the first attempts with those looked like pebbles again.
- **`stats`:** `windows` (windows that let light in), `beams` (of them with a patch of sun- or moonlight), `moon`
  (strength 0…1), `sound` (speakers sending waves).
- **Effect lab (`dev/fxlab.py`, `dev/fxlab/`):** the floor plan with all effects on a page of its own – see
  `dev/README.md`. Use it to look at a change before the UI tests run on Windows.
- **UI test `ui_home`:** the rooms stay untouched only if neither light nor sound is there; by day every preview lets
  light in, sunny previews show a patch by day and by night (moon), moonlight exactly with an open sky (since 0.27.5 also through the gaps in `drizzle` and `storm`).

## Since 0.26.1 – daylight, living sun rays, hoarfrost

- **Daylight (`weather_fx.js`, step 0):** by day the surroundings are evenly brighter – one wash over the whole frame up
  to the wall (sky light comes from everywhere, no corner stays dark), `day` = 0 at −6° sun elevation, 1 from 10°;
  dimmer under clouds, dimmest in precipitation and fog, a touch warm under sun. The sunlight gradient on top of it is
  gentler and never runs out to nothing (the far side keeps a third). Reason: white bands on a dark ground read as
  night (wish of the operator) – brightening only one side would have the same wrong effect.
- **Warm sunlight:** new token `--fx-sun: var(--warn)`; sunlight and sun rays are `--fx-light` mixed with it (`mix`),
  pale at noon, golden with a low sun – in steps of 0.1, so the shaft images are rarely rebuilt.
- **Sun rays live (`bornRay`, `rayGlow`):** four shapes (`BEAM_PROFILES`: broad, filaments, fine, widening; each fades
  in at its start), 9 on a clear day, 8 partly cloudy, 4 overcast, 3 in a storm (× 0.7 with `subtle`). Each one fades in,
  shimmers (two slow waves), breathes in width, turns a little against the light and fades out; then it is born anew
  elsewhere in another shape. Below clouds they live shorter (4–10 s), pause longer and start further into the picture;
  on a clear day they live 10–22 s and wander only with the haze (`RAY_HAZE` of the cloud pace).
- **No ray through the house (`shadeRays`):** the rooms of the floor shown are swept along the light beyond the frame
  (room + one parallelogram per edge, all in the same sense of rotation, so the nonzero rule fills the union) and
  punched out of the ray image together with the walls and a soft border. The rays are therefore drawn alone on the
  margin image and laid onto the main image at once. `stats.blocked` = rooms that throw a shaft.
- **A clear day is animated now:** the rays live at the calm pace (12 frames/s, old devices 6, self-regulation as before);
  with `prefers-reduced-motion` they stand (one picture, no frame).
- **Hoarfrost (`fx.frost`):** icy veil, patches of fine ice needles (`rime`: six directions, side branches at 60°, densest
  in a few lobes) fixed on the ground in plan units (`groundSpots`, shared with the puddles: never in the house or under
  another floor, but also on balconies), with the margin to the wall; dimmer at night. The glinting crystals sparkle more
  in sunlight. `stats.frost` = patches in the last frame.
- **UI test `ui_home`:** hoarfrost patches exactly with the preview `frost`; by day with `clear`, `frost`, `heat` sun rays
  and a shadow shaft; at night no ray.

## Since 0.25 – several floors, decks, wandering sun rays

- **Stored form (`floorplan_store.py`, purely additive):** walls, rooms, openings, devices, groups and decks at the top
  level (and, with them, stairs) are the floor with level 0; every further floor carries the same lists (`CONTENT_KEYS`) in
  `floors: [{id, name, level, …}]` – `level` > 0 above, < 0 below, kept without gaps by `_clean`. `floor_id` and
  `floor_name` name the floor at the top level. Width, height, address and all display settings are shared. A plan from
  before 0.25 is valid unchanged and simply one floor. At most `MAX_FLOORS` (8).
- **Readers never look at the top level and `floors` separately:** backend `floorplan_store.floors(plan)`, `rooms(plan)`,
  `groups(plan)` (room assignment, room routes, groups in `control.py` and the flow engine, entitlements); browser
  `HO.floorplan.floorsOf(plan)`, `roomsOf`, `groupsOf`, `hasWalls` (home page, widgets, lights, flow editor, Matter tile).
- **A device stands on exactly one floor – two guards.** Store: `_clean` walks the floors (level 0, then upwards, then
  downwards) with one set `taken`; a device that an earlier floor holds as a position, as a group member or docked is
  dropped on the later one. Ids of floors, rooms and groups are made unique across floors (`_unique`), so `plan:<id>`
  room keys and group ids stay unambiguous. Editor: everything that puts a device onto the floor shown calls `claim(id)`
  first (`placeDevice`, docking through the panel), which takes it off every other floor (`removeFrom`). "Nicht platziert"
  and "Geräte verteilen" look at all floors (`isPlaced` = `floorOf`). Moving a device is explicit: panel of the device →
  "Stockwerk" (`moveToFloor`). `tests/core/test_stores.py` section 5 holds the store rules.
- **Working plan in the browser (`normPlan`, block "Several floors" in `floorplan.js`):** `plan.levels` = all floors in one
  form, bottom to top; `plan.floor` = the floor shown and edited; `plan.walls`, `plan.rooms`, `plan.openings`,
  `plan.devices`, `plan.groups`, `plan.decks`, `plan.stairs` are accessors onto that floor – so all drawing and editing code works on one
  floor and cannot reach another by accident. `JSON.stringify(plan)` yields the stored form again (`toJSON` →
  `storedPlan`): saving, the undo history and `fp.plan` for the home page need nothing else. `st.floorId` keeps the floor
  across fresh data, undo and redo (`openPlan`). Never spread or `Object.assign` a working plan to copy it – clone it.
- **Editor:** second line of the toolbar (`floorBarHTML`): the floors, "+ Stockwerk" (panel `newFloorHTML`: on top or at
  the bottom of the stack; take over the outline of the floor shown – walls, openings and decks with new ids, no devices,
  no room names, no hub links – or start empty), "Durchscheinen" and "Einrasten". The neighbouring floor (`neighbour()`:
  the one below, for the lowest the one above) is drawn dashed below everything (`underlaySVG`, `.fp-under`); `snapPt`
  takes its corners and straight walls as snapping targets. Both are editor preferences of the browser (`ho.fpGhost`,
  `ho.fpFloorSnap`), like the grid. Name and delete of the floor shown: panel without a selection (`floorPanelHTML`,
  `deleteFloor` – asks first; if the floor with level 0 goes, the one above takes its place, `relevel`). The size of the
  area clamps the content of all floors. `autoRooms` skips rooms another floor already shows and never shrinks the area.
- **View:** `fp.floors`, `fp.floor`, `fp.setFloor(id)`, `fp.stepFloor(±1)`, option `floor` (start) and callback `onFloor`.
  Home page and full screen: two buttons with the short name between them (`floorSwitchHTML`, `#fpFloorSw` in
  `overview.js`), remembered per device (`ho.fpFloor`). Widget: option `floorNav` draws the buttons in the frame
  (`.fp-floornav`); the widget setting `floor` is empty (switchable) or the id of a fixed floor. The title names the floor,
  the average is the one of its rooms. A floor below the ground (`level < 0`) has no weather effects and no cast shadow,
  only the soft halo.
- **Decks (`decks`, tool "Balkon"):** balcony or terrace = a closed outline stored like a wall whose last point repeats the
  first (`{id, pts, bend}`, at least three corners, `MAX_DECKS` 40 per floor). Drawn like a wall (the draft carries
  `deck: true`; finishing closes it). Selection type `deck`: drag the area (drag mode `move`), corners and middles like a
  wall (`deckJoint` – a corner moves alone, it never drags a wall along), part of the multi-selection. Look (`decksSVG`,
  CSS block "Floor plan, since 0.25"): a faint floor with one of three surfaces (`surface`: planks of wood as the standard and not stored, `tiles` = square
  grid, `plain` = no pattern; chosen in the panel, pattern in plan units along the longest edge),
  a thin railing line; cut out where a room is (mask, not on `HO.perf.low`). Decks are Basic.
- **Decks and the weather:** a deck is outside – nothing is punched out, rain, snow, light and wind pass over it.
  `fxMask()` hands over `decks` and `covered` (room areas of all other floors, `builtOver`); `seedPuddles` treats both like
  the building: a puddle belongs to the ground and is never painted across a balcony above it or under another floor.
  `shape` contains the floor, the decks and the revision of `covered`, so puddles are laid out anew when the floor changes.
- **Stairs (`stairs`, tool "Treppe"):** `{id, a, b, width}` – a staircase from its foot `a` up to its head `b`
  (`STAIR_WIDTH`: 0.5…6 cells, 2 by default, at most `MAX_STAIRS` 20 per floor); drawn by dragging from the lowest to
  the highest step. Selection type `stair`: drag = move (`smove`), the ends move freely (`send`: length and direction),
  panel with width and "Richtung"; part of the multi-selection. **Two looks from one entry** (`stairsSVG`): on its own
  floor the way up (`stairSVG`: treads, no arrow); on the floor above the same place is the stairwell
  (`stairwellSVG`, `data-kind="stairwell"`, not editable there): a dark pit, a shade that deepens towards the foot
  (gradient along the run), side walls and treads that narrow and fade – it reads as the way down. In the view a tap
  on a staircase goes one floor up, a tap on a stairwell one floor down (`viewTap`). **Stairs above each other**
  (`stacked`: same line, the middle of one on the other): the staircase of the floor shown and the stairwell below it
  share the width, one half each (`span`), so both stay visible and can be tapped. Copying a floor does not copy stairs –
  the stairwell appears by itself. Stairs are Basic.
- **Sun rays wander with the wind (`weather_fx.js`, `rayDrift`):** they move across the light at the pace of the clouds
  (`CLOUD_PACE`), at least `RAY_SIDE_MIN` of it to one side if the wind blows along the light. Since 0.26.1 the rays
  live on their own and a clear day is animated (see "Since 0.26.1").
- **Basic/Pro:** a further floor needs `multi_floorplan` (`entitlements.check_floorplan`, monotonic: existing floors stay
  editable and deletable); the room limit counts the rooms of all floors.
- **Demo home:** balcony in front of the living room; second floor "Dachgeschoss" with a studio, a roof terrace and three
  devices.

## Since 0.24.3 – several devices at one element

- **Docking takes several devices:** a window, a door or a radiator carries up to `MAX_DOCKED` (4) devices – a window its
  contact and its roller shutter, a radiator two thermostats. Stored additively: `openings[].device_id` stays the first
  docked device, `extra_ids` lists the further ones (only present with more than one; `floorplan_store._docked`/`_set_docked`,
  in the browser `dockedIds`/`setDocked`/`undock`). A device hangs on at most one element; `_clean` enforces that.
- **What docks where (`dockTypes`):** thermostat → radiator; contact (or any device reporting `open`) and **cover** → window
  and door. Nothing else docks – lights, plugs, sensors, motion and smoke detectors stand free in the room.
- **Look:** the dots follow the rules of a single one and lie side by side along the element, centred on it (`dockLayout`,
  distance `2r + 4u`). State of the element: one open contact marks the window or door open, one heating thermostat
  highlights the radiator (the strongest counts); the heat map takes the mean of the thermostats of a radiator.
- **Use:** tapping a dot opens that device; tapping the element opens its only device or – with several – all of them
  together (`onGroupClick` with a pseudo group `open:<id>`). In the editor the panel of an element lists the docked devices
  ("Lösen") and offers the fitting ones to add; dragging a device onto an occupied element docks it next to the others.

## Since 0.24.2 – device symbols

- **Three looks, one rule for the state** (`renderSVG`, CSS block "Floor plan: devices" in `app.css`): a device is the
  **standard bubble** (fixed radius `R`, symbol of its kind from `HO.iconPaths`), a **dot** at its window, door or radiator
  when docked, or a **plan symbol** (`.fp-dev.symbol`). A group is a bubble `GROUP_SCALE` times as large. The frame of a
  bubble and the dot of a docked device or plan symbol carry the state in the same colours: accent = idle, `--ok` = active
  (`on`, `heat`, `open`, `motion`), grey = no contact. **Problems never recolour the frame** – they are marks
  (`markSVG`: battery, lock, no contact, fault, number of a group), placed by `deviceMarks` at the same corners for every
  symbol. The bubble cannot be scaled; the size per device kind (`device_styles`) and its input field are gone – the field
  is no longer read or written.
- **Plan symbols (`js/floorplan_symbols.js`, `HO.planSymbols`):** 30 shapes drawn from above like the plan itself, front
  towards the bottom edge (TV, soundbar, speaker, hi-fi, console, computer, printer, router, camera, three lights,
  furniture, household appliances, car). The drawing rules stand at the top of the file (box of 48 units, classes `f`/`l`/`k`,
  colours and line width only in `.fp-sym`, keys are never renamed – the ten keys from before 0.24.2 are kept, so old plans
  show the new drawing). The symbol itself stays neutral, the **status dot** (`.fp-dot`) sits at its top right corner.
- **Size and turn only for plan symbols:** `devices[id].furniture` (key), `size` (0.5…4, 1 is not stored) and `rot`
  (steps of 45°, since 0.29 whole degrees; 0 is not stored); `floorplan_store._clean` only keeps `size`/`rot` together with a symbol. At size 1 the
  box covers `SYMBOL_CELLS` cells, so a symbol keeps its proportion to the rooms at every zoom (`symbolBox`;
  `SYMBOL_MIN_PX` keeps it readable on very small plans). In the editor the selected symbol gets a frame with a handle
  at the corner (`data-kind="symscale"`, drag mode `symscale`), plus −/+ and "Drehen" in the panel for the finger.
- **Symbol picker** (`symbolPicker`, `.fp-sympick`): pictures instead of a list – for a device the standard bubble and
  all plan symbols, for a group additionally the symbols of the device kinds. A plan symbol in a group is fitted into
  the group bubble and is never scaled.
- **Basic/Pro:** `custom_appearance` now counts devices shown as a plan symbol (`entitlements._appearance`).

## Since 0.20.1

- **The weather is attached to the plot (`weather_fx.js`, `carry`):** `getMask()` additionally returns `view` (plan unit → pixel)
  and `shape` (identifier of the building). If the plan sits differently in the frame than in the last picture – gesture, zoom button, new size –,
  drops (with their target), drop rings, flakes, leaves, wind lines, clouds, fog, stars and frost move along; what
  leaves on one side comes back in on the opposite one. **During a gesture the canvas stays put and keeps drawing**
  (`gesture(true)` → `follow()` in step with `drawLive`, the offset comes through `getShift`); only on low-power devices
  the picture stands still and is moved by CSS like the SVG. During the gesture `fxMask()` returns the outline of the painted picture
  (`live.mask`). Never again freeze the weather picture and push it along – it stood still and was cut off at the
  edge (wish of the operator).
- **Nothing jumps:** `seed()` only adjusts the lists (`fit`): existing particles stay, some are added or
  dropped – for new weather values, a new size and a new step of the self-regulation. The key of the mask (`fxMask`)
  works with the position **in the frame**, not on the screen (otherwise every scroll of the page rebuilt the flow
  field). On the phone the height of the view no longer follows the address bar folding in and out (`screenHeight`). A
  calm single picture uses the time of the last picture.
- **Rain:** every drop has a target on the ground somewhere in the picture (`launch`), ends there and triggers its
  drop ring exactly there (`land`) – no ring without a drop. Rings are drawn in one path per brightness step (`RINGS`).
  **Puddles** (`puddle`, `seedPuddles`): flat, irregular water areas as pre-drawn images, fixed places in
  plan units, never in the house; their size slowly follows the rain intensity (`wet`). No light edge, no shading from
  one side – that looked like stones.
- **Wind:** the number of lines grows clearly with the wind (`wispCount`: few at 10 km/h, a multiple in a storm), in gusts
  more are moving (the others rest). A line that runs towards a wall fades out beforehand (`solidAt` looks ahead,
  `out` counts down) instead of ending abruptly. **Leaves from 15 km/h** (`LEAF_MIN`), in the accent colour (`--fx-leaf: var(--accent)`),
  muted like the warmest spot of the heat map (`LEAF_ALPHA` = `HEAT_MAX`), as fast as the lines; not with snow.
  **Clouds** move in wind direction at `CLOUD_PACE` (65 %) of the speed of the lines, all about equally fast.
- **Wind arrow** (`.fp-wind`, `renderWind` in `floorplan.js`): top right in the floor plan, only with weather effects; shows where
  the wind blows to (wind direction + 180° + north of the plan), next to it km/h (`fx.wind_kmh` from `weather.fx`, in previews
  `wind × 80`).
- **Sun:** no round yellow glow any more. The rays arrive in parallel – a straight gradient across the picture in
  very transparent white (`--fx-light: var(--text)`), plus fine bundles of rays (`beam`): few fixed ones on a clear day,
  under clouds one breaks through now and then (`stats.rays`). Since 0.26.1 replaced by daylight and living rays
  (see "Since 0.26.1").
- **Building shadow toned down:** `SHADOW_DAY` 0.4 (before 0.55), `SHADOW_NIGHT` 0.34 (before 0.5).
- **Heat map linear and without adding:** every spot gets the proximity-weighted mean of its thermostats
  (on the scale of the heating plan 0…1) times fall-off – transparent at the lower end of the scale, `HEAT_MAX` at the upper. Several
  thermostats in one room or a warm neighbouring room no longer drive the colour above the room's own value; the `tanh`
  is gone. A room with 17 °C is almost transparent on a scale from 15 to 22 °C, even if 17 °C is its target
  (wish of the operator).
- **Check pictures:** `dev/fx_shots.py` photographs every weather situation (own values too, through
  `HO.state.fxPreview = {base: 'rain', rain: 1, …}`) to `tests/shots/effects/`.

## Since 0.20

- **Camera (`js/floorplan_camera.js`, `HO.planCamera`):** one camera for the view on the home page and the full screen.
  `floorplan.js` reads it with `look()` as `{cx, cy, s, cw, ch}` and sets it with `aim()`; in the view `st.zoom`/`st.zc`
  are behind it, in the full screen `cam`. Gestures: full screen – one finger/mouse pans, two fingers zoom, the mouse wheel zooms around
  the pointer, double click zooms in. View – two fingers or Ctrl+mouse wheel zoom, the mouse wheel alone scrolls the page,
  one finger only pans once zoomed in; plus the buttons −, + and "Ansicht zurücksetzen" (`fpFit`).
- **During the gesture the SVG is not repainted:** `beginLive()` sets `.fp-moving`, `drawLive()` moves and scales
  the SVG by CSS transform (the weather picture has been drawing along itself since 0.20.1, see above), `settleLive()` writes
  the `viewBox` once (and only redraws when the scale changed). In the middle of the gesture a redraw only happens outside of `LIVE_RANGE`. Fresh data during the gesture is
  remembered (`live.dirty`). Never again set the `viewBox` per pointer move – that was the cause of the stutter.
- **Rebuild only on change:** `renderSVG()` compares the generated markup with the last written one (`st.drawn`).
  What should not change the picture is therefore not in the markup: the highlight of a room is set by `markRooms()`, the
  sun position for drawing is rounded to tenths of a degree (`steadySun`), `setAttr` only writes attributes on change.
- **Cold-air pulse in its own rhythm (`pulse`):** the SMIL animation at open windows is paused and advanced 8 times per
  second (low power: 4); during gestures and with a hidden tab it stands still.
- **Economical shadows:** with `HO.perf.low` the floor plan draws shadows without SVG filters (halo from three borders,
  device shadow as the gradient `#fpdevsoft`).
- **Weather effects regulate themselves (`weather_fx.js`, `STEPS`):** every view measures delay and drawing time and takes
  back one step if needed (pixel density, frame rate, amount); from step 2 on the device remembers "low power" (`HO.perf.learnLow`).
  `stats.step` names the step (test, diagnosis).
- **Toolbar above the floor plan** (`.fp-tools.fp-toolscroll`): always one line, scrollable sideways when space is short.
- **Measurements and experiments:** `dev/perf.md`. **Tests:** `tests/ui/ui_camera.py`, `ui_home.py`, `ui_visual.py`.

## Structure and rules

- **Floor plan (`floorplan.json`; since 0.25 per floor, see "Since 0.25"):** only open wall polylines (`bend[i]` = quadratic control point); the browser computes the areas. `rooms` = name, Home Base link, anchor. `openings` = `door|window|passage|radiator`. `devices` = positions (`furniture` = plan symbol with `size` and `rot`, see "Since 0.24.2"; `t`/`s` from old plans are no longer read). **Docking (since 0.19 in the editor as in the view):** if a device is dragged onto a fitting element (thermostat → radiator, contact → window/door), it sits at once as a dot centred on the element – there is no longer a position along the element or a side (since 0.29.1 a dot can be moved along its element again: `along`, see there); dragging the dot away releases it again (then one holds the bubble, `nearestDock` with some holding force). **Unplaced devices** are in the editor as a strip directly below the plan (`.fp-tray`: one line, swipe sideways; tap and then tap on the plan or – with the finger upwards – drag), the other details only below that. A docked device sits as a dot on its element (since 0.12.3): radiator dot in front of the radiator in the room, window/door on an outer wall (`roomSideOf` = room on one side only) outside in front of it, inner ones centred in the opening. Device symbols have a soft shadow (`devShadowSVG`), clipped per symbol to its own room area and below the walls – it never bleeds over walls, into neighbouring rooms or out of the home. `groups` = device groups `{id, name, x, y, members}` (drag a device onto another one/a group in the editor; members have no position of their own and do not dock, a device is in at most one group – `_clean` enforces that). **Editor multi-selection (since 0.12.3, browser only):** `st.msel` (drag a frame, Shift/Ctrl/Cmd+click, "Alles"/Ctrl+A) → frame with handles: move, corners scale uniformly (Shift frees it), side centres on one axis. Transformed are wall points including coinciding points of other walls, curve points, doors/windows on fully moved edges (otherwise `carryOpenings`), free devices/groups (selected or in selected rooms) – never symbols. Room anchors are set anew from the corners that moved along (`roomRefs`), otherwise a deformed room would lose its name and its Home Base link. No `assignByPosition` in the process (no room changes at the Home Base). Grid snapping can be switched off (`gridSnap`, localStorage `ho.fpSnap`); snapping to wall points/walls always stays on. `shadow`/`north` = cast shadow by sun position (since 0.12.3; `sunPos` computes in the browser, place = `geo` from `/api/overview`, rounded from the weather cache, otherwise the middle of Germany – no internet request); the user sets north in the editor (degrees clockwise from "up"), the app does not know the orientation of the plan. Drawn as a luminance mask from `SHADOW_STEPS` copies offset along the shadow direction (light at the back, painted over dark at the front): sharp side edges, fall-off only towards the back, no overlap. Zoom without full screen (`st.zoom`, plus/minus) only changes the `viewBox` (`applyZoomBox`, centre `st.zc`), the SVG area stays the same size; when zoomed in, dragging pans the section. Heat map opacity at most `HEAT_MAX` (linear on the scale, since 0.20.1 without adding several thermostats) – so that accents stay visible. **Scale (since 0.16, since 0.18 from the heating plan alone):** `HO.floorplan.heatScale(heat_range)` is the only source for picture and legend – span of the heating plan from `routines_store.heat_range` (lowest to highest heating value of the active routine, without "Aus"/frost protection below 10 °C or ≤ level OFF, whole degrees, at least 4 °C). **What the thermostats measure or have set does not shift the scale** (wish of the operator): every room gets the colour of its temperature on this fixed scale, below it without colour, above it full. `home_name` = title top left (default "Wohnung") with the mean temperature of the rooms; pointing (mouse) or the first tap (touch) shows room + room temperature there, room names are only in the plan in the editor. Motion glows magenta (no green).
- **Weather effects on the floor plan (since 0.16, Pro `floorplan_weather`):**
  - **Data:** `weather.fx(current)` is the only place that computes `fx = {kind: clear|partly|overcast|fog|rain|snow|storm|thunder, cloud, rain, snow, wind (0..1), gust (0..1, since 0.19.1: measured gusts on the scale of `wind`, never smaller), wind_dir, thunder, storm, hail, frost, heat (0..1), is_day}` from the WMO code and the readings (`cloud_cover, precipitation, rain, showers, snowfall, wind_direction_10m, wind_gusts_10m`, `temperature_2m` – the same egress entry `weather`); `/api/weather` delivers it as `fx`. Overlays: storm (gusts ≥ 60 km/h; `kind = storm` only in otherwise dry weather), hail (WMO 96/99), sleet (`rain` and `snow` at once), frost (dry and ≤ 4 °C since 0.27, with `frost_level`; before ≤ 0 °C), heat (from 28 °C by day with sun). Tests: `tests/core/test_weather.py` section 6.
  - **Module `js/weather_fx.js`** (loaded after `floorplan.js`): `HO.weatherFx.mount(wrap, {after: svg, getMask, getSun, getShift})` → `update(fx, {level})`, `redraw()`, `gesture(on)`, `follow()`, `destroy()`. One `<canvas class="fp-fx">` directly behind the SVG (title/legend/tip lie above it, `pointer-events: none`). **No weather inside:** after every picture rooms (fill) and walls (stroke) are punched out by `destination-out`; drawing only happens in the floor plan area (`getMask().box`). Since 0.27 the inside layer follows after that (light through the windows, sound waves – see "Since 0.27"). Colours only from the tokens `--fx-rain|snow|light|cloud|leaf|flash|fog|star|sun|moon|sound` (`:root`, derived from existing tokens/the accent; light = white, leaves = accent, sun = `--warn`, only ever mixed into light), opacity in the module (mostly ≤ 0.35).
  - **Layers (since 0.17.1, as of 0.26.1, from bottom to top):** daylight (even, by day) · snow cover/frost base tone and hoarfrost patches · puddles in rain · sunlight as a gentle gradient from the sun side (fades in at dusk, a low sun reaches further, heat intensifies it) · clouds (pre-drawn patches `blob()`: dark shadows in the sunlight by day, light veils at night) · fog banks · sun rays with the shadow shaft of the house · stars, rarely a shooting star · wind as a flow around the house (from 10 km/h) · rain in two depths, every drop ends in a drop ring (drizzle finer) · hail · snow in two depths · leaves from 15 km/h · frost glitter · thunderstorm. **Day and night blend smoothly:** `dark` = 0…1 from the sun elevation of `getSun()` (0° … −6°), at night everything is muted (`pk`). Every kind of weather shows something outside – also at night and also without particles (`ui_home` checks that).
  - **Margin around the house (since 0.18, since 0.19 not for clouds and fog):** precipitation, wind lines, sunshine, stars and lightning leave a narrow margin free around the building (`MARGIN`, soft transition `FEATHER`) – no drop and no glow sticks to the wall. **Clouds and fog reach up to the wall** (wish of the operator): they are drawn onto the main picture, everything with a margin onto a second picture in memory (`edgeCanvas`) that is punched out with the margin and laid on top (`onEdge`/`flushEdge`/`punch`).
  - **Wind (since 0.19):** clouds **and fog banks** move in wind direction (`windVec`: `wind_dir` + north of the plan). Precipitation always falls from top to bottom and only leans with the wind. **Wind lines from 10 km/h** (`WIND_FX_MIN` = 10/80, because `fx.wind` = km/h ÷ `weather.WIND_MAX_KMH`), few and delicate in light wind; leaves since 0.20.1 from 15 km/h (`LEAF_MIN`).
  - **Layout of the home page (since 0.19.2, `overview.js`, title "ComfyOS" – since 0.19.3 only on the phone; on the desktop the bar at the top shows greeting and date (`setTitle`, `.pt-short`/`.pt-long`) and `#heroBox` is hidden because the sidebar already names "ComfyOS"; logo and name lead to the home page, `[data-home-link]`):** header line **without a tile** `#heroBox` (`heroHTML`: greeting and date, `.section-head` as for Flow/Heizplan/Szenen) → `#warnBox` → **one** main tile (`section.card.home-card`) with the floor plan (`.card-head` with `.fp-tools`, `#fpHost`, `#fpFoot`) and below it home/away including the heating routine (`homeHTML`, `#homeBox`) → `#infoBox` (`infoHTML` = `houseLightHTML`: tile "Licht & Szenen" with house scene and all lights on/off + `statsHTML`: key figures on their own). The overall state ("Alles OK"/"Handlungsbedarf" including reasons from `issuesOf`) is in the tile "Geräte online" (`button.stat.stat-status`, `data-act="issues"` → filter "Probleme"). `partial()` redraws `#heroBox`, `#homeBox` and `#infoBox`. That way the floor plan is visible as completely as possible on opening; new content belongs below the floor plan, not into the main tile.
  - **On the phone (≤ 560 px, since 0.19.2):** tools above the floor plan only as symbols (`.fp-tool .lbl` hidden, name in `title`/`aria-label`); below the floor plan the address stands alone in the first line, below it weather and warning sign in **one** line (`.fp-loc` → `.fp-addr` + `.fp-wxrow`; `ui_home` checks both).
  - **Frame across the whole tile width (since 0.19.1, home page only, view only):** `#fpHost .fp-wrap.view:not(.cam)` has negative side margins the size of the inner padding of the tile (`--card-pad` on `.card`), `floorplan.js` `size()` sets the width of the SVG to the whole width. If the height limits the plan, it stays the same size in the middle and the section becomes wider (`st.viewW` in plan units, `applyZoomBox` works with it) – the weather effects get the space in addition (`fxMask().box` = rectangle of the SVG). In the editor the frame stays exactly the plan; the full screen (`cam`) is untouched. `ui_home` checks margin and centre.
  - **Wind as a flow around the house (since 0.19.1, replaces the short straight strokes – they looked like rain falling upwards):**
    - **Field:** `buildFlow` computes a potential flow through the stream function ψ on a coarse grid (at most `FLOW_CELLS` = 2800 cells, old devices 1400; reaches 30 % beyond the section): the free wind at the border, every connected part of the building (punch mask including margin, drawn into the grid) an obstacle with fixed ψ = mean of the free wind over its area; solved by over-relaxation. The lines thus wrap around every building shape, faster at the flanks, calmer in the lee.
    - **Never compute per picture:** `ensureFlow` only rebuilds when the mask (object from `fxMask`, cached per section) or the wind direction changes, with a moving camera at most every 300 ms (`HO.weatherFx.stats.builds`). Per picture only lookups happen (`flowAt`: bilinear + two shear waves for the meandering + at most 4 eddies).
    - **Lines:** `wisps` – particles with `TRAIL` points of history (12, old devices 8), drawn in `SHADES` brightness steps (one stroke call per step), fade in and out, live 1.8–3.6 s, start at random in the section. Number by area and wind force, speed 30…220 px/s – length and speed show the wind force. Reduced motion: half as many, as standing streamlines (no animation frame).
    - **Eddies:** from `EDDY_MIN` (~12 km/h) small eddies detach in the lee alternately from both flanks (`eddies`, at most 4, sense of rotation of the shear layer), drift off at 55 % of the wind speed and fade in and out softly.
    - **Interplay:** snow and leaves read the same field (snow still always falls downwards, at least 30 % of its speed; inside the building `flowAt` returns the free wind so that nothing gets stuck). Rain and fog damp the lines, in snowfall they carry the snow colour. The gusts (`fx.gust` compared with `fx.wind`) determine how strongly the one common value `wind` swells and subsides – it drives lines, clouds, fog, rain lean, snow and leaves.
    - **Weather data stays one request:** gusts are in the existing request to Open-Meteo; the DWD only delivers the official warnings. No new egress.
  - **Wind report and hourly forecast (since 0.18, part of `floorplan_weather`):** the same weather request additionally delivers `hourly` (2 days); `/api/weather` returns `wind_report` (`weather.wind_report`: km/h, gusts, direction, compass direction, Beaufort) and `today` (`weather.hourly_forecast`: 24 hours from now) – without the feature (with `enforcing()`) the route leaves out both. Interface: tapping the weather below the floor plan → `weatherSheet()`; in the full screen the unfolded details (`wxProHTML`). No additional request, no new egress.
  - **Performance (do not soften):** ≤ 30 frames/s as long as something falls, flies or flashes; calm pictures (only clouds, fog, stars, frost, sun rays) 12 frames/s, old devices 6; since 0.26.1 a clear day runs at this calm pace (living sun rays). `devicePixelRatio` ≤ 2, particles by area and halved on old devices (without native pointer events or ≤ 2 cores); pause with a hidden tab and out of sight (`IntersectionObserver`); `prefers-reduced-motion` = one calm single picture without particles and without `requestAnimationFrame` (`HO.weatherFx.stats.frames` stays 0 – UI test `tests/ui/ui_home.py`).
  - **Integration:** `floorplan.js` `syncFx()` at the end of `renderSVG` (view only, not in the editor, only with rooms); `fp.setWeather(fx)` from the weather request of the home page (`overview.js` `updateWeather`, no second request); the camera calls `redraw()`; `destroy()` of the floor plan clears the canvas away. The building shadow takes `cloud` for opacity `× (1 − 0.6·cloud)` and blur – only when effects are active. **Sun position in one place:** `sunNow()` in `floorplan.js` feeds shadow and `getSun()`.
  - **Shadow at night (since 0.17.1):** below 1° sun elevation `nightShadowSVG` draws a soft, even halo around the building instead of the cast shadow (`.fp-shadow.night`, fades in down to −6°, clouds damp it) – also without weather effects.
  - **Gate & settings:** without `floorplan_weather` (`HO.can`) nothing is mounted, shadows as before; weather data and symbol stay for everybody. `floorplan.json` `fx` (default on) and `fx_level` (`normal`|`subtle`) – pure presentation, no backend gate. **Switch:** button "Wetter" above the floor plan (`data-act="fpFx"`, in the full screen `pvFx`) → `fp.toggleFx()` (saves at once) – the same value as the setting in the editor; without address/weather a note explains the way. Preview only for owner/`COMFYOS_DEV=1` (`HO.license.licensed_plan`/`dev`): selection in the floor plan editor or `#home?fx=<kind>&time=day|dusk|night` (also `#plan`) → `HO.state.fxPreview` and `HO.state.fxTime` (until reload). Previews = `HO.weatherFx.KINDS` (14: the eight kinds plus `drizzle`, `sleet`, `breeze`, `hail`, `frost`, `heat`).
- **Official weather warnings (since 0.17.1, `weather.alerts`, egress `weather_warnings`):**
  - **Source:** WFS of the Deutscher Wetterdienst (`maps.dwd.de/geoserver/dwd/ows`, `typeName=dwd:Warnungen_Gemeinden`, verified 11/2026). The municipality containing the point is queried: `CQL_FILTER=INTERSECTS(THE_GEOM, POINT(<latitude> <longitude>))` – **with WFS 2.0 the latitude comes before the longitude**; `propertyName` saves the municipality borders. Only the coordinates from the weather cache rounded to 2 decimals are sent (`_geocode`, since 0.17.1 also remembers `cc`). If the place is not in Germany (`cc` ≠ `DE`), nothing is queried.
  - **Selection (`parse_warnings`):** only `STATUS = Actual`, no `MSGTYPE = Cancel`, nothing expired, the same warning for several parts of a municipality once; sorted running → level → start, at most `WARN_MAX`. Per warning `{id, event, headline, level 1–4 (Minor…Extreme), onset, expires, active, description, instruction, windows}`; `windows` = window hint only for wind, thunderstorm, rain, hail, snow (`EC_GROUP` or a word in the event), not for frost, ice, fog, heat. Texts are limited and only go through `HO.esc` in the browser.
  - **Request:** no background task of its own. `/api/weather` delivers `warnings` and `warnings_source` along (10 minutes cache, on failure known warnings stay until they expire, never an error). `floorplan.json` `warnings` (default on, only effective with address and `weather`) – switch with disclosure in the floor plan editor directly below the weather.
  - **Interface (`overview.js`):** `#warnBox` above the main tile (`warnHTML`: most important warning, level, period, "+ n weitere"), window hint from the contacts (`windowTipHTML`: names open ones, live through `partial()`), "Details" → `warnSheet()`, hiding per device (`localStorage` `ho.warnHide`, holds until the warning changes) – the warning sign next to the weather (`.fp-warn`, in the full screen `.pv-chip.warn`) stays. The home page now asks for the weather every 30 s (`pollWeather`; browser cache 10 minutes).
  - **Deliberately not included:** no event in the log, no flow trigger, no push – that would need a request in the background. Tests: `tests/core/test_weather.py` sections 7–9, `tests/security/test_privacy.py` 3b, `tests/ui/ui_home.py`. **Tests with an address in the floor plan replace `weather._client` and `weather._warn_client`.**
- **Temperature colours & heat map follow the accent colour (since 0.15):** `HO.accentRGB()` reads `--accent`; `HO.tempRGB`/`tempColor`/`tempGradient` (heating plan, legend) run from grey to the accent colour, the heat map in the floor plan paints in `HO.accentRGB()` (part of the cache key). No fixed magenta/pink any more – derive new colour scales from the accent too.
