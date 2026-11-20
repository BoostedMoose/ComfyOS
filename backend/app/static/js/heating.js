/* ComfyOS – Heizplan: Routinen, Wochen-Zeitleisten je Raum, Editor, Kopieren, Verlauf */
(function (HO) {
  'use strict'
  const LEVELS = [['COMFORT', 'Komfort'], ['ON', 'Standard'], ['ECO', 'Eco'], ['OFF', 'Aus']]
  let S = { key: null, routines: null, routine: null, rooms: [], home: {}, hist: [], histRoom: null, hours: 24, chart: null, timer: null, lastSync: null, editorOpen: false, gen: 0, logStats: null }

  /* ---------- Plan-Logik */
  const pointsOf = (sched, room, day) => ((sched[room] || {})[String(day)] || []).filter(p => p.temp != null)
  const mins = t => { const [h, m] = t.split(':').map(Number); return h * 60 + m }
  // Temperature a day starts with: the last switching point of one of the previous days.
  function startTemp(sched, room, day) {
    for (let k = 1; k <= 7; k++) {
      const pts = pointsOf(sched, room, (day - k + 7) % 7)
      if (pts.length) return pts[pts.length - 1].temp
    }
    return null
  }
  // Daily schedule as sections (from, to, temperature) over 24 hours – for the bar.
  function segments(sched, room, day, override) {
    const pts = (override || pointsOf(sched, room, day)).slice().sort((a, b) => mins(a.time) - mins(b.time))
    let cur = startTemp(sched, room, day), at = 0
    const segs = []
    for (const p of pts) {
      const m = mins(p.time)
      if (m > at) segs.push({ from: at, to: m, temp: cur })
      cur = p.temp; at = m
    }
    if (at < 1440) segs.push({ from: at, to: 1440, temp: cur })
    return segs
  }
  const barHTML = segs => segs.map(s => {
    const w = (s.to - s.from) / 14.4
    return `<div class="tl-seg ${s.temp == null ? 'empty' : ''}" style="left:${(s.from / 14.4).toFixed(3)}%;width:${w.toFixed(3)}%;${s.temp != null ? `background:${HO.tempColor(s.temp)}` : ''}">${w >= 9 && s.temp != null ? HO.temp(s.temp) : ''}</div>`
  }).join('')

  const roomName = rid => (S.routine && S.routine.room_names && S.routine.room_names[rid]) || (S.rooms.find(r => r.room_id === rid) || {}).name || rid
  // Rooms that appear in the heating schedule: all with a thermostat and all for which there already are switching points.
  function visibleRooms() {
    const ids = new Set(S.rooms.filter(r => r.room_id && r.thermostats > 0).map(r => r.room_id))
    const sched = (S.routine && S.routine.schedule) || {}
    for (const rid of Object.keys(sched)) if (Object.values(sched[rid]).some(p => p && p.length)) ids.add(rid)
    return [...ids].sort((a, b) => HO.byName(roomName(a), roomName(b)))
  }

  /* ---------- Darstellung */
  function headHTML() {
    const rs = S.routines.routines, r = S.routine, sync = S.routines.sync
    const role = k => k === S.home.present_routine ? '<span class="badge accent">Zuhause</span>' : k === S.home.away_routine ? '<span class="badge warn">Abwesend</span>' : ''
    // head without a tile as in Flow (since 0.19.2): title, explanation, import and new; below it the routine in a tile
    return `<div class="section-head"><div><h2 style="margin:0">Heizroutinen</h2>
        <div class="small muted">Wochenpläne je Raum. Es gilt die aktive Routine; Zuhause/Abwesend wechselt zwischen zwei.</div></div>
        <div class="row"><button class="btn sm" data-act="import" title="Heizpläne aus JSON importieren">${HO.icon('upload', 'sm')} Import</button>
        <button class="btn sm primary" data-act="new">${HO.icon('plus', 'sm')} Neu</button></div></div>
      ${rs.length ? `<section class="card hz-head">
      <div class="chips scroll">${rs.map(x => `<span class="chip ${x.key === S.key ? 'active' : ''}" data-r="${HO.esc(x.key)}"><span class="dot ${x.active ? 'on' : ''}"></span>${HO.esc(x.name)}</span>`).join('')}</div>
      ${r ? `<div class="row between hz-routine"><div><h2>${HO.esc(r.name)}</h2>
          <div class="row wrap g6" style="margin-top:6px">${r.active ? '<span class="badge ok">Aktiv</span>' : ''}${role(r.key)}
            ${r.source === 'local' ? '<span class="badge">nur lokal</span>' : ''}${r.dirty || r.pending_create || r.pending_rename ? '<span class="badge warn">wartet auf Abgleich</span>' : ''}</div></div>
        <div class="row">${r.active ? '' : `<button class="btn sm" data-act="activate">${HO.icon('check', 'sm')} Aktivieren</button>`}
          <button class="btn-icon sm" data-act="menu" aria-label="Weitere Aktionen">${HO.icon('more')}</button></div></div>` : ''}
      <div class="sync-line">${HO.syncLine(sync)}</div>
      ${!sync.configured ? `<p class="small muted" style="margin:8px 0 0">Zu jedem Schaltpunkt der aktiven Routine setzt ComfyOS die Solltemperatur der Thermostate im Raum. Von Hand verstellt gilt bis zum nächsten Schaltpunkt.</p>` : ''}
    </section>` : ''}`
  }

  // Card of a room in the heating schedule: measured and target temperature, below it one bar per weekday with the daily schedule.
  function roomCardHTML(rid) {
    const sched = S.routine.schedule, info = S.rooms.find(r => r.room_id === rid) || {}
    const today = HO.todayIndex(), nowPct = (new Date().getHours() * 60 + new Date().getMinutes()) / 14.4
    const rows = HO.dayNames.map((dn, d) => `<button class="tl-row ${d === today ? 'today' : ''}" data-edit="${HO.esc(rid)}" data-day="${d}" aria-label="${HO.dayLong[d]} bearbeiten">
        <span class="tl-day">${dn}</span><span class="tl-bar">${barHTML(segments(sched, rid, d))}${d === today ? `<span class="tl-now" style="left:${nowPct.toFixed(2)}%"></span>` : ''}</span></button>`).join('')
    return `<article class="card hz-room"><header><div><h3>${HO.esc(roomName(rid))}</h3>
        <div class="now">${info.temperature != null ? `Ist ${HO.temp(info.temperature)}` : 'Keine Messung'}${info.target != null ? ` · Soll ${HO.temp(info.target)}` : ''}</div></div>
        <button class="btn-icon sm" data-copyroom="${HO.esc(rid)}" title="Wochenplan auf andere Räume übertragen" aria-label="Wochenplan kopieren">${HO.icon('copy', 'sm')}</button></header>
      <div class="tl">${rows}</div>
      <div class="tl-axis"><span></span><div><span>0</span><span>6</span><span>12</span><span>18</span><span>24</span></div></div></article>`
  }

  // Bytes as a readable size (B, KB, MB).
  function fmtBytes(b) {
    if (!b || b < 1024) return (b || 0) + ' B'
    if (b < 1024 * 1024) return Math.round(b / 1024) + ' KB'
    return (b / (1024 * 1024)).toFixed(1) + ' MB'
  }

  // Section "Verlauf": room choice (also the average), period, chart and legend.
  function historyHTML() {
    const rooms = S.hist.filter(h => h.points.some(p => p.ist != null))
    if (S.histRoom === '__avg__' && rooms.length < 2) S.histRoom = null
    if (!S.histRoom || (S.histRoom !== '__avg__' && !rooms.find(h => (h.room_id || h.room) === S.histRoom))) {
      const pref = rooms.find(h => S.rooms.find(r => r.room_id === h.room_id && r.thermostats)) || rooms[0]
      S.histRoom = pref ? (pref.room_id || pref.room) : null
    }
    const storageInfo = S.logStats ? ` · Speicher: ${fmtBytes(S.logStats.bytes)}` : ''
    return `<section class="card section"><div class="card-head"><div><h3>Temperatur- & Heizverlauf</h3><div class="small muted">Aus den ComfyOS-Logs · Ist, Soll und Ventil je Raum${storageInfo}</div></div>
        <div class="row gap-sm"><div class="seg"><button data-hours="24" class="${S.hours === 24 ? 'active' : ''}">24 Std.</button><button data-hours="168" class="${S.hours === 168 ? 'active' : ''}">7 Tage</button></div><button class="btn-icon sm" data-act="logSettings" title="Log-Einstellungen">${HO.icon('settings', 'sm')}</button></div></div>
      ${rooms.length ? `<div class="chips scroll" style="margin-bottom:12px">${rooms.map(h => `<span class="chip ${(h.room_id || h.room) === S.histRoom ? 'active' : ''}" data-hroom="${HO.esc(h.room_id || h.room)}">${HO.esc(h.room_id ? roomName(h.room_id) : h.room)}</span>`).join('')}${rooms.length > 1 ? `<span class="chip ${S.histRoom === '__avg__' ? 'active' : ''}" data-hroom="__avg__">⌀ Durchschnitt</span>` : ''}</div>` : ''}
      <div class="chart" id="hzChart"></div>
      <div class="chart-legend" id="hzLegend"><span><i style="background:var(--accent)"></i>Ist</span><span><i style="background:var(--soll)"></i>Soll</span><span class="lane" hidden></span></div></section>`
  }

  // Dialog "Log-Einstellungen": measuring interval and retention period, delete the history.
  async function logSettingsSheet() {
    let cfg
    try { cfg = await HO.api('logs/settings') } catch (e) { return HO.toast(e.message, 'bad') }
    const s = HO.sheet({ title: 'Log-Einstellungen', body: `
      <label class="field"><span>Messintervall (Sekunden)</span>
        <input type="number" id="lsSample" min="60" max="3600" step="60" value="${cfg.sample_seconds}">
        <span class="hint">Wie oft Temperaturwerte aufgezeichnet werden. Standard: 300 (5 Min.)</span></label>
      <label class="field"><span>Aufbewahrung (Monate)</span>
        <input type="number" id="lsRetention" min="1" max="60" step="1" value="${cfg.retention_months}">
        <span class="hint">Wie lange Logdaten gespeichert werden. Standard: 13</span></label>`,
      actions: [{ label: 'Abbrechen', kind: 'ghost' }, { label: 'Speichern', kind: 'primary', onClick: async () => {
        const sample = Math.max(60, Math.min(3600, +s.el.querySelector('#lsSample').value || 300))
        const retention = Math.max(1, Math.min(60, +s.el.querySelector('#lsRetention').value || 13))
        try {
          await HO.api('logs/settings', { method: 'PUT', body: { sample_seconds: sample, retention_months: retention } })
          HO.toast('Log-Einstellungen gespeichert.')
          s.close()
        } catch (e) { HO.toast(e.message, 'bad'); return true }
      } }] })
  }

  // Draw the page "Heizplan": head with routines, history and the room cards – or the hint if there is no routine
  // yet.
  function renderAll(page) {
    if (!S.routines.routines.length) {
      page.innerHTML = headHTML() + historyHTML() + `<div class="card section empty">${HO.icon('flame', 'lg')}<h3 style="margin:8px 0">Noch keine Heizroutinen</h3>
        <p class="muted">${S.routines.sync.configured ? 'Der Abgleich mit der Magenta-App läuft – oder lege selbst eine an.' : 'Lege eine Routine an oder aktiviere unter System den Magenta-Sync, um die Routinen aus der App zu übernehmen.'}</p>
        <div class="row" style="justify-content:center;flex-wrap:wrap"><button class="btn primary" data-act="new">${HO.icon('plus', 'sm')} Routine anlegen</button>
        <button class="btn" data-act="import">${HO.icon('upload', 'sm')} Aus JSON importieren</button></div></div>`
    } else {
      const rooms = visibleRooms()
      page.innerHTML = headHTML() + historyHTML() + `<div class="section-head section"><h3>Wochenplan</h3>
          <div class="legend"><span>5°</span><div class="bar" style="background:${HO.tempGradient()}"></div><span>25°</span></div></div>
        <div class="hz-grid">${rooms.map(roomCardHTML).join('') || `<div class="card empty">Keine Räume mit Heizkörpern gefunden.</div>`}</div>`
    }
    mountChart(page)
  }

  // History of the average of all rooms: per point in time the last value of each room, at most 30 minutes old.
  function avgPoints() {
    const rooms = S.hist.filter(h => h.points.some(p => p.ist != null))
    if (!rooms.length) return []
    // per point in time: last value of each room (at most 30 min old) – otherwise the average jumps
    // if rooms measure in different time buckets
    const HOLD = 1800, ts = [...new Set(rooms.flatMap(r => r.points.map(p => p.t)))].sort((a, b) => a - b)
    const last = rooms.map(() => ({ i: 0, ist: null, istT: -Infinity, soll: null, sollT: -Infinity, valve: null, valveT: -Infinity }))
    return ts.map(t => {
      let iS = 0, iN = 0, sS = 0, sN = 0, vS = 0, vN = 0
      rooms.forEach((r, k) => {
        const c = last[k]
        while (c.i < r.points.length && r.points[c.i].t <= t) {
          const p = r.points[c.i++]
          if (p.ist != null) { c.ist = p.ist; c.istT = p.t }
          if (p.soll != null) { c.soll = p.soll; c.sollT = p.t }
          if (p.valve != null) { c.valve = p.valve; c.valveT = p.t }
        }
        if (c.ist != null && t - c.istT <= HOLD) { iS += c.ist; iN++ }
        if (c.soll != null && t - c.sollT <= HOLD) { sS += c.soll; sN++ }
        if (c.valve != null && t - c.valveT <= HOLD) { vS += c.valve; vN++ }
      })
      return { t, ist: iN ? Math.round(iS / iN * 100) / 100 : null, soll: sN ? Math.round(sS / sN * 10) / 10 : null, valve: vN ? Math.round(vS / vN) : null }
    })
  }

  // Mount the chart of the chosen room (or of the average).
  function mountChart(page) {
    const el = page.querySelector('#hzChart')
    if (!el) return
    if (S.chart) S.chart.destroy()
    const pts = S.histRoom === '__avg__' ? avgPoints() : (() => { const h = S.hist.find(x => (x.room_id || x.room) === S.histRoom); return h ? h.points : [] })()
    S.chart = HO.tempChart(el, pts, { hours: S.hours, lane: true })
    const lg = page.querySelector('#hzLegend .lane')   // legend of the valve lane only if it is drawn
    if (lg) {
      const mode = S.chart && S.chart.lane
      lg.hidden = !mode
      lg.innerHTML = mode === 'valve' ? '<i style="background:var(--valve)"></i>Ventil offen (%)' : '<i class="hatch"></i>Heizbedarf (abgeleitet)'
    }
  }

  /* ---------- Daten */
  async function loadRoutine() {
    S.routine = S.key ? await HO.api('routines/' + encodeURIComponent(S.key)).catch(() => null) : null
  }
  // Load everything for the page: routines, rooms, home/away, history, size of the log; determine the routine shown.
  async function loadAll() {
    const [routines, rooms, home, hist, logStats] = await Promise.all([HO.api('routines'), HO.api('rooms').catch(() => []),
      HO.api('home').catch(() => ({})), HO.api('history/rooms?hours=' + S.hours).catch(() => []),
      HO.api('logs/heating/stats').catch(() => null)])
    S.routines = routines; S.rooms = rooms; S.home = home; S.hist = hist; S.lastSync = routines.last_sync; S.logStats = logStats
    if (!S.key || !routines.routines.find(r => r.key === S.key)) {
      const a = routines.routines.find(r => r.active) || routines.routines[0]
      S.key = a ? a.key : null
    }
    await loadRoutine()
  }

  /* ---------- Editor for a room/day */
  function openEditor(page, rid, day) {
    const r = S.routine, lv = r.levels || {}
    let pts = pointsOf(r.schedule, rid, day).map(p => ({ time: p.time, temp: p.temp }))
    const days = new Set([day]), rooms = new Set([rid])
    const all = visibleRooms()
    S.editorOpen = true
    const body = () => `<div class="preview-bar tl-bar" style="height:34px">${barHTML(segments(r.schedule, rid, day, pts))}</div>
      <div id="ptList">${pts.length ? pts.map((p, i) => `<div class="pt-row">
          <input type="time" value="${p.time}" step="900" data-i="${i}" data-f="time">
          <div class="stepper"><button class="btn-icon sm" data-step="-0.5" data-i="${i}">${HO.icon('minus', 'sm')}</button><span class="val" style="color:${HO.tempColor(p.temp)}">${HO.temp(p.temp, true)}</span><button class="btn-icon sm" data-step="0.5" data-i="${i}">${HO.icon('plus', 'sm')}</button></div>
          <div class="levels">${LEVELS.filter(([k]) => lv[k] != null).map(([k, l]) => `<span class="chip ${p.temp === lv[k] ? 'active' : ''}" data-lv="${lv[k]}" data-i="${i}">${l} ${HO.temp(lv[k])}</span>`).join('')}</div>
          <button class="btn-icon sm plain" data-del="${i}" aria-label="Schaltpunkt löschen">${HO.icon('trash', 'sm')}</button></div>`).join('')
        : '<p class="muted small">Keine Schaltpunkte – der Raum hält den Wert vom Vortag.</p>'}</div>
      <button class="btn sm" data-add style="margin-top:6px">${HO.icon('plus', 'sm')} Schaltpunkt</button>
      <div class="field" style="margin-top:20px"><span>Auch übernehmen für</span>
        <div class="chips">${HO.dayNames.map((n, d) => `<span class="chip ${days.has(d) ? 'active' : ''}" data-day="${d}">${n}</span>`).join('')}</div>
        <div class="chips" style="margin-top:6px"><span class="chip" data-days="0,1,2,3,4">Werktage</span><span class="chip" data-days="5,6">Wochenende</span><span class="chip" data-days="0,1,2,3,4,5,6">Alle Tage</span></div></div>
      ${all.length > 1 ? `<div class="field"><span>Auch für Räume</span><div class="chips">${all.map(x => `<span class="chip ${rooms.has(x) ? 'active' : ''}" data-room="${HO.esc(x)}">${HO.esc(roomName(x))}</span>`).join('')}</div></div>` : ''}`
    const label = () => `Speichern${days.size > 1 || rooms.size > 1 ? ` (${days.size > 1 ? days.size + ' Tage' : '1 Tag'} · ${rooms.size > 1 ? rooms.size + ' Räume' : '1 Raum'})` : ''}`
    const s = HO.sheet({ title: `${roomName(rid)} · ${HO.dayLong[day]}`, wide: true, body: body(),
      actions: [{ label: 'Abbrechen', kind: 'ghost' }, { label: label(), kind: 'primary', onClick: () => save() }] })
    const rerender = () => {
      s.body().innerHTML = body()
      s.el.querySelector('.sheet-foot .primary').textContent = label()
    }
    const sortPts = () => {
      const seen = new Map(); for (const p of pts) seen.set(p.time, p)
      pts = [...seen.values()].sort((a, b) => mins(a.time) - mins(b.time))
    }
    s.el.addEventListener('click', e => {
      const t = e.target.closest('[data-step],[data-lv],[data-del],[data-add],[data-day],[data-days],[data-room]')
      if (!t) return
      if (t.dataset.step) { const p = pts[+t.dataset.i]; p.temp = Math.max(5, Math.min(30, Math.round((p.temp + Number(t.dataset.step)) * 2) / 2)) }
      else if (t.dataset.lv) pts[+t.dataset.i].temp = Number(t.dataset.lv)
      else if (t.dataset.del != null) pts.splice(+t.dataset.del, 1)
      else if (t.dataset.add != null) {
        const last = pts[pts.length - 1]
        const m = last ? Math.min(1425, mins(last.time) + 60) : 360
        pts.push({ time: String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'), temp: last ? last.temp : 20 })
        sortPts()
      } else if (t.dataset.days) { t.dataset.days.split(',').forEach(d => days.add(+d)) }
      else if (t.dataset.day != null && !t.closest('#ptList')) { const d = +t.dataset.day; if (d !== day) days.has(d) ? days.delete(d) : days.add(d) }
      else if (t.dataset.room) { const x = t.dataset.room; if (x !== rid) rooms.has(x) ? rooms.delete(x) : rooms.add(x) }
      rerender()
    })
    s.el.addEventListener('change', e => {
      const t = e.target
      if (t.dataset.f === 'time' && t.value) { pts[+t.dataset.i].time = t.value; sortPts(); rerender() }
    })
    const obs = new MutationObserver(() => { if (!document.body.contains(s.el)) { S.editorOpen = false; obs.disconnect() } })
    obs.observe(document.body, { childList: true })
    // Save the daily schedule from the editor for the chosen rooms and days and sync.
    async function save() {
      sortPts()
      const changes = []
      for (const room of rooms) for (const d of days) changes.push({ room, day: d, points: pts.map(p => ({ time: p.time, temp: p.temp })) })
      const res = await HO.api(`routines/${encodeURIComponent(S.key)}/schedule`, { method: 'PUT', body: { changes } })
      S.routine = res.routine
      HO.syncToast(res.sync, 'Heizplan gespeichert')
      S.routines = await HO.api('routines')
      renderAll(page)
    }
  }

  // Dialog "Wochenplan übertragen": copy the schedule of a room to other rooms.
  function copyRoomSheet(page, rid) {
    const others = visibleRooms().filter(x => x !== rid), sel = new Set()
    if (!others.length) return HO.toast('Keine weiteren Räume vorhanden.', 'warn')
    const body = () => `<p class="muted small">Der komplette Wochenplan von <b>${HO.esc(roomName(rid))}</b> ersetzt den Plan der gewählten Räume.</p>
      <div class="chips">${others.map(x => `<span class="chip ${sel.has(x) ? 'active' : ''}" data-room="${HO.esc(x)}">${HO.esc(roomName(x))}</span>`).join('')}</div>`
    const s = HO.sheet({ title: 'Wochenplan übertragen', body: body(), actions: [{ label: 'Abbrechen', kind: 'ghost' },
      { label: 'Übertragen', kind: 'primary', onClick: async () => {
        if (!sel.size) { HO.toast('Bitte mindestens einen Raum wählen.', 'warn'); return true }
        const changes = []
        for (const room of sel) for (let d = 0; d < 7; d++) changes.push({ room, day: d, points: pointsOf(S.routine.schedule, rid, d).map(p => ({ time: p.time, temp: p.temp })) })
        const res = await HO.api(`routines/${encodeURIComponent(S.key)}/schedule`, { method: 'PUT', body: { changes } })
        S.routine = res.routine
        HO.syncToast(res.sync, `Wochenplan auf ${sel.size} Raum/Räume übertragen`)
        renderAll(page)
      } }] })
    s.el.addEventListener('click', e => { const t = e.target.closest('[data-room]'); if (!t) return
      sel.has(t.dataset.room) ? sel.delete(t.dataset.room) : sel.add(t.dataset.room); s.body().innerHTML = body() })
  }

  /* ---------- Import / export (JSON, e.g. created by an AI) */
  function downloadJSON(obj, name) {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' }))
    a.download = name; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }
  // Dialog "Aus JSON importieren": paste routines or choose a file, have them checked, take them over – with an example
  // to copy.
  async function importSheet(page) {
    const info = await HO.api('heating/import').catch(() => ({ rooms: [], levels: {}, routines: [] }))
    const rooms = info.rooms.length ? info.rooms : ['Wohnzimmer', 'Bad']
    const example = { routinen: [{ name: 'Homeoffice', räume: {
      [rooms[0]]: { 'Mo-Fr': [['06:30', 21], ['08:00', 20.5], ['22:00', 17]], 'Sa-So': [['08:00', 21], ['23:00', 'Eco']] },
      ...(rooms[1] ? { [rooms[1]]: { 'täglich': [['06:00', 'Komfort'], ['08:00', 18]] } } : {}) } }] }
    const lv = info.levels || {}
    const prompt = `Erstelle Heizpläne für mein Smart Home (ComfyOS) als JSON. Antworte ausschließlich mit gültigem JSON in genau diesem Format:\n\n${JSON.stringify(example, null, 2)}\n\nRegeln:\n- Räume – nur diese Namen verwenden: ${rooms.join(', ')}\n- Tage: Mo, Di, Mi, Do, Fr, Sa, So; Bereiche wie "Mo-Fr", "Sa-So" oder "täglich"\n- Pro Tag Schaltpunkte als ["HH:MM", Temperatur]; eine Temperatur gilt ab ihrer Uhrzeit bis zum nächsten Punkt (auch über Mitternacht)\n- Temperatur in °C (5–30, halbe Grad) oder als Stufe Komfort (${lv.COMFORT == null ? 22 : lv.COMFORT} °C), Standard (${lv.ON == null ? 20 : lv.ON} °C), Eco (${lv.ECO == null ? 15 : lv.ECO} °C), Aus (${lv.OFF == null ? 6 : lv.OFF} °C)\n- Höchstens 24 Schaltpunkte pro Tag; mehrere Routinen in "routinen" sind möglich\n- Gibt es eine Routine mit gleichem Namen schon${info.routines.length ? ` (${info.routines.join(', ')})` : ''}, werden nur die genannten Räume/Tage ersetzt\n\nMeine Wünsche: `
    let checked = null
    const s = HO.sheet({ title: 'Heizpläne importieren', wide: true, body: `
      <p class="small muted" style="margin-top:0">JSON-Datei wählen oder Text einfügen. Räume werden über den Namen zugeordnet; vor dem Übernehmen siehst du, was passiert.</p>
      <label class="btn sm">${HO.icon('upload', 'sm')} Datei wählen<input type="file" accept=".json,application/json,text/plain" id="impFile" hidden></label>
      <label class="field"><span>Inhalt</span><textarea id="impText" rows="8" spellcheck="false" placeholder='{ "routinen": [ … ] }' style="font-family:ui-monospace,monospace;font-size:12px"></textarea></label>
      <details style="margin-top:10px"><summary class="small muted" style="cursor:pointer">Format &amp; Vorlage für eine KI</summary>
        <p class="small muted">Den Prompt kopieren, in ChatGPT/Claude o. Ä. einfügen, Wünsche ergänzen – die Antwort hier einfügen. Deine Räume sind schon eingetragen.</p>
        <pre class="tiny" style="white-space:pre-wrap;max-height:200px;overflow:auto;background:var(--bg-2);padding:10px;border-radius:10px">${HO.esc(JSON.stringify(example, null, 2))}</pre>
        <div class="row wrap"><button class="btn sm" data-imp="prompt">${HO.icon('copy', 'sm')} KI-Prompt kopieren</button>
          <button class="btn sm" data-imp="example">Beispiel einfügen</button>
          <button class="btn sm" data-imp="export">${HO.icon('download', 'sm')} Aktuelle Pläne exportieren</button></div></details>
      <div id="impResult" style="margin-top:14px"></div>
      <div class="form-actions"><button class="btn" data-imp="check">Prüfen</button><button class="btn primary" data-imp="apply" disabled>Übernehmen</button></div>` })
    const txt = s.el.querySelector('#impText'), out = s.el.querySelector('#impResult'), applyBtn = s.el.querySelector('[data-imp="apply"]')
    const invalidate = () => { checked = null; applyBtn.disabled = true }
    txt.addEventListener('input', invalidate)
    s.el.querySelector('#impFile').addEventListener('change', async e => {
      const f = e.target.files[0]; if (!f) return
      txt.value = await f.text(); invalidate(); check()
    })
    const result = r => r.routines.map(e => `<div class="li" style="display:block">
        <div class="row between"><b>${HO.esc(e.name)}</b><span class="badge ${e.errors.length ? 'bad' : e.action === 'create' ? 'accent' : 'warn'}">${e.errors.length ? 'Fehler' : e.action === 'create' ? 'wird angelegt' : 'wird ergänzt'}</span></div>
        ${e.rooms.map(x => `<div class="small">${HO.esc(x.name)}${HO.esc(x.file) !== HO.esc(x.name) ? ` <span class="muted">(„${HO.esc(x.file)}“)</span>` : ''} · <span class="muted">${x.days.join(', ')}</span></div>`).join('')}
        ${e.unmatched.length ? `<div class="small" style="color:var(--warn)">Kein passender Raum, wird übersprungen: ${e.unmatched.map(HO.esc).join(', ')}</div>` : ''}
        ${e.errors.map(x => `<div class="small" style="color:var(--bad)">${HO.esc(x)}</div>`).join('')}</div>`).join('')
    // Have pasted JSON checked by the server and show the result per routine.
    async function check() {
      if (!txt.value.trim()) { HO.toast('Bitte zuerst eine Datei wählen oder JSON einfügen.', 'warn'); return }
      try {
        const r = await HO.api('heating/import', { method: 'POST', body: txt.value, raw: 'application/json' })
        out.innerHTML = `<div class="list">${result(r)}</div>${r.ok ? '' : '<p class="small" style="color:var(--bad)">Bitte die Fehler korrigieren und erneut prüfen.</p>'}`
        checked = r.ok ? txt.value : null; applyBtn.disabled = !r.ok
      } catch (e) { out.innerHTML = `<div class="notice warn">${HO.icon('warn', 'sm')}<div>${HO.esc(e.message)}</div></div>`; invalidate() }
    }
    s.el.addEventListener('click', async e => {
      const b = e.target.closest('[data-imp]'); if (!b) return
      const act = b.dataset.imp
      try {
        if (act === 'prompt') {
          const done = await HO.copy(prompt)
          HO.toast(done ? 'Prompt kopiert – Wünsche ergänzen und an die KI schicken'
            : 'Kopieren nicht möglich – bitte den Text markieren.', done ? 'ok' : 'warn')
        }
        else if (act === 'example') { txt.value = JSON.stringify(example, null, 2); invalidate() }
        else if (act === 'export') downloadJSON(await HO.api('heating/export'), 'heizplaene.json')
        else if (act === 'check') await check()
        else if (act === 'apply' && checked) {
          b.disabled = true
          const r = await HO.api('heating/import?apply=true', { method: 'POST', body: checked, raw: 'application/json' })
          s.close()
          HO.syncToast(r.sync, `Importiert: ${r.done.map(d => d.name).join(', ')}`)
          await loadAll(); renderAll(page)
        }
      } catch (err) { HO.toast(err.message, 'bad', 6000); if (act === 'apply') b.disabled = false }
    })
  }

  // Dialog "Neue Heizroutine": name and optionally a template.
  function newRoutineSheet(page) {
    const rs = S.routines.routines
    HO.sheet({ title: 'Neue Heizroutine', body: `<label class="field"><span>Name</span><input type="text" id="nrName" maxlength="40" placeholder="z. B. Urlaub, Homeoffice"></label>
      <label class="field"><span>Vorlage</span><select id="nrFrom"><option value="">Leer beginnen</option>${rs.map(r => `<option value="${HO.esc(r.key)}" ${r.key === S.key ? 'selected' : ''}>Kopie von ${HO.esc(r.name)}</option>`).join('')}</select></label>
      ${S.routines.sync.configured ? '<p class="small muted" style="margin-top:12px">Wird automatisch auch in der Magenta-App angelegt.</p>' : ''}`,
    actions: [{ label: 'Abbrechen', kind: 'ghost' }, { label: 'Anlegen', kind: 'primary', onClick: async s => {
      const name = s.el.querySelector('#nrName').value.trim()
      if (!name) { HO.toast('Bitte einen Namen eingeben.', 'warn'); return true }
      const res = await HO.api('routines', { method: 'POST', body: { name, copy_from: s.el.querySelector('#nrFrom').value || null } })
      S.key = res.routine.key
      HO.syncToast(res.sync, `„${name}" angelegt`)
      await loadAll(); renderAll(page)
    } }] })
  }

  // Menu of the routine shown: rename, set as active, export, delete.
  function routineMenu(page) {
    const r = S.routine
    const reload = async () => { await loadAll(); renderAll(page) }
    HO.menu(r.name, [
      { label: 'Umbenennen', icon: 'edit', onClick: async () => {
        const n = await HO.prompt('Routine umbenennen', r.name); if (!n || n === r.name) return
        const res = await HO.api(`routines/${encodeURIComponent(r.key)}/name`, { method: 'PUT', body: { name: n } })
        HO.syncToast(res.sync, 'Umbenannt'); reload() } },
      { label: 'Duplizieren', icon: 'copy', onClick: async () => {
        const res = await HO.api('routines', { method: 'POST', body: { name: r.name + ' (Kopie)', copy_from: r.key } })
        S.key = res.routine.key; HO.syncToast(res.sync, 'Kopie angelegt'); reload() } },
      { label: 'Für „Zuhause" verwenden', icon: 'home', onClick: async () => {
        const res = await HO.api('home/mapping', { method: 'PUT', body: { present: r.key } })
        HO.syncToast(res.sync, `Zuhause → ${r.name}`); reload() } },
      { label: 'Für „Abwesend" verwenden', icon: 'away', onClick: async () => {
        const res = await HO.api('home/mapping', { method: 'PUT', body: { away: r.key } })
        HO.syncToast(res.sync, `Abwesend → ${r.name}`); reload() } },
      { label: 'Löschen', icon: 'trash', danger: true, onClick: async () => {
        if (!await HO.confirm(`„${r.name}" wirklich löschen?${S.routines.sync.configured ? ' Sie wird auch in der Magenta-App gelöscht.' : ''}`, { ok: 'Löschen', danger: true })) return
        try { const res = await HO.api(`routines/${encodeURIComponent(r.key)}`, { method: 'DELETE' }); HO.syncToast(res.sync, 'Gelöscht'); S.key = null; reload() }
        catch (e) { HO.toast(e.message, 'bad', 5000) } } }
    ])
  }

  HO.pages.heating = {
    title: 'Heizplan',
    // Open the page: draw at once from the last state, then refresh; set up the controls.
    async render(page) {
      const my = ++S.gen
      if (S.routines) {
        // page change: draw at once from the last state (no blank frame), then refresh
        renderAll(page)
        loadAll().then(() => { if (my === S.gen && !S.editorOpen) renderAll(page) }).catch(() => {})
      } else {
        page.innerHTML = '<div class="card"><div class="spinner"></div></div>'
        await loadAll()
        if (my !== S.gen) return   // Seite inzwischen verlassen
        renderAll(page)
      }
      // trigger the sync; if anything has changed, redraw
      HO.kickSync().then(async st => {
        if (my === S.gen && st.last_sync !== S.lastSync && !S.editorOpen && document.getElementById('page') === page) { await loadAll(); renderAll(page) }
      }).catch(() => {})
      S.timer = setInterval(async () => {
        if (document.hidden || S.editorOpen) return
        const st = await HO.api('routines').catch(() => null)
        if (st && (st.last_sync !== S.lastSync || st.pending !== S.routines.pending)) { await loadAll(); renderAll(page) }
        else if (st) { S.routines = st; const l = page.querySelector('.sync-line'); if (l) l.innerHTML = HO.syncLine(st.sync) }
      }, 20000)
      page.onclick = async e => {
        const t = e.target.closest('[data-r],[data-act],[data-edit],[data-copyroom],[data-hours],[data-hroom]')
        if (!t) return
        try {
          if (t.dataset.r) { S.key = t.dataset.r; await loadRoutine(); renderAll(page) }
          else if (t.dataset.edit) openEditor(page, t.dataset.edit, +t.dataset.day)
          else if (t.dataset.copyroom) copyRoomSheet(page, t.dataset.copyroom)
          else if (t.dataset.hours) { S.hours = +t.dataset.hours; S.hist = await HO.api('history/rooms?hours=' + S.hours); renderAll(page) }
          else if (t.dataset.hroom) { S.histRoom = t.dataset.hroom; renderAll(page) }
          else if (t.dataset.act === 'new') newRoutineSheet(page)
          else if (t.dataset.act === 'import') importSheet(page)
          else if (t.dataset.act === 'menu') routineMenu(page)
          else if (t.dataset.act === 'logSettings') logSettingsSheet()
          else if (t.dataset.act === 'activate') {
            const res = await HO.api(`routines/${encodeURIComponent(S.key)}/activate`, { method: 'POST' })
            HO.syncToast(res.sync, `„${S.routine.name}" aktiviert`); await loadAll(); renderAll(page)
          }
        } catch (err) { HO.toast(err.message, 'bad', 5000) }
      }
    },
    destroy() { S.gen++; clearInterval(S.timer); if (S.chart) S.chart.destroy(); S.chart = null }
  }
})(window.HO)
