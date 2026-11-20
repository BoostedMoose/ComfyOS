/* ComfyOS – light: lamp control (brightness, white tone, colour wheel, gradient, effects), scene chips, room light sheet,
   page "Szenen" (studio + library).
   Scenes are palettes: ComfyOS distributes the colours over the lamps of a room, of a group or over single lamps. */
(function (HO) {
  'use strict'
  const EFFECTS = { candle: 'Kerze', fire: 'Kaminfeuer', sparkle: 'Funkeln', glisten: 'Glitzern', opal: 'Opal', prism: 'Prisma',
    underwater: 'Unterwasser', cosmos: 'Kosmos', sunbeam: 'Sonnenstrahl', enchant: 'Zauber' }
  const GMODES = { interpolated_palette: 'Verlauf', interpolated_palette_mirrored: 'Gespiegelt', random_pixelated: 'Gemischt',
    segmented_palette: 'Segmente' }
  const WHITES = [[2200, 'Kerzenlicht'], [2700, 'Warmweiß'], [4000, 'Neutral'], [6500, 'Tageslicht']]
  const SPEEDS = [[60, 'Langsam'], [20, 'Mittel'], [8, 'Schnell']]
  const COLOR_KEYS = ['kelvin', 'mirek', 'color', 'xy', 'gradient', 'gradient_xy']

  const scenesState = () => (HO.state.overview && HO.state.overview.scenes) || { scenes: [], active: {} }
  const sceneById = id => scenesState().scenes.find(s => s.id === id)
  const activeFor = key => (scenesState().active || {})[key]
  const rgbCss = c => `rgb(${c[0]},${c[1]},${c[2]})`

  /* ---------- Colour wheel (angle = hue, distance = saturation) */
  function hsvToRgb(h, s, v) {
    const f = n => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)) }
    return [f(5), f(3), f(1)].map(x => Math.round(x * 255))
  }
  // Colour from red/green/blue (0–255) to hue (degrees), saturation and lightness (0–1 each).
  function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255
    const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b)
    let h = 0
    if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
    return [(h * 60 + 360) % 360, mx ? d / mx : 0, mx]
  }
  // dragging through window listeners instead of pointer capture (gets along with scrollable sheets on touch devices)
  function wheel(host, hex, onInput, onDone) {
    host.innerHTML = '<canvas></canvas><span class="lc-mark"></span>'
    const cv = host.querySelector('canvas'), mark = host.querySelector('.lc-mark')
    const size = host.clientWidth || 240, n = Math.round(size * Math.min(2, window.devicePixelRatio || 1))
    cv.width = cv.height = n
    const ctx = cv.getContext('2d'), img = ctx.createImageData(n, n), R = n / 2
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const dx = x - R, dy = y - R, r = Math.hypot(dx, dy) / R
        if (r > 1) continue
        const i = (y * n + x) * 4, c = hsvToRgb((Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, r, 1)
        img.data[i] = c[0]; img.data[i + 1] = c[1]; img.data[i + 2] = c[2]
        img.data[i + 3] = r > 0.985 ? Math.round((1 - r) / 0.015 * 255) : 255
      }
    }
    ctx.putImageData(img, 0, 0)
    // Put the marker on the colour wheel at the place of this colour.
    const place = h => {
      const [hh, ss] = rgbToHsv(...HO.hexToRgb(h)), a = hh * Math.PI / 180
      mark.style.left = (50 + Math.cos(a) * ss * 50) + '%'; mark.style.top = (50 + Math.sin(a) * ss * 50) + '%'
      mark.style.background = h
    }
    place(hex || '#ffffff')
    // Colour under the pointer on the colour wheel: the angle is the hue, the distance to the centre the saturation.
    const pick = e => {
      const b = cv.getBoundingClientRect(), dx = e.clientX - b.left - b.width / 2, dy = e.clientY - b.top - b.height / 2
      const out = HO.rgbToHex(...hsvToRgb((Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360, Math.min(1, Math.hypot(dx, dy) / (b.width / 2)), 1))
      place(out)
      return out
    }
    let last = null
    const move = e => { last = pick(e); onInput(last) }
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up)
      if (last) onDone(last)
    }
    cv.addEventListener('pointerdown', e => {
      e.preventDefault()
      last = pick(e); onInput(last)
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
    })
    return { set: place }
  }

  const kRange = d => {
    const ct = ((d.light || {}).caps || {}).ct || [153, 500]
    return [Math.ceil(1e6 / ct[1] / 50) * 50, Math.floor(1e6 / ct[0] / 50) * 50]
  }
  const ctTrack = (lo, hi) => `linear-gradient(90deg,${[0, 0.25, 0.5, 0.75, 1].map(f => rgbCss(HO.mirekToRgb(1e6 / (lo + (hi - lo) * f)))).join(',')})`
  const curHex = d => { const c = HO.lightColors(Object.assign({}, d, { on: true, online: true })); return c.length ? HO.rgbToHex(...c[0]) : '#ffffff' }

  /* ---------- Lamp control (in the device sheet) */
  function tabsOf(d) {
    const c = (d.light || {}).caps || {}, t = []
    if (c.ct || c.color) t.push(['white', 'Weiß'])
    if (c.color) t.push(['color', c.gradient ? 'Farbe & Verlauf' : 'Farbe'])
    if ((c.effects || []).length) t.push(['fx', 'Effekte'])
    return t
  }
  HO.lights = {}
  HO.lights.controllable = d => !!(d && d.kind === 'light' && d.light && HO.canControl(d))

  /* ---------- Device lock: greyed-out switches carry data-locked; tapping only shows the hint.
     Capture phase, so that no other click handler (home page, sheets) sends the command after all. */
  HO.LOCK_HINT = 'Gesperrt – im Gerätemenü entsperren'
  HO.lockedBtn = (d, cls) => `<button type="button" class="btn-icon sm is-locked ${cls || ''}" data-locked="${HO.esc(d.id)}" aria-disabled="true"
    aria-label="${HO.esc(d.name)}: gesperrt" title="${HO.LOCK_HINT}">${HO.icon('lock', 'sm')}</button>`
  document.addEventListener('click', e => {
    const t = e.target.closest && e.target.closest('[data-locked]')
    if (!t) return
    e.preventDefault(); e.stopPropagation()
    HO.toast(HO.LOCK_HINT, 'warn')
  }, true)
  HO.lights.controlHTML = d => {
    const L = d.light, c = L.caps, tabs = tabsOf(d)
    let tab = L.effect ? 'fx' : (L.mode === 'xy' || L.mode === 'gradient') ? 'color' : 'white'
    if (!tabs.some(t => t[0] === tab)) tab = tabs.length ? tabs[0][0] : ''
    const [klo, khi] = kRange(d), kNow = L.mirek ? Math.round(1e6 / L.mirek) : 2700
    const modes = (c.gradient_modes || []).filter(m => GMODES[m])
    return `<div class="lc" data-lc data-tab="${tab}">
      <div class="lc-top"><button type="button" class="lc-power" data-lc-power aria-label="An/Aus">${HO.icon('power')}</button>
        <div class="lc-bri"><input type="range" min="1" max="100" step="1" value="${d.brightness || 100}" data-lc-bri aria-label="Helligkeit">
          <span class="lc-val" data-lc-val></span></div></div>
      ${tabs.length > 1 ? `<div class="seg lc-tabs">${tabs.map(([k, l]) => `<button type="button" data-lc-tab="${k}">${l}</button>`).join('')}</div>` : ''}
      ${c.ct || c.color ? `<div class="lc-pane" data-pane="white">
        <input type="range" class="lc-ct" min="${klo}" max="${khi}" step="50" value="${Math.max(klo, Math.min(khi, kNow))}" data-lc-ct aria-label="Weißton" style="background:${ctTrack(klo, khi)}">
        <div class="row between tiny muted"><span>warm</span><span data-lc-kval></span><span>kalt</span></div>
        <div class="chips" style="margin-top:8px">${WHITES.filter(([k]) => k >= klo - 100 && k <= khi + 100).map(([k, l]) => `<button type="button" class="chip" data-lc-k="${k}">${l}</button>`).join('')}</div></div>` : ''}
      ${c.color ? `<div class="lc-pane" data-pane="color">
        ${c.gradient ? `<div class="lc-segs" data-lc-segs></div>` : ''}
        <div class="lc-wheel" data-lc-wheel></div>
        ${c.gradient && modes.length > 1 ? `<div class="seg lc-gmode">${modes.map(m => `<button type="button" data-lc-gmode="${m}">${GMODES[m]}</button>`).join('')}</div>` : ''}</div>` : ''}
      ${(c.effects || []).length ? `<div class="lc-pane" data-pane="fx"><div class="chips">
        ${c.effects.map(e => `<button type="button" class="chip" data-lc-fx="${HO.esc(e)}">${HO.icon('sparkle', 'sm')}${HO.esc(EFFECTS[e] || e)}</button>`).join('')}</div>
        <p class="tiny muted" style="margin:8px 0 0">Nochmal antippen schaltet den Effekt aus.</p></div>` : ''}
      ${scenesState().scenes.length ? `<div class="lc-scenes"><div class="eyebrow">Szene auf diese Lampe</div>${HO.lights.sceneRowHTML({ dev: d.id })}</div>` : ''}</div>`
  }

  HO.lights.bindControl = (root, d, opts = {}) => {
    const box = root.querySelector('[data-lc]')
    if (!box) return
    const L = d.light, c = L.caps, q = s => box.querySelector(s)
    let pending = null, timer = null, lastSent = 0, wh = null, seg = 'all'
    let pts = []
    // Prepare the colour points of a light strip with a gradient – from its state, otherwise filled with the current colour.
    const initPts = () => {
      const n = c.gradient || 0
      pts = L.mode === 'gradient' && L.points.length > 1 ? L.points.map(p => HO.rgbToHex(...HO.xyToRgb(p[0], p[1]))) : []
      const fill = pts[pts.length - 1] || curHex(d)
      while (pts.length < n) pts.push(fill)
      pts = pts.slice(0, n)
    }
    initPts()
    // Send the light state wanted last (while dragging a slider intermediate values go out bundled).
    const flush = async () => {
      timer = null
      const st = pending; pending = null
      if (!st) return
      lastSent = Date.now()
      try { await HO.api(`devices/${encodeURIComponent(d.id)}/command`, { method: 'POST', body: { state: st } }) } catch (e) { HO.toast(e.message, 'bad') }
    }
    // at most ~4 commands per second while dragging (Hue: max. 10/s for the whole bridge)
    const send = (st, now) => {
      if (pending && COLOR_KEYS.some(k => k in st)) COLOR_KEYS.forEach(k => delete pending[k])
      pending = Object.assign(pending || {}, st)
      clearTimeout(timer)
      timer = setTimeout(flush, now ? 0 : Math.max(0, 260 - (Date.now() - lastSent)))
    }
    // Draw the light control from the state of the lamp: tabs, on/off, brightness, colour or white tone, segments.
    const paint = () => {
      const tab = box.dataset.tab
      box.querySelectorAll('[data-pane]').forEach(p => { p.hidden = p.dataset.pane !== tab })
      box.querySelectorAll('[data-lc-tab]').forEach(b => b.classList.toggle('active', b.dataset.lcTab === tab))
      q('[data-lc-power]').classList.toggle('on', !!d.on)
      q('[data-lc-power]').setAttribute('aria-pressed', !!d.on)
      q('[data-lc-val]').textContent = d.on ? (d.brightness || 100) + ' %' : 'Aus'
      const col = HO.lightColors(Object.assign({}, d, { on: true, online: true }))
      box.style.setProperty('--lc-color', col.length ? rgbCss(col[0]) : 'var(--text)')
      q('[data-lc-bri]').style.background = `linear-gradient(90deg, var(--card-3), ${col.length > 1 ? col.map(rgbCss).join(',') : col.length ? rgbCss(col[0]) : 'var(--text)'})`
      box.classList.toggle('is-off', !d.on)
      const kv = q('[data-lc-kval]'); if (kv) kv.textContent = L.mode === 'ct' && L.mirek ? Math.round(1e6 / L.mirek / 50) * 50 + ' K' : ''
      box.querySelectorAll('[data-lc-fx]').forEach(b => b.classList.toggle('active', b.dataset.lcFx === L.effect))
      box.querySelectorAll('[data-lc-gmode]').forEach(b => b.classList.toggle('active', b.dataset.lcGmode === (L.gmode || 'interpolated_palette')))
      const segs = q('[data-lc-segs]')
      if (segs) {
        segs.innerHTML = pts.map((h, i) => `<button type="button" class="lc-seg ${seg === i ? 'sel' : ''}" data-lc-seg="${i}" style="background:${h}" aria-label="Segment ${i + 1}"></button>`).join('') +
          `<button type="button" class="chip ${seg === 'all' ? 'active' : ''}" data-lc-seg="all">Alle</button>`
      }
      if (opts.onChange) opts.onChange(d)
    }
    // Change the tab of the light control; the colour wheel is only created when "Farbe" is opened for the first time.
    const showTab = t => {
      box.dataset.tab = t
      paint()
      if (t === 'color' && !wh) {
        wh = wheel(q('[data-lc-wheel]'), seg === 'all' ? curHex(d) : pts[seg], hex => pickColor(hex, false), hex => pickColor(hex, true))
      }
    }
    // Take over the chosen colour – for a light strip for all segments or only the chosen one – and send it.
    const pickColor = (hex, final) => {
      d.on = true
      if (c.gradient) {
        if (seg === 'all') pts = pts.map(() => hex); else pts[seg] = hex
        L.points = pts.map(HO.hexToXy); L.mode = 'gradient'; L.mirek = null
        send({ on: true, gradient: pts.slice() }, final)
      } else {
        L.xy = HO.hexToXy(hex); L.mode = 'xy'; L.mirek = null; L.points = []
        send({ on: true, color: hex }, final)
      }
      paint()
    }
    // Set the white tone (kelvin) and send it.
    const setKelvin = (k, final) => {
      d.on = true; L.mirek = Math.round(1e6 / k); L.mode = 'ct'; L.points = []
      if (c.gradient) initPts()
      send({ on: true, kelvin: k }, final)
      paint()
    }
    box.addEventListener('click', e => {
      const t = e.target.closest('[data-lc-power],[data-lc-tab],[data-lc-k],[data-lc-fx],[data-lc-seg],[data-lc-gmode]')
      if (!t) return
      if (t.dataset.lcTab) return showTab(t.dataset.lcTab)
      if (t.hasAttribute('data-lc-power')) { d.on = !d.on; send({ on: d.on }, true); return paint() }
      if (t.dataset.lcK) { const k = +t.dataset.lcK, ct = q('[data-lc-ct]'); if (ct) ct.value = k; return setKelvin(k, true) }
      if (t.dataset.lcFx) {
        const e2 = L.effect === t.dataset.lcFx ? 'no_effect' : t.dataset.lcFx
        L.effect = e2 === 'no_effect' ? null : e2; d.on = true
        send({ on: true, effect: e2 }, true); return paint()
      }
      if (t.dataset.lcSeg) { seg = t.dataset.lcSeg === 'all' ? 'all' : +t.dataset.lcSeg; if (wh) wh.set(seg === 'all' ? pts[0] : pts[seg]); return paint() }
      if (t.dataset.lcGmode) { L.gmode = t.dataset.lcGmode; send({ gradient: pts.slice(), gradient_mode: L.gmode }, true); return paint() }
    })
    box.addEventListener('input', e => {
      if (e.target.matches('[data-lc-bri]')) { d.on = true; d.brightness = +e.target.value; send({ brightness: d.brightness }); paint() }
      else if (e.target.matches('[data-lc-ct]')) setKelvin(+e.target.value, false)
    })
    box.addEventListener('change', e => {
      if (e.target.matches('[data-lc-bri]')) send({ brightness: +e.target.value }, true)
      else if (e.target.matches('[data-lc-ct]')) setKelvin(+e.target.value, true)
    })
    showTab(box.dataset.tab)
  }

  // Own sheet for a lamp
  HO.lights.sheet = (d, opts = {}) => {
    const ctl = HO.lights.controllable(d)
    const s = HO.sheet({ title: d.name, body: `<div class="small muted" style="margin:-6px 0 14px">${HO.esc(d.room || '')} · ${HO.esc(HO.devState(d))}</div>
      ${d.locked ? `<div class="notice">${HO.icon('lock', 'sm')}<div>Gesperrt – ComfyOS schaltet diese Lampe nicht. Entsperren im Gerätemenü auf der Startseite.</div></div>`
        : ctl ? HO.lights.controlHTML(d) : d.online && d.control_item
        ? `<button class="btn ${d.on ? '' : 'primary'}" style="width:100%" data-lt-sheet-toggle>${HO.icon('power', 'sm')} ${d.on ? 'Ausschalten' : 'Einschalten'}</button>
           <p class="tiny muted">Helligkeit und Farbe gehen nur bei Lampen an der Hue Bridge.</p>`
        : `<div class="notice warn">${HO.icon('warn', 'sm')}<div>${HO.esc(d.status_detail || 'Lampe nicht erreichbar.')}</div></div>`}` })
    if (ctl) HO.lights.bindControl(s.el, d, opts)
    s.el.addEventListener('click', async e => {
      if (!e.target.closest('[data-lt-sheet-toggle]')) return
      s.close()
      try { await HO.api(`devices/${encodeURIComponent(d.id)}/command`, { method: 'POST', body: { state: { on: !d.on } } }); d.on = !d.on; if (opts.onChange) opts.onChange(d) }
      catch (err) { HO.toast(err.message, 'bad') }
    })
    return s
  }

  const dotStyle = d => { const c = HO.lightColors(d); return c.length ? `background:${c.length > 1 ? `linear-gradient(135deg,${c.map(rgbCss).join(',')})` : rgbCss(c[0])}` : '' }
  const flagsHTML = d => HO.devIssues(d).filter(i => i.k !== 'offline').map(i => `<span class="t-flag ${i.k}" title="${HO.esc(i.t)}">${HO.icon(i.k === 'battery' ? 'battery' : 'warn', 'sm')}</span>`).join('')
  // Lamp row in the room cards of the home page: tapping opens the lamp, the button switches
  HO.lights.lampRowHTML = d => `<div class="lt-lamp ${d.on && d.online ? 'on' : ''} ${d.online ? '' : 'off'}">
      <button type="button" class="lt-lamp-main" data-lt-open="${HO.esc(d.id)}"><span class="lt-dot" style="${dotStyle(d)}"></span>
        <span class="lt-name">${HO.esc(d.name)}</span><span class="tiny muted">${HO.esc(HO.devState(d))}</span></button>${flagsHTML(d)}
      ${d.online && d.control_item ? (d.locked ? HO.lockedBtn(d) : `<button type="button" class="btn-icon sm ${d.on ? 'on' : ''} ${d._busy ? 'busy' : ''}" data-lt-toggle="${HO.esc(d.id)}" aria-pressed="${!!d.on}" aria-label="${d.on ? 'Ausschalten' : 'Einschalten'}">${HO.icon('power', 'sm')}</button>`) : ''}</div>`
  const avgBri = ls => { const on = ls.filter(d => d.light && d.online && d.on); return on.length ? Math.round(on.reduce((a, d) => a + (d.brightness || 100), 0) / on.length) : 100 }
  HO.lights.avgBrightness = avgBri

  /* ---------- Light settings of a room (tapping the room head on the home page).
     ops (from the home page): room(on), bri(v), toggle(d, on), open(d) – with their optimistic display. */
  HO.lights.onData = null   // the home page reports new device states
  HO.lights.roomSheet = (roomId, ops = {}) => {
    const ov = () => HO.state.overview
    const lamps = () => ov().devices.filter(d => d.room_id === roomId && d.kind === 'light').sort((a, b) => HO.byName(a.name, b.name))
    const info = (ov().rooms || []).find(r => r.room_id === roomId)
    const name = (info && info.name) || (lamps()[0] && lamps()[0].room) || 'Raum'
    let sliding = false
    // effect of the room (independent of the scene): local draft, started from the stored state
    const saved = () => (ov().effects || {})[roomId] || null
    // Effect setting of this room: the stored one, otherwise the default (no effect, whole room, own colours).
    const fx = (() => {
      const e = saved()
      return e ? { effect: e.effect, target: Array.isArray(e.target) ? 'devices' : 'room', devices: Array.isArray(e.target) ? e.target.slice() : [],
        color_source: e.color_source, color: e.color || '#ff8a2a', kelvin: e.kelvin, speed: e.speed == null ? 50 : e.speed }
        : { effect: null, target: 'room', devices: [], color_source: 'own', color: '#ff8a2a', kelvin: null, speed: 50 }
    })()
    const fxLamps = () => lamps().filter(d => d.light && d.online && !d.locked && ((d.light.caps || {}).effects || []).length)
    // Section "Effekt" in the room dialog: choice of the effect, lamps, colour source and speed – only what the lamps can do.
    const fxHTML = () => {
      const ls = fxLamps()
      if (!ls.length) return ''
      const all = [...new Set(ls.reduce((a, d) => a.concat(d.light.caps.effects), []))]
      const cur = saved(), able = fx.effect ? ls.filter(d => d.light.caps.effects.includes(fx.effect)) : ls
      return `<div class="eyebrow" style="margin-top:16px">Effekt</div>
        <div class="chips scroll" style="margin-top:8px"><button type="button" class="chip ${cur ? '' : 'active'}" data-fx="">Aus</button>
          ${all.map(e => `<button type="button" class="chip ${cur && cur.effect === e ? 'active' : ''}" data-fx="${HO.esc(e)}">${HO.icon('sparkle', 'sm')}${HO.esc(EFFECTS[e] || e)}</button>`).join('')}</div>
        ${fx.effect ? `<div class="rs-fx">
          <div class="seg sm"><button type="button" data-fx-target="room" class="${fx.target === 'room' ? 'active' : ''}">Ganzer Raum</button>
            <button type="button" data-fx-target="devices" class="${fx.target === 'devices' ? 'active' : ''}">Einzelne Lampen</button></div>
          ${fx.target === 'devices' ? `<div class="chips" style="margin-top:8px">${able.map(d => `<button type="button" class="chip ${fx.devices.includes(d.id) ? 'active' : ''}" data-fx-dev="${HO.esc(d.id)}">${HO.esc(d.name)}</button>`).join('')}</div>` : ''}
          <div class="seg sm" style="margin-top:10px">${[['own', 'Eigene Farben'], ['scene', 'Szenenfarbe'], ['custom', 'Eigene Farbe wählen']]
            .map(([k, l]) => `<button type="button" data-fx-src="${k}" class="${fx.color_source === k ? 'active' : ''}">${l}</button>`).join('')}</div>
          ${fx.color_source === 'custom' ? '<div class="lc-wheel rs-fx-wheel" data-fx-wheel></div>' : ''}
          <label class="field" style="margin-top:10px"><span>Tempo</span><input type="range" min="0" max="100" value="${fx.speed}" data-fx-speed aria-label="Tempo des Effekts"></label>
          <p class="tiny muted" style="margin:6px 0 0">Ein Szenenwechsel lässt den Effekt laufen. Kann eine Lampe keine Effektfarbe oder kein Tempo, nutzt sie ihre eigenen.</p></div>` : ''}`
    }
    // Section "Aktuellen Zustand speichern": as a room variant of the active scene or as a new scene.
    const saveHTML = sc => {
      const hasVar = sc && sc.variants && sc.variants[roomId]
      return `<div class="eyebrow" style="margin-top:16px">Aktuellen Zustand speichern</div>
        <div class="row wrap g8" style="margin-top:8px">
          ${sc ? `<button type="button" class="btn sm" data-rs-save="variant">${HO.icon('check', 'sm')} Für diesen Raum speichern</button>` : ''}
          <button type="button" class="btn sm" data-rs-save="new">${HO.icon('plus', 'sm')} Als neue Szene</button>
          ${hasVar ? `<button type="button" class="btn sm ghost" data-rs-save="reset">${HO.icon('undo', 'sm')} Raumvariante zurücksetzen</button>` : ''}</div>
        ${sc ? `<p class="tiny muted" style="margin:6px 0 0">„Für diesen Raum speichern“: „${HO.esc(sc.name)}“ startet hier künftig genau so${hasVar ? ' (gespeichert)' : ''}. Andere Räume bleiben unberührt.</p>` : ''}`
    }
    // Content of the room dialog "Licht": head with switch, brightness, scenes, effect, saving and the lamps.
    const body = () => {
      const ls = lamps(), on = ls.filter(d => d.on && d.online), sw = ls.filter(HO.canControl)
      const sc = HO.lights.activeScene('room:' + roomId), glow = HO.lightGlow(ls, 0.75)
      return `<div class="rs-hero ${glow || HO.lights.sceneImg(sc) ? 'lit' : ''}">${glow ? `<div class="rc-glow" style="background:${glow}"></div>` : ''}${HO.lights.imgTag(sc, 'rc-img')}
          <div class="rc-title"><b>${on.length ? `${on.length} von ${ls.length} an` : 'Alles aus'}</b>
            <div class="rc-sub">${sc ? `${HO.icon('palette', 'sm')}${HO.esc(sc.name)}` : 'Keine Szene aktiv'}</div></div>
          ${sw.length ? `<button class="room-switch ${on.length ? 'on' : ''}" role="switch" aria-checked="${!!on.length}" data-rs-all="${on.length ? 0 : 1}" aria-label="Licht ${HO.esc(name)}"></button>` : ''}</div>
        ${ls.some(d => d.light && d.online && !d.locked) ? `<input type="range" class="lt-bri" min="1" max="100" value="${avgBri(ls)}" data-rs-bri aria-label="Helligkeit ${HO.esc(name)}">` : ''}
        ${scenesState().scenes.length ? `<div class="eyebrow" style="margin-top:16px">Szenen</div>${HO.lights.sceneRowHTML({ room: roomId }) || '<p class="tiny muted" style="margin:6px 0 0">Diesem Raum ist noch keine Szene zugeordnet – auf der Seite „Szenen“ zuordnen oder unten den aktuellen Zustand als neue Szene speichern.</p>'}` : ''}
        ${sw.length ? saveHTML(sc) : ''}
        ${fxHTML()}
        <div class="eyebrow" style="margin-top:16px">Lampen</div>
        <div class="rs-lamps">${ls.map(d => `<div class="rs-lamp ${d.online ? '' : 'off'}">
          <div class="row"><button type="button" class="lt-lamp-main" data-rs-open="${HO.esc(d.id)}"><span class="lt-dot" style="${dotStyle(d)}"></span>
            <span class="lt-name">${HO.esc(d.name)}</span><span class="tiny muted">${HO.esc(HO.devState(d))}</span></button>
            ${d.online && d.control_item ? (d.locked ? HO.lockedBtn(d) : `<button type="button" class="btn-icon sm ${d.on ? 'on' : ''}" data-rs-toggle="${HO.esc(d.id)}" aria-pressed="${!!d.on}" aria-label="${d.on ? 'Ausschalten' : 'Einschalten'}">${HO.icon('power', 'sm')}</button>`) : ''}</div>
          ${HO.lights.controllable(d) && d.on ? `<input type="range" class="lt-bri sm" min="1" max="100" value="${d.brightness || 100}" data-rs-lbri="${HO.esc(d.id)}" aria-label="Helligkeit ${HO.esc(d.name)}">` : ''}</div>`).join('')}</div>`
    }
    const s = HO.sheet({ title: `Licht · ${name}`, body: body() })
    // mount the colour wheel for "Eigene Farbe wählen" again after every redraw
    const mountWheel = () => {
      const host = s.el.querySelector('[data-fx-wheel]')
      if (host) wheel(host, fx.color, () => {}, hex => { fx.color = hex; fx.kelvin = null; sendFx() })
    }
    const redraw = () => { if (!sliding) { s.body().innerHTML = body(); mountWheel() } }
    mountWheel()
    const close = s.close
    s.close = () => { if (HO.lights.onData === redraw) HO.lights.onData = null; close() }
    HO.lights.onData = redraw
    const devOf = id => ov().devices.find(x => x.id === id)
    const post = async (path, body) => { try { return await HO.api(path, { method: 'POST', body }) } catch (e) { HO.toast(e.message, 'bad') } }
    // Start the chosen effect for the room and remember it.
    const sendFx = async () => {
      if (!fx.effect) return
      if (fx.target === 'devices' && !fx.devices.length) return HO.toast('Bitte mindestens eine Lampe wählen.', 'warn')
      const req = { effect: fx.effect, target: fx.target, devices: fx.target === 'devices' ? fx.devices : [], color_source: fx.color_source, speed: fx.speed }
      if (fx.color_source === 'custom') { if (fx.kelvin) req.kelvin = fx.kelvin; else req.color = fx.color }
      try {
        const r = await HO.api(`effects/${encodeURIComponent(roomId)}`, { method: 'PUT', body: req })
        const o = ov(); if (o) { o.effects = o.effects || {}; o.effects[roomId] = r.effect }
        if (r.failed && r.failed.length) HO.toast(`Teilweise: ${r.failed[0]}`, 'warn', 6000)
        else if (!r.switched) HO.toast('Keine Lampe hier kann diesen Effekt.', 'warn')
        else HO.toast(`Effekt „${EFFECTS[fx.effect] || fx.effect}“${r.unable ? ` · ${r.unable} Lampe${r.unable === 1 ? '' : 'n'} ohne diesen Effekt` : ''}`)
      } catch (e) { HO.toast(e.message, 'bad') }
      redraw()
    }
    const sendFxSoon = HO.debounce(sendFx, 500)
    // Save the current light of the room: as a room variant, as a new scene – or reset a room variant.
    const saveState = async kind => {
      const sc = HO.lights.activeScene('room:' + roomId)
      try {
        if (kind === 'variant' && sc) {
          await HO.api(`scenes/${encodeURIComponent(sc.id)}/variants/${encodeURIComponent(roomId)}`, { method: 'POST' })
          HO.toast(`Gespeichert – „${sc.name}“ startet in ${name} künftig so`)
        } else if (kind === 'reset' && sc) {
          await HO.api(`scenes/${encodeURIComponent(sc.id)}/variants/${encodeURIComponent(roomId)}`, { method: 'DELETE' })
          HO.toast('Raumvariante zurückgesetzt')
        } else if (kind === 'new') {
          const nm = await HO.prompt('Als neue Szene speichern', '', { label: 'Name der Szene', ok: 'Anlegen' })
          if (!nm) return
          const r = await HO.api(`scenes/from-room/${encodeURIComponent(roomId)}`, { method: 'POST', body: { name: nm } })
          HO.toast(`Szene „${r.name}“ angelegt – in ${name} angeboten`)
        }
      } catch (e) { HO.toast(e.message, 'bad') }
    }
    s.el.addEventListener('pointerdown', e => { if (e.target.matches('input[type=range]') || e.target.closest('[data-fx-wheel]')) sliding = true })
    const release = () => { sliding = false }
    s.el.addEventListener('pointerup', release); s.el.addEventListener('pointercancel', release)
    s.el.addEventListener('input', e => { if (e.target.matches('[data-fx-speed]')) fx.speed = +e.target.value })
    s.el.addEventListener('click', e => {
      const f = e.target.closest('[data-fx],[data-fx-target],[data-fx-dev],[data-fx-src],[data-rs-save]')
      if (f) {
        if (f.dataset.rsSave) return saveState(f.dataset.rsSave)
        if (f.dataset.fx != null) {
          if (!f.dataset.fx) {
            fx.effect = null
            return HO.api(`effects/${encodeURIComponent(roomId)}`, { method: 'DELETE' })
              .then(() => { const o = ov(); if (o && o.effects) delete o.effects[roomId]; HO.toast('Effekt aus'); redraw() })
              .catch(err => HO.toast(err.message, 'bad'))
          }
          fx.effect = f.dataset.fx
        } else if (f.dataset.fxTarget) {
          fx.target = f.dataset.fxTarget
          if (fx.target === 'devices' && !fx.devices.length) { redraw(); return }
        } else if (f.dataset.fxDev) {
          const id = f.dataset.fxDev
          fx.devices = fx.devices.includes(id) ? fx.devices.filter(x => x !== id) : fx.devices.concat(id)
        } else if (f.dataset.fxSrc) {
          fx.color_source = f.dataset.fxSrc
          if (fx.color_source === 'custom') { redraw(); return sendFx() }
        }
        return sendFx()
      }
      const t = e.target.closest('[data-rs-all],[data-rs-toggle],[data-rs-open]')
      if (!t) return
      if (t.dataset.rsAll) {
        const on = t.dataset.rsAll === '1'
        return ops.room ? ops.room(on) : post('lights', { on, room_ids: [roomId] }).then(redraw)
      }
      const d = devOf(t.dataset.rsToggle || t.dataset.rsOpen)
      if (!d) return
      if (t.dataset.rsOpen) return ops.open ? ops.open(d) : HO.lights.sheet(d, { onChange: redraw })
      if (ops.toggle) return ops.toggle(d, !d.on)
      d.on = !d.on; redraw(); post(`devices/${encodeURIComponent(d.id)}/command`, { state: { on: d.on } })
    })
    s.el.addEventListener('change', e => {
      sliding = false
      const v = +e.target.value
      if (e.target.matches('[data-fx-speed]')) { fx.speed = v; return sendFxSoon() }
      if (e.target.matches('[data-rs-bri]')) {
        if (ops.bri) return ops.bri(v)
        lamps().filter(d => d.light && d.online).forEach(d => { d.on = true; d.brightness = v })
        return post('lights', { on: true, room_ids: [roomId], brightness: v }).then(redraw)
      }
      const id = e.target.dataset.rsLbri, d = id && devOf(id)
      if (!d) return
      d.on = true; d.brightness = v
      post(`devices/${encodeURIComponent(id)}/command`, { state: { brightness: v } })
      if (HO.lights.onScenes) HO.lights.onScenes()
      redraw()
    })
    return s
  }

  /* ---------- Scene chips (room, group or lamp); tapping the active scene ends it */
  HO.lights.sceneRowHTML = t => {
    const st = scenesState(), key = t.room ? 'room:' + t.room : t.group ? 'group:' + t.group : null
    const act = key && st.active[key] ? st.active[key].scene : null
    // in the room only assigned scenes (since 0.15: empty = assigned to no room); the currently active one stays visible
    const list = st.scenes.filter(s => !t.room || s.rooms.includes(t.room) || s.id === act)
    if (!list.length) return ''
    const attr = t.room ? `data-sc-room="${HO.esc(t.room)}"` : t.group ? `data-sc-group="${HO.esc(t.group)}"` : `data-sc-dev="${HO.esc(t.dev)}"`
    return `<div class="chips scroll sc-row" data-sc-row>${list.map(s => `<button type="button" class="chip sc-chip ${s.id === act ? 'active' : ''}" data-sc-apply="${HO.esc(s.id)}" ${attr}
      title="${s.id === act ? 'Antippen beendet die Szene' : 'Szene aktivieren'}"><span class="sc-dot" style="background:${HO.paletteGradient(s.palette)}"></span>${HO.esc(s.name)}${s.dynamic ? HO.icon('sparkle', 'sm') : ''}</button>`).join('')}</div>`
  }
  HO.lights.activeScene = key => { const a = activeFor(key); return a ? sceneById(a.scene) : null }
  HO.lights.onScenes = null   // aktuelle Seite zeichnet neu

  // chip rows scroll horizontally and are redrawn often – otherwise the active scene would be outside the picture
  const reveal = row => {
    const a = row.querySelector('.sc-chip.active')
    if (!a) return
    const rb = row.getBoundingClientRect(), ab = a.getBoundingClientRect()
    if (ab.left < rb.left || ab.right > rb.right) row.scrollLeft += ab.left - rb.left - 8
  }
  new MutationObserver(ms => {
    for (const m of ms) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue
      if (n.matches('[data-sc-row]')) reveal(n)
      else n.querySelectorAll('[data-sc-row]').forEach(reveal)
    }
  }).observe(document.documentElement, { childList: true, subtree: true })

  /* ---------- Scene images of the Hue Bridge: only if the bridge delivers them. Remember failures,
     otherwise every redraw asks again. The fallback is the colour gradient of the scene (data-pal) or the room light. */
  const noImg = new Set()
  HO.lights.sceneImg = sc => sc && sc.image_rid && !noImg.has(sc.image_rid) ? '/api/scenes/image/hue/' + encodeURIComponent(sc.image_rid) : null
  HO.lights.imgTag = (sc, cls, pal) => {
    const url = HO.lights.sceneImg(sc)
    if (!url) return pal ? `<span class="${cls}" style="background:${pal}"></span>` : ''
    return `<img class="${cls}" src="${url}" alt="" loading="lazy" data-img-rid="${HO.esc(sc.image_rid)}" ${pal ? `data-pal="${HO.esc(pal)}"` : ''}>`
  }
  // capture, because 'error' does not bubble
  document.addEventListener('error', e => {
    const t = e.target
    if (!(t instanceof HTMLImageElement) || !t.dataset.imgRid) return
    noImg.add(t.dataset.imgRid)
    if (!t.dataset.pal) return t.remove()
    const s = document.createElement('span')
    s.className = t.className
    s.style.background = t.dataset.pal
    t.replaceWith(s)
  }, true)

  // The mouse wheel scrolls horizontal rows (chips, hourly forecast); on touch devices swiping works natively
  document.addEventListener('wheel', e => {
    const row = e.target.closest('.chips.scroll, .wx-hours')
    if (!row || Math.abs(e.deltaX) > 2 || row.scrollWidth <= row.clientWidth) return
    const atEnd = e.deltaY > 0 && row.scrollLeft >= row.scrollWidth - row.clientWidth - 1
    const atStart = e.deltaY < 0 && row.scrollLeft <= 0
    if (atEnd || atStart) return
    e.preventDefault()
    row.scrollLeft += e.deltaY
  }, { passive: false })

  /* Mouse (since 0.19.2): horizontal rows (scenes, routines, filters, hourly forecast) can also be dragged with the
     mouse button held – on touch one swipes natively. Since 0.24.3 the same holds for the content of a widget, which
     scrolls up and down (DRAG_COLS). From 5 px it counts as dragging – the first movement decides the direction; the click
     after it is swallowed, so that nothing switches unintentionally. Left out: the bar of unplaced devices in the floor
     plan editor (devices are dragged there), the floor plan itself, form fields, selectable text and the widget editor. */
  const DRAG_ROWS = '.chips.scroll, .wx-hours', DRAG_COLS = '.wg-body'
  const DRAG_NEVER = 'input, select, textarea, .fp-wrap, .fp-tray, .wg-note, .wg-board.editing'
  let rowDrag = null
  document.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'mouse' || e.button !== 0 || !e.target.closest || e.target.closest(DRAG_NEVER)) return
    let row = e.target.closest(DRAG_ROWS), col = e.target.closest(DRAG_COLS)
    if (row && row.scrollWidth <= row.clientWidth + 1) row = null
    if (col && col.scrollHeight <= col.clientHeight + 1) col = null
    if (!row && !col) return
    rowDrag = { row, col, el: null, x: e.clientX, y: e.clientY, left: row ? row.scrollLeft : 0, top: col ? col.scrollTop : 0, moved: false }
  })
  document.addEventListener('pointermove', e => {
    if (!rowDrag) return
    const d = rowDrag, dx = e.clientX - d.x, dy = e.clientY - d.y
    if (!d.moved) {
      if (Math.abs(dx) < 5 && Math.abs(dy) < 5) return
      // the first movement decides: sideways moves the row, up or down the column – whichever is there
      d.el = d.row && (Math.abs(dx) >= Math.abs(dy) || !d.col) ? d.row : d.col || d.row
      d.moved = true
      d.el.classList.add('row-dragging')
    }
    if (d.el === d.row) d.row.scrollLeft = d.left - dx
    else d.col.scrollTop = d.top - dy
    e.preventDefault()
  })
  // End dragging a scrolling row or column with the mouse; the following click then does not count.
  const endRowDrag = () => {
    if (!rowDrag) return
    const d = rowDrag
    rowDrag = null
    if (d.el) d.el.classList.remove('row-dragging')
    if (!d.moved) return
    const swallow = ev => { ev.stopPropagation(); ev.preventDefault() }
    window.addEventListener('click', swallow, true)
    setTimeout(() => window.removeEventListener('click', swallow, true), 0)
  }
  window.addEventListener('pointerup', endRowDrag)
  window.addEventListener('pointercancel', endRowDrag)

  document.addEventListener('click', async e => {
    const t = e.target.closest('[data-sc-apply]')
    if (!t) return
    e.preventDefault()
    const st = scenesState(), sid = t.dataset.scApply, room = t.dataset.scRoom, group = t.dataset.scGroup, dev = t.dataset.scDev
    const key = room ? 'room:' + room : group ? 'group:' + group : null
    const stop = key && t.classList.contains('active')
    const row = t.closest('[data-sc-row]')
    if (row) row.querySelectorAll('[data-sc-apply]').forEach(b => b.classList.toggle('active', !stop && b === t && !!key))
    if (key) { if (stop) delete st.active[key]; else st.active[key] = { scene: sid, since: Date.now() / 1000 } }
    if (HO.lights.onScenes) HO.lights.onScenes()
    const sc = sceneById(sid)
    try {
      const r = stop ? await HO.api('scenes/stop', { method: 'POST', body: room ? { room_ids: [room] } : { group_id: group } })
        : await HO.api(`scenes/${encodeURIComponent(sid)}/apply`, { method: 'POST', body: room ? { room_ids: [room] } : group ? { group_id: group } : { device_ids: [dev] } })
      if (r.failed && r.failed.length) HO.toast(`Teilweise: ${r.failed[0]}`, 'warn', 6000)
      else HO.toast(stop ? `Szene beendet${r.restored ? ' – Licht wie vorher' : ''}` : `Szene „${sc ? sc.name : ''}“`)
    } catch (err) { HO.toast(err.message, 'bad') }
  })

  /* ---------- Scene editor */
  // Colours from a photo (only in the browser – the image does not leave the device and is not stored)
  async function photoPalette(file, k = 5) {
    let src
    try { src = await createImageBitmap(file) } catch (e) {
      src = await new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = URL.createObjectURL(file) })
    }
    const w = src.width || src.naturalWidth, h = src.height || src.naturalHeight, f = 96 / Math.max(w, h)
    const cv = document.createElement('canvas'); cv.width = Math.max(1, Math.round(w * f)); cv.height = Math.max(1, Math.round(h * f))
    const ctx = cv.getContext('2d'); ctx.drawImage(src, 0, 0, cv.width, cv.height)
    const data = ctx.getImageData(0, 0, cv.width, cv.height).data, px = []
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 127 && Math.max(data[i], data[i + 1], data[i + 2]) > 30) px.push([data[i], data[i + 1], data[i + 2]])
    if (!px.length) return []
    const d2 = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    const cs = [px[px.length >> 1].slice()]
    while (cs.length < k) {
      let best = null, bd = -1
      for (const p of px) { const dd = Math.min(...cs.map(c => d2(p, c))); if (dd > bd) { bd = dd; best = p } }
      if (bd < 400) break
      cs.push(best.slice())
    }
    let count = []
    for (let it = 0; it < 8; it++) {
      const sum = cs.map(() => [0, 0, 0]); count = cs.map(() => 0)
      for (const p of px) {
        let bi = 0, bd = Infinity
        cs.forEach((c, i) => { const dd = d2(p, c); if (dd < bd) { bd = dd; bi = i } })
        sum[bi][0] += p[0]; sum[bi][1] += p[1]; sum[bi][2] += p[2]; count[bi]++
      }
      cs.forEach((c, i) => { if (count[i]) for (let j = 0; j < 3; j++) c[j] = sum[i][j] / count[i] })
    }
    return cs.map((c, i) => [c, count[i]]).filter(x => x[1]).sort((a, b) => b[1] - a[1]).map(([c]) => {
      const [h, s] = rgbToHsv(...c)
      return HO.rgbToHex(...hsvToRgb(h, Math.min(1, s * 1.25), 1))
    })
  }

  function roomList() {
    const ov = HO.state.overview || {}
    return (ov.rooms || []).filter(r => r.room_id).sort((a, b) => HO.byName(a.name, b.name))
  }

  /* Scene studio: card at the top of the page "Szenen" for creating and editing. Since 0.19.2 only visible while
     something is being edited ("Neu" in the head or tapping a scene); saving, deleting and cancelling close it.
     Only rebuilt when the scene changes – redrawing the library leaves the inputs in place. */
  const NEW_SCENE = () => ({ name: '', palette: [{ c: '#ff9a3c', b: 80 }, { c: '#e2557a', b: 70 }, { c: '#7c4dff', b: 60 }], dynamic: false, speed: 20, rooms: [] })
  let edit = null   // { orig, sc, sel }
  // Open the scene studio: edit an existing scene (on a copy) or start a new one.
  function studioMount(host, orig) {
    const sc = orig ? JSON.parse(JSON.stringify(orig)) : NEW_SCENE()
    edit = { orig, sc, sel: 0 }
    host.hidden = false
    const rooms = roomList()
    host.innerHTML = `<div class="card-head"><div><div class="eyebrow">${orig ? (orig.source === 'hue' ? 'Von Hue übernommen' : 'In ComfyOS angelegt') : 'Szenen-Studio'}</div>
        <h3>${orig ? 'Szene bearbeiten' : 'Neue Szene'}</h3></div>
        <button type="button" class="btn-icon sm plain" data-st="new" title="Schließen" aria-label="Studio schließen">${HO.icon('x')}</button></div>
      <div class="sc-studio-grid"><div>
        <label class="field"><span>Name</span><input type="text" id="scName" maxlength="40" value="${HO.esc(sc.name)}" autocomplete="off" placeholder="z. B. Filmabend"></label>
        <div class="sc-preview" id="scPrev"></div>
        <div class="row between" style="margin-top:14px"><span class="eyebrow">Farben</span><span class="tiny muted">bis 9 – der Reihe nach auf die Lampen verteilt</span></div>
        <div class="sc-pal" id="scPal"></div>
        <div class="row wrap g8" style="margin:10px 0 0">
          <button type="button" class="btn sm" data-add="c">${HO.icon('plus', 'sm')} Farbe</button>
          <button type="button" class="btn sm" data-add="ct">${HO.icon('plus', 'sm')} Weiß</button>
          <button type="button" class="btn sm" data-photo>${HO.icon('image', 'sm')} Aus Foto</button>
          <input type="file" accept="image/*" id="scFile" hidden></div>
        <label class="check" style="margin-top:16px"><input type="checkbox" id="scDyn" ${sc.dynamic ? 'checked' : ''}><span>Dynamisch – die Farben wandern langsam durch die Lampen</span></label>
        <div class="seg" id="scSpeed" style="margin-top:8px">${SPEEDS.map(([v, l]) => `<button type="button" data-speed="${v}">${l}</button>`).join('')}</div>
        <div class="row between" style="margin-top:16px"><span class="eyebrow">Räume</span>
          ${rooms.length ? '<span class="row g6"><button type="button" class="btn sm ghost" data-rooms="all">Alle</button><button type="button" class="btn sm ghost" data-rooms="none">Keine</button></span>' : ''}</div>
        <div class="chips" id="scRooms" style="margin-top:8px">${rooms.map(r => `<span class="chip" data-room="${HO.esc(r.room_id)}">${HO.esc(r.name)}${sc.variants && sc.variants[r.room_id] ? ` <span class="sc-var" title="Eigener Zustand für diesen Raum gespeichert">${HO.icon('check', 'sm')}</span>` : ''}</span>`).join('') || '<span class="tiny muted">Keine Räume bekannt.</span>'}</div>
        <p class="tiny muted" id="scRoomHint" style="margin:6px 0 0"></p></div>
        <div class="sc-edit" id="scEdit"></div></div>
      <div class="form-actions"><button type="button" class="btn ghost" data-st="new">Abbrechen</button>
        ${orig ? `<button type="button" class="btn ghost danger" data-st="delete">${HO.icon('trash', 'sm')} Löschen</button>` : ''}
        <button type="button" class="btn primary" data-st="save">${HO.icon('check', 'sm')} ${orig ? 'Speichern' : 'Szene anlegen'}</button></div>`
    studioPaint(host); studioPane(host)
  }
  // Redraw the studio: preview, palette, speed, rooms.
  function studioPaint(host) {
    const $ = s => host.querySelector(s), sc = edit.sc
    $('#scPrev').innerHTML = `<span style="background:${HO.paletteGradient(sc.palette)}"></span><b>${HO.esc($('#scName').value.trim() || 'Neue Szene')}</b>`
    $('#scPal').innerHTML = sc.palette.map((e, i) => `<button type="button" class="sc-sw ${i === edit.sel ? 'sel' : ''}" data-sel="${i}" style="background:${rgbCss(HO.entryRgb(e))}"
      aria-label="Farbe ${i + 1}">${e.ct ? '<span>W</span>' : ''}</button>`).join('')
    $('#scSpeed').hidden = !sc.dynamic
    host.querySelectorAll('[data-speed]').forEach(b => b.classList.toggle('active', +b.dataset.speed === sc.speed))
    host.querySelectorAll('#scRooms [data-room]').forEach(b => b.classList.toggle('active', sc.rooms.includes(b.dataset.room)))
    const hint = $('#scRoomHint')
    if (hint) {
      hint.textContent = sc.rooms.length ? 'Angeboten in den gewählten Räumen; die Hausszene auf der Startseite setzt sie dort.'
        : 'Noch keinem Raum zugeordnet – die Szene erscheint erst in einem Raum, wenn du ihn hier wählst.'
      hint.style.color = sc.rooms.length ? '' : 'var(--warn)'
    }
    host.querySelectorAll('[data-add]').forEach(b => { b.disabled = sc.palette.length >= 9 })
  }
  // Editing area of the chosen colour: colour wheel or white tone slider, plus brightness.
  function studioPane(host) {
    const $ = s => host.querySelector(s), sc = edit.sc, e = sc.palette[edit.sel]
    if (!e) { $('#scEdit').innerHTML = ''; return }
    const lo = 2000, hi = 6500
    $('#scEdit').innerHTML = `<div class="eyebrow" style="margin-bottom:10px">Farbe ${edit.sel + 1} von ${sc.palette.length}</div>
      ${e.c ? '<div class="lc-wheel" id="scWheel"></div>'
        : `<input type="range" class="lc-ct" min="${lo}" max="${hi}" step="50" value="${Math.round(1e6 / e.ct / 50) * 50}" id="scCt" style="background:${ctTrack(lo, hi)}" aria-label="Weißton">
         <div class="row between tiny muted"><span>warm</span><span id="scK">${Math.round(1e6 / e.ct / 50) * 50} K</span><span>kalt</span></div>`}
      <label class="field" style="margin-top:12px"><span>Helligkeit dieser Farbe · <b id="scBv">${e.b} %</b></span>
        <input type="range" min="1" max="100" value="${e.b}" id="scBri" class="lc-ct" style="background:linear-gradient(90deg,var(--card-3),${rgbCss(HO.entryRgb(e))})"></label>
      ${sc.palette.length > 1 ? `<button type="button" class="btn sm ghost danger" data-remove>${HO.icon('trash', 'sm')} Farbe entfernen</button>` : ''}`
    if (e.c) wheel($('#scWheel'), e.c, hex => { e.c = hex; studioPaint(host) },
      () => { $('#scBri').style.background = `linear-gradient(90deg,var(--card-3),${e.c})` })
  }
  // Close the studio without saving.
  function studioClose(host) {
    edit = null
    host.hidden = true
    host.innerHTML = ''
    renderSoon()
  }
  // Buttons of the studio: save, delete, cancel.
  async function studioAction(host, act) {
    const { orig, sc } = edit
    if (act === 'new') return studioClose(host)
    if (act === 'delete') {
      if (!await HO.confirm(`Szene „${orig.name}“ löschen?`, { title: 'Szene löschen', ok: 'Löschen', danger: true })) return
      try { await HO.api(`scenes/${encodeURIComponent(orig.id)}`, { method: 'DELETE' }) } catch (e) { return HO.toast(e.message, 'bad') }
      HO.toast('Szene gelöscht')
    } else {
      sc.name = host.querySelector('#scName').value.trim()
      if (!sc.name) return HO.toast('Bitte einen Namen eingeben.', 'warn')
      try {
        await HO.api(orig ? `scenes/${encodeURIComponent(orig.id)}` : 'scenes', { method: orig ? 'PUT' : 'POST',
          body: { name: sc.name, palette: sc.palette, dynamic: sc.dynamic, speed: sc.speed, rooms: sc.rooms } })
      } catch (e) { return HO.toast(e.message, 'bad') }
      HO.toast(orig ? 'Szene gespeichert' : `Szene „${sc.name}“ angelegt`)
    }
    studioClose(host)
    load(true)
  }
  // Set up the controls of the studio: choose colours, add, remove, take over from a photo, speed and rooms.
  function studioBind(host) {
    host.addEventListener('click', ev => {
      const t = ev.target.closest('[data-sel],[data-add],[data-photo],[data-remove],[data-speed],#scRooms [data-room],[data-rooms],[data-st]')
      if (!t || !edit) return
      const sc = edit.sc
      if (t.dataset.st) return studioAction(host, t.dataset.st)
      if (t.dataset.sel) { edit.sel = +t.dataset.sel; studioPane(host) }
      else if (t.dataset.add) { sc.palette.push(t.dataset.add === 'c' ? { c: '#ffb347', b: 80 } : { ct: 370, b: 80 }); edit.sel = sc.palette.length - 1; studioPane(host) }
      else if (t.hasAttribute('data-photo')) return host.querySelector('#scFile').click()
      else if (t.hasAttribute('data-remove')) { sc.palette.splice(edit.sel, 1); edit.sel = Math.max(0, edit.sel - 1); studioPane(host) }
      else if (t.dataset.speed) sc.speed = +t.dataset.speed
      else if (t.dataset.room) { const r = t.dataset.room; sc.rooms = sc.rooms.includes(r) ? sc.rooms.filter(x => x !== r) : sc.rooms.concat(r) }
      else if (t.dataset.rooms) sc.rooms = t.dataset.rooms === 'all' ? roomList().map(r => r.room_id) : []
      studioPaint(host)
    })
    host.addEventListener('input', ev => {
      if (!edit) return
      const e = edit.sc.palette[edit.sel]
      if (ev.target.id === 'scName') return studioPaint(host)
      if (!e) return
      if (ev.target.id === 'scCt') { e.ct = Math.round(1e6 / +ev.target.value); host.querySelector('#scK').textContent = ev.target.value + ' K' }
      if (ev.target.id === 'scBri') { e.b = +ev.target.value; host.querySelector('#scBv').textContent = e.b + ' %' }
      studioPaint(host)
    })
    host.addEventListener('change', async ev => {
      if (!edit) return
      if (ev.target.id === 'scDyn') { edit.sc.dynamic = ev.target.checked; return studioPaint(host) }
      if (ev.target.id !== 'scFile' || !ev.target.files[0]) return
      try {
        const cols = await photoPalette(ev.target.files[0])
        if (!cols.length) return HO.toast('Im Foto wurden keine Farben gefunden.', 'warn')
        edit.sc.palette = cols.map((c, i) => ({ c, b: Math.max(40, 80 - i * 8) })); edit.sel = 0
        studioPane(host); studioPaint(host)
        HO.toast(`${cols.length} Farben aus dem Foto übernommen – das Foto selbst wird nicht gespeichert.`, 'ok', 5000)
      } catch (err) { HO.toast('Foto konnte nicht gelesen werden.', 'bad') }
      ev.target.value = ''
    })
  }

  // Dialog "Szenen der Hue Bridge": tick a selection and take it over as ComfyOS scenes.
  async function bridgeImport(done) {
    let list
    try { list = (await HO.api('scenes/bridge')).scenes } catch (e) { return HO.toast(e.message, 'bad') }
    if (!list.length) return HO.toast('Auf der Hue Bridge sind keine Szenen mit Farben gespeichert.', 'warn')
    const have = new Set(scenesState().scenes.map(s => s.name.toLowerCase()))
    const sh = HO.sheet({ title: 'Szenen der Hue Bridge', wide: true, body: `<p class="small muted" style="margin-top:0">Übernommen werden Name und Farben.
      Danach gehören die Szenen zu ComfyOS und passen in jeden Raum – auch mit Lampen, die nicht an der Bridge hängen.
      Liegt eine Szene auf der Bridge in mehreren Räumen, kommt sie nur einmal. Übernommene Szenen sind zunächst
      <b>keinem Raum zugeordnet</b> – die Räume wählst du danach im Szenen-Studio.</p>
      <div class="list">${list.map(x => `<label class="check sc-imp"><input type="checkbox" value="${HO.esc(x.id)}" ${have.has(x.name.toLowerCase()) ? '' : 'checked'}>
        ${HO.lights.imgTag(x, 'sc-imp-img', HO.paletteGradient(x.palette))}
        <span class="grow"><b>${HO.esc(x.name)}</b>
        <span class="tiny muted">${(x.groups || []).length ? 'auf der Bridge: ' + HO.esc(x.groups.join(', ')) : ''}${have.has(x.name.toLowerCase()) ? ' · gibt es schon' : ''}</span></span></label>`).join('')}</div>`,
      actions: [{ label: 'Abbrechen', kind: 'ghost' }, { label: 'Übernehmen', kind: 'primary', icon: 'download', onClick: async api => {
        const ids = [...api.el.querySelectorAll('.sc-imp input:checked')].map(i => i.value)
        if (!ids.length) { HO.toast('Bitte mindestens eine Szene wählen.', 'warn'); return true }
        const r = await HO.api('scenes/bridge/import', { method: 'POST', body: { ids } })
        HO.toast(`${r.created.length} Szene${r.created.length === 1 ? '' : 'n'} übernommen – jetzt im Studio Räumen zuordnen`, 'ok', 5000); if (done) done()
      } }] })
    return sh
  }

  /* ---------- Page "Szenen" */
  let gen = 0, stream = null, raf = false, pageEl = null
  let tab = (() => { try { return localStorage.getItem('ho.scTab') || 'all' } catch (e) { return 'all' } })()

  // Where a scene is running right now (room or group name)
  function activeWhere(sid) {
    const ov = HO.state.overview, st = scenesState()
    const rooms = Object.fromEntries((ov.rooms || []).map(r => [r.room_id, r.name]))
    const groups = Object.fromEntries(HO.floorplan.groupsOf(ov.floorplan).map(g => [g.id, g.name || 'Gruppe']))
    return Object.entries(st.active || {}).filter(([, a]) => a.scene === sid)
      .map(([k]) => k.startsWith('room:') ? rooms[k.slice(5)] || k.slice(5) : groups[k.slice(6)] || 'Gruppe')
  }
  // Card of a scene in the library: colour gradient, name, details and where it is running right now.
  function cardHTML(s, names) {
    const where = activeWhere(s.id), pal = HO.paletteGradient(s.palette)
    const nVar = Object.keys(s.variants || {}).length
    const meta = [`${s.palette.length} Farbe${s.palette.length === 1 ? '' : 'n'}`, s.dynamic ? 'dynamisch' : '',
      s.rooms.length ? s.rooms.map(r => names[r] || r).join(', ') : 'Noch keinem Raum zugeordnet',
      nVar ? `${nVar} Raumvariante${nVar === 1 ? '' : 'n'}` : ''].filter(Boolean).join(' · ')
    return `<button type="button" class="sc-card ${where.length ? 'live' : ''} ${edit && edit.orig && edit.orig.id === s.id ? 'editing' : ''}" data-sc-edit="${HO.esc(s.id)}">
      <span class="sc-cover">${HO.lights.imgTag(s, 'sc-img', pal)}${s.source === 'hue' ? '<span class="sc-src">Hue</span>' : ''}</span>
      <span class="sc-meta"><b>${HO.esc(s.name)}</b><span class="tiny muted">${HO.esc(meta)}</span>
        ${where.length ? `<span class="tiny sc-live">${HO.icon('palette', 'sm')}${HO.esc(where.join(', '))}</span>` : ''}</span></button>`
  }
  // Library of the scenes with tabs (all, own, from Hue) and "Alle beenden".
  function libHTML() {
    const ov = HO.state.overview, st = scenesState(), names = Object.fromEntries((ov.rooms || []).map(r => [r.room_id, r.name]))
    const hue = st.scenes.filter(s => s.source === 'hue'), own = st.scenes.filter(s => s.source !== 'hue')
    if (!hue.length) tab = 'all'
    const list = (tab === 'hue' ? hue : tab === 'own' ? own : st.scenes).slice().sort((a, b) => HO.byName(a.name, b.name))
    const anyActive = Object.keys(st.active || {}).length
    return `<section class="section"><div class="section-head"><h3>Bibliothek</h3><div class="row wrap">
        ${anyActive ? `<button type="button" class="btn sm ghost" data-lt-stopall>${HO.icon('palette', 'sm')} Alle beenden</button>` : ''}
        ${hue.length ? `<div class="seg">${[['all', 'Alle', st.scenes.length], ['own', 'ComfyOS', own.length], ['hue', 'Hue', hue.length]]
          .map(([k, l, n]) => `<button type="button" data-sc-tab="${k}" class="${tab === k ? 'active' : ''}">${l} <span class="muted">${n}</span></button>`).join('')}</div>` : ''}</div></div>
      ${list.length ? `<div class="sc-grid">${list.map(s => cardHTML(s, names)).join('')}</div>` : '<div class="card empty"><div>Noch keine Szenen.</div></div>'}
      <p class="tiny muted">Eine Szene ist eine Farbpalette: ComfyOS verteilt die Farben auf die Lampen im Raum – Verlaufslampen bekommen mehrere.
        Gestartet werden Szenen auf der Startseite in den Raumkarten (nur dort, wo sie zugeordnet sind) oder für das ganze Haus in der Kachel „Licht &amp; Szenen“ unter dem Grundriss.
        Im Raum lässt sich der aktuelle Zustand als Raumvariante speichern. Antippen einer Karte hier öffnet sie oben im Studio.</p></section>`
  }
  // Draw the page "Licht & Szenen" from the current state: rooms with light, library, studio.
  function render() {
    const ov = HO.state.overview, page = pageEl
    if (!page || !ov) return
    if (!ov.status.configured) {
      page.innerHTML = `<div class="card empty">${HO.icon('palette', 'lg')}<div>Noch nichts verbunden.</div><button class="btn primary" data-goto="settings?tab=plugins">Zu den Plugins</button></div>`
      return
    }
    if (!page.querySelector('#scStudio')) {
      // head without a tile as in Flow (since 0.19.2): import (e.g. from the Hue Bridge) and "Neu" open the studio
      page.innerHTML = `<div class="section-head"><div><h2 style="margin:0">Szenen</h2>
          <div class="small muted">Farbpaletten für Räume und das ganze Haus – gestartet wird auf der Startseite.</div></div>
          <div class="row"><button class="btn sm" data-act="scImport">${HO.icon('upload', 'sm')} Import</button>
          <button class="btn sm primary" data-act="scNew">${HO.icon('plus', 'sm')} Neu</button></div></div>
        <section class="card sc-studio" id="scStudio" hidden></section><div id="scLib"></div>`
      studioBind(page.querySelector('#scStudio'))
    }
    page.querySelector('#scLib').innerHTML = libHTML()
  }
  // Redraw in the next frame – several messages in quick succession give one drawing.
  function renderSoon() {
    if (raf) return
    raf = true
    requestAnimationFrame(() => { raf = false; render() })
  }
  // Fetch the state anew and draw (discarded if the page has changed meanwhile).
  async function load(quiet) {
    const my = gen
    try {
      const ov = await HO.api('overview')
      if (my !== gen) return
      HO.state.overview = ov
      renderSoon()
    } catch (e) { if (!quiet) HO.toast(e.message, 'bad') }
  }
  const loadSoon = HO.debounce(() => load(true), 400)
  // Subscribe to live messages: lamps, scenes and effects change without a reload.
  function openStream() {
    if (stream) return
    stream = HO.live.on(m => {
      const ov = HO.state.overview
      if (!ov || !ov.devices) return
      if (m.type === 'reload') return loadSoon()
      if (m.type === 'toast') return HO.toast(m.text, 'ok', 6000)
      if (m.type === 'scenes') { ov.scenes = { scenes: m.scenes, active: m.active }; return renderSoon() }
    })
  }

  // Page "Szenen" (the route stays #light): studio for creating/editing, below it the library.
  // Scenes are started in the room cards of the home page.
  HO.pages.light = {
    title: 'Szenen',
    // Open the page: draw at once from the last state, then refresh; set up live stream and controls.
    async render(page) {
      pageEl = page
      const my = ++gen
      edit = null
      if (HO.state.overview) {
        render()          // page change: at once from the last state, then refresh
        load(true)
      } else {
        page.innerHTML = '<div class="card"><div class="spinner"></div></div>'
        await load()
        if (my !== gen) return
        if (!HO.state.overview) throw new Error('Szenen konnten nicht geladen werden.')
        render()
      }
      openStream()
      HO.lights.onScenes = renderSoon
      page.onclick = async e => {
        const t = e.target.closest('[data-lt-stopall],[data-sc-edit],[data-act],[data-sc-tab],[data-goto]')
        if (!t) return
        if (t.dataset.goto) return HO.go(t.dataset.goto)
        if (t.hasAttribute('data-lt-stopall')) {
          try { const r = await HO.api('scenes/stop', { method: 'POST', body: {} }); HO.toast(`${r.stopped} Szene${r.stopped === 1 ? '' : 'n'} beendet`) } catch (err) { HO.toast(err.message, 'bad') }
          return
        }
        if (t.dataset.scTab) { tab = t.dataset.scTab; try { localStorage.setItem('ho.scTab', tab) } catch (err) { } return renderSoon() }
        if (t.dataset.act === 'scImport') {
          return HO.menu('Szenen importieren', [{ label: 'Von der Hue Bridge', icon: 'download', onClick: () => bridgeImport(() => load(true)) }])
        }
        const host = page.querySelector('#scStudio')
        if (!host || host.contains(t)) return
        const sc = t.dataset.scEdit && sceneById(t.dataset.scEdit)
        if (t.dataset.act === 'scNew') studioMount(host, null)
        else if (sc) studioMount(host, sc)
        else return
        renderSoon()
        host.scrollIntoView({ behavior: 'smooth', block: 'start' })
        if (!sc) { const name = host.querySelector('#scName'); if (name && window.matchMedia('(min-width: 700px)').matches) name.focus() }
      }
    },
    destroy() {
      gen++; pageEl = null; edit = null; HO.lights.onScenes = null
      if (stream) { stream(); stream = null }
    }
  }
})(window.HO)
