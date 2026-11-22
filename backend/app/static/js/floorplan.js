/* ComfyOS – floor plan: view and editor (mouse + touch through pointer events)
   Only walls are drawn. Every area enclosed by walls all around is automatically a room.
   Doors, windows and radiators go onto the walls; devices are placed freely.
   Since 0.25 a plan has several floors (one is shown and edited at a time, see "Several floors") and balconies or
   terraces (`decks`: a closed outline drawn like a wall; they lie outside, the weather passes over them). */
(function (HO) {
  'use strict'
  const SNAP = 0.25, VSNAP = 0.4, WSNAP = 0.3, ESNAP = 0.7, EPS = 1e-3
  // grid snapping can be switched off (editor preference in the browser); snapping to wall points/walls always stays on
  let gridSnap = (() => { try { return localStorage.getItem('ho.fpSnap') !== '0' } catch (e) { return true } })()
  const snap = (v, s = SNAP) => gridSnap ? Math.round(v / s) * s : Math.round(v * 1000) / 1000
  const COARSE = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches)   // touch: larger handles
  const REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
  const uid = p => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 5)
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
  const r3 = v => Math.round(v * 1000) / 1000
  const clone = o => JSON.parse(JSON.stringify(o))
  const cross = (a, b) => a[0] * b[1] - a[1] * b[0]
  /* Devices in the plan. The standard bubble has one fixed size (radius R, from the scale of the view); a group bubble is
     GROUP_SCALE times as large. Only a plan symbol (floorplan_symbols.js) has a size of its own: at size 1 its box of 48
     units covers SYMBOL_CELLS cells – it keeps its proportion to the rooms at every zoom; SYMBOL_MIN_PX keeps it
     readable on very small plans. DOT = radius of a status dot (docked device, plan symbol), MARK = radius of a mark
     (battery, lock, no contact, fault), both as a share of R. */
  const GROUP_SCALE = 1.3, DOT = 0.36, MARK = 0.44, COUNT_MARK = 0.56
  const MAX_DOCKED = 4   // devices at one window, door or radiator (floorplan_store.MAX_DOCKED)
  const SYMBOL_CELLS = 2, SYMBOL_MIN = 0.5, SYMBOL_MAX = 4, SYMBOL_STEP = 0.1, SYMBOL_TURN = 45, SYMBOL_MIN_PX = 26
  // Several floors: what every floor holds of its own (floorplan_store.CONTENT_KEYS) and how many a plan can have
  const CONTENT = ['walls', 'rooms', 'openings', 'devices', 'groups', 'decks', 'stairs'], MAX_FLOORS = 8, GROUND_ID = 'ground'
  const STAIR_MIN = 0.5, STAIR_MAX = 6, STAIR_WIDTH = 2   // width of a staircase in cells (floorplan_store.STAIR_WIDTH)
  // Editor preferences in the browser (like the grid): the neighbouring floor shines through, points snap to its walls
  const readPref = (key, fallback) => { try { const v = localStorage.getItem(key); return v == null ? fallback : v !== '0' } catch (e) { return fallback } }
  const writePref = (key, on) => { try { localStorage.setItem(key, on ? '1' : '0') } catch (e) { } }
  let ghostOn = readPref('ho.fpGhost', true), floorSnap = readPref('ho.fpFloorSnap', true)

  /* ---------- Geometrie */
  const edgeOf = (w, i) => [w.pts[i], w.pts[i + 1], w.bend[i] || null]
  // control point such that the curve passes through m at t = 0.5 (dragging at the middle of the edge)
  const ctrlFor = (a, b, m) => [2 * m[0] - (a[0] + b[0]) / 2, 2 * m[1] - (a[1] + b[1]) / 2]
  const midOf = (a, b, c) => c ? [0.25 * a[0] + 0.5 * c[0] + 0.25 * b[0], 0.25 * a[1] + 0.5 * c[1] + 0.25 * b[1]] : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]

  // SVG path of a wall: straight pieces as lines, bent ones as quadratic curves.
  function pathOf(w) {
    let d = `M${w.pts[0][0]} ${w.pts[0][1]}`
    for (let i = 0; i < w.pts.length - 1; i++) {
      const [, b, c] = edgeOf(w, i)
      d += c ? `Q${c[0]} ${c[1]} ${b[0]} ${b[1]}` : `L${b[0]} ${b[1]}`
    }
    return d
  }
  // Wall as a sequence of straight pieces – every bend is split into twelve short segments (basis for areas and hits).
  function flatten(w) {
    const out = []
    for (let i = 0; i < w.pts.length - 1; i++) {
      const [a, b, c] = edgeOf(w, i)
      out.push(a)
      if (c) for (let k = 1; k < 12; k++) {
        const t = k / 12, u = 1 - t
        out.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]])
      }
    }
    out.push(w.pts[w.pts.length - 1])
    return out
  }
  // Liegt der Punkt im Vieleck? (Strahl-Verfahren)
  function inside(poly, p) {
    let ins = false
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j]
      if ((yi > p[1]) !== (yj > p[1]) && p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi) ins = !ins
    }
    return ins
  }
  // Bounding box of a polygon: top left corner, width, height.
  function bbox(poly) {
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1])
    const x = Math.min(...xs), y = Math.min(...ys)
    return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }
  }
  // Signed area of a polygon (the winding direction determines the sign).
  function area(poly) {
    let a = 0
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) a += poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
    return a / 2
  }
  // Centroid of a polygon; for an area without extent the centre of its bounding box.
  function centroid(poly) {
    let a = 0, cx = 0, cy = 0
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const f = poly[j][0] * poly[i][1] - poly[i][0] * poly[j][1]
      a += f; cx += (poly[j][0] + poly[i][0]) * f; cy += (poly[j][1] + poly[i][1]) * f
    }
    if (Math.abs(a) < 1e-9) { const b = bbox(poly); return [b.x + b.w / 2, b.y + b.h / 2] }
    return [cx / (3 * a), cy / (3 * a)]
  }
  // Drop a point onto a segment: position on the segment (t = 0…1), foot point and distance.
  function project(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1e-9
    const t = clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2, 0, 1)
    const q = [a[0] + dx * t, a[1] + dy * t]
    return { t, q, d: dist(p, q) }
  }
  // free width of an area at the height of p (for the label)
  function chord(poly, p) {
    let l = -Infinity, r = Infinity
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j]
      if ((yi > p[1]) === (yj > p[1])) continue
      const x = xi + (p[1] - yi) * (xj - xi) / (yj - yi)
      if (x <= p[0]) l = Math.max(l, x); else r = Math.min(r, x)
    }
    return isFinite(l) && isFinite(r) ? r - l : 2
  }
  const edgeDist = (poly, p) => Math.min(...poly.map((a, i) => project(p, a, poly[(i + 1) % poly.length]).d))
  // label point: centroid, unless it lies outside or close to a wall (L shape, arcs)
  function labelPoint(poly) {
    const c = centroid(poly), bb = bbox(poly)
    let best = null, bd = -1
    for (let i = 1; i < 24; i++) for (let j = 1; j < 24; j++) {
      const q = [bb.x + bb.w * i / 24, bb.y + bb.h * j / 24]
      if (!inside(poly, q)) continue
      const d = edgeDist(poly, q)
      if (d > bd) { bd = d; best = q }
    }
    if (inside(poly, c) && edgeDist(poly, c) >= bd * 0.7) return c
    return best || c
  }

  /* ---------- Rooms from walls: intersections, graph, closed areas */
  function segIntersect(p, r, q, s) {   // segments p+t·r and q+u·s → list [t, u]
    const den = cross(r, s), Lr = Math.hypot(...r) || 1e-9, Ls = Math.hypot(...s) || 1e-9
    const qp = [q[0] - p[0], q[1] - p[1]]
    if (Math.abs(den) > 1e-9 * Lr * Ls) {
      const t = cross(qp, s) / den, u = cross(qp, r) / den, tt = 0.01 / Lr, tu = 0.01 / Ls
      return t >= -tt && t <= 1 + tt && u >= -tu && u <= 1 + tu ? [[clamp(t, 0, 1), clamp(u, 0, 1)]] : []
    }
    if (Math.abs(cross(qp, r)) / Lr > 0.01) return []   // parallel, not on one line
    // collinear: end points of the one on the other
    const out = [], rr = Lr * Lr, ss = Ls * Ls
    for (const e of [q, [q[0] + s[0], q[1] + s[1]]]) {
      const t = ((e[0] - p[0]) * r[0] + (e[1] - p[1]) * r[1]) / rr
      if (t > 0 && t < 1) out.push([t, null])
    }
    for (const e of [p, [p[0] + r[0], p[1] + r[1]]]) {
      const u = ((e[0] - q[0]) * s[0] + (e[1] - q[1]) * s[1]) / ss
      if (u > 0 && u < 1) out.push([null, u])
    }
    return out
  }
  // Compute the closed areas (rooms) from the walls: split walls at their intersections, merge nearby points,
  // walk the cycles and leave out the outer area.
  function computeFaces(walls) {
    const segs = []
    for (const w of walls) {
      const f = flatten(w)
      for (let i = 0; i < f.length - 1; i++) if (dist(f[i], f[i + 1]) > EPS) segs.push({ p: f[i], r: [f[i + 1][0] - f[i][0], f[i + 1][1] - f[i][1]], cuts: [0, 1] })
    }
    for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++) {
      const A = segs[i], B = segs[j]
      for (const [t, u] of segIntersect(A.p, A.r, B.p, B.r)) { if (t != null) A.cuts.push(t); if (u != null) B.cuts.push(u) }
    }
    // merge nodes (grid hash)
    const verts = [], grid = new Map(), TOL = 0.02
    // Number of the corner point at this place – one already known in the immediate vicinity is reused.
    const vid = q => {
      const gx = Math.round(q[0] / TOL), gy = Math.round(q[1] / TOL)
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        for (const k of grid.get((gx + dx) + ',' + (gy + dy)) || []) if (dist(verts[k], q) < TOL) return k
      }
      verts.push(q); const key = gx + ',' + gy
      grid.set(key, [...(grid.get(key) || []), verts.length - 1])
      return verts.length - 1
    }
    const adj = new Map(), add = (a, b) => { if (!adj.has(a)) adj.set(a, new Set()); adj.get(a).add(b) }
    for (const s of segs) {
      const ts = [...new Set(s.cuts.map(t => Math.round(t * 1e6) / 1e6))].sort((a, b) => a - b)
      let prev = null
      for (const t of ts) {
        const v = vid([s.p[0] + s.r[0] * t, s.p[1] + s.r[1] * t])
        if (prev != null && prev !== v) { add(prev, v); add(v, prev) }
        prev = v
      }
    }
    // remove open wall ends – they bound no area
    let pruned = true
    while (pruned) {
      pruned = false
      for (const [v, ns] of adj) if (ns.size < 2) { for (const n of ns) adj.get(n).delete(v); adj.delete(v); pruned = true }
    }
    const sorted = new Map()
    for (const [v, ns] of adj) sorted.set(v, [...ns].sort((a, b) =>
      Math.atan2(verts[a][1] - verts[v][1], verts[a][0] - verts[v][0]) - Math.atan2(verts[b][1] - verts[v][1], verts[b][0] - verts[v][0])))
    const seen = new Set(), faces = []
    for (const [u0, ns] of sorted) for (const v0 of ns) {
      if (seen.has(u0 + '>' + v0)) continue
      const poly = []
      let u = u0, v = v0, guard = 0
      while (!seen.has(u + '>' + v) && guard++ < 5000) {
        seen.add(u + '>' + v); poly.push(verts[u])
        const list = sorted.get(v), i = list.indexOf(u)
        const w = list[(i - 1 + list.length) % list.length]
        u = v; v = w
      }
      const A = area(poly)
      if (A > 0.05 && poly.length >= 3) faces.push({ poly, area: A })   // inner areas; the outer border has a negative area
    }
    faces.sort((a, b) => a.area - b.area)
    for (const f of faces) f.label = labelPoint(f.poly)
    return faces
  }
  // assign the room entries (name, link) to the areas – through their anchor point
  function assign(faces, rooms) {
    const used = new Set()
    return faces.map(f => {
      const cand = rooms.filter(r => !used.has(r.id) && inside(f.poly, [r.x, r.y]))
        .sort((a, b) => (!!b.room_id - !!a.room_id))
      const room = cand[0] || null
      if (room) used.add(room.id)
      return { face: f, room }
    })
  }

  // convert old plans (rooms as polygons/rectangles, lines) into walls + room anchors
  function normWall(w, closed) {
    let pts = (w.pts || []).map(p => [+p[0], +p[1]])
    let bend = pts.map((_, i) => (w.bend && w.bend[i]) || null)
    if (closed && pts.length >= 3) { pts = [...pts, pts[0]] } else bend = bend.slice(0, pts.length - 1)
    return { id: w.id || uid('w'), pts, bend: bend.slice(0, pts.length - 1) }
  }
  // Balcony or terrace: a closed outline, stored like a wall whose last point repeats the first.
  function normDeck(d) {
    const w = normWall(d, false)
    if (dist(w.pts[0], w.pts[w.pts.length - 1]) > EPS) { w.pts.push([...w.pts[0]]); w.bend.push(null) }
    w.id = d.id || uid('d')
    if (d.surface === 'tiles' || d.surface === 'plain') w.surface = d.surface   // without it: planks of wood
    return w
  }
  // Bring what one floor holds into the form that is drawn: clean walls, openings and decks, switch rooms of the old
  // kind to anchor points, a device is in one group only.
  function normContent(src) {
    const c = {}
    c.walls = (src.walls || []).filter(w => w.pts && w.pts.length >= 2).map(w => normWall(w, false))
    c.rooms = []
    for (const r of src.rooms || []) {
      if (!r.pts && r.w != null) r.pts = [[r.x, r.y], [r.x + r.w, r.y], [r.x + r.w, r.y + r.h], [r.x, r.y + r.h]]
      if (r.pts && r.pts.length >= 3) {
        const w = normWall({ pts: r.pts, bend: r.bend }, true)
        c.walls.push(w)
        const at = labelPoint(flatten(w).slice(0, -1))
        c.rooms.push({ id: r.id || uid('r'), name: r.name || 'Raum', room_id: r.room_id || null, x: at[0], y: at[1] })
      } else if (r.x != null && r.y != null && !r.pts) c.rooms.push({ id: r.id || uid('r'), name: r.name || 'Raum', room_id: r.room_id || null, x: +r.x, y: +r.y })
    }
    for (const l of src.lines || []) if (l.pts && l.pts.length >= 2) c.walls.push(normWall(l, false))
    c.openings = (src.openings || []).map(o => ({ ...o, type: ['door', 'window', 'passage', 'radiator'].includes(o.type) ? o.type : 'window', side: o.side === -1 ? -1 : 1 }))
    c.devices = src.devices && typeof src.devices === 'object' ? src.devices : {}
    const grouped = new Set()
    c.groups = (src.groups || []).map(g => ({ id: g.id || uid('g'), name: g.name || '', icon: g.icon || '', x: +g.x || 0, y: +g.y || 0,
      members: (g.members || []).filter(id => !grouped.has(id) && grouped.add(id)) })).filter(g => g.members.length)
    for (const id of grouped) delete c.devices[id]
    c.decks = (src.decks || []).filter(d => d.pts && d.pts.length >= 3).map(normDeck)
    c.stairs = (src.stairs || []).filter(t => t && t.a && t.b).map(t => ({ id: t.id || uid('s'), a: [+t.a[0], +t.a[1]], b: [+t.b[0], +t.b[1]],
      width: clamp(+t.width || STAIR_WIDTH, STAIR_MIN, STAIR_MAX) }))
    return c
  }

  /* ---------- Several floors (since 0.25)
     Stored (floorplan_store.py): walls, rooms, openings, devices, groups and decks at the top level are the floor with
     level 0, every further floor carries the same six lists in `floors` (level > 0 above, < 0 below). A plan from
     before 0.25 simply has one floor.
     Drawn: the working plan (normPlan) holds all floors in one form in `levels` (bottom to top) and one of them as
     `floor` – the floor shown and edited. `plan.walls`, `plan.rooms`, … always are the lists of this floor, so
     everything that draws or edits works on one floor and cannot reach another one by accident. Size, address and
     display settings are shared. JSON.stringify(plan) – and with it saving and the undo history – yields the stored
     form again (toJSON). */
  // Name of a floor: its own, or one from its level.
  const floorLabel = f => f.name || (f.level === 0 ? 'Erdgeschoss' : f.level > 0 ? f.level + '. Obergeschoss' : f.level === -1 ? 'Keller' : -f.level + '. Untergeschoss')
  const floorShort = f => f.name || (f.level === 0 ? 'EG' : f.level > 0 ? f.level + '. OG' : f.level === -1 ? 'KG' : -f.level + '. UG')
  // Give the floors levels without gaps around `ground` (level 0) and sort them bottom to top. A further floor without
  // a level goes on top.
  function relevel(levels, ground) {
    const rank = f => f.level > 0 ? f.level : 1e9
    const above = levels.filter(f => f !== ground && f.level >= 0).sort((a, b) => rank(a) - rank(b))
    const below = levels.filter(f => f !== ground && f.level < 0).sort((a, b) => b.level - a.level)
    ground.level = 0
    above.forEach((f, i) => { f.level = i + 1 })
    below.forEach((f, i) => { f.level = -i - 1 })
    levels.sort((a, b) => a.level - b.level)
  }
  // The stored form of a working plan: the floor with level 0 at the top level, the others in `floors`.
  function storedPlan(p) {
    const out = Object.assign({}, p), ground = p.levels.find(f => f.level === 0) || p.levels[0]
    CONTENT.forEach(key => { out[key] = ground[key] })
    out.floor_id = ground.id; out.floor_name = ground.name
    out.floors = p.levels.filter(f => f !== ground).map(f => {
      const floor = { id: f.id, name: f.name, level: f.level }
      CONTENT.forEach(key => { floor[key] = f[key] })
      return floor
    })
    return out
  }
  // Bring the plan from the server into the form that is drawn: add defaults, bring every floor into one form and open
  // the floor `floorId` (without it or if it is gone: the one with level 0).
  function normPlan(plan, floorId) {
    const p = clone(plan || {})
    p.width = p.width || 24; p.height = p.height || 14; p.address = p.address || ''; p.show_names = p.show_names !== false; p.weather = p.weather !== false; p.warnings = p.warnings !== false
    p.home_name = p.home_name || ''
    p.shadow = p.shadow !== false; p.north = ((Math.round(+p.north || 0) % 360) + 360) % 360
    const ground = Object.assign({ id: p.floor_id || GROUND_ID, name: p.floor_name || '', level: 0 }, normContent(p)), levels = [ground]
    for (const f of Array.isArray(p.floors) ? p.floors : []) {
      if (f && typeof f === 'object' && levels.length < MAX_FLOORS) levels.push(Object.assign({ id: f.id || uid('f'), name: f.name || '', level: Math.round(+f.level) || 0 }, normContent(f)))
    }
    relevel(levels, ground)
    for (const key of CONTENT.concat(['floors', 'floor_id', 'floor_name', 'lines'])) delete p[key]
    // what belongs to the floors is not an own, listed property of the plan: it is reached through `floor`
    const hide = (key, spec) => Object.defineProperty(p, key, Object.assign({ enumerable: false, configurable: true }, spec))
    hide('levels', { value: levels, writable: true })
    hide('floor', { value: levels.find(f => f.id === floorId) || ground, writable: true })
    CONTENT.forEach(key => hide(key, { get() { return p.floor[key] }, set(value) { p.floor[key] = value } }))
    hide('toJSON', { value: () => storedPlan(p) })
    return p
  }
  // All floors of a plan in one form, bottom to top, each with `label` and `short` – for pages that only read the plan
  // as /api/floorplan delivers it (groups of all floors, choice of a floor, "is anything drawn at all").
  function floorsOf(plan) {
    const p = plan || {}
    let list = p.levels
    if (!list) {
      const ground = { id: p.floor_id || GROUND_ID, name: p.floor_name || '', level: 0 }
      CONTENT.forEach(key => { ground[key] = p[key] || (key === 'devices' ? {} : []) })
      list = [ground].concat((Array.isArray(p.floors) ? p.floors : []).filter(f => f && typeof f === 'object'))
        .sort((a, b) => (a.level || 0) - (b.level || 0))
    }
    return list.map(f => Object.assign({}, f, { label: floorLabel(f), short: floorShort(f) }))
  }

  /* ---------- Device symbols */
  // The plan symbol of a stored device (`furniture` = key, `size`, `rot` in degrees) – or null for the standard bubble.
  // k = cells per symbol unit; hw/hh = half width and height of the box the turned symbol covers, in cells (u = one pixel).
  function symbolBox(entry, u) {
    const sym = entry && entry.furniture && HO.planSymbols[entry.furniture]
    if (!sym) return null
    const size = clamp(Number(entry.size) || 1, SYMBOL_MIN, SYMBOL_MAX), rot = (((Number(entry.rot) || 0) % 360) + 360) % 360
    const k = Math.max(SYMBOL_CELLS * size, SYMBOL_MIN_PX * u) / 48
    const a = rot * Math.PI / 180, w = sym.w * k / 2, h = sym.h * k / 2
    return { sym, size, rot, k, hw: Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a)), hh: Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a)) }
  }
  // One mark at a device (centre x/y, radius b): 'battery', 'lock', 'offline', 'error' (extra = 'alarm' lets it blink)
  // or 'count' (extra = the number of devices in a group).
  function markSVG(type, x, y, b, extra) {
    const f = v => Math.round(v * b * 1e4) / 1e4, at = `transform="translate(${r3(x)} ${r3(y)})"`, disc = `<circle r="${f(1)}"/>`
    if (type === 'count') return `<g class="fp-badge count" ${at}>${disc}<text y="${f(0.36)}" font-size="${f(1.05)}" text-anchor="middle">${extra}</text></g>`
    if (type === 'battery') return `<g class="fp-badge battery" ${at}>${disc}<rect x="${f(-0.5)}" y="${f(-0.29)}" width="${f(0.8)}" height="${f(0.58)}" rx="${f(0.1)}"/><rect x="${f(0.32)}" y="${f(-0.14)}" width="${f(0.14)}" height="${f(0.28)}"/></g>`
    if (type === 'lock') return `<g class="fp-badge lock" ${at}><title>${HO.esc(HO.LOCK_HINT || 'Gesperrt')}</title>${disc}<rect x="${f(-0.43)}" y="${f(-0.1)}" width="${f(0.86)}" height="${f(0.57)}" rx="${f(0.1)}"/><path d="M${f(-0.24)} ${f(-0.1)}V${f(-0.29)}a${f(0.24)} ${f(0.24)} 0 0 1 ${f(0.48)} 0V${f(-0.1)}"/></g>`
    if (type === 'offline') return `<g class="fp-badge offline" ${at}>${disc}<path d="M${f(-0.4)} ${f(-0.4)}L${f(0.4)} ${f(0.4)}M${f(0.4)} ${f(-0.4)}L${f(-0.4)} ${f(0.4)}"/></g>`
    return `<g class="fp-badge error ${extra || ''}" ${at}>${disc}<path d="M0 ${f(-0.48)}V${f(0.12)}M0 ${f(0.48)}v.001"/></g>`
  }
  // The marks of one device around its symbol (cx/cy = corner bottom right, b = radius of a mark) – the same places for
  // every symbol: lock top left, no contact or fault bottom right, weak battery top right. At a plan symbol the status
  // dot sits top right (`dot`), the battery moves to the bottom left.
  function deviceMarks(d, cx, cy, b, dot) {
    const fault = d.alarm || (d.errors && d.errors.length)
    return (d.locked ? markSVG('lock', -cx, -cy, b) : '') +
      (HO.lowBattery(d) ? markSVG('battery', dot ? -cx : cx, dot ? cy : -cy, b) : '') +
      (!d.online ? markSVG('offline', cx, cy, b) : fault ? markSVG('error', cx, cy, b, d.alarm ? 'alarm' : '') : '')
  }

  /* Position of the sun (simplified NOAA formulas, accurate to ~1°): az = azimuth from north clockwise, el = elevation.
     Purely local – only needs the approximate place, no query to the internet. */
  function sunPos(date, lat, lon) {
    const rad = Math.PI / 180, d = date.getTime() / 864e5 - 10957.5          // Tage seit J2000.0
    const g = (357.529 + 0.98560028 * d) * rad, q = 280.459 + 0.98564736 * d
    const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * rad, e = (23.439 - 0.00000036 * d) * rad
    const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L))
    const ha = ((18.697374558 + 24.06570982441908 * d) % 24) * 15 * rad + lon * rad - ra, la = lat * rad
    const el = Math.asin(Math.sin(la) * Math.sin(dec) + Math.cos(la) * Math.cos(dec) * Math.cos(ha))
    const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(la) - Math.sin(la) * Math.cos(ha))
    return { az: (az / rad + 360) % 360, el: el / rad }
  }
  HO.sunPos = sunPos
  /* Position and phase of the moon (low-precision formulas, accurate to about 2°): az and el like sunPos, phase = lit
     share of the disc (0 = new moon, 1 = full moon). Purely local as well. */
  function moonPos(date, lat, lon) {
    const rad = Math.PI / 180, d = date.getTime() / 864e5 - 10957.5
    const lng = (218.316 + 13.176396 * d) * rad, anomaly = (134.963 + 13.064993 * d) * rad, node = (93.272 + 13.229350 * d) * rad
    const l = lng + 6.289 * rad * Math.sin(anomaly), b = 5.128 * rad * Math.sin(node), e = 23.439 * rad
    const ra = Math.atan2(Math.sin(l) * Math.cos(e) - Math.tan(b) * Math.sin(e), Math.cos(l))
    const dec = Math.asin(Math.sin(b) * Math.cos(e) + Math.cos(b) * Math.sin(e) * Math.sin(l))
    const ha = ((18.697374558 + 24.06570982441908 * d) % 24) * 15 * rad + lon * rad - ra, la = lat * rad
    const el = Math.asin(Math.sin(la) * Math.sin(dec) + Math.cos(la) * Math.cos(dec) * Math.cos(ha))
    const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(la) - Math.sin(la) * Math.cos(ha))
    // phase from the angle between moon and sun along the ecliptic
    const g = (357.529 + 0.98560028 * d) * rad, sun = (280.459 + 0.98564736 * d + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * rad
    return { az: (az / rad + 360) % 360, el: el / rad, phase: (1 - Math.cos(l - sun)) / 2 }
  }
  HO.moonPos = moonPos

  /* Scale of the heat map – one source for image and legend: only the span of the heating schedule (backend: lowest to
     highest heating value of the active routine, without "off"/frost protection), whole degrees, at least 4 °C wide. What
     the thermostats currently measure or have set does not shift the scale – every room gets the colour that
     corresponds to its temperature on this fixed scale (below without colour, above full). Without a schedule 10–25 °C. */
  function heatScale(range) {
    let lo = range && isFinite(+range[0]) ? +range[0] : 10, hi = range && isFinite(+range[1]) ? +range[1] : 25
    if (lo > hi) { const x = lo; lo = hi; hi = x }
    lo = Math.floor(lo); hi = Math.ceil(hi)
    if (hi - lo < 4) { const c = (lo + hi) / 2; lo = Math.floor(c - 2); hi = Math.ceil(c + 2) }
    return [lo, hi]
  }
  const SHADOW_H = 1.2, SHADOW_MAX = 4.5   // shadow length in grid cells: height / tan(sun elevation), capped
  const SHADOW_STEPS = 32
  // Opacity of the building shadow: cast shadow by day, soft halo at night (toned down since 0.20.1 – the
  // shadow around the walls was too strong)
  const SHADOW_DAY = 0.4, SHADOW_NIGHT = 0.34
  // Himmelsrichtungen in 16 Schritten ab Norden im Uhrzeigersinn (Windpfeil am Grundriss)
  const COMPASS = ['N', 'NNO', 'NO', 'ONO', 'O', 'OSO', 'SO', 'SSO', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']

  const TOOLS = [['select', 'pointer', 'Auswählen'], ['wall', 'wall', 'Wand'], ['door', 'door', 'Tür'],
    ['window', 'contact', 'Fenster'], ['passage', 'passage', 'Durchbruch'], ['radiator', 'radiator', 'Heizkörper'],
    ['deck', 'deck', 'Balkon'], ['stair', 'stairs', 'Treppe']]
  const HINTS = {
    select: 'Antippen zum Bearbeiten. Bei einer ausgewählten Wand: Punkte ziehen (verbundene Wände ziehen mit), Mitte ziehen = Bogen, Mitte doppelt antippen = neuer Punkt, Punkt doppelt antippen = löschen.',
    wall: 'Punkte nacheinander antippen. Ist eine Fläche rundum von Wänden umschlossen, wird sie automatisch ein Raum. Ersten Punkt erneut antippen schließt den Zug; „Fertig" oder Doppeltippen beendet ihn. Shift = 45°-Schritte.',
    door: 'Entlang einer Wand ziehen – rastet ein. Aufschlag-Seite danach umschaltbar.',
    window: 'Entlang einer Wand ziehen – rastet ein. Mit einem Fensterkontakt verknüpfbar.',
    passage: 'Entlang einer Wand ziehen – öffnet die Wand ohne Tür (z. B. offener Durchgang). Die Heatmap fließt hindurch.',
    radiator: 'Entlang einer Wand ziehen – der Heizkörper sitzt auf der Raumseite. Mit einem Thermostat verknüpfbar.',
    stair: 'Von der untersten zur obersten Stufe ziehen. Im Stockwerk darüber erscheint an derselben Stelle der Abgang. In der Ansicht wechselt Antippen der Treppe das Stockwerk.',
    deck: 'Balkon oder Terrasse: Ecken nacheinander antippen, den ersten Punkt erneut antippen schließt die Fläche („Fertig" ebenso). Rastet an Wänden ein. Die Fläche liegt außen – Regen, Schnee und Sonne ziehen darüber hinweg.'
  }
  const OPEN_LABEL = { door: 'Tür', window: 'Fenster', passage: 'Durchbruch', radiator: 'Heizkörper' }
  const HOME_DEFAULT = 'Wohnung', MAX_MEMBERS = 24

  HO.floorplan = {
    computeFaces, assign, heatScale, floorsOf,
    // Is anything drawn on any floor of this stored plan?
    hasWalls: plan => floorsOf(plan).some(f => (f.walls || []).length > 0),
    // The device groups and the rooms of all floors of a stored plan.
    groupsOf: plan => [].concat.apply([], floorsOf(plan).map(f => f.groups || [])),
    roomsOf: plan => [].concat.apply([], floorsOf(plan).map(f => f.rooms || [])),
    // Mount the floor plan into an element. opts: plan, devices, rooms, heat/heatRange, weather, camera (full screen with
    // a free camera; with it `embed` and `still` for the floor plan widget), floor (id of the floor shown first),
    // floorNav (buttons in the frame to change the floor) and callbacks (onSave, onDeviceClick, onRoomClick,
    // onGroupClick, onBackgroundClick, onAssign, onNewRoom, onRoomsRenamed, onFloor). Returns the controller: update,
    // setEditing, setHeat, zoomBy, fit, highlight, ungroup, floors, setFloor, stepFloor, destroy.
    mount(el, opts) {
      const st = {
        plan: normPlan(opts.plan, opts.floor), floorId: null, newFloor: null, devices: opts.devices || [], rooms: opts.rooms || [],
        editing: false, tool: 'select', sel: null, msel: [], armed: null, drag: null, draft: null, ppu: 30, base: 30, zoom: 1, zc: null, lastTap: null,
        heat: opts.heat != null ? !!opts.heat : !!(opts.plan && opts.plan.heatmap), hm: { key: null, url: null },
        heatRange: opts.heatRange || null,
        hl: null, tap: null, focus: null,
        wx: opts.weather || null, fxView: null   // Wettereffekte: fx aus /api/weather (Startseite), Canvas-Ansicht
      }
      // Full screen: infinite surface, centre cx/cy in plan units, s = pixels per unit. The view on the
      // home page keeps st.zoom (multiple of the overall view) and st.zc (centre) instead. `look()` and `aim()`
      // further down read and set both uniformly – gestures and buttons only know this form.
      const cam = opts.camera ? { cx: 0, cy: 0, s: 0, fit: 0, cw: 0, ch: 0 } : null
      st.floorId = st.plan.floor.id
      // Floor plan as a widget (since 0.24): `embed` = the camera view inside a tile (labels sit at its edge, not below a
      // top bar), `still` = the section stays as it is – no panning, no zooming, the page scrolls over it; taps still count.
      const still = !!(cam && opts.still)
      const heatOn = () => st.heat && !st.editing
      const devById = () => Object.fromEntries(st.devices.map(d => [d.id, d]))
      const roomById = () => Object.fromEntries(st.rooms.filter(r => r.room_id).map(r => [r.room_id, r]))
      const undoStack = [], redoStack = []
      let lastSnap = clone(st.plan), snapped = true
      const _flush = HO.debounce(() => { lastSnap = clone(st.plan); snapped = true; opts.onSave && opts.onSave(st.plan) }, 500)
      const save = () => {
        if (snapped) { undoStack.push(lastSnap); if (undoStack.length > 50) undoStack.shift(); redoStack.length = 0; snapped = false }
        _flush()
      }
      // Undo the last step in the editor and save.
      function doUndo() {
        if (!undoStack.length) return
        redoStack.push(clone(st.plan)); openPlan(undoStack.pop())
        lastSnap = clone(st.plan); snapped = true; syncRooms(); st.sel = null; st.msel = []
        if (opts.onSave) opts.onSave(st.plan); render()
      }
      // Redo the undone step and save.
      function doRedo() {
        if (!redoStack.length) return
        undoStack.push(clone(st.plan)); openPlan(redoStack.pop())
        lastSnap = clone(st.plan); snapped = true; syncRooms(); st.sel = null; st.msel = []
        if (opts.onSave) opts.onSave(st.plan); render()
      }
      const listOf = type => type === 'room' ? st.plan.rooms : type === 'wall' ? st.plan.walls : type === 'open' ? st.plan.openings
        : type === 'group' ? st.plan.groups : type === 'deck' ? st.plan.decks : type === 'stair' ? st.plan.stairs : []
      const groupById = id => st.plan.groups.find(g => g.id === id)
      const groupOf = id => st.plan.groups.find(g => g.members.includes(id))
      const posOf = id => st.plan.devices[id] || (g => g && { x: g.x, y: g.y })(groupOf(id))

      /* ---------- Floors: st.plan.floor is the floor shown and edited, st.floorId its id (it survives a fresh plan from
         the server, an undo and a reload of the data). A device stands on exactly one floor: everything that puts a
         device onto this floor calls claim() first, which takes it off every other one – and the store drops a
         device that two floors hold all the same (floorplan_store._clean). */
      // Take over a stored plan (fresh data, a step of the history) and stay on the floor shown – if it still exists.
      function openPlan(stored) {
        st.plan = normPlan(stored, st.floorId)
        st.floorId = st.plan.floor.id
      }
      const levels = () => st.plan.levels
      const floorAtLevel = level => levels().find(f => f.level === level) || null
      const allRooms = () => [].concat.apply([], levels().map(f => f.rooms))
      // the floor that shines through in the editor and whose walls points snap to: the one below, for the lowest
      // floor the one above
      const neighbour = () => floorAtLevel(st.plan.floor.level - 1) || floorAtLevel(st.plan.floor.level + 1)
      // a floor below the ground has no weather and no sun
      const underground = () => st.plan.floor.level < 0
      // Does this floor hold the device – as a position, as a member of a group or docked to an element?
      const holds = (floor, id) => !!floor.devices[id] || floor.groups.some(g => g.members.includes(id)) ||
        floor.openings.some(o => o.device_id === id || (o.extra_ids || []).includes(id))
      const floorOf = id => levels().find(f => holds(f, id)) || null
      const isPlaced = id => !!floorOf(id)
      // Take a device off a floor entirely: its position, its place at an element, its membership of a group (a group
      // with one device left dissolves).
      function removeFrom(floor, id) {
        delete floor.devices[id]
        for (const o of floor.openings) {
          const ids = (o.device_id ? [o.device_id] : []).concat(o.extra_ids || [])
          if (!ids.includes(id)) continue
          const rest = ids.filter(x => x !== id)
          o.device_id = rest[0] || null
          if (rest.length > 1) o.extra_ids = rest.slice(1); else delete o.extra_ids
        }
        for (const g of floor.groups.slice()) {
          if (!g.members.includes(id)) continue
          g.members = g.members.filter(x => x !== id)
          if (g.members.length < 2) {
            for (const m of g.members) floor.devices[m] = { x: g.x, y: g.y }
            floor.groups = floor.groups.filter(x => x !== g)
          }
        }
      }
      // Before a device is put onto the floor shown: take it off every other floor.
      function claim(id) {
        for (const f of levels()) if (f !== st.plan.floor) removeFrom(f, id)
      }
      // Show another floor. Selection, drafts and highlights belong to the floor left behind and are dropped.
      function setFloor(id) {
        const floor = levels().find(f => f.id === id)
        if (!floor || floor === st.plan.floor) return false
        settleLive()
        st.plan.floor = floor; st.floorId = floor.id
        st.sel = null; st.msel = []; st.armed = null; st.draft = null; st.drag = null; st.focus = null; st.hl = null; st.newFloor = null
        tip.classList.add('hidden')
        render()
        if (opts.onFloor) opts.onFloor(floor.id)
        return true
      }
      // One floor up (1) or down (−1), if there is one.
      function stepFloor(direction) {
        const next = floorAtLevel(st.plan.floor.level + (direction > 0 ? 1 : -1))
        return next ? setFloor(next.id) : false
      }
      // Add a floor on top of the highest (`where` = 'above') or below the lowest one. `copy` takes over walls, doors,
      // windows, radiators and decks of the floor shown – with new ids, without devices, without room names and
      // without links to rooms of a hub: the new rooms are rooms of their own. Stairs are not copied: the stairs of the
      // floor below show on the new floor as the stairwell by themselves.
      function addFloor(where, copy) {
        const all = levels(), from = st.plan.floor
        if (all.length >= MAX_FLOORS) return HO.toast(`Mehr als ${MAX_FLOORS} Stockwerke passen nicht in einen Grundriss.`, 'warn')
        if (!HO.can('multi_floorplan')) return HO.upsell({ title: 'Mehrere Stockwerke', message: 'Weitere Stockwerke im Grundriss gehören zu ComfyOS Pro.' })
        const floor = { id: uid('f'), name: '', level: where === 'below' ? all[0].level - 1 : all[all.length - 1].level + 1,
          walls: [], rooms: [], openings: [], devices: {}, groups: [], decks: [], stairs: [] }
        if (copy) {
          floor.walls = from.walls.map((w, i) => ({ id: uid('w') + i, pts: clone(w.pts), bend: clone(w.bend) }))
          floor.openings = from.openings.map((o, i) => ({ id: uid('o') + i, type: o.type, a: [...o.a], b: [...o.b], side: o.side, device_id: null }))
          floor.decks = from.decks.map((d, i) => Object.assign({ id: uid('d') + i, pts: clone(d.pts), bend: clone(d.bend) }, d.surface ? { surface: d.surface } : {}))
        }
        save()                                   // the step before goes into the history
        all.push(floor)
        all.sort((a, b) => a.level - b.level)
        setFloor(floor.id)
        syncRooms(); save(); render()
      }
      // Delete the floor shown, after a confirmation. Its devices are unplaced again. If it was the floor with level
      // 0, the one above it (otherwise the one below) takes that place.
      async function deleteFloor() {
        const floor = st.plan.floor
        if (levels().length < 2) return
        const sure = await HO.confirm(`„${floorLabel(floor)}“ mit allen Wänden und Räumen löschen? Die Geräte dieses Stockwerks sind danach wieder unplatziert.`,
          { title: 'Stockwerk löschen?', ok: 'Löschen', danger: true })
        if (!sure || st.plan.floor !== floor || levels().length < 2) return
        save()
        const next = floorAtLevel(floor.level - 1) || floorAtLevel(floor.level + 1)
        st.plan.levels = levels().filter(f => f !== floor)
        const ground = floor.level === 0 ? floorAtLevel(1) || floorAtLevel(-1) : floorAtLevel(0)
        relevel(st.plan.levels, ground)
        st.plan.floor = next; st.floorId = next.id
        st.sel = null; st.msel = []; st.armed = null; st.draft = null; st.newFloor = null
        save(); render()
        if (opts.onFloor) opts.onFloor(next.id)
      }
      // Move a device of the floor shown to another floor: it stands free at the same place there.
      function moveToFloor(id, floorId) {
        const target = levels().find(f => f.id === floorId), here = st.plan.floor, pos = posOf(id)
        if (!target || target === here || !pos) return
        const entry = { x: pos.x, y: pos.y }, stored = here.devices[id] || {}
        for (const key of ['furniture', 'size', 'rot']) if (stored[key] != null) entry[key] = stored[key]
        for (const f of levels()) removeFrom(f, id)
        target.devices[id] = entry
        st.sel = null
        save(); render()
        HO.toast(`Gerät steht jetzt im Stockwerk „${floorLabel(target)}“.`)
      }
      const selItem = () => st.sel ? listOf(st.sel.type).find(x => x.id === st.sel.id) : null
      // multi-selection (st.msel = [{type, id}]): drag a frame, Shift/Ctrl+click or "Alles"
      const isSel = (type, id) => (st.sel && st.sel.type === type && st.sel.id === id) || st.msel.some(x => x.type === type && x.id === id)
      let faceCache = { key: null, faces: [] }
      // The areas of the plan – recomputed only if the walls have changed.
      const faces = () => {
        const key = JSON.stringify(st.plan.walls)
        if (faceCache.key !== key) faceCache = { key, faces: computeFaces(st.plan.walls) }
        return faceCache.faces
      }
      const layout = () => assign(faces(), st.plan.rooms)
      const roomKey = r => r.room_id || 'plan:' + r.id
      // room (floor plan) at a place of the plan
      const roomAt = p => { const hit = layout().find(({ face, room }) => room && inside(face.poly, p)); return hit ? hit.room : null }
      // device dropped on the plan: if it lies in another room, it is assigned to that one
      /* Docking: contacts and covers (roller shutters, blinds) belong to windows and doors, thermostats to radiators.
         Several devices can hang on one element (since 0.24.3, at most MAX_DOCKED) – a window with its contact and its
         shutter, a radiator with two thermostats. Stored: `device_id` = the first one (as before), `extra_ids` = the
         further ones. A device hangs on at most one element. */
      const isContact = d => !!d && d.kind !== 'cover' && (d.kind === 'contact' || d.open != null)
      const dockTypes = d => !d ? [] : d.kind === 'thermostat' ? ['radiator'] : (d.kind === 'cover' || isContact(d)) ? ['window', 'door'] : []
      const dockedIds = o => (o.device_id ? [o.device_id] : []).concat(o.extra_ids || [])
      const setDocked = (o, ids) => { o.device_id = ids[0] || null; if (ids.length > 1) o.extra_ids = ids.slice(1); else delete o.extra_ids }
      const dockedTo = id => st.plan.openings.find(o => dockedIds(o).includes(id))
      // Detach a device from its element (`except` = the element it should stay on).
      const undock = (id, except) => { for (const o of st.plan.openings) if (o !== except && dockedIds(o).includes(id)) setDocked(o, dockedIds(o).filter(x => x !== id)) }
      // The docked devices of an element that the home knows, as device rows.
      const dockedDevices = (o, db) => dockedIds(o).map(id => db[id]).filter(Boolean)
      // room side of a window from the geometry (independent of drawing direction and stored side):
      // 1/-1 = the room lies left/right of the edge a→b, null = both sides/no room (interior window) -> the stored side applies
      function roomSideOf(o) {
        const L = dist(o.a, o.b) || 1, m = midOf(o.a, o.b), n = [-(o.b[1] - o.a[1]) / L, (o.b[0] - o.a[0]) / L]
        const fs = faces(), at = k => fs.some(f => inside(f.poly, [m[0] + n[0] * k, m[1] + n[1] * k]))
        const pos = at(0.4), neg = at(-0.4)
        return pos === neg ? null : pos ? 1 : -1
      }
      // side an element points "inwards" to: windows always open into the room, otherwise the chosen side
      const baseSide = o => (o.type === 'window' ? roomSideOf(o) : null) || o.side
      // t = Position entlang des Elements (0…1), off = Abstand ins Rauminnere
      function dockPoint(o, off = 0.55, t = 0.5) {
        const sd = baseSide(o), L = dist(o.a, o.b) || 1, n = [-(o.b[1] - o.a[1]) / L * sd, (o.b[0] - o.a[0]) / L * sd]
        const m = [o.a[0] + (o.b[0] - o.a[0]) * t, o.a[1] + (o.b[1] - o.a[1]) * t]
        return [r3(m[0] + n[0] * off), r3(m[1] + n[1] * off)]
      }
      // Fitting opening (window/door for contacts, radiator for thermostats) within reach of the point – what the device
      // would dock to.
      function nearestDock(d, p, keep = null) {
        const types = dockTypes(d)
        let best = null
        for (const o of st.plan.openings) {
          if (!types.includes(o.type) || (dockedIds(o).length >= MAX_DOCKED && !dockedIds(o).includes(d.id))) continue
          // docked holds on a little longer (no flutter at the border); dragging clearly away detaches the device
          const pr = project(p, o.a, o.b), lim = o === keep ? 1.15 : 0.9
          if (pr.d < lim && pr.t > -0.1 && pr.t < 1.1 && (!best || pr.d < best.d)) best = { o, d: pr.d }
        }
        return best
      }
      // Docking: the device hangs on the element and appears there as a dot in the middle (view and editor alike).
      // Stored is a place next to the element in the room (for the room assignment); a position along the
      // element or a side no longer exists since 0.19 (old values `t`/`s` in stored plans do no harm).
      function dock(id, o) {
        undock(id, o)
        if (o) {
          if (!dockedIds(o).includes(id)) setDocked(o, dockedIds(o).concat(id))
          const q = dockPoint(o, 0.55 * (o.type === 'radiator' ? 1 : -1), 0.5)
          const kept = Object.assign({}, st.plan.devices[id] || {})
          delete kept.t; delete kept.s
          st.plan.devices[id] = Object.assign(kept, { x: q[0], y: q[1] })
        }
      }
      // positions of all docked devices: the dots of one element lie side by side, centred on it; overlaps between
      // elements (radiator below a window) are pushed apart
      function dockLayout(db, R, u) {
        const list = []
        for (const o of st.plan.openings) {
          const ids = dockedIds(o).filter(id => db[id] && st.plan.devices[id])
          if (!ids.length) continue
          const L = dist(o.a, o.b) || 1, dir = [(o.b[0] - o.a[0]) / L, (o.b[1] - o.a[1]) / L]
          // view and editor alike: radiator dot in front of the body in the room (distance as if a window contact sat
          // behind it); window/door at the outer wall: dot outside in front of the element; interior ones: centred in the opening
          const bs = baseSide(o), r = R * DOT, mid = midOf(o.a, o.b), step = 2 * r + 4 * u
          let n = [-dir[1] * bs, dir[0] * bs], off = 0
          if (o.type === 'radiator') off = Math.max(4 * u + Math.max(0.2, 7 * u) / 2, 2 * r + 3 * u)
          else {
            const rs = roomSideOf(o)   // a room on only one side = outer wall
            if (rs) { n = [dir[1] * rs, -dir[0] * rs]; off = r + 6 * u }
          }
          ids.forEach((id, i) => {
            const along = (i - (ids.length - 1) / 2) * step, m = [mid[0] + dir[0] * along, mid[1] + dir[1] * along]
            // grab area: generous for a single dot, half the distance to the neighbour for several
            list.push({ id, r, n, m, off, hit: ids.length > 1 ? step / 2 : 0, p: [m[0] + n[0] * off, m[1] + n[1] * off] })
          })
        }
        for (let pass = 0; pass < 6; pass++) {
          let moved = false
          for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
            const A = list[i], B = list[j], need = A.r + B.r + 2 * u, dd = dist(A.p, B.p)
            if (dd >= need) continue
            const F = A.off > B.off ? A : B   // push the one lying further out further out
            F.off += need - dd + u; F.p = [F.m[0] + F.n[0] * F.off, F.m[1] + F.n[1] * F.off]; moved = true
          }
          if (!moved) break
        }
        return new Map(list.map(e => [e.id, e]))
      }
      // Put a device on the plan (tap after selecting or by drag & drop from the list); docks if close enough
      function placeDevice(id, p) {
        claim(id)
        const nd = nearestDock(devById()[id], p), mt = !nd && mergeTarget(id, p)
        st.plan.devices[id] = { x: r3(p[0]), y: r3(p[1]) }
        if (nd) dock(id, nd.o)
        else if (mt) return mergeInto(id, mt)
        st.sel = { type: 'dev', id }
        save(); render(); assignByPosition(id)
      }
      // Assign the device to the room it now lies in (reports it through onAssign if the room changes).
      function assignByPosition(id) {
        const d = devById()[id], pos = posOf(id), r = pos && roomAt([pos.x, pos.y])
        if (!d || !r || !r.room_id || roomKey(r) === d.room_id || !opts.onAssign) return
        opts.onAssign(d, roomKey(r), roomLabel(r))
      }

      /* ---------- Groups (like folders): drag a device onto a device or a group */
      function mergeTarget(id, p) {
        const R = clamp(st.ppu * 0.6, 8, 15) / st.ppu, db = devById()
        let best = null
        for (const g of st.plan.groups) { const dd = dist(p, [g.x, g.y]); if (dd < R * 1.6 && (!best || dd < best.d)) best = { type: 'group', id: g.id, d: dd } }
        for (const [oid, q] of Object.entries(st.plan.devices)) {
          if (oid === id || !db[oid] || dockedTo(oid)) continue
          const dd = dist(p, [q.x, q.y]); if (dd < R * 1.2 && (!best || dd < best.d)) best = { type: 'dev', id: oid, d: dd }
        }
        return best
      }
      // Device dragged onto another device or a group: both form a group or the device joins it (at most
      // MAX_MEMBERS).
      function mergeInto(id, tgt) {
        let g = tgt.type === 'group' ? groupById(tgt.id) : null
        if (g && g.members.length >= MAX_MEMBERS) { HO.toast(`Höchstens ${MAX_MEMBERS} Geräte je Gruppe.`, 'warn'); save(); return render() }
        undock(id)
        if (g) { if (!g.members.includes(id)) g.members.push(id) }
        else {
          const q = st.plan.devices[tgt.id]
          g = { id: uid('g'), name: '', x: q.x, y: q.y, members: [tgt.id, id] }
          delete st.plan.devices[tgt.id]
          st.plan.groups.push(g)
        }
        delete st.plan.devices[id]
        st.sel = { type: 'group', id: g.id }
        save(); render(); assignByPosition(id)
      }
      // Detach a device from the group and place it singly to the right; if only one is left, the group dissolves
      function ungroup(gid, id) {
        const g = groupById(gid)
        if (!g || !g.members.includes(id)) return
        g.members = g.members.filter(x => x !== id)
        st.plan.devices[id] = { x: r3(clamp(g.x + 1.1, 0, st.plan.width)), y: r3(g.y) }
        if (g.members.length < 2) {
          for (const m of g.members) st.plan.devices[m] = { x: g.x, y: g.y }
          st.plan.groups = st.plan.groups.filter(x => x !== g)
          if (st.sel && st.sel.type === 'group' && st.sel.id === gid) st.sel = null
        }
      }
      // Dissolve a group: its devices then stand singly in a circle around the old place.
      function dissolve(gid) {
        const g = groupById(gid)
        if (!g) return
        g.members.forEach((id, i) => {
          const a = i * 2 * Math.PI / g.members.length
          st.plan.devices[id] = { x: r3(clamp(g.x + Math.cos(a) * 0.9, 0, st.plan.width)), y: r3(clamp(g.y + Math.sin(a) * 0.9, 0, st.plan.height)) }
        })
        st.plan.groups = st.plan.groups.filter(x => x !== g)
        st.sel = null
      }
      // Size of a plan symbol (1 = standard, in steps of 5 %); the standard size is not stored.
      function setSymbolSize(entry, size) {
        const v = clamp(Math.round(size * 20) / 20, SYMBOL_MIN, SYMBOL_MAX)
        if (v === 1) delete entry.size; else entry.size = v
      }
      // Collective state of a group: the most conspicuous wins (motion, heating, on, open)
      const groupClass = ms => {
        const c = ms.map(HO.devClass)
        return c.includes('motion live') ? 'motion live' : c.includes('heat') ? 'heat' : c.includes('on') ? 'on' : c.includes('open') ? 'open'
          : c.includes('motion') ? 'motion' : c.every(x => x === 'off') ? 'off' : ''
      }

      // after every wall change: new areas get a room, vanished rooms drop out
      function syncRooms() {
        const out = [], names = new Set(allRooms().map(r => r.name))
        let n = 1
        for (const { face, room } of layout()) {
          let r = room
          if (!r) {
            while (names.has('Raum ' + n)) n++
            r = { id: uid('r'), name: 'Raum ' + n, room_id: null }; names.add(r.name)
          }
          r.x = r3(face.label[0]); r.y = r3(face.label[1])
          out.push(r)
        }
        st.plan.rooms = out
        if (st.sel && st.sel.type === 'room' && !out.some(r => r.id === st.sel.id)) st.sel = null
      }
      // After a change to the walls: reconcile rooms, save, draw – and report newly created rooms for
      // naming.
      const changed = () => {
        const before = new Set(st.plan.rooms.map(r => r.id))
        syncRooms(); save(); render()
        const created = st.plan.rooms.filter(r => !before.has(r.id))
        if (created.length && opts.onNewRoom) created.forEach(r => opts.onNewRoom(r, st.rooms))
      }

      el.innerHTML = `<div class="fp-tools hidden"></div>
        <div class="fp-wrap view${cam ? ' cam' : ''}${cam && opts.embed ? ' embed' : ''}${still ? ' still' : ''}"><svg class="fp-svg" xmlns="http://www.w3.org/2000/svg"></svg><div class="fp-tip hidden"></div>
          <div class="fp-title hidden" aria-live="polite"></div>
          <div class="fp-wind hidden" role="img"></div>
          <div class="fp-floornav hidden"></div>
          <div class="fp-legend hidden"><span class="fp-lo"></span><i></i><span class="fp-hi"></span></div></div>
        <div class="fp-tray hidden"></div>
        <div class="fp-side hidden"></div>`
      const tools = el.querySelector('.fp-tools'), wrap = el.querySelector('.fp-wrap'),
        svg = el.querySelector('svg'), tip = el.querySelector('.fp-tip'), side = el.querySelector('.fp-side'),
        trayBox = el.querySelector('.fp-tray'), floorNav = el.querySelector('.fp-floornav')
      // the buttons of the floor change are no part of the plan: neither a tap on the plan nor the start of a gesture
      floorNav.addEventListener('pointerdown', e => e.stopPropagation())
      floorNav.addEventListener('click', e => {
        e.stopPropagation()
        const button = e.target.closest('[data-floorstep]')
        if (button && !button.disabled) stepFloor(+button.dataset.floorstep)
      })

      /* ---------- Display */
      // Write an attribute only if it changes – even writing the same value can trigger a repaint
      const setAttr = (node, name, value) => { if (node.getAttribute(name) !== value) node.setAttribute(name, value) }
      let screenBasis = { w: 0, h: 0 }
      // Screen height the view is sized by. On the phone it keeps changing a little while scrolling (the
      // address bar folds in and out) – if the floor plan followed that, it would grow and shrink in the middle of scrolling,
      // the page would jerk and the weather effects would start over. Small changes at the same width do not count there.
      function screenHeight() {
        const w = window.innerWidth, h = window.innerHeight, slack = COARSE ? 0.2 * screenBasis.h : 0
        if (w !== screenBasis.w || Math.abs(h - screenBasis.h) > slack) screenBasis = { w, h }
        return screenBasis.h
      }
      // Set the size of the drawing surface. View: as wide as the frame, at most a good half of the screen height; the
      // zoom only changes the section. Full screen: see sizeCam.
      function size() {
        const { width: W, height: H } = st.plan
        if (cam) return sizeCam()
        wrap.classList.toggle('view', !st.editing)   // before measuring: in the view the frame reaches the edge of the tile (CSS)
        const cw = Math.max(260, wrap.clientWidth || el.clientWidth || 600)
        const maxH = screenHeight() * (st.editing ? 0.62 : 0.56)
        // zoom only changes the section (viewBox), the surface on the page stays the same size
        const base = Math.max(10, Math.min(cw / W, maxH / H))
        st.base = base
        st.ppu = base * st.zoom
        // View (since 0.19.1): the frame always fills the whole width. If the plan is narrower (the height limits it),
        // it stays the same size in the middle and the section gets wider left and right (viewW in plan units) –
        // more room around the house. In the editor the frame still is exactly the plan.
        st.viewW = st.editing ? W : Math.max(W, cw / base)
        setAttr(svg, 'width', (st.viewW * base).toFixed(0))
        setAttr(svg, 'height', (H * base).toFixed(0))
        wrap.classList.toggle('zoomed', st.zoom > 1.001 && !st.editing)
        applyZoomBox()
      }
      // view: limit the centre so that the section stays in the plan (if it shows more than the plan, it stays centred)
      function clampCentre(c) {
        const { width: W, height: H } = st.plan, vw = (st.viewW || W) / st.zoom, vh = H / st.zoom
        const lim = (v, size, w) => w >= size ? size / 2 : clamp(v, w / 2, size - w / 2)
        return [lim(c[0], W, vw), lim(c[1], H, vh)]
      }
      // Write the section of the view as the viewBox (zoom and centre, limited to the edge of the plan).
      function applyZoomBox() {
        const { width: W, height: H } = st.plan, vw = (st.viewW || W) / st.zoom, vh = H / st.zoom
        st.zc = clampCentre(st.zc || [W / 2, H / 2])
        setAttr(svg, 'viewBox', `${r3(st.zc[0] - vw / 2)} ${r3(st.zc[1] - vh / 2)} ${r3(vw)} ${r3(vh)}`)
      }

      /* ---------- Camera: zoom 0.8× to 8× of the overall view; at most a quarter of the screen may be empty next to the plan */
      function sizeCam() {
        const { width: W, height: H } = st.plan
        cam.cw = Math.max(200, wrap.clientWidth || window.innerWidth)
        cam.ch = Math.max(200, wrap.clientHeight || window.innerHeight)
        const fit = Math.min(cam.cw / W, cam.ch / H) * 0.9
        if (!cam.s) { cam.s = fit; cam.cx = W / 2; cam.cy = H / 2 }
        else if (cam.fit && Math.abs(fit - cam.fit) > 1e-6) cam.s *= fit / cam.fit   // rotating/resizing: keep the zoom level
        cam.fit = fit
        clampCam()
        st.ppu = cam.s
        setAttr(svg, 'width', '100%'); setAttr(svg, 'height', '100%')
        applyBox()
      }
      // Limit the camera of the full screen: zoom 0.8 to 8 times the overall view, at most a quarter of the screen empty
      // next to the plan.
      function clampCam() {
        const { width: W, height: H } = st.plan
        cam.s = clamp(cam.s, cam.fit * 0.8, cam.fit * 8)
        const lim = (c, size, v) => { const pad = v * 0.25, lo = v / 2 - pad, hi = size - v / 2 + pad; return lo > hi ? size / 2 : clamp(c, lo, hi) }
        cam.cx = lim(cam.cx, W, cam.cw / cam.s); cam.cy = lim(cam.cy, H, cam.ch / cam.s)
      }
      // the dot grid of the infinite surface (full screen) moves with the camera
      function moveDots() {
        const x = cam.cx - cam.cw / cam.s / 2, y = cam.cy - cam.ch / cam.s / 2
        const dots = `${cam.s}px ${cam.s}px|${(-x * cam.s).toFixed(1)}px ${(-y * cam.s).toFixed(1)}px`
        if (dots === moveDots.last) return
        moveDots.last = dots
        wrap.style.backgroundSize = dots.split('|')[0]
        wrap.style.backgroundPosition = dots.split('|')[1]
      }
      // Write the section of the full screen as the viewBox; dot grid and resting weather image follow.
      function applyBox() {
        const vw = cam.cw / cam.s, vh = cam.ch / cam.s, x = cam.cx - vw / 2, y = cam.cy - vh / 2
        setAttr(svg, 'viewBox', `${x.toFixed(3)} ${y.toFixed(3)} ${vw.toFixed(3)} ${vh.toFixed(3)}`)
        moveDots()
        if (st.fxView) st.fxView.redraw()   // ruhendes Wetterbild folgt der Kamera (laufende Animation ohnehin)
      }

      /* ---------- Tick of the animations in the SVG (the cold-air pulse at open windows)
         A running SVG animation makes the browser repaint the image 60 times per second – with the blurs
         of the shadows each of these frames costs as much as a complete rebuild. But the pulse only breathes once
         every three seconds: PULSE_FPS frames per second look just the same. Therefore the clock of the SVG is paused and
         a tick of our own advances it. During a gesture and with a hidden tab the pulse stands still entirely. */
      const PULSE_FPS = HO.perf.low ? 4 : 8
      const pulse = {
        timer: 0,
        step() { if (!live && !document.hidden) { try { svg.setCurrentTime(performance.now() / 1000) } catch (err) { } } },
        // After a rebuild: if there are animations in the SVG, pause their clock and start the own tick – otherwise end
        // the tick.
        sync() {
          const needed = !!svg.querySelector('animate') && typeof svg.pauseAnimations === 'function'
          if (needed) { try { svg.pauseAnimations() } catch (err) { } }
          if (needed && !this.timer) this.timer = setInterval(() => this.step(), 1000 / PULSE_FPS)
          else if (!needed && this.timer) this.stop()
        },
        stop() { clearInterval(this.timer); this.timer = 0 }
      }

      /* ---------- One camera for view and full screen (maths and gestures: floorplan_camera.js)
         look() reads it as {cx, cy, s, cw, ch}, aim() sets it with the limits of the respective view.
         During a gesture (live) the painted image stays and is only moved and scaled; only
         settleLive() draws the new section. That saves a complete repaint of the SVG per movement. */
      const look = () => cam ? { cx: cam.cx, cy: cam.cy, s: cam.s, cw: cam.cw, ch: cam.ch }
        : { cx: st.zc[0], cy: st.zc[1], s: st.ppu, cw: st.viewW * st.base, ch: st.plan.height * st.base }
      const limitScale = s => cam ? clamp(s, cam.fit * 0.8, cam.fit * 8) : clamp(s, st.base * 0.5, st.base * 4)
      // Set the camera, with the limits of the respective view – without drawing yet.
      function aim(next) {
        if (cam) { cam.s = limitScale(next.s); cam.cx = next.cx; cam.cy = next.cy; clampCam(); st.ppu = cam.s; return }
        st.zoom = limitScale(next.s) / st.base
        st.ppu = st.base * st.zoom
        st.zc = clampCentre([next.cx, next.cy])
      }
      const LIVE_RANGE = [0.5, 2]   // outside this scale a redraw happens once in the middle of the gesture too
      let live = null, liveFrame = 0, svgOffset = [0, 0]
      const fxCanvas = () => wrap.querySelector('canvas.fp-fx')
      // Pointer position on the drawing surface. The position of the SVG in the frame is only measured at rest – during
      // the gesture it carries a transform.
      function pointerAt(e) {
        const box = wrap.getBoundingClientRect()
        if (!live) { const sb = svg.getBoundingClientRect(); svgOffset = [sb.left - box.left, sb.top - box.top] }
        return [e.clientX - box.left - svgOffset[0], e.clientY - box.top - svgOffset[1]]
      }
      // A gesture begins: remember the painted state; from now on it is only moved and scaled.
      function beginLive() {
        if (live) return
        live = { from: look(), dirty: false, mask: null, shift: null, fxStays: false }
        wrap.classList.add('fp-moving')
        tip.classList.add('hidden')
        pinFx()
      }
      /* Fix the weather image to the state painted just now: its building outline (live.mask) holds until it is redrawn.
         The weather is attached to the plot and moves along with every movement – on fast devices the canvas stays
         in place for that and keeps drawing (fxStays, it gets the offset live.shift); on economical ones the image stands
         still and is moved like the SVG. */
      function pinFx() {
        live.mask = null; live.shift = null
        live.mask = fxMask()
        live.fxStays = st.fxView ? st.fxView.gesture(true) : false
      }
      // Remove the transform of the gesture from plan and weather image.
      function clearTransforms() {
        svg.style.transform = ''
        const cv = fxCanvas()
        if (cv) cv.style.transform = ''
      }
      // One frame of the gesture: put the painted SVG and the weather image by a transform where the camera is now.
      function drawLive() {
        liveFrame = 0
        if (!live) return
        const now = look(), t = HO.planCamera.between(live.from, now)
        if (t.k < LIVE_RANGE[0] || t.k > LIVE_RANGE[1]) {   // has become too blurry or too small: redraw and continue
          clearTransforms()
          live.from = now
          live.dirty = false
          live.mask = null; live.shift = null
          renderSVG(true)
          pinFx()
          return
        }
        svg.style.transform = `translate(${t.tx.toFixed(1)}px, ${t.ty.toFixed(1)}px) scale(${t.k.toFixed(4)})`
        // the weather image covers the whole frame, the SVG can lie offset in it – hold on to the same point
        live.shift = { k: t.k, x: t.tx + svgOffset[0] * (1 - t.k), y: t.ty + svgOffset[1] * (1 - t.k) }
        if (live.fxStays && st.fxView) st.fxView.follow()      // the weather moves with the plan in the same frame
        else {
          const cv = fxCanvas()
          if (cv) cv.style.transform = `translate(${live.shift.x.toFixed(1)}px, ${live.shift.y.toFixed(1)}px) scale(${t.k.toFixed(4)})`
        }
        if (cam) moveDots()
      }
      function moveLive(next) {
        aim(next)
        if (live && !liveFrame) liveFrame = requestAnimationFrame(drawLive)
      }
      // The gesture is over: remove the transform and draw the new section properly once.
      function settleLive() {
        if (!live) return
        if (liveFrame) { cancelAnimationFrame(liveFrame); liveFrame = 0 }
        const was = live
        live = null
        clearTransforms()
        wrap.classList.remove('fp-moving')
        // only moved: the section is enough; zoomed (or new data meanwhile): size everything anew
        if (was.dirty || Math.abs(look().s / was.from.s - 1) > 0.001) renderSVG()
        else if (cam) applyBox()
        else applyZoomBox()
        if (st.fxView) st.fxView.gesture(false)   // only now: the new section stands, the weather image reads it fresh
      }
      /* ---------- Title top left: the home with its average, on pointing/tapping the room with its temperature */
      const tC = t => HO.temp(t) + 'C'
      // Bubble top left: the home with its average temperature, on pointing or tapping the room with its
      // temperature.
      function renderTitle() {
        const box = el.querySelector('.fp-title')
        box.classList.toggle('hidden', st.editing || !st.plan.walls.length)
        if (st.editing) return
        if (!cam) { box.style.left = (svg.offsetLeft + 10) + 'px'; box.style.top = (svg.offsetTop + 10) + 'px' }
        const rb = roomById(), tempOf = r => { const i = rb[roomKey(r)]; return i && i.temperature != null ? i.temperature : null }
        const r = st.focus && st.plan.rooms.find(x => x.id === st.focus)
        let name, t
        if (r) { name = roomLabel(r); t = tempOf(r) }
        else {
          const ts = st.plan.rooms.map(tempOf).filter(v => v != null)
          // with several floors the title names the floor shown, the average is the one of its rooms
          name = (st.plan.home_name || HOME_DEFAULT) + (levels().length > 1 ? ' · ' + floorLabel(st.plan.floor) : '')
          t = ts.length ? ts.reduce((a, b) => a + b, 0) / ts.length : null
        }
        box.classList.toggle('room', !!r)
        box.innerHTML = `<b>${HO.esc(name)}${t != null ? ':' : ''}</b>${t != null ? `<span>${r ? '' : 'Ø '}${tC(t)}</span>` : ''}`
      }
      // Highlighted room (pointed at or tapped): as a class on the finished SVG, not in the markup – otherwise the
      // image would count as changed on every change and would be rebuilt
      function markRooms() {
        svg.querySelectorAll('.fp-room').forEach(g => {
          const id = g.dataset.id
          g.classList.toggle('hl', !st.editing && !!id && (id === st.hl || id === st.focus))
        })
      }
      // Highlight a room (pointing with the mouse, tapping) or lift the highlight.
      function setFocus(id) {
        id = id || null
        if (st.focus === id) return
        st.focus = id
        markRooms()
        renderTitle()
      }
      function roomLabel(r) {
        const linked = r && r.room_id && roomById()[r.room_id]
        return linked ? linked.name : r ? r.name : ''
      }

      // Area of a room as a hit area; the temperature is shown by the heat map, not the fill.
      function roomSVG({ face, room }, rb, u) {
        const info = room ? rb[roomKey(room)] : null, t = info ? info.temperature : null
        const fill = 'rgba(255,255,255,.035)'   // the temperature is shown by the heat map, not the room area
        const d = 'M' + face.poly.map(p => p.join(' ')).join('L') + 'Z'
        const sel = room && isSel('room', room.id)
        // the highlight (pointing, tapping) is set by markRooms() – it is deliberately not in the markup, see renderSVG
        return `<g class="fp-room ${sel ? 'sel' : ''}" data-kind="room" data-id="${HO.esc(room ? room.id : '')}"><path class="body" d="${d}" fill="${fill}"/></g>`
      }
      // Label of a room in the editor: name and – if there is room – temperature, in a size fitting the scale.
      function roomTextSVG({ face, room }, rb, u) {
        const info = room ? rb[roomKey(room)] : null, t = info ? info.temperature : null
        const [cx, cy] = face.label, room_w = chord(face.poly, face.label)
        // the font grows with the scale (small screen = daintier) but stays legible
        const fpx = clamp(st.ppu * 0.42, 8.5, 11.5), tpx = clamp(st.ppu * 0.48, 9.5, 13.5), fs = fpx * u, tfs = tpx * u
        const showTemp = t != null && edgeDist(face.poly, face.label) * st.ppu > 18
        let name = roomLabel(room) || ''
        const chars = Math.max(3, Math.floor((room_w * st.ppu - 8) / (fpx * 0.6)))
        if (name.length > chars) name = name.slice(0, chars - 1) + '…'
        const y0 = showTemp ? cy - tfs * 0.15 : cy + fs * 0.35
        return `<g class="fp-rlabel"><text class="rn" x="${cx}" y="${y0}" font-size="${fs}" text-anchor="middle">${HO.esc(name)}</text>
          ${showTemp ? `<text class="rt" x="${cx}" y="${y0 + tfs * 1.1}" font-size="${tfs}" text-anchor="middle">Ø ${HO.temp(t)}</text>` : ''}</g>`
      }

      // Draw a window, door or radiator – with the state of the docked device (open/closed, heating).
      function openingSVG(o, u, db, extra = '') {
        const a = o.a, b = o.b, L = dist(a, b)
        if (L < 0.05) return ''
        const sd = baseSide(o), d = [(b[0] - a[0]) / L, (b[1] - a[1]) / L], n = [-d[1] * sd, d[0] * sd]
        const docked = dockedDevices(o, db), contacts = docked.filter(isContact), heating = docked.filter(x => HO.devClass(x) === 'heat')
        const sel = !!o.id && isSel('open', o.id)
        const ln = (p, q, cls) => `<line class="${cls}" x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}"/>`
        // with a contact: open only if reachable and reported open – otherwise (also without contact to the device) draw it closed
        // (several contacts: one open one is enough; several thermostats: one heating one is enough – the strongest counts)
        const contact = o.type !== 'radiator' && contacts.length ? (contacts.some(x => x.online && x.open) ? 'open' : 'closed') : null
        const isHeat = o.type === 'radiator' && heating.length > 0
        const heatIntensity = isHeat ? Math.min(1, Math.max(0.2, Math.max.apply(null, heating.map(x => (x.target || 0) - (x.temperature || 0))) / 5)) : 0
        const state = o.type === 'radiator' ? (isHeat ? 'is-heat' : '') : contact === 'open' ? 'is-open' : contact === 'closed' ? 'is-closed' : ''
        let g = `<g class="fp-open ${o.type} ${state} ${sel ? 'sel' : ''} ${extra}" data-kind="open" data-id="${HO.esc(o.id || '')}" ${isHeat ? `style="--heat:${heatIntensity.toFixed(2)}"` : ''}>`
        if (o.type === 'radiator') {
          // schematic: flat box with fins, on the room side in front of the wall
          const g0 = 4 * u, th = Math.max(0.2, 7 * u), at = k => [n[0] * k, n[1] * k]
          const P = (p, k) => [p[0] + at(k)[0], p[1] + at(k)[1]]
          const c = [P(a, g0), P(b, g0), P(b, g0 + th), P(a, g0 + th)]
          g += `<path class="rad" d="M${c.map(p => p.join(' ')).join('L')}Z"/>`
          const fins = Math.max(2, Math.round(L / 0.25))
          for (let k = 1; k < fins; k++) { const q = [a[0] + d[0] * L * k / fins, a[1] + d[1] * L * k / fins]; g += ln(P(q, g0), P(q, g0 + th), 'fin') }
          return g + ln(P(a, g0 + th / 2), P(b, g0 + th / 2), 'hit') + '</g>'
        }
        g += ln(a, b, 'cut')
        if (o.type === 'passage') {
          // Durchbruch: Wand offen, kurze Laibungsstriche an beiden Enden
          const t = 3.2 * u
          for (const e of [a, b]) g += ln([e[0] + n[0] * t, e[1] + n[1] * t], [e[0] - n[0] * t, e[1] - n[1] * t], 'jamb')
          return g + ln(a, b, 'hit') + '</g>'
        }
        const cr = (p, q) => (p[0] - a[0]) * (q[1] - a[1]) - (p[1] - a[1]) * (q[0] - a[0])
        if (o.type === 'window') {
          const off = 2.4 * u, o1 = [n[0] * off, n[1] * off]
          g += ln([a[0] + o1[0], a[1] + o1[1]], [b[0] + o1[0], b[1] + o1[1]], 'win') + ln([a[0] - o1[0], a[1] - o1[1]], [b[0] - o1[0], b[1] - o1[1]], 'win')
          g += ln([a[0] + o1[0], a[1] + o1[1]], [a[0] - o1[0], a[1] - o1[1]], 'win') + ln([b[0] + o1[0], b[1] + o1[1]], [b[0] - o1[0], b[1] - o1[1]], 'win')
          if (contact === 'open') {
            // sash opened into the room (about 45°) with a swing arc
            const S = L * 0.75, ang = Math.PI / 4
            const P = [a[0] + (d[0] * Math.cos(ang) + n[0] * Math.sin(ang)) * S, a[1] + (d[1] * Math.cos(ang) + n[1] * Math.sin(ang)) * S]
            const E = [a[0] + d[0] * S, a[1] + d[1] * S]
            g += ln(a, P, 'sash') + `<path class="swing" d="M${P[0]} ${P[1]}A${S} ${S} 0 0 ${cr(P, E) > 0 ? 1 : 0} ${E[0]} ${E[1]}"/>`
          }
        } else if (contact === 'closed') {
          // door closed: the leaf lies in the opening
          const off = 1.6 * u
          g += ln([a[0] + n[0] * off, a[1] + n[1] * off], [b[0] + n[0] * off, b[1] + n[1] * off], 'leaf')
        } else {
          // open (the contact reports open) or without a contact: classic door symbol with a swing arc
          const P = [a[0] + n[0] * L, a[1] + n[1] * L]
          g += ln(a, P, 'leaf') + `<path class="swing" d="M${P[0]} ${P[1]}A${L} ${L} 0 0 ${cr(P, b) > 0 ? 1 : 0} ${b[0]} ${b[1]}"/>`
        }
        return g + ln(a, b, 'hit') + '</g>'
      }

      /* ---------- Heat map: soft, radial glow around every placed thermostat.
         Colour = measured temperature on the scale of the heating schedule (heatScale), linear: transparent at the lower end
         of the scale, HEAT_MAX at the upper end – so a room with 17 °C is almost transparent on a scale from 15 to
         22 °C, even if 17 °C is exactly its set point. Several thermostats do not add up (since 0.20.1):
         every place gets the mean of its sources weighted by proximity, and only the fall-off with the path through
         the home thins it out. Walls and windows stop the glow, doors let it through. */
      const HEAT_R = 3.6     // half-value distance in grid cells (long, soft fall-off)
      const HEAT_MAX = 0.68  // highest opacity: the warmest places stay restrained, accent outlines stand out
      const openWindows = () => { const db = devById(); return st.plan.openings.filter(o => o.type === 'window' && dockedDevices(o, db).some(x => isContact(x) && x.online && x.open)) }
      // Heat image as a small raster image (data URL): heat spreads from radiators and thermostats through the rooms,
      // walls stop it. Only recomputed if plan or temperatures change.
      function heatmapURL(lay) {
        const { width: W, height: H } = st.plan, db = devById()
        const [lo, hi] = heatScale(st.heatRange), span = hi - lo
        const k = d => clamp((+d.temperature - lo) / span, 0, 1)
        const live = d => d && d.kind === 'thermostat' && d.online && d.temperature != null
        // heat sources: radiators with a docked thermostat (whole width) …
        // (several thermostats at one radiator: the mean of their temperatures)
        const warmth = o => { const list = dockedDevices(o, db).filter(live); return list.length ? list.reduce((sum, x) => sum + k(x), 0) / list.length : null }
        const src = st.plan.openings.filter(o => o.type === 'radiator' && warmth(o) != null).map(o => {
          const L = dist(o.a, o.b), n = Math.max(2, Math.ceil(L / 0.15)), seeds = []
          for (let i = 0; i <= n; i++) {
            const t = i / n, m = [o.a[0] + (o.b[0] - o.a[0]) * t, o.a[1] + (o.b[1] - o.a[1]) * t], q = dockPoint({ ...o, a: m, b: [m[0] + (o.b[0] - o.a[0]) * 1e-3, m[1] + (o.b[1] - o.a[1]) * 1e-3] }, 0.3)
            seeds.push(q)
          }
          return { seeds, k: warmth(o) }
        })
        // … and freely placed thermostats (not docked)
        for (const [id, pos] of Object.entries(st.plan.devices)) {
          if (live(db[id]) && !dockedTo(id)) src.push({ seeds: [[pos.x, pos.y]], k: k(db[id]) })
        }
        const tint = HO.accentRGB()   // the heat map glows in the accent colour (follows an own Pro colour)
        const key = JSON.stringify([W, H, st.plan.walls, st.plan.openings.map(o => [o.type, o.a, o.b, o.side]), src, st.heatRange, tint])
        if (st.hm.key === key) return st.hm.url
        const cs = Math.max(0.08, W / 320), gw = Math.ceil(W / cs), gh = Math.ceil(H / cs), N = gw * gh
        const cx = i => (i + 0.5) * cs
        // 1) blocking: outside the rooms, walls; open doors again
        const blocked = new Uint8Array(N)
        const polys = lay.map(l => l.face.poly)
        if (polys.length) {
          for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
            const p = [cx(x), cx(y)]
            if (!polys.some(poly => inside(poly, p))) blocked[y * gw + x] = 1
          }
        }
        // Block or release all cells of the heat grid within distance r around a segment (walls, passages).
        const stamp = (a, b, r, val) => {
          const x0 = Math.max(0, Math.floor((Math.min(a[0], b[0]) - r) / cs)), x1 = Math.min(gw - 1, Math.ceil((Math.max(a[0], b[0]) + r) / cs))
          const y0 = Math.max(0, Math.floor((Math.min(a[1], b[1]) - r) / cs)), y1 = Math.min(gh - 1, Math.ceil((Math.max(a[1], b[1]) + r) / cs))
          for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if (project([cx(x), cx(y)], a, b).d < r) blocked[y * gw + x] = val
        }
        for (const w of st.plan.walls) { const f = flatten(w); for (let i = 0; i < f.length - 1; i++) stamp(f[i], f[i + 1], cs * 0.8, 1) }
        for (const o of st.plan.openings) if (o.type === 'door' || o.type === 'passage') stamp(o.a, o.b, cs * 1.3, 0)
        // 2) per thermostat: path distance (Dijkstra on the grid, 8 neighbours, no diagonal through wall corners)
        //    Per cell: warm = sum (value × proximity), near = sum of the proximity, gap = how far all sources are away
        //    (product of the fall-off remainders). Proximity = (HEAT_R / path)² – right at a source only it counts. The
        //    fall-off has a broad shoulder (remainder squared): around the thermostat the full colour of the room stands,
        //    only further away does it thin out – that way a warm room reads as warm as a whole.
        const warm = new Float32Array(N), near = new Float32Array(N), gap = new Float32Array(N).fill(1), maxD = HEAT_R * 7
        const dd = new Float64Array(N)   // Float32 would round when comparing and discard nodes
        for (const s of src) {
          dd.fill(Infinity)
          const heap = []
          for (const q of s.seeds) {
            const sx = clamp(Math.floor(q[0] / cs), 0, gw - 1), sy = clamp(Math.floor(q[1] / cs), 0, gh - 1), i0 = sy * gw + sx
            if (dd[i0] !== 0) { dd[i0] = 0; heap.push([0, i0]) }
          }
          const push = (d, i) => { heap.push([d, i]); let c = heap.length - 1; while (c > 0) { const pa = (c - 1) >> 1; if (heap[pa][0] <= heap[c][0]) break; [heap[pa], heap[c]] = [heap[c], heap[pa]]; c = pa } }
          const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let c = 0; for (;;) { const l = 2 * c + 1, r = l + 1; let m = c; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === c) break; [heap[m], heap[c]] = [heap[c], heap[m]]; c = m } } return top }
          while (heap.length) {
            const [d, i] = pop()
            if (d > dd[i] || d > maxD) continue
            const x = i % gw, y = (i / gw) | 0
            for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
              if (!dx && !dy) continue
              const nx = x + dx, ny = y + dy
              if (nx < 0 || ny < 0 || nx >= gw || ny >= gh) continue
              const j = ny * gw + nx
              if (blocked[j]) continue
              if (dx && dy && (blocked[y * gw + nx] || blocked[ny * gw + x])) continue
              const nd = d + (dx && dy ? 1.4142 : 1) * cs
              if (nd < dd[j]) { dd[j] = nd; push(nd, j) }
            }
          }
          for (let i = 0; i < N; i++) {
            if (dd[i] >= maxD) continue
            const far = (dd[i] / HEAT_R) ** 2, weight = 1 / (far + 0.02), rest = far / (1 + far)
            warm[i] += s.k * weight; near[i] += weight; gap[i] *= rest * rest
          }
        }
        // 3) image (scaled up softly; the walls lie on top)
        const cv = document.createElement('canvas'); cv.width = gw; cv.height = gh
        const ctx = cv.getContext('2d'), img = ctx.createImageData(gw, gh), px = img.data
        for (let i = 0; i < N; i++) {
          // temperature of the place on the scale (0…1) × fall-off: linear up to HEAT_MAX, nothing adds up
          const a = near[i] ? HEAT_MAX * (warm[i] / near[i]) * (1 - gap[i]) : 0
          px[i * 4] = tint[0]; px[i * 4 + 1] = tint[1]; px[i * 4 + 2] = tint[2]; px[i * 4 + 3] = Math.round(a * 255)
        }
        ctx.putImageData(img, 0, 0)
        st.hm = { key, url: cv.toDataURL() }
        return st.hm.url
      }

      /* Building shadow: the floor plan (rooms + walls) offset in SHADOW_STEPS steps along the shadow direction,
         painted over from back (light) to front (dark) – in a luminance mask, so that nothing adds up.
         That way the side edges stay sharp; only towards the back, away from the building, does the shadow fade out. */
      // Position of the sun for drawing to a tenth of a degree: otherwise the shadow would change by fractions of a pixel
      // every few seconds – and with it the whole image, which would then be repainted every time
      const steadySun = sun => ({ az: Math.round(sun.az * 10) / 10, el: Math.round(sun.el * 10) / 10 })
      // Cast shadow of the building by the position of the sun; at night a soft halo instead (nightShadowSVG).
      function shadowSVG(lay) {
        if (!st.plan.shadow || !lay.length && !st.plan.walls.length) return ''
        const sun = steadySun(sunNow())
        if (underground()) return nightShadowSVG(lay, { el: -6 })   // below the ground: no sun, only the soft halo
        if (sun.el < 1) return nightShadowSVG(lay, sun)
        const len = Math.min(SHADOW_MAX, SHADOW_H / Math.tan(sun.el * Math.PI / 180))
        const a = (st.plan.north + sun.az + 180) * Math.PI / 180, v = [Math.sin(a) * len, -Math.cos(a) * len]
        const pts = lay.flatMap(it => it.face.poly).concat(st.plan.walls.flatMap(w => w.pts))
        if (!pts.length) return ''
        const N = SHADOW_STEPS, sw = Math.max(0.12, len / N * 1.5), pad = len + sw + 0.5
        const x0 = Math.min(...pts.map(p => p[0])) - pad, y0 = Math.min(...pts.map(p => p[1])) - pad
        const x1 = Math.max(...pts.map(p => p[0])) + pad, y1 = Math.max(...pts.map(p => p[1])) + pad
        const d = ps => 'M' + ps.map(p => r3(p[0]) + ' ' + r3(p[1])).join('L')
        const body = lay.map(it => `<path d="${d(it.face.poly)}Z"/>`).join('')
          + st.plan.walls.filter(w => w.pts.length > 1).map(w => `<path d="${d(w.pts)}" fill="none"/>`).join('')
        let copies = ''
        for (let i = N; i >= 0; i--) {
          const t = i / N, g = Math.round(255 * Math.min(1, 1 - t + 0.5 / N)), c = `rgb(${g},${g},${g})`
          copies += `<use href="#fpshB" transform="translate(${r3(v[0] * t)} ${r3(v[1] * t)})" fill="${c}" stroke="${c}"/>`
        }
        const box = `x="${r3(x0)}" y="${r3(y0)}" width="${r3(x1 - x0)}" height="${r3(y1 - y0)}"`
        // weather effects (Pro): clouds make the shadow weaker and softer – without the feature it stays as before
        const fx = st.editing ? null : fxNow(), cloud = fx ? clamp(+fx.cloud || 0, 0, 1) : 0
        // economical devices (HO.perf): the shadow stays, only without blur
        const blur = cloud > 0.05 && !HO.perf.low ? `<filter id="fpshblur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${r3(0.05 + cloud * 0.45)}"/></filter>` : ''
        return `<defs><g id="fpshB" stroke-width="${r3(sw)}" stroke-linejoin="round" stroke-linecap="round">${body}</g>
          <mask id="fpshmask" maskUnits="userSpaceOnUse" ${box}>${copies}</mask>${blur}</defs>
          <g class="fp-shadow" opacity="${(SHADOW_DAY * Math.min(1, sun.el / 12) * (1 - 0.6 * cloud)).toFixed(2)}"${blur ? ' filter="url(#fpshblur)"' : ''}><rect ${box} mask="url(#fpshmask)"/></g>`
      }

      /* Night: no cast shadow (no sun), but a soft, even halo around the building – it still stands out
         from the ground, only more subtly. Fades in at dusk (sun 1° … −6°); clouds dampen it. */
      function nightShadowSVG(lay, sun) {
        const fx = st.editing ? null : fxNow(), cloud = fx ? clamp(+fx.cloud || 0, 0, 1) : 0
        const k = clamp((1 - sun.el) / 7, 0, 1) * (1 - 0.4 * cloud)
        const d = ps => 'M' + ps.map(p => r3(p[0]) + ' ' + r3(p[1])).join('L')
        const body = lay.map(it => `<path d="${d(it.face.poly)}Z"/>`).join('')
          + st.plan.walls.filter(w => w.pts.length > 1).map(w => `<path d="${d(w.pts)}" fill="none"/>`).join('')
        if (!body || k < 0.02) return ''
        // economical devices (HO.perf): the same halo from three stacked borders that get paler outwards instead of
        // a blur – an SVG filter over the whole building costs noticeable time there on every repaint
        if (HO.perf.low) {
          const ring = (width, alpha) => `<g stroke-width="${width}" opacity="${alpha}">${body}</g>`
          return `<g class="fp-shadow night" opacity="${(SHADOW_NIGHT * k).toFixed(2)}" fill="black" stroke="black"
            stroke-linejoin="round" stroke-linecap="round">${ring(1.5, 0.22)}${ring(1.0, 0.3)}${ring(0.5, 0.5)}</g>`
        }
        return `<defs><filter id="fpshblur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="0.4"/></filter></defs>
          <g class="fp-shadow night" opacity="${(SHADOW_NIGHT * k).toFixed(2)}" filter="url(#fpshblur)" fill="black" stroke="black"
            stroke-width="0.6" stroke-linejoin="round" stroke-linecap="round">${body}</g>`
      }

      /* Centred, soft shadow below device symbols – lifts the magenta outline off the heat map.
         Clipped per symbol to its own room area (the filter acts before the clipping), lies below the walls:
         so it bleeds neither over walls nor into neighbouring rooms or out of the home. Outside every room: no shadow. */
      function devShadowSVG(list, lay, R) {
        const clips = new Map(), low = HO.perf.low
        let out = ''
        for (const x of list) {
          const k = lay.findIndex(it => inside(it.face.poly, x.p))
          if (k < 0) continue
          clips.set(k, lay[k].face.poly)
          // economical devices (HO.perf): a soft gradient instead of a blur per symbol – looks almost the same
          out += low ? `<circle cx="${x.p[0]}" cy="${x.p[1]}" r="${(x.r * 1.2 + R * 0.9).toFixed(3)}" fill="url(#fpdevsoft)" clip-path="url(#fpclip${k})"/>`
            : `<circle cx="${x.p[0]}" cy="${x.p[1]}" r="${(x.r * 1.2).toFixed(3)}" filter="url(#fpdevblur)" clip-path="url(#fpclip${k})"/>`
        }
        if (!out) return ''
        const defs = [...clips].map(([k, poly]) => `<clipPath id="fpclip${k}"><path d="M${poly.map(p => p.join(' ')).join('L')}Z"/></clipPath>`).join('')
        if (low) {
          return `<defs>${defs}<radialGradient id="fpdevsoft"><stop offset="0" stop-color="black" stop-opacity=".75"/><stop offset=".45" stop-color="black" stop-opacity=".6"/>
            <stop offset=".75" stop-color="black" stop-opacity=".2"/><stop offset="1" stop-color="black" stop-opacity="0"/></radialGradient></defs>
            <g class="fp-devshadow soft">${out}</g>`
        }
        return `<defs>${defs}<filter id="fpdevblur" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="${(R * 0.45).toFixed(3)}"/></filter></defs>
          <g class="fp-devshadow">${out}</g>`
      }

      /* Stairs. A staircase runs from its foot `a` to its head `b` and has a width. On its own floor it is the way up:
         its treads. On the floor above, the same place is the stairwell (stairwellSVG): a
         pit whose shade deepens towards the foot, with side walls and treads that narrow and fade – it reads as the
         way down. A tap on either changes the floor (viewTap).
         Stairs above each other (a stairwell of a house): if a staircase of the floor shown lies on the stairwell of
         the floor below, the two share the width – one half the flight up, the other half the way down (`span`), so
         both stay visible and both can be tapped. */
      // Geometry of a staircase: `point(t, k)` = point at t along the run (0 foot … 1 head) and k across (−1 … 1).
      // `span` = the part of the width that is drawn ([−1, 0] or [0, 1] for one half), without it the whole width.
      function stairShape(stair, span) {
        const L = dist(stair.a, stair.b) || 1, dir = [(stair.b[0] - stair.a[0]) / L, (stair.b[1] - stair.a[1]) / L]
        const half = (stair.width || STAIR_WIDTH) / 2, from = span ? span[0] : -1, to = span ? span[1] : 1
        const across = k => from + (k + 1) / 2 * (to - from)
        const at = (t, k) => [r3(stair.a[0] + dir[0] * L * t - dir[1] * half * across(k)), r3(stair.a[1] + dir[1] * L * t + dir[0] * half * across(k))]
        const line = pts => 'M' + pts.map(p => p.join(' ')).join('L')
        return { L, point: at, line, steps: Math.max(3, Math.round(L / 0.5)), outline: line([at(0, -1), at(1, -1), at(1, 1), at(0, 1)]) + 'Z' }
      }
      // A staircase of the floor shown: the way up.
      function stairSVG(stair, extra, span) {
        const shape = stairShape(stair, span), sel = !!stair.id && isSel('stair', stair.id)
        let treads = ''
        for (let i = 1; i < shape.steps; i++) treads += shape.line([shape.point(i / shape.steps, -1), shape.point(i / shape.steps, 1)])
        return `<g class="fp-stair ${sel ? 'sel' : ''} ${extra || ''}" data-kind="stair" data-id="${HO.esc(stair.id || '')}"><path class="hit" d="${shape.outline}"/><path class="body" d="${shape.outline}"/><path class="tread" d="${treads}"/></g>`
      }
      // A staircase of the floor below, seen from above: the stairwell. The head is level with this floor, the foot
      // lies deepest – there the shade is darkest, the treads are narrowest and palest.
      function stairwellSVG(stair, index, span) {
        const shape = stairShape(stair, span), narrow = 0.24
        let treads = ''
        for (let i = 1; i < shape.steps; i++) {
          const t = i / shape.steps, k = 1 - narrow * (1 - t)
          treads += `<path class="tread" opacity="${(0.35 + 0.55 * t).toFixed(2)}" d="${shape.line([shape.point(t, -k), shape.point(t, k)])}"/>`
        }
        const side = sign => `<path class="side" d="${shape.line([shape.point(1, sign), shape.point(0, sign), shape.point(0, sign * (1 - narrow))])}Z"/>`
        return `<defs><linearGradient id="fpwell${index}" gradientUnits="userSpaceOnUse" x1="${stair.b[0]}" y1="${stair.b[1]}" x2="${stair.a[0]}" y2="${stair.a[1]}">
            <stop offset="0" stop-color="black" stop-opacity="0"/><stop offset=".45" stop-color="black" stop-opacity=".35"/><stop offset="1" stop-color="black" stop-opacity=".92"/></linearGradient></defs>
          <g class="fp-stair well" data-kind="stairwell" data-id="${HO.esc(stair.id)}"><path class="hit" d="${shape.outline}"/><path class="pit" d="${shape.outline}"/>${side(-1)}${side(1)}${treads}
            <path class="shade" d="${shape.outline}" fill="url(#fpwell${index})"/><path class="rim" d="${shape.outline}"/></g>`
      }
      // Run of a staircase as a unit vector, and whether a point lies on the staircase.
      const stairDir = stair => { const L = dist(stair.a, stair.b) || 1; return [(stair.b[0] - stair.a[0]) / L, (stair.b[1] - stair.a[1]) / L] }
      function onStair(stair, q) {
        const dir = stairDir(stair), v = [q[0] - stair.a[0], q[1] - stair.a[1]], along = v[0] * dir[0] + v[1] * dir[1]
        return along >= 0 && along <= dist(stair.a, stair.b) && Math.abs(v[1] * dir[0] - v[0] * dir[1]) <= (stair.width || STAIR_WIDTH) / 2
      }
      // Do two staircases lie above each other – running along the same line, the middle of one on the other?
      function stacked(one, other) {
        const d1 = stairDir(one), d2 = stairDir(other)
        return Math.abs(d1[0] * d2[0] + d1[1] * d2[1]) > 0.7 && (onStair(one, midOf(other.a, other.b)) || onStair(other, midOf(one.a, one.b)))
      }
      // All stairs to draw: the stairwells of the floor below, then the stairs of the floor shown. A staircase above a
      // stairwell takes the half on its left, the stairwell the other half – whichever way the two run.
      function stairsSVG() {
        const below = floorAtLevel(st.plan.floor.level - 1), wells = below ? below.stairs : [], own = st.plan.stairs
        return wells.map((well, i) => {
          const above = own.find(stair => stacked(stair, well)), d1 = above && stairDir(above), d2 = stairDir(well)
          return stairwellSVG(well, i, !above ? null : d1[0] * d2[0] + d1[1] * d2[1] > 0 ? [-1, 0] : [0, 1])
        }).join('') + own.map(stair => stairSVG(stair, '', wells.some(well => stacked(stair, well)) ? [0, 1] : null)).join('')
      }

      /* Balconies and terraces of the floor shown: a floor of planks (a pattern of fine lines with staggered joints,
         running along the longest edge), of square tiles or plain (`surface`), inside a thin railing line. They lie below the rooms and are cut out where a
         room is – a deck is always outside. */
      function decksSVG(lay) {
        const decks = st.plan.decks, { width: W, height: H } = st.plan
        if (!decks.length) return ''
        let defs = '', body = ''
        decks.forEach((d, i) => {
          let longest = 0, turn = 0
          for (let k = 0; k < d.pts.length - 1; k++) {
            const a = d.pts[k], b = d.pts[k + 1], L = dist(a, b)
            if (L > longest) { longest = L; turn = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI }
          }
          const path = pathOf(d) + 'Z', sel = isSel('deck', d.id)
          // the floor: planks of wood (long boards with staggered joints), square tiles, or plain without a pattern
          const lines = d.surface === 'plain' ? '' : d.surface === 'tiles' ? ['1', '1', 'M0 0H1M0 0V1'] : ['7', '1', 'M0 0H7M0 .5H7M2 0V.5M5.5 .5V1']
          if (lines) defs += `<pattern id="fpplank${i}" width="${lines[0]}" height="${lines[1]}" patternUnits="userSpaceOnUse" patternTransform="rotate(${r3(turn)})"><path class="fp-plank" d="${lines[2]}"/></pattern>`
          body += `<g class="fp-deck ${sel ? 'sel' : ''}" data-kind="deck" data-id="${HO.esc(d.id)}"><path class="floor" d="${path}"/>${lines ? `<path class="planks" d="${path}" fill="url(#fpplank${i})"/>` : ''}<path class="rail" d="${path}"/></g>`
        })
        // economical devices (HO.perf) save the mask: there a deck drawn across a room shows through its floor
        const cut = lay.length > 0 && !HO.perf.low
        if (cut) {
          defs += `<mask id="fpdeckcut" maskUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><rect x="0" y="0" width="${W}" height="${H}" fill="white"/>${
            lay.map(it => `<path d="M${it.face.poly.map(p => p.join(' ')).join('L')}Z" fill="black"/>`).join('')}</mask>`
        }
        return `<defs>${defs}</defs><g class="fp-decks"${cut ? ' mask="url(#fpdeckcut)"' : ''}>${body}</g>`
      }
      // Editor: walls and decks of the neighbouring floor shine through faintly for orientation (can be switched off).
      function underlaySVG() {
        const floor = st.editing && ghostOn ? neighbour() : null
        if (!floor || (!floor.walls.length && !floor.decks.length)) return ''
        return `<g class="fp-under">${floor.decks.map(d => `<path class="deck" d="${pathOf(d)}Z"/>`).join('')}${floor.walls.map(w => `<path d="${pathOf(w)}"/>`).join('')}</g>`
      }

      // Draw the whole floor plan: shadows, decks, rooms, heat map, walls, openings, devices, groups and – in the editor –
      // the neighbouring floor, draft and handles. The SVG is only rebuilt if the result changes.
      function renderSVG(inGesture) {
        // in the middle of a gesture the image stands still (only moved): new data is remembered and drawn afterwards
        if (live && !inGesture) { live.dirty = true; return }
        size()
        const u = 1 / st.ppu, { width: W, height: H } = st.plan
        const rb = roomById(), db = devById(), lay = layout()
        // stroke widths (CSS) scale along: slimmer on the phone, full width from about 30 px per cell
        svg.style.setProperty('--fp-k', clamp(st.ppu / 30, 0.55, 1).toFixed(3))
        let s = cam ? `<rect class="fp-plane" x="0" y="0" width="${W}" height="${H}" rx="${8 * u}"/>` : ''
        s += `<defs><pattern id="fpdots" width="1" height="1" patternUnits="userSpaceOnUse"><circle class="fp-grid-dot" cx="0" cy="0" r="${1.3 * u}"/></pattern></defs>`
        if (st.editing) s += `<rect x="0" y="0" width="${W}" height="${H}" fill="url(#fpdots)"/>`
        s += shadowSVG(lay) + underlaySVG() + decksSVG(lay)
        for (const it of lay) s += roomSVG(it, rb, u)
        if (heatOn()) {
          // open windows: cold air displaces the heat – pulsing radial mask on the heat map (no colour of its own,
          // only the room shows through there). Centre slightly inside the room, size by window width.
          const cold = openWindows().map(o => {
            const sd = baseSide(o), L = dist(o.a, o.b) || 1, n = [-(o.b[1] - o.a[1]) / L * sd, (o.b[0] - o.a[0]) / L * sd], m = midOf(o.a, o.b)
            const r = clamp(L * 0.75, 1.2, 2.4)
            return `<circle cx="${r3(m[0] + n[0] * 0.15)}" cy="${r3(m[1] + n[1] * 0.15)}" r="${r3(r)}" fill="url(#fpcold)">${REDUCED ? ''
              : `<animate attributeName="r" values="${r3(r * 0.7)};${r3(r * 1.15)};${r3(r * 0.7)}" dur="3.2s" repeatCount="indefinite" calcMode="spline" keySplines=".45 0 .55 1;.45 0 .55 1"/>`}</circle>`
          })
          if (cold.length) {
            s += `<defs><radialGradient id="fpcold"><stop offset="0" stop-color="black"/><stop offset=".45" stop-color="black" stop-opacity=".85"/>
                <stop offset="1" stop-color="black" stop-opacity="0"/></radialGradient>
              <mask id="fpheatmask" maskUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><rect x="0" y="0" width="${W}" height="${H}" fill="white"/>${cold.join('')}</mask></defs>`
          }
          s += `<image class="fp-heat" href="${heatmapURL(lay)}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="none"${cold.length ? ' mask="url(#fpheatmask)"' : ''}/>`
        }
        s += stairsSVG()
        s += '<!--fp-devshadow-->'   // device shadows: before the walls, so that those cover their edge
        for (const w of st.plan.walls) {
          const sel = isSel('wall', w.id), d = pathOf(w)
          s += `<g class="fp-wall ${sel ? 'sel' : ''}" data-kind="wall" data-id="${HO.esc(w.id)}"><path class="wall" d="${d}"/><path class="hit" d="${d}"/></g>`
        }
        for (const o of st.plan.openings) s += openingSVG(o, u, db)
        if (st.editing) for (const it of lay) s += roomTextSVG(it, rb, u)   // Ansicht: Raum steht oben links (renderTitle)
        // devices: a docked one is a dot at its element, a plan symbol stands free with a status dot, all others are
        // the standard bubble – one size for all, only plan symbols can be scaled
        const R = clamp(st.ppu * 0.6, 8, 15) * u, nfs = clamp(st.ppu * 0.3, 7.5, 11) * u, mark = R * MARK
        const nameSVG = (name, below) => st.plan.show_names ? `<text class="fp-device-name" x="0" y="${r3(below + nfs * 1.15)}" font-size="${nfs}" stroke-width="${nfs * 0.28}" text-anchor="middle">${HO.esc(name)}</text>` : ''
        st.dockPos = dockLayout(db, R, u)
        const mg = st.drag && st.drag.merge, shadows = []
        for (const [id, stored] of Object.entries(st.plan.devices)) {
          const d = db[id]
          if (!d) continue
          const sel = isSel('dev', id) ? 'sel' : '', merge = mg && mg.type === 'dev' && mg.id === id ? 'merge' : ''
          const dp = dockedTo(id) && st.dockPos.get(id), box = !dp && symbolBox(stored, u)
          if (dp) {
            // docked = small dot at the element itself (radiator: on the body, window/door: outside) – in the
            // view and in the editor. In the editor the grab area is larger: dragging the dot away detaches the device.
            const iss = d.alarm || (d.errors && d.errors.length) ? 'ind-bad' : HO.lowBattery(d) ? 'ind-warn' : ''
            shadows.push({ p: dp.p, r: dp.r })
            s += `<g class="fp-dev ${HO.devClass(d)} ${sel} docked dock-ind ${iss}" data-kind="dev" data-id="${HO.esc(id)}" transform="translate(${dp.p[0]} ${dp.p[1]})">
            <circle class="hit" r="${dp.hit || dp.r * (st.editing ? 3.4 : 2.2)}"/><circle class="bg" r="${dp.r}"/></g>`
          } else if (box) {
            // plan symbol: the shape itself stays neutral, the dot at its corner carries the state (same colours as a
            // docked dot); the grab area is the whole box, at least as large as a bubble
            const cx = box.sym.round ? box.hw * 0.71 : box.hw, cy = box.sym.round ? box.hh * 0.71 : box.hh
            const hx = Math.max(box.hw, R * 0.8), hy = Math.max(box.hh, R * 0.8)
            shadows.push({ p: [stored.x, stored.y], r: (box.hw + box.hh) * 0.45 })
            s += `<g class="fp-dev symbol ${HO.devClass(d)} ${sel} ${merge}" data-kind="dev" data-id="${HO.esc(id)}" transform="translate(${stored.x} ${stored.y})">
            <rect class="hit" x="${r3(-hx)}" y="${r3(-hy)}" width="${r3(2 * hx)}" height="${r3(2 * hy)}" rx="${r3(R * 0.3)}"/>
            <g class="fp-sym" transform="rotate(${box.rot}) scale(${box.k.toFixed(5)}) translate(-24 -24)">${box.sym.svg}</g>
            <circle class="fp-dot" cx="${r3(cx)}" cy="${r3(-cy)}" r="${r3(R * DOT)}"/>
            ${deviceMarks(d, cx, cy, mark, true)}${nameSVG(d.name, box.hh)}
          </g>`
          } else {
            const gs = R * 1.22   // edge of the glyph (24 units)
            shadows.push({ p: [stored.x, stored.y], r: R })
            s += `<g class="fp-dev ${HO.devClass(d)} ${sel} ${merge}" data-kind="dev" data-id="${HO.esc(id)}" transform="translate(${stored.x} ${stored.y})">
            <circle class="bg" r="${R}"/><g class="glyph" transform="translate(${-gs / 2} ${-gs / 2}) scale(${gs / 24})">${HO.iconPaths[d.kind] || HO.iconPaths.device}</g>
            ${deviceMarks(d, R * 0.78, R * 0.78, mark, false)}${nameSVG(d.name, R)}
          </g>`
          }
        }
        // groups: one bubble for several devices (always the same size), in it a chosen plan symbol, the symbol of a
        // device kind or the symbols of the first four devices
        for (const g of st.plan.groups) {
          const ms = g.members.map(id => db[id]).filter(Boolean)
          if (!ms.length) continue
          const r0 = R * GROUP_SCALE, n = ms.length, sel = isSel('group', g.id)
          const symbol = g.icon && HO.planSymbols[g.icon], kindIcon = g.icon && !symbol && HO.iconPaths[g.icon]
          let mini
          if (symbol) {
            const k = symbol.round ? r0 * 1.5 / symbol.w : r0 * 1.62 / Math.hypot(symbol.w, symbol.h)   // fits into the circle
            mini = `<g class="fp-sym" transform="scale(${k.toFixed(5)}) translate(-24 -24)">${symbol.svg}</g>`
          } else if (kindIcon) {
            const gs = r0 * 1.22
            mini = `<g class="glyph" transform="translate(${-gs / 2} ${-gs / 2}) scale(${gs / 24})">${kindIcon}</g>`
          } else {
            const gs = r0 * (n === 1 ? 1.2 : 0.66), k = r0 * 0.38
            mini = ms.slice(0, 4).map((d, i) => {
              const c = n === 1 ? [0, 0] : n === 2 ? [(i ? 1 : -1) * k, 0] : [(i % 2 ? 1 : -1) * k, (i < 2 ? -1 : 1) * k]
              return `<g class="glyph" transform="translate(${c[0] - gs / 2} ${c[1] - gs / 2}) scale(${gs / 24})">${HO.iconPaths[d.kind] || HO.iconPaths.device}</g>`
            }).join('')
          }
          const hasErr = ms.some(d => d.alarm || (d.errors && d.errors.length)), hasOffline = ms.some(d => !d.online), low = ms.some(HO.lowBattery)
          const edge = r0 * 0.78
          shadows.push({ p: [g.x, g.y], r: r0 })
          s += `<g class="fp-dev fp-group ${groupClass(ms)} ${sel ? 'sel' : ''} ${mg && mg.type === 'group' && mg.id === g.id ? 'merge' : ''}" data-kind="group" data-id="${HO.esc(g.id)}" transform="translate(${g.x} ${g.y})">
            <circle class="bg" r="${r0}"/>${mini}
            ${markSVG('count', r0 * 0.8, -r0 * 0.8, R * COUNT_MARK, n)}
            ${hasErr ? markSVG('error', edge, edge, mark) : ''}${hasOffline ? markSVG('offline', hasErr ? -edge : edge, edge, mark) : ''}${!hasErr && !hasOffline && low ? markSVG('battery', edge, edge, mark) : ''}
            ${nameSVG(g.name || 'Gruppe', r0)}
          </g>`
        }
        s = s.replace('<!--fp-devshadow-->', devShadowSVG(shadows, lay, R))
        // Entwurf
        const dr = st.draft
        if (dr && dr.kind === 'wall' && dr.pts.length) {
          const pts = dr.cur ? [...dr.pts, dr.cur] : dr.pts
          s += `<polyline class="fp-draft" points="${pts.map(p => p.join(',')).join(' ')}"/>`
          dr.pts.forEach((p, i) => {
            const close = i === 0 && dr.pts.length >= 3
            s += `<circle class="fp-draft-pt ${close ? 'close' : ''}" cx="${p[0]}" cy="${p[1]}" r="${(close ? 9 : 4) * u}"/>`
          })
        } else if (dr && dr.kind === 'stair') {
          if (dist(dr.a, dr.b) > 0.2) s += stairSVG({ a: dr.a, b: dr.b, width: STAIR_WIDTH }, 'draft')
        } else if (dr && dr.kind === 'open') {
          s += openingSVG({ type: dr.type, a: dr.a, b: dr.b, side: dr.side || 1 }, u, db, 'draft')
        }
        // Griffe der Auswahl
        const it = st.editing && selItem()
        if (it && (st.sel.type === 'wall' || st.sel.type === 'deck')) {
          for (let i = 0; i < it.pts.length - 1; i++) {
            const [a, b, c] = edgeOf(it, i), m = midOf(a, b, c)
            s += `<circle class="fp-mid ${c ? 'bent' : ''}" data-kind="mid" data-i="${i}" cx="${m[0]}" cy="${m[1]}" r="${6 * u}"/>`
          }
          // a deck is closed: its last point is its first one and gets no handle of its own
          const corners = st.sel.type === 'deck' ? it.pts.slice(0, -1) : it.pts
          corners.forEach((p, i) => { s += `<circle class="fp-handle" data-kind="vtx" data-i="${i}" cx="${p[0]}" cy="${p[1]}" r="${8 * u}"/>` })
        } else if (it && st.sel.type === 'stair') {
          for (const e of ['a', 'b']) s += `<circle class="fp-handle" data-kind="send" data-end="${e}" cx="${it[e][0]}" cy="${it[e][1]}" r="${8 * u}"/>`
        } else if (it && st.sel.type === 'open') {
          for (const e of ['a', 'b']) s += `<circle class="fp-handle" data-kind="oend" data-end="${e}" cx="${it[e][0]}" cy="${it[e][1]}" r="${8 * u}"/>`
        }
        // selected plan symbol: frame with one handle at the corner – dragging it changes the size of the symbol
        if (st.editing && st.sel && st.sel.type === 'dev' && !st.msel.length && db[st.sel.id] && !dockedTo(st.sel.id)) {
          const e = st.plan.devices[st.sel.id], box = symbolBox(e, u)
          if (box) {
            const pad = 5 * u, hs = (COARSE ? 18 : 10) * u, x1 = e.x + box.hw + pad, y1 = e.y + box.hh + pad
            s += `<rect class="fp-mbox" x="${r3(e.x - box.hw - pad)}" y="${r3(e.y - box.hh - pad)}" width="${r3(2 * (box.hw + pad))}" height="${r3(2 * (box.hh + pad))}" rx="${4 * u}"/>
              <rect class="fp-mhandle corner" data-kind="symscale" x="${r3(x1 - hs / 2)}" y="${r3(y1 - hs / 2)}" width="${hs}" height="${hs}" rx="${3 * u}"/>`
          }
        }
        // Mehrfachauswahl: Rahmen mit Griffen (Ecken + Seitenmitten)
        const md = st.drag && (st.drag.mode === 'mmove' || st.drag.mode === 'mscale') ? st.drag : null
        st.mbox = st.editing && st.msel.length ? mselBox(md ? md.T : mselTargets()) : null
        if (st.mbox) {
          const b = st.mbox, pad = 6 * u, x0 = b.x - pad, y0 = b.y - pad, x1 = b.x + b.w + pad, y1 = b.y + b.h + pad
          const hs = (COARSE ? 18 : 10) * u
          s += `<rect class="fp-mbox" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" rx="${4 * u}"/>`
          const hp = { nw: [x0, y0], n: [(x0 + x1) / 2, y0], ne: [x1, y0], e: [x1, (y0 + y1) / 2], se: [x1, y1], s: [(x0 + x1) / 2, y1], sw: [x0, y1], w: [x0, (y0 + y1) / 2] }
          for (const [c, q] of Object.entries(hp)) {
            s += `<rect class="fp-mhandle ${c.length === 2 ? 'corner' : ''}" data-kind="mscale" data-c="${c}" x="${q[0] - hs / 2}" y="${q[1] - hs / 2}" width="${hs}" height="${hs}" rx="${c.length === 2 ? 3 * u : hs / 2}"/>`
          }
        }
        if (st.drag && st.drag.mode === 'marquee' && st.drag.moved) {
          const a = st.drag.start, c = st.drag.cur
          s += `<rect class="fp-marquee" x="${Math.min(a[0], c[0])}" y="${Math.min(a[1], c[1])}" width="${Math.abs(c[0] - a[0])}" height="${Math.abs(c[1] - a[1])}"/>`
        }
        // Only rebuild if the image really changes. The home page reports fresh device data every few
        // seconds; mostly everything is as before afterwards – and a rebuild would mean: repaint everything (with shadows and
        // blurs) and start every running animation over.
        if (s !== st.drawn) {
          svg.innerHTML = s
          st.drawn = s
          pulse.sync()
        }
        markRooms()
        const legend = el.querySelector('.fp-legend')
        if (legend) {
          const [lo, hi] = heatScale(st.heatRange)   // the same scale as the image
          legend.querySelector('.fp-lo').textContent = Math.round(lo) + '°'
          legend.querySelector('.fp-hi').textContent = Math.round(hi) + '°'
        }
        renderTitle()
        renderFloorNav()
        syncFx()
      }
      // Buttons in the frame to change the floor (opts.floorNav: floor plan widget) – only with several floors, never in
      // the editor. Written only on a change.
      function renderFloorNav() {
        const show = !!opts.floorNav && !st.editing && levels().length > 1, floor = st.plan.floor
        const key = show ? [floor.id, floorShort(floor), !!floorAtLevel(floor.level + 1), !!floorAtLevel(floor.level - 1)].join('|') : ''
        if (key === st.navKey) return
        st.navKey = key
        floorNav.classList.toggle('hidden', !show)
        if (!show) return
        floorNav.innerHTML = `<button type="button" data-floorstep="1" title="Ein Stockwerk nach oben" aria-label="Ein Stockwerk nach oben" ${floorAtLevel(floor.level + 1) ? '' : 'disabled'}>${HO.icon('chevron', 'sm')}</button>
          <span title="${HO.esc(floorLabel(floor))}">${HO.esc(floorShort(floor))}</span>
          <button type="button" data-floorstep="-1" title="Ein Stockwerk nach unten" aria-label="Ein Stockwerk nach unten" ${floorAtLevel(floor.level - 1) ? '' : 'disabled'}>${HO.icon('chevron', 'sm')}</button>`
      }

      /* ---------- Weather effects (Pro floorplan_weather, weather_fx.js): only in the view, only with weather and rooms */
      const fxAllowed = () => !!HO.weatherFx && HO.can('floorplan_weather') && st.plan.weather !== false && st.plan.fx !== false
      // Position of the sun – one place for shadows and weather effects; `moon` = position and phase of the moon (az, el,
      // phase) or null. Preview (owner/development): force day or night – the night of the preview has a bright moon.
      // The effect lab (dev/fxlab) sets any position: HO.state.fxTime = {az, el, moon: {az, el, phase}}.
      function sunNow() {
        const tm = HO.state.fxTime
        if (tm && typeof tm === 'object') return { az: +tm.az || 0, el: +tm.el || 0, moon: tm.moon || null }
        if (tm === 'day') return { az: 215, el: 32, moon: null }
        if (tm === 'dusk') return { az: 262, el: -2, moon: null }
        if (tm === 'night') return { az: 20, el: -25, moon: { az: 150, el: 38, phase: 0.85 } }
        const geo = (HO.state.overview && HO.state.overview.geo) || [51.16, 10.45], now = new Date()
        return Object.assign(sunPos(now, geo[0], geo[1]), { moon: moonPos(now, geo[0], geo[1]) })
      }
      // Weather for the effects: the real weather of the home page or a chosen preview; null if effects are not
      // allowed or switched off.
      function fxNow() {
        if (!fxAllowed()) return null
        const pv = HO.state.fxPreview, tm = HO.state.fxTime
        if (pv) return HO.weatherFx.preset(pv, tm ? tm === 'day' : (st.wx ? st.wx.is_day !== false : sunNow().el > 0))
        return st.wx && tm ? Object.assign({}, st.wx, { is_day: tm === 'day' }) : st.wx
      }
      /* What the effects show inside the rooms (since 0.27, plan units): `windows` = the windows in an outer wall – a, b,
         n = unit vector into the room, room = index of the room area (as in fxMask().polys), open = share of light a
         docked roller shutter lets through; `speakers` = every placed device or group that plays right now – x, y,
         r = radius of its symbol, room, level = volume 0…1. A muted speaker sends no waves; a floor below the ground
         has no window light. */
      function indoorNow() {
        const fs = faces(), db = devById(), u = 1 / st.ppu, R = clamp(st.ppu * 0.6, 8, 15) * u
        const roomOf = p => fs.findIndex(f => inside(f.poly, p))
        const windows = [], speakers = []
        for (const o of underground() ? [] : st.plan.openings) {
          const side = o.type === 'window' ? roomSideOf(o) : null, L = dist(o.a, o.b)
          if (!side || L < 0.2) continue
          const n = [-(o.b[1] - o.a[1]) / L * side, (o.b[0] - o.a[0]) / L * side], m = midOf(o.a, o.b)
          const room = roomOf([m[0] + n[0] * 0.4, m[1] + n[1] * 0.4])
          if (room < 0) continue
          const shut = dockedDevices(o, db).filter(d => d.kind === 'cover' && d.online && d.position != null).map(d => +d.position || 0)
          windows.push({ a: o.a, b: o.b, n, room, open: shut.length ? clamp(1 - Math.max.apply(null, shut) / 100, 0, 1) : 1 })
        }
        const plays = d => !!d && d.online && HO.caps.has(d, 'playing') && !!d.playing && !d.muted && (d.volume == null || d.volume > 0)
        const loud = d => d.volume == null ? 0.5 : clamp(d.volume / 60, 0.2, 1)
        for (const [id, pos] of Object.entries(st.plan.devices)) {
          const d = db[id]
          if (!plays(d) || dockedTo(id)) continue
          const room = roomOf([pos.x, pos.y]), box = symbolBox(pos, u)
          if (room >= 0) speakers.push({ x: pos.x, y: pos.y, r: box ? Math.max(box.hw, box.hh) : R, room, level: loud(d) })
        }
        for (const g of st.plan.groups) {
          const playing = g.members.map(id => db[id]).filter(plays), room = roomOf([g.x, g.y])
          if (playing.length && room >= 0) speakers.push({ x: g.x, y: g.y, r: R * GROUP_SCALE, room, level: Math.max.apply(null, playing.map(loud)) })
        }
        return { windows, speakers }
      }
      // Effects without weather: only what happens inside (sound waves) – the weather is unknown or below the ground.
      const INDOOR_ONLY = { kind: 'none', cloud: 0, rain: 0, snow: 0, wind: 0, indoor_only: true }
      // Mount, update or remove the effect canvas – depending on whether effects should run right now. Without weather
      // (no address, a floor below the ground, Basic, effects switched off) it still runs while a speaker plays: the
      // sound waves need no weather and belong to Basic; the light through the windows is part of the Pro effects.
      function syncFx() {
        const shown = !st.editing && faces().length > 0 && !!HO.weatherFx
        let fx = shown && !underground() ? fxNow() : null
        st.indoor = shown ? indoorNow() : null
        if (st.indoor && !(HO.can('floorplan_weather') && st.plan.fx !== false)) st.indoor.windows = []   // light is Pro, sound is not
        renderWind(fx)
        if (!fx && st.indoor && st.indoor.speakers.length) fx = INDOOR_ONLY
        if (!fx) { if (st.fxView) { st.fxView.destroy(); st.fxView = null } return }
        if (!st.fxView) st.fxView = HO.weatherFx.mount(wrap, { after: svg, getMask: fxMask, getSun: fxSun, getShift: fxShift, getIndoor: () => st.indoor })
        st.fxView.update(fx, { level: st.plan.fx_level })
      }
      /* Wind arrow top right in the floor plan (since 0.20.1, only with weather effects): shows where the wind blows to –
         rotated by the north orientation of the plan, that is the way the wind lines and clouds move. Next to it the speed.
         Written only on a change. */
      function renderWind(fx) {
        const box = el.querySelector('.fp-wind')
        const kmh = fx ? Math.round(typeof fx.wind_kmh === 'number' ? fx.wind_kmh : (fx.wind || 0) * 80) : 0
        const on = !!fx && typeof fx.wind_dir === 'number' && kmh >= 1
        const from = on ? ((Math.round(fx.wind_dir) % 360) + 360) % 360 : 0
        const key = on ? [from, kmh, st.plan.north, cam ? '' : svg.offsetLeft + '/' + svg.offsetTop + '/' + svg.offsetWidth].join('|') : ''
        wrap.classList.toggle('has-wind', on)
        if (key === st.windKey) return
        st.windKey = key
        box.classList.toggle('hidden', !on)
        if (!on) return
        if (!cam) { box.style.right = (wrap.clientWidth - svg.offsetLeft - svg.offsetWidth + 10) + 'px'; box.style.top = (svg.offsetTop + 10) + 'px' }
        const label = `Wind aus ${COMPASS[Math.round(from / 22.5) % 16]} mit ${kmh} km/h – der Pfeil zeigt, wohin er weht`
        box.setAttribute('title', label); box.setAttribute('aria-label', label)
        box.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V5M6.5 10.5 12 5l5.5 5.5"/></svg><span>${kmh} km/h</span>`
        // wind_dir = where the wind comes from; the arrow points the opposite way, "up" in the plan is rotated by north
        box.querySelector('svg').style.transform = `rotate(${(st.plan.north + from + 180) % 360}deg)`
      }
      // building in pixels of the container – holds with zoom and camera (viewBox), cached per section
      let maskCache = { key: null, val: null }
      /* What lies under the floor shown: the room areas of all other floors (plan units). The weather takes it for
         ground that is built over – no puddle lies there. Looked at once per plan and floor shown (the home page hands
         in a fresh plan every few seconds); the areas are only computed anew if the walls of those floors changed
         (`rev` counts that and is part of the id of the building). */
      let groundCache = { plan: null, floor: null, key: '', rev: 0, polys: [] }
      // The room areas of all floors but the one shown (plan units), from the cache above.
      function builtOver() {
        if (groundCache.plan !== st.plan || groundCache.floor !== st.floorId) {
          const others = levels().filter(f => f !== st.plan.floor), key = JSON.stringify(others.map(f => [f.id, f.walls]))
          if (key !== groundCache.key) {
            groundCache.polys = [].concat.apply([], others.map(f => computeFaces(f.walls).map(face => face.poly)))
            groundCache.key = key; groundCache.rev++
          }
          groundCache.plan = st.plan; groundCache.floor = st.floorId
        }
        return groundCache.polys
      }
      // Outline of the building in pixels of the frame (for the weather effects, which only draw outside); remembered
      // per section.
      function fxMask() {
        if (live && live.mask) return live.mask      // in the middle of a gesture: the outline of the painted image
        const m = svg.getScreenCTM()
        if (!m) return null
        const base = wrap.getBoundingClientRect(), sr = svg.getBoundingClientRect()
        // position in the frame, not on the screen: otherwise the outline would count as new on every scroll of the page –
        // the weather effects would then keep recomputing their flow field and the wind lines jumped
        const ox = m.e - base.left, oy = m.f - base.top, bx = sr.left - base.left, by = sr.top - base.top
        const fine = v => Math.round(v * 64)
        // the building is the floor shown: its walls, its decks – and what the other floors build over
        const covered = builtOver()
        const shape = [faceCache.key, st.floorId, JSON.stringify(st.plan.decks.map(d => d.pts)), groundCache.rev].join('|')
        const key = [m.a, m.b, m.c, m.d, fine(ox), fine(oy), fine(bx), fine(by), sr.width, sr.height, shape].join('|')
        if (maskCache.key === key) return maskCache.val
        const T = p => [m.a * p[0] + m.c * p[1] + ox, m.b * p[0] + m.d * p[1] + oy]
        // view = how plan units become pixels (origin and scale): with it puddles lie fixed on the ground;
        // shape = id of the building (stays the same during camera moves);
        // decks = balconies and terraces of this floor (outside: the weather passes over them), covered = areas of the
        // other floors – on neither of them a puddle of the ground may lie
        const val = { box: { x: bx, y: by, w: sr.width, h: sr.height },
          polys: faces().map(f => f.poly.map(T)), walls: st.plan.walls.map(w => flatten(w).map(T)),
          decks: st.plan.decks.map(d => flatten(d).map(T)), covered: covered.map(poly => poly.map(T)),
          wall: 4.5 * clamp(st.ppu / 30, 0.55, 1) + 2, view: { x: ox, y: oy, s: m.a }, shape }
        maskCache = { key, val }
        return val
      }
      // offset of the painted plan during a gesture (only if the weather image stays in place and draws along itself)
      const fxShift = () => live && live.fxStays ? live.shift : null
      // Sun and moon for the effects: elevation and direction towards them in the plan (north of the plan applied).
      function fxSun() {
        const sun = sunNow(), towards = az => { const a = (st.plan.north + az) * Math.PI / 180; return [Math.sin(a), -Math.cos(a)] }
        const moon = sun.moon ? { el: sun.moon.el, dir: towards(sun.moon.az), phase: clamp(+sun.moon.phase || 0, 0, 1) } : null
        return { el: sun.el, dir: towards(sun.az), north: st.plan.north, moon }
      }

      // settings "Wettereffekte" (next to "Wetter anzeigen"): on/off, strength, preview only for owner and COMFYOS_DEV=1
      function fxSettingsHTML() {
        if (!HO.weatherFx) return ''
        const can = HO.can('floorplan_weather'), off = st.plan.weather === false || !can, lv = st.plan.fx_level === 'subtle' ? 'subtle' : 'normal'
        const lic = HO.license || {}, preview = lic.licensed_plan === 'owner' || !!lic.dev
        return `<label class="check" style="margin-top:10px"><input type="checkbox" data-f="fx" ${st.plan.fx !== false && can ? 'checked' : ''} ${off ? 'disabled' : ''}>
            <span>Wettereffekte rund um den Grundriss ${HO.proTag('floorplan_weather')} <span class="muted small">(Regen, Schnee, Wind, Gewitter und Sonnenlicht rund ums Haus, Licht von Sonne und Mond durch die Fenster)</span></span></label>
          <label class="field" style="margin-top:8px"><span>Stärke der Wettereffekte</span>
            <select data-f="fx_level" ${off || st.plan.fx === false ? 'disabled' : ''}><option value="subtle" ${lv === 'subtle' ? 'selected' : ''}>Dezent</option><option value="normal" ${lv === 'normal' ? 'selected' : ''}>Normal</option></select></label>
          ${preview ? `<label class="field" style="margin-top:8px"><span>Wetter-Vorschau <span class="muted small">(nur Owner/Entwicklung, gilt bis zum Neuladen; auch per <code>#home?fx=thunder&amp;time=night</code>)</span></span>
            <div class="row"><select class="grow" data-f="fx_preview"><option value="">Echtes Wetter</option>${HO.weatherFx.KINDS.map(k =>
              `<option value="${k}" ${HO.state.fxPreview === k ? 'selected' : ''}>${HO.esc(HO.weatherFx.LABELS[k])}</option>`).join('')}</select>
            <select data-f="fx_time" aria-label="Tageszeit der Vorschau">${[['', 'Jetzt'], ['day', 'Tag'], ['dusk', 'Dämmerung'], ['night', 'Nacht']].map(([v, l]) =>
              `<option value="${v}" ${(HO.state.fxTime || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div></label>` : ''}`
      }

      /* ---------- Tools & sidebar (only in edit mode) */
      // Symbol picker of the editor: the standard look first, then every plan symbol as a picture (`kinds` = the symbols
      // of the device kinds too, for groups). A tap sets the symbol of the selected device or group (data-act="sym").
      function symbolPicker(current, firstLabel, firstIcon, kinds) {
        const button = (key, label, picture) => `<button type="button" data-act="sym" data-v="${key}" class="${(current || '') === key ? 'active' : ''}" title="${HO.esc(label)}" aria-label="${HO.esc(label)}" aria-pressed="${(current || '') === key}">${picture}</button>`
        const chosen = !current ? firstLabel : HO.planSymbols[current] ? HO.planSymbols[current].label : HO.kindLabel[current] || firstLabel
        return `<div class="field" style="margin-top:12px"><span>Symbol · ${HO.esc(chosen)}</span><div class="fp-sympick">
          ${button('', firstLabel, firstIcon)}${Object.entries(HO.planSymbols).map(([key, symbol]) => button(key, symbol.label, HO.planSymbolIcon(key))).join('')}
          ${kinds ? Object.entries(HO.kindLabel).filter(([key]) => HO.iconPaths[key]).map(([key, label]) => button(key, label, HO.icon(key))).join('') : ''}</div></div>`
      }
      // Second line of the toolbar: the floors to switch between (bottom to top), "add a floor" and – with a
      // neighbouring floor – the switches for shining through and snapping.
      function floorBarHTML() {
        const near = neighbour()
        return `<div class="fp-floorbar fp-toolscroll"><span class="row g8"><span class="small muted">Stockwerk</span>
          <span class="seg">${levels().map(f => `<button data-floor="${HO.esc(f.id)}" class="${f === st.plan.floor ? 'active' : ''}" title="${HO.esc(floorLabel(f))}">${HO.esc(floorShort(f))}</button>`).join('')}</span>
          <button class="btn sm" data-act="flooradd" title="Ein weiteres Stockwerk anlegen">${HO.icon('plus', 'sm')} Stockwerk${HO.proTag('multi_floorplan')}</button>
          ${near ? `<button class="btn sm ${ghostOn ? 'primary' : ''}" data-act="ghost" aria-pressed="${ghostOn}" title="„${HO.esc(floorLabel(near))}“ zur Orientierung durchscheinen lassen">${HO.icon('eye', 'sm')} Durchscheinen</button>
          <button class="btn sm ${floorSnap ? 'primary' : ''}" data-act="floorsnap" aria-pressed="${floorSnap}" title="An den Wänden von „${HO.esc(floorLabel(near))}“ einrasten">${HO.icon('layers', 'sm')} Einrasten</button>` : ''}</span></div>`
      }
      // Panel "new floor": above or below, with the outline of the floor shown or empty.
      function newFloorHTML() {
        const n = st.newFloor, pick = (act, value, on, label) => `<button data-act="${act}" data-v="${value}" class="${on ? 'active' : ''}">${label}</button>`
        return `<div class="fp-panel"><h4>Neues Stockwerk</h4>
          <div class="field" style="margin-top:10px"><span>Lage</span><div class="seg">${pick('floorwhere', 'above', n.where !== 'below', 'Ganz oben')}${pick('floorwhere', 'below', n.where === 'below', 'Ganz unten')}</div></div>
          <div class="field"><span>Grundriss</span><div class="seg">${pick('floorcopy', '1', n.copy, `Von „${HO.esc(floorShort(st.plan.floor))}“ übernehmen`)}${pick('floorcopy', '0', !n.copy, 'Leer anfangen')}</div>
            <span class="hint">Übernommen werden Wände, Türen, Fenster, Heizkörper und Balkone – keine Geräte. Die Räume des neuen Stockwerks sind eigene Räume.
              Beim Zeichnen scheint das Stockwerk darunter durch.</span></div>
          <div class="row" style="margin-top:12px"><button class="btn sm primary" data-act="floorcreate">${HO.icon('check', 'sm')} Anlegen</button>
            <button class="btn sm ghost" data-act="floorcancel">Abbrechen</button></div></div>`
      }
      // Panel of the floor shown (only with several floors): its name, how many devices stand elsewhere, delete.
      function floorPanelHTML() {
        if (levels().length < 2) return ''
        const floor = st.plan.floor, known = new Set(st.devices.map(d => d.id))
        const count = f => Object.keys(f.devices).filter(id => known.has(id)).length + f.groups.reduce((sum, g) => sum + g.members.filter(id => known.has(id)).length, 0)
        const elsewhere = levels().filter(f => f !== floor).reduce((sum, f) => sum + count(f), 0)
        return `<div class="fp-panel" style="margin-bottom:10px"><div class="row between"><h4>Stockwerk</h4>
            <button class="btn sm danger" data-act="floordelete">${HO.icon('trash', 'sm')} Löschen</button></div>
          <label class="field" style="margin-top:10px"><span>Name</span><input type="text" data-f="floorname" maxlength="40" placeholder="${HO.esc(floorLabel({ name: '', level: floor.level }))}" value="${HO.esc(floor.name)}"></label>
          <p class="small muted" style="margin:8px 0 0">${count(floor)} Geräte hier, ${elsewhere} auf anderen Stockwerken. Ein Gerät steht immer nur auf einem Stockwerk –
            zum Umziehen das Gerät antippen und unter „Stockwerk“ ein anderes wählen.</p></div>`
      }
      // Draw the toolbar and the panel of the editor: tools, the settings of the selection and the unplaced devices.
      // While a wall or a deck is drawn, "Fertig", "Punkt zurück" and "Abbrechen" lie over the usual buttons, which
      // stay in place invisibly (.fp-toolset.drawing): the toolbar keeps its height and the plan does not jump away
      // under the finger.
      function renderTools() {
        el.querySelector('.fp-legend').classList.toggle('hidden', !heatOn())
        tools.classList.toggle('hidden', !st.editing)
        side.classList.toggle('hidden', !st.editing)
        trayBox.classList.toggle('hidden', !st.editing)
        wrap.classList.toggle('view', !st.editing)
        if (!st.editing) return
        const drawing = st.draft && st.draft.kind === 'wall' && st.draft.pts.length
        tools.innerHTML = `
          <div class="fp-toolscroll"><div class="seg">${TOOLS.map(([id, ic, l]) => `<button data-tool="${id}" class="${st.tool === id ? 'active' : ''}">${HO.icon(ic, 'sm')} ${l}</button>`).join('')}</div></div>
          <div class="row wrap g8 fp-toolset ${drawing ? 'drawing' : ''}">${drawing ? `<div class="row g8 fp-drawbar"><button class="btn sm primary" data-act="finish">${HO.icon('check', 'sm')} Fertig</button>
            <button class="btn sm" data-act="undo">Punkt zurück</button><button class="btn sm ghost" data-act="cancel">Abbrechen</button></div>` : ''}
          <span class="row g2"><button class="btn sm" data-act="histundo" ${undoStack.length ? '' : 'disabled'}>${HO.icon('undo', 'sm')}</button><button class="btn sm" data-act="histredo" ${redoStack.length ? '' : 'disabled'}>${HO.icon('redo', 'sm')}</button></span>
          <button class="btn sm ${gridSnap ? 'primary' : ''}" data-act="gridsnap" aria-pressed="${gridSnap}" title="Am Raster einrasten">${HO.icon('grid', 'sm')} Raster</button>
          ${st.plan.walls.length ? `<button class="btn sm" data-act="selall" title="Ganzen Grundriss samt Geräten auswählen (Strg+A)">${HO.icon('maximize', 'sm')} Alles</button>` : ''}
          ${st.plan.walls.length || !HO.roomHub() ? '' : `<button class="btn sm" data-act="autorooms">${HO.icon('wand', 'sm')} Räume aus ${HO.esc(HO.roomHub())}</button>`}
          <button class="btn sm" data-act="autoplace">${HO.icon('wand', 'sm')} Geräte verteilen</button>
          <span class="row fp-size small muted">Fläche <input type="number" min="6" max="80" step="1" value="${st.plan.width}" data-size="width"> ×
            <input type="number" min="4" max="60" step="1" value="${st.plan.height}" data-size="height"></span></div>
          ${floorBarHTML()}`
        const db = devById(), it = selItem()
        const unplaced = st.devices.filter(d => !isPlaced(d.id)).sort((a, b) => HO.byName(a.room + a.name, b.room + b.name))
        let panel = ''
        if (st.newFloor) panel = newFloorHTML()
        else if (st.msel.length) {
          const cnt = t => st.msel.filter(x => x.type === t).length
          const parts = [['room', 'Raum', 'Räume'], ['wall', 'Wand', 'Wände'], ['open', 'Element', 'Elemente'], ['deck', 'Balkon', 'Balkone'], ['stair', 'Treppe', 'Treppen'], ['dev', 'Gerät', 'Geräte'], ['group', 'Gruppe', 'Gruppen']]
            .map(([t, one, many]) => [cnt(t), one, many]).filter(([n]) => n).map(([n, one, many]) => `${n} ${n === 1 ? one : many}`)
          panel = `<div class="fp-panel"><div class="row between"><h4>Auswahl</h4><div class="row">
              <button class="btn sm ghost" data-act="selnone">Aufheben</button>
              <button class="btn sm danger" data-act="delete">${HO.icon('trash', 'sm')} Löschen</button></div></div>
            <p class="small" style="margin:8px 0 0">${parts.join(' · ')}</p>
            <p class="small muted" style="margin:8px 0 0">Im Rahmen ziehen verschiebt alles. Ecken skalieren gleichmäßig (Umschalt = frei), Seitenmitten nur in eine Richtung.
              Gerätesymbole behalten ihre Größe und bleiben an ihrer Stelle im Raum; Geräte in ausgewählten Räumen wandern mit.</p></div>`
        } else if (st.sel && st.sel.type === 'room' && it) {
          const ro = st.rooms.filter(x => x.room_id && !String(x.room_id).startsWith('plan:')).sort((a, b) => HO.byName(a.name, b.name))
          const hub = HO.roomHub()      // who manages the rooms (e.g. "Home Base"); without a hub there is nothing to link
          panel = `<div class="fp-panel"><h4>Raum</h4>
            <label class="field" style="margin-top:10px" ${hub || ro.length ? '' : 'hidden'}><span>Verknüpfter Raum${hub ? ' (' + HO.esc(hub) + ')' : ''}</span>
              <select data-f="link"><option value="">${hub ? '— neu (wird dort angelegt) —' : '— keiner —'}</option>${ro.map(o => {
                const taken = allRooms().some(x => x.id !== it.id && x.room_id === o.room_id)   // on any floor
                return `<option value="${HO.esc(o.room_id)}" ${o.room_id === it.room_id ? 'selected' : ''} ${taken ? 'disabled' : ''}>${HO.esc(o.name)} (${o.devices} Geräte)${taken ? ' – schon verknüpft' : ''}</option>`
              }).join('')}</select>
              <span class="hint">Vorhandenen Raum übernehmen: hier auswählen. Verknüpfte Räume zeigen ihre Durchschnittstemperatur; „Geräte verteilen" setzt deren Geräte hinein.</span></label>
            <label class="field"><span>Name${it.room_id ? ' (gilt in ganz ComfyOS)' : ''}</span><input type="text" data-f="name" maxlength="40" value="${HO.esc(roomLabel(it))}"></label>
            <p class="small muted" style="margin:10px 0 0">Die Form ergibt sich aus den Wänden – zum Ändern eine Wand antippen.</p></div>`
        } else if (st.sel && st.sel.type === 'wall' && it) {
          const bent = it.bend.some(Boolean)
          panel = `<div class="fp-panel"><div class="row between"><h4>Wand</h4><div class="row">
              ${bent ? `<button class="btn sm" data-act="straighten">Begradigen</button>` : ''}
              <button class="btn sm danger" data-act="delete">${HO.icon('trash', 'sm')} Löschen</button></div></div>
            <p class="small muted" style="margin:10px 0 0">Punkte ziehen (verbundene Wände ziehen mit) · Mitte ziehen = Bogen · Mitte doppelt antippen = neuer Punkt · Punkt doppelt antippen = löschen</p></div>`
        } else if (st.sel && st.sel.type === 'stair' && it) {
          panel = `<div class="fp-panel"><div class="row between"><h4>Treppe</h4>
              <button class="btn sm danger" data-act="delete">${HO.icon('trash', 'sm')} Löschen</button></div>
            <div class="row wrap g8" style="margin-top:10px"><span class="small muted">Breite</span>
              <button class="btn sm" data-act="stairwidth" data-v="-1" title="Schmaler" aria-label="Schmaler" ${it.width <= STAIR_MIN ? 'disabled' : ''}>${HO.icon('minus', 'sm')}</button>
              <b class="fp-symsize">${String(it.width).replace('.', ',')}</b>
              <button class="btn sm" data-act="stairwidth" data-v="1" title="Breiter" aria-label="Breiter" ${it.width >= STAIR_MAX ? 'disabled' : ''}>${HO.icon('plus', 'sm')}</button>
              <button class="btn sm" data-act="stairflip" title="Unten und oben tauschen">${HO.icon('flip', 'sm')} Richtung</button></div>
            <p class="small muted" style="margin:10px 0 0">Ziehen verschiebt · Enden ziehen = Länge und Richtung · „Richtung“ tauscht unten und oben.</p>
            <p class="small muted" style="margin:8px 0 0">Im Stockwerk darüber erscheint an derselben Stelle der Abgang. In der Ansicht wechselt Antippen der Treppe das Stockwerk.</p></div>`
        } else if (st.sel && st.sel.type === 'deck' && it) {
          panel = `<div class="fp-panel"><div class="row between"><h4>Balkon / Terrasse</h4><div class="row">
              ${it.bend.some(Boolean) ? `<button class="btn sm" data-act="straighten">Begradigen</button>` : ''}
              <button class="btn sm danger" data-act="delete">${HO.icon('trash', 'sm')} Löschen</button></div></div>
            <div class="field" style="margin-top:10px"><span>Boden</span><div class="seg">${[['', 'Holz'], ['tiles', 'Fliesen'], ['plain', 'Glatt']].map(([key, label]) =>
              `<button data-act="decksurface" data-v="${key}" class="${(it.surface || '') === key ? 'active' : ''}">${label}</button>`).join('')}</div></div>
            <p class="small muted" style="margin:10px 0 0">Fläche ziehen = verschieben · Punkte ziehen (rasten an Wänden ein) · Mitte ziehen = Bogen · Mitte doppelt antippen = neuer Punkt · Punkt doppelt antippen = löschen</p>
            <p class="small muted" style="margin:8px 0 0">Die Fläche liegt außen: Wettereffekte ziehen darüber hinweg, Pfützen bleiben am Boden daneben.</p></div>`
        } else if (st.sel && st.sel.type === 'open' && it) {
          const rad = it.type === 'radiator'
          const here = dockedDevices(it, db), full = dockedIds(it).length >= MAX_DOCKED
          const links = st.devices.filter(d => dockTypes(d).includes(it.type) && !dockedIds(it).includes(d.id)).sort((a, b) => HO.byName(a.room + a.name, b.room + b.name))
          panel = `<div class="fp-panel"><div class="row between"><div class="seg">
              ${['window', 'door', 'passage', 'radiator'].map(k => `<button data-act="otype" data-v="${k}" class="${it.type === k ? 'active' : ''}">${HO.icon(k === 'window' ? 'contact' : k, 'sm')} ${OPEN_LABEL[k]}</button>`).join('')}</div>
              <button class="btn sm danger" data-act="delete">${HO.icon('trash', 'sm')}</button></div>
            ${it.type === 'passage' ? '' : `<div class="row wrap" style="margin-top:10px">
              <button class="btn sm" data-act="oswap" title="Links/rechts spiegeln">${HO.icon('mirror', 'sm')} Spiegeln</button>
              ${it.type === 'window' && roomSideOf(it) ? '' : `<button class="btn sm" data-act="oside" title="Auf die andere Wandseite">${HO.icon('flip', 'sm')} Wandseite</button>`}</div>
            <p class="small muted" style="margin:8px 0 0">Ziehen verschiebt entlang der Wand · über die Wand ziehen = andere Seite · Enden ziehen = Breite</p>`}
            ${it.type === 'passage' ? `<p class="small muted" style="margin:10px 0 0">Ziehen verschiebt entlang der Wand · Enden ziehen = Breite</p>` : `
            <div class="field" style="margin-top:12px"><span>Angedockte Geräte</span>
              <div class="fp-gm">${here.map(d => `<div class="row between"><span class="row">${HO.icon(d.kind, 'sm')}${HO.esc(d.name)}</span>
                <button class="btn sm" data-act="oundock" data-v="${HO.esc(d.id)}">Lösen</button></div>`).join('') || '<span class="small muted">Noch keines.</span>'}</div></div>
            ${full ? `<p class="small muted" style="margin:8px 0 0">Mehr als ${MAX_DOCKED} Geräte passen nicht an ein Element.</p>` : `
            <label class="field"><span>${rad ? 'Thermostat andocken' : 'Kontakt oder Rollladen andocken'}</span><select data-f="odev"><option value="">— wählen —</option>
              ${links.map(d => { const at = floorOf(d.id); return `<option value="${HO.esc(d.id)}">${HO.esc(d.name)} · ${HO.esc(d.room)}${at && at !== st.plan.floor ? ' (steht in „' + HO.esc(floorShort(at)) + '“)' : ''}</option>` }).join('')}</select>
              <span class="hint">${rad ? 'Heizt ein Thermostat, wird der Heizkörper hervorgehoben.' : `Ist ein Kontakt offen, wird ${it.type === 'door' ? 'die Tür' : 'das Fenster'} im Plan markiert.`}
                Mehrere Geräte liegen als Punkte nebeneinander.</span></label>`}`}</div>`
        } else if (st.sel && st.sel.type === 'group' && it) {
          panel = `<div class="fp-panel"><div class="row between"><h4>Gruppe</h4><button class="btn sm" data-act="gdissolve">Auflösen</button></div>
            <label class="field" style="margin-top:10px"><span>Name</span><input type="text" data-f="gname" maxlength="40" placeholder="Gruppe" value="${HO.esc(it.name)}"></label>
            ${symbolPicker(it.icon, 'Geräte anzeigen', HO.icon('grid'), true)}
            <div class="fp-gm">${it.members.map(id => db[id] ? `<div class="row between"><span class="row">${HO.icon(db[id].kind, 'sm')}${HO.esc(db[id].name)}</span>
              <button class="btn sm" data-act="gout" data-v="${HO.esc(id)}">Herauslösen</button></div>` : '').join('')}</div>
            <p class="small muted" style="margin:10px 0 0">Gerät auf die Gruppe ziehen = hinzufügen · Herauslösen = wieder einzeln platzieren.</p></div>`
        } else if (st.sel && st.sel.type === 'dev') {
          const d = db[st.sel.id], entry = st.plan.devices[st.sel.id] || {}, box = d && !dockedTo(d.id) && symbolBox(entry, 1 / st.ppu)
          if (d) panel = `<div class="fp-panel"><div class="row between"><div><b>${HO.esc(d.name)}</b><div class="small muted">${HO.esc(d.room)} · ${HO.esc(HO.kindLabel[d.kind] || '')}</div></div>
            <button class="btn sm" data-act="unplace">Vom Plan nehmen</button></div>
            ${dockedTo(d.id) ? '' : symbolPicker(HO.planSymbols[entry.furniture] ? entry.furniture : '', 'Standard', HO.icon(d.kind), false)}
            ${box ? `<div class="row wrap g8" style="margin-top:10px"><span class="small muted">Größe</span>
              <button class="btn sm" data-act="symsize" data-v="-1" title="Kleiner" aria-label="Kleiner" ${box.size <= SYMBOL_MIN ? 'disabled' : ''}>${HO.icon('minus', 'sm')}</button>
              <b class="fp-symsize">${Math.round(box.size * 100)} %</b>
              <button class="btn sm" data-act="symsize" data-v="1" title="Größer" aria-label="Größer" ${box.size >= SYMBOL_MAX ? 'disabled' : ''}>${HO.icon('plus', 'sm')}</button>
              <button class="btn sm" data-act="symturn" title="Um 45° drehen">${HO.icon('refresh', 'sm')} Drehen</button></div>
              <p class="small muted" style="margin:8px 0 0">Größe auch direkt im Plan: die Ecke des Rahmens ziehen.</p>` : ''}
            ${levels().length > 1 ? `<label class="field" style="margin-top:12px"><span>Stockwerk</span><select data-f="devfloor">${levels().slice().reverse().map(f =>
              `<option value="${HO.esc(f.id)}" ${f === st.plan.floor ? 'selected' : ''}>${HO.esc(floorLabel(f))}</option>`).join('')}</select>
              <span class="hint">Ein anderes Stockwerk wählen = das Gerät zieht dorthin um und steht dort frei an derselben Stelle.</span></label>` : ''}
            ${d.room_pending ? `<p class="small" style="margin:10px 0 0;color:var(--warn)">${HO.esc(HO.roomHub() || 'Hub')}: noch in „${HO.esc(d.hb_room || 'kein Raum')}" – Übertragen unter dem Grundriss.</p>` : ''}
            <p class="small muted" style="margin:10px 0 0">In einen anderen Raum ziehen = Gerät diesem Raum zuordnen.
              ${dockTypes(d).length ? (d.kind === 'thermostat' ? ' Auf einen Heizkörper ziehen = andocken, der Heizkörper strahlt dann in der Heatmap.' : ' Auf ein Fenster oder eine Tür ziehen = andocken – auch neben ein Gerät, das dort schon sitzt.') : ''}
              ${dockedTo(d.id) ? ' <b>Angedockt</b> – das Gerät sitzt als Punkt am Element; zum Lösen den Punkt wegziehen.' : ' Auf ein anderes Gerät ziehen = Gruppe bilden.'}</p></div>`
        } else {
          panel = `${st.tool === 'select' ? floorPanelHTML() : ''}<div class="notice">${HO.icon('info', 'sm')}<div>${HINTS[st.tool]}</div></div>
            ${st.tool === 'select' && st.plan.rooms.length ? (() => {
              const linked = new Set(allRooms().map(r => r.room_id).filter(Boolean))
              const missing = st.rooms.filter(r => r.room_id && !String(r.room_id).startsWith('plan:') && !linked.has(r.room_id))
              return missing.length ? `<div class="notice" style="margin-top:10px">${HO.icon('map', 'sm')}<div>Noch nicht im Grundriss: <b>${missing.map(r => HO.esc(r.name)).join(', ')}</b>. Raum im Plan antippen und unter „Verknüpfter Raum" auswählen.</div></div>` : ''
            })() : ''}
            ${st.tool === 'select' ? `<label class="check" style="margin-top:12px"><input type="checkbox" data-f="heatmap" ${st.plan.heatmap ? 'checked' : ''}>
              <span>Heatmap immer einblenden <span class="muted small">(Schein um die platzierten Thermostate)</span></span></label>
              <label class="check" style="margin-top:10px"><input type="checkbox" data-f="show_names" ${st.plan.show_names ? 'checked' : ''}><span>Gerätenamen anzeigen</span></label>` : ''}
            ${st.tool === 'select' ? `<label class="field" style="margin-top:12px"><span>Name des Zuhauses</span><input type="text" data-f="home_name" maxlength="40" placeholder="${HOME_DEFAULT}" value="${HO.esc(st.plan.home_name)}">
              <span class="hint">Steht oben links im Grundriss, daneben die Durchschnittstemperatur.</span></label>
              <label class="field" style="margin-top:12px"><span>Adresse</span><input type="text" data-f="address" maxlength="120" placeholder="Straße, Ort" value="${HO.esc(st.plan.address)}">
              <span class="hint">Wird auf der Startseite unter dem Grundriss angezeigt.</span></label>
              <label class="check" style="margin-top:10px"><input type="checkbox" data-f="weather" ${st.plan.weather !== false ? 'checked' : ''}>
                <span>Außentemperatur und Wetter anzeigen <span class="muted small">(lädt aus dem Internet von Open-Meteo; übertragen wird nur PLZ/Ort, nie die Straße. Der gefundene Ort dient auch für Sonnenzeiten in Flows)</span></span></label>
              <label class="check" style="margin-top:10px"><input type="checkbox" data-f="warnings" ${st.plan.warnings !== false ? 'checked' : ''} ${st.plan.weather === false ? 'disabled' : ''}>
                <span>Amtliche Wetterwarnungen anzeigen <span class="muted small">(lädt aus dem Internet vom Deutschen Wetterdienst; übertragen werden nur die auf ca. 1 km gerundeten Koordinaten des Orts. Warnt auf der Startseite und erinnert an offene Fenster – nur für Orte in Deutschland)</span></span></label>
              ${fxSettingsHTML()}
              <label class="check" style="margin-top:12px"><input type="checkbox" data-f="shadow" ${st.plan.shadow ? 'checked' : ''}>
                <span>Schatten nach Sonnenstand <span class="muted small">(wird im Gerät berechnet, ohne Internet)</span></span></label>
              <label class="field" style="margin-top:10px"><span>Norden im Grundriss · <b data-north-val>${st.plan.north}°</b></span>
                <div class="row"><svg class="fp-compass" viewBox="-12 -12 24 24" style="transform:rotate(${st.plan.north}deg)" aria-hidden="true">
                  <circle r="11"/><path d="M0 -9L4 4L0 1.5L-4 4Z"/></svg>
                  <input type="range" class="grow" min="0" max="355" step="5" value="${st.plan.north}" data-f="north" aria-label="Richtung Norden"></div>
                <span class="hint">Pfeil auf Norden drehen (Handy-Kompass oder Karte). Danach fällt der Schatten in die richtige Richtung.</span></label>` : ''}`
        }
        side.innerHTML = panel
        // bar right below the plan (one line, swipe sideways – like the nodes in the flow editor)
        const scrolled = (trayBox.querySelector('.chips') || {}).scrollLeft || 0
        trayBox.innerHTML = `<div class="row between fp-tray-head"><h4>Nicht platziert (${unplaced.length})</h4>
          ${st.armed ? `<span class="badge accent">Jetzt auf den Plan tippen</span>` : unplaced.length ? `<span class="small muted">antippen oder auf den Plan ziehen</span>` : ''}</div>
          <div class="chips scroll">${unplaced.map(d => `<span class="chip ${st.armed === d.id ? 'armed' : ''}" data-arm="${HO.esc(d.id)}">${HO.icon(d.kind, 'sm')}${HO.esc(d.name)} <span class="muted">· ${HO.esc(d.room)}</span></span>`).join('') || '<span class="muted small">Alle Geräte sind platziert.</span>'}</div>`
        const row = trayBox.querySelector('.chips')
        if (row && scrolled) row.scrollLeft = scrolled
      }
      const render = () => { renderSVG(); renderTools() }

      /* ---------- Snapping: grid, wall points, walls */
      function toPlan(e) {
        const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY
        const p = pt.matrixTransform(svg.getScreenCTM().inverse())
        return [clamp(p.x, 0, st.plan.width), clamp(p.y, 0, st.plan.height)]
      }
      const clampPt = q => [r3(clamp(q[0], 0, st.plan.width)), r3(clamp(q[1], 0, st.plan.height))]
      // All straight wall pieces – what windows, doors and radiators can sit on (`walls`: of another floor, for snapping).
      function straightEdges(skip, walls = st.plan.walls) {
        const out = []
        for (const w of walls) for (let i = 0; i < w.pts.length - 1; i++) {
          if (w.bend[i] || (skip && skip.some(s => s.w === w && (s.i === i || s.i === i + 1)))) continue
          out.push({ a: w.pts[i], b: w.pts[i + 1], w, i })
        }
        return out
      }
      // skip: list {w, i} of points being dragged right now (do not snap to itself)
      function snapPt(p, { from = null, skip = null, free = false } = {}) {
        // the neighbouring floor that shines through: its corners and walls catch points too (can be switched off)
        const near = floorSnap && !free ? neighbour() : null
        if (!free) {
          let best = null, bd = VSNAP
          for (const w of st.plan.walls.concat(st.plan.decks)) w.pts.forEach((q, i) => {
            if (skip && skip.some(s => s.w === w && s.i === i)) return
            const d = dist(p, q); if (d < bd) { bd = d; best = q }
          })
          if (near) for (const w of near.walls.concat(near.decks)) for (const q of w.pts) { const d = dist(p, q); if (d < bd) { bd = d; best = q } }
          if (st.draft && st.draft.pts) st.draft.pts.forEach(q => { const d = dist(p, q); if (d < bd) { bd = d; best = q } })
          if (best) return [...best]
        }
        if (from) {   // 45°-Schritte
          const dx = p[0] - from[0], dy = p[1] - from[1], len = Math.hypot(dx, dy), ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4)
          return clampPt([snap(from[0] + Math.cos(ang) * len), snap(from[1] + Math.sin(ang) * len)])
        }
        if (!free) {   // onto a wall (T junction): grid point on the wall, otherwise the foot of the perpendicular
          const g = clampPt([snap(p[0]), snap(p[1])])
          let best = null
          for (const e of straightEdges(skip).concat(near ? straightEdges(null, near.walls) : [])) {
            const pr = project(p, e.a, e.b)
            if (pr.d < WSNAP && (!best || pr.d < best.d)) best = { ...pr, e }
          }
          if (best) return project(g, best.e.a, best.e.b).d < 0.02 ? g : clampPt(best.q)
          return g
        }
        return clampPt([snap(p[0]), snap(p[1])])
      }
      // The straight wall piece closest to the point (within the snapping distance).
      function nearestEdge(p) {
        let best = null
        for (const e of straightEdges()) {
          const pr = project(p, e.a, e.b)
          if (pr.d < ESNAP && (!best || pr.d < best.d)) best = { ...e, ...pr }
        }
        return best
      }
      function onEdge(e, t) {   // point on a wall (grid along the wall)
        const L = dist(e.a, e.b), s = clamp(snap(t * L), 0, L)
        return clampPt([e.a[0] + (e.b[0] - e.a[0]) * s / L, e.a[1] + (e.b[1] - e.a[1]) * s / L])
      }
      function edgeSnap(p) {
        const e = nearestEdge(p)
        return e ? { p: onEdge(e, e.t), edge: { a: e.a, b: e.b } } : { p: snapPt(p, { free: true }), edge: null }
      }
      // side of a wall on which a room lies (doors open inwards, radiators sit inside)
      function roomSide(a, b) {
        const L = dist(a, b) || 1, m = midOf(a, b), n = [-(b[1] - a[1]) / L, (b[0] - a[0]) / L]
        const probe = k => [m[0] + n[0] * k, m[1] + n[1] * k]
        const fs = faces(), inA = fs.some(f => inside(f.poly, probe(0.4))), inB = fs.some(f => inside(f.poly, probe(-0.4)))
        return inA || !inB ? 1 : -1
      }

      // doors/windows/radiators remember their wall edge and move along when it is moved
      function attachOpenings() {
        const out = []
        for (const o of st.plan.openings) {
          const m = midOf(o.a, o.b)
          for (const e of straightEdges()) {
            if (project(o.a, e.a, e.b).d > 0.05 || project(o.b, e.a, e.b).d > 0.05) continue
            const L = dist(e.a, e.b) || 1, dir = [(e.b[0] - e.a[0]) / L, (e.b[1] - e.a[1]) / L]
            out.push({ o, w: e.w, i: e.i, t: project(m, e.a, e.b).t, len: dist(o.a, o.b), sgn: ((o.b[0] - o.a[0]) * dir[0] + (o.b[1] - o.a[1]) * dir[1]) >= 0 ? 1 : -1 })
            break
          }
        }
        return out
      }
      // Take openings along when their wall moves: they keep position and length on the wall piece.
      function carryOpenings(att) {
        for (const c of att) {
          const a = c.w.pts[c.i], b = c.w.pts[c.i + 1]
          if (!a || !b) continue
          const L = dist(a, b) || 1, dir = [(b[0] - a[0]) / L, (b[1] - a[1]) / L], m = [a[0] + (b[0] - a[0]) * c.t, a[1] + (b[1] - a[1]) * c.t]
          const h = Math.min(c.len, L) / 2 * c.sgn
          c.o.a = clampPt([m[0] - dir[0] * h, m[1] - dir[1] * h]); c.o.b = clampPt([m[0] + dir[0] * h, m[1] + dir[1] * h])
        }
      }
      // all wall points at the same place (corners/junctions) – are dragged together
      const jointAt = q => st.plan.walls.flatMap(w => w.pts.map((p, i) => dist(p, q) < EPS * 5 ? { w, i } : null).filter(Boolean))

      /* ---------- Bearbeitungsaktionen */
      function finishDraft(close) {
        const d = st.draft
        st.draft = null
        if (d && d.kind === 'wall' && d.deck) {
          // a balcony or terrace is always a closed area: three corners at least, the outline closes by itself
          if (d.pts.length < 3) { HO.toast('Ein Balkon braucht mindestens drei Ecken.', 'warn'); return render() }
          const deck = { id: uid('d'), pts: [...d.pts, [...d.pts[0]]], bend: d.pts.map(() => null) }
          st.plan.decks.push(deck)
          st.tool = 'select'; st.sel = { type: 'deck', id: deck.id }
          save()
          return render()
        }
        if (d && d.kind === 'wall') {
          const pts = close ? [...d.pts, [...d.pts[0]]] : d.pts
          if (pts.length >= 2) {
            const w = { id: uid('w'), pts, bend: pts.slice(1).map(() => null) }
            st.plan.walls.push(w)
            return changed()
          }
        }
        render()
      }
      // Set the next point of a new wall; a double tap or tapping the start closes the run.
      function addDraftPoint(p, e) {
        if (!st.draft || st.draft.kind !== 'wall') st.draft = { kind: 'wall', deck: st.tool === 'deck', pts: [], cur: null, lastAt: 0 }
        const d = st.draft, last = d.pts[d.pts.length - 1]
        const q = snapPt(p, { from: e.shiftKey && last ? last : null })
        const now = Date.now()
        if (last && now - d.lastAt < 380 && dist(q, last) < 0.3) return finishDraft()           // Doppeltippen
        if (d.pts.length >= 3 && dist(q, d.pts[0]) < VSNAP + 0.05) return finishDraft(true)     // close the run
        if (last && dist(q, last) < 0.05) return
        d.pts.push(q); d.cur = null; d.lastAt = now
        render()
      }
      // Delete a corner point of a wall; if less than a segment is left, the whole wall goes.
      function deleteVertex(w, i) {
        if (w.pts.length <= 2) return deleteSelection()
        const n = w.pts.length
        w.pts.splice(i, 1)
        if (i === 0) w.bend.splice(0, 1)
        else if (i === n - 1) w.bend.splice(n - 2, 1)
        else { w.bend.splice(i, 1); w.bend[i - 1] = null }
        changed()
      }
      // The points of a deck at corner i: the corner itself, and for the first corner the closing point at the end too.
      const deckJoint = (deck, i) => i === 0 || i === deck.pts.length - 1 ? [{ w: deck, i: 0 }, { w: deck, i: deck.pts.length - 1 }] : [{ w: deck, i }]
      // Delete a corner of a deck; the outline stays closed. With three corners left the whole deck goes.
      function deleteDeckVertex(deck, i) {
        const n = deck.pts.length
        if (n <= 4) return deleteSelection()
        if (i === 0 || i === n - 1) {
          deck.pts.pop(); deck.bend.pop()
          deck.pts.shift(); deck.bend.shift()
          deck.pts.push([...deck.pts[0]]); deck.bend.push(null)
        } else { deck.pts.splice(i, 1); deck.bend.splice(i, 1); deck.bend[i - 1] = null }
        save(); render()
      }
      // Tapping the middle of a wall piece: a bend becomes straight again, a straight piece gets a new
      // corner point.
      function midAction(w, i) {
        const [a, b, c] = edgeOf(w, i)
        if (c) w.bend[i] = null
        else { w.pts.splice(i + 1, 0, clampPt(midOf(a, b, null))); w.bend.splice(i + 1, 0, null) }
        changed()
      }
      // Was this the second tap on the same element within 380 ms?
      function doubleTap(key) {
        const now = Date.now(), l = st.lastTap
        st.lastTap = { key, t: now }
        return !!(l && l.key === key && now - l.t < 380)
      }
      /* ---------- Multi-selection: move and scale.
         With a room or wall all wall points at the same place move along (rooms stay closed), doors/windows on
         fully moved edges are scaled along, on partly moved ones carried along. Device symbols never scale – only their
         position in the room (selected devices and all free devices/groups in selected rooms). */
      const vkey = (w, i) => w.id + ':' + i
      // Select everything: walls, rooms, openings, groups and free-standing devices.
      function selectAll() {
        st.sel = null
        st.msel = [...st.plan.walls.map(w => ({ type: 'wall', id: w.id })), ...st.plan.decks.map(d => ({ type: 'deck', id: d.id })),
          ...st.plan.stairs.map(t => ({ type: 'stair', id: t.id })),
          ...st.plan.rooms.map(r => ({ type: 'room', id: r.id })),
          ...st.plan.openings.map(o => ({ type: 'open', id: o.id })), ...st.plan.groups.map(g => ({ type: 'group', id: g.id })),
          ...Object.keys(st.plan.devices).filter(id => !dockedTo(id)).map(id => ({ type: 'dev', id }))]
        render()
      }
      // Add an element to the multi-selection or remove it from it (Shift/Ctrl+click).
      function toggleSel(type, id) {
        if (!st.msel.length && st.sel) st.msel = [{ ...st.sel }]
        st.sel = null
        const i = st.msel.findIndex(x => x.type === type && x.id === id)
        if (i >= 0) st.msel.splice(i, 1); else st.msel.push({ type, id })
        if (st.msel.length === 1) { st.sel = st.msel[0]; st.msel = [] }
        render()
      }
      // Select everything that lies entirely in the dragged frame.
      function selectRect(a, b) {
        const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1])
        const inR = p => p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1
        const out = [
          ...st.plan.walls.filter(w => w.pts.every(inR)).map(w => ({ type: 'wall', id: w.id })),
          ...st.plan.decks.filter(d => d.pts.every(inR)).map(d => ({ type: 'deck', id: d.id })),
          ...st.plan.stairs.filter(t => inR(t.a) && inR(t.b)).map(t => ({ type: 'stair', id: t.id })),
          ...layout().filter(it => it.room && it.face.poly.every(inR)).map(it => ({ type: 'room', id: it.room.id })),
          ...st.plan.openings.filter(o => inR(o.a) && inR(o.b)).map(o => ({ type: 'open', id: o.id })),
          ...st.plan.groups.filter(g => inR([g.x, g.y])).map(g => ({ type: 'group', id: g.id })),
          ...Object.entries(st.plan.devices).filter(([id, q]) => !dockedTo(id) && inR([q.x, q.y])).map(([id]) => ({ type: 'dev', id }))]
        st.sel = out.length === 1 ? out[0] : null
        st.msel = out.length > 1 ? out : []
      }
      // What moves with the multi-selection: corner points, bends, openings, devices and groups – also the walls
      // of selected rooms.
      function mselTargets() {
        const has = t => new Set(st.msel.filter(x => x.type === t).map(x => x.id))
        const ws = has('wall'), rs = has('room'), os = has('open'), ds = has('dev'), gs = has('group'), ks = has('deck')
        const V = new Map(), add = (w, i) => V.set(vkey(w, i), { w, i })
        for (const w of st.plan.walls) if (ws.has(w.id)) w.pts.forEach((_, i) => add(w, i))
        const lay = layout(), selFaces = lay.filter(it => it.room && rs.has(it.room.id)).map(it => it.face.poly)
        for (const poly of selFaces) for (const w of st.plan.walls) w.pts.forEach((q, i) => { if (poly.some(p => dist(p, q) < EPS * 5)) add(w, i) })
        for (const v of [...V.values()]) for (const j of jointAt(v.w.pts[v.i])) add(j.w, j.i)
        // selected decks move as a whole; they take no wall along (added after the joints of the walls)
        for (const d of st.plan.decks) if (ks.has(d.id)) d.pts.forEach((_, i) => add(d, i))
        const bends = []
        for (const w of st.plan.walls.concat(st.plan.decks)) w.bend.forEach((c, i) => { if (c && V.has(vkey(w, i)) && V.has(vkey(w, i + 1))) bends.push({ w, i }) })
        const opAff = new Set(), carry = []
        for (const c of attachOpenings()) {
          const a = V.has(vkey(c.w, c.i)), b = V.has(vkey(c.w, c.i + 1))
          if (a && b) opAff.add(c.o); else if (a || b) carry.push(c)
        }
        for (const o of st.plan.openings) if (os.has(o.id) && !carry.some(c => c.o === o)) opAff.add(o)
        const inSel = p => selFaces.some(poly => inside(poly, p))
        const devs = Object.keys(st.plan.devices).filter(id => !dockedTo(id) && (ds.has(id) || inSel([st.plan.devices[id].x, st.plan.devices[id].y])))
        const groups = st.plan.groups.filter(g => gs.has(g.id) || inSel([g.x, g.y]))
        // stairs: selected ones and those standing in a selected room
        const picked = has('stair'), stairs = st.plan.stairs.filter(t => picked.has(t.id) || inSel(midOf(t.a, t.b)))
        // fasten room anchors to the corners that move (along) – otherwise a deformed neighbouring room loses name and link
        const roomRefs = lay.filter(it => it.room).map(it => ({ room: it.room, refs: it.face.poly.map(p => jointAt(p)[0]).filter(Boolean) }))
        return { V: [...V.values()], bends, opAff: [...opAff], carry, devs, groups, stairs, roomRefs }
      }
      // Frame around everything that belongs to the multi-selection – or null.
      function mselBox(T) {
        const pts = [...T.V.map(v => v.w.pts[v.i]), ...T.opAff.flatMap(o => [o.a, o.b]),
          ...T.devs.map(id => [st.plan.devices[id].x, st.plan.devices[id].y]), ...T.groups.map(g => [g.x, g.y]),
          ...T.stairs.reduce((list, t) => list.concat([t.a, t.b]), [])]
        return pts.length ? bbox(pts) : null
      }
      // Start moving or scaling the multi-selection: remember the starting position of all affected points.
      function mselStart(mode, p, extra = {}) {
        const T = mselTargets(), box = mselBox(T)
        if (!box) return false
        const O = { V: T.V.map(v => [...v.w.pts[v.i]]), bends: T.bends.map(b => [...b.w.bend[b.i]]), op: T.opAff.map(o => ({ a: [...o.a], b: [...o.b] })),
          dev: T.devs.map(id => [st.plan.devices[id].x, st.plan.devices[id].y]), grp: T.groups.map(g => [g.x, g.y]),
          stair: T.stairs.map(t => ({ a: [...t.a], b: [...t.b] })) }
        st.drag = { mode, T, O, box, start: p, moved: false, ...extra }
        return true
      }
      // Recompute the multi-selection from its starting position (move, scale) – everything stays in the plan.
      function mselApply(d, f) {
        const { T, O } = d
        T.V.forEach((v, k) => { v.w.pts[v.i] = clampPt(f(O.V[k])) })
        T.bends.forEach((b, k) => { b.w.bend[b.i] = clampPt(f(O.bends[k])) })
        T.opAff.forEach((o, k) => { o.a = clampPt(f(O.op[k].a)); o.b = clampPt(f(O.op[k].b)) })
        carryOpenings(T.carry)
        T.devs.forEach((id, k) => { const q = clampPt(f(O.dev[k])); st.plan.devices[id] = { ...st.plan.devices[id], x: q[0], y: q[1] } })
        T.groups.forEach((g, k) => { const q = clampPt(f(O.grp[k])); g.x = q[0]; g.y = q[1] })
        T.stairs.forEach((t, k) => { t.a = clampPt(f(O.stair[k].a)); t.b = clampPt(f(O.stair[k].b)) })
        for (const r of T.roomRefs) {
          const poly = r.refs.map(v => v.w.pts[v.i])
          if (poly.length >= 3) { const c = labelPoint(poly); r.room.x = r3(c[0]); r.room.y = r3(c[1]) }
        }
      }
      // drag a corner (nw/ne/sw/se) or a side centre (n/e/s/w); the opposite side stays, never beyond the edge of the plan
      function mselScale(d, p, free) {
        const b = d.box, c = d.c, W = st.plan.width, H = st.plan.height
        const hx = c.includes('e') || c.includes('w'), hy = c.includes('n') || c.includes('s')
        const ax = c.includes('w') ? b.x + b.w : b.x, ay = c.includes('n') ? b.y + b.h : b.y
        const cx = c.includes('w') ? b.x : b.x + b.w, cy = c.includes('n') ? b.y : b.y + b.h
        let q = [p[0] - d.off[0], p[1] - d.off[1]]
        if (gridSnap) q = [snap(q[0]), snap(q[1])]
        const spanX = cx - ax, spanY = cy - ay
        let sx = hx && Math.abs(spanX) > 1e-6 ? (q[0] - ax) / spanX : 1, sy = hy && Math.abs(spanY) > 1e-6 ? (q[1] - ay) / spanY : 1
        if (hx && hy && !free) {   // uniform: projection onto the diagonal
          const L2 = spanX * spanX + spanY * spanY
          sx = sy = L2 > 1e-9 ? ((q[0] - ax) * spanX + (q[1] - ay) * spanY) / L2 : 1
        }
        const limX = !hx || Math.abs(spanX) < 1e-6 ? Infinity : spanX > 0 ? (W - ax) / spanX : ax / -spanX
        const limY = !hy || Math.abs(spanY) < 1e-6 ? Infinity : spanY > 0 ? (H - ay) / spanY : ay / -spanY
        if (hx && hy && !free) sx = sy = clamp(sx, 0.1, Math.min(limX, limY))
        else { sx = clamp(sx, 0.1, limX); sy = clamp(sy, 0.1, limY) }
        mselApply(d, o => [ax + (o[0] - ax) * sx, ay + (o[1] - ay) * sy])
        d.moved = true
      }
      // Move the multi-selection, aligned to the grid and never beyond the edge of the plan.
      function mselMove(d, p) {
        const b = d.box, W = st.plan.width, H = st.plan.height
        const dx = clamp(snap(p[0] - d.start[0]), -b.x, W - b.x - b.w), dy = clamp(snap(p[1] - d.start[1]), -b.y, H - b.y - b.h)
        mselApply(d, o => [o[0] + dx, o[1] + dy])
        d.moved = d.moved || dx !== 0 || dy !== 0
      }
      // Delete everything selected; groups are dissolved.
      function deleteMulti() {
        const has = t => new Set(st.msel.filter(x => x.type === t).map(x => x.id))
        const ws = has('wall'), os = has('open'), ds = has('dev'), gs = has('group'), ks = has('deck')
        st.plan.walls = st.plan.walls.filter(w => !ws.has(w.id))
        st.plan.decks = st.plan.decks.filter(d => !ks.has(d.id))
        const gone = has('stair')
        st.plan.stairs = st.plan.stairs.filter(t => !gone.has(t.id))
        st.plan.openings = st.plan.openings.filter(o => !os.has(o.id))
        for (const id of ds) delete st.plan.devices[id]
        for (const id of gs) dissolve(id)
        st.msel = []; st.sel = null
        changed()
      }

      // Delete the selected element. Rooms cannot be deleted – they arise from walls.
      function deleteSelection() {
        if (st.msel.length) return deleteMulti()
        if (!st.sel) return
        if (st.sel.type === 'dev') delete st.plan.devices[st.sel.id]
        else if (st.sel.type === 'group') { dissolve(st.sel.id); save(); return render() }
        else if (st.sel.type === 'room') return HO.toast('Räume entstehen aus Wänden – zum Entfernen eine Wand löschen.', 'warn')
        else {
          const key = st.sel.type === 'wall' ? 'walls' : st.sel.type === 'deck' ? 'decks' : st.sel.type === 'stair' ? 'stairs' : 'openings'
          st.plan[key] = st.plan[key].filter(x => x.id !== st.sel.id)
        }
        st.sel = null
        changed()
      }

      /* ---------- Zeiger */
      svg.addEventListener('pointerdown', e => {
        const p = toPlan(e), target = e.target.closest('[data-kind]')
        const kind = target && target.dataset.kind
        if (!st.editing) {
          // view: only trigger on release (viewTap) – swiping/scrolling over the plan no longer opens anything
          if (cam && !still) return
          // second finger during a tap = gesture, not a tap
          const now = Date.now(), multi = st.tap && st.tap.id !== e.pointerId && now - st.tap.t < TAP_MS
          st.tap = multi ? null : { id: e.pointerId, x: e.clientX, y: e.clientY, t: now, target }
          return   // panning and zooming: camera gestures at the frame (further down)
        }
        e.preventDefault()
        if (st.armed) { const id = st.armed; st.armed = null; return placeDevice(id, p) }
        const tool = st.tool
        if (tool === 'wall' || tool === 'deck') {
          // The first point swaps the toolbar ("Fertig", "Abbrechen") and the plan shifts under the finger: the click
          // that belongs to this tap would then land on whatever lies there now – a tool, a device of the bar below –
          // and end the draft. It is swallowed.
          st.suppressClick = true
          setTimeout(() => { st.suppressClick = false }, 400)
          return addDraftPoint(p, e)
        }
        svg.setPointerCapture(e.pointerId)
        if (tool === 'stair' && !['stair', 'send'].includes(kind)) {   // drag from the lowest to the highest step
          const foot = snapPt(p, { free: true })
          st.draft = { kind: 'stair', a: foot, b: foot }; st.drag = { mode: 'stair-new' }; return
        }
        if (OPEN_LABEL[tool] && !['oend', 'open', 'dev'].includes(kind)) {   // on an element/a handle: edit instead of new
          const s = edgeSnap(p)
          st.draft = { kind: 'open', type: tool, a: s.p, b: s.p, edge: s.edge }; st.drag = { mode: 'open-new' }; return
        }
        const mod = e.shiftKey || e.ctrlKey || e.metaKey
        if (st.msel.length) {
          if (kind === 'mscale') {
            const c = target.dataset.c
            if (mselStart('mscale', p, { c })) {
              const b = st.drag.box
              st.drag.off = [p[0] - (c.includes('w') ? b.x : c.includes('e') ? b.x + b.w : p[0]),
                p[1] - (c.includes('n') ? b.y : c.includes('s') ? b.y + b.h : p[1])]
            }
            return
          }
          const b = st.mbox, pad = 10 / st.ppu
          if (!mod && b && p[0] >= b.x - pad && p[0] <= b.x + b.w + pad && p[1] >= b.y - pad && p[1] <= b.y + b.h + pad) { mselStart('mmove', p); return }
          if (!mod) st.msel = []   // outside the frame: lift the selection, then as usual
        }
        if (mod && ['wall', 'open', 'dev', 'group', 'room', 'deck', 'stair'].includes(kind) && target.dataset.id) return toggleSel(kind, target.dataset.id)
        // selecting
        const it = selItem()
        if ((kind === 'vtx' || kind === 'mid') && it) {
          const i = +target.dataset.i
          const isDeck = st.sel.type === 'deck'
          if (doubleTap(kind + i + it.id)) return kind !== 'vtx' ? midAction(it, i) : isDeck ? deleteDeckVertex(it, i) : deleteVertex(it, i)
          // a corner of a deck moves alone (with its closing point), a corner of a wall takes the connected walls along
          const joint = kind !== 'vtx' ? [] : isDeck ? deckJoint(it, i) : jointAt(it.pts[i])
          st.drag = { mode: kind, it, i, joint, att: attachOpenings(), moved: false }; return
        }
        if (kind === 'oend' && it) { st.drag = { mode: 'oend', it, end: target.dataset.end, moved: false }; return }
        if (kind === 'send' && it) { st.drag = { mode: 'send', it, end: target.dataset.end, moved: false }; return }
        if (kind === 'stair') {
          const stair = st.plan.stairs.find(x => x.id === target.dataset.id)
          st.sel = { type: 'stair', id: stair.id }
          st.drag = { mode: 'smove', it: stair, start: p, orig: { a: [...stair.a], b: [...stair.b] }, moved: false }
          return render()
        }
        if (kind === 'symscale' && st.sel && st.sel.type === 'dev' && st.plan.devices[st.sel.id]) {
          // corner of the frame of a plan symbol: the distance to its centre sets the size
          const e = st.plan.devices[st.sel.id]
          st.drag = { mode: 'symscale', id: st.sel.id, size: clamp(Number(e.size) || 1, SYMBOL_MIN, SYMBOL_MAX), reach: Math.max(dist(p, [e.x, e.y]), EPS), moved: false }
          return
        }
        if (kind === 'dev') {
          const id = target.dataset.id, o = dockedTo(id), dp = o && st.dockPos && st.dockPos.get(id)
          const pos = dp ? { x: dp.p[0], y: dp.p[1] } : st.plan.devices[id]
          st.sel = { type: 'dev', id }
          st.drag = { mode: 'dev', id, off: [p[0] - pos.x, p[1] - pos.y], moved: false, dock: o || null }
          return render()
        }
        if (kind === 'group') {
          const g = groupById(target.dataset.id)
          st.sel = { type: 'group', id: g.id }
          st.drag = { mode: 'gmove', it: g, off: [p[0] - g.x, p[1] - g.y], moved: false }
          return render()
        }
        if (kind === 'wall') {
          const w = st.plan.walls.find(x => x.id === target.dataset.id)
          st.sel = { type: 'wall', id: w.id }
          // connected points of other walls move along, so that rooms stay closed
          const joint = [...new Map(w.pts.flatMap(q => jointAt(q)).map(j => [j.w.id + ':' + j.i, j])).values()]
          st.drag = { mode: 'move', it: w, start: p, joint, orig: joint.map(j => [...j.w.pts[j.i]]), bend: clone(w.bend), att: attachOpenings(),
            bb: bbox(joint.map(j => j.w.pts[j.i])), moved: false }
          return render()
        }
        if (kind === 'deck') {
          // a balcony or terrace moves as a whole – like a wall, only that nothing is connected to it
          const deck = st.plan.decks.find(x => x.id === target.dataset.id)
          const joint = deck.pts.map((_, i) => ({ w: deck, i }))
          st.sel = { type: 'deck', id: deck.id }
          st.drag = { mode: 'move', it: deck, start: p, joint, orig: joint.map(j => [...j.w.pts[j.i]]), bend: clone(deck.bend), att: [],
            bb: bbox(deck.pts), moved: false }
          return render()
        }
        if (kind === 'open') {
          const o = st.plan.openings.find(x => x.id === target.dataset.id)
          st.sel = { type: 'open', id: o.id }
          st.drag = { mode: 'omove', it: o, start: p, orig: { a: [...o.a], b: [...o.b] }, moved: false }
          return render()
        }
        // room or empty area: dragging a frame = multi-selection; only tapping = choose the room or lift the selection
        st.drag = { mode: 'marquee', start: p, cur: p, room: kind === 'room' ? target.dataset.id || null : null, add: mod, moved: false }
      })
      // Set the selection: one element becomes the single selection, several the multi-selection (without duplicates).
      function setSelection(list) {
        const uniq = [...new Map(list.map(x => [x.type + ':' + x.id, x])).values()]
        st.sel = uniq.length === 1 ? uniq[0] : null
        st.msel = uniq.length > 1 ? uniq : []
      }

      // tapping in the view: device → details, window/door/radiator → linked device, room → room, otherwise background
      function viewTap(target) {
        const kind = target && target.dataset.kind
        // stairs change the floor: a staircase leads up, the stairwell of the floor below leads down
        if (kind === 'stair' || kind === 'stairwell') { stepFloor(kind === 'stair' ? 1 : -1); return }
        if (kind === 'dev') { const d = devById()[target.dataset.id]; if (d && opts.onDeviceClick) return opts.onDeviceClick(d) }
        else if (kind === 'open') {
          // one docked device: its details; several: all of them together, like a group
          const o = st.plan.openings.find(x => x.id === target.dataset.id), list = o ? dockedDevices(o, devById()) : []
          if (list.length > 1 && opts.onGroupClick) return opts.onGroupClick({ id: 'open:' + o.id, name: OPEN_LABEL[o.type], members: list.map(d => d.id) }, list)
          if (list.length && opts.onDeviceClick) return opts.onDeviceClick(list[0])
        } else if (kind === 'group') {
          const g = groupById(target.dataset.id)
          if (g && opts.onGroupClick) return opts.onGroupClick(g, g.members.map(id => devById()[id]).filter(Boolean))
        } else if (kind === 'room') {
          const r = st.plan.rooms.find(x => x.id === target.dataset.id)
          if (r) {
            setFocus(r.id)
            if (opts.onRoomClick) return opts.onRoomClick({ ...r, key: roomKey(r), name: roomLabel(r) }, roomById()[roomKey(r)])
            return
          }
        }
        setFocus(null)
        if (opts.onBackgroundClick) opts.onBackgroundClick()
      }
      const TAP_MOVE = 8, TAP_MS = 650
      // A valid tap is noted on release and only executed with the following click –
      // that way the click does not hit the dialog backdrop that was just opened (iOS) and close it again.
      const armTap = target => { st.tapHit = { target, at: Date.now() } }
      svg.addEventListener('pointerup', e => {
        const tp = st.tap; st.tap = null
        if (!st.editing && (!cam || still) && tp && tp.id === e.pointerId && Date.now() - tp.t < TAP_MS) armTap(tp.target)
      })
      svg.addEventListener('pointercancel', () => { st.tap = null })   // the browser scrolls the page → not a tap
      wrap.addEventListener('click', () => {
        const h = st.tapHit; st.tapHit = null
        if (h && !st.editing && Date.now() - h.at < 700) viewTap(h.target)
      })

      svg.addEventListener('pointermove', e => {
        if (!st.editing) {
          if (st.tap && st.tap.id === e.pointerId && Math.hypot(e.clientX - st.tap.x, e.clientY - st.tap.y) > TAP_MOVE) st.tap = null
          return live ? undefined : hover(e)
        }
        const p = toPlan(e)
        if ((st.tool === 'wall' || st.tool === 'deck') && st.draft && st.draft.pts && st.draft.pts.length) {
          const last = st.draft.pts[st.draft.pts.length - 1]
          st.draft.cur = snapPt(p, { from: e.shiftKey ? last : null })
          return renderSVG()
        }
        const d = st.drag
        if (!d) return
        if (d.mode === 'marquee') {
          d.cur = p; d.moved = d.moved || dist(p, d.start) * st.ppu > 6
          if (d.moved) renderSVG()
          return
        }
        if (d.mode === 'mmove') { mselMove(d, p); return renderSVG() }
        if (d.mode === 'mscale') { mselScale(d, p, e.shiftKey); return renderSVG() }
        const W = st.plan.width, H = st.plan.height
        if (d.mode === 'stair-new') { st.draft.b = snapPt(p, { free: true }); return renderSVG() }
        if (d.mode === 'send') {
          // an end of a staircase moves freely: length and direction; it never gets shorter than a step and a half
          const q = snapPt(p, { free: true })
          if (dist(q, d.it[d.end === 'a' ? 'b' : 'a']) >= 0.75) { d.it[d.end] = q; d.moved = true }
          return renderSVG()
        }
        if (d.mode === 'smove') {
          const box = bbox([d.orig.a, d.orig.b])
          const dx = clamp(snap(p[0] - d.start[0]), -box.x, W - box.x - box.w), dy = clamp(snap(p[1] - d.start[1]), -box.y, H - box.y - box.h)
          d.it.a = [r3(d.orig.a[0] + dx), r3(d.orig.a[1] + dy)]; d.it.b = [r3(d.orig.b[0] + dx), r3(d.orig.b[1] + dy)]
          d.moved = d.moved || dx !== 0 || dy !== 0
          return renderSVG()
        }
        if (d.mode === 'open-new') {
          const dr = st.draft
          if (dr.edge) { const pr = project(p, dr.edge.a, dr.edge.b); dr.b = onEdge({ a: dr.edge.a, b: dr.edge.b }, pr.t) }
          else dr.b = snapPt(p, { free: true })
          if (dist(dr.a, dr.b) > 0.05) dr.side = roomSide(dr.a, dr.b)
        } else if (d.mode === 'vtx') {
          const q = snapPt(p, { skip: d.joint })
          for (const j of d.joint) j.w.pts[j.i] = [...q]
          carryOpenings(d.att); d.moved = true
        } else if (d.mode === 'mid') {
          const [a, b] = edgeOf(d.it, d.i)
          const straight = project(p, a, b).d < 0.15
          d.it.bend[d.i] = straight ? null : clampPt(ctrlFor(a, b, p)); d.moved = true
        } else if (d.mode === 'oend') {
          // move the end along its own direction (stays on the wall)
          const o = d.it, other = d.end === 'a' ? o.b : o.a, L = dist(o.a, o.b) || 1
          const dir = [(o[d.end][0] - other[0]) / L, (o[d.end][1] - other[1]) / L]
          const len = Math.max(0.25, snap((p[0] - other[0]) * dir[0] + (p[1] - other[1]) * dir[1]))
          o[d.end] = clampPt([other[0] + dir[0] * len, other[1] + dir[1] * len]); d.moved = true
        } else if (d.mode === 'move') {
          const bb = d.bb
          const dx = clamp(snap(p[0] - d.start[0]), -bb.x, W - bb.x - bb.w), dy = clamp(snap(p[1] - d.start[1]), -bb.y, H - bb.y - bb.h)
          d.joint.forEach((j, k) => { j.w.pts[j.i] = [r3(d.orig[k][0] + dx), r3(d.orig[k][1] + dy)] })
          d.it.bend = d.bend.map(c => c ? clampPt([c[0] + dx, c[1] + dy]) : null)
          carryOpenings(d.att); d.moved = dx !== 0 || dy !== 0 || d.moved
        } else if (d.mode === 'omove') {
          // move a door/window/radiator; snaps onto the nearest wall
          const o = d.it, L = dist(d.orig.a, d.orig.b), m0 = midOf(d.orig.a, d.orig.b)
          const m = [m0[0] + p[0] - d.start[0], m0[1] + p[1] - d.start[1]], e = nearestEdge(m)
          if (e) {
            const EL = dist(e.a, e.b), od = [d.orig.b[0] - d.orig.a[0], d.orig.b[1] - d.orig.a[1]]
            let dir = [(e.b[0] - e.a[0]) / EL, (e.b[1] - e.a[1]) / EL]
            if (dir[0] * od[0] + dir[1] * od[1] < 0) dir = [-dir[0], -dir[1]]   // Anschlag bleibt, wie er war
            const c = onEdge(e, e.t), half = Math.min(L, EL) / 2
            o.a = clampPt([c[0] - dir[0] * half, c[1] - dir[1] * half]); o.b = clampPt([c[0] + dir[0] * half, c[1] + dir[1] * half])
            // dragged over the wall -> onto the other side of the wall
            const v = [p[0] - c[0], p[1] - c[1]], s = -dir[1] * v[0] + dir[0] * v[1]
            if (Math.abs(s) > 0.2) o.side = s > 0 ? 1 : -1
          } else {
            const dx = snap(p[0] - d.start[0]), dy = snap(p[1] - d.start[1])
            o.a = clampPt([d.orig.a[0] + dx, d.orig.a[1] + dy]); o.b = clampPt([d.orig.b[0] + dx, d.orig.b[1] + dy])
          }
          d.moved = true
        } else if (d.mode === 'dev') {
          const raw = [clamp(p[0] - d.off[0], 0, W), clamp(p[1] - d.off[1], 0, H)]
          const nd = nearestDock(devById()[d.id], raw, d.dock)
          d.dock = nd ? nd.o : null
          if (!nd) undock(d.id)
          // close to a fitting element: dock at once (the dot appears centred at the element – clear feedback);
          // dragged away: the bubble in the hand again
          if (nd) dock(d.id, nd.o)
          else st.plan.devices[d.id] = { ...st.plan.devices[d.id], x: clamp(r3(raw[0]), 0, W), y: clamp(r3(raw[1]), 0, H) }
          d.merge = nd ? null : mergeTarget(d.id, raw)
          d.moved = true
        } else if (d.mode === 'symscale') {
          const e = st.plan.devices[d.id]
          if (e) { setSymbolSize(e, d.size * dist(p, [e.x, e.y]) / d.reach); d.moved = true }
        } else if (d.mode === 'gmove') {
          d.it.x = clamp(r3(p[0] - d.off[0]), 0, W); d.it.y = clamp(r3(p[1] - d.off[1]), 0, H); d.moved = true
        }
        renderSVG()
      })

      // Finish a drag in the editor: evaluate the selection frame, dock the device, group it or assign it to a room,
      // save.
      const endDrag = () => {
        const d = st.drag
        st.drag = null
        if (!d) return
        if (d.mode === 'marquee') {
          if (d.moved) {
            const before = d.add ? (st.msel.length ? st.msel : st.sel ? [st.sel] : []) : []
            selectRect(d.start, d.cur)
            setSelection([...before, ...(st.msel.length ? st.msel : st.sel ? [st.sel] : [])])
          } else if (d.room) { st.sel = { type: 'room', id: d.room }; st.msel = [] }
          else if (!d.add) { st.sel = null; st.msel = [] }
          return render()
        }
        if (d.mode === 'mmove' || d.mode === 'mscale') { if (d.moved) changed(); else render(); return }
        if (d.mode === 'stair-new') {
          const dr = st.draft
          st.draft = null
          if (dr && dist(dr.a, dr.b) >= 1) {
            const stair = { id: uid('s'), a: dr.a, b: dr.b, width: STAIR_WIDTH }
            st.plan.stairs.push(stair)
            st.tool = 'select'; st.sel = { type: 'stair', id: stair.id }
            save()
          }
          return render()
        }
        if (d.mode === 'open-new') {
          const dr = st.draft
          st.draft = null
          if (dist(dr.a, dr.b) >= 0.3) {
            const o = { id: uid('o'), type: dr.type, a: dr.a, b: dr.b, side: roomSide(dr.a, dr.b), device_id: null }
            st.plan.openings.push(o); st.sel = { type: 'open', id: o.id }; save()
          }
          render()
        } else if (d.moved && ['vtx', 'mid', 'move'].includes(d.mode)) changed()
        else if (d.moved && d.mode === 'dev' && d.merge) mergeInto(d.id, d.merge)
        else if (d.moved && d.mode === 'gmove') { save(); render(); d.it.members.forEach(assignByPosition) }
        else if (d.moved) { save(); render(); if (d.mode === 'dev') assignByPosition(d.id) }
      }
      svg.addEventListener('pointerup', endDrag)
      svg.addEventListener('pointercancel', endDrag)

      /* ---------- Tooltip (Maus) */
      function hover(e) {
        if (e.pointerType !== 'mouse') return
        const m = svg.getScreenCTM(), base = wrap.getBoundingClientRect()   // holds with the camera (viewBox) too
        if (!m) return
        const rr = roomAt([(e.clientX - m.e) / m.a, (e.clientY - m.f) / m.d])
        setFocus(rr ? rr.id : null)
        const g = e.target.closest('.fp-dev, .fp-open')
        let d = null, pos = null
        const place = q => { tip.style.left = (q[0] * m.a + m.e - base.left) + 'px'; tip.style.top = (q[1] * m.d + m.f - base.top - 6) + 'px' }
        if (g && g.dataset.kind === 'group') {
          const gr = groupById(g.dataset.id), ms = gr ? gr.members.map(id => devById()[id]).filter(Boolean) : []
          if (!gr || !ms.length) { tip.classList.add('hidden'); return }
          place([gr.x, gr.y])
          tip.innerHTML = `<b>${HO.esc(gr.name || 'Gruppe')}</b><div class="muted">${ms.length} Geräte</div>
            ${ms.map(x => `<div style="margin-top:4px">${HO.esc(x.name)} <span class="muted">· ${HO.esc(HO.devState(x))}</span></div>`).join('')}`
          tip.classList.remove('hidden')
          return
        }
        if (g && g.classList.contains('fp-dev')) {
          d = devById()[g.dataset.id]
          const dp = st.dockPos && st.dockPos.get(g.dataset.id), q = st.plan.devices[g.dataset.id]
          pos = dp ? dp.p : q && [q.x, q.y]
        } else if (g) { const o = st.plan.openings.find(x => x.id === g.dataset.id); d = o && dockedDevices(o, devById())[0]; pos = o && midOf(o.a, o.b) }
        if (!d || !pos) { tip.classList.add('hidden'); return }
        place(pos)
        tip.innerHTML = `<b>${HO.esc(d.name)}</b><div class="muted">${HO.esc(d.room)} · ${HO.esc(HO.kindLabel[d.kind] || 'Gerät')}</div>
          <div style="margin-top:4px">${HO.esc(HO.devState(d))}</div>
          ${d.battery != null ? `<div class="${HO.lowBattery(d) ? '' : 'muted'}" style="${HO.lowBattery(d) ? 'color:var(--warn)' : ''}">Batterie ${d.battery} %</div>` : ''}
          ${!d.online && d.status_detail ? `<div class="muted">${HO.esc(String(d.status_detail).slice(0, 70))}</div>` : ''}
          ${d.last_seen ? `<div class="muted">Zuletzt gesehen ${HO.relTime(d.last_seen)}</div>` : ''}`
        tip.classList.remove('hidden')
      }
      svg.addEventListener('pointerleave', e => { tip.classList.add('hidden'); if (e.pointerType === 'mouse') setFocus(null) })

      /* ---------- Drag & drop from "Nicht platziert" */
      let tray = null
      trayBox.addEventListener('pointerdown', e => {
        const chip = e.target.closest('[data-arm]')
        if (!chip || !st.editing || e.button > 0) return
        tray = { id: chip.dataset.arm, x: e.clientX, y: e.clientY, ghost: null, chip, touch: e.pointerType === 'touch' }
      })
      // Drag a device out of the bar: only after some distance does the image appear under the finger – on a touch device
      // only when dragging upwards, swiping sideways scrolls the bar.
      const trayMove = e => {
        if (!tray) return
        if (!tray.ghost) {
          const dx = e.clientX - tray.x, dy = e.clientY - tray.y
          if (Math.hypot(dx, dy) < 6) return
          // finger: swiping sideways scrolls through the bar; only dragging upwards (to the plan) takes the device along
          if (tray.touch && !(dy < 0 && Math.abs(dy) > Math.abs(dx))) { tray = null; return }
          tray.ghost = tray.chip.cloneNode(true); tray.ghost.classList.add('fp-ghost'); document.body.appendChild(tray.ghost)
          st.armed = null
        }
        e.preventDefault()
        tray.ghost.style.left = e.clientX + 'px'; tray.ghost.style.top = e.clientY + 'px'
        const r = svg.getBoundingClientRect()
        wrap.classList.toggle('drop', e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom)
      }
      // Release a device from the bar: over the plan it is dropped there.
      const trayUp = e => {
        if (!tray) return
        const t = tray; tray = null
        wrap.classList.remove('drop')
        if (!t.ghost) return                     // only tapped -> normal click (select, then tap on the plan)
        t.ghost.remove(); st.suppressClick = true; setTimeout(() => { st.suppressClick = false }, 0)
        const r = svg.getBoundingClientRect()
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) placeDevice(t.id, toPlan(e))
        else renderTools()
      }
      document.addEventListener('pointermove', trayMove, { passive: false })
      document.addEventListener('pointerup', trayUp)
      document.addEventListener('pointercancel', trayUp)

      /* ---------- Klicks in Werkzeugleiste/Seitenleiste */
      el.addEventListener('click', e => {
        const t = e.target.closest('[data-tool],[data-act],[data-arm],[data-floor]')
        if (!t || !st.editing || st.suppressClick) return
        if (t.dataset.floor) { setFloor(t.dataset.floor); return }
        if (t.dataset.tool) { st.tool = t.dataset.tool; st.sel = null; st.msel = []; st.armed = null; st.draft = null; st.newFloor = null; render(); return }
        if (t.dataset.arm) { st.armed = st.armed === t.dataset.arm ? null : t.dataset.arm; st.tool = 'select'; st.draft = null; renderTools(); return }
        const act = t.dataset.act, it = selItem()
        if (act === 'finish') finishDraft()
        else if (act === 'undo') { if (st.draft && st.draft.pts) { st.draft.pts.pop(); if (!st.draft.pts.length) st.draft = null } render() }
        else if (act === 'cancel') { st.draft = null; render() }
        else if (act === 'histundo') { doUndo(); return }
        else if (act === 'histredo') { doRedo(); return }
        else if (act === 'gridsnap') {
          gridSnap = !gridSnap
          try { localStorage.setItem('ho.fpSnap', gridSnap ? '1' : '0') } catch (err) { }
          renderTools(); return
        }
        else if (act === 'ghost') { ghostOn = !ghostOn; writePref('ho.fpGhost', ghostOn); render(); return }
        else if (act === 'floorsnap') { floorSnap = !floorSnap; writePref('ho.fpFloorSnap', floorSnap); renderTools(); return }
        else if (act === 'flooradd') {
          // the outline of the floor shown is offered as the starting point as soon as something is drawn on it
          st.newFloor = { where: 'above', copy: st.plan.walls.length > 0 }
          st.tool = 'select'; st.sel = null; st.msel = []; st.armed = null; st.draft = null; render(); return
        }
        else if (act === 'floorwhere' && st.newFloor) { st.newFloor.where = t.dataset.v === 'below' ? 'below' : 'above'; renderTools(); return }
        else if (act === 'floorcopy' && st.newFloor) { st.newFloor.copy = t.dataset.v === '1'; renderTools(); return }
        else if (act === 'floorcancel') { st.newFloor = null; renderTools(); return }
        else if (act === 'floorcreate' && st.newFloor) { addFloor(st.newFloor.where, st.newFloor.copy); return }
        else if (act === 'floordelete') { deleteFloor(); return }
        else if (act === 'selall') { selectAll(); return }
        else if (act === 'selnone') { st.msel = []; render(); return }
        else if (act === 'delete') deleteSelection()
        else if (act === 'unplace') { delete st.plan.devices[st.sel.id]; st.sel = null; save(); render() }
        else if (act === 'sym') {
          // symbol picker: plan symbol of the selected device (empty = standard bubble) or symbol of the selected group
          const key = t.dataset.v || '', entry = st.sel && st.sel.type === 'dev' && st.plan.devices[st.sel.id]
          if (entry) { if (key) entry.furniture = key; else { delete entry.furniture; delete entry.size; delete entry.rot } }
          else if (it && st.sel.type === 'group') it.icon = key
          else return
          save(); render()
        }
        else if (act === 'symsize' || act === 'symturn') {
          const entry = st.sel && st.sel.type === 'dev' && st.plan.devices[st.sel.id]
          if (!entry || !symbolBox(entry, 1 / st.ppu)) return
          if (act === 'symsize') setSymbolSize(entry, (Number(entry.size) || 1) + (t.dataset.v === '-1' ? -SYMBOL_STEP : SYMBOL_STEP))
          else { const rot = ((Number(entry.rot) || 0) + SYMBOL_TURN) % 360; if (rot) entry.rot = rot; else delete entry.rot }
          save(); render()
        }
        else if (act === 'oundock' && it && st.sel.type === 'open') {
          // detach a device from the selected element: it stands free next to it again
          undock(t.dataset.v); save(); render()
        }
        else if (act === 'gout' && it) { ungroup(it.id, t.dataset.v); save(); render() }
        else if (act === 'gdissolve' && it) { dissolve(it.id); save(); render() }
        else if (act === 'straighten' && it) { it.bend = it.bend.map(() => null); changed() }
        else if (act === 'stairwidth' && it && st.sel.type === 'stair') {
          it.width = clamp(Math.round((it.width + (t.dataset.v === '-1' ? -0.25 : 0.25)) * 100) / 100, STAIR_MIN, STAIR_MAX); save(); render()
        }
        else if (act === 'decksurface' && it && st.sel.type === 'deck') {
          if (t.dataset.v) it.surface = t.dataset.v; else delete it.surface
          save(); render()
        }
        else if (act === 'stairflip' && it && st.sel.type === 'stair') { [it.a, it.b] = [it.b, it.a]; save(); render() }
        else if (act === 'otype' && it) { it.type = t.dataset.v; save(); render() }
        else if (act === 'oside' && it) { it.side = -it.side; save(); render() }
        else if (act === 'oswap' && it) { [it.a, it.b] = [it.b, it.a]; it.side = -it.side; save(); render() }
        else if (act === 'autorooms') {
          autoRooms(); autoPlace(); save(); render()
          HO.toast('Räume angelegt – Wände antippen und ziehen zum Anpassen.')
        } else if (act === 'autoplace') {
          const n = autoPlace(); save(); render()
          HO.toast(n ? `${n} Geräte in ihre Räume gesetzt.` : 'Keine passenden Geräte: Räume erst mit Home-Base-Räumen verknüpfen.', n ? 'ok' : 'warn')
        }
      })
      el.addEventListener('change', e => {
        const t = e.target
        if (t.dataset.size) {
          st.plan[t.dataset.size] = clamp(parseInt(t.value, 10) || 10, t.dataset.size === 'width' ? 6 : 4, t.dataset.size === 'width' ? 80 : 60)
          // the area is shared by all floors: everything on every floor stays inside it
          for (const f of levels()) {
            for (const w of f.walls.concat(f.decks)) { w.pts = w.pts.map(clampPt); w.bend = w.bend.map(c => c ? clampPt(c) : null) }
            for (const o of f.openings.concat(f.stairs)) { o.a = clampPt(o.a); o.b = clampPt(o.b) }
            for (const d of [...Object.values(f.devices), ...f.groups]) { [d.x, d.y] = clampPt([d.x, d.y]) }
          }
          return changed()
        }
        if (t.dataset.f === 'heatmap') { st.plan.heatmap = t.checked; st.heat = t.checked; save(); return }
        if (t.dataset.f === 'show_names') { st.plan.show_names = t.checked; save(); renderSVG(); return }
        if (t.dataset.f === 'weather') { st.plan.weather = t.checked; save(); renderTools(); return }
        if (t.dataset.f === 'fx') { st.plan.fx = t.checked; save(); renderTools(); return }
        if (t.dataset.f === 'fx_level') { st.plan.fx_level = t.value === 'subtle' ? 'subtle' : 'normal'; save(); return }
        if (t.dataset.f === 'warnings') { st.plan.warnings = t.checked; save(); return }
        if (t.dataset.f === 'fx_preview') { HO.state.fxPreview = HO.weatherFx.KINDS.indexOf(t.value) >= 0 ? t.value : null; return }
        if (t.dataset.f === 'fx_time') { HO.state.fxTime = ['day', 'dusk', 'night'].indexOf(t.value) >= 0 ? t.value : null; return }
        if (t.dataset.f === 'shadow') { st.plan.shadow = t.checked; save(); renderSVG(); return }
        if (t.dataset.f === 'north') { save(); return }
        if (t.dataset.f === 'devfloor' && st.sel && st.sel.type === 'dev') { moveToFloor(st.sel.id, t.value); return }
        const it = selItem()
        if (!it) return
        if (t.dataset.f === 'link' && st.sel.type === 'room') {
          const prevLinked = it.room_id
          it.room_id = t.value || null
          if (it.room_id && (!prevLinked && /^Raum \d+$/.test(it.name))) it.name = roomById()[it.room_id].name
          save(); render()
        } else if (t.dataset.f === 'odev' && st.sel.type === 'open') {
          // dock a further device to the selected element (a device from a group leaves its group for that)
          const id = t.value, grp = id && groupOf(id)
          if (!id || dockedIds(it).length >= MAX_DOCKED) return
          claim(id)                              // a device from another floor moves here
          if (grp) ungroup(grp.id, id)
          dock(id, it); save(); render()
        }
      })
      const renameLinked = HO.debounce(async (roomId, name) => {
        try {
          await HO.api(`rooms/${encodeURIComponent(roomId)}/name`, { method: 'PUT', body: { name } })
          if (opts.onRoomsRenamed) opts.onRoomsRenamed()
        } catch (err) { HO.toast(err.message, 'bad') }
      }, 700)
      el.addEventListener('input', e => {
        const t = e.target
        if (t.dataset.f === 'address') { st.plan.address = t.value.slice(0, 120); save(); return }
        if (t.dataset.f === 'home_name') { st.plan.home_name = t.value.slice(0, 40); save(); return }
        if (t.dataset.f === 'floorname') {   // the button of the floor in the toolbar follows while typing
          st.plan.floor.name = t.value.replace(/\s+/g, ' ').slice(0, 40)
          const button = tools.querySelector('[data-floor].active')
          if (button) { button.textContent = floorShort(st.plan.floor); button.title = floorLabel(st.plan.floor) }
          save(); return
        }
        if (t.dataset.f === 'north') {   // rotate live, saved on release (change)
          st.plan.north = +t.value
          const box = t.closest('.field'), c = box && box.querySelector('.fp-compass'), v = box && box.querySelector('[data-north-val]')
          if (c) c.style.transform = `rotate(${st.plan.north}deg)`
          if (v) v.textContent = st.plan.north + '°'
          renderSVG(); return
        }
        if (t.dataset.f === 'gname') { const g = selItem(); if (g) { g.name = t.value.slice(0, 40); save(); renderSVG() } return }
        if (t.dataset.f !== 'name' || !st.sel || st.sel.type !== 'room') return
        const r = selItem()
        if (!r) return
        r.name = t.value
        if (r.room_id) {
          const info = roomById()[r.room_id]
          if (info) info.name = t.value
          renameLinked(r.room_id, t.value)
        }
        save(); renderSVG()
      })
      // Keys in the editor: undo/redo, Enter and Esc while drawing, Del deletes, Ctrl+A selects everything, arrows
      // move.
      const onKey = e => {
        if (!st.editing || ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) { e.preventDefault(); doUndo(); return }
        if ((e.ctrlKey || e.metaKey) && (e.key === 'Z' || (e.key === 'z' && e.shiftKey) || e.key === 'y')) { e.preventDefault(); doRedo(); return }
        if (st.draft && st.draft.pts) {
          if (e.key === 'Enter') { e.preventDefault(); finishDraft() }
          else if (e.key === 'Escape') { st.draft = null; render() }
          else if (e.key === 'Backspace') { e.preventDefault(); st.draft.pts.pop(); if (!st.draft.pts.length) st.draft = null; render() }
          return
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'a') { e.preventDefault(); selectAll(); return }
        if (e.key === 'Escape' && (st.sel || st.msel.length)) { st.sel = null; st.msel = []; render() }
        else if ((e.key === 'Delete' || e.key === 'Backspace') && (st.sel || st.msel.length)) { e.preventDefault(); deleteSelection() }
      }
      document.addEventListener('keydown', onKey)

      /* ---------- Automatik */
      function autoRooms() {
        // rooms that another floor already shows are not drawn a second time
        const elsewhere = new Set(levels().filter(f => f !== st.plan.floor).reduce((ids, f) => ids.concat(f.rooms.map(r => r.room_id)), []))
        const list = st.rooms.filter(r => r.room_id && r.devices > 0 && !elsewhere.has(r.room_id)).sort((a, b) => HO.byName(a.name, b.name))
        if (!list.length) return
        const cols = Math.ceil(Math.sqrt(list.length * 1.6)), rw = 6, rh = 4.5, m = 0.5
        const rows = Math.ceil(list.length / cols), shared = levels().length > 1   // the area of other floors never shrinks
        st.plan.width = Math.max(shared ? st.plan.width : 12, cols * rw + 2 * m)
        st.plan.height = Math.max(shared ? st.plan.height : 8, rows * rh + 2 * m)
        // grid cells; shared edges only once as a wall
        const segs = new Map(), key = (a, b) => [a, b].map(p => p.join(',')).sort().join('|')
        st.plan.walls = []; st.plan.rooms = []
        list.forEach((r, i) => {
          const x = m + (i % cols) * rw, y = m + Math.floor(i / cols) * rh
          const c = [[x, y], [x + rw, y], [x + rw, y + rh], [x, y + rh]]
          for (let k = 0; k < 4; k++) { const a = c[k], b = c[(k + 1) % 4]; segs.set(key(a, b), [a, b]) }
          st.plan.rooms.push({ id: uid('r') + i, name: r.name, room_id: r.room_id, x: x + rw / 2, y: y + rh / 2 })
        })
        st.plan.walls = [...segs.values()].map((s, i) => ({ id: uid('w') + i, pts: s.map(p => [...p]), bend: [null] }))
        syncRooms()
      }
      // Put devices that are not placed yet into their rooms – lined up from bottom left, the room centre stays free for
      // name and temperature.
      function autoPlace() {
        const placed = new Set(st.devices.filter(d => isPlaced(d.id)).map(d => d.id))
        const target = {}
        for (const { face, room } of layout()) {
          if (!room) continue
          const k = roomKey(room)
          if (!target[k] || face.area > target[k].face.area) target[k] = { face }
        }
        let n = 0
        const byRoom = {}
        for (const d of st.devices) if (!placed.has(d.id) && target[d.room_id]) (byRoom[d.room_id] = byRoom[d.room_id] || []).push(d)
        for (const [rid, devs] of Object.entries(byRoom)) {
          // fill from bottom left upwards – the centre stays free for room name and temperature
          const { face } = target[rid], poly = face.poly, bb = bbox(poly), cell = 1.25, c = face.label
          const cols = Math.max(1, Math.floor((bb.w - 0.4) / cell))
          devs.forEach((d, i) => {
            let q = [snap(bb.x + 0.2 + cell / 2 + (i % cols) * cell), snap(bb.y + bb.h - 0.75 - Math.floor(i / cols) * cell)]
            if (!inside(poly, q) || dist(q, c) < 1) q = [snap(c[0] + (i % 3 - 1) * cell), snap(c[1] + 1.2 + Math.floor(i / 3) * cell * 0.8)]
            st.plan.devices[d.id] = { x: q[0], y: q[1] }
            n++
          })
        }
        return n
      }

      /* ---------- Camera gestures (view and full screen, floorplan_camera.js)
         Full screen: one finger or the mouse pans, two fingers zoom, mouse wheel and trackpad zoom.
         View:        two fingers zoom, as do Ctrl+mouse wheel and pinching on the trackpad (the mouse wheel alone scrolls
                   the page); one finger or the mouse pans as soon as it is zoomed in – otherwise it scrolls the page.
                   Plus the buttons −, + and "Ansicht zurücksetzen" above the floor plan.
         A tap only counts without movement and with one finger – navigating opens no rooms. */
      HO.planCamera.gestures(wrap, {
        enabled: () => !st.editing && !still,
        canPan: () => !!cam || st.zoom > 1.001,
        wheel: e => !!cam || !!e.ctrlKey,
        doubleClick: () => !!cam,
        local: pointerAt,
        get: look,
        set: moveLive,
        limit: limitScale,
        begin: beginLive,
        end: settleLive,
        multi: () => { st.tap = null },
        tap: target => { st.tap = null; armTap(target) }
      })

      /* ---------- Size */
      let lastW = 0, lastH = 0
      const ro = window.ResizeObserver ? new ResizeObserver(() => {
        const w = wrap.clientWidth, h = wrap.clientHeight
        if (Math.abs(w - lastW) > 6 || (cam && Math.abs(h - lastH) > 6)) { lastW = w; lastH = h; requestAnimationFrame(() => renderSVG()) }
      }) : null
      if (ro) ro.observe(wrap)
      render()

      return {
        // Take over fresh data (devices, rooms, plan, temperature range) and draw – except in the middle of a drag.
        update(data) {
          if (data.devices) st.devices = data.devices
          if (data.rooms) st.rooms = data.rooms
          if (data.heatRange !== undefined) st.heatRange = data.heatRange || null
          if (data.plan && !st.editing) openPlan(data.plan)
          if (!st.drag) render()
        },
        setEditing(on) {
          st.editing = on; st.sel = null; st.msel = []; st.armed = null; st.tool = 'select'; st.draft = null; st.newFloor = null
          tip.classList.add('hidden'); render()
        },
        get plan() { return st.plan },
        // the floors bottom to top ({id, level, name, short, active}), the id of the floor shown, and changing it
        get floors() { return levels().map(f => ({ id: f.id, level: f.level, name: floorLabel(f), short: floorShort(f), active: f === st.plan.floor })) },
        get floor() { return st.plan.floor.id },
        setFloor(id) { return setFloor(id) },
        stepFloor(direction) { return stepFloor(direction) },
        get heat() { return st.heat },
        setHeat(on) { st.heat = !!on; render() },
        // Zoom in or out by the factor (buttons + and −), around the centre of the section.
        zoomBy(factor) {
          settleLive()
          const c = look()
          aim({ cx: c.cx, cy: c.cy, s: c.s * factor })
          render()
        },
        // reset the view: whole floor plan, centred
        fit() { settleLive(); if (cam) cam.s = 0; else { st.zoom = 1; st.zc = null } render() },
        // zoom level as a multiple of the overall view (1 = whole floor plan) – for buttons and tests
        get zoom() { return cam ? (cam.fit ? cam.s / cam.fit : 1) : st.zoom },
        get moving() { return !!live },
        highlight(id) { if (st.hl !== (id || null)) { st.hl = id || null; if (!id) st.focus = null; renderSVG() } },
        // take a device out of a group (view): save at once, so that a reload does not overwrite the change
        ungroup(gid, id) { ungroup(gid, id); render(); if (opts.onSave) opts.onSave(st.plan) },
        get camera() { return cam ? { s: cam.s, fit: cam.fit, cx: cam.cx, cy: cam.cy } : null },
        toggleNames() { st.plan.show_names = !st.plan.show_names; save(); render() },
        // weather effects on/off (button "Wetter" above the floor plan) – the same setting as in the editor; saved at once,
        // so that a reload does not overwrite the change
        toggleFx() { st.plan.fx = st.plan.fx === false; render(); if (opts.onSave) opts.onSave(st.plan); return st.plan.fx },
        resize() { render() },
        startWithAutoRooms() { st.editing = true; autoRooms(); autoPlace(); save(); render() },
        // weather of the home page (fx from /api/weather) – weather effects and building shadows follow
        setWeather(fx) {
          st.wx = fx || null
          if (!st.drag) renderSVG()
        },
        get fx() { return st.fxView },
        // Tear down: unregister tick, weather image, observers and keys.
        destroy() {
          pulse.stop()
          if (liveFrame) cancelAnimationFrame(liveFrame)
          if (st.fxView) { st.fxView.destroy(); st.fxView = null }
          if (ro) ro.disconnect(); document.removeEventListener('keydown', onKey)
          document.removeEventListener('pointermove', trayMove); document.removeEventListener('pointerup', trayUp); document.removeEventListener('pointercancel', trayUp)
        }
      }
    }
  }
})(window.HO)
