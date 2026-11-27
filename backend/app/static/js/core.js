/* ComfyOS – Kern: API, Icons, Formatierung, Sheets, Toasts */
window.HO = window.HO || {};
(function (HO) {
  'use strict'

  HO.state = { tab: 'home', overview: null, settings: null }

  /* ---------- Performance tier of this device: full or economical (since 0.20)
     Economical means: no blur behind surfaces, shadows in the floor plan without filters, weather effects with fewer
     pixels. Nothing is dropped, it is only painted more cheaply. The tier is determined like this:
       1. chosen under System → Diagnose ("voll" or "sparsam") – always applies;
       2. otherwise measured: if the weather effects cannot keep up, the device remembers "economical"
          (weather_fx.js calls learnLow) – on the next start it applies from the beginning;
       3. otherwise assumed: a browser without `gap` in flex containers (Safari up to 14.0) runs on an old device.
     Stored in the browser (localStorage `ho.perf`) – the tier belongs to the device, not to the installation. */
  HO.perf = (function () {
    const KEY = 'ho.perf', root = document.documentElement
    const read = () => { try { return localStorage.getItem(KEY) } catch (e) { return null } }
    const write = v => { try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY) } catch (e) { } }
    let stored = read()
    const old = root.classList.contains('no-flexgap')
    const api = {
      // 'auto' | 'high' | 'low' – what the user has chosen
      get mode() { return stored === 'high' || stored === 'low' ? stored : 'auto' },
      get low() { return api.mode === 'auto' ? (stored === 'learned-low' || old) : api.mode === 'low' },
      // why the tier applies (for the diagnosis page)
      get reason() { return api.mode !== 'auto' ? 'gewählt' : stored === 'learned-low' ? 'gemessen' : old ? 'älterer Browser' : 'Standard' },
      set(mode) { stored = mode === 'high' || mode === 'low' ? mode : null; write(stored); apply() },
      learnLow() { if (api.mode === 'auto' && stored !== 'learned-low') { stored = 'learned-low'; write(stored); apply() } }
    }
    function apply() { root.classList.toggle('perf-low', api.low) }
    apply()
    return api
  })()
  HO.pages = {}
  HO.ackedErrors = {}

  /* ---------- API */
  HO.api = async function (path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' }
    if (opts.body !== undefined) {
      if (opts.raw) { init.body = opts.body; init.headers['Content-Type'] = opts.raw }
      else { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json' }
    }
    const r = await fetch('/api/' + path, init)
    const fail = (msg, detail) => Object.assign(new Error(msg), { status: r.status, detail })
    if (r.status === 401 && !path.startsWith('auth/')) {
      if (HO.onUnauthorized) HO.onUnauthorized()
      throw fail('Nicht angemeldet', 'Nicht angemeldet')
    }
    const ct = r.headers.get('content-type') || ''
    const data = ct.includes('json') ? await r.json().catch(() => null) : await r.text()
    if (!r.ok) {
      const d = data && data.detail
      if (r.status === 402 && d && d.code === 'pro_required') { HO.upsell(d); throw fail(d.message || d.title, d) }
      throw fail(typeof d === 'string' ? d : (d ? JSON.stringify(d) : 'Fehler ' + r.status), d)
    }
    return data
  }

  /* ---------- Plan & Pro (display only – enforcement happens in the backend) */
  HO.ents = { enforce: false, plan: 'basic', features: {}, limits: {} }
  HO.loadEnts = () => HO.api('entitlements').then(e => { HO.ents = e; return e }).catch(() => HO.ents)
  /* Stored passwords: the field shows dots (placeholder value) so that one sees something is stored –
     the real value never leaves the server. HO.pwAttrs(stored) returns the attributes for the <input type="password">,
     HO.pwValue(field) the newly entered value or '' = leave unchanged. On typing into it the field is selected
     (app.js), so that the first input replaces the dots. */
  HO.PW_SAVED = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022'
  HO.pwAttrs = saved => saved ? `value="${HO.PW_SAVED}" data-saved="1" title="Gespeichert – zum Ändern neu eingeben"` : ''
  HO.pwValue = el => !el || el.value === HO.PW_SAVED ? '' : el.value
  HO.can = id => !HO.ents.enforce || !!(HO.ents.features[id] && HO.ents.features[id].granted)
  HO.proTag = id => HO.can(id) ? '' : '<span class="pro-tag">Pro</span>'
  HO.upsell = detail => {
    if (!HO.ents.enforce) return
    const avail = Object.values(HO.ents.features || {}).filter(f => f.status === 'available')
    HO.sheet({ title: (detail && detail.title) || 'ComfyOS Pro', body: `
      ${detail && detail.message ? `<p>${HO.esc(detail.message)}</p>` : ''}
      <div class="notice accent">${HO.icon('info', 'sm')}<div>Mit Pro bekommst du:</div></div>
      <ul class="pro-list">${avail.map(f => `<li><b>${HO.esc(f.title)}</b><span class="small muted">${HO.esc(f.desc)}</span></li>`).join('')}</ul>`,
      actions: [{ label: 'Später', kind: 'ghost' },
        { label: 'Lizenz eingeben', kind: 'primary', onClick: () => { location.hash = 'settings' } }] })
  }

  /* ---------- Text & Format */
  HO.esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const nf1 = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  const nf0 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
  HO.temp = (v, fixed) => v == null || isNaN(v) ? '–' : (fixed ? nf1 : nf0).format(v) + '°'
  HO.num = v => nf0.format(v)
  HO.relTime = ts => {
    if (!ts) return 'nie'
    const t = typeof ts === 'number' ? ts * (ts < 1e12 ? 1000 : 1) : Date.parse(ts)
    const s = Math.round((Date.now() - t) / 1000)
    if (s < 45) return 'gerade eben'
    if (s < 3600) return 'vor ' + Math.round(s / 60) + ' Min.'
    if (s < 86400) return 'vor ' + Math.round(s / 3600) + ' Std.'
    return new Date(t).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) + ' ' +
      new Date(t).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
  }
  HO.dayNames = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
  HO.dayLong = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
  HO.todayIndex = () => (new Date().getDay() + 6) % 7   // 0 = Montag
  HO.debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms) } }

  /* Fields of a device row that the live stream reports individually – the same list as core.LIVE_FIELDS in the
     backend (tests/core/test_style.py compares both). Whoever takes over live messages uses this list. */
  HO.LIVE_FIELDS = ['online', 'on', 'brightness', 'light', 'target', 'temperature', 'open', 'motion', 'alarm',
    'power_w', 'position', 'locked', 'valve', 'present', 'humidity', 'playing', 'volume', 'muted', 'media', 'illuminance']

  // Can the device be controlled right now? Reachable, with a control channel and not locked – the one place for it.
  HO.canControl = d => !!(d && d.online && d.control_item && !d.locked)

  /* Write HTML into an element – but only if it has changed. The home page gets fresh data every few seconds;
     mostly everything is as before afterwards. Rewriting would still mean: create elements anew, lay them out
     anew, paint anew – and opened sections, scroll positions and running animations would be lost.
     If the user has tapped or entered something in the element, it is written in any case the next time:
     its state (a flipped switch) then no longer comes from the HTML written last.
     Returns true if it was written. */
  HO.setHTML = (el, html) => {
    if (!el) return false
    if (!el.__htmlWatched) {
      el.__htmlWatched = true
      const touched = () => { el.__htmlTouched = true }
      ;['click', 'input', 'change'].forEach(type => el.addEventListener(type, touched, true))
    }
    if (el.__html === html && !el.__htmlTouched) return false
    el.innerHTML = html
    el.__html = html
    el.__htmlTouched = false
    return true
  }
  // To the top of the page – smoothly where the browser can. Safari up to 13 does not know scrolling with options
  // as an object and would do nothing.
  HO.toTop = () => {
    if ('scrollBehavior' in document.documentElement.style) window.scroll({ top: 0, behavior: 'smooth' })
    else window.scrollTo(0, 0)
  }
  HO.byName = (a, b) => String(a).localeCompare(String(b), 'de')

  /* Accent colour as [r, g, b] – follows --accent (default #109482, Pro: own colour through HO.applyAccent) */
  const ACCENT_DEFAULT = [16, 148, 130]
  HO.accentRGB = () => {
    let v = ''
    try { v = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() } catch (e) { }
    const m = /^#?([0-9a-f]{6})$/i.exec(v)
    if (!m) return ACCENT_DEFAULT.slice()
    const c = parseInt(m[1], 16)
    return [(c >> 16) & 255, (c >> 8) & 255, c & 255]
  }
  /* Temperature -> colour: one scale from neutral grey (cold) to the accent colour (warm), lighter at the top.
     Applies to heating schedule, legend and heat map – no fixed magenta any more. */
  const mixRGB = (a, b, f) => a.map((c, k) => Math.round(c + (b[k] - c) * f))
  let tempStopsKey = '', TEMP_STOPS = []
  // Colour steps of the temperature scale (grey → accent colour → light); recomputed as soon as the accent colour changes.
  const tempStops = () => {
    const acc = HO.accentRGB(), key = acc.join(',')
    if (key !== tempStopsKey) {
      const grey = [78, 74, 88]
      TEMP_STOPS = [[5, [62, 61, 70]], [12, grey], [16, mixRGB(grey, acc, 0.3)], [18, mixRGB(grey, acc, 0.55)],
        [20, mixRGB(grey, acc, 0.8)], [22, acc], [25, mixRGB(acc, [255, 255, 255], 0.35)]]
      tempStopsKey = key
    }
    return TEMP_STOPS
  }
  HO.tempRGB = t => {
    if (t == null || isNaN(t)) return [60, 66, 80]
    const S = tempStops()
    if (t <= S[0][0]) return S[0][1]
    for (let i = 1; i < S.length; i++) {
      if (t <= S[i][0]) {
        const [t0, c0] = S[i - 1], [t1, c1] = S[i], f = (t - t0) / (t1 - t0)
        return mixRGB(c0, c1, f)
      }
    }
    return S[S.length - 1][1]
  }
  HO.tempColor = (t, a = 1) => { const [r, g, b] = HO.tempRGB(t); return `rgba(${r},${g},${b},${a})` }
  HO.tempGradient = () => 'linear-gradient(90deg,' + tempStops().map(([t, c]) => `rgb(${c.join(',')}) ${((t - 5) / 20 * 100).toFixed(0)}%`).join(',') + ')'

  /* ---------- Icons (eigene, 24er-Raster, Linien) */
  const P = {
    home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
    flame: '<path d="M12 3c.5 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 2.5-5 .2 2 1 3 2.2 3.3C11.3 9 11 6 12 3z"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    sync: '<path d="M4 12a8 8 0 0 1 13.7-5.7L20 9"/><path d="M20 4v5h-5"/><path d="M20 12a8 8 0 0 1-13.7 5.7L4 15"/><path d="M4 20v-5h5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h4"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
    thermostat: '<path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 11v6"/>',
    contact: '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M12 3v18M5 12h14"/>',
    motion: '<circle cx="12" cy="12" r="2"/><path d="M8 8a5.6 5.6 0 0 0 0 8M16 8a5.6 5.6 0 0 1 0 8M5 5a9.9 9.9 0 0 0 0 14M19 5a9.9 9.9 0 0 1 0 14"/>',
    presence: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    light: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.6.6 1 1.4 1 2.5h6c0-1.1.4-1.9 1-2.5A6 6 0 0 0 12 3z"/>',
    switch: '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0z"/><path d="M12 16v5"/>',
    sensor: '<path d="M4 16a8 8 0 1 1 16 0"/><path d="M12 16l4-5"/><circle cx="12" cy="16" r="1"/>',
    smoke: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 4v2"/>',
    media: '<rect x="6" y="3" width="12" height="18" rx="2"/><circle cx="12" cy="14" r="3"/><circle cx="12" cy="7" r=".8"/>',
    button: '<rect x="6" y="3" width="12" height="18" rx="2.5"/><rect x="9" y="6.5" width="6" height="4.5" rx="1"/><rect x="9" y="13" width="6" height="4.5" rx="1"/>',
    cast: '<path d="M3 8V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><path d="M3 12a8 8 0 0 1 8 8"/><path d="M3 16a4 4 0 0 1 4 4"/><circle cx="3.6" cy="19.4" r=".8"/>',
    device: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="12" cy="12" r="3"/>',
    battery: '<rect x="3" y="8" width="16" height="8" rx="2"/><path d="M21 11v2M6 11v2"/>',
    offline: '<path d="M3 3l18 18"/><path d="M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.8M19 13a10 10 0 0 0-2.3-1.7M2 9.5a15 15 0 0 1 4-2.6M22 9.5A15 15 0 0 0 13 6"/>',
    away: '<path d="M14 3H6v18h8"/><path d="M10 12h11M17 8l4 4-4 4"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>', upload: '<path d="M12 21V9M7 14l5-5 5 5M5 3h14"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    list: '<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    cover: '<path d="M3 4h18"/><rect x="5" y="4" width="14" height="10" rx="1"/><path d="M5 7.5h14M5 11h14"/><path d="M9 17l3 3 3-3"/>',
    cols1: '<rect x="6" y="4" width="12" height="16" rx="2"/>',
    cols2: '<rect x="3.5" y="4" width="7.5" height="16" rx="2"/><rect x="13" y="4" width="7.5" height="16" rx="2"/>',
    cols3: '<rect x="2.5" y="4" width="5" height="16" rx="1.5"/><rect x="9.5" y="4" width="5" height="16" rx="1.5"/><rect x="16.5" y="4" width="5" height="16" rx="1.5"/>',
    move: '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>',
    grip: '<circle cx="9" cy="6" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/><circle cx="15" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="18" r="1.3"/>',
    map: '<path d="M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>',
    pointer: '<path d="M5 3l14 8-6 2-2 6z"/>',
    square: '<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M4 9h16" stroke-dasharray="2 2"/>',
    wand: '<path d="M4 20 15 9"/><path d="M14 4v2M20 10h-2M18 5l-1.4 1.4M18 15l-1.4-1.4M13 9l2 2"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
    warn: '<path d="M12 3 2 20h20z"/><path d="M12 9v5M12 17v.01"/>',
    power: '<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    cloud: '<path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    logout: '<path d="M14 3H6v18h8"/><path d="M10 12h11M17 8l4 4-4 4"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>', star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    poly: '<path d="M4 18 7 5l9 3 4 10z"/>',
    wall: '<path d="M4 20V8h10V4"/><path d="M4 20h16"/>',
    radiator: '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M7.5 5v12M12 5v12M16.5 5v12M6 17v2M18 17v2"/>',
    passage: '<path d="M3 12h6M15 12h6"/><path d="M9 9v6M15 9v6"/>',
    deck: '<rect x="4" y="6" width="16" height="12" rx="1.5"/><path d="M4 10h16M4 14h16M10 6v4M14 10v4M9 14v4"/>',
    stairs: '<path d="M4 20h4v-4h4v-4h4V8h4"/>',
    mirror: '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 7 4 12l5 5V7zM15 7l5 5-5 5V7z"/>',
    flip: '<path d="M3 12h18" stroke-dasharray="2 2"/><path d="M7 9l5-5 5 5H7zM7 15l5 5 5-5H7z"/>',
    pin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
    door: '<path d="M4 21h16"/><path d="M7 21V4h9v17"/><path d="M13 12.5v.01"/>',
    calendar: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    maximize: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
    minimize: '<path d="M8 3v5H3M16 3v5h5M3 16h5v5M21 16h-5v5"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
    type: '<path d="M4 6V4h16v2M12 4v16M8 20h8"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/><rect x="8.5" y="8.5" width="7" height="7" rx="1"/>',
    // Wetter (Open-Meteo-Symbole)
    'wx-sun': '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    'wx-moon': '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    'wx-partly': '<path d="M9 3.5v1.5M3.5 9H5M5.1 5.1l1 1M12.9 5.1l-1 1"/><path d="M6.3 11.3a3.5 3.5 0 0 1 6.4-2.7"/><path d="M9 20h8.5a3.5 3.5 0 0 0 .4-7 5 5 0 0 0-9.6.9A3.1 3.1 0 0 0 9 20z"/>',
    'wx-partly-night': '<path d="M11.5 4.5a5 5 0 0 0-5.8 6.3"/><path d="M9 20h8.5a3.5 3.5 0 0 0 .4-7 5 5 0 0 0-9.6.9A3.1 3.1 0 0 0 9 20z"/>',
    'wx-cloud': '<path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"/>',
    'wx-fog': '<path d="M7 13h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 4.5a4.3 4.3 0 0 0 1 8.5z"/><path d="M4 17h16M6 20.5h12"/>',
    'wx-drizzle': '<path d="M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5.5a4.3 4.3 0 0 0 1 8.5z"/><path d="M8.5 17.5v.5M12 19v.5M15.5 17.5v.5"/>',
    'wx-rain': '<path d="M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5.5a4.3 4.3 0 0 0 1 8.5z"/><path d="M8.5 17l-1 3M12.5 17l-1 3M16.5 17l-1 3"/>',
    'wx-snow': '<path d="M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5.5a4.3 4.3 0 0 0 1 8.5z"/><path d="M8.5 18h.01M12 20h.01M15.5 18h.01M10 21.5h.01M14 21.5h.01"/>',
    'wx-storm': '<path d="M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5.5a4.3 4.3 0 0 0 1 8.5z"/><path d="M12.5 14l-2.5 4h4l-2.5 4"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    // ComfyOS Flow
    workflow: '<circle cx="4" cy="12" r="2"/><circle cx="20" cy="6" r="2"/><circle cx="20" cy="18" r="2"/><path d="M6 12c4 0 6-6 12-6M6 12c4 0 6 6 12 6"/>',
    bolt: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
    filter: '<path d="M4 5h16l-6 8v6l-4-2v-4z"/>',
    split: '<path d="M3 12h6"/><path d="M9 12c3.5 0 4-5 8.5-5M9 12c3.5 0 4 5 8.5 5"/><path d="M16 4.5 18.5 7 16 9.5M16 14.5l2.5 2.5-2.5 2.5"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    repeat: '<path d="M4 11V9a3 3 0 0 1 3-3h12"/><path d="M16 3l3 3-3 3"/><path d="M20 13v2a3 3 0 0 1-3 3H5"/><path d="M8 21l-3-3 3-3"/>',
    tag: '<path d="M3 12V4h8l9 9-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="M12 17l4-6"/><path d="M7 20h10"/>',
    hash: '<path d="M10 4 8 20M16 4l-2 16M5 9h15M4 15h15"/>',
    toggle: '<rect x="2" y="7" width="20" height="10" rx="5"/><circle cx="16" cy="12" r="2.8"/>',
    hourglass: '<path d="M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"/>',
    dice: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r=".9"/><circle cx="15" cy="15" r=".9"/><circle cx="15" cy="9" r=".9"/><circle cx="9" cy="15" r=".9"/>',
    shuffle: '<path d="M3 7h3c5 0 7 10 12 10h3"/><path d="M3 17h3c2 0 3.5-1.5 4.7-3.5M13.3 9.5C14.5 8 16 7 18 7h3"/><path d="M18 4l3 3-3 3M18 14l3 3-3 3"/>',
    bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    pause: '<path d="M9 5v14M15 5v14"/>',
    undo: '<path d="M4 7h11a4 4 0 0 1 0 8H9"/><path d="M7 4 4 7l3 3"/>',
    redo: '<path d="M20 7H9a4 4 0 0 0 0 8h6"/><path d="M17 4l3 3-3 3"/>',
    paste: '<rect x="5" y="5" width="14" height="16" rx="2"/><path d="M9 5V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1M9 12h6M9 16h4"/>',
    select: '<rect x="4" y="4" width="16" height="16" rx="2" stroke-dasharray="3 3"/><path d="m11 11 7 2.6-3 1.2-1.2 3z"/>',
    // Licht & Szenen
    palette: '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.8-.9 1.4-2l-.4-1a1.8 1.8 0 0 1 1.7-2.5H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10" cy="7" r="1.2"/><circle cx="15" cy="7.5" r="1.2"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>'
  }
  HO.iconPaths = P
  /* ---------- Columns (since 0.19.2): System page and room tiles of the home page in 1–3 columns. Chosen per device
     (localStorage ho.cols.<page>); only what fits the width is offered (at least COL_MIN pixels per column) –
     on the phone it stays one column and the choice disappears. */
  HO.cols = {
    MIN: 340, GAP: 18,
    fit(el) { const w = (el && el.clientWidth) || document.documentElement.clientWidth; return Math.max(1, Math.min(3, Math.floor((w + HO.cols.GAP) / (HO.cols.MIN + HO.cols.GAP)))) },
    chosen(key, def) { try { const v = +localStorage.getItem('ho.cols.' + key); return v >= 1 && v <= 3 ? v : def } catch (e) { return def } },
    choose(key, n) { try { localStorage.setItem('ho.cols.' + key, String(n)) } catch (e) { } },
    segHTML(key, cur) {
      return `<div class="seg cols-seg" data-cols-seg="${key}" hidden title="Spalten">${[1, 2, 3].map(n =>
        `<button type="button" data-cols="${n}" class="${n === cur ? 'active' : ''}" title="${n} Spalte${n === 1 ? '' : 'n'}" aria-label="${n} Spalte${n === 1 ? '' : 'n'}">${HO.icon('cols' + n, 'sm')}</button>`).join('')}</div>`
    },
    // adapt the choice to the width: hide it if only one column fits; grey out too many columns
    syncSeg(root, key, fit) {
      const seg = root && root.querySelector(`[data-cols-seg="${key}"]`)
      if (!seg) return
      const cur = Math.min(HO.cols.chosen(key, 2), fit)
      seg.hidden = fit < 2
      seg.querySelectorAll('[data-cols]').forEach(b => { const n = +b.dataset.cols; b.disabled = n > fit; b.classList.toggle('active', n === cur) })
    }
  }
  /* ---------- Arranging tiles freely (since 0.19.2 System, since 0.19.3 also the room tiles of the home page)
     HO.tileGrid({ page, box, key, saved, item }): box = container of the tiles (gets the class "mz"), key = page for
     column choice and storage place (system | plugins | home), saved = stored arrangement {"<n>": [[id, …], …]},
     item = selector of the tiles (default .card; each needs an id – if it is missing, one is assigned).
     The tiles stand in 1–3 columns (HO.cols, chosen per device; one on the phone). Every column is a stack of its own:
     flush at the top, every tile only as high as its content. Without an own arrangement ComfyOS distributes the tiles in
     turn into the currently shortest column (the columns get about equally long) – also while content is still loading,
     but never again once one does something on the page (otherwise tiles would jump under the finger). "Anordnen" puts
     a surface for dragging over every tile (mouse: everywhere, finger: at the handle) with arrows. The arrangement holds
     per number of columns and lives in settings.json (PUT /api/settings/layout), that is on all devices.
     If a page redraws the content of box (home page on every change), it calls refresh() afterwards; while
     "Anordnen" is running, it does not redraw box. */
  HO.tileGrid = function ({ page, box, key, saved, item }) {
    item = item || '.card'
    saved = saved || {}
    box.classList.add('mz')
    let order = []
    let cols = 0, arranging = false, settleUntil = Date.now() + 4000, drag = null, saveTimer = null, roTimer = null
    // Height of the fully distributed container – layout() fixes it while it rebuilds (see there). An observer
    // updates it when content loads later; it only reports after the rebuild, never in the middle.
    let lastHeight = 0
    const heightRO = new ResizeObserver(() => { lastHeight = box.offsetHeight })
    heightRO.observe(box)
    // All tiles of the container – no matter whether they already stand in columns or still lie loose in it.
    const tiles = () => {
      const out = []
      for (const c of [...box.children]) {
        if (c.classList.contains('mz-col')) out.push(...[...c.children].filter(x => x.matches(item)))
        else if (c.matches(item)) out.push(c)
      }
      return out
    }
    // order in the HTML = default; every tile needs an id, otherwise it would be lost when distributing
    const readOrder = () => { order = tiles().map((c, i) => { if (!c.id) c.id = key + '-tile-' + i; return c.id }) }
    readOrder()
    const rank = c => { const i = order.indexOf(c.id); return i < 0 ? order.length : i }
    const colEls = () => [...box.children].filter(c => c.classList.contains('mz-col'))
    const height = c => c.getBoundingClientRect().height
    const wanted = () => Math.min(HO.cols.chosen(key, 2), HO.cols.fit(box))

    // distribute tiles: own arrangement of this number of columns, all others (new plugins …) into the shortest column each
    function layout() {
      // The container is emptied and refilled here – and already arrived with tiles lying loosely side by side
      // if the page has just rewritten it. Without a fixed minimum height the page is much shorter for a moment;
      // because measuring happens along the way, the browser computes in the middle and jumped to the top as soon as one
      // stood further down – on the home page with every live message of a device (Safari and iPhone). Therefore, as
      // the very first thing, before any measurement, fix the last known height and remember the scroll position.
      const locked = lastHeight > 0
      if (locked) box.style.minHeight = lastHeight + 'px'
      const scrollY = locked ? window.pageYOffset : 0
      const n = wanted()
      HO.cols.syncSeg(page, key, HO.cols.fit(box))
      const all = tiles().sort((a, b) => rank(a) - rank(b)), byId = {}
      if (!all.length) {               // nothing to distribute (e.g. "Keine Geräte in dieser Auswahl") – the content stays
        box.style.minHeight = ''
        lastHeight = box.offsetHeight
        return
      }
      all.forEach(c => { byId[c.id] = c })
      const focus = document.activeElement
      const columns = []
      for (let i = 0; i < n; i++) { const d = document.createElement('div'); d.className = 'mz-col'; columns.push(d) }
      box.textContent = ''
      columns.forEach(d => box.appendChild(d))
      const plan = saved[String(n)], placed = new Set()
      if (plan) plan.forEach((ids, i) => ids.forEach(id => { if (byId[id] && !placed.has(id)) { columns[i].appendChild(byId[id]); placed.add(id) } }))
      const rest = all.filter(c => !placed.has(c.id))
      // measure heights at column width: put the other tiles invisibly into a measuring column of the same width for a moment
      const probe = document.createElement('div')
      probe.className = 'mz-probe'
      probe.style.width = columns[0].getBoundingClientRect().width + 'px'
      box.appendChild(probe)
      rest.forEach(c => probe.appendChild(c))
      const heights = rest.map(height), sums = columns.map(height)
      rest.forEach((c, i) => {
        let k = 0
        for (let j = 1; j < n; j++) if (sums[j] < sums[k] - 1) k = j
        columns[k].appendChild(c)
        sums[k] += heights[i] + HO.cols.GAP
      })
      probe.remove()
      cols = n
      box.style.minHeight = ''
      lastHeight = box.offsetHeight
      if (locked && Math.abs(window.pageYOffset - scrollY) > 1) window.scrollTo(window.pageXOffset, scrollY)
      if (focus && focus !== document.activeElement && box.contains(focus)) { try { focus.focus({ preventScroll: true }) } catch (e) { } }
      if (arranging) overlays(true)
    }
    const current = () => colEls().map(col => [...col.children].filter(c => c.id).map(c => c.id))
    // Remember the arrangement for the current number of columns and save it shortly after (several moves give one request).
    function save() {
      clearTimeout(saveTimer)
      const columns = current(), n = cols
      saved[String(n)] = columns
      saveTimer = setTimeout(() => HO.api('settings/layout', { method: 'PUT', body: { page: key, cols: n, columns } })
        .catch(e => HO.toast(e.message, 'bad')), 500)
    }

    // arranging: surface with handle, name and arrows over every tile
    function overlays(on) {
      tiles().forEach(c => {
        [...c.children].filter(x => x.classList.contains('mz-over')).forEach(x => x.remove())
        if (!on) return
        const h = c.querySelector('h3'), o = document.createElement('div')
        o.className = 'mz-over'
        o.innerHTML = `<button type="button" class="mz-grip" aria-label="Ziehen zum Verschieben" title="Ziehen zum Verschieben">${HO.icon('grip')}</button>
          <b class="mz-name">${HO.esc(h ? h.textContent : c.id)}</b>
          <div class="mz-btns">${[['up', 'Nach oben'], ['down', 'Nach unten'], ['left', 'Eine Spalte nach links'], ['right', 'Eine Spalte nach rechts']]
            .map(([d, l]) => `<button type="button" class="btn-icon sm mz-${d}" data-mz="${d}" aria-label="${l}" title="${l}" ${cols < 2 && (d === 'left' || d === 'right') ? 'hidden' : ''}>${HO.icon('chevron', 'sm')}</button>`).join('')}</div>`
        c.appendChild(o)
      })
    }
    // Move a tile with the arrows: up or down in its column, or into the neighbouring column.
    function move(card, dir) {
      const col = card.parentElement, list = colEls(), ci = list.indexOf(col)
      if (dir === 'up' && card.previousElementSibling) col.insertBefore(card, card.previousElementSibling)
      else if (dir === 'down' && card.nextElementSibling) col.insertBefore(card.nextElementSibling, card)
      else if (dir === 'left' || dir === 'right') {
        const to = list[ci + (dir === 'left' ? -1 : 1)]
        if (!to) return
        const idx = [...col.children].indexOf(card)
        to.insertBefore(card, to.children[idx] || null)
      } else return
      save()
      card.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
    // dragging: column under the pointer (otherwise the nearest), there before the first tile whose centre is lower
    function dropAt(x, y) {
      const list = colEls()
      let col = list.find(c => { const r = c.getBoundingClientRect(); return x >= r.left && x <= r.right })
      if (!col) {
        let best = Infinity
        list.forEach(c => { const r = c.getBoundingClientRect(), d = Math.abs(x - (r.left + r.right) / 2); if (d < best) { best = d; col = c } })
      }
      const before = [...col.children].find(c => { if (c === drag.card) return false; const r = c.getBoundingClientRect(); return y < r.top + r.height / 2 })
      if (drag.card.parentElement === col && (before ? drag.card.nextElementSibling === before : !drag.card.nextElementSibling)) return
      col.insertBefore(drag.card, before || null)
      drag.moved = true
    }
    // scroll along at the top/bottom edge – the page is longer than the screen
    function scrollTick() {
      if (!drag) return
      const edge = 70, y = drag.y, h = window.innerHeight
      const v = y < edge ? -(edge - y) / 2.5 : y > h - edge ? (y - (h - edge)) / 2.5 : 0     // up to ~28 px per frame: the long page is crossed quickly too
      if (v) { window.scrollBy(0, v); dropAt(drag.x, drag.y) }
      drag.raf = requestAnimationFrame(scrollTick)
    }
    // Start dragging a tile – with the mouse anywhere on the surface, with the finger only at the handle.
    const onDown = e => {
      if (!arranging) return
      const over = e.target.closest && e.target.closest('.mz-over')
      if (!over || e.target.closest('[data-mz]')) return
      if (e.pointerType === 'mouse' ? e.button !== 0 : !e.target.closest('.mz-grip')) return   // finger: only at the handle, otherwise scroll
      e.preventDefault()
      drag = { card: over.parentElement, id: e.pointerId, x: e.clientX, y: e.clientY, moved: false }
      drag.card.classList.add('mz-dragging')
      box.classList.add('mz-sorting')
      drag.raf = requestAnimationFrame(scrollTick)
    }
    // The tile follows the pointer and moves to the place it is over.
    const onMove = e => {
      if (!drag || e.pointerId !== drag.id) return
      e.preventDefault()
      drag.x = e.clientX; drag.y = e.clientY
      dropAt(e.clientX, e.clientY)
    }
    // End dragging; if the tile was moved, save the arrangement.
    const onUp = e => {
      if (!drag || (e && e.pointerId !== drag.id)) return
      cancelAnimationFrame(drag.raf)
      drag.card.classList.remove('mz-dragging')
      box.classList.remove('mz-sorting')
      if (drag.moved) save()
      drag = null
    }
    // Click while arranging: the arrows move the tile; the tile itself does not react meanwhile.
    const onClick = e => {
      const b = e.target.closest && e.target.closest('[data-mz]')
      if (b) { e.stopPropagation(); return move(b.closest('.mz-over').parentElement, b.dataset.mz) }
      if (arranging && e.target.closest && e.target.closest('.mz-over')) e.stopPropagation()     // die Kachel darunter bleibt still
    }
    // content loads later: redistribute in the first seconds (only without an own arrangement, never after an input)
    const settling = () => Date.now() <= settleUntil && !arranging && !drag && !saved[String(cols)]
    const ro = new ResizeObserver(() => {
      if (!settling()) return
      clearTimeout(roTimer)
      roTimer = setTimeout(() => { if (settling() && box.isConnected) layout() }, 150)
    })
    const stopSettle = () => { settleUntil = 0 }
    const onResize = HO.debounce(() => {
      if (!box.isConnected) return
      HO.cols.syncSeg(page, key, HO.cols.fit(box))
      if (wanted() !== cols) layout()
    }, 150)
    const watch = () => tiles().forEach(c => ro.observe(c))

    box.addEventListener('pointerdown', onDown)
    box.addEventListener('pointerdown', stopSettle, true)
    box.addEventListener('keydown', stopSettle, true)
    box.addEventListener('click', onClick, true)
    window.addEventListener('pointermove', onMove, { passive: false })
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    window.addEventListener('resize', onResize)
    layout()
    watch()

    return {
      get arranging() { return arranging },
      relayout() { layout(); watch() },
      // box was redrawn: read the order anew and distribute (the stored arrangement stays)
      refresh() { readOrder(); layout(); watch() },
      // new or replaced tiles (plugins.js): sort them in and let them load briefly
      adopt() { settleUntil = Date.now() + 2500; layout(); watch() },
      // Switch "Anordnen" on or off: surfaces for dragging over the tiles, state of the buttons in the page head.
      arrange(on) {
        arranging = on
        box.classList.toggle('arranging', on)
        overlays(on)
        const btn = page.querySelector('[data-act=arrange]'), auto = page.querySelector('[data-act=arrangeAuto]')
        if (btn) { btn.classList.toggle('primary', on); btn.setAttribute('aria-pressed', on); btn.innerHTML = `${HO.icon(on ? 'check' : 'move', 'sm')} ${on ? 'Fertig' : 'Anordnen'}` }
        if (auto) auto.hidden = !on
        if (on) HO.toast(window.matchMedia('(pointer: fine)').matches ? 'Kacheln ziehen oder mit den Pfeilen verschieben' : 'Am Griff ziehen oder mit den Pfeilen verschieben')
        if (!on) layout()                // the page may have collected new content
      },
      // forget the own arrangement of this number of columns and distribute automatically again
      reset() {
        delete saved[String(cols)]
        HO.api('settings/layout', { method: 'PUT', body: { page: key, cols, columns: null } }).catch(e => HO.toast(e.message, 'bad'))
        layout()
        HO.toast('Wieder automatisch verteilt')
      },
      // Tear down: unregister observers, timers and pointer events.
      destroy() {
        clearTimeout(roTimer); ro.disconnect(); heightRO.disconnect()      // a pending save still runs to its end
        if (drag) { cancelAnimationFrame(drag.raf); drag = null }
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        window.removeEventListener('resize', onResize)
      }
    }
  }


  HO.icon = (name, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || P.device}</svg>`

  /* ---------- Device helpers */
  HO.kindLabel = { thermostat: 'Heizkörper', contact: 'Fenster/Tür', motion: 'Bewegung', light: 'Licht', switch: 'Steckdose',
    sensor: 'Sensor', smoke: 'Rauchmelder', media: 'Lautsprecher', cover: 'Rollladen', presence: 'Anwesenheit', button: 'Taster',
    unknown: 'Gerät' }
  // Ambient light in lux as text ("12 lx", "1.200 lx")
  HO.lux = v => v == null ? '' : HO.num(Math.round(v)) + ' lx'
  /* Last press per button device (live message {"type": "button"}): {name, press, at}. A press is an event, not a
     state – the page shows it for a while and forgets it after a reload. */
  HO.PRESS_TEXT = { short: 'kurz', long: 'lang', double: 'doppelt' }
  HO.buttonSeen = {}
  HO.notePress = m => {
    if (!m || m.type !== 'button' || !m.id) return
    HO.buttonSeen[m.id] = { button: String(m.button || ''), name: String(m.name || m.button || ''), press: String(m.press || ''), at: Date.now() }
  }
  // Issues of a device: no contact, battery, fault, alarm
  HO.devIssues = d => {
    const acked = HO.ackedErrors[d.id]
    return [
      !d.online ? { k: 'offline', t: 'Kein Kontakt' + (d.status_detail ? ' – ' + d.status_detail : '') } : null,
      d.alarm ? { k: 'alarm', t: 'Alarm!' } : null,
      ...(d.errors || []).filter(t => !acked || !acked.has(t)).map(t => ({ k: 'error', t })),
      HO.lowBattery(d) ? { k: 'battery', t: 'Batterie schwach' + (d.battery != null ? ` (${d.battery} %)` : '') } : null
    ].filter(Boolean)
  }
  // Does a speaker play right now? One with a playback state says so itself; a further box of a room (no state of
  // its own) carries a track only while its room plays.
  HO.playingNow = d => d.kind === 'media' && (d.playing != null ? !!d.playing : !!d.media)
  HO.devState = d => {
    if (!d.online) return 'Kein Kontakt'
    if (d.alarm) return 'ALARM'
    // speaker with a playback state or a box that plays along; without both (only listed by a hub) the general display below applies
    if (d.kind === 'media' && (d.playing != null || d.media)) return HO.playingNow(d) ? 'Spielt' + (d.media && d.media.title ? ' · ' + d.media.title : '') : 'Pausiert'
    switch (d.kind) {
      case 'thermostat': return `${HO.temp(d.temperature)} · Soll ${HO.temp(d.target)}` + (d.valve != null ? (d.valve > 0 ? ` · Ventil ${d.valve} %` : ' · Ventil zu') : '')
      case 'sensor': {
        // temperature and humidity as far as reported; otherwise the first further reading (e.g. brightness of an MQTT sensor)
        const parts = [d.temperature != null ? HO.temp(d.temperature) : '', d.humidity != null ? Math.round(d.humidity) + ' %' : '',
          HO.lux(d.illuminance)].filter(Boolean)
        if (parts.length) return parts.join(' · ')
        const first = Object.entries(d.props || {})[0]
        return first ? `${first[0]}: ${first[1]}` : HO.temp(d.temperature)
      }
      case 'presence': return d.present == null ? 'unbekannt' : d.present ? 'Anwesend' : 'Abwesend'
      case 'contact': return d.open == null ? 'unbekannt' : d.open ? 'Offen' : 'Geschlossen'
      case 'motion': {
        const ago = Math.round((Date.now() - (HO.motionSeen[d.id] || 0)) / 1000)
        return (d.motion ? 'Bewegung' : ago < HO.MOTION_HOLD / 1000 ? `Bewegung vor ${Math.max(1, ago)} s` : 'Keine Bewegung') +
          (d.temperature != null ? ' · ' + HO.temp(d.temperature) : '') + (d.illuminance != null ? ' · ' + HO.lux(d.illuminance) : '')
      }
      case 'button': {
        const p = HO.buttonSeen[d.id], ago = p ? Math.round((Date.now() - p.at) / 1000) : null
        if (p && ago < 600) return `${p.name} ${HO.PRESS_TEXT[p.press] || ''} · vor ${ago < 60 ? Math.max(1, ago) + ' s' : Math.round(ago / 60) + ' min'}`
        const n = (d.buttons || []).length
        return n ? `${n} Taste${n > 1 ? 'n' : ''}` : 'Taster'
      }
      case 'light': return d.on == null ? 'unbekannt' : d.on ? ('An' + (d.brightness > 0 ? ' · ' + d.brightness + ' %' : '')) : 'Aus'
      case 'switch': return d.on == null ? 'unbekannt' : (d.on ? 'An' : 'Aus') + (d.power_w != null ? ' · ' + HO.num(d.power_w) + ' W' : '')
      case 'cover': return d.position == null ? 'unbekannt' : d.position <= 1 ? 'Offen' : d.position >= 99 ? 'Geschlossen' : `${d.position} % zu`
      default: return (d.values && d.values[0]) ? `${d.values[0].label}: ${d.values[0].state}` : (HO.kindLabel[d.kind] || 'Gerät')
    }
  }
  // motion: detectors often reset after a few seconds – let it glow on briefly
  HO.MOTION_HOLD = 60000
  HO.motionSeen = {}
  HO.noteMotion = devs => {
    let any = false
    for (const d of devs || []) if (d.motion) { HO.motionSeen[d.id] = Date.now(); any = true }
    return any
  }
  HO.devClass = d => !d.online ? 'off' : d.kind === 'cover' ? (d.position != null && d.position >= 99 ? '' : 'open')
    : (d.kind === 'light' || d.kind === 'switch') && d.on ? 'on'
    : HO.playingNow(d) ? 'on'
    : d.kind === 'presence' && d.present ? 'on'
    : d.kind === 'thermostat' && (d.valve != null ? d.valve > 0 : d.target != null && d.temperature != null && d.target > d.temperature + 0.3) ? 'heat'
      : d.kind === 'contact' && d.open ? 'open'
        : d.motion ? 'motion live'
          : Date.now() - (HO.motionSeen[d.id] || 0) < HO.MOTION_HOLD ? 'motion' : ''
  HO.lowBattery = d => d.low_battery === true || (d.battery != null && d.battery <= 20)

  /* ---------- Light: colour spaces (same formulas as light_model.py) */
  const lin = v => v > 0.04045 ? ((v + 0.055) / 1.055) ** 2.4 : v / 12.92
  const gam = v => v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
  const clamp255 = v => Math.max(0, Math.min(255, Math.round(v)))
  HO.hexToRgb = h => /^#[0-9a-f]{6}$/i.test(h || '') ? [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)) : [255, 255, 255]
  HO.rgbToHex = (r, g, b) => '#' + [r, g, b].map(v => clamp255(v).toString(16).padStart(2, '0')).join('')
  HO.hexToXy = h => {
    const [r, g, b] = HO.hexToRgb(h).map(c => lin(c / 255))
    const X = r * 0.664511 + g * 0.154324 + b * 0.162028, Y = r * 0.283881 + g * 0.668433 + b * 0.047685
    const Z = r * 0.000088 + g * 0.072310 + b * 0.986039, s = X + Y + Z
    return s > 0 ? [+(X / s).toFixed(4), +(Y / s).toFixed(4)] : [0.3127, 0.329]
  }
  HO.xyToRgb = (x, y) => {
    y = Math.max(y, 1e-4)
    const X = x / y, Y = 1, Z = (1 - x - y) / y
    let rgb = [X * 1.656492 - Y * 0.354851 - Z * 0.255038, -X * 0.707196 + Y * 1.655397 + Z * 0.036152,
      X * 0.051713 - Y * 0.121364 + Z * 1.011530].map(v => Math.max(0, v))
    const m = Math.max(...rgb) || 1
    return rgb.map(v => clamp255(gam(v / m) * 255))
  }
  HO.mirekToRgb = m => {
    const t = 1e6 / Math.max(100, m) / 100
    const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592
    const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492
    const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307
    return [r, g, b].map(clamp255)
  }
  HO.WARM = 370
  // Colours of a lamp that is on (gradient lamps: several); lamps without colour control = warm white
  HO.lightColors = d => {
    if (!d || d.kind !== 'light' || !d.on || !d.online) return []
    const L = d.light
    if (!L) return [HO.mirekToRgb(HO.WARM)]
    if (L.mode === 'gradient' && L.points && L.points.length > 1) return L.points.map(p => HO.xyToRgb(p[0], p[1]))
    if (L.mode === 'ct' && L.mirek) return [HO.mirekToRgb(L.mirek)]
    if (L.xy) return [HO.xyToRgb(L.xy[0], L.xy[1])]
    return [HO.mirekToRgb(HO.WARM)]
  }
  // Colour from hue (degrees), saturation and lightness (0–1) to [r, g, b] 0–255, and back.
  const hslRgb = (h, s, l) => {
    const a = s * Math.min(l, 1 - l), f = n => { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)) }
    return [f(0), f(8), f(4)].map(v => clamp255(v * 255))
  }
  // [r, g, b] 0–255 to [hue in degrees, saturation, lightness].
  const rgbHsl = c => {
    const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2
    let h = 0
    if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
    return [(h * 60 + 360) % 360, d ? d / (1 - Math.abs(2 * l - 1)) : 0, l]
  }
  /* Colours of a running Hue effect along a light strip (since 0.30.2, wish of the operator: "exakt mit mehreren Farben
     wie Hue"). The bridge reports only the effect and its colour; the strip itself plays it over its pixels, every
     effect with a pattern of its own. Simulated here: n colours from one end of the strip to the other at time t
     (seconds), p = phase of the lamp (two strips never run in step). base = [r, g, b] of the effect colour. Candle,
     fire, sparkle, glisten and opal take the colour of the effect; prism, underwater, cosmos, sunbeam and enchant have
     their own palette, as on the bridge. Brightness is laid into the colour (a dark pixel = a darker colour). */
  HO.effectColors = (effect, base, n, t, p) => {
    const b = rgbHsl(base || [255, 147, 41]), out = [], wave = (x, sp, k) => 0.5 + 0.5 * Math.sin(x * k - t * sp + p)
    const twinkle = (i, rate) => Math.pow(Math.max(0, Math.sin(t * rate + p + i * 2.399 + Math.sin(i * 7.1) * 3)), 18)
    for (let i = 0; i < n; i++) {
      const x = n > 1 ? i / (n - 1) : 0, q = p + i * 1.71
      let h = b[0], s = b[1], l = b[2], v = 1
      switch (effect) {
        case 'candle':   // warm, every pixel flickers on its own, a little redder when it dims
          v = 0.62 + 0.22 * Math.sin(t * 7.3 + q) * Math.sin(t * 2.9 + 2 * q) + 0.16 * Math.sin(t * 13.1 + 3 * q)
          h = b[0] - 6 * (1 - v)
          break
        case 'fire': {   // flames run along the strip: red at the root, amber at the tips, uneven tongues
          const f = wave(x, 2.2, 5.3) * 0.6 + wave(x, 3.7, 9.1) * 0.4
          h = b[0] - 14 + 26 * f; s = Math.max(s, 0.85); v = 0.45 + 0.55 * f
          break
        }
        case 'sparkle':  // the colour, dimmed, and single pixels flash white for a moment
          v = 0.55; l = Math.min(0.95, l + 0.45 * twinkle(i, 2.6)); s *= 1 - 0.8 * twinkle(i, 2.6); v += 0.45 * twinkle(i, 2.6)
          break
        case 'glisten':  // a soft shimmer wandering over the colour
          v = 0.7 + 0.3 * wave(x, 1.6, 7) * wave(x, 0.9, 3); l = Math.min(0.9, l + 0.18 * twinkle(i, 1.4))
          break
        case 'opal':     // pastel shades around the colour, drifting slowly
          h = b[0] + 50 * Math.sin(t * 0.35 + x * 3.2 + p); s = Math.min(s, 0.5); l = Math.max(l, 0.68)
          break
        case 'prism':    // the spectrum runs along the strip
          h = x * 220 + t * 30 + p * 20; s = 1; l = 0.5
          break
        case 'underwater':   // turquoise to deep blue in slow waves
          h = 180 + 40 * wave(x, 0.8, 4.2); s = 0.9; l = 0.42; v = 0.6 + 0.4 * wave(x, 1.3, 6.5)
          break
        case 'cosmos':   // violet and night blue, with a star now and then
          h = 235 + 55 * wave(x, 0.4, 2.6); s = 0.85; l = 0.38 + 0.4 * twinkle(i, 0.9)
          break
        case 'sunbeam':  // warm gold in broad, slow bands of light
          h = 38 + 10 * wave(x, 0.6, 3); s = 0.95; l = 0.5; v = 0.55 + 0.45 * wave(x, 0.9, 4.5)
          break
        case 'enchant':  // pink and lilac with sparks
          h = 290 + 45 * wave(x, 0.7, 3.4); s = 0.8; l = 0.55 + 0.3 * twinkle(i, 1.8)
          break
        default:         // an effect the bridge knows but ComfyOS not: the colour breathes along the strip
          v = 0.75 + 0.25 * wave(x, 0.8, 4)
      }
      const c = hslRgb(((h % 360) + 360) % 360, Math.max(0, Math.min(1, s)), Math.max(0, Math.min(1, l)))
      const k = Math.max(0.25, Math.min(1, v))
      out.push([c[0] * k, c[1] * k, c[2] * k].map(Math.round))
    }
    return out
  }
  const stops = (cols, alpha) => {
    if (cols.length === 1) cols = [cols[0], cols[0]]
    return cols.map((c, i) => `rgba(${c[0]},${c[1]},${c[2]},${(c[3] != null ? c[3] : 1) * alpha}) ${Math.round(i / (cols.length - 1) * 100)}%`).join(',')
  }
  // Background from the current light of several lamps (empty if everything is off)
  HO.lightGlow = (devs, alpha = 0.55) => {
    const cols = []
    for (const d of devs || []) {
      const f = 0.45 + 0.55 * Math.min(100, d.brightness || 100) / 100
      for (const c of HO.lightColors(d)) cols.push([...c, f])
    }
    return cols.length ? `linear-gradient(100deg,${stops(cols.slice(0, 10), alpha)})` : ''
  }
  HO.entryRgb = e => e.c ? HO.hexToRgb(e.c) : HO.mirekToRgb(e.ct || HO.WARM)
  HO.paletteGradient = (pal, alpha = 1, deg = 120) =>
    `linear-gradient(${deg}deg,${stops((pal || []).map(HO.entryRgb).concat((pal || []).length ? [] : [[60, 60, 70]]), alpha)})`

  /* ---------- Toasts */
  // Put into the clipboard. Without HTTPS and on older devices there is no Clipboard API –
  // then the old way through an invisible field. Returns true if it worked.
  HO.copy = async text => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); return true }
    } catch (e) { /* continue with the fallback */ }
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
      document.body.appendChild(ta)
      ta.select(); ta.setSelectionRange(0, text.length)
      const ok = document.execCommand && document.execCommand('copy')
      ta.remove()
      return !!ok
    } catch (e) { return false }
  }
  HO.toast = (msg, kind = 'ok', ms = 3600) => {
    const el = document.createElement('div')
    el.className = 'toast ' + kind
    el.innerHTML = HO.icon(kind === 'bad' ? 'warn' : kind === 'warn' ? 'info' : 'check') + `<div>${HO.esc(msg)}</div>`
    document.getElementById('toasts').appendChild(el)
    setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 300) }, ms)
  }
  /* ---------- Live stream (SSE): ONE connection for the whole app.
     Pages register with HO.live.on(fn) and get a function for unregistering. The connection stays across
     page changes (formerly every page opened its own – on the iPhone, which only allows a few connections per
     server, pages hung after quick switching). Without listeners it closes after a short time. */
  HO.live = (function () {
    const subs = []
    let es = null, closeTimer = null
    // Open the one live stream and distribute every message to all subscribers.
    function open() {
      if (es || !window.EventSource) return
      es = new EventSource('/api/stream')
      es.onmessage = ev => {
        let m
        try { m = JSON.parse(ev.data) } catch (e) { return }
        HO.notePress(m)
        subs.slice().forEach(fn => { try { fn(m) } catch (e) { } })
      }
    }
    // End a subscription; without subscribers the stream closes after 15 seconds (a page change keeps it).
    function off(fn) {
      const i = subs.indexOf(fn)
      if (i >= 0) subs.splice(i, 1)
      if (subs.length) return
      clearTimeout(closeTimer)
      closeTimer = setTimeout(() => { if (!subs.length && es) { es.close(); es = null } }, 15000)
    }
    return {
      on(fn) { clearTimeout(closeTimer); subs.push(fn); open(); return () => off(fn) },
      get open() { return !!es }
    }
  })()

  /* Trigger the heating sync (when opening home page and heating): never two requests at once – otherwise a sluggish
     other side would keep one more connection busy with every page change. */
  let syncKick = null
  HO.kickSync = () => {
    if (HO.demo && HO.demo.active) return Promise.resolve({})   // the demo has no heating sync – the server refuses the request (403)
    if (!syncKick) {
      syncKick = HO.api('sync', { method: 'POST' })
      const done = () => { syncKick = null }
      syncKick.then(done, done)
    }
    return syncKick
  }

  /* Name of the hub that manages the rooms (e.g. "Home Base") – null if rooms live in the floor plan alone */
  HO.roomHub = () => {
    const p = HO.state.overview && HO.state.overview.room_pending
    return (p && p.hub) || null
  }

  HO.syncToast = (sync, okText) => {
    if (!sync) return HO.toast(okText)
    if (sync.synced) HO.toast(okText + ' · in Magenta-App übernommen')
    else if (sync.reason && sync.reason.includes('nicht aktiv')) HO.toast(okText)
    else HO.toast(okText + ' · Abgleich: ' + (sync.reason || 'ausstehend'), 'warn', 5200)
  }

  /* ---------- Sheet (Bottom-Sheet am Handy, Dialog am Desktop) */
  HO.sheet = function ({ title, body = '', actions = [], wide = false, onMount }) {
    const bg = document.createElement('div')
    bg.className = 'sheet-bg'
    bg.innerHTML = `<div class="sheet ${wide ? 'wide' : ''}" role="dialog" aria-modal="true">
      <div class="sheet-head"><h3>${HO.esc(title)}</h3><button class="btn-icon plain" data-close aria-label="Schließen">${HO.icon('x')}</button></div>
      <div class="sheet-body">${body}</div>
      ${actions.length ? `<div class="sheet-foot">${actions.map((a, i) => `<button class="btn ${a.kind || ''}" data-a="${i}">${a.icon ? HO.icon(a.icon, 'sm') : ''}${HO.esc(a.label)}</button>`).join('')}</div>` : ''}
    </div>`
    const api = {
      el: bg, body: () => bg.querySelector('.sheet-body'),
      close: () => { document.removeEventListener('keydown', onKey); bg.remove() }
    }
    const onKey = e => { if (e.key === 'Escape') api.close() }
    document.addEventListener('keydown', onKey)
    bg.addEventListener('click', async e => {
      if (e.target === bg || e.target.closest('[data-close]')) return api.close()
      const b = e.target.closest('[data-a]')
      if (b) {
        const a = actions[+b.dataset.a]
        if (!a.onClick) return api.close()
        b.disabled = true
        try { const keep = await a.onClick(api); if (keep !== true) api.close() }
        catch (err) { HO.toast(err.message, 'bad', 5000) }
        finally { b.disabled = false }
      }
    })
    document.body.appendChild(bg)
    if (onMount) onMount(api)
    const first = bg.querySelector('input:not([type=hidden])')
    if (first && window.matchMedia('(min-width: 700px)').matches) first.focus()
    return api
  }
  HO.confirm = (text, { title = 'Bitte bestätigen', ok = 'OK', danger = false } = {}) => new Promise(res => {
    const s = HO.sheet({ title, body: `<p>${HO.esc(text)}</p>`, actions: [
      { label: 'Abbrechen', kind: 'ghost', onClick: () => res(false) },
      { label: ok, kind: danger ? 'danger' : 'primary', onClick: () => res(true) }] })
    s.el.addEventListener('click', e => { if (e.target === s.el || e.target.closest('[data-close]')) res(false) })
  })
  HO.prompt = (title, value = '', { label = 'Name', ok = 'Speichern' } = {}) => new Promise(res => {
    HO.sheet({ title, body: `<label class="field"><span>${HO.esc(label)}</span><input type="text" id="promptVal" value="${HO.esc(value)}" maxlength="40"></label>`,
      actions: [{ label: 'Abbrechen', kind: 'ghost', onClick: () => res(null) },
        { label: ok, kind: 'primary', onClick: s => res(s.el.querySelector('#promptVal').value.trim() || null) }],
      onMount: s => s.el.querySelector('#promptVal').addEventListener('keydown', e => {
        if (e.key === 'Enter') { res(e.target.value.trim() || null); s.close() } }) })
  })
  HO.menu = (title, items) => {
    const s = HO.sheet({ title, body: `<div class="menu-list">${items.map((it, i) =>
      `<button data-m="${i}" class="${it.danger ? 'danger' : ''}">${HO.icon(it.icon || 'chevron')}${HO.esc(it.label)}</button>`).join('')}</div>` })
    s.el.querySelector('.menu-list').addEventListener('click', e => {
      const b = e.target.closest('[data-m]'); if (!b) return
      s.close(); items[+b.dataset.m].onClick()
    })
  }

  /* ---------- Akzentfarbe (Pro) */
  HO.applyAccent = function (hex) {
    var r = document.documentElement.style
    if (!hex) { r.removeProperty('--accent'); r.removeProperty('--accent-2'); r.removeProperty('--accent-text'); r.removeProperty('--accent-soft'); r.removeProperty('--accent-line'); r.removeProperty('--on-accent'); return }
    var m = hex.match(/^#?([0-9a-f]{6})$/i); if (!m) return
    var c = parseInt(m[1], 16), R = (c >> 16) & 255, G = (c >> 8) & 255, B = c & 255
    // Colour from red/green/blue (0–255) to hue (degrees), saturation and lightness (per cent).
    function hsl(R, G, B) {
      var r = R / 255, g = G / 255, b = B / 255, max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
      var h = 0, s = 0, l = (max + min) / 2
      if (d > 0) { s = d / (1 - Math.abs(2 * l - 1)); h = max === r ? ((g - b) / d + 6) % 6 * 60 : max === g ? ((b - r) / d + 2) * 60 : ((r - g) / d + 4) * 60 }
      return [Math.round(h), Math.round(s * 100), Math.round(l * 100)]
    }
    var v = hsl(R, G, B), h = v[0], s = v[1], l = v[2]
    r.setProperty('--accent', hex)
    r.setProperty('--accent-2', 'hsl(' + h + ',' + s + '%,' + Math.max(l - 8, 5) + '%)')
    r.setProperty('--accent-text', 'hsl(' + h + ',' + Math.min(s + 15, 100) + '%,' + Math.min(l + 30, 85) + '%)')
    r.setProperty('--accent-soft', 'hsla(' + h + ',' + s + '%,' + l + '%,.15)')
    r.setProperty('--accent-line', 'hsla(' + h + ',' + s + '%,' + l + '%,.55)')
    var lum = (0.299 * R + 0.587 * G + 0.114 * B) / 255
    r.setProperty('--on-accent', lum > 0.5 ? '#000' : '#fff')
  }

  /* ---------- Logo (inline SVG, folgt --accent / --accent-2) */
  HO.logo = (cls) => `<svg class="logo ${cls || ''}" viewBox="0 0 512 512" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent-2)"/></linearGradient></defs><rect width="512" height="512" rx="116" fill="url(#lg)"/><path d="M256 112 L398 230 L398 380 L114 380 L114 230 Z" fill="#fff" stroke="#fff" stroke-width="34" stroke-linejoin="round"/><path d="M215 337 A58 58 0 1 1 297 337" fill="none" stroke="url(#lg)" stroke-width="22" stroke-linecap="round"/><circle cx="293.3" cy="251.6" r="8" fill="#fff"/></svg>`

  /* ---------- Sync status as a short line */
  HO.syncLine = st => {
    // without a sync ComfyOS runs the active routine itself (heating_scheduler.py, since 0.23.1)
    if (!st || !st.configured) return `<span class="statusdot ok"></span> ComfyOS führt den Heizplan aus · Magenta-Sync aus`
    if (st.running) return `<span class="statusdot spin"></span> Gleiche mit Magenta-App ab …`
    if (st.ok === false) return `<span class="statusdot bad"></span> Abgleich fehlgeschlagen: ${HO.esc(st.last_error || '')}`
    if (st.pending) return `<span class="statusdot warn"></span> ${st.pending} Änderung(en) warten auf Abgleich`
    return `<span class="statusdot ok"></span> Mit Magenta-App abgeglichen · ${HO.relTime(st.last_sync)}`
  }
})(window.HO)
