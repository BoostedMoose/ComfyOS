/* ComfyOS – widget view of the home page (since 0.24): the board with its grid, the editor and the settings

   The home page can be switched from the classic view to the widget view (overview.js). A widget view consists of
   pages; every page is a grid of cells on which widgets lie freely – like the surfaces of the floor plan and of Flow:
   a dot grid, everything snaps to it. The stored form is described in widgets_store.py (data/widgets.json).

     HO.widgets.register(type, def)     a widget type (the catalogue is widget_types.js):
        title, icon, group              name, icon and group in the catalogue ('overview' | 'data' | 'action' | 'deco')
        desc                            one line for the catalogue
        size: [w, h]                    size of a new widget in a grid of 12 columns; min: [w, h] smallest size
        narrow: [w, h]                  size on the phone (grid of 4 columns) if it should differ
        needs: [capability, …]          what the home should have (keys of `caps`, like HO.blocks) – only a hint in
                                        the catalogue, never a plugin name
        head: false                     new widgets of this type start without a title line
        setup: true                     open the settings right after adding (a device or room has to be chosen)
        fields: [field, …]              settings of the type: { key, label, type, options, def, min, max, step, unit,
                                        hint, pro, show, load } with type 'select' | 'bool' | 'text' | 'textarea' | 'range' |
                                        'multi'; options = [[value, label], …] or a function (ctx) returning them;
                                        pro = id of the Pro feature this setting needs
        name(widget, ctx)               optional: title if the user gave none (e.g. the name of the chosen device)
        render(widget, ctx) -> HTML     content; rewritten only when it changes (HO.setHTML). Controls inside use the
                                        same data attributes as the classic home page – its handlers carry them out
        mount(body, widget, ctx)        instead of render, for content with a life of its own (floor plan, clock):
                                        returns { update(widget, ctx), resize(), destroy() }
     ctx = { ov, wx, w, h, sz, hz, editing, home, data(key, loader, maxAge), opt(key) }
        ov = /api/overview, wx = weather, w/h = size of the content in pixels, sz/hz = size class ('xs' | 's' | 'm' |
        'l'), home = helpers of the home page (HO.home), data() = cached request that redraws when it arrives,
        opt(key) = setting of the widget with the default of its field

     HO.widgets.mount(host, { bar, home, overview, doc }) -> board
        board.update(overview)          take over fresh data (every widget draws only what changed)
        board.editing, board.busy       editor open / a gesture or an open choice – do not redraw under the finger
        board.destroy()
     HO.widgets.load()                  fetch the stored view (GET /api/widgets)
     HO.widgets.types(), HO.widgets.get(type)

   Plans: the view is Basic. Further pages are the Pro feature `advanced_dashboard`; a setting marked `pro` needs its
   feature. The server enforces both (entitlements.check_widgets) – here it is only shown; nothing stored is ever
   removed because a plan does not cover it. */
(function (HO) {
  'use strict'

  const registry = {}, order = []
  const GROUPS = [['overview', 'Übersicht'], ['data', 'Daten'], ['action', 'Aktionen'], ['deco', 'Gestaltung']]
  const ROW_RATIO = { flat: 0.55, normal: 0.75, square: 1 }
  const NARROW_BELOW = 700          // container width in pixels below which the phone grid applies
  const BASE_COLS = 12              // the sizes of the catalogue are meant for this many columns
  const MAX_H = 24, MAX_UNDO = 40
  const HEAD = 46                   // height of the title line in pixels (app.css .wg-head)
  const FADE_ROWS = '.chips.scroll, .wx-hours'   // rows inside a widget that scroll sideways
  const STYLES = [['card', 'Kachel'], ['glass', 'Glas'], ['accent', 'Akzent'], ['bare', 'Ohne Rahmen']]
  const TINTS = ['', 'accent', 'ok', 'warn', 'bad', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 's8']
  const ICONS = ['home', 'grid', 'light', 'flame', 'bolt', 'media', 'map', 'star', 'sun', 'shield', 'layers', 'palette',
    'calendar', 'gauge', 'sparkle', 'presence']
  const cache = {}                  // shared request cache of all widgets: key -> { at, value, pending }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
  const copy = v => JSON.parse(JSON.stringify(v))
  const newId = prefix => prefix + Array.from({ length: 10 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

  // First free place for a size in a grid: row by row from the top, in every row from the left.
  function firstFit(list, w, h, cols) {
    w = Math.min(w, cols)
    for (let y = 0; y < 2000; y++) {
      for (let x = 0; x + w <= cols; x++) {
        const spot = { x, y, w, h }
        if (!list.some(o => hit(spot, o))) return spot
      }
    }
    return { x: 0, y: 0, w, h }
  }

  // Close gaps upwards: every widget rises as far as nothing is in its way (`skip` stays where it is).
  function compactUp(list, skip) {
    const sorted = list.slice().sort((a, b) => a.y - b.y || a.x - b.x)
    for (const it of sorted) {
      if (it === skip) continue
      while (it.y > 0 && !sorted.some(o => o !== it && hit({ x: it.x, y: it.y - 1, w: it.w, h: it.h }, o))) it.y--
    }
  }

  // Make room for a moved or resized widget: whatever it covers moves down, and what that covers in turn too.
  function settle(list, moved, compact) {
    const queue = [moved]
    let guard = 0
    while (queue.length && guard++ < 4000) {
      const a = queue.shift()
      for (const b of list) {
        if (b === a || b === moved || !hit(a, b)) continue
        b.y = a.y + a.h
        queue.push(b)
      }
    }
    if (compact) compactUp(list, moved)
  }

  /* ---------- Shared requests: several widgets (and several redraws) share one answer */
  // Value of a cached request, or undefined while the first answer is still on its way. `done` is called when
  // a fresh answer arrived.
  function cached(key, loader, maxAge, done) {
    const now = Date.now()
    const entry = cache[key] || (cache[key] = { at: 0, value: undefined, pending: null })
    if (!entry.pending && now - entry.at > maxAge) {
      const finish = value => { entry.at = Date.now(); entry.pending = null; if (value !== undefined) entry.value = value; if (done) done() }
      entry.pending = Promise.resolve().then(loader).then(finish, () => finish(entry.value === undefined ? null : undefined))
    }
    return entry.value
  }

  /* ---------- Fields of the settings dialog */
  const fieldOptions = (field, ctx) => (typeof field.options === 'function' ? field.options(ctx) : field.options) || []
  const fieldValue = (field, opts) => opts[field.key] !== undefined ? opts[field.key] : field.def
  // One field of the settings as HTML. `attr` is the data attribute that names it (data-f = setting of the type,
  // data-g = general setting of the widget, data-p = setting of the page).
  function fieldHTML(field, value, attr, ctx) {
    const name = `${attr}="${HO.esc(field.key)}"`
    const label = `${HO.esc(field.label)}${field.pro ? HO.proTag(field.pro) : ''}`
    const hint = field.hint ? `<span class="hint">${HO.esc(field.hint)}</span>` : ''
    if (field.type === 'bool') {
      return `<label class="check wg-f"><input type="checkbox" ${name} ${value ? 'checked' : ''}><span>${label}${hint ? `<br>${hint}` : ''}</span></label>`
    }
    if (field.type === 'select') {
      const list = fieldOptions(field, ctx)
      const known = list.some(o => String(o[0]) === String(value == null ? '' : value))
      return `<label class="field wg-f"><span>${label}</span><select ${name}>${known || value == null || value === '' ? '' : `<option value="${HO.esc(value)}" selected>(nicht mehr vorhanden)</option>`}
        ${list.map(o => `<option value="${HO.esc(o[0])}" ${String(o[0]) === String(value == null ? '' : value) ? 'selected' : ''}>${HO.esc(o[1])}</option>`).join('')}</select>${hint}</label>`
    }
    if (field.type === 'multi') {
      const chosen = Array.isArray(value) ? value : []
      const list = fieldOptions(field, ctx)
      return `<div class="field wg-f"><span>${label}</span><div class="chips wg-multi" ${name}>${list.map(o =>
        `<button type="button" class="chip ${chosen.indexOf(String(o[0])) >= 0 ? 'active' : ''}" data-v="${HO.esc(o[0])}">${HO.esc(o[1])}</button>`).join('') ||
        '<span class="small muted">Nichts zur Auswahl.</span>'}</div>${hint}</div>`
    }
    if (field.type === 'range') {
      return `<label class="field wg-f"><span>${label} · <b data-out="${HO.esc(field.key)}">${HO.esc(value)}${HO.esc(field.unit || '')}</b></span>
        <input type="range" ${name} min="${field.min}" max="${field.max}" step="${field.step || 1}" value="${HO.esc(value)}">${hint}</label>`
    }
    if (field.type === 'textarea') {
      return `<label class="field wg-f"><span>${label}</span><textarea ${name} rows="4" maxlength="${field.max || 400}">${HO.esc(value || '')}</textarea>${hint}</label>`
    }
    return `<label class="field wg-f"><span>${label}</span><input type="text" ${name} maxlength="${field.max || 60}" value="${HO.esc(value == null ? '' : value)}"
      placeholder="${HO.esc(field.placeholder || '')}">${hint}</label>`
  }
  // Read the value of a field from its control (after an input or a tap on a chip).
  function fieldRead(field, el, target) {
    if (field.type === 'bool') return !!el.checked
    if (field.type === 'range') return Number(el.value)
    if (field.type === 'multi') {
      if (target && target.dataset.v !== undefined) target.classList.toggle('active')
      return Array.from(el.querySelectorAll('.chip.active')).map(c => c.dataset.v)
    }
    if (field.type === 'select' && field.number) return Number(el.value)
    return el.value
  }

  /* ---------- The board */
  // Mount the widget view into `host`. `opts.bar` is the element for pages and buttons above it, `opts.home` the
  // helpers of the home page, `opts.doc` the stored view and `opts.overview` the state of the home.
  function mount(host, opts) {
    const S = {
      doc: opts.doc && Array.isArray(opts.doc.pages) ? opts.doc : { pages: [] },
      ov: opts.overview, page: 0, editing: false, sel: null, mode: 'wide', cols: BASE_COLS, cw: 80, rh: 60, gap: 12,
      live: null, auto: {}, drag: null, undo: [], saved: '', els: {}, sheet: null, dead: false
    }
    const bar = opts.bar
    S.saved = JSON.stringify(S.doc.pages)
    try { const id = localStorage.getItem('ho.homePage'); S.page = Math.max(0, S.doc.pages.findIndex(p => p.id === id)) } catch (e) { }
    host.classList.add('wg-board')
    host.innerHTML = '<div class="wg-grid"></div>'
    let grid = host.firstChild
    const page = () => S.doc.pages[S.page] || null
    const widgetById = id => { const p = page(); return p ? p.widgets.find(w => w.id === id) : null }
    const def = w => registry[w.type]

    /* ----- Places */
    const fitPlace = (p, cols) => { const w = clamp(p.w, 1, cols); return { x: clamp(p.x, 0, cols - w), y: Math.max(0, p.y), w, h: clamp(p.h, 1, MAX_H) } }
    // Place of a widget in the grid shown right now: while a gesture runs its provisional place, on the phone the
    // stored phone place or the one derived from the wide arrangement.
    function placeOf(w) {
      if (S.live && S.live[w.id]) return S.live[w.id]
      if (S.mode === 'narrow') return fitPlace(w.narrow || S.auto[w.id] || { x: 0, y: 0, w: S.cols, h: 2 }, S.cols)
      return fitPlace(w.wide, S.cols)
    }
    // Phone arrangement of the widgets that have none of their own: in the reading order of the wide grid, each
    // about as wide as there (in proportion), into the first free place.
    function deriveNarrow() {
      const p = page(), taken = []
      S.auto = {}
      if (!p) return
      p.widgets.forEach(w => { if (w.narrow) taken.push(fitPlace(w.narrow, p.cols_narrow)) })
      const rest = p.widgets.filter(w => !w.narrow).sort((a, b) => a.wide.y - b.wide.y || a.wide.x - b.wide.x)
      for (const w of rest) {
        const d = def(w) || {}, pref = d.narrow
        const width = pref ? pref[0] : Math.max(Math.min(2, p.cols_narrow), Math.round(w.wide.w * p.cols_narrow / p.cols))
        const spot = firstFit(taken, clamp(width, 1, p.cols_narrow), pref ? pref[1] : w.wide.h, p.cols_narrow)
        taken.push(spot)
        S.auto[w.id] = spot
      }
    }
    // The phone arrangement becomes the user's own as soon as it is changed there: write the derived places down.
    function freezeNarrow() {
      const p = page()
      if (!p || S.mode !== 'narrow') return
      p.widgets.forEach(w => { if (!w.narrow) w.narrow = copy(placeOf(w)) })
      S.auto = {}
    }
    const setPlace = (w, place) => { if (S.mode === 'narrow') w.narrow = copy(place); else w.wide = copy(place) }
    const places = () => page().widgets.map(w => Object.assign({ id: w.id }, placeOf(w)))

    /* ----- Measure and lay out */
    // Work out the grid for the current width: phone or wide grid, cell size, gap.
    function measure() {
      const p = page(), width = grid.clientWidth || host.clientWidth || 320
      S.mode = width < NARROW_BELOW ? 'narrow' : 'wide'
      if (!p) return
      S.cols = S.mode === 'narrow' ? p.cols_narrow : p.cols
      S.gap = S.mode === 'narrow' ? Math.min(p.gap, 12) : p.gap
      S.cw = (width - S.gap * (S.cols - 1)) / S.cols
      S.rh = Math.max(44, Math.round(S.cw * (ROW_RATIO[p.row] || ROW_RATIO.normal)))
      if (S.mode === 'narrow') deriveNarrow()
    }
    const rectOf = place => ({ left: place.x * (S.cw + S.gap), top: place.y * (S.rh + S.gap),
      width: place.w * S.cw + (place.w - 1) * S.gap, height: place.h * S.rh + (place.h - 1) * S.gap })
    const sizeClass = (v, a, b, c) => v < a ? 'xs' : v < b ? 's' : v < c ? 'm' : 'l'
    // Put every widget at its place and size; widgets with content of their own learn about a new size.
    function layout() {
      const p = page()
      if (!p) return
      let rows = 0
      for (const w of p.widgets) {
        const entry = S.els[w.id]
        if (!entry) continue
        const place = placeOf(w), r = rectOf(place), st = entry.el.style
        rows = Math.max(rows, place.y + place.h)
        st.left = r.left.toFixed(1) + 'px'; st.top = r.top.toFixed(1) + 'px'
        st.width = r.width.toFixed(1) + 'px'; st.height = r.height.toFixed(1) + 'px'
        const key = Math.round(r.width) + 'x' + Math.round(r.height)
        if (entry.size === key) continue
        entry.size = key
        entry.w = r.width; entry.h = r.height
        entry.el.dataset.sz = sizeClass(r.width, 150, 250, 420)
        entry.el.dataset.hz = sizeClass(r.height, 110, 190, 320)
        // --wg-k: how much room the content has (1 = a tile of about 170 × 130 pixels); big figures follow it
        st.setProperty('--wg-k', clamp(Math.min(r.width / 170, r.height / 130), 0.7, 2.4).toFixed(2))
        if (entry.ctl && entry.ctl.resize) { entry.ctl.resize(); markCuts(entry) }
        else if (!entry.ctl) drawWidget(w)       // the size class may change what a widget shows
      }
      if (S.editing) rows += 3                   // room to drop something below the last widget
      grid.style.height = Math.max(S.editing ? 4 : 0, rows) * (S.rh + S.gap) - (rows ? S.gap : 0) + 'px'
      grid.style.backgroundSize = `${(S.cw + S.gap).toFixed(2)}px ${(S.rh + S.gap).toFixed(2)}px`
      grid.style.backgroundPosition = `${(-S.gap / 2 - 1).toFixed(1)}px ${(-S.gap / 2 - 1).toFixed(1)}px`
    }

    /* ----- Draw */
    // What a widget gets to draw with: state of the home, weather, its size, helpers.
    function ctxFor(w) {
      const entry = S.els[w.id] || {}, d = def(w) || {}
      const head = w.head && w.style !== 'bare' ? HEAD : 0
      return {
        ov: S.ov, wx: opts.home.weather(), w: Math.max(40, (entry.w || 200) - 28), h: Math.max(30, (entry.h || 140) - 24 - head),
        sz: entry.el ? entry.el.dataset.sz : 'm', hz: entry.el ? entry.el.dataset.hz : 'm', editing: S.editing, home: opts.home,
        data: (key, loader, maxAge) => cached(key, loader, maxAge == null ? 30000 : maxAge, redrawSoon),
        opt: key => { const f = (d.fields || []).find(x => x.key === key); return w.opts[key] !== undefined ? w.opts[key] : f ? f.def : undefined }
      }
    }
    /* What does not fit scrolls inside the widget (the body up and down, rows of chips sideways). An edge behind which
       more content waits fades out instead of ending abruptly: the classes cut-t/cut-b and cut-l/cut-r say which edges
       those are (app.css .wg-fade, .wg-fade-x). Called after drawing, after a new size and while scrolling. */
    function markCuts(entry) {
      if (!entry || !entry.body) return
      // Set the two classes of one scroller: is content hidden before its start, after its end?
      const mark = (el, vertical) => {
        const pos = vertical ? el.scrollTop : el.scrollLeft
        const room = vertical ? el.scrollHeight - el.clientHeight : el.scrollWidth - el.clientWidth
        el.classList.toggle(vertical ? 'cut-t' : 'cut-l', pos > 1)
        el.classList.toggle(vertical ? 'cut-b' : 'cut-r', room - pos > 1)
      }
      mark(entry.body, true)
      const rows = entry.body.querySelectorAll(FADE_ROWS)
      for (let i = 0; i < rows.length; i++) { rows[i].classList.add('wg-fade-x'); mark(rows[i], false) }
    }
    const titleOf = (w, ctx) => w.title || (def(w) && def(w).name && def(w).name(w, ctx)) || (def(w) ? def(w).title : w.type)
    // Draw the content of one widget (and its title line) – written only if it changed.
    function drawWidget(w) {
      const entry = S.els[w.id], d = def(w)
      if (!entry) return
      const ctx = ctxFor(w)
      if (entry.head) HO.setHTML(entry.head, `<span class="wg-ic">${HO.icon(d ? d.icon : 'warn', 'sm')}</span><b>${HO.esc(titleOf(w, ctx))}</b>`)
      if (!d) return void HO.setHTML(entry.body, `<div class="wg-none">${HO.icon('warn')}<span>Dieses Widget kennt diese Version nicht.</span></div>`)
      try {
        if (entry.ctl) entry.ctl.update(w, ctx)
        else if (d.mount && !entry.tried) { entry.tried = true; entry.ctl = d.mount(entry.body, w, ctx) || null; if (!entry.ctl) HO.setHTML(entry.body, d.render ? d.render(w, ctx) : '') }
        else if (d.render) HO.setHTML(entry.body, d.render(w, ctx))
      } catch (e) {
        HO.setHTML(entry.body, `<div class="wg-none">${HO.icon('warn')}<span>Konnte nicht gezeichnet werden.</span></div>`)
      }
      markCuts(entry)
    }
    // Controls of a widget in the editor: handle to move, name, settings, copy, remove, corner to resize.
    function editHTML(w) {
      const d = def(w)
      return `<div class="wg-edit" data-wg-edit="${HO.esc(w.id)}">
        <button type="button" class="wg-grip" aria-label="Ziehen zum Verschieben" title="Ziehen zum Verschieben">${HO.icon('grip', 'sm')}</button>
        <b class="wg-edit-name">${HO.esc(titleOf(w, ctxFor(w)))}</b>
        <div class="wg-tools">
          <button type="button" class="btn-icon sm" data-wg-do="settings" aria-label="Einstellungen" title="Einstellungen">${HO.icon('settings', 'sm')}</button>
          <button type="button" class="btn-icon sm" data-wg-do="copy" aria-label="Duplizieren" title="Duplizieren">${HO.icon('copy', 'sm')}</button>
          <button type="button" class="btn-icon sm" data-wg-do="remove" aria-label="Entfernen" title="Entfernen">${HO.icon('trash', 'sm')}</button></div>
        <button type="button" class="wg-size" aria-label="Ziehen zum Ändern der Größe" title="Größe ändern">${HO.icon('maximize', 'sm')}</button></div>`
    }
    // Classes of a widget element from its settings (style, colour accent, alignment, selection).
    function widgetClass(w) {
      return `wg wg-s-${w.style} ${w.tint ? 'wg-tinted wg-t-' + w.tint : ''} wg-al-${w.align} wg-type-${w.type}${w.head && w.style !== 'bare' ? '' : ' wg-nohead'}${S.sel === w.id ? ' sel' : ''}`
    }
    // Build the element of a widget anew (after adding it or changing its settings).
    function buildWidget(w) {
      const old = S.els[w.id]
      if (old && old.ctl && old.ctl.destroy) old.ctl.destroy()
      const el = document.createElement('section')
      el.className = widgetClass(w)
      el.dataset.wg = w.id
      el.style.setProperty('--wg-z', (w.scale / 100).toFixed(2))
      el.innerHTML = `${w.head && w.style !== 'bare' ? '<header class="wg-head"></header>' : ''}<div class="wg-body wg-fade"></div>${S.editing ? editHTML(w) : ''}`
      if (old && old.el.parentNode === grid) grid.replaceChild(el, old.el); else grid.appendChild(el)
      S.els[w.id] = { el, head: el.querySelector('.wg-head'), body: el.querySelector('.wg-body'), ctl: null, size: '', w: 0, h: 0 }
    }
    // Tear down every widget element (page change, end of the board).
    function clearWidgets() {
      Object.keys(S.els).forEach(id => { const c = S.els[id].ctl; if (c && c.destroy) { try { c.destroy() } catch (e) { } } })
      S.els = {}
    }
    // Empty view: offer a suggestion built from what the home has, or an empty page.
    function emptyHTML() {
      return `<div class="wg-empty card"><div class="wg-empty-art">${HO.icon('grid', 'lg')}</div>
        <h2>Deine Widget-Ansicht</h2>
        <p class="muted">Stell dir die Startseite selbst zusammen: Uhr, Wetter, Räume, Szenen, Grundriss und mehr – frei auf einem Raster platziert, in jeder Größe.</p>
        ${S.doc.outdated ? `<div class="notice warn">${HO.icon('info', 'sm')}<div>Die gespeicherte Widget-Ansicht stammt aus einem älteren Format (veraltet) und wird nicht mehr angezeigt. Leg sie hier neu an.</div></div>` : ''}
        <div class="row wrap wg-empty-btns"><button class="btn primary" data-wg-bar="starter">${HO.icon('wand', 'sm')} Mit Vorschlag starten</button>
          <button class="btn" data-wg-bar="blank">${HO.icon('plus', 'sm')} Leer beginnen</button></div></div>`
    }
    // Draw the current page from scratch: grid, all widgets, the bar above.
    function render() {
      clearWidgets()
      const p = page()
      host.classList.toggle('editing', S.editing)
      host.dataset.backdrop = p ? p.backdrop : 'plain'
      if (!p) {
        host.innerHTML = emptyHTML()
        grid = host.firstChild
        return drawBar()
      }
      host.innerHTML = '<div class="wg-grid"></div>'
      grid = host.firstChild
      measure()
      p.widgets.forEach(buildWidget)
      layout()
      p.widgets.forEach(drawWidget)
      if (S.editing && !p.widgets.length) grid.insertAdjacentHTML('beforeend', `<div class="wg-hint">${HO.icon('plus')}<span>Tippe auf das Raster oder auf „Widget“, um etwas hinzuzufügen.</span></div>`)
      drawBar()
    }
    // The bar above the board: the pages and – in the editor – add, page settings, undo, done.
    function drawBar() {
      if (!bar) return
      const pages = S.doc.pages, tool = (act, icon, label, cls, extra) =>
        `<button class="btn sm wg-tool ${cls || ''}" data-wg-bar="${act}" title="${label}" aria-label="${label}" ${extra || ''}>${HO.icon(icon, 'sm')}<span class="lbl">${label}</span></button>`
      const tabs = pages.length > 1 || S.editing ? `<div class="chips scroll wg-pages">${pages.map((p, i) =>
        `<button type="button" class="chip ${i === S.page ? 'active' : ''}" data-wg-page="${i}">${HO.icon(p.icon, 'sm')}${HO.esc(p.name)}</button>`).join('')}
        ${S.editing ? `<button type="button" class="chip wg-page-add" data-wg-bar="addPage" title="Weitere Seite">${HO.icon('plus', 'sm')}Seite${HO.proTag('advanced_dashboard')}</button>` : ''}</div>` : '<div class="grow"></div>'
      const tools = !pages.length ? '' : S.editing
        ? `${tool('add', 'plus', 'Widget')}${tool('page', 'layers', 'Seite')}${tool('undo', 'undo', 'Rückgängig', '', S.undo.length ? '' : 'disabled')}${tool('done', 'check', 'Fertig', 'primary')}`
        : tool('edit', 'edit', 'Bearbeiten')
      HO.setHTML(bar, `${tabs}<div class="row g6 wg-bar-tools">${tools}</div>`)
    }

    /* ----- Changes: undo, save */
    // Remember the state before a change (for "Rückgängig").
    function touch() {
      S.undo.push(JSON.stringify(S.doc.pages))
      if (S.undo.length > MAX_UNDO) S.undo.shift()
    }
    let saveTimer = null
    // Send the view to the server. If the plan refuses a change (402), the last stored state comes back.
    async function saveNow() {
      saveTimer = null
      const body = JSON.stringify(S.doc.pages)
      if (body === S.saved) return
      try {
        await HO.api('widgets', { method: 'PUT', body: { pages: S.doc.pages } })
        S.saved = body
        S.doc.outdated = false
      } catch (e) {
        if (e.status === 402) { S.doc.pages = JSON.parse(S.saved); S.page = clamp(S.page, 0, Math.max(0, S.doc.pages.length - 1)); S.undo = []; if (!S.dead) render() }
        else HO.toast('Widget-Ansicht nicht gespeichert: ' + e.message, 'bad')
      }
    }
    const save = () => { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 500); drawBar() }
    // Take back the last change.
    function undo() {
      const last = S.undo.pop()
      if (!last) return
      S.doc.pages = JSON.parse(last)
      S.page = clamp(S.page, 0, Math.max(0, S.doc.pages.length - 1))
      S.sel = null
      render()
      save()
    }
    // Show another page and remember it on this device.
    function showPage(index) {
      S.page = clamp(index, 0, Math.max(0, S.doc.pages.length - 1))
      S.sel = null
      try { if (page()) localStorage.setItem('ho.homePage', page().id) } catch (e) { }
      render()
    }

    /* ----- Editor: add, copy, remove */
    // A new widget of a type with the defaults of its fields, sized for the grid of the page.
    function newWidget(type, at) {
      const p = page(), d = registry[type], opt = {}
      ;(d.fields || []).forEach(f => { if (f.def !== undefined) opt[f.key] = copy(f.def) })
      const size = d.size || [3, 2], small = d.narrow || [Math.min(p.cols_narrow, Math.max(2, Math.round(size[0] * p.cols_narrow / BASE_COLS))), size[1]]
      const wide = { w: clamp(Math.round(size[0] * p.cols / BASE_COLS), 1, p.cols), h: size[1] }
      const w = { id: newId('w'), type, title: '', head: d.head !== false, style: d.style || 'card', tint: '', scale: 100, align: d.align || 'start',
        wide: null, narrow: null, opts: opt }
      // on the phone the new widget gets a phone place of its own (where it was put); the others keep following the
      // wide arrangement until they are moved there themselves
      const here = p.widgets.map(placeOf), size2 = S.mode === 'narrow' ? [Math.min(small[0], p.cols_narrow), small[1]] : [wide.w, wide.h]
      const want = { x: at ? clamp(at.x, 0, S.cols - size2[0]) : 0, y: at ? at.y : 0, w: size2[0], h: size2[1] }
      const spot = at && !here.some(o => hit(want, o)) ? want : firstFit(here, size2[0], size2[1], S.cols)
      if (S.mode === 'narrow') { w.narrow = spot; w.wide = firstFit(p.widgets.map(o => o.wide), wide.w, wide.h, p.cols) } else w.wide = spot
      return w
    }
    // Add a widget of the chosen type (at a tapped cell, otherwise at the first free place) and select it.
    function addWidget(type, at) {
      const p = page()
      if (!p || !registry[type]) return
      if (p.widgets.length >= ((S.doc.limits || {}).widgets || 60)) return HO.toast('Mehr Widgets passen nicht auf eine Seite.', 'warn')
      touch()
      const w = newWidget(type, at)
      p.widgets.push(w)
      S.sel = w.id
      render()
      save()
      const entry = S.els[w.id]
      if (entry && entry.el.scrollIntoView) entry.el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      if (registry[type].setup) widgetSheet(w.id)
    }
    // Copy a widget with all its settings into the first free place.
    function copyWidget(id) {
      const p = page(), src = widgetById(id)
      if (!src) return
      touch()
      const w = copy(src), now = placeOf(src)
      w.id = newId('w')
      w.wide = firstFit(p.widgets.map(o => o.wide), src.wide.w, src.wide.h, p.cols)
      w.narrow = S.mode === 'narrow' ? firstFit(p.widgets.map(placeOf), now.w, now.h, p.cols_narrow) : null
      p.widgets.push(w)
      S.sel = w.id
      render()
      save()
    }
    // Remove a widget; the others close the gap if the page is set to do so.
    function removeWidget(id) {
      const p = page()
      if (!p || !widgetById(id)) return
      touch()
      p.widgets = p.widgets.filter(w => w.id !== id)
      if (p.compact) { const list = places(); compactUp(list, null); list.forEach(it => setPlaceById(it)) }
      S.sel = null
      render()
      save()
      HO.toast('Widget entfernt – „Rückgängig“ holt es zurück.')
    }
    const setPlaceById = it => { const w = widgetById(it.id); if (w) setPlace(w, { x: it.x, y: it.y, w: it.w, h: it.h }) }
    // Select a widget in the editor (null = none).
    function select(id) {
      if (S.sel === id) return
      S.sel = id
      Object.keys(S.els).forEach(k => S.els[k].el.classList.toggle('sel', k === id))
    }

    /* ----- Editor: move and resize */
    // Try a new place for a widget: the others make room; nothing is stored yet, only shown.
    function tryPlace(id, target) {
      const list = S.drag ? copy(S.drag.base) : places()
      const me = list.find(it => it.id === id)
      Object.assign(me, target)
      settle(list, me, page().compact)
      S.live = {}
      list.forEach(it => { S.live[it.id] = { x: it.x, y: it.y, w: it.w, h: it.h } })
      layout()
    }
    // Make the provisional places permanent and save.
    function commitPlaces() {
      const p = page()
      if (!S.live) return
      const live = S.live
      S.live = null
      const changed = p.widgets.some(w => { const a = placeOf(w), b = live[w.id]; return b && (a.x !== b.x || a.y !== b.y || a.w !== b.w || a.h !== b.h) })
      if (changed) {
        touch()
        freezeNarrow()
        const list = p.widgets.map(w => Object.assign({ id: w.id }, live[w.id] || placeOf(w)))
        if (p.compact) compactUp(list, null)
        list.forEach(setPlaceById)
        save()
      }
      layout()
    }
    const minOf = w => { const d = def(w) || {}, m = d.min || [1, 1]; return [Math.min(S.cols, S.mode === 'narrow' ? Math.min(m[0], 2) : m[0]), m[1]] }
    // Move or resize the selected widget by cells (arrow keys, buttons of the dialog).
    function nudge(id, dx, dy, dw, dh) {
      const w = widgetById(id)
      if (!w) return
      const a = placeOf(w), min = minOf(w)
      const width = clamp(a.w + dw, min[0], S.cols), height = clamp(a.h + dh, min[1], MAX_H)
      tryPlace(id, { x: clamp(a.x + dx, 0, S.cols - width), y: Math.max(0, a.y + dy), w: width, h: height })
      commitPlaces()
    }
    // Scroll along while a widget is dragged near the upper or lower edge of the screen.
    function dragScroll() {
      if (!S.drag) return
      const edge = 70, y = S.drag.cy, h = window.innerHeight
      const v = y < edge ? -(edge - y) / 3 : y > h - edge ? (y - (h - edge)) / 3 : 0
      if (v) { window.scrollBy(0, v); dragMove(S.drag.cx, S.drag.cy) }
      S.drag.raf = requestAnimationFrame(dragScroll)
    }
    // The pointer moved during a drag: work out the cell it is over and try that place.
    function dragMove(x, y) {
      const d = S.drag, px = S.cw + S.gap, py = S.rh + S.gap
      d.cx = x; d.cy = y
      const dx = x - d.sx, dy = y - d.sy + (window.pageYOffset - d.scroll)
      if (!d.moved && Math.hypot(dx, dy) < 5) return
      d.moved = true
      const from = d.from, min = minOf(widgetById(d.id))
      let target
      if (d.kind === 'size') {
        const w = clamp(Math.round((from.w * px + dx) / px), min[0], S.cols - from.x)
        target = { x: from.x, y: from.y, w, h: clamp(Math.round((from.h * py + dy) / py), min[1], MAX_H) }
      } else {
        target = { x: clamp(Math.round((from.x * px + dx) / px), 0, S.cols - from.w), y: Math.max(0, Math.round((from.y * py + dy) / py)), w: from.w, h: from.h }
      }
      if (d.last && d.last.x === target.x && d.last.y === target.y && d.last.w === target.w && d.last.h === target.h) return
      d.last = target
      tryPlace(d.id, target)
    }
    // Pointer down in the editor: select; start moving (mouse and pen anywhere on the widget, a finger only at the
    // handle – otherwise it scrolls the page) or resizing (corner).
    function onDown(e) {
      if (!S.editing || S.drag) return
      const over = e.target.closest && e.target.closest('.wg-edit')
      if (!over) return
      select(over.dataset.wgEdit)
      if (e.target.closest('[data-wg-do]')) return
      const sizing = !!e.target.closest('.wg-size'), gripping = !!e.target.closest('.wg-grip')
      if (e.pointerType === 'mouse' ? e.button !== 0 : !(sizing || gripping || e.pointerType === 'pen')) return
      e.preventDefault()
      const w = widgetById(over.dataset.wgEdit)
      S.drag = { id: w.id, kind: sizing ? 'size' : 'move', pointer: e.pointerId, sx: e.clientX, sy: e.clientY, cx: e.clientX, cy: e.clientY,
        scroll: window.pageYOffset, from: copy(placeOf(w)), base: places(), moved: false, last: null, raf: 0 }
      S.els[w.id].el.classList.add('wg-dragging')
      host.classList.add('wg-moving')
      S.drag.raf = requestAnimationFrame(dragScroll)
    }
    const onMove = e => { if (S.drag && e.pointerId === S.drag.pointer) { e.preventDefault(); dragMove(e.clientX, e.clientY) } }
    // End of a drag: keep the new arrangement (or nothing, if the pointer hardly moved).
    function onUp(e) {
      const d = S.drag
      if (!d || (e && e.pointerId !== d.pointer)) return
      cancelAnimationFrame(d.raf)
      S.drag = null
      host.classList.remove('wg-moving')
      const entry = S.els[d.id]
      if (entry) entry.el.classList.remove('wg-dragging')
      if (d.moved && (!e || e.type !== 'pointercancel')) commitPlaces(); else { S.live = null; layout() }
    }
    // Keys in the editor: arrows move the selected widget, with Shift they resize it; Delete removes, Escape lifts the selection.
    function onKey(e) {
      if (!S.editing || !S.sel || document.querySelector('.sheet-bg') || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')) return
      const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key]
      if (step) { e.preventDefault(); return e.shiftKey ? nudge(S.sel, 0, 0, step[0], step[1]) : nudge(S.sel, step[0], step[1], 0, 0) }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); removeWidget(S.sel) }
      else if (e.key === 'Escape') select(null)
      else if (e.key === 'Enter') widgetSheet(S.sel)
    }
    // Cell of the grid under a point of the screen.
    function cellAt(x, y) {
      const r = grid.getBoundingClientRect()
      return { x: clamp(Math.floor((x - r.left) / (S.cw + S.gap)), 0, S.cols - 1), y: Math.max(0, Math.floor((y - r.top) / (S.rh + S.gap))) }
    }

    /* ----- Dialogs: catalogue, widget, page */
    // Catalogue "Widget hinzufügen": all types by group; `at` = cell the new widget should go to.
    function catalogue(at) {
      const devices = (S.ov && S.ov.devices) || []
      const fits = d => !d.needs || !d.needs.length || d.needs.some(key => HO.caps.devices(devices, key).length)
      const tile = d => `<button type="button" class="wg-cat ${fits(d) ? '' : 'dim'}" data-type="${d.type}">
        <span class="wg-cat-ic">${HO.icon(d.icon)}</span><b>${HO.esc(d.title)}${d.proHint ? HO.proTag(d.proHint) : ''}</b>
        <span class="small muted">${HO.esc(fits(d) ? d.desc : 'Kein passendes Gerät im Zuhause')}</span></button>`
      const s = HO.sheet({ title: 'Widget hinzufügen', wide: true, body: GROUPS.map(g => {
        const list = order.map(t => registry[t]).filter(d => d.group === g[0])
        return list.length ? `<div class="eyebrow wg-cat-head">${g[1]}</div><div class="wg-cat-grid">${list.map(tile).join('')}</div>` : ''
      }).join('') })
      s.body().addEventListener('click', e => {
        const b = e.target.closest('[data-type]')
        if (!b) return
        s.close()
        addWidget(b.dataset.type, at)
      })
    }
    // Rebuild one widget after its settings changed and draw it.
    function refreshWidget(w) {
      buildWidget(w)
      measure()
      layout()
      drawWidget(w)
    }
    const GENERAL = [
      { key: 'title', label: 'Titel', type: 'text', max: 60 },
      { key: 'head', label: 'Titelzeile anzeigen', type: 'bool' },
      { key: 'style', label: 'Stil', type: 'select', options: STYLES },
      { key: 'align', label: 'Inhalt ausrichten', type: 'select', options: [['start', 'Oben'], ['center', 'Mitte'], ['end', 'Unten']] },
      { key: 'scale', label: 'Größe des Inhalts', type: 'range', min: 60, max: 180, step: 5, unit: ' %' }
    ]
    // Settings of a widget: content (fields of its type), look, and its exact place and size in the grid.
    function widgetSheet(id) {
      const w = widgetById(id), d = w && def(w)
      if (!w || !d) return
      const ctx = ctxFor(w), general = GENERAL.map(f => Object.assign({}, f, { placeholder: f.key === 'title' ? titleOf(Object.assign({}, w, { title: '' }), ctx) : '' }))
      const shown = f => !f.show || f.show(w.opts)
      const contentHTML = () => (d.fields || []).filter(shown).map(f => fieldHTML(f, fieldValue(f, w.opts), 'data-f', ctx)).join('') ||
        '<p class="small muted">Dieses Widget hat keine eigenen Einstellungen.</p>'
      // Exact place and size as numbers (column, row, width, height) with shortcuts.
      const placeHTML = () => {
        const a = placeOf(w), num = (key, label, value, min, max) => `<label class="field"><span>${label}</span><input type="number" data-place="${key}" min="${min}" max="${max}" value="${value}"></label>`
        return `<div class="wg-place">${num('x', 'Spalte', a.x + 1, 1, S.cols)}${num('y', 'Zeile', a.y + 1, 1, 400)}${num('w', 'Breite', a.w, 1, S.cols)}${num('h', 'Höhe', a.h, 1, MAX_H)}</div>
          <div class="row wrap g6 wg-place-btns"><button type="button" class="btn sm" data-place-do="full">Volle Breite</button>
            <button type="button" class="btn sm" data-place-do="half">Halbe Breite</button><button type="button" class="btn sm" data-place-do="top">Ganz nach oben</button></div>
          <p class="tiny muted">Raster mit ${S.cols} Spalten${S.mode === 'narrow' ? ' (Handy-Anordnung – der breite Bildschirm hat seine eigene)' : ''}. Im Raster ziehst du am Griff, die Größe an der Ecke; Pfeiltasten verschieben, mit Umschalt ändern sie die Größe.</p>`
      }
      const s = HO.sheet({ title: d.title, wide: true, body: `<div class="wg-sheet">
          <div><div class="eyebrow">Inhalt</div><div data-box="content">${contentHTML()}</div></div>
          <div><div class="eyebrow">Darstellung</div>${general.map(f => fieldHTML(f, w[f.key], 'data-g', ctx)).join('')}
            <div class="field wg-f"><span>Farbakzent</span><div class="wg-tints" data-tints>${TINTS.map(t =>
              `<button type="button" class="wg-tint ${t ? 'wg-t-' + t : 'none'} ${w.tint === t ? 'active' : ''}" data-tint="${t}" title="${t ? 'Farbakzent' : 'Kein Akzent'}" aria-label="${t ? 'Farbakzent ' + t : 'Kein Akzent'}"></button>`).join('')}</div></div>
            <div class="eyebrow wg-sep">Position und Größe</div><div data-box="place">${placeHTML()}</div></div></div>`,
        actions: [{ label: 'Entfernen', kind: 'ghost danger', icon: 'trash', onClick: () => removeWidget(id) },
          { label: 'Duplizieren', kind: 'ghost', icon: 'copy', onClick: () => copyWidget(id) }, { label: 'Fertig', kind: 'primary' }] })
      S.sheet = s
      let touched = false
      const before = () => { if (!touched) { touched = true; touch() } }
      const body = s.body()
      // a choice that has to be fetched first (e.g. the flows): draw the fields again once it is there
      const loads = (d.fields || []).filter(f => f.load).map(f => f.load(ctx))
      if (loads.length) {
        Promise.all(loads).then(() => { const box = body.querySelector('[data-box=content]'); if (box && body.isConnected) box.innerHTML = contentHTML() })
          .catch(() => { })
      }
      // An input in the dialog: take the value over at once – the widget behind the dialog shows it.
      const onInput = e => {
        const t = e.target
        const fEl = t.closest('[data-f]'), gEl = t.closest('[data-g]')
        if (fEl) {
          const f = d.fields.find(x => x.key === fEl.dataset.f)
          if (!f || (f.type === 'multi' && e.type !== 'click') || (f.type !== 'multi' && e.type === 'click')) return
          if (f.type === 'multi' && !t.closest('[data-v]')) return
          const value = fieldRead(f, fEl, t.closest('[data-v]'))
          if (f.pro && value === true && !HO.can(f.pro)) {
            fEl.checked = false
            return HO.upsell({ title: f.label, message: `„${f.label}“ gehört zu ComfyOS Pro.` })
          }
          before()
          w.opts[f.key] = value
          const out = body.querySelector(`[data-out="${f.key}"]`)
          if (out) out.textContent = value + (f.unit || '')
          if (e.type === 'change' && d.fields.some(x => x.show)) body.querySelector('[data-box=content]').innerHTML = contentHTML()
        } else if (gEl && e.type !== 'click') {
          const f = GENERAL.find(x => x.key === gEl.dataset.g)
          before()
          w[f.key] = f.key === 'title' ? gEl.value.trim() : fieldRead(f, gEl)
          const out = body.querySelector(`[data-out="${f.key}"]`)
          if (out) out.textContent = w[f.key] + (f.unit || '')
        } else if (t.dataset.place && e.type === 'change') {
          const a = Object.assign({}, placeOf(w)), v = Math.round(Number(t.value)) || 1, min = minOf(w)
          if (t.dataset.place === 'x') a.x = clamp(v - 1, 0, S.cols - a.w)
          if (t.dataset.place === 'y') a.y = Math.max(0, v - 1)
          if (t.dataset.place === 'w') { a.w = clamp(v, min[0], S.cols); a.x = Math.min(a.x, S.cols - a.w) }
          if (t.dataset.place === 'h') a.h = clamp(v, min[1], MAX_H)
          tryPlace(id, a)
          commitPlaces()
          body.querySelector('[data-box=place]').innerHTML = placeHTML()
          return
        } else return
        refreshWidget(w)
        save()
      }
      body.addEventListener('input', onInput)
      body.addEventListener('change', onInput)
      body.addEventListener('click', e => {
        const tint = e.target.closest('[data-tint]'), act = e.target.closest('[data-place-do]')
        if (tint) {
          before()
          w.tint = tint.dataset.tint
          body.querySelectorAll('[data-tint]').forEach(b => b.classList.toggle('active', b === tint))
          refreshWidget(w)
          return save()
        }
        if (act) {
          const a = Object.assign({}, placeOf(w)), min = minOf(w)
          if (act.dataset.placeDo === 'full') { a.x = 0; a.w = S.cols }
          if (act.dataset.placeDo === 'half') { a.w = Math.max(min[0], Math.floor(S.cols / 2)); a.x = Math.min(a.x, S.cols - a.w) }
          if (act.dataset.placeDo === 'top') a.y = 0
          tryPlace(id, a)
          commitPlaces()
          body.querySelector('[data-box=place]').innerHTML = placeHTML()
          return
        }
        onInput(e)
      })
    }
    // Change the number of columns of a grid: places and widths follow in proportion.
    function rescale(p, key, from, to) {
      p.widgets.forEach(w => {
        const a = w[key]
        if (!a) return
        const width = clamp(Math.round(a.w * to / from), 1, to)
        w[key] = { x: clamp(Math.round(a.x * to / from), 0, to - width), y: a.y, w: width, h: a.h }
      })
    }
    // Settings of the page: name, icon, grid (columns, row height, gap, closing gaps), background, order, deleting.
    function pageSheet() {
      const p = page()
      if (!p) return
      const fields = [
        { key: 'name', label: 'Name', type: 'text', max: 30 },
        { key: 'cols', label: 'Spalten am breiten Bildschirm', type: 'range', min: 4, max: 24, step: 1, hint: 'Mehr Spalten = feineres Raster. Die Widgets behalten ihre Lage und Größe im Verhältnis.' },
        { key: 'cols_narrow', label: 'Spalten am Handy', type: 'range', min: 2, max: 6, step: 1 },
        { key: 'row', label: 'Höhe einer Zeile', type: 'select', options: [['flat', 'Flach'], ['normal', 'Normal'], ['square', 'Quadratisch']] },
        { key: 'gap', label: 'Abstand zwischen den Widgets', type: 'range', min: 0, max: 32, step: 2, unit: ' px' },
        { key: 'compact', label: 'Lücken nach oben schließen', type: 'bool', hint: 'Aus: Widgets bleiben genau dort liegen, wo du sie hinlegst.' },
        { key: 'backdrop', label: 'Hintergrund', type: 'select', options: [['plain', 'Schlicht'], ['dots', 'Punktraster'], ['glow', 'Leuchten']] }
      ]
      const s = HO.sheet({ title: 'Seite', wide: true, body: `<div class="wg-sheet"><div>${fields.slice(0, 1).map(f => fieldHTML(f, p[f.key], 'data-p')).join('')}
          <div class="field wg-f"><span>Symbol</span><div class="wg-icons">${ICONS.map(i =>
            `<button type="button" class="btn-icon sm ${p.icon === i ? 'on' : ''}" data-icon="${i}" aria-label="Symbol ${i}">${HO.icon(i, 'sm')}</button>`).join('')}</div></div>
          ${fields.slice(6).map(f => fieldHTML(f, p[f.key], 'data-p')).join('')}
          <div class="row wrap g6 wg-page-btns"><button type="button" class="btn sm" data-page-do="left">${HO.icon('back', 'sm')} Nach vorn</button>
            <button type="button" class="btn sm" data-page-do="right">Nach hinten ${HO.icon('chevron', 'sm')}</button></div></div>
          <div><div class="eyebrow">Raster</div>${fields.slice(1, 6).map(f => fieldHTML(f, p[f.key], 'data-p')).join('')}
            <button type="button" class="btn sm" data-page-do="phone">${HO.icon('refresh', 'sm')} Handy-Anordnung neu berechnen</button>
            <p class="tiny muted">Handy und breiter Bildschirm haben je eine eigene Anordnung. Ohne eigene Handy-Anordnung leitet ComfyOS sie aus der breiten ab.</p></div></div>`,
        actions: [{ label: 'Seite löschen', kind: 'ghost danger', icon: 'trash', onClick: async () => {
          if (!await HO.confirm(`„${p.name}“ mit ${p.widgets.length} Widget${p.widgets.length === 1 ? '' : 's'} löschen?`, { title: 'Seite löschen', ok: 'Löschen', danger: true })) return true
          touch()
          S.doc.pages.splice(S.page, 1)
          showPage(S.page - 1)
          save()
        } }, { label: 'Fertig', kind: 'primary' }] })
      S.sheet = s
      let touched = false
      const before = () => { if (!touched) { touched = true; touch() } }
      const body = s.body()
      // An input in the page dialog: apply at once, the board behind shows it.
      const apply = e => {
        const el = e.target.closest('[data-p]')
        if (!el) return
        const f = fields.find(x => x.key === el.dataset.p), value = fieldRead(f, el)
        before()
        if (f.key === 'name') p.name = String(value).trim() || 'Seite'
        else if (f.key === 'cols') { rescale(p, 'wide', p.cols, value); p.cols = value }
        else if (f.key === 'cols_narrow') { rescale(p, 'narrow', p.cols_narrow, value); p.cols_narrow = value }
        else p[f.key] = value
        const out = body.querySelector(`[data-out="${f.key}"]`)
        if (out) out.textContent = value + (f.unit || '')
        render()
        save()
      }
      body.addEventListener('input', apply)
      body.addEventListener('change', apply)
      body.addEventListener('click', e => {
        const icon = e.target.closest('[data-icon]'), act = e.target.closest('[data-page-do]')
        if (icon) {
          before()
          p.icon = icon.dataset.icon
          body.querySelectorAll('[data-icon]').forEach(b => b.classList.toggle('on', b === icon))
          drawBar()
          return save()
        }
        if (!act) return
        before()
        if (act.dataset.pageDo === 'phone') { p.widgets.forEach(w => { w.narrow = null }); HO.toast('Handy-Anordnung folgt wieder der breiten.') }
        else {
          const to = S.page + (act.dataset.pageDo === 'left' ? -1 : 1)
          if (to < 0 || to >= S.doc.pages.length) return
          S.doc.pages.splice(to, 0, S.doc.pages.splice(S.page, 1)[0])
          S.page = to
        }
        render()
        save()
      })
    }
    // A new page with the default grid.
    const blankPage = name => ({ id: newId('p'), name, icon: S.doc.pages.length ? 'grid' : 'home', cols: 12, cols_narrow: 4, row: 'normal', gap: 12, compact: true,
      backdrop: 'plain', widgets: [] })
    // Add a page. More than one page is the Pro feature "Mehrere Widget-Seiten" – the server decides, here only the hint.
    async function addPage() {
      if (S.doc.pages.length >= 1 && !HO.can('advanced_dashboard')) {
        return HO.upsell({ title: 'Mehrere Widget-Seiten', message: 'Eine Widget-Seite gehört zu Basic. Weitere Seiten – etwa je Etage oder fürs Wand-Tablet – gibt es mit ComfyOS Pro.' })
      }
      if (S.doc.pages.length >= ((S.doc.limits || {}).pages || 12)) return HO.toast('Mehr Seiten sind nicht möglich.', 'warn')
      const name = await HO.prompt('Neue Seite', '', { label: 'Name', ok: 'Anlegen' })
      if (!name) return
      touch()
      S.doc.pages.push(blankPage(name.slice(0, 30)))
      showPage(S.doc.pages.length - 1)
      save()
    }
    // First page: empty, or filled with a suggestion that fits the home (widget_types.js: HO.widgets.starter).
    function start(withSuggestion) {
      S.doc.pages = [blankPage('Zuhause')]
      S.page = 0
      S.editing = true
      render()
      if (withSuggestion) {
        const p = page()
        ;(HO.widgets.starter ? HO.widgets.starter(S.ov) : []).forEach(item => {
          if (!registry[item[0]]) return
          const w = newWidget(item[0], null)
          // the suggestion is laid out on the wide grid; the phone arrangement follows it by itself
          w.narrow = null
          w.wide = firstFit(p.widgets.map(o => o.wide), Math.min(item[1] || (w.wide || { w: 3 }).w, p.cols), item[2] || (w.wide || { h: 2 }).h, p.cols)
          Object.assign(w.opts, item[3] || {})
          Object.assign(w, item[4] || {})
          p.widgets.push(w)
        })
        render()
      }
      save()
      HO.toast(withSuggestion ? 'Vorschlag angelegt – zieh die Widgets zurecht und tippe „Fertig“.' : 'Tippe aufs Raster oder auf „Widget“.')
    }
    // Switch the editor on or off.
    function setEditing(on) {
      S.editing = on
      S.sel = null
      S.live = null
      if (!on && saveTimer) { clearTimeout(saveTimer); saveNow() }
      render()
      if (on) HO.toast(window.matchMedia('(pointer: fine)').matches ? 'Widgets ziehen, an der Ecke die Größe ändern, aufs Raster klicken zum Hinzufügen'
        : 'Am Griff ziehen, an der Ecke die Größe ändern, aufs Raster tippen zum Hinzufügen')
    }

    /* ----- Events */
    // Buttons of the bar and of the empty view.
    function onBar(e) {
      const tab = e.target.closest('[data-wg-page]'), b = e.target.closest('[data-wg-bar]')
      if (tab) return showPage(+tab.dataset.wgPage)
      if (!b) return
      const act = b.dataset.wgBar
      if (act === 'edit') setEditing(true)
      else if (act === 'done') setEditing(false)
      else if (act === 'add') catalogue(null)
      else if (act === 'page') pageSheet()
      else if (act === 'undo') undo()
      else if (act === 'addPage') addPage()
      else if (act === 'starter') start(true)
      else if (act === 'blank') start(false)
    }
    // Clicks on the board. In the editor: buttons of a widget, a tap on the free grid adds a widget there; the content
    // of the widgets stays still. Otherwise: the few actions the board carries out itself (start a flow).
    function onClick(e) {
      if (e.target.closest('[data-wg-bar]')) return onBar(e)
      if (S.editing) {
        e.stopPropagation()
        const act = e.target.closest('[data-wg-do]'), over = e.target.closest('.wg-edit')
        if (act && over) {
          const id = over.dataset.wgEdit
          return act.dataset.wgDo === 'settings' ? widgetSheet(id) : act.dataset.wgDo === 'copy' ? copyWidget(id) : removeWidget(id)
        }
        if (over) return
        if (S.sel) return select(null)
        if (page() && (e.target === grid || e.target === host || e.target.closest('.wg-hint'))) catalogue(cellAt(e.clientX, e.clientY))
        return
      }
      const play = e.target.closest('[data-wg-play]')
      if (play) { e.stopPropagation(); return playFlow(play) }
    }
    // Double click on a widget in the editor opens its settings.
    const onDouble = e => { const over = S.editing && e.target.closest('.wg-edit'); if (over) widgetSheet(over.dataset.wgEdit) }
    // Start the play button of a flow (widget "Flow-Knopf").
    async function playFlow(button) {
      const parts = String(button.dataset.wgPlay).split('/')
      if (parts.length !== 2 || button.disabled) return
      if (button.dataset.confirm && !await HO.confirm(`„${button.dataset.name || 'Flow'}“ jetzt starten?`, { title: 'Flow starten', ok: 'Starten' })) return
      button.disabled = true
      button.classList.add('busy')
      try {
        const r = await HO.api(`flows/${encodeURIComponent(parts[0])}/play/${encodeURIComponent(parts[1])}`, { method: 'POST' })
        HO.toast((r.trace || []).length ? 'Ausgelöst' : 'Ausgelöst – keine Aktion lief (Bedingungen?)')
      } catch (err) { if (err.status !== 402) HO.toast(err.message, 'bad') }
      button.disabled = false
      button.classList.remove('busy')
    }
    let redrawPending = false
    // Draw all widgets in the next frame (an answer of a shared request arrived).
    function redrawSoon() {
      if (redrawPending || S.dead) return
      redrawPending = true
      requestAnimationFrame(() => { redrawPending = false; if (!S.dead && !busy()) drawAll() })
    }
    const drawAll = () => { const p = page(); if (p) p.widgets.forEach(drawWidget) }
    const busy = () => !!S.drag || HO.media.choosing(host)
    const onResize = HO.debounce(() => {
      if (S.dead || !page() || S.drag) return
      const was = S.mode
      measure()
      if (was !== S.mode) return render()
      layout()
    }, 120)
    const ro = window.ResizeObserver ? new ResizeObserver(onResize) : null
    if (ro) ro.observe(host)

    // Scrolling inside a widget (scroll events do not bubble, hence the capture): its cut edges change.
    const onScroll = e => { const el = e.target.closest && e.target.closest('[data-wg]'); if (el) markCuts(S.els[el.dataset.wg]) }
    host.addEventListener('scroll', onScroll, true)
    host.addEventListener('pointerdown', onDown)
    host.addEventListener('click', onClick, true)
    host.addEventListener('dblclick', onDouble)
    if (bar) bar.addEventListener('click', onBar)
    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    window.addEventListener('resize', onResize)
    document.addEventListener('keydown', onKey)
    host.classList.add('wg-fresh')              // the widgets fade in once when the view opens (not on every redraw)
    setTimeout(() => host.classList.remove('wg-fresh'), 900)
    render()

    return {
      get editing() { return S.editing },
      get busy() { return busy() },
      get pages() { return S.doc.pages },
      get mode() { return S.mode },
      // Fresh state of the home: every widget draws what changed.
      update(overview) {
        if (overview) S.ov = overview
        if (!busy()) drawAll()
      },
      edit(on) { setEditing(!!on) },
      // Tear down: timers, observers, listeners, widgets with a life of their own; a pending save still goes out.
      destroy() {
        S.dead = true
        if (saveTimer) { clearTimeout(saveTimer); saveNow() }
        if (S.drag) cancelAnimationFrame(S.drag.raf)
        if (ro) ro.disconnect()
        clearWidgets()
        host.removeEventListener('scroll', onScroll, true)
        host.removeEventListener('pointerdown', onDown)
        host.removeEventListener('click', onClick, true)
        host.removeEventListener('dblclick', onDouble)
        if (bar) bar.removeEventListener('click', onBar)
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        window.removeEventListener('resize', onResize)
        document.removeEventListener('keydown', onKey)
      }
    }
  }

  HO.widgets = {
    GROUPS, STYLES, TINTS, ICONS,
    // Register a widget type (or replace it); the order of the first registration is the order in the catalogue.
    register(type, def) {
      if (!type || (typeof def.render !== 'function' && typeof def.mount !== 'function')) throw new Error('Widget braucht einen Namen und render() oder mount()')
      if (!registry[type]) order.push(type)
      registry[type] = Object.assign({ type, title: type, icon: 'grid', group: 'overview', desc: '', size: [3, 2], min: [1, 1], fields: [] }, def)
      return registry[type]
    },
    get(type) { return registry[type] || null },
    types() { return order.slice() },
    load() { return HO.api('widgets') },
    mount, firstFit, settle, compactUp
  }
})(window.HO)
