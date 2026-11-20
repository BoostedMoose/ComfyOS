/* ComfyOS – the widgets of the widget view (since 0.24): the catalogue for widgets.js

   Every widget type is registered with HO.widgets.register (contract: head of widgets.js). The list of type names is
   the same as widgets_store.TYPES in the backend (tests/core/test_widgets.py compares both).

   Rules for a widget:
     * It finds its devices by what they can do (`caps`, kind of the device row) – never by plugin. A newly set up
       plugin therefore shows up in the fitting widgets by itself.
     * Controls carry the data attributes of the classic home page (data-cmd, data-dev, data-home, data-lights,
       data-room-lights, data-house, data-sc-apply, data-media-act, data-act …): its handlers switch – through the same
       routes, with device lock and event log. Only what the home page does not know is handled by the board
       (data-wg-play).
     * Foreign text (names of devices, rooms, scenes, flows, event messages, notes) always goes through HO.esc.
     * A setting that shows a Pro feature is marked `pro`; without the plan the widget keeps the setting and shows a
       short hint instead of the content – nothing is removed.

   HO.widgets.starter(overview)   suggestion for the first page: [type, width, height, settings, properties] per widget,
                                  chosen by what the home has. */
(function (HO) {
  'use strict'
  const W = HO.widgets
  const devices = ctx => (ctx.ov && ctx.ov.devices) || []
  const roomList = ctx => ((ctx.ov && ctx.ov.rooms) || []).filter(r => r.room_id).sort((a, b) => HO.byName(a.name, b.name))
  const roomName = (ctx, id) => { const r = roomList(ctx).find(x => x.room_id === id); return r ? r.name : '' }
  const roomOptions = ctx => roomList(ctx).map(r => [r.room_id, r.name])
  const deviceOptions = filter => ctx => devices(ctx).filter(filter).sort((a, b) => HO.byName(a.name, b.name))
    .map(d => [d.id, d.name + (d.room && d.room !== 'Nicht zugeordnet' ? ' · ' + d.room : '')])
  const find = (ctx, id) => devices(ctx).find(d => d.id === id) || null
  const none = (icon, text) => `<div class="wg-none">${HO.icon(icon)}<span>${HO.esc(text)}</span></div>`
  const proHint = text => `<div class="wg-pro">${HO.icon('star', 'sm')}<span>${HO.esc(text)} gehört zu ComfyOS Pro.</span></div>`
  const pad2 = n => String(n).padStart(2, '0')
  const lampTint = d => { const c = HO.lightColors(d); return c.length ? `style="--wg-lamp:rgb(${c[0].join(',')})"` : '' }

  /* ---------- Small graphics */
  const RING = 2 * Math.PI * 42
  // Gauge as three quarters of a circle: `frac` 0…1 is filled, the text stands in the middle.
  function gaugeSVG(frac, big, small) {
    const f = Math.max(0, Math.min(1, isFinite(frac) ? frac : 0))
    return `<div class="wg-gauge"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle class="wg-g-track" cx="50" cy="50" r="42" stroke-dasharray="${(RING * 0.75).toFixed(1)} ${RING.toFixed(1)}" transform="rotate(135 50 50)"/>
      <circle class="wg-g-fill" cx="50" cy="50" r="42" stroke-dasharray="${(RING * 0.75 * f).toFixed(1)} ${RING.toFixed(1)}" transform="rotate(135 50 50)"/></svg>
      <div class="wg-g-text"><b>${big}</b>${small ? `<span>${small}</span>` : ''}</div></div>`
  }
  // Closed ring: share `frac` of the whole, text in the middle.
  function ringSVG(frac, big, small, cls) {
    const f = Math.max(0, Math.min(1, isFinite(frac) ? frac : 0))
    return `<div class="wg-gauge ring ${cls || ''}"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle class="wg-g-track" cx="50" cy="50" r="42"/>
      <circle class="wg-g-fill" cx="50" cy="50" r="42" stroke-dasharray="${(RING * f).toFixed(1)} ${RING.toFixed(1)}" transform="rotate(-90 50 50)"/></svg>
      <div class="wg-g-text"><b>${big}</b>${small ? `<span>${small}</span>` : ''}</div></div>`
  }
  // Small curve without axes over a list of numbers (history of the power consumption).
  function sparkSVG(values) {
    const list = values.filter(v => v != null && isFinite(v))
    if (list.length < 2) return ''
    const max = Math.max.apply(null, list) || 1, step = 100 / (list.length - 1)
    const pts = list.map((v, i) => `${(i * step).toFixed(2)},${(38 - v / max * 34).toFixed(2)}`)
    return `<svg class="wg-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      <polygon class="wg-spark-area" points="0,40 ${pts.join(' ')} 100,40"/><polyline class="wg-spark-line" points="${pts.join(' ')}"/></svg>`
  }
  // Row of a list inside a widget: icon, name, state on the right; tapping opens the device.
  const deviceRow = (d, state, cls) => `<button type="button" class="wg-row ${cls || ''}" data-dev="${HO.esc(d.id)}">
    <span class="dev-ic ${HO.devClass(d)}">${HO.icon(d.kind, 'sm')}</span><span class="wg-row-name">${HO.esc(d.name)}</span>
    <span class="wg-row-state">${HO.esc(state)}</span></button>`

  /* ============================================================ Overview */

  /* ---------- Clock */
  const greeting = () => { const h = new Date().getHours(); return h < 11 ? 'Guten Morgen' : h < 18 ? 'Guten Tag' : 'Guten Abend' }
  // Clock face with hour marks and hands.
  function analogSVG(now, seconds) {
    const h = now.getHours() % 12 + now.getMinutes() / 60, m = now.getMinutes() + now.getSeconds() / 60, s = now.getSeconds()
    const marks = Array.from({ length: 12 }, (x, i) => `<line class="wg-ck-mark ${i % 3 ? '' : 'big'}" x1="50" y1="${i % 3 ? 7 : 6}" x2="50" y2="${i % 3 ? 11 : 14}" transform="rotate(${i * 30} 50 50)"/>`).join('')
    return `<svg class="wg-ck" viewBox="0 0 100 100" role="img" aria-label="${pad2(now.getHours())}:${pad2(now.getMinutes())} Uhr">
      <circle class="wg-ck-face" cx="50" cy="50" r="47"/>${marks}
      <line class="wg-ck-h" x1="50" y1="52" x2="50" y2="26" transform="rotate(${(h * 30).toFixed(1)} 50 50)"/>
      <line class="wg-ck-m" x1="50" y1="54" x2="50" y2="13" transform="rotate(${(m * 6).toFixed(1)} 50 50)"/>
      ${seconds ? `<line class="wg-ck-s" x1="50" y1="58" x2="50" y2="11" transform="rotate(${s * 6} 50 50)"/>` : ''}<circle class="wg-ck-dot" cx="50" cy="50" r="2.6"/></svg>`
  }
  W.register('clock', {
    title: 'Uhr', icon: 'clock', group: 'overview', desc: 'Uhrzeit und Datum, digital oder mit Zeigern', size: [3, 2], min: [2, 1], head: false, align: 'center',
    fields: [
      { key: 'look', label: 'Zifferblatt', type: 'select', def: 'digital', options: [['digital', 'Digital'], ['analog', 'Zeiger'], ['both', 'Zeiger und Datum']] },
      { key: 'seconds', label: 'Sekunden anzeigen', type: 'bool', def: false },
      { key: 'date', label: 'Datum anzeigen', type: 'bool', def: true },
      { key: 'greeting', label: 'Begrüßung anzeigen', type: 'bool', def: false }
    ],
    // The clock ticks by itself: once a second with seconds or hands, otherwise a few times a minute.
    mount(body, widget) {
      let opts = widget.opts, timer = null
      // Write the current time in the chosen look (only what changed is written).
      const draw = () => {
        const now = new Date(), look = opts.look || 'digital', seconds = !!opts.seconds
        const date = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' })
        // a fine line below shows how far the day has come (off together with the date)
        const day = ((now.getHours() * 60 + now.getMinutes()) / 14.4).toFixed(1)
        const sub = `${opts.greeting ? `<div class="eyebrow">${greeting()}</div>` : ''}${opts.date !== false ? `<div class="wg-sub">${date}</div>
          <div class="wg-daybar" style="--day:${day}%" title="So weit ist der Tag"><i style="width:${day}%"></i></div>` : ''}`
        const digital = `<div class="wg-big wg-time">${pad2(now.getHours())}<i>:</i>${pad2(now.getMinutes())}${seconds ? `<small>${pad2(now.getSeconds())}</small>` : ''}</div>`
        HO.setHTML(body, look === 'digital' ? `<div class="wg-clock">${digital}${sub}</div>`
          : `<div class="wg-clock analog ${look === 'both' ? 'both' : ''}">${analogSVG(now, seconds)}${look === 'both' ? `<div>${digital}${sub}</div>` : ''}</div>`)
      }
      const arm = () => { clearInterval(timer); timer = setInterval(draw, opts.seconds ? 1000 : 10000) }
      draw()
      arm()
      return { update(next) { if (!!next.opts.seconds !== !!opts.seconds) { opts = next.opts; arm() } opts = next.opts; draw() }, resize: draw, destroy() { clearInterval(timer) } }
    }
  })

  /* ---------- Floor plan */
  W.register('floorplan', {
    title: 'Grundriss', icon: 'map', group: 'overview', desc: 'Dein Grundriss mit Geräten und Temperaturen – in jeder Größe', size: [8, 6], min: [3, 3], narrow: [4, 5],
    fields: [
      { key: 'interact', label: 'Verschieben und Zoomen erlauben', type: 'bool', def: false, hint: 'Aus: Der Grundriss steht still, die Seite scrollt darüber hinweg. Antippen geht immer.' },
      // several floors (since 0.25): one widget that changes the floor with buttons in its frame, or one widget per floor
      { key: 'floor', label: 'Stockwerk', type: 'select', def: '', hint: 'Umschaltbar: Knöpfe im Widget wechseln das Stockwerk. Oder ein festes Stockwerk wählen – für die ganze Übersicht je Stockwerk ein Widget anlegen.',
        options: ctx => [['', 'Umschaltbar']].concat(HO.floorplan.floorsOf(ctx && ctx.ov && ctx.ov.floorplan).map(f => [f.id, f.label])) },
      { key: 'heat', label: 'Wärme-Heatmap', type: 'bool', def: false, pro: 'floorplan_heatmap' },
      { key: 'fx', label: 'Wettereffekte', type: 'bool', def: false, pro: 'floorplan_weather' }
    ],
    // The floor plan lives in the widget with a camera of its own; the home page hands in data and callbacks.
    mount(body, widget, ctx) {
      const plan = ctx.ov && ctx.ov.floorplan
      if (!plan || !HO.floorplan.hasWalls(plan)) return null
      const fxOn = o => !!o.fx && HO.can('floorplan_weather')
      body.innerHTML = '<div class="wg-plan"></div>'
      // a fixed floor shows no buttons; without one the widget starts on the ground floor and can be switched
      let fixed = widget.opts.floor || ''
      const fp = ctx.home.mountPlan(body.firstChild, { still: !widget.opts.interact, heat: !!widget.opts.heat && HO.can('floorplan_heatmap'),
        weather: fxOn(widget.opts) ? ctx.home.fx() : null, floor: fixed, floorNav: !fixed })
      return {
        // Fresh data and settings: the plan follows, a newly chosen fixed floor is shown.
        update(next, c) {
          fp.update({ devices: c.ov.devices, rooms: c.ov.rooms, plan: c.ov.floorplan, heatRange: c.ov.heat_range })
          if ((next.opts.floor || '') !== fixed) { fixed = next.opts.floor || ''; if (fixed) fp.setFloor(fixed) }
          fp.setWeather(fxOn(next.opts) ? c.home.fx() : null)
        },
        resize() { fp.resize() },
        destroy() { fp.destroy() }
      }
    },
    render: () => none('map', 'Noch kein Grundriss – in der klassischen Ansicht zeichnen')
  })

  /* ---------- Room */
  // Light switch of a room (the same attributes as on the classic home page).
  function roomSwitch(ctx, rid, name) {
    const lights = devices(ctx).filter(d => d.room_id === rid && d.kind === 'light' && HO.canControl(d))
    if (!lights.length) return ''
    const on = lights.some(d => d.on)
    return `<button class="room-switch ${on ? 'on' : ''}" role="switch" aria-checked="${on}" data-room-lights="${HO.esc(JSON.stringify([rid]))}" data-on="${on ? 0 : 1}"
      data-name="${HO.esc(name)}" title="${on ? 'Alle Lampen im Raum ausschalten' : 'Alle Lampen im Raum einschalten'}" aria-label="Licht ${HO.esc(name)}"></button>`
  }
  W.register('room', {
    title: 'Raum', icon: 'door', group: 'overview', desc: 'Ein Raum auf einen Blick: Temperatur, Licht, Geräte', size: [4, 3], min: [2, 2], setup: true,
    fields: [
      { key: 'room', label: 'Raum', type: 'select', def: '', options: ctx => [['', '— wählen —']].concat(roomOptions(ctx)) },
      { key: 'devices', label: 'Geräte auflisten', type: 'bool', def: true },
      { key: 'scenes', label: 'Szenen anbieten', type: 'bool', def: true }
    ],
    name: (w, ctx) => roomName(ctx, w.opts.room),
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const rid = w.opts.room, info = roomList(ctx).find(r => r.room_id === rid)
      if (!info) return none('door', 'Raum wählen')
      const list = devices(ctx).filter(d => d.room_id === rid).sort((a, b) => HO.byName(a.name, b.name))
      const lamps = list.filter(d => d.kind === 'light'), on = lamps.filter(d => d.on && d.online)
      const glow = HO.lightGlow(lamps, 0.7), sc = lamps.length ? HO.lights.activeScene('room:' + rid) : null
      // The name of the room is the title of the widget – it is not repeated here. The big figure is the temperature;
      // a room without one shows its light instead ("2/3"), a room without lamps the number of its devices.
      const hasTemp = info.temperature != null
      const lightText = lamps.length ? (on.length ? `${on.length} von ${lamps.length} an` : 'Licht aus') : ''
      const big = hasTemp ? HO.temp(info.temperature, true) : lamps.length ? `${on.length}<small>/${lamps.length}</small>` : `${list.length}`
      const facts = [hasTemp && info.target != null ? 'Soll ' + HO.temp(info.target) : '',
        hasTemp ? lightText : lamps.length ? (on.length ? 'Lampen an' : 'Licht aus') : (list.length === 1 ? 'Gerät' : 'Geräte'),
        sc ? sc.name : '', info.open ? `${info.open} offen` : ''].filter(Boolean)
      const headless = !w.head || w.style === 'bare'
      return `<div class="wg-room ${on.length ? 'lit' : ''}">${glow ? `<div class="wg-glow" style="background:${glow}"></div>` : ''}
        <div class="wg-room-top" ${lamps.length ? `data-room-head="${HO.esc(rid)}" role="button" tabindex="0" title="Lichteinstellung"` : ''}>
          <div>${headless ? `<div class="eyebrow">${HO.esc(info.name)}</div>` : ''}<div class="wg-big">${big}</div>
            <div class="wg-sub">${HO.esc(facts.join(' · ') || ' ')}</div></div>
          ${roomSwitch(ctx, rid, info.name)}</div>
        ${ctx.opt('scenes') && lamps.length && ctx.hz !== 'xs' ? HO.lights.sceneRowHTML({ room: rid }) : ''}
        ${ctx.opt('devices') && ctx.hz !== 'xs' ? `<div class="wg-list">${list.map(d => deviceRow(d, HO.devState(d), d.on || HO.playingNow(d) ? 'on' : '')).join('') ||
          '<span class="small muted">Keine Geräte in diesem Raum.</span>'}</div>` : ''}</div>`
    }
  })

  /* ---------- Devices online and problems */
  W.register('status', {
    title: 'Geräte und Probleme', icon: 'shield', group: 'overview', desc: 'Wie viele Geräte erreichbar sind – und was Aufmerksamkeit braucht', size: [3, 2], min: [2, 2],
    fields: [{ key: 'list', label: 'Geräte mit Problemen auflisten', type: 'bool', def: true }],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const list = devices(ctx), online = list.filter(d => d.online).length, issues = ctx.home.issues(ctx.ov)
      const bad = list.filter(d => HO.devIssues(d).length)
      const ring = ringSVG(list.length ? online / list.length : 0, `${online}<small>/${list.length}</small>`, 'online', issues.length ? 'warn' : 'ok')
      const small = ctx.sz === 'xs' || ctx.sz === 's'
      const rows = ctx.opt('list') && bad.length && (ctx.hz === 'm' || ctx.hz === 'l') ? `<div class="wg-list">${bad.map(d => deviceRow(d, HO.devIssues(d)[0].t, 'warn')).join('')}</div>` : ''
      return `<div class="wg-status ${rows ? 'with-list' : ''}"><div class="wg-status-top">${ring}
        <div class="wg-status-text"><b class="${issues.length ? 'warn' : 'ok'}">${HO.icon(issues.length ? 'warn' : 'check', 'sm')}${issues.length ? (small ? 'Prüfen' : 'Handlungsbedarf') : 'Alles OK'}</b>
          <span class="wg-sub">${HO.esc(issues.join(' · ') || 'Alle Geräte melden sich.')}</span></div></div>${rows}</div>`
    }
  })

  /* ---------- Windows and doors */
  W.register('openings', {
    title: 'Fenster und Türen', icon: 'contact', group: 'overview', desc: 'Was offen steht – Kontakte und auf Wunsch Rollläden', size: [3, 3], min: [2, 2], needs: ['open'],
    fields: [
      { key: 'only_open', label: 'Nur offene auflisten', type: 'bool', def: false },
      { key: 'covers', label: 'Rollläden mit anzeigen', type: 'bool', def: false }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const contacts = devices(ctx).filter(d => d.kind === 'contact'), open = contacts.filter(d => d.online && d.open)
      const covers = ctx.opt('covers') ? devices(ctx).filter(d => d.kind === 'cover') : []
      if (!contacts.length && !covers.length) return none('contact', 'Keine Fenster- oder Türkontakte')
      const shown = (ctx.opt('only_open') ? open : contacts.slice().sort((a, b) => (b.open ? 1 : 0) - (a.open ? 1 : 0) || HO.byName(a.name, b.name))).concat(covers)
      return `<div class="wg-open ${open.length ? 'warn' : ''}"><div class="wg-kpi"><div class="wg-big">${open.length}</div>
        <div class="wg-sub">${open.length === 1 ? 'steht offen' : 'stehen offen'}<br>von ${contacts.length} Kontakt${contacts.length === 1 ? '' : 'en'}</div></div>
        ${ctx.hz !== 'xs' ? `<div class="wg-list">${shown.map(d => deviceRow(d, HO.devState(d), d.open ? 'warn' : '')).join('') ||
          '<span class="small muted">Alles geschlossen.</span>'}</div>` : ''}</div>`
    }
  })

  /* ---------- Presence of people */
  W.register('people', {
    title: 'Anwesenheit', icon: 'presence', group: 'overview', desc: 'Wer zu Hause ist (Anwesenheits-Geräte aller Plugins)', size: [3, 2], min: [2, 1], needs: ['present'],
    fields: [{ key: 'devices', label: 'Nur diese anzeigen', type: 'multi', def: [], options: deviceOptions(d => d.kind === 'presence'), hint: 'Nichts gewählt = alle.' }],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const chosen = ctx.opt('devices') || []
      const list = devices(ctx).filter(d => d.kind === 'presence' && (!chosen.length || chosen.indexOf(d.id) >= 0)).sort((a, b) => HO.byName(a.name, b.name))
      if (!list.length) return none('presence', 'Keine Anwesenheits-Geräte')
      return `<div class="wg-people">${list.map(d => `<button type="button" class="wg-person ${d.present ? 'here' : ''} ${d.online ? '' : 'off'}" data-dev="${HO.esc(d.id)}">
        <span class="wg-avatar">${HO.esc(String(d.name || '?').trim().charAt(0).toUpperCase())}</span><b>${HO.esc(d.name)}</b>
        <span class="wg-sub">${d.present == null ? 'unbekannt' : d.present ? 'zu Hause' : 'unterwegs'}</span></button>`).join('')}</div>`
    }
  })

  /* ---------- Events */
  const EVENT_ICONS = { heating: 'flame', window: 'contact', device: 'device', battery: 'battery', command: 'power', home: 'home', magenta: 'cloud',
    connection: 'globe', update: 'download', system: 'settings', room: 'map', hue: 'light', flow: 'workflow', security: 'shield', scene: 'palette', license: 'tag' }
  const EVENT_KINDS = [['', 'Alle'], ['command', 'Befehle'], ['device', 'Geräte'], ['window', 'Fenster'], ['heating', 'Heizung'], ['flow', 'Flow'],
    ['scene', 'Szenen'], ['home', 'Zuhause'], ['connection', 'Verbindung'], ['security', 'Sicherheit'], ['system', 'System']]
  W.register('events', {
    title: 'Ereignisse', icon: 'activity', group: 'overview', desc: 'Was zuletzt im Zuhause passiert ist', size: [4, 3], min: [2, 2],
    fields: [
      { key: 'count', label: 'Anzahl', type: 'range', def: 8, min: 3, max: 30, step: 1 },
      { key: 'kind', label: 'Art', type: 'select', def: '', options: EVENT_KINDS }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const all = ctx.data('events', () => HO.api('events?limit=120'), 20000)
      if (all === undefined) return '<div class="spinner"></div>'
      const kind = ctx.opt('kind'), list = (all || []).filter(e => !kind || e.type === kind).slice(0, ctx.opt('count'))
      if (!list.length) return none('activity', 'Noch keine Ereignisse')
      return `<div class="wg-list wg-events">${list.map(e => `<div class="wg-row plain"><span class="dev-ic">${HO.icon(EVENT_ICONS[e.type] || 'info', 'sm')}</span>
        <span class="wg-row-name">${HO.esc(e.message)}</span><span class="wg-row-state">${HO.esc(HO.relTime(e.ts))}</span></div>`).join('')}</div>
        <div class="wg-foot"><button class="link-btn" data-goto="events">Alle Ereignisse</button></div>`
    }
  })

  /* ---------- Connections */
  const CONN = { live: ['ok', 'Online'], ok: ['ok', 'verbunden'], bad: ['bad', 'getrennt'], error: ['bad', 'Fehler'], connecting: ['spin', 'verbindet'] }
  W.register('connections', {
    title: 'Verbindungen', icon: 'link', group: 'overview', desc: 'Zustand der eingerichteten Plugins', size: [3, 2], min: [2, 2],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const items = (ctx.ov && ctx.ov.status && ctx.ov.status.items) || []
      if (!items.length) return none('link', 'Noch kein Plugin eingerichtet')
      return `<div class="wg-list">${items.map(i => { const c = CONN[i.state] || ['', i.state]
        return `<button type="button" class="wg-row" data-goto="settings?tab=plugins"><span class="statusdot ${c[0]}"></span>
          <span class="wg-row-name">${HO.esc(i.name)}</span><span class="wg-row-state">${HO.esc(c[1])}</span></button>` }).join('')}</div>`
    }
  })

  /* ============================================================ Data */

  /* ---------- Weather */
  // Mood of a weather icon for the colour of the widget: sun, night, rain, snow, storm or cloud.
  const weatherMood = icon => /moon|night/.test(icon) ? 'night' : /sun|partly/.test(icon) ? 'sun' : /rain|drizzle/.test(icon) ? 'rain'
    : /snow/.test(icon) ? 'snow' : /storm/.test(icon) ? 'storm' : 'cloud'
  W.register('weather', {
    title: 'Wetter', icon: 'cloud', group: 'data', desc: 'Wetter am Ort, auf Wunsch mit Stundenprognose und Wind', size: [3, 2], min: [2, 1],
    fields: [
      { key: 'details', label: 'Gefühlt, Höchst- und Tiefstwert, Luftfeuchte', type: 'bool', def: true },
      { key: 'forecast', label: 'Stundenprognose und Wind', type: 'bool', def: false, pro: 'floorplan_weather' },
      { key: 'warn', label: 'Wetterwarnungen anzeigen', type: 'bool', def: true }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const wx = ctx.wx
      if (!wx) return none('cloud', (ctx.ov && ctx.ov.floorplan && ctx.ov.floorplan.address) ? 'Wetter wird geladen …' : 'Adresse im Grundriss eintragen (Bearbeiten)')
      const round = v => v == null ? '–' : Math.round(v) + '°', warnings = ctx.opt('warn') ? ctx.home.warnings() : []
      const facts = [wx.apparent != null ? 'gefühlt ' + round(wx.apparent) : '', wx.min != null && wx.max != null ? `${round(wx.min)} / ${round(wx.max)}` : '',
        wx.humidity != null ? Math.round(wx.humidity) + ' % Feuchte' : ''].filter(Boolean)
      let more = ''
      if (ctx.opt('forecast') && ctx.hz !== 'xs') {
        if (!HO.can('floorplan_weather')) more = proHint('Stundenprognose und Wind')
        else {
          const wind = wx.wind_report, hours = (wx.today || []).slice(0, Math.max(3, Math.floor(ctx.w / 52)))
          more = `${wind ? `<div class="wg-sub wg-wind">${HO.icon('wx-cloud', 'sm')}${wind.speed} km/h${wind.compass ? ' aus ' + HO.esc(wind.compass) : ''} · ${HO.esc(wind.label)}</div>` : ''}
            ${hours.length ? `<div class="wg-hours">${hours.map(h => `<div class="wg-hour"><span>${HO.esc(String(h.time).slice(0, 2))}</span>${HO.icon('wx-' + h.icon, 'sm')}<b>${round(h.temperature)}</b>
              <i title="Regenwahrscheinlichkeit">${h.pop == null ? '' : Math.round(h.pop) + ' %'}</i></div>`).join('')}</div>` : ''}`
        }
      }
      return `<div class="wg-weather wx-m-${weatherMood(wx.icon)}" data-act="wx" role="button" tabindex="0" title="Wetter im Detail"><div class="wg-aura"></div>
        <div class="wg-wx-now"><span class="wg-wx-ic">${HO.icon('wx-' + wx.icon)}</span><div><div class="wg-big">${round(wx.temperature)}</div>
          <div class="wg-sub">${HO.esc(wx.label)} · ${HO.esc(wx.place)}</div></div></div>
        ${ctx.opt('details') && facts.length && ctx.hz !== 'xs' ? `<div class="wg-sub wg-facts">${HO.esc(facts.join(' · '))}</div>` : ''}${more}
        ${warnings.length ? `<button class="status-pill warn wg-wx-warn" data-act="warn">${HO.icon('warn', 'sm')}<b>${warnings.length === 1 ? HO.esc(warnings[0].headline) : warnings.length + ' Wetterwarnungen'}</b></button>` : ''}</div>`
    }
  })

  /* ---------- Reading of a device */
  const pct = v => Math.round(v) + ' %'
  const READINGS = {
    temperature: ['Temperatur', v => HO.temp(v, true), 5, 30], target: ['Solltemperatur', v => HO.temp(v, true), 5, 30],
    humidity: ['Luftfeuchte', pct, 0, 100], illuminance: ['Umgebungslicht', v => HO.lux(v), 0, 1000],
    power_w: ['Leistung', v => HO.num(v) + ' W', 0, 2000], battery: ['Batterie', pct, 0, 100], valve: ['Ventil', pct, 0, 100],
    brightness: ['Helligkeit', pct, 0, 100], position: ['Rollladen', pct, 0, 100], volume: ['Lautstärke', pct, 0, 100]
  }
  const readingOf = (d, key) => (key && d[key] != null) ? key : Object.keys(READINGS).find(k => d[k] != null && typeof d[k] === 'number')
  W.register('value', {
    title: 'Messwert', icon: 'gauge', group: 'data', desc: 'Ein Wert eines Geräts – groß, als Anzeige oder als Balken', size: [2, 2], min: [1, 1], setup: true,
    fields: [
      { key: 'device', label: 'Gerät', type: 'select', def: '', options: ctx => [['', '— wählen —']].concat(deviceOptions(d => !!readingOf(d, ''))(ctx)) },
      { key: 'prop', label: 'Wert', type: 'select', def: '', options: [['', 'Automatisch']].concat(Object.keys(READINGS).map(k => [k, READINGS[k][0]])) },
      { key: 'look', label: 'Darstellung', type: 'select', def: 'gauge', options: [['number', 'Zahl'], ['gauge', 'Rundanzeige'], ['bar', 'Balken']] },
      { key: 'min', label: 'Skala von', type: 'text', def: '', max: 8, placeholder: 'automatisch', show: o => o.look !== 'number' },
      { key: 'max', label: 'Skala bis', type: 'text', def: '', max: 8, placeholder: 'automatisch', show: o => o.look !== 'number' }
    ],
    name: (w, ctx) => { const d = find(ctx, w.opts.device); return d ? d.name : '' },
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const d = find(ctx, w.opts.device)
      if (!d) return none('gauge', 'Gerät wählen')
      const key = readingOf(d, w.opts.prop)
      if (!key || !d.online) return `<div class="wg-value" data-dev="${HO.esc(d.id)}">${none(d.online ? 'gauge' : 'offline', d.online ? 'Kein Wert' : 'Kein Kontakt')}</div>`
      const spec = READINGS[key], v = d[key], text = HO.esc(spec[1](v)), label = HO.esc(spec[0])
      const lo = isFinite(parseFloat(w.opts.min)) ? parseFloat(w.opts.min) : spec[2], hi = isFinite(parseFloat(w.opts.max)) ? parseFloat(w.opts.max) : spec[3]
      const frac = hi > lo ? (v - lo) / (hi - lo) : 0, look = ctx.opt('look')
      const inner = look === 'gauge' && ctx.hz !== 'xs' ? gaugeSVG(frac, text, label)
        : `<div class="wg-big">${text}</div><div class="wg-sub">${label}</div>${look === 'bar' ? `<div class="wg-bar"><i style="width:${(Math.max(0, Math.min(1, frac)) * 100).toFixed(0)}%"></i></div>` : ''}`
      return `<div class="wg-value" data-dev="${HO.esc(d.id)}" role="button" tabindex="0">${inner}</div>`
    }
  })

  /* ---------- Power consumption */
  W.register('power', {
    title: 'Stromverbrauch', icon: 'bolt', group: 'data', desc: 'Leistung jetzt, Verbrauch heute und die größten Verbraucher', size: [4, 2], min: [2, 1], needs: ['power_w'],
    fields: [
      { key: 'top', label: 'Größte Verbraucher anzeigen', type: 'range', def: 3, min: 0, max: 8, step: 1 },
      { key: 'chart', label: 'Verlauf als Kurve', type: 'bool', def: false, pro: 'energy_analytics' },
      { key: 'hours', label: 'Zeitraum der Kurve', type: 'select', def: '24', options: [['6', '6 Stunden'], ['24', '24 Stunden'], ['168', '7 Tage']], show: o => !!o.chart }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const rows = devices(ctx).filter(d => d.power_w != null || d.energy_kwh != null)
      if (!rows.length) return none('bolt', 'Kein Gerät misst den Verbrauch')
      const live = rows.filter(d => d.online && d.power_w != null).sort((a, b) => b.power_w - a.power_w)
      const total = live.length ? live.reduce((a, d) => a + d.power_w, 0) : null, today = ctx.ov.power && ctx.ov.power.kwh_today
      const top = live.filter(d => d.power_w >= 1).slice(0, ctx.hz === 'xs' ? 0 : ctx.opt('top')), max = top.length ? top[0].power_w : 1
      let chart = ''
      if (ctx.opt('chart') && ctx.hz !== 'xs') {
        if (!HO.can('energy_analytics')) chart = proHint('Der Verbrauchsverlauf')
        else {
          const hours = ctx.opt('hours'), data = ctx.data('power:' + hours, () => HO.api('power/series?hours=' + encodeURIComponent(hours)), 120000)
          chart = data ? sparkSVG((data.points || []).map(p => p.w)) || '<div class="wg-sub">Noch kein Verlauf.</div>' : ''
        }
      }
      return `<div class="wg-energy" data-act="power" role="button" tabindex="0" title="Stromverbrauch im Detail">
        <div class="wg-kpi"><div class="wg-big">${HO.esc(ctx.home.watt(total))}</div>
          <div class="wg-sub">${today != null ? 'Heute ' + HO.esc(ctx.home.kwh(today)) : `${rows.length} Messgerät${rows.length === 1 ? '' : 'e'}`}</div></div>
        ${chart}${top.length ? `<div class="wg-tops">${top.map(d => `<div class="wg-top"><span>${HO.esc(d.name)}</span><b>${HO.esc(ctx.home.watt(d.power_w))}</b>
          <div class="wg-bar"><i style="width:${Math.max(4, d.power_w / max * 100).toFixed(0)}%"></i></div></div>`).join('')}</div>` : ''}</div>`
    }
  })

  /* ---------- Heating */
  W.register('heating', {
    title: 'Heizung', icon: 'flame', group: 'data', desc: 'Ist und Soll je Raum, dazu die aktive Heizroutine', size: [4, 3], min: [2, 2], needs: ['target'],
    fields: [
      { key: 'rooms', label: 'Nur diese Räume', type: 'multi', def: [], options: roomOptions, hint: 'Nichts gewählt = alle Räume mit Temperatur.' },
      { key: 'routine', label: 'Aktive Heizroutine anzeigen', type: 'bool', def: true }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const chosen = ctx.opt('rooms') || []
      const list = roomList(ctx).filter(r => r.temperature != null && (!chosen.length || chosen.indexOf(r.room_id) >= 0))
      if (!list.length) return none('flame', 'Kein Raum meldet eine Temperatur')
      const range = ctx.ov.heat_range || [16, 24], lo = Math.min(range[0], 15) - 1, hi = Math.max(range[1], 24) + 1
      const at = v => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)).toFixed(1)
      const active = ((ctx.ov.routines || {}).routines || []).find(r => r.active)
      return `<div class="wg-heat">${ctx.opt('routine') && ctx.hz !== 'xs' ? `<div class="wg-sub">${active ? 'Heizroutine: <b>' + HO.esc(active.name) + '</b>' : 'Keine Heizroutine aktiv'}</div>` : ''}
        <div class="wg-list">${list.map(r => `<div class="wg-heat-row"><span class="wg-row-name">${HO.esc(r.name)}</span>
          <b>${HO.temp(r.temperature, true)}</b><span class="wg-row-state">${r.target != null ? 'Soll ' + HO.temp(r.target) : ''}</span>
          <div class="wg-heat-bar"><i style="width:${at(r.temperature)}%;background:${HO.tempColor(r.temperature)}"></i>
            ${r.target != null ? `<u style="left:${at(r.target)}%"></u>` : ''}</div></div>`).join('')}</div></div>`
    }
  })

  /* ============================================================ Actions */

  /* ---------- Home / away */
  W.register('presence', {
    title: 'Zuhause / Abwesend', icon: 'home', group: 'action', desc: 'Umschalten zwischen Zuhause und Abwesend, mit Heizroutine', size: [3, 2], min: [2, 1],
    fields: [{ key: 'routine', label: 'Heizroutine anzeigen', type: 'bool', def: true }],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const home = ctx.ov.home || {}, away = home.state === 'AWAY'
      const active = ((ctx.ov.routines || {}).routines || []).find(r => r.active)
      return `<div class="wg-presence"><div class="wg-switch2">
          <button data-home="PRESENT" class="${away ? '' : 'active'}">${HO.icon('home')}<span>Zuhause</span></button>
          <button data-home="AWAY" class="${away ? 'active' : ''}">${HO.icon('away')}<span>Abwesend</span></button></div>
        ${ctx.opt('routine') && ctx.hz !== 'xs' ? `<div class="wg-sub">${active ? 'Heizroutine: <b>' + HO.esc(active.name) + '</b>' : 'Keine Heizroutine aktiv'}
          ${home.pending ? ' · wird übertragen …' : ''}</div>` : ''}</div>`
    }
  })

  /* ---------- All lights / lights of a room */
  W.register('lights', {
    title: 'Licht', icon: 'light', group: 'action', desc: 'Alle Lichter im Haus oder in einem Raum schalten und dimmen', size: [3, 2], min: [2, 1], needs: ['on'],
    fields: [
      { key: 'room', label: 'Bereich', type: 'select', def: '', options: ctx => [['', 'Ganzes Haus']].concat(roomOptions(ctx)) },
      { key: 'slider', label: 'Helligkeit regeln (nur für einen Raum)', type: 'bool', def: true, show: o => !!o.room }
    ],
    name: (w, ctx) => w.opts.room ? 'Licht ' + roomName(ctx, w.opts.room) : 'Alle Lichter',
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const rid = w.opts.room, lamps = devices(ctx).filter(d => d.kind === 'light' && (!rid || d.room_id === rid))
      if (!lamps.length) return none('light', rid ? 'Keine Lampen in diesem Raum' : 'Keine Lampen im Zuhause')
      const on = lamps.filter(d => d.on && d.online), glow = HO.lightGlow(lamps, 0.7), name = rid ? roomName(ctx, rid) : 'Alle Lichter'
      const attrs = next => rid ? `data-room-lights="${HO.esc(JSON.stringify([rid]))}" data-on="${next}" data-name="${HO.esc(name)}"` : `data-lights="${next}"`
      const dim = rid && ctx.opt('slider') && ctx.hz !== 'xs' && lamps.some(d => d.light && d.online)
      return `<div class="wg-lights ${on.length ? 'lit' : ''}">${glow ? `<div class="wg-glow" style="background:${glow}"></div>` : ''}
        <div class="wg-lights-top"><button class="wg-power ${on.length ? 'on' : ''}" ${attrs(on.length ? 0 : 1)} aria-pressed="${!!on.length}"
            aria-label="${on.length ? 'Ausschalten' : 'Einschalten'}" title="${on.length ? 'Ausschalten' : 'Einschalten'}">${HO.icon('power')}</button>
          <div><div class="wg-big">${on.length}<small>/${lamps.length}</small></div><div class="wg-sub">${on.length ? 'an' : 'Licht aus'}</div></div>
          ${on.length && on.length < lamps.length && ctx.sz !== 'xs' && ctx.sz !== 's' ? `<button class="btn sm" ${attrs(1)}>Alle an</button>` : ''}</div>
        ${dim ? `<input type="range" class="lt-bri" min="1" max="100" value="${HO.lights.avgBrightness(lamps)}" data-room-bri="${HO.esc(rid)}" aria-label="Helligkeit ${HO.esc(name)}">` : ''}</div>`
    }
  })

  /* ---------- House scene */
  const sceneTile = (s, on, attrs) => `<button type="button" class="wg-scene sc-chip ${on ? 'active' : ''}" ${attrs} style="background:${HO.paletteGradient(s.palette, 0.85)}">
    <span>${HO.esc(s.name)}</span>${s.dynamic ? HO.icon('sparkle', 'sm') : ''}</button>`
  W.register('house_scene', {
    title: 'Hausszene', icon: 'palette', group: 'action', desc: 'Eine Szene für alle zugeordneten Räume auf einmal', size: [4, 2], min: [2, 1], needs: ['on'],
    fields: [
      { key: 'scenes', label: 'Nur diese Szenen', type: 'multi', def: [], options: ctx => (((ctx.ov || {}).scenes || {}).scenes || []).map(s => [s.id, s.name]), hint: 'Nichts gewählt = alle Szenen mit Räumen.' },
      { key: 'off', label: 'Knopf „Keine“ anzeigen', type: 'bool', def: true }
    ],
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const st = ctx.ov.scenes || { scenes: [], active: {} }, act = st.active || {}, chosen = ctx.opt('scenes') || []
      const lampRooms = new Set(devices(ctx).filter(d => d.kind === 'light' && d.room_id).map(d => d.room_id))
      const list = (st.scenes || []).filter(s => s.rooms && s.rooms.some(r => lampRooms.has(r)) && (!chosen.length || chosen.indexOf(s.id) >= 0))
        .sort((a, b) => HO.byName(a.name, b.name))
      if (!list.length) return none('palette', 'Keine Szene ist einem Raum zugeordnet')
      const isOn = s => s.rooms.filter(r => lampRooms.has(r)).every(r => act['room:' + r] && act['room:' + r].scene === s.id)
      return `<div class="wg-scenes">${ctx.opt('off') ? `<button type="button" class="wg-scene none ${Object.keys(act).length ? '' : 'active'}" data-house-off
          title="Alle laufenden Szenen beenden"><span>Keine</span></button>` : ''}
        ${list.map(s => sceneTile(s, isOn(s), `data-house="${HO.esc(s.id)}" title="In ${s.rooms.length} Raum/Räumen setzen"`)).join('')}</div>`
    }
  })

  /* ---------- Scenes of a room */
  W.register('scenes', {
    title: 'Szenen-Schnellwahl', icon: 'sparkle', group: 'action', desc: 'Die Szenen eines Raums als große Tasten', size: [4, 2], min: [2, 1], needs: ['on'], setup: true,
    fields: [{ key: 'room', label: 'Raum', type: 'select', def: '', options: ctx => [['', '— wählen —']].concat(roomOptions(ctx)) }],
    name: (w, ctx) => w.opts.room ? 'Szenen ' + roomName(ctx, w.opts.room) : '',
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const rid = w.opts.room
      if (!rid || !roomName(ctx, rid)) return none('sparkle', 'Raum wählen')
      const st = ctx.ov.scenes || { scenes: [], active: {} }, cur = (st.active || {})['room:' + rid], act = cur ? cur.scene : null
      const list = (st.scenes || []).filter(s => (s.rooms || []).indexOf(rid) >= 0 || s.id === act).sort((a, b) => HO.byName(a.name, b.name))
      if (!list.length) return none('sparkle', 'Diesem Raum ist keine Szene zugeordnet')
      return `<div class="wg-scenes" data-sc-row>${list.map(s => sceneTile(s, s.id === act,
        `data-sc-apply="${HO.esc(s.id)}" data-sc-room="${HO.esc(rid)}" title="${s.id === act ? 'Antippen beendet die Szene' : 'Szene aktivieren'}"`)).join('')}</div>`
    }
  })

  /* ---------- Single device */
  // Control of a device by what it can do: switch, set point, cover, playback – or its lock.
  function deviceControl(d, ctx) {
    const id = HO.esc(d.id)
    if (d.online && d.control_item && d.locked) return HO.lockedBtn(d, 'wg-lock')
    if (!HO.canControl(d)) return ''
    if (d.kind === 'cover') return `<div class="wg-keys">
      <button class="btn-icon up" data-cmd="cover" data-v="up" data-id="${id}" aria-label="Öffnen" title="Öffnen">${HO.icon('chevron')}</button>
      <button class="btn-icon" data-cmd="cover" data-v="stop" data-id="${id}" aria-label="Anhalten" title="Anhalten">${HO.icon('pause')}</button>
      <button class="btn-icon down" data-cmd="cover" data-v="down" data-id="${id}" aria-label="Schließen" title="Schließen">${HO.icon('chevron')}</button></div>`
    if (d.kind === 'thermostat') return `<div class="wg-keys">
      <button class="btn-icon" data-cmd="delta" data-v="-0.5" data-id="${id}" aria-label="Kälter">${HO.icon('minus')}</button>
      <button class="btn-icon" data-cmd="delta" data-v="0.5" data-id="${id}" aria-label="Wärmer">${HO.icon('plus')}</button></div>`
    if (d.kind === 'light' || d.kind === 'switch') return `<button class="wg-power ${d.on ? 'on' : ''} ${d._busy ? 'busy' : ''}" data-cmd="toggle" data-on="${d.on ? 0 : 1}" data-id="${id}"
      aria-pressed="${!!d.on}" aria-label="${d.on ? 'Ausschalten' : 'Einschalten'}" ${lampTint(d)}>${HO.icon('power')}</button>`
    if (HO.caps.has(d, 'playing', true)) return `<button class="wg-power ${d.playing ? 'on' : ''}" data-media-act="playing" data-on="${d.playing ? 0 : 1}" data-id="${id}"
      aria-pressed="${!!d.playing}" aria-label="${d.playing ? 'Pause' : 'Wiedergabe'}">${HO.icon(d.playing ? 'pause' : 'play')}</button>`
    return ''
  }
  W.register('device', {
    title: 'Gerät', icon: 'device', group: 'action', desc: 'Ein einzelnes Gerät mit seinem Zustand und der passenden Bedienung', size: [2, 2], min: [1, 1], setup: true,
    fields: [
      { key: 'device', label: 'Gerät', type: 'select', def: '', options: ctx => [['', '— wählen —']].concat(deviceOptions(() => true)(ctx)) },
      { key: 'look', label: 'Darstellung', type: 'select', def: 'tile', options: [['tile', 'Kachel mit Zustand'], ['button', 'Nur der Knopf']] }
    ],
    name: (w, ctx) => { const d = find(ctx, w.opts.device); return d ? d.name : '' },
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const d = find(ctx, w.opts.device)
      if (!d) return none('device', 'Gerät wählen')
      const control = deviceControl(d, ctx), lit = d.on || HO.playingNow(d)
      if (ctx.opt('look') === 'button' && control) return `<div class="wg-device only ${lit ? 'lit' : ''} ${d.online ? '' : 'off'}">${control}</div>`
      const big = d.kind === 'thermostat' && d.temperature != null ? HO.temp(d.temperature, true) : ''
      return `<div class="wg-device ${lit ? 'lit' : ''} ${d.online ? '' : 'off'}" ${lampTint(d)}>
        <button type="button" class="wg-device-info" data-dev="${HO.esc(d.id)}" title="Details">
          ${big ? `<span class="wg-big">${big}</span>` : `<span class="dev-ic ${HO.devClass(d)}" ${lampTint(d)}>${HO.icon(d.kind)}</span>`}
          <span class="wg-sub">${HO.esc(HO.devState(d))}</span></button>${control}</div>`
    }
  })

  /* ---------- Flow button */
  let flowList = null
  const loadFlows = () => HO.api('flows').then(r => { flowList = r.flows || []; return flowList })
  // The play buttons of all flows as a choice "flow id/node id".
  function flowOptions() {
    const out = [['', '— wählen —']]
    for (const f of flowList || []) {
      for (const n of f.nodes || []) {
        if (n.type !== 'play' || (n.config && n.config.role && n.config.role !== 'trigger')) continue
        out.push([f.id + '/' + n.id, f.name + ((n.config || {}).label ? ' · ' + n.config.label : '')])
      }
    }
    return out
  }
  W.register('flow', {
    title: 'Flow-Knopf', icon: 'workflow', group: 'action', desc: 'Startet einen Flow über seinen Play-Baustein', size: [2, 2], min: [1, 1], setup: true, align: 'center',
    fields: [
      { key: 'flow', label: 'Flow (Baustein „Play“)', type: 'select', def: '', options: flowOptions, load: loadFlows,
        hint: 'Zur Auswahl stehen Flows mit einem Play-Baustein als Auslöser.' },
      { key: 'label', label: 'Beschriftung', type: 'text', def: '', max: 30, placeholder: 'wie im Flow' },
      { key: 'confirm', label: 'Vor dem Start nachfragen', type: 'bool', def: false }
    ],
    name: w => w.opts.label || '',
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const key = w.opts.flow, parts = String(key || '').split('/')
      if (parts.length !== 2) return none('workflow', 'Flow wählen')
      const flows = ctx.data('flows', loadFlows, 60000), f = (flows || []).find(x => x.id === parts[0])
      const node = f && (f.nodes || []).find(n => n.id === parts[1])
      if (flows && !node) return none('workflow', 'Diesen Flow gibt es nicht mehr')
      const label = w.opts.label || (node && node.config && node.config.label) || (f ? f.name : 'Los'), off = f && !f.enabled
      return `<div class="wg-flow"><button class="wg-power play" data-wg-play="${HO.esc(key)}" data-name="${HO.esc(label)}" ${w.opts.confirm ? 'data-confirm="1"' : ''}
          ${off ? 'disabled' : ''} aria-label="${HO.esc(label)} starten">${HO.icon('play')}</button>
        <div class="wg-sub"><b>${HO.esc(label)}</b>${off ? '<br>Flow ist ausgeschaltet' : f && ctx.hz !== 'xs' ? '<br>' + HO.esc(f.name) : ''}</div></div>`
    }
  })

  /* ---------- Playback */
  W.register('media', {
    title: 'Wiedergabe', icon: 'media', group: 'action', desc: 'Ein Lautsprecher: Titel, Wiedergabe, Lautstärke, Favoriten', size: [4, 3], min: [2, 2], needs: ['playing'],
    fields: [{ key: 'device', label: 'Lautsprecher', type: 'select', def: '', options: ctx => [['', 'Automatisch (was gerade spielt)']].concat(HO.media.devices(devices(ctx)).map(d => [d.id, d.name])) }],
    name: (w, ctx) => { const d = find(ctx, w.opts.device); return d ? d.name : '' },
    render(w, ctx) {     // HTML of the widget from the current state of the home (contract: head of widgets.js)
      const list = HO.media.devices(devices(ctx))
      const d = list.find(x => x.id === w.opts.device) || (w.opts.device ? null : list.find(x => x.playing && x.online) || list[0])
      if (!d) return none('media', list.length ? 'Diesen Lautsprecher gibt es nicht mehr' : 'Kein Lautsprecher im Zuhause')
      const image = d.online && d.media && d.media.image
      return `<div class="wg-media ${d.playing && d.online ? 'lit' : ''}">${image ? `<div class="wg-cover" style="background-image:url('${HO.esc(image)}')"></div>` : ''}${HO.media.cardHTML(d)}</div>`
    }
  })

  /* ============================================================ Decoration */

  W.register('note', {
    title: 'Notiz', icon: 'type', group: 'deco', desc: 'Freier Text – für Hinweise, WLAN-Namen, Müllabfuhr', size: [3, 2], min: [1, 1], setup: true,
    fields: [{ key: 'text', label: 'Text', type: 'textarea', def: '', max: 400 }],
    render: w => w.opts.text ? `<div class="wg-note">${HO.esc(w.opts.text)}</div>` : none('type', 'Text eintragen')
  })

  W.register('heading', {
    title: 'Überschrift', icon: 'hash', group: 'deco', desc: 'Gliedert die Seite in Abschnitte', size: [12, 1], min: [2, 1], narrow: [4, 1], head: false, style: 'bare', align: 'end', setup: true,
    fields: [
      { key: 'text', label: 'Überschrift', type: 'text', def: 'Abschnitt', max: 60 },
      { key: 'sub', label: 'Zeile darunter', type: 'text', def: '', max: 120 }
    ],
    render: w => `<div class="wg-heading"><h2>${HO.esc(w.opts.text || 'Abschnitt')}</h2>${w.opts.sub ? `<span class="wg-sub">${HO.esc(w.opts.sub)}</span>` : ''}</div>`
  })

  /* ---------- Suggestion for the first page */
  // Widgets that fit this home, in reading order: [type, width, height, settings, properties].
  W.starter = function (ov) {
    const list = (ov && ov.devices) || [], plan = (ov && ov.floorplan) || {}, has = fn => list.some(fn)
    const out = [['clock', 3, 2, { greeting: true }], ['presence', 3, 2], ['status', 3, 2]]
    out.splice(1, 0, plan.address ? ['weather', 3, 2] : ['connections', 3, 2])
    if (HO.floorplan.hasWalls(plan)) out.push(['floorplan', 8, 6])
    if (has(d => d.kind === 'light')) out.push(['lights', 4, 2])
    if (((ov.scenes || {}).scenes || []).some(s => (s.rooms || []).length)) out.push(['house_scene', 4, 2])
    if (has(d => d.power_w != null || d.energy_kwh != null)) out.push(['power', 4, 2])
    if (has(d => d.kind === 'thermostat')) out.push(['heating', 4, 3])
    if (has(d => HO.caps.has(d, 'playing'))) out.push(['media', 4, 3])
    if (has(d => d.kind === 'contact')) out.push(['openings', 4, 3])
    ;((ov && ov.rooms) || []).filter(r => r.room_id && r.devices).slice(0, 3).forEach(r => out.push(['room', 4, 3, { room: r.room_id }]))
    out.push(['events', 4, 3])
    return out
  }
})(window.HO)
