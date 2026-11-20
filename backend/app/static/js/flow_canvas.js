/* ComfyOS Flow – node canvas (infinite surface) and the shared catalogue rules (HO.flowSpec).
   Camera as in the floor plan: one finger/mouse = pan, two fingers/mouse wheel = zoom.
   Nodes snap to the grid and align with their neighbours (edges, centres, straight lines).
   Drag lines from a port; the end snaps to the nearest fitting port.
   Touch alternative: tap a port, then tap the counterpart port.
   Multi-selection: Shift/Ctrl+click, selection frame (Shift+drag, on the tablet hold long) or the mode "Auswählen".
   Live: canvas.live(msg) shows which node is running, waiting or holding right now (SSE messages of the engine). */
(function (HO) {
  'use strict'
  const GRID = 20, NODE_W = 232, PORT_SNAP = 34, ALIGN = 9, TAP_MOVE = 7, TAP_MS = 650, ZMIN = 0.3, ZMAX = 2.2
  const HOLD_MS = 450, HISTORY = 50, STEP_MS = 230, GLOW_MS = 1100, PASTE_SHIFT = 40
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v))
  const uid = p => p + Math.random().toString(16).slice(2, 10)
  const clone = o => JSON.parse(JSON.stringify(o))

  /* ---------- Catalogue rules: the same as backend/app/flow_catalog.py (role, when, n+1, counted outputs) */
  const FS = HO.flowSpec = {
    cat: null, byType: {}, props: {},
    // Take over the catalogue of the nodes and make it searchable by type and property.
    use(c) {
      FS.cat = c
      FS.byType = Object.fromEntries(c.nodes.map(n => [n.type, n]))
      FS.props = Object.fromEntries((c.properties || []).map(p => [p.id, p]))
    },
    spec: t => FS.byType[t],
    field: (sp, k) => ((sp && sp.fields) || []).find(f => f.key === k),
    // Setting of a node – if it is missing, the default from the catalogue.
    value(n, k) {
      const v = (n.config || {})[k]
      if (v == null || v === '') {
        const f = FS.field(FS.byType[n.type], k)
        return f && f.default != null && f.default !== '' ? f.default : undefined
      }
      return v
    },
    roles: sp => (sp && sp.roles ? Object.keys(sp.roles) : []),
    // Role of a node with several roles (trigger or condition); null if it knows no roles.
    role(n) {
      const have = FS.roles(FS.byType[n.type])
      if (!have.length) return null
      const want = (n.config || {}).role
      return have.indexOf(want) >= 0 ? want : have[0]
    },
    ptype(n) { const p = FS.props[(n.config || {}).prop]; return p ? p.type : null },
    // Does the condition of a catalogue entry ("only if field = value") hold for this node?
    when(n, cond) {
      return !cond || Object.keys(cond).every(k => {
        const want = cond[k], have = k === 'role' ? FS.role(n) : k === '@ptype' ? FS.ptype(n) : FS.value(n, k)
        return Array.isArray(want) ? want.indexOf(have) >= 0 : have === want
      })
    },
    split(port) { const m = /^([a-z_]+?)(\d{1,2})$/.exec(port || ''); return m ? [m[1], +m[2]] : [port, null] },
    // Outputs of a node by role and settings – growing outputs as often as their count field says.
    outs(n) {
      const sp = FS.byType[n.type]
      if (!sp) return []
      const r = FS.role(n), raw = r ? (sp.roles[r].outputs || []) : (sp.outputs || []), out = []
      for (const p of raw) {
        if (p.count === undefined) { out.push(p); continue }
        let c = 1
        if (FS.when(n, p.count_when)) c = clamp(Math.floor(Number(FS.value(n, p.count)) || 1), 1, p.max || 10)
        for (let i = 1; i <= c; i++) out.push(Object.assign({}, p, { id: p.id + i, label: p.label || (c > 1 ? String(i) : '') }))
      }
      return out
    },
    // The input of a node for a port id, or null (growing inputs carry a number).
    inPort(n, port) {
      const sp = FS.byType[n.type]
      if (!sp || FS.roles(sp).length) return null
      const s = FS.split(port)
      for (const p of sp.inputs || []) {
        if (!FS.when(n, p.when)) continue
        if (p.grow ? (p.id === port || (p.id === s[0] && s[1] !== null && s[1] >= 1)) : p.id === port) return p
      }
      return null
    },
    // Inputs of a node; a growing input always shows one free place more than are in use.
    ins(n, edges) {
      const sp = FS.byType[n.type], out = []
      if (!sp || FS.roles(sp).length) return out
      for (const p of sp.inputs || []) {
        if (!FS.when(n, p.when)) continue
        if (!p.grow) { out.push(p); continue }
        const used = (edges || []).filter(e => e.to === n.id && FS.split(String(e.toPort))[0] === p.id).length
        const c = Math.min(p.max || 10, used + 1)
        for (let i = 1; i <= c; i++) out.push(Object.assign({}, p, { id: p.id + i, free: i > used }))
      }
      return out
    },
    // Check lines and number growing inputs without gaps; returns {edges, removed}
    normalize(nodes, edges) {
      const byId = Object.fromEntries(nodes.map(n => [n.id, n]))
      let kept = [], removed = 0
      for (const e of edges) {
        const a = byId[e.from], b = byId[e.to]
        const pa = a && FS.outs(a).find(p => p.id === e.fromPort), pb = b && FS.inPort(b, e.toPort)
        if (!pa || !pb || pa.kind !== pb.kind) { removed++; continue }
        kept.push([e, pb])
      }
      const last = {}
      kept.forEach((x, i) => { if (x[1].kind === 'bool' && !x[1].grow) last[x[0].to + '/' + x[0].toPort] = i })
      kept = kept.filter((x, i) => x[1].grow || x[1].kind !== 'bool' || last[x[0].to + '/' + x[0].toPort] === i)
      const groups = {}
      kept.forEach((x, i) => {
        if (!x[1].grow) return
        const no = FS.split(String(x[0].toPort))[1]
        ;(groups[x[0].to + '/' + x[1].id] = groups[x[0].to + '/' + x[1].id] || []).push([no === null ? 999 : no, i, x[1]])
      })
      const rename = {}
      Object.keys(groups).forEach(k => {
        groups[k].sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach((it, idx) => {
          rename[it[1]] = idx < (it[2].max || 10) ? it[2].id + (idx + 1) : null
        })
      })
      const out = []
      kept.forEach((x, i) => {
        if (i in rename) {
          if (rename[i] === null) { removed++; return }
          x[0].toPort = rename[i]
        }
        out.push(x[0])
      })
      return { edges: out, removed }
    }
  }

  HO.flowCanvas = {
    // Mount the drawing surface of a flow. opts: flow (is edited directly), specs (catalogue), summary and problem
    // (texts per node), insets (margins left free) and callbacks (onChange, onSelect, onNodeTap, onBackgroundTap,
    // onHistory, onHint, onRender, onFire). Returns the controller of the editor.
    mount(stage, opts) {
      const flow = opts.flow
      FS.use(opts.specs)
      const cam = { x: -60, y: -60, s: 1 }
      const st = { sel: { nodes: new Set(), edge: null }, pending: null, drag: null, rel: {}, ptrs: new Map(),
        selectMode: false, undo: [], redo: [], live: {}, glow: {}, cond: {}, queue: [], playing: false, pasteN: 0 }
      stage.classList.add('fl-stage')
      stage.innerHTML = `<svg class="fl-wires" aria-hidden="true"><g class="fl-guides"></g><g class="fl-edges"></g><path class="fl-wire fl-temp hidden"/><rect class="fl-band hidden"/></svg>
        <div class="fl-world"></div><button class="fl-edge-del hidden" aria-label="Linie löschen" title="Linie löschen">${HO.icon('trash', 'sm')}</button>`
      const svg = stage.querySelector('.fl-wires'), gEdges = svg.querySelector('.fl-edges'), gGuides = svg.querySelector('.fl-guides')
      const temp = svg.querySelector('.fl-temp'), band = svg.querySelector('.fl-band')
      const world = stage.querySelector('.fl-world'), delBtn = stage.querySelector('.fl-edge-del')

      /* ---------- Spezifikation & Geometrie */
      const nodeById = id => flow.nodes.find(n => n.id === id)
      const specOf = n => FS.byType[n.type]
      const portOf = (n, dir, id) => n && (dir === 'in' ? FS.ins(n, flow.edges) : FS.outs(n)).find(p => p.id === id)
      const cardEl = id => world.querySelector(`.fl-node[data-node="${CSS.escape(id)}"]`)
      const local = e => { const r = stage.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top] }
      const toWorld = q => [cam.x + q[0] / cam.s, cam.y + q[1] / cam.s]
      const toScreen = p => [(p[0] - cam.x) * cam.s, (p[1] - cam.y) * cam.s]
      function portWorld(id, dir, port) {
        const n = nodeById(id), r = st.rel[id], q = r && r[dir][port]
        return n && q ? [n.x + q[0], n.y + q[1]] : null
      }
      function wirePath(a, b) {
        const dx = Math.max(36, Math.abs(b[0] - a[0]) * 0.5)
        return `M${a[0]} ${a[1]}C${a[0] + dx} ${a[1]} ${b[0] - dx} ${b[1]} ${b[0]} ${b[1]}`
      }
      const changed = () => opts.onChange && opts.onChange()
      const selChanged = () => opts.onSelect && opts.onSelect(st.sel.nodes.size, !!st.sel.edge)

      /* ---------- Undo / redo: snapshot of the graph before every change */
      const shot = () => JSON.stringify({ nodes: flow.nodes, edges: flow.edges })
      const history = () => opts.onHistory && opts.onHistory(st.undo.length > 0, st.redo.length > 0)
      // Record the state for undo (at most HISTORY steps).
      function snap(state) {
        const s = state || shot()
        if (st.undo[st.undo.length - 1] === s) return
        st.undo.push(s)
        if (st.undo.length > HISTORY) st.undo.shift()
        st.redo = []
        history()
      }
      // Restore a recorded state and redraw.
      function restore(s) {
        const g = JSON.parse(s)
        flow.nodes.splice(0, flow.nodes.length, ...g.nodes)
        flow.edges.splice(0, flow.edges.length, ...g.edges)
        st.sel = { nodes: new Set(), edge: null }
        st.pending = null
        st.rel = {}
        render(); changed(); selChanged(); history()
      }
      // Undo the last step.
      function undo() { if (!st.undo.length) return; st.redo.push(shot()); restore(st.undo.pop()) }
      // Redo the undone step.
      function redo() { if (!st.redo.length) return; st.undo.push(shot()); restore(st.redo.pop()) }

      /* ---------- Darstellung */
      function liveText(l) {
        if (!l) return ''
        if (l.until) {
          const s = Math.max(0, Math.round(l.until - Date.now() / 1000))
          const t = s >= 3600 ? Math.floor(s / 3600) + ':' + String(Math.floor(s % 3600 / 60)).padStart(2, '0') + ' Std.'
            : Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')
          return (l.round ? `Runde ${l.round} · ` : '') + 'noch ' + t
        }
        if (l.round) return `Runde ${l.round}`
        return l.on ? 'gilt gerade' : ''
      }
      // Card of a node: head with icon and name, summary, ports, warning and live display.
      function cardHTML(n) {
        const sp = specOf(n)
        if (!sp) return ''
        const role = FS.role(n), cat = role || sp.category
        const warn = opts.problem ? opts.problem(n, sp) : ''
        const l = st.live[n.id], lt = liveText(l), c = st.cond[n.id]
        const fields = (sp.fields || []).filter(f => f.widget !== 'token').length + (role && FS.roles(sp).length > 1 ? 1 : 0)
        const custom = opts.customized ? opts.customized(n, sp) : false
        const port = (p, dir) => `<div class="fl-port ${dir} k-${p.kind} ${p.free ? 'free' : ''}" data-port="${HO.esc(p.id)}" data-dir="${dir}" title="${HO.esc(HO.flowPortText ? HO.flowPortText(p, dir) : '')}">${dir === 'in' ? '<i></i>' : ''}${p.label ? `<span>${HO.esc(p.label)}</span>` : ''}${dir === 'out' ? '<i></i>' : ''}</div>`
        return `<div class="fl-node cat-${cat} ${st.sel.nodes.has(n.id) ? 'sel' : ''} ${warn ? 'warn' : ''} ${st.glow[n.id] ? 'run' : ''} ${l && (l.until || l.round) ? 'waiting' : ''} ${l && l.on ? 'holds' : ''}" data-node="${HO.esc(n.id)}" style="transform:translate(${n.x}px,${n.y}px)">
          <div class="fl-head">${HO.icon(sp.icon || 'device', 'sm')}<b>${HO.esc(sp.label)}</b>${opts.canFire && opts.canFire(n) ? `<button class="fl-fire" data-fire title="Jetzt auslösen (Test)" aria-label="Jetzt auslösen">${HO.icon('play', 'sm')}</button>` : ''}</div>
          <div class="fl-sum">${role && FS.roles(sp).length > 1 ? `<span class="fl-role">${HO.esc(FS.cat.roles[role].label)}</span>` : ''}${HO.esc(opts.summary ? opts.summary(n, sp) : '')}</div>
          ${warn ? `<div class="fl-warn">${HO.icon('warn', 'sm')}<span>${HO.esc(warn)}</span></div>` : ''}
          <div class="fl-ports"><div class="fl-ins">${FS.ins(n, flow.edges).map(p => port(p, 'in')).join('')}</div><div class="fl-outs">${FS.outs(n).map(p => port(p, 'out')).join('')}</div></div>
          <div class="fl-live ${lt ? '' : 'hidden'}"><span>${HO.esc(lt)}</span></div>
          ${c ? `<span class="fl-verdict ${c.val ? 'yes' : 'no'}">${c.val ? 'Ja' : 'Nein'}</span>` : ''}
          ${fields ? `<span class="fl-gear ${warn ? 'warn' : custom ? 'set' : ''}" title="${warn ? 'Einstellungen fehlen' : custom ? 'Eigene Einstellungen' : 'Einstellungen'}">${HO.icon('settings', 'sm')}</span>` : ''}</div>`
      }
      // Measure the size of a card and the position of its ports – the lines depend on them.
      function measure(n) {
        const el = cardEl(n.id)
        if (!el) return
        const r = { in: {}, out: {}, w: el.offsetWidth || NODE_W, h: el.offsetHeight || 80 }
        el.querySelectorAll('.fl-port').forEach(pe => {
          const dot = pe.querySelector('i')
          let x = dot.offsetWidth / 2, y = dot.offsetHeight / 2, e = dot
          while (e && e !== el) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent }
          r[pe.dataset.dir][pe.dataset.port] = [x, y]
        })
        st.rel[n.id] = r
      }
      // Redraw all cards and lines.
      function render() {
        world.innerHTML = flow.nodes.map(cardHTML).join('')
        flow.nodes.forEach(measure)
        markPorts()
        renderEdges()
        if (opts.onRender) opts.onRender()
      }
      // Redraw one card (after changed settings) and bring its lines up to date.
      function refreshNode(id) {
        const n = nodeById(id), el = cardEl(id)
        if (!n || !el) return render()
        el.outerHTML = cardHTML(n)
        measure(n)
        markPorts()
        renderEdges()
      }
      // Draw all lines: kind of the signal, selection, glow during a test run.
      function renderEdges() {
        let s = ''
        for (const e of flow.edges) {
          const a = portWorld(e.from, 'out', e.fromPort), b = portWorld(e.to, 'in', e.toPort)
          if (!a || !b) continue
          const kind = (portOf(nodeById(e.from), 'out', e.fromPort) || {}).kind || 'signal', d = wirePath(a, b)
          const near = st.sel.nodes.has(e.from) || st.sel.nodes.has(e.to), g = st.glow['e:' + e.id]
          s += `<path class="fl-wire k-${kind} ${st.sel.edge === e.id ? 'sel' : ''} ${near ? 'near' : ''} ${g ? 'flow ' + g : ''}" data-wire="${HO.esc(e.id)}" d="${d}"/><path class="fl-hit" data-edge="${HO.esc(e.id)}" d="${d}"/>`
        }
        gEdges.innerHTML = s
        placeDel()
      }
      // Apply the camera: cards by a transform, lines through the viewBox, the dot grid moves along.
      function applyCam() {
        const W = stage.clientWidth || 1, H = stage.clientHeight || 1
        world.style.transform = `translate(${-cam.x * cam.s}px,${-cam.y * cam.s}px) scale(${cam.s})`
        svg.setAttribute('viewBox', `${cam.x} ${cam.y} ${W / cam.s} ${H / cam.s}`)
        stage.style.backgroundSize = `${GRID * cam.s}px ${GRID * cam.s}px`
        stage.style.backgroundPosition = `${-cam.x * cam.s}px ${-cam.y * cam.s}px`
        placeDel()
      }
      // Put the button "Linie löschen" in the middle of the selected line.
      function placeDel() {
        const e = st.sel.edge && flow.edges.find(x => x.id === st.sel.edge)
        const a = e && portWorld(e.from, 'out', e.fromPort), b = e && portWorld(e.to, 'in', e.toPort)
        delBtn.classList.toggle('hidden', !(a && b))
        if (!a || !b) return
        const dx = Math.max(36, Math.abs(b[0] - a[0]) * 0.5)
        const m = [(a[0] + 3 * (a[0] + dx) + 3 * (b[0] - dx) + b[0]) / 8, (a[1] + 3 * a[1] + 3 * b[1] + b[1]) / 8]
        const q = toScreen(m)
        delBtn.style.left = q[0] + 'px'; delBtn.style.top = q[1] + 'px'
      }
      // highlight ports: while connecting (dragging or tapping) all fitting ones
      function markPorts() {
        const src = (st.drag && st.drag.mode === 'wire' && st.drag.kind) ? st.drag : st.pending
        world.querySelectorAll('.fl-port').forEach(pe => {
          const nid = pe.closest('.fl-node').dataset.node
          const isSrc = src && nid === src.node && pe.dataset.dir === src.dir && pe.dataset.port === src.port
          pe.classList.toggle('pending', !!(st.pending && isSrc))
          pe.classList.toggle('can', !!(src && compatible(src, nid, pe.dataset.dir, pe.dataset.port)))
        })
      }
      // Can the two ports be connected? Opposite direction, same kind of signal, not connected yet, not
      // the same node.
      function compatible(src, nid, dir, port) {
        if (nid === src.node || dir === src.dir) return false
        const p = portOf(nodeById(nid), dir, port)
        if (!p || p.kind !== src.kind) return false
        const [f, fp, t, tp] = src.dir === 'out' ? [src.node, src.port, nid, port] : [nid, port, src.node, src.port]
        return !flow.edges.some(e => e.from === f && e.fromPort === fp && e.to === t && e.toPort === tp)
      }
      // Show guide lines while aligning (vertical at gx, horizontal at gy) or remove them.
      function guides(gx, gy) {
        const W = stage.clientWidth / cam.s, H = stage.clientHeight / cam.s
        gGuides.innerHTML = (gx != null ? `<line x1="${gx}" y1="${cam.y}" x2="${gx}" y2="${cam.y + H}"/>` : '') +
          (gy != null ? `<line x1="${cam.x}" y1="${gy}" x2="${cam.x + W}" y2="${gy}"/>` : '')
      }

      /* ---------- Auswahl & Bearbeiten */
      function paintSel() {
        world.querySelectorAll('.fl-node').forEach(el => el.classList.toggle('sel', st.sel.nodes.has(el.dataset.node)))
        renderEdges()
        selChanged()
      }
      // Select exactly one node (without an argument: nothing).
      function selectOnly(id) { st.sel = { nodes: new Set(id ? [id] : []), edge: null }; paintSel() }
      // Add a node to the selection or remove it from it.
      function toggleSel(id) {
        st.sel.edge = null
        if (st.sel.nodes.has(id)) st.sel.nodes.delete(id); else st.sel.nodes.add(id)
        paintSel()
      }
      // Auswahl aufheben.
      function clearSel() { st.sel = { nodes: new Set(), edge: null }; paintSel() }
      // Select all nodes.
      function selectAll() { st.sel = { nodes: new Set(flow.nodes.map(n => n.id)), edge: null }; paintSel() }
      // after every line change: remove unfitting lines, renumber growing inputs
      function tidy() {
        const r = FS.normalize(flow.nodes, flow.edges)
        flow.edges.splice(0, flow.edges.length, ...r.edges)
        return r.removed
      }
      // Connect two ports. A yes/no input takes exactly one line: a new one replaces the old one.
      function connect(src, nid, dir, port) {
        const [from, fromPort, to, toPort] = src.dir === 'out' ? [src.node, src.port, nid, port] : [nid, port, src.node, src.port]
        // a yes/no input takes exactly one line: a new one replaces the old one
        const pin = portOf(nodeById(to), 'in', toPort)
        if (pin && pin.kind === 'bool') {
          flow.edges.splice(0, flow.edges.length, ...flow.edges.filter(e => !(e.to === to && e.toPort === toPort)))
        }
        flow.edges.push({ id: uid('e'), from, fromPort, to, toPort })
        tidy()
        render(); changed()
      }
      // Delete nodes together with their lines.
      function removeNodes(ids) {
        const gone = new Set(ids)
        if (!gone.size) return
        snap()
        flow.nodes.splice(0, flow.nodes.length, ...flow.nodes.filter(n => !gone.has(n.id)))
        flow.edges.splice(0, flow.edges.length, ...flow.edges.filter(e => !gone.has(e.from) && !gone.has(e.to)))
        gone.forEach(id => { delete st.rel[id]; st.sel.nodes.delete(id) })
        tidy()
        render(); changed(); selChanged()
      }
      const removeNode = id => removeNodes([id])
      // Delete one line.
      function removeEdge(id) {
        snap()
        flow.edges.splice(0, flow.edges.length, ...flow.edges.filter(e => e.id !== id))
        st.sel.edge = null
        tidy()
        render(); changed(); selChanged()
      }
      function removeSelected() {
        if (st.sel.edge) removeEdge(st.sel.edge)
        else removeNodes([...st.sel.nodes])
      }
      // settings of a node have changed (role, number of paths …): remove lines at ports that dropped out
      function configChanged(id) {
        const removed = tidy()
        render()
        return removed
      }
      // look for free room: new nodes do not cover existing ones – wide preferably to the right
      // (flow direction), narrow (phone) below, so that both stay in the picture
      const spots = narrow => {
        const out = []
        for (let i = -3; i <= 3; i++) for (let j = -3; j <= 3; j++) out.push([i, j])
        // narrow: stay in the same column and stack downwards – one column further would lie outside the picture
        const far = p => narrow ? Math.abs(p[0]) * 4 + (p[1] >= 0 ? p[1] : 2.5 - p[1]) : Math.abs(p[0]) + Math.abs(p[1])
        return out.sort((a, b) => far(a) - far(b) || (narrow ? b[1] - a[1] || b[0] - a[0] : b[0] - a[0] || b[1] - a[1]))
      }
      // Find free room for a new node near the wanted place (on the grid).
      function freeSpot(x, y) {
        const hit = (px, py) => flow.nodes.some(n => {
          const r = st.rel[n.id] || { w: NODE_W, h: 90 }
          return px < n.x + r.w + 16 && px + NODE_W + 16 > n.x && py < n.y + r.h + 16 && py + 90 + 16 > n.y
        })
        for (const [i, j] of spots(stage.clientWidth < 600)) {
          const px = Math.round((x + i * (NODE_W + 48)) / GRID) * GRID, py = Math.round((y + j * 130) / GRID) * GRID
          if (!hit(px, py)) return [px, py]
        }
        return [Math.round(x / GRID) * GRID, Math.round((y + 40) / GRID) * GRID]
      }
      // bring a node into the picture – only move as far as needed, so that the rest stays visible
      function reveal(n) {
        const ins = opts.insets ? opts.insets() : { l: 0, t: 0, r: 0, b: 0 }, r = st.rel[n.id] || { w: NODE_W, h: 90 }
        const a = toScreen([n.x, n.y]), b = toScreen([n.x + r.w, n.y + r.h]), m = 12
        const W = stage.clientWidth, H = stage.clientHeight
        const shift = (lo, hi, min, max) => lo < min ? lo - min : hi > max ? Math.min(hi - max, lo - min) : 0
        const dx = shift(a[0], b[0], ins.l + m, W - ins.r - m), dy = shift(a[1], b[1], ins.t + m, H - ins.b - m)
        if (!dx && !dy) return
        cam.x += dx / cam.s; cam.y += dy / cam.s
        applyCam()
      }
      // Create a new node – at the place of the pointer or in the centre – and select it.
      function addNode(type, clientX, clientY, config) {
        const sp = FS.byType[type]
        if (!sp) return null
        let p
        if (clientX != null) { const r = stage.getBoundingClientRect(); p = toWorld([clientX - r.left, clientY - r.top]); p = [p[0] - NODE_W / 2, p[1] - 24] }
        else { const c = center(); p = [c[0] - NODE_W / 2, c[1] - 50] }
        const [x, y] = freeSpot(p[0], p[1])
        snap()
        const n = { id: uid('n'), type, x, y, config: config || (opts.defaults ? opts.defaults(sp) : {}) }
        flow.nodes.push(n)
        st.sel = { nodes: new Set([n.id]), edge: null }
        render(); changed(); selChanged()
        if (clientX == null) reveal(n)
        return n
      }
      /* ---------- Copy / paste: nodes with all lines between them; clipboard in sessionStorage,
         so that pasting from one flow into another works too */
      function copyOf(ids) {
        const want = new Set(ids)
        const nodes = flow.nodes.filter(n => want.has(n.id)).map(n => { const c = clone(n); delete c.config.token; return c })
        const edges = flow.edges.filter(e => want.has(e.from) && want.has(e.to)).map(clone)
        return { nodes, edges }
      }
      // Copy the selection (with the lines between the nodes); returns the number.
      function copy() {
        if (!st.sel.nodes.size) return 0
        const clip = copyOf([...st.sel.nodes])
        try { sessionStorage.setItem('ho.flowClip', JSON.stringify(clip)) } catch (e) { }
        st.clip = clip
        st.pasteN = 0
        return clip.nodes.length
      }
      // Paste copied nodes with new ids, offset by `shift`, and select them.
      function insert(clip, shift) {
        const nodes = (clip.nodes || []).filter(n => FS.byType[n.type])
        if (!nodes.length) return 0
        snap()
        const ids = {}
        const added = nodes.map(n => {
          const c = clone(n)
          ids[n.id] = c.id = uid('n')
          c.x = Math.round((n.x + shift) / GRID) * GRID; c.y = Math.round((n.y + shift) / GRID) * GRID
          return c
        })
        flow.nodes.push(...added)
        for (const e of clip.edges || []) {
          if (ids[e.from] && ids[e.to]) flow.edges.push({ id: uid('e'), from: ids[e.from], fromPort: e.fromPort, to: ids[e.to], toPort: e.toPort })
        }
        tidy()
        st.sel = { nodes: new Set(added.map(n => n.id)), edge: null }
        render(); changed(); selChanged()
        reveal(added[0])
        return added.length
      }
      // Paste from the clipboard; repeated pasting offsets further each time.
      function paste() {
        let clip = st.clip
        try { clip = JSON.parse(sessionStorage.getItem('ho.flowClip')) || clip } catch (e) { }
        if (!clip || !Array.isArray(clip.nodes)) return 0
        st.pasteN++
        return insert(clip, PASTE_SHIFT * st.pasteN)
      }
      function duplicateNode(id) { return insert(copyOf([id]), PASTE_SHIFT) }
      function center() {
        const ins = opts.insets ? opts.insets() : { l: 0, t: 0, r: 0, b: 0 }
        return toWorld([(ins.l + stage.clientWidth - ins.r) / 2, (ins.t + stage.clientHeight - ins.b) / 2])
      }
      // Fit the whole flow into the surface (toolbars left free).
      function fit() {
        const W = stage.clientWidth || 1, H = stage.clientHeight || 1
        const ins = opts.insets ? opts.insets() : { l: 0, t: 0, r: 0, b: 0 }
        if (!flow.nodes.length) { cam.s = 1; cam.x = -ins.l - 40; cam.y = -ins.t - 40; return applyCam() }
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
        for (const n of flow.nodes) {
          const r = st.rel[n.id] || { w: NODE_W, h: 90 }
          x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + r.w); y1 = Math.max(y1, n.y + r.h)
        }
        const pad = 40, aw = W - ins.l - ins.r - 2 * pad, ah = H - ins.t - ins.b - 2 * pad
        cam.s = clamp(Math.min(aw / (x1 - x0), ah / (y1 - y0)), ZMIN, 1.15)
        cam.x = (x0 + x1) / 2 - (ins.l + (W - ins.l - ins.r) / 2) / cam.s
        cam.y = (y0 + y1) / 2 - (ins.t + (H - ins.t - ins.b) / 2) / cam.s
        applyCam()
      }
      // Zoom around a point of the surface: what lies there stays there.
      function zoomAt(f, sx, sy) {
        const w = toWorld([sx, sy])
        cam.s = clamp(cam.s * f, ZMIN, ZMAX)
        cam.x = w[0] - sx / cam.s; cam.y = w[1] - sy / cam.s
        applyCam()
      }

      /* ---------- Dragging nodes (the whole selection too): alignment > straight line > grid */
      function moveNode(d, dx, dy) {
        const n = d.n, r = st.rel[n.id] || { w: NODE_W, h: 80, in: {}, out: {} }, tol = ALIGN / cam.s
        const group = d.group
        let x = d.x0 + dx / cam.s, y = d.y0 + dy / cam.s, gx = null, gy = null
        // straight lines: own port at the height of the connected port
        for (const e of flow.edges) {
          if (gy != null) break
          let other = null, mine = null
          if (e.to === n.id && !group.has(e.from)) { other = portWorld(e.from, 'out', e.fromPort); mine = r.in[e.toPort] }
          else if (e.from === n.id && !group.has(e.to)) { other = portWorld(e.to, 'in', e.toPort); mine = r.out[e.fromPort] }
          if (other && mine && Math.abs(y + mine[1] - other[1]) < tol) { y = other[1] - mine[1]; gy = other[1] }
        }
        for (const o of flow.nodes) {
          if (group.has(o.id)) continue
          const ro = st.rel[o.id] || r
          if (gx == null && Math.abs(o.x - x) < tol) { x = o.x; gx = o.x }
          else if (gx == null && Math.abs(o.x + ro.w / 2 - (x + r.w / 2)) < tol) { x = o.x + ro.w / 2 - r.w / 2; gx = o.x + ro.w / 2 }
          if (gy == null && Math.abs(o.y - y) < tol) { y = o.y; gy = o.y }
        }
        if (gx == null) x = Math.round(x / GRID) * GRID
        if (gy == null) y = Math.round(y / GRID) * GRID
        const mx = x - d.x0, my = y - d.y0
        for (const [id, p0] of d.start) {
          const m = nodeById(id)
          if (!m) continue
          m.x = p0[0] + mx; m.y = p0[1] + my
          const el = cardEl(id)
          if (el) el.style.transform = `translate(${m.x}px,${m.y}px)`
        }
        guides(gx, gy)
        renderEdges()
      }

      /* ---------- Auswahlrahmen */
      function moveBand(d, q) {
        const a = toWorld(d.q0), b = toWorld(q)
        const x = Math.min(a[0], b[0]), y = Math.min(a[1], b[1]), w = Math.abs(a[0] - b[0]), h = Math.abs(a[1] - b[1])
        band.setAttribute('x', x); band.setAttribute('y', y); band.setAttribute('width', w); band.setAttribute('height', h)
        band.setAttribute('class', 'fl-band')
        const hit = new Set(d.base)
        for (const n of flow.nodes) {
          const r = st.rel[n.id] || { w: NODE_W, h: 90 }
          if (n.x < x + w && n.x + r.w > x && n.y < y + h && n.y + r.h > y) hit.add(n.id)
        }
        st.sel = { nodes: hit, edge: null }
        world.querySelectorAll('.fl-node').forEach(el => el.classList.toggle('sel', hit.has(el.dataset.node)))
      }
      // Auswahlrahmen beenden.
      function endBand() { band.setAttribute('class', 'fl-band hidden'); paintSel() }

      /* ---------- Linie ziehen */
      function startWire(d) {
        d.before = shot()
        if (d.dir === 'in') {   // input in use: pick up the existing line and re-plug it
          const ex = [...flow.edges].reverse().find(e => e.to === d.node && e.toPort === d.port)
          if (ex) {
            flow.edges.splice(flow.edges.indexOf(ex), 1)
            Object.assign(d, { detached: ex, node: ex.from, dir: 'out', port: ex.fromPort })
          }
        }
        const p = portOf(nodeById(d.node), d.dir, d.port)
        d.kind = p ? p.kind : 'signal'
        d.anchor = portWorld(d.node, d.dir, d.port)
        st.pending = null
        markPorts(); renderEdges()
      }
      // The fitting port within snapping distance of the pointer – the dragged line snaps to it.
      function nearestPort(d, q, e) {
        let best = null
        world.querySelectorAll('.fl-port.can').forEach(pe => {
          const nid = pe.closest('.fl-node').dataset.node, w = portWorld(nid, pe.dataset.dir, pe.dataset.port)
          if (!w) return
          const s = toScreen(w), dist = Math.hypot(s[0] - q[0], s[1] - q[1])
          if (dist < PORT_SNAP && (!best || dist < best.dist)) best = { nid, dir: pe.dataset.dir, port: pe.dataset.port, el: pe, w, dist }
        })
        if (best) return best
        // released over a node: nearest fitting port of this node (free ones first)
        const over = document.elementFromPoint(e.clientX, e.clientY), card = over && over.closest && over.closest('.fl-node')
        if (!card) return null
        card.querySelectorAll('.fl-port.can').forEach(pe => {
          const w = portWorld(card.dataset.node, pe.dataset.dir, pe.dataset.port)
          if (!w) return
          const s = toScreen(w), dist = Math.hypot(s[0] - q[0], s[1] - q[1]) - (pe.classList.contains('free') ? 1000 : 0)
          if (!best || dist < best.dist) best = { nid: card.dataset.node, dir: pe.dataset.dir, port: pe.dataset.port, el: pe, w, dist }
        })
        return best
      }
      // Lead the dragged line to the pointer or to the snapped port.
      function moveWire(d, q, e) {
        d.target = nearestPort(d, q, e)
        const p = d.target ? d.target.w : toWorld(q)
        const [a, b] = d.dir === 'out' ? [d.anchor, p] : [p, d.anchor]
        temp.setAttribute('d', wirePath(a, b))
        temp.setAttribute('class', `fl-wire fl-temp k-${d.kind}`)
        world.querySelectorAll('.fl-port.snap').forEach(x => x.classList.remove('snap'))
        if (d.target) d.target.el.classList.add('snap')
      }
      function endWire() {
        temp.setAttribute('class', 'fl-wire fl-temp hidden')
        world.querySelectorAll('.fl-port.snap').forEach(x => x.classList.remove('snap'))
      }
      // Port tapped: connect with a fitting port tapped before, otherwise note this one (connecting
      // without dragging).
      function tapPort(d) {
        const p = portOf(nodeById(d.node), d.dir, d.port)
        if (!p) return
        const pd = st.pending
        if (pd && compatible(pd, d.node, d.dir, d.port)) {
          snap()
          st.pending = null
          return connect(pd, d.node, d.dir, d.port)
        } else if (pd && pd.node === d.node && pd.dir === d.dir && pd.port === d.port) st.pending = null
        else {
          st.pending = { node: d.node, dir: d.dir, port: d.port, kind: p.kind }
          if (opts.onHint) opts.onHint(d.dir === 'out' ? 'Jetzt einen passenden Eingang antippen' : 'Jetzt einen passenden Ausgang antippen')
        }
        markPorts(); renderEdges()
      }

      /* ---------- Zeiger */
      const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
      const multiKey = e => e.shiftKey || e.ctrlKey || e.metaKey
      // Two fingers on the surface: abort a running drag and zoom.
      function startPinch() {
        const [a, b] = [...st.ptrs.values()]
        if (st.drag && st.drag.mode === 'wire') endWire()
        if (st.drag && st.drag.mode === 'band') endBand()
        clearTimeout(st.holdT)
        st.drag = { mode: 'pinch', d0: Math.max(10, Math.hypot(a[0] - b[0], a[1] - b[1])), s0: cam.s, anchor: toWorld(mid(a, b)) }
        guides(null, null)
      }
      // Pointer down: depending on the place start dragging a line, moving a node, a selection frame or panning the surface.
      function down(e) {
        if ((e.pointerType === 'mouse' && e.button > 0) || e.target.closest('.fl-edge-del')) return
        const q = local(e)
        st.ptrs.set(e.pointerId, q)
        try { stage.setPointerCapture(e.pointerId) } catch (err) { }
        if (st.ptrs.size === 2 && (!st.drag || st.drag.mode === 'pan' || !st.drag.moved)) return startPinch()
        if (st.ptrs.size > 1) return
        const base = { id: e.pointerId, q0: q, t0: Date.now(), moved: false, multi: multiKey(e) || st.selectMode }
        const portEl = e.target.closest('.fl-port'), nodeEl = e.target.closest('.fl-node'), hit = e.target.closest('.fl-hit')
        if (nodeEl && e.target.closest('.fl-fire')) st.drag = { ...base, mode: 'fire', n: nodeById(nodeEl.dataset.node) }
        else if (portEl && nodeEl) st.drag = { ...base, mode: 'wire', node: nodeEl.dataset.node, dir: portEl.dataset.dir, port: portEl.dataset.port }
        else if (nodeEl) { const n = nodeById(nodeEl.dataset.node); st.drag = { ...base, mode: 'node', n, x0: n.x, y0: n.y } }
        else if (hit) st.drag = { ...base, mode: 'edge', edge: hit.dataset.edge }
        else if (base.multi) st.drag = { ...base, mode: 'band', base: new Set(st.sel.nodes) }
        else {
          st.drag = { ...base, mode: 'pan', c0: [cam.x, cam.y] }
          // hold long on an empty area (tablet): selection frame instead of panning
          clearTimeout(st.holdT)
          if (e.pointerType !== 'mouse') {
            const d = st.drag
            st.holdT = setTimeout(() => {
              if (st.drag === d && !d.moved) { d.mode = 'band'; d.base = new Set(st.sel.nodes); d.held = true; moveBand(d, d.q0) }
            }, HOLD_MS)
          }
        }
      }
      // Pointer moved: zoom, lead a line, move nodes (with guide lines), drag a frame or pan the
      // surface.
      function move(e) {
        if (!st.ptrs.has(e.pointerId)) return
        const q = local(e)
        st.ptrs.set(e.pointerId, q)
        const d = st.drag
        if (d && d.mode === 'pinch') {
          if (st.ptrs.size < 2) return
          const [a, b] = [...st.ptrs.values()], m = mid(a, b)
          cam.s = clamp(d.s0 * Math.hypot(a[0] - b[0], a[1] - b[1]) / d.d0, ZMIN, ZMAX)
          cam.x = d.anchor[0] - m[0] / cam.s; cam.y = d.anchor[1] - m[1] / cam.s
          return applyCam()
        }
        if (!d || d.id !== e.pointerId) return
        const dx = q[0] - d.q0[0], dy = q[1] - d.q0[1]
        if (!d.moved) {
          if (Math.hypot(dx, dy) < TAP_MOVE) return
          d.moved = true
          clearTimeout(st.holdT)
          if (d.mode === 'edge' || d.mode === 'fire') Object.assign(d, { mode: 'pan', c0: [cam.x, cam.y] })
          else if (d.mode === 'wire') startWire(d)
          else if (d.mode === 'node') {
            // if the node belongs to the selection, the whole selection moves along – otherwise it is selected alone
            if (!st.sel.nodes.has(d.n.id)) { if (d.multi) toggleSel(d.n.id); else selectOnly(d.n.id) }
            d.group = new Set(st.sel.nodes)
            d.start = [...d.group].map(id => { const m = nodeById(id); return [id, [m.x, m.y]] })
            d.before = shot()
          }
        }
        if (d.mode === 'pan') { cam.x = d.c0[0] - dx / cam.s; cam.y = d.c0[1] - dy / cam.s; applyCam() }
        else if (d.mode === 'node') moveNode(d, dx, dy)
        else if (d.mode === 'wire') moveWire(d, q, e)
        else if (d.mode === 'band') moveBand(d, q)
      }
      // Pointer released: connect a line, finish moving, evaluate the frame – or treat it as a tap.
      function up(e) {
        if (!st.ptrs.has(e.pointerId)) return
        st.ptrs.delete(e.pointerId)
        const d = st.drag
        if (d && d.mode === 'pinch') {
          if (st.ptrs.size === 1) { const [id, q] = [...st.ptrs.entries()][0]; st.drag = { id, q0: q, t0: 0, moved: true, mode: 'pan', c0: [cam.x, cam.y] } }
          else if (!st.ptrs.size) st.drag = null
          return
        }
        if (!d || d.id !== e.pointerId) return
        st.drag = null
        clearTimeout(st.holdT)
        const tap = !d.moved && e.type === 'pointerup' && Date.now() - d.t0 < TAP_MS
        if (d.mode === 'wire') {
          if (d.moved) {
            endWire()
            if (d.target) { snap(d.before); connect(d, d.target.nid, d.target.dir, d.target.port) }
            else if (d.detached) { snap(d.before); tidy(); render(); changed() }
            else { markPorts(); renderEdges() }
          } else if (tap) tapPort(d)
        } else if (d.mode === 'node') {
          guides(null, null)
          if (d.moved) { snap(d.before); changed() }
          else if (tap) {
            if (d.multi) return toggleSel(d.n.id)
            selectOnly(d.n.id)
            // only open with the following click – otherwise the click (iOS) hits the new dialog backdrop and closes it
            st.tapHit = { n: d.n, at: Date.now() }
          }
        } else if (d.mode === 'fire' && tap) st.tapHit = { n: d.n, at: Date.now(), fire: true }
        else if (d.mode === 'edge' && tap) { st.sel = { nodes: new Set(), edge: d.edge }; paintSel() }
        else if (d.mode === 'band') {
          endBand()
          if (!d.moved && !d.held) clearSel()
        } else if (d.mode === 'pan' && tap) {
          const had = st.pending
          st.pending = null
          clearSel()
          if (had) markPorts()
          if (opts.onBackgroundTap) opts.onBackgroundTap()
        }
      }
      // Mouse wheel and trackpad: zoom around the pointer or pan.
      function wheel(e) {
        e.preventDefault()
        const q = local(e)
        if (!e.ctrlKey && e.deltaX) { cam.x += e.deltaX / cam.s; cam.y += e.deltaY / cam.s; applyCam() }
        else zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), q[0], q[1])
      }
      // Keys in the editor: Del, copy/paste, undo/redo, select all, 0 fits.
      function key(e) {
        if (document.querySelector('.sheet-bg') || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')) return
        const mod = e.ctrlKey || e.metaKey, k = (e.key || '').toLowerCase()
        if ((e.key === 'Delete' || e.key === 'Backspace') && (st.sel.nodes.size || st.sel.edge)) { e.preventDefault(); removeSelected() }
        else if (mod && k === 'c') { if (copy() && opts.onHint) opts.onHint('Kopiert') }
        else if (mod && k === 'v') { e.preventDefault(); paste() }
        else if (mod && k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo() }
        else if (mod && k === 'y') { e.preventDefault(); redo() }
        else if (mod && k === 'a') { e.preventDefault(); selectAll() }
        else if (e.key === 'Escape' && (st.sel.nodes.size || st.sel.edge || st.pending)) { st.pending = null; clearSel(); markPorts() }
        else if (e.key === '+' || e.key === '=') zoomAt(1.25, stage.clientWidth / 2, stage.clientHeight / 2)
        else if (e.key === '-') zoomAt(1 / 1.25, stage.clientWidth / 2, stage.clientHeight / 2)
        else if (e.key === '0') fit()
      }

      /* ---------- Live: what is running right now? Steps are played one after the other, so that one sees the path */
      function paintNode(id) {
        const el = cardEl(id), n = nodeById(id)
        if (!el || !n) return
        const l = st.live[id], lt = liveText(l)
        el.classList.toggle('run', !!st.glow[id])
        el.classList.toggle('waiting', !!(l && (l.until || l.round)))
        el.classList.toggle('holds', !!(l && l.on))
        // the display hangs on the card as a badge (absolute) – its size and the ports stay as they are
        const box = el.querySelector('.fl-live')
        if (box) { box.classList.toggle('hidden', !lt); box.querySelector('span').textContent = lt }
        let v = el.querySelector('.fl-verdict')
        const c = st.cond[id]
        if (c && !v) { v = document.createElement('span'); el.appendChild(v) }
        if (v) { if (c) { v.className = 'fl-verdict ' + (c.val ? 'yes' : 'no'); v.textContent = c.val ? 'Ja' : 'Nein' } else v.remove() }
      }
      // Let a node or line light up briefly (test run, live).
      function glow(key, cls) {
        const k = key
        st.glow[k] = cls || true
        clearTimeout(st.glowT && st.glowT[k])
        st.glowT = st.glowT || {}
        st.glowT[k] = setTimeout(() => {
          delete st.glow[k]
          if (k.indexOf('e:') === 0) paintWire(k.slice(2)); else paintNode(k)
        }, GLOW_MS)
        if (k.indexOf('e:') === 0) paintWire(k.slice(2)); else paintNode(k)
      }
      // Set the glow of a line on its element.
      function paintWire(id) {
        const w = gEdges.querySelector(`[data-wire="${CSS.escape(id)}"]`)
        if (!w) return
        const g = st.glow['e:' + id]
        w.classList.toggle('flow', !!g)
        w.classList.toggle('yes', g === 'yes'); w.classList.toggle('no', g === 'no')
      }
      // Show one step of a run: node and line light up, conditions show yes or no.
      function playStep(m) {
        if (m.ev === 'run') glow(m.node)
        else if (m.ev === 'step') { if (m.edge) glow('e:' + m.edge); glow(m.node) }
        else if (m.ev === 'cond') {
          if (m.edge) glow('e:' + m.edge, m.val ? 'yes' : 'no')
          st.cond[m.node] = { val: !!m.val }
          clearTimeout(st.condT && st.condT[m.node])
          st.condT = st.condT || {}
          st.condT[m.node] = setTimeout(() => { delete st.cond[m.node]; paintNode(m.node) }, 4000)
          paintNode(m.node)
        }
      }
      // Play the steps of a run one after the other, with a short pause in between.
      function pump() {
        const m = st.queue.shift()
        if (!m) { st.playing = false; return }
        st.playing = true
        playStep(m)
        st.playT = setTimeout(pump, STEP_MS)
      }
      // Take over a live message of the engine: wait times, counters and held states appear at the nodes.
      function live(m) {
        if (m.ev === 'wait') { st.live[m.node] = Object.assign({}, st.live[m.node], { until: m.until }); return paintNode(m.node) }
        if (m.ev === 'idle') {
          const l = Object.assign({}, st.live[m.node]); delete l.until; delete l.round
          st.live[m.node] = l
          return paintNode(m.node)
        }
        if (m.ev === 'state') { st.live[m.node] = Object.assign({}, st.live[m.node], { on: !!m.on }); return paintNode(m.node) }
        if (m.ev === 'run' || m.ev === 'step' || m.ev === 'cond') {
          if (st.queue.length > 150) st.queue.splice(0, st.queue.length - 150)   // storm: only show the newest
          st.queue.push(m)
          if (!st.playing) pump()
        }
      }
      // Set the live state of all nodes at once (when the editor opens).
      function setLive(map) {
        st.live = {}
        Object.keys(map || {}).forEach(id => { st.live[id] = Object.assign({}, map[id]) })
        flow.nodes.forEach(n => paintNode(n.id))
      }
      // show the last pass once more (path from /api/flows/<id> → last)
      function replay(last) {
        if (!last) return
        live({ ev: 'run', node: last.node })
        for (const p of last.path || []) {
          if (p[0] === 's') live({ ev: 'step', edge: p[1], node: p[2] })
          else if (p[0] === 'c') live({ ev: 'cond', edge: p[1], node: p[2], val: p[3] })
        }
      }
      const clockT = setInterval(() => {
        Object.keys(st.live).forEach(id => { if (st.live[id] && st.live[id].until) paintNode(id) })
      }, 1000)

      stage.addEventListener('pointerdown', down)
      stage.addEventListener('pointermove', move)
      stage.addEventListener('pointerup', up)
      stage.addEventListener('pointercancel', up)
      stage.addEventListener('wheel', wheel, { passive: false })
      stage.addEventListener('gesturestart', e => e.preventDefault())
      stage.addEventListener('click', () => {
        const h = st.tapHit
        st.tapHit = null
        if (!h || Date.now() - h.at > 700) return
        if (h.fire) { if (opts.onFire) opts.onFire(h.n) } else if (opts.onNodeTap) opts.onNodeTap(h.n)
      })
      delBtn.addEventListener('click', () => { if (st.sel.edge) removeEdge(st.sel.edge) })
      document.addEventListener('keydown', key)
      const ro = window.ResizeObserver ? new ResizeObserver(() => applyCam()) : null
      if (ro) ro.observe(stage)

      tidy()
      render()
      applyCam()
      requestAnimationFrame(() => { flow.nodes.forEach(measure); renderEdges(); fit() })

      return {
        render, refreshNode, fit, addNode, duplicateNode, removeNode, removeSelected, configChanged,
        copy, paste, undo, redo, snap, selectAll, clearSel, live, setLive, replay,
        hasClip() { try { return !!sessionStorage.getItem('ho.flowClip') || !!st.clip } catch (e) { return !!st.clip } },
        setSelectMode(on) { st.selectMode = !!on; stage.classList.toggle('picking', st.selectMode) },
        select: id => selectOnly(id),
        zoomBy: f => zoomAt(f, stage.clientWidth / 2, stage.clientHeight / 2),
        selected: () => (st.sel.nodes.size || st.sel.edge ? st.sel : null),
        // Tear down the drawing surface: unregister keys, timers and observers.
        destroy() {
          document.removeEventListener('keydown', key)
          clearInterval(clockT); clearTimeout(st.playT); clearTimeout(st.holdT)
          Object.keys(st.glowT || {}).forEach(k => clearTimeout(st.glowT[k]))
          Object.keys(st.condT || {}).forEach(k => clearTimeout(st.condT[k]))
          if (ro) ro.disconnect()
        }
      }
    }
  }
})(window.HO)
