/* ComfyOS – weather effects around the floor plan (Pro: floorplan_weather)
   A <canvas> above the floor plan SVG (pointer-events: none). Drawing only happens OUTSIDE the building:
   after every frame rooms and walls are punched out again (destination-out) – inside there are neither particles
   nor lightning. The building shadow stays in the SVG (floorplan.js). Here, from bottom to top:
     daylight · snow cover/hoarfrost patches · puddles in rain · sunlight as a gentle gradient from the sunny side
     (with dusk, heat) · cloud shadows (day) or cloud veil (night) · fog banks · sun rays with the shadow shaft of
     the house · stars and rarely a shooting star · wind as a flow around the house · rain in two
     depths, every drop ends in a ripple · hail · snow in two depths · leaves in the wind ·
     hoarfrost glitter · thunderstorm.
   Day and night blend smoothly into each other (sun elevation from getSun); at night everything is dampened.

   HO.weatherFx.mount(container, {after, getMask, getSun, getShift})
       -> {update(fx, {level}), redraw(), gesture(on), follow(), destroy(), animating}
     getMask()  = {box: {x, y, w, h}, polys: [[[x, y], …]], walls: [[[x, y], …]], wall: px,
                   view: {x, y, s} (plan unit -> pixel), shape: id of the building,
                   decks: [[[x, y], …]], covered: [[[x, y], …]]}   (CSS pixels in the container)
                  decks = balconies and terraces of the floor shown, covered = what other floors build over. Both are
                  outside for the weather – rain, snow, light and wind pass over them, they are not punched out. Only
                  the puddles keep off them: a puddle lies on the ground, never on a balcony above it or under a roof.
     getSun()   = {el: degrees, dir: [x, y] direction to the sun in the plan, north: degrees}
     getShift() = {k, x, y} or null: how the painted plan is moved and scaled right now during a gesture
   fx (from /api/weather) = {kind, cloud, rain, snow, wind, gust, wind_dir, thunder, storm, hail, frost, heat, is_day}

   Margin around the house: precipitation, wind, light and stars keep a narrow margin to the wall free (MARGIN). Clouds
   and fog lie flat over the surroundings and reach up to the wall – they are drawn onto the main image,
   everything with a margin onto a second image (in memory), which is punched out with a margin and then laid on top.
   Wind (a small flow computation since 0.19.1, see "Flow around the house"): from 10 km/h (WIND_FX_MIN) lines
   with a fading tail move in the wind direction and wrap around the building – faster at the flanks, calmer in
   the lee, where small vortices detach with more wind. More wind means more and faster lines, in
   gusts further ones join; one that runs towards a wall fades out before. From 15 km/h (LEAF_MIN) leaves in the
   accent colour drift along. Snow and leaves read the same field. Clouds move in the wind direction, a little slower
   than the lines; precipitation always falls from top to bottom and only leans with the wind. The effects
   influence each other: gusts (fx.gust) drive everything together, rain and fog dampen the wind lines, in
   snowfall they become drifting snow.
   Nothing jumps (since 0.20.1): if weather values or the size change, existing particles stay where they are –
   some are only added or dropped. The weather is attached to the plot: if the floor plan is moved or
   zoomed (gesture, buttons, new size), rain, snow, clouds, wind lines, puddles and stars move with the plan
   (carry) – they keep running and fill the whole frame; what is pushed out comes back in on the other
   side. The canvas itself stays in place for that, only its content and the punched-out house move along
   (economical devices: during the gesture the image stands still and moves as a whole, afterwards it lies right).

   Daylight (since 0.26.1): by day the whole surroundings are evenly brighter (sky light, dimmer under clouds and in
   precipitation) – no corner stays dark. The sunlight on top of it is a gentle gradient from the sunny side that never
   runs out to nothing, in a warm tint (--fx-sun, warmer the lower the sun stands).

   Sun rays (since 0.25, reworked in 0.26.1): shafts of light of different shapes (broad, split into filaments, fine,
   widening), each with its own life – it fades in, shimmers, breathes in width, turns a little and fades out, then a
   new one appears elsewhere; below clouds they come and go faster and leave pauses. They wander with the clouds across
   the light (rayDrift), on a clear day slowly with the haze (RAY_HAZE). The building throws a shadow shaft into
   them (shadeRays): a ray that hits the house does not continue behind it.

   Hoarfrost (frost, since 0.26.1): an icy veil, patches of fine ice needles lying fixed on the ground (like the
   puddles, never in the house, but on balconies) and single crystals that glint – more in sunlight.

   Performance: at most 30 frames/s, calm images (only clouds, fog, stars, hoarfrost, sun rays) 12 frames/s,
   devicePixelRatio ≤ 2, particles by area; if a device cannot keep up, the view regulates itself down (STEPS: first
   pixels, then frame rate and amount). Clouds, fog, puddles, hoarfrost and light shafts as pre-drawn images, the
   flow field is only recomputed if section or wind direction change (never per frame), pause with a
   hidden tab and out of sight, prefers-reduced-motion = one calm image without moving particles and without
   requestAnimationFrame (wind then as a few standing streamlines, the sun rays stand). Since 0.26.1 a clear day
   is no longer a single image: the sun rays live at the calm pace. */
(function (HO) {
  'use strict'
  const REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  // economical devices (HO.perf, core.js): smaller flow grid, shorter tails, fewer ripples
  const OLD = !!(HO.perf && HO.perf.low)
  /* Steps of the self-regulation: every view measures how punctually its frames arrive and what drawing costs.
     If it cannot keep up, it goes back one step – first pixels, then frame rate and amount. The effects
     stay, they only become more economical. fps = frames per second with precipitation/wind, calm = with a calm image,
     dpr = highest pixel density, amount = share of the particles. Economical devices start at START_LOW. */
  const STEPS = [
    { fps: 30, calm: 12, dpr: 2, amount: 1 },
    { fps: 30, calm: 12, dpr: 1.25, amount: 0.8 },
    { fps: 24, calm: 8, dpr: 1, amount: 0.6 },
    { fps: 15, calm: 6, dpr: 1, amount: 0.4 }
  ]
  const START_LOW = 2
  const LAG_LIMIT = 1.4      // frames arrive 40 % later than planned on average
  // Drawing itself may take at most a quarter of the time between two frames. Only the time in the script is
  // measured; transferring the image to the screen costs the browser about as much again afterwards –
  // together a good half of the time is left for everything else (controls, live messages).
  const COST_LIMIT = 0.25
  const CHECK_EVERY = 30     // frames between two checks
  const MARGIN = 7, FEATHER = 6   // px: free margin around the building and width of the soft transition
  const WIND_FX_MIN = 10 / 80     // fx.wind ist km/h ÷ 80 (weather.WIND_MAX_KMH): Windlinien ab 10 km/h
  const LEAF_MIN = 15 / 80        // leaves from 15 km/h
  const LEAF_ALPHA = 0.68         // leaves in the accent colour, as dampened as the warmest place of the heat map (HEAT_MAX)
  const CLOUD_PACE = 0.65         // clouds move a little slower than the wind lines
  const RAY_SIDE_MIN = 0.35       // sun rays always wander at least this share of the cloud pace across the light
  const RAY_HAZE = 0.3            // on a clear day they wander only with the haze: this share of the cloud pace
  const FROST_UNIT = 0.36         // in the hoarfrost image the radius 1 corresponds to this share of the image edge
  const EDDY_MIN = 0.15           // Wirbel im Windschatten ab ~12 km/h, voll ab ~50 km/h
  const FLOW_CELLS = OLD ? 1400 : 2800   // flow grid: at most this many cells
  const TRAIL = OLD ? 8 : 12      // points in the tail of a wind line
  const SHADES = 5                // brightness levels of the tails (one stroke call per level)
  const FADE_OUT = 0.4            // s: a wind line running towards a wall fades out for this long
  const RINGS = 5                 // brightness levels of the ripples (one stroke call per level)
  const MAX_RIPPLES = OLD ? 180 : 640
  const PUDDLE_UNIT = 0.27        // in the puddle image the radius 1 corresponds to this share of the image edge
  const SUNNY = ['clear', 'partly', 'overcast', 'storm']   // weather kinds in which sunlight can be seen
  const TAU = Math.PI * 2
  // frames = drawn animation frames (UI test: with reduced motion it stays 0); wisps = wind lines,
  // cells/solid = cells of the flow grid and those of them that are building, builds = how often the field was computed
  // step = step of the self-regulation of the view drawn last (0 = full); leaves, puddles, ripples = leaves,
  // puddles, ripples in the last frame; rays = sum of the brightness of the sun rays visible right now,
  // blocked = parts of the building that throw a shadow shaft into them, frost = hoarfrost patches in the last frame
  const stats = { frames: 0, wisps: 0, cells: 0, solid: 0, builds: 0, step: 0, leaves: 0, puddles: 0, ripples: 0, rays: 0,
    blocked: 0, frost: 0 }
  const rnd = (a, b) => a + Math.random() * (b - a)
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))

  /* Previews (owner/development): the eight weather kinds of the backend (fx.kind) and their overlays */
  const BASE = { cloud: 0, rain: 0, snow: 0, wind: 0.15, wind_dir: 250, thunder: false, storm: false, hail: false, frost: false, heat: 0 }
  const PRESETS = {
    clear: { cloud: 0.05, wind: 0.1 },
    partly: { cloud: 0.5, wind: 0.2 },
    overcast: { cloud: 0.95, wind: 0.1 },
    fog: { cloud: 1, wind: 0.05 },
    drizzle: { kind: 'rain', cloud: 0.85, rain: 0.15, wind: 0.1 },
    rain: { cloud: 0.9, rain: 0.6, wind: 0.35 },
    sleet: { kind: 'rain', cloud: 0.95, rain: 0.4, snow: 0.35, wind: 0.3 },
    snow: { cloud: 0.9, snow: 0.6, wind: 0.15, wind_dir: 20 },
    breeze: { kind: 'partly', cloud: 0.4, wind: 0.55 },
    storm: { cloud: 0.7, rain: 0.35, wind: 0.95, wind_dir: 260, storm: true },
    thunder: { cloud: 1, rain: 0.7, wind: 0.5, wind_dir: 230, thunder: true },
    hail: { kind: 'thunder', cloud: 1, rain: 0.5, wind: 0.5, wind_dir: 230, thunder: true, hail: true },
    frost: { kind: 'clear', cloud: 0.1, wind: 0.1, frost: true },
    heat: { kind: 'clear', cloud: 0.05, wind: 0.1, heat: 0.8 }
  }
  const KINDS = Object.keys(PRESETS)
  const LABELS = { clear: 'Klar', partly: 'Teilweise bewölkt', overcast: 'Bedeckt', fog: 'Nebel', drizzle: 'Nieselregen', rain: 'Regen',
    sleet: 'Schneeregen', snow: 'Schnee', breeze: 'Windig', storm: 'Sturm', thunder: 'Gewitter', hail: 'Gewitter mit Hagel',
    frost: 'Frost', heat: 'Hitze' }
  // Weather of a preview: the name of a weather kind or – for check images (dev/fx_shots.py) – an object
  // {base: 'rain', rain: 1, wind: 0.5, …} that replaces single values of the weather kind.
  function preset(name, isDay) {
    const custom = name && typeof name === 'object' ? name : null, base = custom ? custom.base || 'clear' : name
    if (!PRESETS[base]) return null
    return Object.assign({}, BASE, { kind: base }, PRESETS[base], custom, { is_day: isDay !== false, preview: true })
  }

  // Read the colours of the effects from the CSS variables (with defaults if one is missing).
  function tokens() {
    const cs = getComputedStyle(document.documentElement), v = n => (cs.getPropertyValue(n) || '').trim()
    return { rain: v('--fx-rain') || '#c2c0c8', snow: v('--fx-snow') || '#f2f1f4', light: v('--fx-light') || '#f2f1f4',
      cloud: v('--fx-cloud') || '#0e0e11', leaf: v('--fx-leaf') || '#109482', flash: v('--fx-flash') || '#f2f1f4',
      fog: v('--fx-fog') || '#c2c0c8', star: v('--fx-star') || '#f2f1f4', sun: v('--fx-sun') || '#f2b33d' }
  }
  // token -> "r,g,b": gradients thus fade out in the same colour (not through black)
  const rgbCache = {}
  // Convert a CSS colour into red/green/blue (through a 1-pixel canvas, remembered per colour).
  function rgb(color) {
    if (rgbCache[color]) return rgbCache[color]
    const c = document.createElement('canvas')
    c.width = c.height = 1
    const g = c.getContext('2d')
    g.fillStyle = color; g.fillRect(0, 0, 1, 1)
    const d = g.getImageData(0, 0, 1, 1).data
    return (rgbCache[color] = d[0] + ',' + d[1] + ',' + d[2])
  }
  const rgba = (color, a) => 'rgba(' + rgb(color) + ',' + a + ')'
  const mixCache = {}
  // Mix two CSS colours (w = share of the second, 0…1) – for the warm tint of the sunlight; remembered per combination.
  function mix(a, b, w) {
    const key = a + '|' + b + '|' + w
    if (mixCache[key]) return mixCache[key]
    const p = rgb(a).split(',').map(Number), q = rgb(b).split(',').map(Number)
    return (mixCache[key] = 'rgb(' + p.map((v, i) => Math.round(v + (q[i] - v) * w)).join(',') + ')')
  }
  // Bring a list to n entries: existing ones stay where they are – if only the strength of the weather changes,
  // nothing jumps; particles are added or the last ones drop out.
  function fit(list, n, make) {
    if (list.length > n) list.length = n
    for (let i = list.length; i < n; i++) list.push(make(i))
    return list
  }
  // Liegt der Punkt im Vieleck? (Strahl-Verfahren)
  function within(poly, x, y) {
    let hit = false
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1]
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) hit = !hit
    }
    return hit
  }
  // Put the outline of the building where the painted plan currently is during a gesture (moved, scaled).
  function moved(mask, s) {
    const T = p => [p[0] * s.k + s.x, p[1] * s.k + s.y]
    const view = mask.view ? { x: mask.view.x * s.k + s.x, y: mask.view.y * s.k + s.y, s: mask.view.s * s.k } : null
    return { box: mask.box, polys: (mask.polys || []).map(p => p.map(T)), walls: (mask.walls || []).map(p => p.map(T)),
      decks: (mask.decks || []).map(p => p.map(T)), covered: (mask.covered || []).map(p => p.map(T)),
      wall: (mask.wall || 6) * s.k, view, shape: mask.shape }
  }
  // Soft, irregular blob (cloud, fog bank): built once from overlapping gradients, after that only drawImage
  function blob(color) {
    const w = 192, h = 112, c = document.createElement('canvas')
    c.width = w; c.height = h
    const g = c.getContext('2d')
    for (let i = 0; i < 7; i++) {
      const r = rnd(26, 52), x = rnd(r, w - r), y = rnd(r, h - r)
      const gr = g.createRadialGradient(x, y, 0, x, y, r)
      gr.addColorStop(0, rgba(color, rnd(0.45, 0.8))); gr.addColorStop(1, rgba(color, 0))
      g.fillStyle = gr; g.fillRect(x - r, y - r, r * 2, r * 2)
    }
    return c
  }
  // Shapes of the light shafts: profile across (position 0…1, brightness) and how wide a shaft is at its start compared
  // to its end – 0 = broad soft shaft, 1 = split into several filaments, 2 = a single fine filament, 3 = narrow at first
  // and widening.
  const BEAM_PROFILES = [
    { taper: 0.7, stops: [[0, 0], [0.22, 0.45], [0.5, 1], [0.78, 0.45], [1, 0]] },
    { taper: 0.75, stops: [[0, 0], [0.12, 0.5], [0.22, 0.1], [0.35, 0.85], [0.46, 0.25], [0.58, 1], [0.7, 0.2], [0.83, 0.6], [1, 0]] },
    { taper: 0.85, stops: [[0, 0], [0.35, 0.1], [0.5, 1], [0.65, 0.1], [1, 0]] },
    { taper: 0.35, stops: [[0, 0], [0.25, 0.6], [0.45, 0.9], [0.6, 0.7], [0.8, 0.35], [1, 0]] }
  ]
  /* Light shaft (sun rays) in one of the shapes of BEAM_PROFILES. Along its length it fades in briefly (no hard start)
     and then slowly out. Built column by column once per shape and tint, after that only drawImage. The sunny side is
     on the left. */
  function beam(color, shape) {
    const w = 256, h = 48, c = document.createElement('canvas'), profile = BEAM_PROFILES[shape]
    c.width = w; c.height = h
    const g = c.getContext('2d')
    for (let x = 0; x < w; x += 2) {
      const p = (x + 1) / w, along = Math.min(1, p / 0.08) * Math.pow(1 - p, 1.2)
      const half = h / 2 * (profile.taper + (1 - profile.taper) * p)
      const across = g.createLinearGradient(0, h / 2 - half, 0, h / 2 + half)
      for (const stop of profile.stops) across.addColorStop(stop[0], rgba(color, stop[1] * along))
      g.fillStyle = across; g.fillRect(x, h / 2 - half, 2, half * 2)
    }
    return c
  }
  /* Hoarfrost patch: fine ice needles in the six directions of the ice crystal, some with side branches at 60°, densest
     in the middle of a few soft lobes and thinning out towards an irregular edge; below them a faint white haze. Built
     once, after that only drawImage. */
  function rime(color) {
    const size = 224, c = document.createElement('canvas')
    c.width = c.height = size
    const g = c.getContext('2d'), mid = size / 2, R = size * FROST_UNIT
    const lobes = [{ x: 0, y: 0, r: 1 }]
    for (let i = Math.floor(rnd(2, 4)); i > 0; i--) {
      const a = rnd(0, TAU), d = rnd(0.3, 0.65)
      lobes.push({ x: Math.cos(a) * d, y: Math.sin(a) * d, r: rnd(0.4, 0.7) })
    }
    // how dense the frost is at a point (in units of the radius): 1 in the middle of a lobe, fading outwards
    const density = (x, y) => {
      let most = 0
      for (const l of lobes) {
        const dx = x - l.x, dy = y - l.y
        most = Math.max(most, Math.exp(-2.2 * (dx * dx + dy * dy) / (l.r * l.r)))
      }
      return most
    }
    for (const l of lobes) {
      const x = mid + l.x * R, y = mid + l.y * R, r = l.r * R * 1.1, haze = g.createRadialGradient(x, y, 0, x, y, r)
      haze.addColorStop(0, rgba(color, 0.1)); haze.addColorStop(1, rgba(color, 0))
      g.fillStyle = haze; g.fillRect(x - r, y - r, r * 2, r * 2)
    }
    g.strokeStyle = color; g.lineCap = 'round'
    for (let i = 0; i < 520; i++) {
      const u = rnd(-1.3, 1.3), v = rnd(-1.3, 1.3), d = density(u, v)
      if (Math.random() > d) continue
      const x = mid + u * R, y = mid + v * R, a = Math.floor(rnd(0, 6)) * Math.PI / 3 + rnd(-0.12, 0.12)
      const len = rnd(2.5, 7) * (0.6 + 0.4 * d), cx = Math.cos(a), cy = Math.sin(a)
      g.globalAlpha = rnd(0.25, 0.7) * (0.5 + 0.5 * d); g.lineWidth = rnd(0.6, 1)
      g.beginPath(); g.moveTo(x - cx * len / 2, y - cy * len / 2); g.lineTo(x + cx * len / 2, y + cy * len / 2)
      if (Math.random() < 0.45) {
        const bx = x + cx * len * 0.15, by = y + cy * len * 0.15, b = len * 0.45
        for (const side of [-1, 1]) { g.moveTo(bx, by); g.lineTo(bx + Math.cos(a + side * Math.PI / 3) * b, by + Math.sin(a + side * Math.PI / 3) * b) }
      }
      g.stroke()
    }
    return c
  }
  /* Puddle: a flat, irregular water surface – main shape with one or two bulges, the outline from a few
     gentle waves, stretched and rotated. Flat means: no light edge, no shading from one side (that
     would look like a stone). The outline can just about be made out, towards the inside the water gets evenly
     denser – the same shape many times on top of each other, each time a little smaller, gives the gradient. Built once,
     after that only drawImage. */
  function puddle(color) {
    const size = 192, c = document.createElement('canvas')
    c.width = c.height = size
    const g = c.getContext('2d'), stretch = rnd(1.5, 2.3)
    const waves = [1, 2, 3, 5].map(n => ({ n, a: rnd(0.04, 0.16) / Math.sqrt(n), ph: rnd(0, TAU) }))
    const lobes = [{ x: 0, y: 0, r: 1 }]
    for (let i = Math.floor(rnd(1, 3)); i > 0; i--) {
      const a = rnd(0, TAU)
      lobes.push({ x: Math.cos(a) * rnd(0.45, 0.75), y: Math.sin(a) * rnd(0.3, 0.55), r: rnd(0.35, 0.6) })
    }
    g.translate(size / 2, size / 2); g.rotate(rnd(0, TAU)); g.scale(size * PUDDLE_UNIT, size * PUDDLE_UNIT)
    for (let layer = 0; layer < 12; layer++) {
      const shrink = 1 - layer * 0.055
      g.fillStyle = rgba(color, layer ? 0.06 : 0.2)      // the outline outside, the gradient towards the inside
      g.beginPath()
      for (const lobe of lobes) {
        for (let i = 0; i <= 48; i++) {
          const a = i / 48 * TAU
          let r = lobe.r * shrink
          for (const w of waves) r *= 1 + w.a * Math.sin(w.n * a + w.ph + lobe.r * 9)
          const x = lobe.x * shrink + Math.cos(a) * r, y = (lobe.y * shrink + Math.sin(a) * r) / stretch
          if (i) g.lineTo(x, y); else g.moveTo(x, y)
        }
        g.closePath()
      }
      g.fill()
    }
    return c
  }

  // Mount the weather canvas into the frame of the floor plan. opts: getMask (outline of the building), getSun (position
  // of the sun), getShift (offset of the plan during a gesture), after (element the canvas stands behind). Returns the
  // controller: update, redraw, gesture, follow, destroy.
  function mount(container, opts) {
    const canvas = document.createElement('canvas')
    canvas.className = 'fp-fx'
    canvas.setAttribute('aria-hidden', 'true')
    canvas.style.opacity = '0'   // fades in softly after the first frame
    if (opts.after && opts.after.parentNode === container) container.insertBefore(canvas, opts.after.nextSibling)
    else container.appendChild(canvas)
    const mainCtx = canvas.getContext('2d')
    // second image in memory for everything that keeps a margin to the wall free (see the head of the file)
    const edgeCanvas = document.createElement('canvas'), edgeCtx = edgeCanvas.getContext('2d')
    let ctx = mainCtx     // what is being drawn on right now (draw switches between main image and margin image)
    let fx = null, level = 'normal', col = tokens(), W = 0, H = 0, dpr = 1
    let drops = [], ripples = [], flakes = [], pellets = [], leaves = [], wisps = [], clouds = [], banks = [], stars = [], glints = []
    let rays = [], puddles = [], cloudImg = [], cloudTint = '', fogImg = [], beamImg = [], beamTint = ''
    let puddleShape = null, wet = 0       // puddles: for which building they lie and how large they are right now (0…1.3)
    let frost = [], frostShape = null     // hoarfrost patches (plan units) and the building they were laid out for
    let sunUp = false                     // sunlight (and with it sun rays) can be seen right now
    let raf = 0, last = 0, lastStamp = 0, visible = true, alive = true, shown = false, seedKey = '', held = false
    let shift = null                      // offset of the painted plan in the frame being drawn right now (getShift)
    let movedOf = { mask: null, k: 0, x: 0, y: 0, val: null }
    let lastView = null                   // position of the plan in the frame drawn last (carry takes the weather along)
    let rayPan = [0, 0]                   // the sun rays have moved this far with the plan (pixels)
    let rayDrift = 0                      // … and this far across the light with the wind, like the clouds (pixels)
    // Self-regulation (STEPS). The performance tier of the device counts at the time of mounting – if an earlier
    // view learned "economical", this one starts right there.
    let step = HO.perf && HO.perf.low ? START_LOW : 0, lag = 1, cost = 0, sinceCheck = 0
    stats.step = step
    let flash = null, nextFlash = 0, meteor = null, nextMeteor = 0, darkNow = 0
    const rings = []
    for (let i = 0; i < RINGS; i++) rings.push([])

    /* ---------- Day/night: 0 = day, 1 = night, dusk in between (sun 0° … −6°) */
    const sunNow = () => opts.getSun ? opts.getSun() : null
    const darkOf = sun => sun && typeof sun.el === 'number' ? clamp(-sun.el / 6, 0, 1) : (fx && fx.is_day === false ? 1 : 0)
    // Sunlight can be seen: the sun is at most 6° below the horizon and the weather lets it through.
    const sunlitOf = sun => !!sun && typeof sun.el === 'number' && sun.el > -6 && SUNNY.indexOf(fx.kind) >= 0

    /* ---------- Size and particles */
    function resize() {
      const w = canvas.clientWidth, h = canvas.clientHeight
      if (!w || !h) return false
      dpr = Math.min(STEPS[step].dpr, window.devicePixelRatio || 1)
      if (w !== W || h !== H || canvas.width !== Math.round(w * dpr)) {
        W = w; H = h
        canvas.width = edgeCanvas.width = Math.round(w * dpr); canvas.height = edgeCanvas.height = Math.round(h * dpr)
        seed()
      }
      return true
    }
    const amount = () => (level === 'subtle' ? 0.55 : 1) * STEPS[step].amount
    // How strongly the wind swells and subsides: as far as the measured gusts lie above the mean wind (fx.gust).
    function gustiness() {
      const calm = fx.storm ? 0.35 : 0.15
      if (typeof fx.gust !== 'number') return calm
      return Math.max(calm * 0.7, clamp((fx.gust - (fx.wind || 0)) / Math.max(fx.wind || 0, 0.08), 0, 0.9) * 0.5)
    }
    // Number of wind lines at this wind force: a few from 10 km/h, clearly more with the wind – more wind shows
    // in the amount, not only in the speed (reduced motion: half as many, standing).
    function wispCount(wind) {
      const breeze = clamp((wind - WIND_FX_MIN) / (0.75 - WIND_FX_MIN), 0, 1.4)
      const density = 0.22 + 1.2 * Math.pow(breeze, 1.15)
      return Math.max(3, Math.round(clamp(W * H / 8000, 10, 80) * density * amount() * (REDUCED ? 0.5 : 1)))
    }
    // Adapt particles, clouds and light bands to the current weather – only if weather, time of day, strength or
    // size have changed. What is already there stays in its place (fit): nothing jumps with new weather values.
    function seed() {
      if (!fx) return
      darkNow = darkOf(sunNow())
      const night = darkNow > 0.5
      const key = [fx.kind, fx.rain, fx.snow, fx.wind, fx.gust, fx.cloud, !!fx.hail, !!fx.frost, !!fx.storm, night, level, step, W, H,
        col.cloud, col.fog, col.light].join('|')
      if (key === seedKey) return
      seedKey = key
      const area = W * H, k = amount(), M = Math.max(W, H), soft = level === 'subtle' ? 0.7 : 1
      const count = (per, lo, hi, f) => REDUCED ? 0 : Math.round(clamp(area / per, lo, hi) * f * k)
      // precipitation: every third drop or flake lies "in front" (larger, faster, stronger).
      // drops and hailstones only get start and target in the frame (launch) – there frame and slant are known.
      fit(drops, count(1700, 24, 340, fx.rain || 0), i => ({ x: 0, y: 0, y0: 0, ly: 0, near: i % 3 === 0, l: rnd(0.7, 1.3), v: rnd(0.8, 1.2), fresh: true }))
      fit(flakes, count(4200, 16, 170, fx.snow || 0), i => ({ x: rnd(0, W), y: rnd(0, H), near: i % 3 === 0, r: rnd(0.7, 1.3), v: rnd(0.7, 1.2), ph: rnd(0, TAU), sw: rnd(6, 14) }))
      fit(pellets, fx.hail ? count(9000, 8, 60, 1) : 0, () => ({ x: 0, y: 0, y0: 0, ly: 0, r: rnd(1.2, 2), v: rnd(0.85, 1.2), fresh: true }))
      // leaves from 15 km/h: two or three in a fresh wind, a good dozen in a storm (not in snow)
      const leafy = !REDUCED && !(fx.snow > 0) && (fx.storm || fx.wind >= LEAF_MIN)
      fit(leaves, leafy ? Math.max(2, Math.round(clamp(2 + (fx.wind - LEAF_MIN) * 16, 2, 14) * clamp(area / 300000, 0.5, 1) * (OLD ? 0.6 : 1) * soft)) : 0,
        () => ({ x: rnd(0, W), y: rnd(0, H), s: rnd(3.5, 6), rot: rnd(0, TAU), sp: rnd(2, 5), ph: rnd(0, TAU), v: rnd(0.75, 1.15) }))
      stats.leaves = leaves.length
      // wind lines: as many as the strongest gust needs – how many of them move right now is decided by the frame
      const hadWisps = wisps.length > 0
      fit(wisps, fx.wind >= WIND_FX_MIN ? wispCount(fx.wind * (1 + gustiness())) : 0,
        () => ({ x: 0, y: 0, age: 0, life: 1, n: 0, sp: 1, out: 0, h: new Float32Array(TRAIL * 2), fresh: true }))
      stats.wisps = wisps.length
      if (!hadWisps || REDUCED) { warm = false; eddies = []; nextEddy = 0 }
      // Wolken: am Tag dunkle Schatten im Sonnenlicht, nachts helle Schleier
      const nCloud = fx.kind === 'partly' ? 3 : fx.kind === 'overcast' ? 4 : fx.kind === 'storm' ? 3 : 0
      const tint = night ? col.fog : col.cloud
      if (nCloud && cloudTint !== tint) { cloudImg = [blob(tint), blob(tint)]; cloudTint = tint }
      fit(clouds, nCloud, () => { const w = rnd(0.42, 0.7) * M; return { x: rnd(0, W), y: rnd(0, H), w, h: w * 0.58, v: rnd(0.9, 1.1) } })
      clouds.forEach((c, i) => { c.img = cloudImg[i % 2]; c.night = night })
      if (fx.kind === 'fog' && !fogImg.length) fogImg = [blob(col.fog), blob(col.fog)]
      fit(banks, fx.kind === 'fog' ? 6 : 0, i => { const w = rnd(0.5, 0.9) * M; return { x: rnd(0, W), y: rnd(0, H), w, h: w * 0.5, img: fogImg[i % 2], v: rnd(4, 10) } })
      // sun rays: many on a clear day, fewer the more clouds there are; each lives on its own (bornRay)
      const nRay = ({ clear: 9, partly: 8, overcast: 4, storm: 3 })[fx.kind] || 0
      fit(rays, Math.round(nRay * soft), () => bornRay({}, true))
      sunUp = sunlitOf(sunNow())
      // stars (clear to slightly cloudy night) and hoarfrost glitter: fixed places, only the brightness breathes
      const sky = ['clear', 'partly', 'storm'].indexOf(fx.kind) >= 0 ? Math.pow(1 - clamp(fx.cloud || 0, 0, 1), 1.4) : 0
      const dot = (lo, hi) => () => ({ u: Math.random(), v: Math.random(), r: rnd(lo, hi), a: rnd(0.3, 0.85), ph: rnd(0, TAU), sp: rnd(0.4, 1.6) })
      fit(stars, Math.round(clamp(area / 5200, 14, 110) * sky * soft), dot(0.5, 1.25))
      fit(glints, fx.frost ? Math.round(clamp(area / 9000, 10, 60) * soft) : 0, dot(1.6, 3))
      const now = performance.now()
      if (!nextFlash) nextFlash = now + rnd(2000, 6000)
      if (!nextMeteor) nextMeteor = now + rnd(20000, 90000)
    }
    // A sun ray begins its life (first = when the weather is laid out: somewhere in the middle of it, so that rays can
    // be seen at once). off = position across the light, w = width (share of the frame), a = brightness, len/start =
    // length and where along the light it begins, life/rest = how long it shines and pauses afterwards, ph/sp =
    // shimmer, tilt = small turn against the light. Below clouds rays live shorter, pause longer and start further in.
    function bornRay(r, first) {
      const cloudy = !fx || fx.kind !== 'clear'
      r.shape = Math.floor(Math.random() * BEAM_PROFILES.length)
      r.off = rnd(-1, 1); r.w = rnd(0.05, 0.15) * (r.shape === 2 ? 0.45 : 1); r.a = rnd(0.55, 1)
      r.len = rnd(0.6, 1.15); r.start = rnd(0, cloudy ? 0.45 : 0.2)
      r.life = cloudy ? rnd(4, 10) : rnd(10, 22); r.rest = cloudy ? rnd(1, 7) : rnd(0, 2)
      r.age = first ? rnd(0, r.life) : 0
      r.ph = rnd(0, TAU); r.ph2 = rnd(0, TAU); r.sp = rnd(0.35, 0.9); r.sp2 = rnd(0.1, 0.25); r.tilt = rnd(-0.035, 0.035)
      return r
    }
    // Brightness of a sun ray now (0…1): fades in and out softly over its life, shimmers in between (two slow waves);
    // 0 while it pauses.
    function rayGlow(r, t) {
      if (r.age >= r.life) return 0
      const p = r.age / r.life
      let e = Math.min(1, p / 0.3, (1 - p) / 0.35)
      e = e * e * (3 - 2 * e)
      return e * (0.72 + 0.28 * Math.sin(t * r.sp + r.ph) * Math.sin(t * r.sp2 * 2.3 + r.ph2))
    }
    /* Free places on the ground for what lies there (puddles, hoarfrost): in plan units, so they move with the plan;
       never in the house and never where another floor builds over the ground. o = {per: plan units² per spot,
       min, max: number, r: [smallest, largest radius], keep: share of the radius that must stay free, decks: false =
       also keep off balconies and terraces of this floor}. */
    function groundSpots(mask, o) {
      const v = mask.view, box = mask.box, spots = []
      if (!v || !(v.s > 0)) return spots
      // where they may lie: in the visible section and around the building (even if the camera moves later)
      let x0 = (box.x - v.x) / v.s, y0 = (box.y - v.y) / v.s, x1 = x0 + box.w / v.s, y1 = y0 + box.h / v.s
      const polys = (mask.polys || []).concat(o.decks ? [] : mask.decks || [], mask.covered || []).filter(p => p.length >= 3)
        .map(p => p.map(q => [(q[0] - v.x) / v.s, (q[1] - v.y) / v.s]))
      for (const p of polys) for (const q of p) { x0 = Math.min(x0, q[0] - 6); y0 = Math.min(y0, q[1] - 6); x1 = Math.max(x1, q[0] + 6); y1 = Math.max(y1, q[1] + 6) }
      // free = neither the centre nor the edge of the spot lies in the building (on a deck) or under another floor
      const free = (x, y, r) => {
        for (let i = 0; i <= 8; i++) {
          const a = i / 8 * TAU, px = i ? x + Math.cos(a) * r : x, py = i ? y + Math.sin(a) * r : y
          if (polys.some(poly => within(poly, px, py))) return false
        }
        return true
      }
      const want = Math.round(clamp((x1 - x0) * (y1 - y0) / o.per, o.min, o.max) * (OLD ? 0.7 : 1))
      for (let i = 0; i < want; i++) {
        for (let tries = 0; tries < 14; tries++) {
          const r = rnd(o.r[0], o.r[1]), x = rnd(x0, x1), y = rnd(y0, y1)
          if (!free(x, y, r * o.keep + 0.5)) continue
          spots.push({ x, y, r })
          break
        }
      }
      return spots
    }
    // Lay out puddles for this building: never in the house or at the wall – and never where a balcony or terrace of this
    // floor lies or another floor builds over the ground: the puddle belongs to the ground, it would otherwise be painted
    // across the balcony above it. How large they are is determined later by the rain.
    function seedPuddles(mask) {
      puddleShape = mask.shape || ''
      const images = [0, 1, 2, 3, 4].map(() => puddle(col.rain))
      puddles = groundSpots(mask, { per: 70, min: 3, max: 9, r: [0.9, 1.9], keep: 1.7, decks: false })
        .map((s, i) => Object.assign(s, { img: images[i % images.length] }))
    }
    // Lay out hoarfrost patches for this building: close together over the ground, also on balconies and terraces (frost
    // forms there as well), never in the house or under another floor.
    function seedFrost(mask) {
      frostShape = mask.shape || ''
      const images = [0, 1, 2].map(() => rime(col.snow))
      frost = groundSpots(mask, { per: 22, min: 6, max: 18, r: [1.3, 2.6], keep: 0.6, decks: true })
        .map((s, i) => Object.assign(s, { img: images[i % images.length] }))
    }

    /* The weather is attached to the plot: if the plan has moved in the frame or changed its scale (from -> to,
       each {x, y, s}), everything under way in the image moves along – drops with their target, ripples, flakes,
       leaves, wind lines with tail, clouds and fog (which grow along when zooming), stars and hoarfrost. What leaves
       the frame that way is brought back in by the image on the other side. */
    function carry(from, to, box) {
      const r = to.s / from.s
      const toX = x => (x - from.x) * r + to.x, toY = y => (y - from.y) * r + to.y
      const around = v => v - Math.floor(v)
      for (const list of [drops, pellets]) {
        for (const d of list) { if (!d.fresh) { d.x = toX(d.x); d.y = toY(d.y); d.y0 = toY(d.y0); d.ly = toY(d.ly) } }
      }
      for (const list of [ripples, flakes, leaves]) for (const p of list) { p.x = toX(p.x); p.y = toY(p.y) }
      for (const list of [clouds, banks]) for (const c of list) { c.x = toX(c.x); c.y = toY(c.y); c.w *= r; c.h *= r }
      for (const p of wisps) {
        if (p.fresh) continue
        p.x = toX(p.x); p.y = toY(p.y)
        for (let i = 0; i < p.n; i++) { p.h[2 * i] = toX(p.h[2 * i]); p.h[2 * i + 1] = toY(p.h[2 * i + 1]) }
      }
      for (const list of [stars, glints]) {
        for (const s of list) { s.u = around((toX(box.x + s.u * box.w) - box.x) / box.w); s.v = around((toY(box.y + s.v * box.h) - box.y) / box.h) }
      }
      const cx = box.x + box.w / 2, cy = box.y + box.h / 2
      rayPan = [rayPan[0] + toX(cx) - cx, rayPan[1] + toY(cy) - cy]
    }

    /* ---------- Richtungen */
    function windVec(sun) {
      // wind_dir = where the wind comes from (degrees from north); it blows the opposite way
      const a = ((sun ? sun.north || 0 : 0) + (fx.wind_dir || 0) + 180) * Math.PI / 180
      return [Math.sin(a), -Math.cos(a)]
    }

    /* ---------- Flow around the house: a coarse grid, computed once per section and wind direction (never per frame).
       Potential flow through the stream function ψ (Laplace equation): at the border the free wind, every part of the
       building an obstacle with a fixed ψ. The streamlines thus wrap around any building shape by themselves – faster
       at the flanks, calmer in front and behind. Only in the frame are added: two gentle shear waves (the lines
       wiggle slightly) and a few vortices that detach alternately from the flanks in the lee and drift off with the
       wind. The field is divergence-free, so lines pile up nowhere. Wind lines, snow and leaves
       all read the same field (flowAt). During a gesture the field of the painted plan keeps applying; it is then
       queried at the back-computed place (shift). */
    const gridCanvas = document.createElement('canvas'), gridCtx = gridCanvas.getContext('2d', { willReadFrequently: true })
    const flow = { mask: null, dir: null, at: 0, wx: 1, wy: 0, gw: 0, gh: 0, cell: 1, x0: 0, y0: 0, u: null, v: null, solid: null,
      lee: null, k1: 0, k2: 0, t1: 0, t2: 0 }
    let fvx = 0, fvy = 0                    // result of flowAt (free flow = length 1)
    let eddies = [], nextEddy = 0, eddySide = 1, warm = false, flowTimer = 0
    const shades = []
    for (let i = 0; i < SHADES; i++) shades.push([])

    // Compute the flow field of the wind around the building: a grid in which the wind avoids the house and forms
    // vortices in the lee.
    function buildFlow(mask, wv) {
      const box = mask.box, wx = wv[0], wy = wv[1], size = Math.max(box.w, box.h)
      // the grid reaches beyond the visible section: the wind can go around the house on the outside too
      const pad = 0.3 * size, dw = box.w + 2 * pad, dh = box.h + 2 * pad
      const cell = Math.max(10, Math.sqrt(dw * dh / FLOW_CELLS))
      const gw = Math.max(4, Math.ceil(dw / cell)), gh = Math.max(4, Math.ceil(dh / cell)), n = gw * gh
      const x0 = box.x - pad, y0 = box.y - pad
      const polys = (mask.polys || []).filter(p => p.length >= 3)
      Object.assign(flow, { wx, wy, gw, gh, cell, x0, y0, u: null, v: null, solid: null, lee: null, k1: TAU / (0.55 * size), k2: TAU / (0.38 * size) })
      stats.builds++; stats.cells = n; stats.solid = 0
      if (!polys.length) return             // no building: the free wind everywhere
      // 1) draw the building with its free margin into the grid and read it as solid cells (the same shape as the punch mask)
      gridCanvas.width = gw; gridCanvas.height = gh
      const g = gridCtx
      g.setTransform(1 / cell, 0, 0, 1 / cell, -x0 / cell, -y0 / cell)
      g.fillStyle = '#000'; g.strokeStyle = '#000'; g.lineJoin = 'round'; g.lineCap = 'round'
      g.lineWidth = (mask.wall || 6) + 2 * MARGIN + FEATHER
      g.beginPath()
      for (const p of polys) { g.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) g.lineTo(p[i][0], p[i][1]); g.closePath() }
      g.fill()
      for (const p of mask.walls || []) { if (p.length < 2) continue; g.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) g.lineTo(p[i][0], p[i][1]) }
      g.stroke()
      const pixels = g.getImageData(0, 0, gw, gh).data, solid = new Uint8Array(n)
      let solidCount = 0
      for (let i = 0; i < n; i++) if (pixels[i * 4 + 3] > 96) { solid[i] = 1; solidCount++ }
      stats.solid = solidCount
      // 2) ψ of the free wind (u = ∂ψ/∂y, v = −∂ψ/∂x); the border of the grid keeps it
      const psi = new Float32Array(n), fixed = new Uint8Array(n)
      for (let j = 0, i = 0; j < gh; j++) {
        for (let k = 0; k < gw; k++, i++) {
          psi[i] = wx * j - wy * k
          if (j === 0 || k === 0 || j === gh - 1 || k === gw - 1) fixed[i] = 1
        }
      }
      // 3) Every connected part of the building gets a fixed ψ: the mean of the free wind over its
      //    area – that way the flow splits left and right of it (flows around, without swirl)
      const seen = new Uint8Array(n), stack = []
      for (let start = 0; start < n; start++) {
        if (!solid[start] || seen[start]) continue
        const part = []
        let sum = 0
        stack.push(start); seen[start] = 1
        while (stack.length) {
          const i = stack.pop(), k = i % gw
          part.push(i); sum += psi[i]
          if (k > 0 && solid[i - 1] && !seen[i - 1]) { seen[i - 1] = 1; stack.push(i - 1) }
          if (k < gw - 1 && solid[i + 1] && !seen[i + 1]) { seen[i + 1] = 1; stack.push(i + 1) }
          if (i >= gw && solid[i - gw] && !seen[i - gw]) { seen[i - gw] = 1; stack.push(i - gw) }
          if (i < n - gw && solid[i + gw] && !seen[i + gw]) { seen[i + gw] = 1; stack.push(i + gw) }
        }
        const mean = sum / part.length
        for (const i of part) { psi[i] = mean; fixed[i] = 1 }
      }
      // 4) solve the Laplace equation (over-relaxation; the free wind already is a good starting solution)
      const rounds = Math.min(160, 2 * Math.max(gw, gh))
      for (let round = 0; round < rounds; round++) {
        for (let j = 1; j < gh - 1; j++) {
          for (let i = j * gw + 1, end = i + gw - 2; i < end; i++) {
            if (!fixed[i]) psi[i] += 1.75 * (0.25 * (psi[i - 1] + psi[i + 1] + psi[i - gw] + psi[i + gw]) - psi[i])
          }
        }
      }
      // 5) velocity from ψ; 0 in the building, capped at sharp corners
      const u = new Float32Array(n), v = new Float32Array(n)
      for (let j = 0, i = 0; j < gh; j++) {
        for (let k = 0; k < gw; k++, i++) {
          if (solid[i]) continue
          if (j === 0 || k === 0 || j === gh - 1 || k === gw - 1) { u[i] = wx; v[i] = wy; continue }
          let a = (psi[i + gw] - psi[i - gw]) / 2, b = -(psi[i + 1] - psi[i - 1]) / 2
          const speed = Math.sqrt(a * a + b * b)
          if (speed > 2.2) { a *= 2.2 / speed; b *= 2.2 / speed }
          u[i] = a; v[i] = b
        }
      }
      // 6) lee: how far the building reaches in wind direction (s) and how wide it is across (c0…c1)
      let s1 = -Infinity, c0 = Infinity, c1 = -Infinity
      for (const p of polys) {
        for (const q of p) {
          const s = q[0] * wx + q[1] * wy, c = -q[0] * wy + q[1] * wx
          if (s > s1) s1 = s
          if (c < c0) c0 = c
          if (c > c1) c1 = c
        }
      }
      Object.assign(flow, { u, v, solid, lee: { s: s1, c0, c1 } })
    }
    // Provide the flow field: recompute if outline or wind direction have changed – during a
    // camera movement only in the calm image.
    function ensureFlow(mask, wv) {
      const dir = Math.round(Math.atan2(wv[1], wv[0]) * 90)
      if (flow.mask === mask && flow.dir === dir) return
      const nowMs = performance.now()
      if (flow.mask && nowMs - flow.at < 300) {
        // camera in motion: do not recompute in every frame – catch up later in the calm image (without animation)
        if (!running() && !flowTimer) flowTimer = setTimeout(() => { flowTimer = 0; if (alive && !raf) draw(0, 0) }, 320)
        return
      }
      buildFlow(mask, wv)
      flow.mask = mask; flow.dir = dir; flow.at = nowMs
      eddies = []
      if (REDUCED) warm = false             // stehende Stromlinien neu legen
    }
    /* Flow at a point -> fvx, fvy (free wind = length 1). Returns true = in the building (then the free wind,
       so that snow and leaves pass below the punch mask as before and do not get stuck). */
    function flowAt(x, y) {
      const f = flow
      if (shift) { x = (x - shift.x) / shift.k; y = (y - shift.y) / shift.k }
      let ux = f.wx, uy = f.wy
      if (f.u) {
        let gx = (x - f.x0) / f.cell - 0.5, gy = (y - f.y0) / f.cell - 0.5
        gx = gx < 0 ? 0 : gx > f.gw - 1.001 ? f.gw - 1.001 : gx
        gy = gy < 0 ? 0 : gy > f.gh - 1.001 ? f.gh - 1.001 : gy
        const k = gx | 0, j = gy | 0, i = j * f.gw + k, ax = gx - k, ay = gy - j
        if (f.solid[i + (ax < 0.5 ? 0 : 1) + (ay < 0.5 ? 0 : f.gw)]) { fvx = ux; fvy = uy; return true }
        const a = (1 - ax) * (1 - ay), b = ax * (1 - ay), c = (1 - ax) * ay, d = ax * ay
        ux = f.u[i] * a + f.u[i + 1] * b + f.u[i + f.gw] * c + f.u[i + f.gw + 1] * d
        uy = f.v[i] * a + f.v[i + 1] * b + f.v[i + f.gw] * c + f.v[i + f.gw + 1] * d
      }
      // wiggling: across the wind a wave along the wind direction, along it a wave across (both divergence-free);
      // weaker close to the building (slow flow), so that nothing is pressed into the wall
      const calm = Math.min(1, Math.sqrt(ux * ux + uy * uy))
      const along = x * f.wx + y * f.wy, across = -x * f.wy + y * f.wx
      const sway = 0.16 * calm * Math.sin(along * f.k1 - f.t1), surge = 0.14 * calm * Math.sin(across * f.k2 + f.t2)
      ux += f.wx * surge - f.wy * sway; uy += f.wy * surge + f.wx * sway
      // vortex: rotation around the core, strongest at distance R, decaying quickly outwards
      for (const e of eddies) {
        const dx = x - e.x, dy = y - e.y, q = (dx * dx + dy * dy) / (e.R * e.R)
        if (q > 9) continue
        const turn = e.g * Math.exp(0.5 * (1 - q)) / e.R
        ux -= turn * dy; uy += turn * dx
      }
      fvx = ux; fvy = uy
      return false
    }
    // Does the point lie in the building including its free margin? Coarse query in the flow grid (without a grid: no).
    function solidAt(x, y) {
      const f = flow
      if (!f.solid) return false
      if (shift) { x = (x - shift.x) / shift.k; y = (y - shift.y) / shift.k }
      const k = Math.floor((x - f.x0) / f.cell), j = Math.floor((y - f.y0) / f.cell)
      return k >= 0 && j >= 0 && k < f.gw && j < f.gh && f.solid[j * f.gw + k] === 1
    }

    /* ---------- Bild */
    function draw(now, dt) {
      if (!fx || !resize()) return
      const painted = opts.getMask ? opts.getMask() : null
      if (!painted) return
      // During a gesture the painted plan stands moved in the frame: the house (punch mask) moves along. The
      // flow field still belongs to the painted plan (flowAt computes back).
      shift = opts.getShift ? opts.getShift() : null
      let mask = painted
      if (shift) {
        if (movedOf.mask !== painted || movedOf.k !== shift.k || movedOf.x !== shift.x || movedOf.y !== shift.y) {
          movedOf = { mask: painted, k: shift.k, x: shift.x, y: shift.y, val: moved(painted, shift) }
        }
        mask = movedOf.val
      }
      // if the plan lies differently in the frame than in the last image (gesture, zoom button, new size), the weather moves along
      const view = mask.view
      if (view && view.s > 0) {
        const moves = lastView && (Math.abs(view.x - lastView.x) > 0.01 || Math.abs(view.y - lastView.y) > 0.01 || Math.abs(view.s / lastView.s - 1) > 1e-5)
        if (moves) carry(lastView, view, mask.box)
        if (moves || !lastView) lastView = { x: view.x, y: view.y, s: view.s }
      }
      // In a calm single image too the clock continues where the last image stood (otherwise whatever follows
      // from the time would jump: gusts, twinkling, sun rays)
      const stamp = now || lastStamp
      lastStamp = stamp
      const box = mask.box, k = level === 'subtle' ? 0.6 : 1, t = stamp / 1000
      const sun = sunNow(), dark = darkNow = darkOf(sun)
      const pk = k * (1 - 0.3 * dark)   // precipitation, wind, fog: dampened at night
      const bx1 = box.x + box.w, by1 = box.y + box.h, mx = box.x + box.w / 2, my = box.y + box.h / 2
      for (const c of [mainCtx, edgeCtx]) {
        c.setTransform(dpr, 0, 0, dpr, 0, 0)
        c.clearRect(0, 0, W, H)
        c.save()
        c.beginPath(); c.rect(box.x, box.y, box.w, box.h); c.clip()
      }
      ctx = mainCtx
      // Punch out the building: fill rooms, trace walls – inside everything stays untouched. With a margin a narrow
      // strip around the house stays free (MARGIN), with a soft transition (a second, wider stroke takes half
      // away) – that way no drop and no light sticks to the wall.
      const wall = mask.wall || 6
      // Punch the building out of the image (on request with a free margin all around).
      const punch = (c, margin) => {
        c.save()
        c.globalCompositeOperation = 'destination-out'; c.fillStyle = '#000'; c.strokeStyle = '#000'
        c.lineJoin = 'round'; c.lineCap = 'round'
        c.beginPath()
        for (const p of mask.polys || []) { if (p.length < 3) continue; c.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) c.lineTo(p[i][0], p[i][1]); c.closePath() }
        for (const p of mask.walls || []) { if (p.length < 2) continue; c.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) c.lineTo(p[i][0], p[i][1]) }
        if (margin) { c.globalAlpha = 0.55; c.lineWidth = wall + 2 * (MARGIN + FEATHER); c.stroke() }
        c.globalAlpha = 1; c.lineWidth = wall + (margin ? 2 * MARGIN : 0); c.stroke()
        c.beginPath()
        for (const p of mask.polys || []) { if (p.length < 3) continue; c.moveTo(p[0][0], p[0][1]); for (let i = 1; i < p.length; i++) c.lineTo(p[i][0], p[i][1]); c.closePath() }
        c.fill()
        c.restore()
      }
      // Margin image: draw -> punch out with a margin -> lay onto the main image -> clear. `edgeUsed` saves the copying
      // if nothing was drawn.
      let edgeUsed = false
      const onEdge = () => { ctx = edgeCtx; edgeUsed = true }
      // Punch out the intermediate layer of the effects near the margin and transfer it to the main image.
      const flushEdge = () => {
        ctx = mainCtx
        if (!edgeUsed) return
        edgeUsed = false
        punch(edgeCtx, true)
        mainCtx.globalAlpha = 1
        mainCtx.drawImage(edgeCanvas, 0, 0, W, H)
        edgeCtx.clearRect(0, 0, W, H)
      }
      // Gusts: the wind swells and subsides – as strongly as the measured gusts lie above the mean wind (fx.gust).
      // This one value drives wind lines, clouds, fog, rain slant, snow and leaves together; pace is the
      // speed of the free flow in pixels per second that lines, leaves and clouds follow.
      const wv = windVec(sun)
      const wind = clamp((fx.wind || 0) * (1 + gustiness() * Math.sin(t * 0.7) * Math.sin(t * 0.23 + 1)), 0, 1.3)
      const pace = (30 + 190 * wind) * clamp(Math.max(W, H) / 900, 0.6, 1.2)
      if (wisps.length || flakes.length || leaves.length) {
        ensureFlow(painted, wv)
        flow.t1 = t * 0.5; flow.t2 = t * 0.33
      }
      const wash = (color, a) => { ctx.globalAlpha = a; ctx.fillStyle = color; ctx.fillRect(box.x, box.y, box.w, box.h) }
      // centre of the building (lightning strikes next to it)
      let cx = mx, rad = Math.max(box.w, box.h) / 2
      const pts = [].concat.apply([], mask.polys || [])
      if (pts.length) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
        for (const p of pts) { x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]) }
        cx = (x0 + x1) / 2; rad = Math.max(20, Math.max(x1 - x0, y1 - y0) / 2)
      }

      // 0) Daylight: by day the surroundings are brighter – evenly over the whole frame up to the wall, because sky light
      //    comes from everywhere (no corner stays dark). Clouds dim it, precipitation and fog more; under sun it is a
      //    touch warm. day = 0 at −6° sun elevation, 1 from 10°.
      const sunlit = sunUp = sunlitOf(sun)
      const day = sun && typeof sun.el === 'number' ? clamp((sun.el + 6) / 16, 0, 1) : fx.is_day === false ? 0 : 1
      const up = sunlit ? clamp((sun.el + 6) / 12, 0, 1) : 0, low = sunlit ? clamp(1 - sun.el / 25, 0, 1) : 0, heat = fx.heat || 0
      // warm tint of the sunlight: pale at noon, golden with a low sun (in steps, so that the shaft images are rarely rebuilt)
      const sunColor = mix(col.light, col.sun, Math.round((0.2 + 0.45 * low) * 10) / 10)
      if (day > 0) {
        const dull = fx.rain > 0 || fx.snow > 0 || fx.kind === 'fog' || fx.thunder
        wash(sunlit ? mix(col.light, col.sun, 0.1) : col.light, (dull ? 0.035 : 0.075 - 0.03 * clamp(fx.cloud || 0, 0, 1)) * day * k)
      }
      // 1) Ground and air: snow cover brightens the surroundings, hoarfrost lies in patches of ice needles
      const raining = fx.rain > 0, wetGoal = raining ? 0.45 + 0.85 * fx.rain : 0
      if (raining && puddleShape !== (mask.shape || '')) { seedPuddles(mask); wet = wetGoal }
      if (fx.frost && frostShape !== (mask.shape || '')) seedFrost(mask)
      // puddles grow and shrink with the rain – leisurely; at once in the calm single image
      wet = dt ? wet + (wetGoal - wet) * Math.min(1, dt * 0.6) : running() ? wet : wetGoal
      if (fx.snow > 0 || fx.frost || raining || wet > 0.03) onEdge()
      if (fx.snow > 0) wash(col.snow, (0.03 + 0.04 * fx.snow) * k * (1 - 0.5 * dark))
      else if (fx.frost) wash(col.snow, 0.03 * k)
      // hoarfrost: patches of fine ice needles, fixed on the ground; at night they stay visible, only dimmer
      let frostShown = 0
      if (fx.frost && frost.length && mask.view) {
        const v = mask.view
        ctx.globalAlpha = 0.55 * k * (1 - 0.35 * dark)
        for (const p of frost) {
          const half = p.r * v.s / FROST_UNIT / 2, x = v.x + p.x * v.s, y = v.y + p.y * v.s
          if (x + half < box.x || x - half > bx1 || y + half < box.y || y - half > by1) continue
          ctx.drawImage(p.img, x - half, y - half, half * 2, half * 2)
          frostShown++
        }
      }
      stats.frost = frostShown
      // rain: a damp veil over everything – that way it can be recognised in the calm image (without drops) too
      if (raining) wash(col.rain, (0.02 + 0.035 * fx.rain) * pk)
      // puddles: the stronger the rain, the larger. They lie fixed on the ground and keep a distance to the wall.
      let puddlesShown = 0
      if (puddles.length && wet > 0.03 && mask.view) {
        const v = mask.view
        ctx.globalAlpha = 0.2 * pk * clamp(wet * 2.5, 0, 1)
        for (const p of puddles) {
          const half = p.r * v.s * wet / PUDDLE_UNIT / 2, x = v.x + p.x * v.s, y = v.y + p.y * v.s
          if (x + half < box.x || x - half > bx1 || y + half < box.y || y - half > by1) continue
          ctx.drawImage(p.img, x - half, y - half, half * 2, half * 2)
          puddlesShown++
        }
      }
      stats.puddles = puddlesShown
      // 2) Sunlight: the rays arrive practically parallel – so a straight gradient across the image, brighter on the
      //    sunny side, in a transparent warm white. It never runs out to nothing: the far side keeps a third of it
      //    (together with the daylight above no side looks like night). Fades in at dusk, a low sun reaches further;
      //    not in fog, rain, snow, thunderstorm. Heat: a little stronger, plus a bright veil.
      // half the extent of the frame in the direction of the sun
      const reach = sunlit ? 0.5 * (Math.abs(sun.dir[0]) * box.w + Math.abs(sun.dir[1]) * box.h) : 0
      if (sunlit) {
        onEdge()
        const cloud = fx.cloud || 0
        const strength = (fx.kind === 'clear' ? 0.09 : fx.kind === 'overcast' ? 0.035 : 0.065) * (1 - 0.4 * cloud) * up * (1 + 0.35 * heat) * k
        const far = 0.35 + 0.65 * low
        const g = ctx.createLinearGradient(mx + sun.dir[0] * reach, my + sun.dir[1] * reach, mx - sun.dir[0] * reach * far, my - sun.dir[1] * reach * far)
        g.addColorStop(0, rgba(sunColor, 1)); g.addColorStop(0.5, rgba(sunColor, 0.6)); g.addColorStop(1, rgba(sunColor, 0.33))
        ctx.globalAlpha = clamp(strength, 0, 0.16); ctx.fillStyle = g; ctx.fillRect(box.x, box.y, box.w, box.h)
        if (heat > 0) wash(sunColor, 0.03 * heat * up * k)
      }
      flushEdge()
      // 3) Clouds: a few large, soft blobs, move in the wind direction – consistent with the wind lines, a little slower
      //    (CLOUD_PACE) and all about equally fast. They reach up to the wall (without a margin).
      for (const c of clouds) {
        if (dt) { c.x += wv[0] * pace * CLOUD_PACE * c.v * dt; c.y += wv[1] * pace * CLOUD_PACE * c.v * dt }
        if (c.x < box.x - c.w / 2) c.x += box.w + c.w; if (c.x > bx1 + c.w / 2) c.x -= box.w + c.w
        if (c.y < box.y - c.h / 2) c.y += box.h + c.h; if (c.y > by1 + c.h / 2) c.y -= box.h + c.h
        ctx.globalAlpha = c.night ? (fx.kind === 'partly' ? 0.09 : 0.12) * k * dark : (fx.kind === 'partly' ? 0.4 : 0.28) * k * (1 - dark)
        ctx.drawImage(c.img, c.x - c.w / 2, c.y - c.h / 2, c.w, c.h)
      }
      // 4) Fog: an even veil, above it sluggish banks that move with the wind – up to the wall (without a margin)
      if (banks.length) {
        wash(col.fog, 0.05 * pk)
        for (const b of banks) {
          if (dt) { b.x += wv[0] * b.v * (0.5 + wind) * dt; b.y += wv[1] * b.v * (0.5 + wind) * dt }
          if (b.x < box.x - b.w / 2) b.x += box.w + b.w; if (b.x > bx1 + b.w / 2) b.x -= box.w + b.w
          if (b.y < box.y - b.h / 2) b.y += box.h + b.h; if (b.y > by1 + b.h / 2) b.y -= box.h + b.h
          ctx.globalAlpha = 0.16 * pk
          ctx.drawImage(b.img, b.x - b.w / 2, b.y - b.h / 2, b.w, b.h)
        }
      }
      // 5) Sun rays: shafts of light in the direction of the light, above the cloud shadows, in the warm tint of the
      //    sunlight. Each one lives on its own (bornRay, rayGlow): it fades in, shimmers, breathes in width, turns a
      //    little and fades out; then a new one appears elsewhere, in another shape. The house throws a shadow shaft
      //    into them (shadeRays) – they are drawn alone on the margin image and laid onto the main image at once.
      let lit = 0, blocked = 0
      if (sunlit && rays.length) {
        if (beamTint !== sunColor) { beamImg = BEAM_PROFILES.map((profile, shape) => beam(sunColor, shape)); beamTint = sunColor }
        onEdge()
        const cloudy = fx.kind !== 'clear', ax = -sun.dir[0], ay = -sun.dir[1], angle = Math.atan2(ay, ax)
        const across = 0.5 * (Math.abs(ay) * box.w + Math.abs(ax) * box.h), span = Math.max(box.w, box.h)
        const power = (cloudy ? (fx.kind === 'partly' ? 0.15 : 0.13) : 0.11 * (1 + 0.35 * heat)) * up * k
        // The rays wander with the wind like the clouds whose gaps they fall through: what shows from above is the
        // movement across the light, at the pace of the clouds (on a clear day only with the haze, RAY_HAZE). If the
        // wind blows almost along the light, they still wander slowly to one side (RAY_SIDE_MIN) – standing rays under
        // moving clouds would look wrong.
        if (dt) {
          const side = wv[1] * ax - wv[0] * ay
          rayDrift += (Math.abs(side) < RAY_SIDE_MIN ? (side < 0 ? -RAY_SIDE_MIN : RAY_SIDE_MIN) : side) * pace * CLOUD_PACE * (cloudy ? 1 : RAY_HAZE) * dt
        }
        // moved with the plan (carry) and with the wind: offset across the light; one that leaves comes back in on the
        // opposite side
        const pan = (rayPan[1] * ax - rayPan[0] * ay + rayDrift) / (across || 1)
        for (const r of rays) {
          if (dt && (r.age += dt) >= r.life + r.rest) bornRay(r, false)
          const glow = rayGlow(r, t)
          if (glow < 0.01) continue
          lit += glow
          let off = r.off + pan + 1
          off = off - 2 * Math.floor(off / 2) - 1
          const wide = r.w * span * (0.85 + 0.15 * Math.sin(t * r.sp2 + r.ph)), long = 2 * reach * r.len * (0.75 + 0.5 * low)
          ctx.save()
          ctx.translate(mx - ax * reach - ay * off * across, my - ay * reach + ax * off * across)
          ctx.rotate(angle + r.tilt + 0.015 * Math.sin(t * r.sp2 * 0.7 + r.ph2))
          ctx.globalAlpha = clamp(power * r.a * glow, 0, 0.22)
          ctx.drawImage(beamImg[r.shape], r.start * long, -wide / 2, long, wide)
          ctx.restore()
        }
        if (lit) blocked = shadeRays(mask, ax, ay)
        flushEdge()
      }
      stats.rays = lit
      stats.blocked = blocked
      // 6) Stars: twinkle calmly, fade with dusk; rarely a shooting star
      if (stars.length && dark > 0.15) {
        onEdge()
        ctx.fillStyle = col.star
        for (const s of stars) {
          ctx.globalAlpha = s.a * (0.6 + 0.4 * Math.sin(s.ph + t * s.sp)) * dark * k
          ctx.beginPath(); ctx.arc(box.x + s.u * box.w, box.y + s.v * box.h, s.r, 0, TAU); ctx.fill()
        }
        if (!REDUCED && now && dark > 0.8 && stars.length > 20) {
          if (!meteor && now >= nextMeteor) {
            const a = rnd(0.35, 0.9) * (Math.random() < 0.5 ? 1 : -1) + Math.PI / 2
            meteor = { t0: now, x: rnd(box.x + box.w * 0.15, bx1 - box.w * 0.15), y: rnd(box.y, box.y + box.h * 0.4), dx: Math.cos(a), dy: Math.abs(Math.sin(a)), len: rnd(70, 130) }
            nextMeteor = now + rnd(45000, 150000)
          }
          if (meteor) {
            const p = (now - meteor.t0) / 650
            if (p >= 1) meteor = null
            else {
              const hx = meteor.x + meteor.dx * meteor.len * p, hy = meteor.y + meteor.dy * meteor.len * p
              const tx = hx - meteor.dx * 46, ty = hy - meteor.dy * 46, g = ctx.createLinearGradient(hx, hy, tx, ty)
              g.addColorStop(0, rgba(col.star, 1)); g.addColorStop(1, rgba(col.star, 0))
              ctx.globalAlpha = Math.sin(Math.PI * p) * 0.6 * k; ctx.strokeStyle = g; ctx.lineWidth = 1.2; ctx.lineCap = 'round'
              ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(tx, ty); ctx.stroke()
            }
          }
        }
      }
      // 7) Wind: lines with a fading tail move in the flow field around the house. Amount, length and speed show the
      //    wind force; in the lee small vortices detach alternately left and right with more wind.
      if (wisps.length) {
        onEdge()
        const swirl = clamp(((fx.wind || 0) - EDDY_MIN) / 0.5, 0, 1), lee = flow.lee
        if (dt) {
          if (swirl > 0 && lee && lee.c1 - lee.c0 > 30) {
            const width = lee.c1 - lee.c0
            nextEddy -= dt
            if (nextEddy <= 0 && eddies.length < 4) {
              const R = clamp(0.2 * width, 16, 64), across = eddySide > 0 ? lee.c1 - 0.12 * width : lee.c0 + 0.12 * width, along = lee.s + 0.6 * R
              // sense of rotation like the shear layer at this flank: fast wind outside, the calm lee inside
              eddies.push({ x: along * wv[0] - across * wv[1], y: along * wv[1] + across * wv[0], R, spin: -eddySide, age: 0,
                life: clamp(3.2 * width / (0.55 * pace), 2.5, 7), g: 0 })
              eddySide = -eddySide
              nextEddy = clamp(2.5 * width / pace, 1.2, 5)
            }
          }
          eddies = eddies.filter(e => (e.age += dt) < e.life)
          for (const e of eddies) {
            e.x += wv[0] * 0.55 * pace * dt; e.y += wv[1] * 0.55 * pace * dt; e.R += 3 * dt
            e.g = e.spin * (0.4 + 0.45 * swirl) * Math.sin(Math.PI * e.age / e.life)   // fades in and out softly
          }
        }
        // this many lines move right now: more in a gust, fewer in between (the others rest until it is their turn)
        const active = Math.min(wisps.length, wispCount(wind))
        // Restart a wind line at a random free place – or let it rest if fewer are moving right now.
        const renew = (p, index) => {
          if (index >= active) { p.n = 0; p.fresh = true; return }
          for (let i = 0; i < 6; i++) { p.x = rnd(box.x, bx1); p.y = rnd(box.y, by1); if (!flowAt(p.x, p.y)) break }
          p.age = 0; p.life = rnd(1.8, 3.6); p.n = 1; p.h[0] = p.x; p.h[1] = p.y; p.fresh = false; p.out = 0
        }
        // Move a particle of the wind one step with the flow and continue its trail. If it runs
        // towards a wall, it fades out before (out counts down) and does not end abruptly; used-up ones restart.
        const advance = (p, pace_step, index) => {
          if (p.fresh || p.age >= p.life || p.x < box.x - 24 || p.x > bx1 + 24 || p.y < box.y - 24 || p.y > by1 + 24) return renew(p, index)
          const inside = flowAt(p.x, p.y)             // after this fvx, fvy hold for this point
          if (inside && !(p.out > 0)) p.out = FADE_OUT
          if (!inside) {
            p.x += fvx * pace * pace_step; p.y += fvy * pace * pace_step
            p.sp = Math.sqrt(fvx * fvx + fvy * fvy)
            // look-ahead: where the line would be shortly – if the building lies there, it starts fading out now
            if (!(p.out > 0) && p.sp > 0.05) {
              const ahead = clamp(pace * p.sp * FADE_OUT, 12, 56) / p.sp
              if (solidAt(p.x + fvx * ahead, p.y + fvy * ahead)) p.out = FADE_OUT
            }
          }
          if (p.out > 0 && (p.out -= pace_step) <= 0) return renew(p, index)
          p.age += pace_step
          p.h.copyWithin(2, 0, TRAIL * 2 - 2); p.h[0] = p.x; p.h[1] = p.y
          if (p.n < TRAIL) p.n++
        }
        if (!warm) {
          // lead-in: the tails are there from the first frame. Reduced motion: longer, standing streamlines.
          warm = true
          const stride = REDUCED ? 1 / 12 : 1 / 30
          for (const p of wisps) p.fresh = true
          for (let i = 0; i <= TRAIL; i++) for (let j = 0; j < wisps.length; j++) advance(wisps[j], stride, j)
          for (const p of wisps) if (p.n) p.age = REDUCED ? p.life / 2 : rnd(0.25, 0.75) * p.life
        } else if (dt) {
          for (let j = 0; j < wisps.length; j++) advance(wisps[j], dt, j)
        }
        // tail: brightest in front, fading towards the back; every line fades in and out, more delicate in the lee.
        // One path is drawn per brightness level (a few stroke calls for all lines).
        for (const s of shades) s.length = 0
        for (const p of wisps) {
          if (p.n < 2) continue
          const fade = p.out > 0 ? p.out / FADE_OUT : 1
          const glow = Math.min(1, p.age / 0.5, (p.life - p.age) / 0.7) * clamp(p.sp * 1.6, 0.35, 1) * fade, h = p.h
          for (let i = 0; i < p.n - 1; i++) {
            const shade = Math.ceil(glow * (1 - i / (TRAIL - 1)) * SHADES) - 1
            if (shade < 0) break
            shades[shade].push(h[2 * i], h[2 * i + 1], h[2 * i + 2], h[2 * i + 3])
          }
        }
        // interplay: drifting snow in snowfall (snow colour), rain and fog dampen the lines
        const strength = clamp(0.5 + (fx.wind || 0) * 1.1, 0.5, 1) * (1 - 0.4 * (fx.rain || 0)) * (banks.length ? 0.7 : 1)
        ctx.strokeStyle = fx.snow > 0 ? col.snow : col.rain; ctx.lineWidth = 1.1; ctx.lineCap = 'butt'
        for (let b = 0; b < SHADES; b++) {
          const s = shades[b]
          if (!s.length) continue
          ctx.globalAlpha = 0.28 * (b + 1) / SHADES * strength * pk
          ctx.beginPath()
          for (let i = 0; i < s.length; i += 4) { ctx.moveTo(s[i], s[i + 1]); ctx.lineTo(s[i + 2], s[i + 3]) }
          ctx.stroke()
        }
      }
      // 8) Rain: fine, slanted strokes in two depths; slant by wind, density and speed by rain strength.
      //    Drizzle falls shorter and slower. Every drop has its target on the ground somewhere in the image: there it ends,
      //    and exactly there its ripple spreads – no ripple without a drop, no drop without a ripple.
      const slant = clamp(wv[0] * (0.25 + wind * 0.9), -0.9, 0.9)
      const land = (x, y, R, life) => { if (ripples.length < MAX_RIPPLES) ripples.push({ x, y, t: 0, life, R }) }
      // Restart a drop or hailstone: the target is a point on the ground, it starts a bit above it – offset
      // the way the wind slants the fall. spread = somewhere along the way (the first time, so that not all
      // land at once).
      const launch = (d, spread) => {
        const height = Math.max(90, rnd(0.3, 0.75) * box.h)
        d.ly = rnd(box.y, by1); d.y0 = d.ly - height
        d.y = spread ? rnd(d.y0, d.ly) : d.y0
        d.x = rnd(box.x, bx1) - slant * (d.ly - d.y)
        d.fresh = false
      }
      if (drops.length || pellets.length || ripples.length || flakes.length || leaves.length || glints.length || (fx.thunder && !REDUCED && now)) onEdge()
      if (drops.length) {
        const rain = fx.rain || 0, fine = rain < 0.3 ? 0.65 : 1, speed = (360 + 340 * rain) * (rain < 0.3 ? 0.75 : 1)
        ctx.strokeStyle = col.rain; ctx.lineCap = 'round'
        for (let pass = 0; pass < 2; pass++) {
          const near = pass === 1, sp = speed * (near ? 1 : 0.7), len = (near ? 16 : 9) * fine
          ctx.globalAlpha = (near ? 0.42 : 0.24) * pk; ctx.lineWidth = near ? 1.2 : 0.9
          ctx.beginPath()
          for (const d of drops) {
            if (d.near !== near) continue
            if (d.fresh) launch(d, true)
            if (dt) { const far = sp * d.v * dt; d.y += far; d.x += slant * far }
            if (d.y >= d.ly) {
              if (near) land(d.x, d.ly, rnd(6, 11), rnd(0.5, 0.85)); else land(d.x, d.ly, rnd(3.5, 6), rnd(0.3, 0.5))
              launch(d, false)
            }
            // the stroke never reaches back beyond the start point: a new drop grows out of it
            const top = Math.max(d.y0, d.y - len * d.l)
            ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - slant * (d.y - top), top)
          }
          ctx.stroke()
        }
      }
      // 9) Hail: fast, bright stones; where one lands, a short, tight ripple
      if (pellets.length) {
        ctx.globalAlpha = 0.55 * pk; ctx.fillStyle = col.snow; ctx.beginPath()
        for (const p of pellets) {
          if (p.fresh) launch(p, true)
          if (dt) { const far = 640 * p.v * dt; p.y += far; p.x += slant * far }
          if (p.y >= p.ly) { land(p.x, p.ly, rnd(3, 5), rnd(0.25, 0.4)); launch(p, false) }
          ctx.moveTo(p.x + p.r, p.y); ctx.arc(p.x, p.y, p.r, 0, TAU)
        }
        ctx.fill()
      }
      // ripples: spread and fade; one path per brightness level
      if (ripples.length) {
        let kept = 0
        for (const list of rings) list.length = 0
        for (let i = 0; i < ripples.length; i++) {
          const s = ripples[i]
          s.t += dt || 0
          if (s.t >= s.life) continue
          ripples[kept++] = s
          const p = s.t / s.life
          rings[Math.min(RINGS - 1, Math.floor(p * RINGS))].push(s.x, s.y, 1.5 + p * s.R)
        }
        ripples.length = kept
        ctx.strokeStyle = col.rain; ctx.lineWidth = 1
        for (let b = 0; b < RINGS; b++) {
          const list = rings[b]
          if (!list.length) continue
          ctx.globalAlpha = (1 - (b + 0.5) / RINGS) * 0.34 * pk
          ctx.beginPath()
          for (let i = 0; i < list.length; i += 3) { ctx.moveTo(list[i] + list[i + 2], list[i + 1]); ctx.arc(list[i], list[i + 1], list[i + 2], 0, TAU) }
          ctx.stroke()
        }
      }
      stats.ripples = ripples.length
      // 10) Snow: small flakes in two depths, slow, swaying slightly (sleet falls faster). The wind
      //     offsets them – with the flow around the house and through the vortices; they still always fall downwards.
      if (flakes.length) {
        const sleet = fx.rain > 0 ? 1.7 : 1
        ctx.fillStyle = col.snow
        for (let pass = 0; pass < 2; pass++) {
          const near = pass === 1
          ctx.globalAlpha = (near ? 0.6 : 0.36) * pk
          ctx.beginPath()
          for (const f of flakes) {
            if (f.near !== near) continue
            if (dt) {
              const fall = (near ? 34 : 18) * f.v * sleet * (1 + 0.6 * wind)
              flowAt(f.x, f.y)
              f.ph += dt * 1.3
              f.y += Math.max(0.3 * fall, fall + (fvy - wv[1]) * wind * 46) * dt
              f.x += (Math.sin(f.ph) * f.sw + fvx * wind * 46) * dt
            }
            if (f.y > by1 + 4) { f.y = box.y - 4; f.x = rnd(box.x, bx1) }
            if (f.y < box.y - 8) f.y += box.h + 12      // pushed out at the top with the plan: back in at the bottom
            if (f.x < box.x - 6) f.x += box.w + 12; if (f.x > bx1 + 6) f.x -= box.w + 12
            const r = (near ? 2.5 : 1.4) * f.r
            ctx.moveTo(f.x + r, f.y); ctx.arc(f.x, f.y, r, 0, TAU)
          }
          ctx.fill()
        }
      }
      // 11) Leaves (from 15 km/h): drift tumbling with the flow around the house and get into the vortices – as fast
      //     as the wind lines, in the accent colour, dampened like the warmest place of the heat map
      if (leaves.length) {
        ctx.globalAlpha = LEAF_ALPHA * pk; ctx.fillStyle = col.leaf
        for (const l of leaves) {
          if (dt) {
            const sp = l.v * pace, wob = Math.sin(l.ph) * 22
            flowAt(l.x, l.y)
            const vx = 0.75 * fvx + 0.25 * wv[0], vy = 0.75 * fvy + 0.25 * wv[1]   // a remainder of free wind: no leaf stays lying in the lee
            l.x += (vx * sp - wv[1] * wob) * dt; l.y += (vy * sp + wv[0] * wob) * dt
            l.rot += l.sp * dt; l.ph += dt * 1.1
          }
          if (l.x < box.x - 12) l.x += box.w + 24; if (l.x > bx1 + 12) l.x -= box.w + 24
          if (l.y < box.y - 12) l.y += box.h + 24; if (l.y > by1 + 12) l.y -= box.h + 24
          ctx.save(); ctx.translate(l.x, l.y); ctx.rotate(l.rot)
          ctx.beginPath(); ctx.moveTo(-l.s, 0); ctx.quadraticCurveTo(0, -l.s * 0.65, l.s, 0); ctx.quadraticCurveTo(0, l.s * 0.65, -l.s, 0)
          ctx.fill(); ctx.restore()
        }
      }
      // 12) Hoarfrost: single crystals glint briefly – brighter in sunlight
      if (glints.length) {
        ctx.fillStyle = col.snow
        const sparkle = 0.8 * (1 + 0.4 * up) * k
        for (const s of glints) {
          const a = Math.pow(Math.max(0, Math.sin(s.ph + t * s.sp)), 3)
          if (a < 0.03) continue
          const x = box.x + s.u * box.w, y = box.y + s.v * box.h
          ctx.globalAlpha = Math.min(1, a * s.a * sparkle)
          ctx.fillRect(x - s.r, y - 0.4, s.r * 2, 0.8); ctx.fillRect(x - 0.4, y - s.r, 0.8, s.r * 2)
        }
      }
      // 13) Thunderstorm: short, double brightening pulse every 8–25 s, occasionally a lightning bolt at the edge (with a soft glow)
      if (fx.thunder && !REDUCED && now) {
        if (!flash && now >= nextFlash) {
          flash = { t0: now, len: rnd(120, 250), bolt: Math.random() < 0.45 ? bolt(box, cx, rad) : null }
          nextFlash = now + rnd(8000, 25000)
        }
        if (flash) {
          const ft = now - flash.t0, L = flash.len
          const env = ft < L ? Math.sin(Math.PI * ft / L) : ft < L * 1.6 ? 0 : ft < L * 2.4 ? 0.6 * Math.sin(Math.PI * (ft - L * 1.6) / (L * 0.8)) : -1
          if (env < 0) flash = null
          else if (env > 0) {
            wash(col.flash, env * (0.12 + 0.08 * dark) * k)
            if (flash.bolt) {
              ctx.strokeStyle = col.flash; ctx.lineJoin = 'round'; ctx.lineCap = 'round'
              for (const pass of [[5, 0.1], [1.3, 0.55]]) {
                ctx.lineWidth = pass[0]; ctx.globalAlpha = env * pass[1] * k
                ctx.beginPath()
                for (const seg of flash.bolt) { ctx.moveTo(seg[0][0], seg[0][1]); for (let i = 1; i < seg.length; i++) ctx.lineTo(seg[i][0], seg[i][1]) }
                ctx.stroke()
              }
            }
          }
        }
      }
      flushEdge()
      edgeCtx.restore()
      mainCtx.restore()
      punch(mainCtx, false)      // clouds and fog: exactly up to the wall, nothing inside
      if (!shown) { shown = true; setTimeout(() => { canvas.style.opacity = '' }, 30) }
    }
    /* Shadow shaft of the building in the sun rays: light that hits the house does not continue behind it. The rooms of
       the floor shown are swept along the light (ax, ay) beyond the frame – the room itself plus one parallelogram per
       edge – and punched out of the margin image, on which only the rays lie right now; the walls and a soft border
       come along as strokes. Every piece is added with the same sense of rotation, so the nonzero rule fills their
       union. Returns the number of rooms that throw a shaft. */
    function shadeRays(mask, ax, ay) {
      const c = edgeCtx, len = 2 * (W + H), dx = ax * len, dy = ay * len
      let parts = 0
      // add a closed piece to the path, turned round if needed so that all pieces rotate the same way
      const piece = pts => {
        let area = 0
        for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) area += (pts[j][0] - pts[i][0]) * (pts[j][1] + pts[i][1])
        if (Math.abs(area) < 1e-6) return
        const list = area > 0 ? pts : pts.slice().reverse()
        c.moveTo(list[0][0], list[0][1])
        for (let i = 1; i < list.length; i++) c.lineTo(list[i][0], list[i][1])
        c.closePath()
      }
      c.save()
      c.globalCompositeOperation = 'destination-out'; c.fillStyle = '#000'; c.strokeStyle = '#000'
      c.lineJoin = 'round'; c.lineCap = 'round'
      c.beginPath()
      for (const p of mask.polys || []) {
        if (p.length < 3) continue
        parts++
        piece(p)
        for (let i = 0, j = p.length - 1; i < p.length; j = i++) piece([p[j], p[i], [p[i][0] + dx, p[i][1] + dy], [p[j][0] + dx, p[j][1] + dy]])
      }
      if (parts) {
        const wall = mask.wall || 6
        c.fill()
        c.lineWidth = wall; c.stroke()
        c.globalAlpha = 0.45; c.lineWidth = wall + 2 * FEATHER; c.stroke()
      }
      c.restore()
      return parts
    }
    // thin, branched lightning bolt at the edge of the image – never above the house (start point outside on the left or right)
    function bolt(box, cx, rad) {
      const left = Math.random() < 0.5
      let x = left ? rnd(box.x + 8, Math.max(box.x + 12, cx - rad - 10)) : rnd(Math.min(box.x + box.w - 12, cx + rad + 10), box.x + box.w - 8)
      let y = box.y, main = [[x, y]]
      const end = box.y + box.h * rnd(0.3, 0.55), segs = []
      while (y < end) { y += rnd(8, 18); x += rnd(-9, 9); main.push([x, y]); if (Math.random() < 0.18) segs.push(branch(x, y)) }
      return [main].concat(segs)
    }
    // Side branch of a lightning bolt: four short pieces slanting downwards.
    function branch(x, y) {
      const out = [[x, y]], dir = Math.random() < 0.5 ? -1 : 1
      for (let i = 0; i < 4; i++) { x += dir * rnd(4, 10); y += rnd(5, 12); out.push([x, y]) }
      return out
    }

    /* ---------- Sequence */
    // fast (30 frames/s) only as long as something falls, flies or flashes; for clouds, fog, stars, hoarfrost and sun rays a
    // calm tick is enough. Stars only count in darkness, sun rays only while the sun shines.
    const quick = () => !!(drops.length || flakes.length || pellets.length || leaves.length || wisps.length || ripples.length || flash || meteor)
    const animated = () => !!fx && !REDUCED && !!(quick() || clouds.length || banks.length || (stars.length && darkNow > 0.15) || glints.length ||
      (rays.length && sunUp) || fx.thunder)
    const running = () => alive && !held && animated() && visible && !document.hidden
    // after every frame: average delay and cost; every CHECK_EVERY frames decide whether a step back is needed
    function govern(gap, interval, spent) {
      if (gap) lag = lag * 0.9 + (gap / interval) * 0.1
      cost = cost * 0.9 + (spent / interval) * 0.1
      if (++sinceCheck < CHECK_EVERY) return
      sinceCheck = 0
      if ((lag > LAG_LIMIT || cost > COST_LIMIT) && step < STEPS.length - 1) {
        step++; lag = 1; cost = 0
        stats.step = step
        if (step >= START_LOW && HO.perf) HO.perf.learnLow()   // this device starts economically in future
        if (resize()) seed()
      }
    }
    // One frame of the animation: draw at the pace of the current step, measure the frame time and request the next frame.
    function frame(ts) {
      raf = 0
      if (!running()) return
      const interval = 1000 / (quick() ? STEPS[step].fps : STEPS[step].calm)
      if (!last || ts - last >= interval - 2) {
        const gap = last ? ts - last : 0, dt = Math.min(0.1, gap / 1000)
        last = ts; stats.frames++
        const t0 = performance.now()
        draw(ts, dt)
        govern(gap, interval, performance.now() - t0)
      }
      raf = requestAnimationFrame(frame)
    }
    function kick() {
      if (running()) { if (!raf) { last = 0; raf = requestAnimationFrame(frame) } }
      else draw(0, 0)   // calm single image (reduced motion, paused, without particles)
    }
    const onVis = () => { if (!document.hidden) kick() }
    document.addEventListener('visibilitychange', onVis)
    let io = null
    if (window.IntersectionObserver) {
      io = new IntersectionObserver(es => { visible = es.some(e => e.isIntersecting); if (visible) kick() })
      io.observe(canvas)
    }
    const ro = window.ResizeObserver ? new ResizeObserver(() => { if (resize() && !raf) draw(0, 0) }) : null
    if (ro) ro.observe(canvas)

    return {
      // Take over new weather or a new strength; without weather the image is cleared.
      update(next, o) {
        const lv = o && o.level === 'subtle' ? 'subtle' : 'normal'
        col = tokens()   // the accent colour may have changed
        fx = next || null; level = lv
        if (!fx) { mainCtx.setTransform(1, 0, 0, 1, 0, 0); mainCtx.clearRect(0, 0, canvas.width, canvas.height); return }
        if (resize()) seed()
        kick()
      },
      redraw() { if (!raf) draw(0, 0) },
      // A gesture at the floor plan begins (on) or ends. Returns whether the weather image stays in its place
      // and keeps running – the house then moves along in the image (getShift, follow). On economical devices the image
      // stands still instead, and the caller moves it as a whole with the plan.
      gesture(on) {
        const stays = !(HO.perf && HO.perf.low) && step < START_LOW
        held = !!on && !stays
        if (!on) kick()
        return stays
      },
      // One frame at the pace of the gesture: the plan was just moved, the house in the weather image follows at once (the
      // own tick would be a touch too late – the house would run away from its section).
      follow() {
        if (!alive || held) return
        if (!running()) return draw(0, 0)
        const ts = performance.now(), gap = last ? ts - last : 0
        last = ts; stats.frames++
        draw(ts, Math.min(0.1, gap / 1000))
      },
      get animating() { return !!raf },
      // Abbauen: Animation anhalten, Beobachter abmelden, Canvas entfernen.
      destroy() {
        alive = false
        if (raf) cancelAnimationFrame(raf)
        raf = 0
        clearTimeout(flowTimer)
        document.removeEventListener('visibilitychange', onVis)
        if (io) io.disconnect()
        if (ro) ro.disconnect()
        if (canvas.parentNode) canvas.parentNode.removeChild(canvas)
      }
    }
  }

  HO.weatherFx = { mount, preset, KINDS, LABELS, REDUCED, stats }
})(window.HO)
