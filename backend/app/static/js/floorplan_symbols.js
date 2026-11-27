/* ComfyOS – plan symbols: the shapes a device (or a group) can take in the floor plan instead of the standard bubble.

   Every symbol is drawn from above, like the floor plan itself, with its front towards the bottom edge (the back of a
   sofa, the screen side of a TV board and the head of a bed are at the top) – the user turns it to the wall it stands at.

   Drawing rules (keep them when adding a symbol):
   - box of 48 × 48 units, the shape is centred on (24, 24); `w`/`h` = the extent it really covers (for the status dot,
     the marks, the name below and the selection frame); `round: true` for circular shapes
   - plain elements are bodies (filled surface with an outline), class `f` = a face lying on a body (cushion, pillow,
     platter), class `l` = a line without a fill, class `k` = a solid ink mark (screen, knob, light) – colours and line
     width come from app.css (`.fp-sym`), never from the symbol
   - details at least 4 units apart: the symbol must still read at 30 pixels (group bubble, small plans)
   - keys are stored in floorplan.json (`devices[id].furniture`, `groups[].icon`) – never rename one */
(function (HO) {
  'use strict'
  const leaf = 'M24 22C19.5 17.5 20 11 24 7c4 4 4.5 10.5 0 15z', sprout = 'M24 22.5c-2.8-3-2.6-7.5 0-10.5 2.6 3 2.8 7.5 0 10.5z'
  const blade = 'M24 23c-4.5-2.5-5.5-7.5-2-11.5 4 2 5 7.5 2 11.5z'
  const turned = (path, cls, angles) => angles.map(a => `<path${cls ? ` class="${cls}"` : ''} transform="rotate(${a} 24 24)" d="${path}"/>`).join('')

  HO.planSymbols = {
    // entertainment and office
    tv: { label: 'Fernseher', w: 42, h: 13,
      svg: '<rect x="3" y="17.5" width="42" height="13" rx="2.5"/><rect class="k" x="7" y="21.5" width="34" height="3.2" rx="1.6"/><path class="l" d="M19 27.5h10"/>' },
    soundbar: { label: 'Soundbar', w: 40, h: 9,
      svg: '<rect x="4" y="19.5" width="40" height="9" rx="4.5"/><circle class="k" cx="12" cy="24" r="1.1"/><circle class="k" cx="18" cy="24" r="1.1"/><circle class="k" cx="24" cy="24" r="1.1"/><circle class="k" cx="30" cy="24" r="1.1"/><circle class="k" cx="36" cy="24" r="1.1"/>' },
    speaker: { label: 'Lautsprecher', w: 22, h: 22,
      svg: '<rect x="13" y="13" width="22" height="22" rx="6"/><circle class="f" cx="24" cy="24" r="6.5"/><circle class="k" cx="24" cy="24" r="1.8"/>' },
    hifi: { label: 'HiFi-Anlage', w: 40, h: 26,
      svg: '<rect x="4" y="11" width="40" height="26" rx="3"/><circle class="f" cx="19" cy="24" r="9.5"/><circle class="l" cx="19" cy="24" r="4"/><circle class="k" cx="19" cy="24" r="1.2"/><path class="l" d="M37 15.5v9L30.5 31M34.5 33.5h5"/><circle class="k" cx="37" cy="15.5" r="1.6"/>' },
    console: { label: 'Spielkonsole', w: 34, h: 16,
      svg: '<path d="M14 16h20a7 7 0 0 1 7 7v2a6 6 0 0 1-11 3.3l-.8-1.3H18.8l-.8 1.3A6 6 0 0 1 7 25v-2a7 7 0 0 1 7-7z"/><path class="l" d="M12.5 22h5.5M15.2 19.3v5.4"/><circle class="k" cx="31" cy="20.5" r="1.2"/><circle class="k" cx="34.2" cy="23.2" r="1.2"/>' },
    computer: { label: 'Computer', w: 29, h: 19,
      svg: '<rect class="k" x="10" y="14" width="28" height="3.2" rx="1.6"/><path class="l" d="M19 20h10"/><rect x="10" y="24" width="20" height="9" rx="2"/><path class="l" d="M13.5 27h13M13.5 30h13"/><rect x="33" y="24.5" width="5" height="8" rx="2.5"/>' },
    printer: { label: 'Drucker', w: 28, h: 30,
      svg: '<rect class="f" x="15" y="9" width="18" height="8" rx="1"/><rect class="f" x="15" y="31" width="18" height="8" rx="1"/><rect x="10" y="15" width="28" height="18" rx="3"/><path class="l" d="M15 20h18M18.5 36h11"/><circle class="k" cx="33" cy="28" r="1.1"/>' },
    router: { label: 'Router', w: 26, h: 23,
      svg: '<path class="l" d="M16 21.5v-7M32 21.5v-7"/><circle class="k" cx="16" cy="13.5" r="1.3"/><circle class="k" cx="32" cy="13.5" r="1.3"/><rect x="11" y="21.5" width="26" height="14" rx="3"/><circle class="k" cx="17" cy="28.5" r="1.1"/><circle class="k" cx="21.5" cy="28.5" r="1.1"/><circle class="k" cx="26" cy="28.5" r="1.1"/>' },
    camera: { label: 'Kamera', w: 24, h: 30,
      svg: '<rect x="17" y="8" width="14" height="10" rx="2.5"/><rect class="k" x="21" y="18" width="6" height="2.5" rx="1"/><path class="l" d="M21.5 23.5 12 38M26.5 23.5 36 38M15.5 33a14 14 0 0 0 17 0"/>' },
    // light
    'floor-lamp': { label: 'Stehlampe', w: 36, h: 36,
      svg: '<path class="l" d="M12 36C14 26 22 18 32 16"/><circle cx="12" cy="36" r="5.5"/><circle class="k" cx="12" cy="36" r="1.4"/><circle cx="32" cy="16" r="10"/><circle class="f" cx="32" cy="16" r="4.2"/>' },
    'ceiling-lamp': { label: 'Deckenleuchte', w: 22, h: 22, round: true,
      svg: '<circle cx="24" cy="24" r="11"/><path class="l" d="M16.2 16.2l15.6 15.6M31.8 16.2 16.2 31.8"/>' },
    'light-bar': { label: 'Lichtleiste', w: 40, h: 21,
      svg: '<rect x="4" y="20.5" width="40" height="7" rx="3.5"/><path class="l" d="M9.5 24h29M12 16v-2.5M24 16v-2.5M36 16v-2.5M12 32v2.5M24 32v2.5M36 32v2.5"/>' },
    // spotlights on a rail (since 0.30.2): round heads with their lens, the rail between them
    'spot-2': { label: 'Spots (2er)', w: 36, h: 14,
      svg: '<rect x="12" y="22" width="24" height="4" rx="2"/><circle cx="13" cy="24" r="7"/><circle cx="35" cy="24" r="7"/><circle class="f" cx="13" cy="24" r="3.4"/><circle class="f" cx="35" cy="24" r="3.4"/>' },
    'spot-3': { label: 'Spots (3er)', w: 44, h: 13,
      svg: '<rect x="8" y="22" width="32" height="4" rx="2"/><circle cx="8.5" cy="24" r="6.5"/><circle cx="24" cy="24" r="6.5"/><circle cx="39.5" cy="24" r="6.5"/><circle class="f" cx="8.5" cy="24" r="3.2"/><circle class="f" cx="24" cy="24" r="3.2"/><circle class="f" cx="39.5" cy="24" r="3.2"/>' },
    // furniture
    sofa: { label: 'Sofa', w: 40, h: 20,
      svg: '<rect x="4" y="14" width="40" height="20" rx="3"/><rect class="f" x="9.5" y="20" width="14" height="11" rx="2"/><rect class="f" x="24.5" y="20" width="14" height="11" rx="2"/>' },
    'corner-sofa': { label: 'Ecksofa', w: 40, h: 34,
      svg: '<path d="M7 7h34a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H25v13a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V10a3 3 0 0 1 3-3z"/><rect class="f" x="10" y="13" width="12" height="9" rx="2"/><rect class="f" x="23.5" y="13" width="15" height="9" rx="2"/><rect class="f" x="10" y="23.5" width="12" height="14.5" rx="2"/>' },
    armchair: { label: 'Sessel', w: 22, h: 22,
      svg: '<rect x="13" y="13" width="22" height="22" rx="4"/><rect class="f" x="18" y="19" width="12" height="13" rx="2"/>' },
    bed: { label: 'Doppelbett', w: 32, h: 38,
      svg: '<rect x="8" y="5" width="32" height="38" rx="3"/><rect class="f" x="11" y="8" width="12" height="8" rx="2.5"/><rect class="f" x="25" y="8" width="12" height="8" rx="2.5"/><path class="l" d="M8 21h32M31 21q1 7 9 8"/>' },
    'single-bed': { label: 'Einzelbett', w: 20, h: 38,
      svg: '<rect x="14" y="5" width="20" height="38" rx="3"/><rect class="f" x="17.5" y="8" width="13" height="8" rx="2.5"/><path class="l" d="M14 21h20M27 21q1 6 7 7"/>' },
    table: { label: 'Esstisch', w: 42, h: 32,
      svg: '<rect class="f" x="13" y="8" width="8" height="4.5" rx="1.5"/><rect class="f" x="27" y="8" width="8" height="4.5" rx="1.5"/><rect class="f" x="13" y="35.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="27" y="35.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="3" y="20" width="4.5" height="8" rx="1.5"/><rect class="f" x="40.5" y="20" width="4.5" height="8" rx="1.5"/><rect x="9" y="14" width="30" height="20" rx="2.5"/>' },
    'round-table': { label: 'Runder Tisch', w: 36, h: 36, round: true,
      svg: '<rect class="f" x="20" y="6" width="8" height="4.5" rx="1.5"/><rect class="f" x="20" y="37.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="6" y="20" width="4.5" height="8" rx="1.5"/><rect class="f" x="37.5" y="20" width="4.5" height="8" rx="1.5"/><circle cx="24" cy="24" r="10.5"/>' },
    desk: { label: 'Schreibtisch', w: 40, h: 30,
      svg: '<rect x="4" y="9" width="40" height="18" rx="2.5"/><rect class="k" x="15" y="12" width="18" height="2.6" rx="1.3"/><rect class="f" x="17" y="18.5" width="14" height="5" rx="1.2"/><rect class="f" x="18" y="30" width="12" height="9" rx="3"/>' },
    plant: { label: 'Pflanze', w: 34, h: 34, round: true,
      svg: turned(sprout, 'f', [60, 180, 300]) + turned(leaf, '', [0, 120, 240]) + '<circle class="k" cx="24" cy="24" r="1.6"/>' },
    bathtub: { label: 'Badewanne', w: 38, h: 20,
      svg: '<rect x="5" y="14" width="38" height="20" rx="5"/><rect class="f" x="9" y="17.5" width="30" height="13" rx="6.5"/><circle class="k" cx="14.5" cy="24" r="1.2"/>' },
    // household
    fridge: { label: 'Kühlschrank', w: 24, h: 25,
      svg: '<rect x="12" y="11" width="24" height="21" rx="3"/><rect class="f" x="12" y="32" width="24" height="4" rx="1.5"/><path class="l" d="M24 16v11M19.2 18.8l9.6 5.4M28.8 18.8l-9.6 5.4"/>' },
    washer: { label: 'Waschmaschine', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><circle class="f" cx="24" cy="26" r="7"/><circle class="l" cx="24" cy="26" r="3.2"/><path class="l" d="M16 15.5h5"/><circle class="k" cx="31.5" cy="15.5" r="1.1"/>' },
    dishwasher: { label: 'Spülmaschine', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><path class="l" d="M12 18h24M20 15h8"/><circle class="f" cx="19.2" cy="27" r="4.2"/><circle class="f" cx="28.8" cy="27" r="4.2"/><circle class="k" cx="19.2" cy="27" r="1.1"/><circle class="k" cx="28.8" cy="27" r="1.1"/>' },
    stove: { label: 'Herd', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><circle class="f" cx="18.5" cy="18.5" r="3.6"/><circle class="f" cx="29.5" cy="18.5" r="3.6"/><circle class="f" cx="18.5" cy="29.5" r="3.6"/><circle class="f" cx="29.5" cy="29.5" r="3.6"/>' },
    coffee: { label: 'Kaffeemaschine', w: 20, h: 24,
      svg: '<rect class="f" x="17" y="26" width="14" height="10" rx="2"/><rect x="14" y="12" width="20" height="16" rx="3"/><path class="l" d="M18.5 17h11"/><circle cx="24" cy="31.5" r="3"/><circle class="k" cx="24" cy="31.5" r="1.3"/>' },
    fan: { label: 'Ventilator', w: 28, h: 28, round: true,
      svg: '<circle cx="24" cy="24" r="14"/>' + turned(blade, 'f', [0, 120, 240]) + '<circle class="k" cx="24" cy="24" r="1.8"/>' },
    vacuum: { label: 'Saugroboter', w: 26, h: 26, round: true,
      svg: '<circle cx="24" cy="24" r="13"/><circle class="f" cx="24" cy="20" r="4.5"/><circle class="k" cx="24" cy="20" r="1.2"/><path class="l" d="M14.5 29.5a11 11 0 0 0 19 0"/>' },
    car: { label: 'Auto', w: 27, h: 40,
      svg: '<rect x="13" y="4" width="22" height="40" rx="7"/><path class="f" d="M16.5 15c5-2 10-2 15 0l-1.5 6H18z"/><path class="f" d="M18 33h12l1.5 4.5c-5 1.5-10 1.5-15 0z"/><path class="l" d="M18 21v12M30 21v12M13 16l-2.5 1M35 16l2.5 1"/>' }
  }

  // A symbol as a small picture of its own (editor: symbol picker).
  HO.planSymbolIcon = key => {
    const symbol = HO.planSymbols[key]
    return symbol ? `<svg class="fp-sym-pic" viewBox="0 0 48 48" aria-hidden="true"><g class="fp-sym">${symbol.svg}</g></svg>` : ''
  }

  /* Smart stretching (since 0.29): a plan symbol drawn wider or taller than its drawing is never distorted – a light
     bar (a slot) grows longer and keeps its round ends, the dots of a sound bar spread along it, the cone of a speaker
     stays round. Before, the drawing was scaled on one axis and every rounded end became an ellipse.
     - The symbol is drawn at one scale for both axes (the smaller one, see floorplan.js symbolBox); along the other axis
       extra length is laid in: `ax`/`ay` = how many times wider/taller than drawn (≥ 1, one of them 1).
     - On that axis whatever lies within `cap` of either end (half the shorter side of the symbol) only moves outwards, the
       middle between the two stretches. A symbol as wide as it is tall has no middle: it opens at its centre line.
     - A rect keeps the radius of its corners, a circle stays a circle and only moves – unless it is the body of the
       symbol and spans the stretch (a round ceiling lamp, a round table): then it becomes a slot with round ends.
     - A path moves every point; an arc keeps its radius wherever nothing stretches.
     HO.planSymbolShape(key, ax, ay) → { parts: [{d, cls}], svg, w, h } in units of the drawing (centre 24, 24; w/h = the
     extent it covers now) or null for an unknown key. Every part is a path (`d` absolute, `cls` the class of the
     drawing) – the same outline serves the symbol, its shadow (floorplan.js) and the effects that leave it free
     (weather_fx.js). A drawing that cannot be read is stretched as before (`parts` null). Remembered per key and stretch. */
  const shapes = {}
  let shapeCount = 0
  const fmt = v => String(Math.round(v * 100) / 100)

  // One axis of the drawing, relative to its centre: the ends (within `cap` of the edge at `half`) move outwards by the
  // extra length, the middle between them stretches to take it; without a middle the drawing opens at the centre.
  function axisMap(half, cap, stretch) {
    const extra = half * (stretch - 1), inner = half - cap
    if (!(extra > 1e-6)) return t => t
    if (inner < 0.5) return t => t > 1e-6 ? t + extra : t < -1e-6 ? t - extra : t
    const slope = (inner + extra) / inner
    return t => Math.abs(t) <= inner ? t * slope : t + (t > 0 ? extra : -extra)
  }

  // The elements of a drawing: tag, attributes, class and the turn of a `rotate(a 24 24)`. Null if the drawing holds
  // anything but rect, circle and path.
  function readElements(svg) {
    const list = [], re = /<(rect|circle|path)\b([^>]*)\/>/g
    let m
    while ((m = re.exec(svg))) {
      const a = {}
      m[2].replace(/([a-z-]+)="([^"]*)"/g, (all, k, v) => { a[k] = v; return all })
      const turn = /rotate\(\s*(-?[\d.]+)/.exec(a.transform || '')
      list.push({ tag: m[1], a, cls: a.class || '', turn: turn ? +turn[1] : 0 })
    }
    return list.length === (svg.match(/</g) || []).length ? list : null
  }

  // Path data as absolute segments [{c: 'M'|'L'|'C'|'Q'|'A'|'Z', p: [numbers]}] (H and V become L) – null for a
  // command the symbols do not use or a broken path.
  function absPath(d) {
    const tok = String(d).match(/[a-zA-Z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) || [], out = []
    const NEED = { M: 2, L: 2, H: 1, V: 1, C: 6, Q: 4, A: 7 }
    let i = 0, cmd = '', x = 0, y = 0, x0 = 0, y0 = 0
    while (i < tok.length) {
      if (/^[a-zA-Z]$/.test(tok[i])) cmd = tok[i++]
      else if (!cmd || cmd === 'z' || cmd === 'Z') return null
      const C = cmd.toUpperCase(), rel = cmd !== C, bx = rel ? x : 0, by = rel ? y : 0
      if (C === 'Z') { out.push({ c: 'Z', p: [] }); x = x0; y = y0; continue }
      const need = NEED[C]
      if (!need || i + need > tok.length) return null
      const v = tok.slice(i, i + need).map(Number)
      i += need
      if (v.some(q => !isFinite(q))) return null
      if (C === 'M') { x = bx + v[0]; y = by + v[1]; x0 = x; y0 = y; out.push({ c: 'M', p: [x, y] }); cmd = rel ? 'l' : 'L' }
      else if (C === 'L') { x = bx + v[0]; y = by + v[1]; out.push({ c: 'L', p: [x, y] }) }
      else if (C === 'H') { x = bx + v[0]; out.push({ c: 'L', p: [x, y] }) }
      else if (C === 'V') { y = by + v[0]; out.push({ c: 'L', p: [x, y] }) }
      else if (C === 'C') { out.push({ c: 'C', p: [bx + v[0], by + v[1], bx + v[2], by + v[3], bx + v[4], by + v[5]] }); x = bx + v[4]; y = by + v[5] }
      else if (C === 'Q') { out.push({ c: 'Q', p: [bx + v[0], by + v[1], bx + v[2], by + v[3]] }); x = bx + v[2]; y = by + v[3] }
      else { out.push({ c: 'A', p: [v[0], v[1], v[2], v[3], v[4], bx + v[5], by + v[6]] }); x = bx + v[5]; y = by + v[6] }
    }
    return out
  }

  // A rounded rectangle as path data (the corners keep their radius).
  function rectPath(x0, y0, x1, y1, r) {
    r = Math.min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    if (!(r > 0.01)) return `M${fmt(x0)} ${fmt(y0)}H${fmt(x1)}V${fmt(y1)}H${fmt(x0)}Z`
    const arc = `A${fmt(r)} ${fmt(r)} 0 0 1 `
    return `M${fmt(x0 + r)} ${fmt(y0)}H${fmt(x1 - r)}${arc}${fmt(x1)} ${fmt(y0 + r)}V${fmt(y1 - r)}${arc}${fmt(x1 - r)} ${fmt(y1)}` +
      `H${fmt(x0 + r)}${arc}${fmt(x0)} ${fmt(y1 - r)}V${fmt(y0 + r)}${arc}${fmt(x0 + r)} ${fmt(y0)}Z`
  }
  const circlePath = (cx, cy, r) => `M${fmt(cx - r)} ${fmt(cy)}A${fmt(r)} ${fmt(r)} 0 1 0 ${fmt(cx + r)} ${fmt(cy)}A${fmt(r)} ${fmt(r)} 0 1 0 ${fmt(cx - r)} ${fmt(cy)}Z`

  // Absolute segments as path data, every point turned by `turn` degrees about the centre and then moved by `place`; an
  // arc takes the stretch its two ends got (none at the ends of a symbol, so a rounded end stays round).
  function pathData(segs, place, turn) {
    const a = turn * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a)
    const spun = (x, y) => turn ? [24 + (x - 24) * ca - (y - 24) * sa, 24 + (x - 24) * sa + (y - 24) * ca] : [x, y]
    let out = '', px = 0, py = 0, sx = 0, sy = 0
    for (const g of segs) {
      const p = g.p
      if (g.c === 'Z') { out += 'Z'; px = sx; py = sy; continue }
      if (g.c === 'A') {
        const from = spun(px, py), to = spun(p[5], p[6]), q0 = place(from[0], from[1]), q1 = place(to[0], to[1])
        const kx = Math.abs(to[0] - from[0]) > 1e-6 ? Math.abs(q1[0] - q0[0]) / Math.abs(to[0] - from[0]) : 1
        const ky = Math.abs(to[1] - from[1]) > 1e-6 ? Math.abs(q1[1] - q0[1]) / Math.abs(to[1] - from[1]) : 1
        out += `A${fmt(p[0] * kx)} ${fmt(p[1] * ky)} ${fmt(p[2] + turn)} ${p[3] ? 1 : 0} ${p[4] ? 1 : 0} ${fmt(q1[0])} ${fmt(q1[1])}`
      } else {
        const pts = []
        for (let i = 0; i < p.length; i += 2) { const s = spun(p[i], p[i + 1]), q = place(s[0], s[1]); pts.push(fmt(q[0]) + ' ' + fmt(q[1])) }
        out += g.c + pts.join(' ')
      }
      px = p[p.length - 2]; py = p[p.length - 1]
      if (g.c === 'M') { sx = px; sy = py }
    }
    return out
  }

  // The parts of a drawing laid out for a stretch (see above); null if the drawing cannot be read.
  function stretchedParts(sym, ax, ay) {
    const elements = readElements(sym.svg)
    if (!elements) return null
    const cap = Math.min(sym.w, sym.h) / 2, fx = axisMap(sym.w / 2, cap, ax), fy = axisMap(sym.h / 2, cap, ay)
    const place = (x, y) => [24 + fx(x - 24), 24 + fy(y - 24)]
    const parts = []
    for (const el of elements) {
      const a = el.a
      let d
      if (el.tag === 'rect') {
        const x = +a.x || 0, y = +a.y || 0, w = +a.width || 0, h = +a.height || 0, r = +a.rx || 0
        if (el.turn) { const segs = absPath(rectPath(x, y, x + w, y + h, r)); d = segs && pathData(segs, place, el.turn) }
        else { const p0 = place(x, y), p1 = place(x + w, y + h); d = rectPath(p0[0], p0[1], p1[0], p1[1], r) }
      } else if (el.tag === 'circle') {
        const r = +a.r || 0, t = el.turn * Math.PI / 180, ox = (+a.cx || 0) - 24, oy = (+a.cy || 0) - 24
        const cx = 24 + ox * Math.cos(t) - oy * Math.sin(t), cy = 24 + ox * Math.sin(t) + oy * Math.cos(t)
        const p0 = place(cx - r, cy - r), p1 = place(cx + r, cy + r), c = place(cx, cy)
        // the body of the symbol across the stretch (a round lamp, a round table) becomes a slot, every other circle moves
        const body = !el.cls && ((ax > 1 && Math.abs(cx - 24) < r && 2 * r >= sym.w / 2) || (ay > 1 && Math.abs(cy - 24) < r && 2 * r >= sym.h / 2))
        d = body && (p1[0] - p0[0] > 2 * r + 0.01 || p1[1] - p0[1] > 2 * r + 0.01) ? rectPath(p0[0], p0[1], p1[0], p1[1], r) : circlePath(c[0], c[1], r)
      } else {
        const segs = absPath(a.d || '')
        d = segs && pathData(segs, place, el.turn)
      }
      if (!d) return null
      parts.push({ d, cls: el.cls })
    }
    return parts
  }

  // The shape of a plan symbol for a stretch (described above the helpers): remembered per key and stretch.
  HO.planSymbolShape = (key, ax, ay) => {
    const sym = HO.planSymbols[key]
    if (!sym) return null
    ax = Math.max(1, Number(ax) || 1); ay = Math.max(1, Number(ay) || 1)
    const id = key + '|' + ax.toFixed(3) + '|' + ay.toFixed(3)
    if (shapes[id]) return shapes[id]
    if (shapeCount > 300) { for (const k of Object.keys(shapes)) delete shapes[k]; shapeCount = 0 }   // a long editing session
    let parts = null
    try { parts = stretchedParts(sym, ax, ay) } catch (e) { parts = null }   // a drawing it cannot read: stretched plainly
    const svg = parts ? parts.map(p => `<path${p.cls ? ` class="${p.cls}"` : ''} d="${p.d}"/>`).join('')
      : `<g transform="translate(24 24) scale(${ax} ${ay}) translate(-24 -24)">${sym.svg}</g>`
    shapeCount++
    return (shapes[id] = { parts, svg, w: sym.w * ax, h: sym.h * ay })
  }
})(window.HO)
