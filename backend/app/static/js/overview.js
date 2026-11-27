/* ComfyOS – home page: home/away, key figures, floor plan, devices by room */
(function (HO) {
  'use strict'
  let fp = null, timer = null, editing = false, filter = 'all', gen = 0, heat = null
  let mode = 'home', peek = null, wxOpen = false   // mode: 'home' (Startseite) | 'plan' (Grundriss im Vollbild)
  let alive = false                                // home or full-screen page currently open
  let view = (() => { try { return localStorage.getItem('ho.view') || 'tiles' } catch (e) { return 'tiles' } })()
  /* Two views of the home page (since 0.24): 'classic' (floor plan, key figures, room tiles) and 'widgets' (the freely
     arranged widget view, widgets.js). The choice belongs to the device (localStorage ho.homeView) – a wall tablet can show
     widgets while the phone stays classic – and is still there after leaving the page or the app. */
  let homeView = (() => { try { return localStorage.getItem('ho.homeView') === 'widgets' ? 'widgets' : 'classic' } catch (e) { return 'classic' } })()
  /* Floor of the floor plan this device shows (since 0.25, localStorage ho.fpFloor): home page and full screen open on
     the floor shown last – a tablet on the upper floor stays there. An id that no longer exists shows the ground floor. */
  let planFloor = (() => { try { return localStorage.getItem('ho.fpFloor') || '' } catch (e) { return '' } })()
  let board = null, widgetDoc = null               // the mounted widget view and (as a promise) its stored pages (GET /api/widgets)

  const greeting = () => { const h = new Date().getHours(); return h < 11 ? 'Guten Morgen' : h < 18 ? 'Guten Tag' : 'Guten Abend' }
  let dateShown = ['', '']
  // the date in words – formatted once per day, not with every live message (since 0.30.3)
  const dateLong = () => {
    const now = new Date(), day = now.toDateString()
    if (dateShown[0] !== day) dateShown = [day, now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' })]
    return dateShown[1]
  }

  /* Home page from top to bottom (since 0.19.2): header without a tile with greeting and date (heroHTML, in
     #heroBox – like the heads of Flow, heating schedule and scenes; the connections are a pill in the top bar), then the main tile with the
     floor plan and below it home/away (homeHTML, #homeBox). Below the main tile: light in the whole house and the
     key figures (infoHTML). That way the floor plan is visible as completely as possible on opening. */
  function heroHTML(ov) {
    return `<div class="section-head home-head"><div><div class="eyebrow">${greeting()}</div><h2 style="margin:0">${dateLong()}</h2></div></div>`
  }
  // Title in the top bar: "ComfyOS" on the phone, greeting and date on the desktop (the sidebar already names ComfyOS)
  function setTitle() {
    const t = document.getElementById('pageTitle')
    if (!t || mode !== 'home') return
    const html = `<span class="pt-short">ComfyOS</span><span class="pt-long"><span class="pt-greet">${greeting()}</span>${dateLong()}</span>${HO.demoTitle()}`
    // written only on a change (it runs with every live message); another page may have written the title meanwhile
    if (t.__homeTitle === html && t.querySelector('.pt-long')) return
    t.innerHTML = html
    t.__homeTitle = html
  }
  // Block "Zuhause / Abwesend": the toggle and below it the active heating routine.
  function homeHTML(ov) {
    const home = ov.home || {}, rs = ov.routines || {}
    const active = (rs.routines || []).find(r => r.active)
    return `<div class="seg lg home-seg">
        <button data-home="PRESENT" class="${home.state !== 'AWAY' ? 'active' : ''}">${HO.icon('home')} Zuhause</button>
        <button data-home="AWAY" class="${home.state === 'AWAY' ? 'active' : ''}">${HO.icon('away')} Abwesend</button>
      </div>
      <div class="hero-sub">${active ? `Heizroutine: <b style="color:var(--text)">${HO.esc(active.name)}</b>` : 'Keine Heizroutine aktiv'}
        ${home.present_name || home.away_name ? ` · Zuhause → ${HO.esc(home.present_name || '–')}, Abwesend → ${HO.esc(home.away_name || '–')}` : ''}
        ${home.pending ? ' · <span style="color:var(--warn)">wird übertragen …</span>' : ''}</div>`
  }

  /* Below the main tile (two tiles since 0.19.2): "Licht im ganzen Haus" (house scene and all lights on/off) and
     the device status. The tile "Geräte online" carries the overall state ("Alles OK" or "Handlungsbedarf" with the
     reasons); with action needed, tapping leads to the devices with problems. */
  function infoHTML(ov) {
    return houseLightHTML(ov) + statsHTML(ov)
  }
  // Tile "Licht & Szenen" for the whole house: house scenes and "Alle Lichter an/aus" – only if there are lamps.
  function houseLightHTML(ov) {
    if (!ov.devices.some(d => d.kind === 'light')) return ''
    return `<section class="card house-light"><div class="card-head"><div><div class="eyebrow">Ganzes Haus</div><h3>Licht &amp; Szenen</h3></div>${HO.icon('light')}</div>
      ${houseScenesHTML(ov)}
      <div class="lights-bar">
        <button class="btn" data-lights="1">${HO.icon('light', 'sm')} Alle Lichter an</button>
        <button class="btn" data-lights="0">${HO.icon('power', 'sm')} Alle Lichter aus</button></div></section>`
  }
  // Key figures of the home: devices online (with action needed), average temperature, open windows, weak
  // batteries.
  function statsHTML(ov) {
    const d = ov.devices, iss = issuesOf(ov)
    const online = d.filter(x => x.online).length
    const open = d.filter(x => x.kind === 'contact' && x.open && x.online).length
    const low = d.filter(HO.lowBattery).length
    const temps = (ov.rooms || []).map(r => r.temperature).filter(v => v != null)
    const avg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null
    return `<section class="card hero-info">
      <div class="stats">
        <button type="button" class="stat stat-status ${iss.length ? 'warn' : ''}" data-act="issues" ${iss.length ? 'title="Geräte mit Problemen anzeigen"' : 'disabled'}>
          <span>Geräte online</span><b>${online}/${d.length}</b>
          <span class="stat-note">${HO.icon(iss.length ? 'warn' : 'check', 'sm')}${iss.length ? 'Handlungsbedarf' : 'Alles OK'}</span>
          ${iss.length ? `<span class="stat-issues">${iss.map(HO.esc).join(' · ')}</span>` : ''}</button>
        <div class="stat"><span>Ø Raumtemperatur</span><b>${HO.temp(avg)}</b></div>
        <div class="stat ${open ? 'warn' : ''}"><span>Fenster offen</span><b>${open}</b></div>
        <div class="stat ${low ? 'warn' : ''}"><span>Batterie schwach</span><b>${low}</b></div>
      </div></section>`
  }

  /* House scene: one scene for all rooms it is assigned to (each room with its variant).
     Active = exactly this scene runs in every assigned room with lamps. "Aus" ends all scenes. */
  function houseScenesHTML(ov) {
    const st = ov.scenes || { scenes: [], active: {} }, act = st.active || {}
    const lampRooms = new Set((ov.devices || []).filter(d => d.kind === 'light' && d.room_id).map(d => d.room_id))
    const list = (st.scenes || []).filter(s => s.rooms && s.rooms.some(r => lampRooms.has(r))).sort((a, b) => HO.byName(a.name, b.name))
    if (!list.length) return ''
    const isOn = s => s.rooms.filter(r => lampRooms.has(r)).every(r => act['room:' + r] && act['room:' + r].scene === s.id)
    const any = Object.keys(act).length
    return `<div class="house-scenes"><div class="eyebrow">Szene fürs ganze Haus</div>
      <div class="chips scroll sc-row" data-sc-row>
        <button type="button" class="chip ${any ? '' : 'active'}" data-house-off title="Alle laufenden Szenen beenden – das Licht wird wie vorher">Keine</button>
        ${list.map(s => `<button type="button" class="chip sc-chip ${isOn(s) ? 'active' : ''}" data-house="${HO.esc(s.id)}" title="In ${s.rooms.length} Raum/Räumen setzen">
          <span class="sc-dot" style="background:${HO.paletteGradient(s.palette)}"></span>${HO.esc(s.name)}${s.dynamic ? HO.icon('sparkle', 'sm') : ''}</button>`).join('')}</div></div>`
  }

  // Overall state: reasons for "Handlungsbedarf" in the tile "Geräte online" (infoHTML)
  function issuesOf(ov) {
    const d = ov.devices || [], out = []
    const n = (k, one, many) => `${k} ${k === 1 ? one : many}`
    const off = d.filter(x => !x.online).length, low = d.filter(HO.lowBattery).length
    if (off) out.push(n(off, 'Gerät offline', 'Geräte offline'))
    if (low) out.push(n(low, 'Batterie schwach', 'Batterien schwach'))
    if (ov.devices_error) out.push('Geräteliste unvollständig')
    if (ov.sync && ov.sync.configured && ov.sync.ok === false) out.push('Magenta-Sync gestört')
    if (ov.home && ov.home.state === 'AWAY') {
      const open = d.filter(x => x.kind === 'contact' && x.open && x.online).length
      if (open) out.push(n(open, 'Fenster offen bei Abwesenheit', 'Fenster offen bei Abwesenheit'))
    }
    const alarm = d.filter(x => x.alarm === true).length
    if (alarm) out.unshift('Rauchmelder meldet Alarm')
    const err = d.filter(x => x.online && x.errors && x.errors.length).length
    if (err) out.push(n(err, 'Gerät meldet einen Fehler', 'Geräte melden Fehler'))
    return out
  }
  /* ---------- Weather for the address (the backend asks Open-Meteo only with postcode/town, caches 15 min) */
  const wxNow = () => { const c = HO.state.weather; return c && c.data && c.data.available ? c.data : null }
  const wxFx = () => { const w = wxNow(); return (w && w.fx) || null }
  // Preview of the weather effects by URL (#home?fx=thunder) – owner and development mode only
  function fxFromHash() {
    const lic = HO.license || {}, m = /[?&]fx=([a-z]+)/.exec(HO.hashParams || '')
    if (!(lic.licensed_plan === 'owner' || lic.dev)) return
    if (m && HO.weatherFx && HO.weatherFx.KINDS.indexOf(m[1]) >= 0) HO.state.fxPreview = m[1]
    const tm = /[?&]time=(day|dusk|night)\b/.exec(HO.hashParams || '')   // Tageszeit der Vorschau: #home?fx=clear&time=night
    if (tm) HO.state.fxTime = tm[1]
  }
  // Load the weather; a state younger than ten minutes is reused. null on an error.
  async function loadWeather(force) {
    const c = HO.state.weather
    if (!force && c && Date.now() - c.at < 10 * 60e3) return c.data
    try { const d = await HO.api('weather'); HO.state.weather = { at: Date.now(), data: d }; return d } catch (e) { return null }
  }
  // check regularly (the backend caches anyway); redrawn only if really new data arrived
  function pollWeather(my) {
    const before = HO.state.weather && HO.state.weather.at
    return loadWeather().then(() => { if (my === gen && (HO.state.weather && HO.state.weather.at) !== before) updateWeather() })
  }
  const wxTemp = v => v == null ? '–' : `${Math.round(v)}°`

  /* ---------- Official weather warnings (Deutscher Wetterdienst; come with /api/weather): hint at the very top of the
     home page, with a view to the window contacts. Hiding is only remembered by this device (until the warning changes). */
  const WARN_LEVELS = ['', 'Wetterwarnung', 'Markantes Wetter', 'Unwetter', 'Extremes Unwetter']
  const wxWarnings = () => { const c = HO.state.weather; return (c && c.data && c.data.warnings) || [] }
  const warnSource = () => { const c = HO.state.weather; return (c && c.data && c.data.warnings_source) || 'Deutscher Wetterdienst' }
  const warnLevel = w => Math.max(1, Math.min(4, +w.level || 1))
  function warnHidden() { try { const v = JSON.parse(localStorage.getItem('ho.warnHide') || '[]'); return Array.isArray(v) ? v : [] } catch (e) { return [] } }
  function warnWhen(ts) {
    const d = new Date(ts * 1000), time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) + ' Uhr'
    return d.toDateString() === new Date().toDateString() ? time : d.toLocaleDateString('de-DE', { weekday: 'short' }) + ' ' + time
  }
  const warnSpan = w => [w.onset ? (w.active ? 'seit ' : 'ab ') + warnWhen(w.onset) : '', w.expires ? 'bis ' + warnWhen(w.expires) : ''].filter(Boolean).join(' · ')
  // Windows closed? With contacts the hint names the open ones, without it stays general.
  function windowTipHTML(ov, w) {
    const cons = (ov.devices || []).filter(d => d.kind === 'contact' && d.online), open = cons.filter(d => d.open)
    const lead = w.active ? 'Fenster schließen' : 'Fenster rechtzeitig schließen'
    if (open.length) {
      return `<b>${lead}:</b> ${open.length} offen – ${HO.esc(open.slice(0, 4).map(d => d.name).join(', '))}${open.length > 4 ? ' …' : ''}`
    }
    if (cons.length) return `Fenster und Türen geschlossen halten – ${cons.length === 1 ? 'der Kontakt meldet' : 'alle ' + cons.length + ' Kontakte melden'} „zu“.`
    return `<b>${lead}</b> und Türen geschlossen halten.`
  }
  const warnTipHTML = (ov, w, open) => `<div class="ww-tip ${open ? 'open' : ''}">${HO.icon('contact', 'sm')}<span>${windowTipHTML(ov, w)}</span></div>`
  const anyOpen = ov => (ov.devices || []).some(d => d.kind === 'contact' && d.online && d.open)
  // Hint about the most important official weather warning (without those hidden by the user); empty if there is none.
  function warnHTML(ov) {
    const hid = warnHidden(), list = wxWarnings().filter(w => hid.indexOf(w.id) < 0)
    if (!list.length) return ''
    const w = list[0], lv = warnLevel(w), more = list.length - 1, tip = list.find(x => x.windows)
    return `<section class="wx-warn ${lv >= 3 ? 'severe' : ''}" role="status">
      <div class="ww-ic">${HO.icon('warn')}</div>
      <div class="ww-main"><div class="ww-head"><b>${HO.esc(w.headline)}</b><span class="badge ${lv >= 3 ? 'bad' : 'warn'}">Stufe ${lv} von 4</span></div>
        <div class="ww-sub">${HO.esc([warnSpan(w), warnSource()].filter(Boolean).join(' · '))}${more ? ` · <button class="link-btn" data-act="warn">+ ${more} weitere</button>` : ''}</div>
        ${tip ? warnTipHTML(ov, tip, anyOpen(ov)) : ''}</div>
      <div class="ww-act"><button class="btn sm" data-act="warn">Details</button>
        <button class="btn-icon sm plain" data-act="warnHide" aria-label="Warnung ausblenden" title="Ausblenden, bis sich die Warnung ändert">${HO.icon('x', 'sm')}</button></div></section>`
  }
  // small sign next to the weather: leads to the warnings, even if the hint at the top is hidden
  function warnChipHTML() {
    const list = wxWarnings()
    if (!list.length) return ''
    const lv = Math.max.apply(null, list.map(warnLevel))
    return `<button class="status-pill fp-warn ${lv >= 3 ? 'severe' : 'warn'}" data-act="warn" title="Amtliche Wetterwarnung für deinen Ort">${HO.icon('warn', 'sm')}<b>${list.length === 1 ? 'Wetterwarnung' : list.length + ' Wetterwarnungen'}</b></button>`
  }
  // Dialog with all current weather warnings including period and level.
  function warnSheet() {
    const ov = HO.state.overview || {}, list = wxWarnings()
    return HO.sheet({ title: 'Wetterwarnungen', body: (list.length ? list.map(w => {
      const lv = warnLevel(w)
      return `<div class="ww-item ${lv >= 3 ? 'severe' : ''}">
        <div class="ww-head"><b>${HO.esc(w.headline)}</b><span class="badge ${lv >= 3 ? 'bad' : 'warn'}">Stufe ${lv} · ${WARN_LEVELS[lv]}</span></div>
        <div class="ww-sub">${HO.esc(warnSpan(w))}</div>
        ${w.description ? `<p>${HO.esc(w.description)}</p>` : ''}${w.instruction ? `<p class="small muted">${HO.esc(w.instruction)}</p>` : ''}
        ${w.windows ? warnTipHTML(ov, w, anyOpen(ov)) : ''}</div>`
    }).join('') : '<p class="muted">Für deinen Ort liegt gerade keine Warnung vor.</p>')
      + `<p class="tiny muted" style="margin-top:14px">Quelle: ${HO.esc(warnSource())}, amtliche Warnungen für deinen Ort. Übertragen werden nur die gerundeten Koordinaten.
        Abschaltbar im Grundriss unter „Bearbeiten“.</p>` })
  }
  // Hide the current weather warnings (remembered on this device); the warning sign stays.
  function hideWarnings() {
    try { localStorage.setItem('ho.warnHide', JSON.stringify(wxWarnings().map(w => w.id))) } catch (e) { }
    renderWarn()
    HO.toast('Ausgeblendet – das Warnzeichen neben dem Wetter bleibt.')
  }
  let warnShown = null
  // Rewrite the warning hint – only if it has changed.
  function renderWarn() {
    const box = document.getElementById('warnBox'), ov = HO.state.overview
    if (!box || !ov) return
    const html = warnHTML(ov)
    if (html !== warnShown) { warnShown = html; box.innerHTML = html }
  }
  function wxInline() {
    const w = wxNow()
    return w ? `<button class="fp-wx" data-act="wx" title="Außen in ${HO.esc(w.place)}: ${HO.esc(w.label)} – antippen für Wind und Stundenprognose">${HO.icon('wx-' + w.icon, 'sm')}<b>${wxTemp(w.temperature)}</b><span class="muted">${HO.esc(w.label)}</span></button>` : ''
  }

  /* ---------- Weather today: wind report and hourly forecast (Pro, together with the weather effects – floorplan_weather).
     Everything from the same weather query (wind_report, today); nothing is loaded extra. */
  function windHTML(w) {
    const r = w.wind_report
    if (!r) return ''
    // Pfeil zeigt, wohin der Wind weht (Richtung = woher er kommt, also um 180° gedreht)
    const arrow = r.direction == null ? '' : `<svg class="wx-arrow" viewBox="0 0 24 24" style="transform:rotate(${(r.direction + 180) % 360}deg)" aria-hidden="true"><path d="M12 20V4M6 10l6-6 6 6"/></svg>`
    return `<div class="wx-wind">${arrow}<div><b>${r.speed} km/h</b>${r.compass ? ' aus ' + HO.esc(r.compass) : ''} · ${HO.esc(r.label)} <span class="muted">(Stärke ${r.beaufort})</span>
      ${r.gusts != null ? `<div class="small muted">Böen bis ${r.gusts} km/h</div>` : ''}</div></div>`
  }
  // Hourly forecast as a horizontal row; a change of day is marked.
  function hoursHTML(w) {
    const hours = w.today || []
    if (!hours.length) return ''
    let day = hours[0].day
    return `<div class="wx-hours" role="list">${hours.map(h => {
      const next = h.day !== day
      day = h.day
      const pop = h.pop == null ? null : Math.max(0, Math.min(100, Math.round(h.pop)))
      return `${next ? '<div class="wx-daysep"><span>morgen</span></div>' : ''}<div class="wx-hour" role="listitem" title="${HO.esc(h.label || '')}">
        <span class="wx-h-time">${HO.esc(h.time.slice(0, 2))} Uhr</span>${HO.icon('wx-' + h.icon, 'sm')}<b>${wxTemp(h.temperature)}</b>
        <span class="wx-h-pop" title="Regenwahrscheinlichkeit"><i style="height:${pop == null ? 0 : pop}%"></i></span>
        <span class="wx-h-sub">${pop == null ? '–' : pop + ' %'}</span>
        <span class="wx-h-sub">${h.wind == null ? '' : Math.round(h.wind) + ' km/h'}</span></div>`
    }).join('')}</div>`
  }
  const wxFactsHTML = w => `<dl>
    ${w.apparent != null ? `<dt>Gefühlt</dt><dd>${wxTemp(w.apparent)}</dd>` : ''}
    ${w.min != null && w.max != null ? `<dt>Heute</dt><dd>${wxTemp(w.min)} / ${wxTemp(w.max)}</dd>` : ''}
    ${w.humidity != null ? `<dt>Luftfeuchte</dt><dd>${Math.round(w.humidity)} %</dd>` : ''}
    ${!w.wind_report && w.wind != null ? `<dt>Wind</dt><dd>${Math.round(w.wind)} km/h</dd>` : ''}</dl>`
  // wind and hours (Pro) or the hint about it
  function wxProHTML(w) {
    if (!HO.can('floorplan_weather')) {
      return `<div class="notice" style="margin-top:12px">${HO.icon('cloud', 'sm')}<div>Windbericht und Stundenprognose gehören mit den Wettereffekten zu ComfyOS Pro.</div></div>`
    }
    return `${windHTML(w)}${(w.today || []).length ? `<div class="eyebrow" style="margin:14px 0 8px">Die nächsten Stunden</div>${hoursHTML(w)}
      <div class="tiny muted" style="margin-top:6px">Balken = Regenwahrscheinlichkeit, darunter der Wind</div>` : ''}`
  }
  // Dialog "Wetter": current values, wind and hours (Pro), origin of the data.
  function weatherSheet() {
    const w = wxNow()
    if (!w) return
    return HO.sheet({ title: `Wetter in ${w.place}`, wide: true, body: `<div class="wx-now">${HO.icon('wx-' + w.icon, 'lg')}
        <div><b>${wxTemp(w.temperature)}</b><span>${HO.esc(w.label)}</span></div><div class="wx-facts">${wxFactsHTML(w)}</div></div>
      ${wxProHTML(w)}
      <p class="tiny muted" style="margin-top:14px">Open-Meteo · übertragen wird nur der Ort${wxWarnings().length ? ' · Warnungen: ' + HO.esc(warnSource()) : ''}</p>` })
  }

  // Block below the floor plan: address, weather and warning sign, plus the hint about room changes not transferred yet
  // and the power line.
  function footHTML(ov) {
    // address; weather and warning sign in a group of their own – on the phone they share the line below the address
    const addr = (ov.floorplan && ov.floorplan.address) || '', warnChip = addr ? warnChipHTML() : '', wx = addr ? wxInline() : ''
    return `<div class="fp-foot">
      <div class="fp-loc"><div class="fp-addr">${addr ? `${HO.icon('pin', 'sm')}<span>${HO.esc(addr)}</span>` : `<span class="muted small">${editing ? '' : 'Adresse im Grundriss unter „Bearbeiten" eintragen'}</span>`}</div>
        ${wx || warnChip ? `<div class="fp-wxrow">${wx}${warnChip ? `<div class="fp-pills">${warnChip}</div>` : ''}</div>` : ''}</div>
      ${ov.room_pending && ov.room_pending.count ? `<button class="fp-pending small" data-act="roomPending">${HO.icon('cloud', 'sm')}
        ${ov.room_pending.count} Raumänderung${ov.room_pending.count === 1 ? '' : 'en'} noch nicht übertragen (${HO.esc(hubName())})</button>` : ''}
      ${powerBarHTML(ov)}</div>`
  }

  /* ---------- Power consumption below the floor plan: power now (live from ov.devices) and consumption today */
  const watt = v => v == null ? '–' : (v >= 1000 ? (v / 1000).toFixed(2).replace('.', ',') + ' kW' : HO.num(v) + ' W')
  const kwhText = v => v == null ? '–' : (v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : String(Math.round(v))).replace('.', ',') + ' kWh'
  const measuring = d => d.power_w != null || d.energy_kwh != null
  const sinceText = ts => ts ? new Date(ts * 1000).toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: new Date(ts * 1000).getFullYear() === new Date().getFullYear() ? undefined : 'numeric' }) : '–'
  // Line "Stromverbrauch": current power of all metering devices and today's consumption; empty if nobody meters.
  function powerBarHTML(ov) {
    const rows = (ov.devices || []).filter(measuring)
    if (!rows.length) return ''   // nobody meters – then no empty display either
    const live = rows.filter(d => d.online && d.power_w != null)
    const total = live.length ? Math.round(live.reduce((a, d) => a + d.power_w, 0) * 10) / 10 : null
    const active = live.filter(d => d.power_w >= 1).length
    const today = ov.power && ov.power.kwh_today != null ? ov.power.kwh_today : null
    return `<button class="power-bar" data-act="power" title="Stromverbrauch im Detail">
      <span class="pb-ic">${HO.icon('bolt', 'sm')}</span>
      <span class="pb-main"><span class="pb-now">${watt(total)}</span>
        <span class="pb-sub">${today != null ? `Heute ${kwhText(today)} · ` : ''}${active ? `${active} von ${rows.length} aktiv` : `${rows.length} Messgerät${rows.length === 1 ? '' : 'e'}`}</span></span>
      ${HO.icon('chevron', 'sm')}</button>`
  }

  // Dialog "Stromverbrauch": key figures, history as a chart (Pro) and the devices by power; "Zurücksetzen" starts
  // the counting anew.
  async function powerSheet() {
    const canGraph = HO.can('energy_analytics')
    const s = HO.sheet({ title: 'Stromverbrauch', wide: true, body: `<div class="spinner"></div>` })
    let chart = null, rows = []
    // Load key figures and device list and write them into the dialog.
    const draw = async () => {
      let data
      try { data = await HO.api('power') } catch (e) { s.body().innerHTML = `<p class="notice bad">${HO.esc(e.message)}</p>`; return }
      rows = data.rows || []
      const live = rows.filter(r => r.online && r.power_w != null)
      const est = data.estimated ? ' ≈' : ''
      s.body().innerHTML = `<div class="pw-stats">
          <div class="pw-stat"><span>Leistung jetzt</span><b>${watt(data.total_w)}</b><small>${data.active || 0} von ${rows.length} aktiv</small></div>
          <div class="pw-stat"><span>Verbrauch seit ${HO.esc(sinceText(data.since))}</span><b>${kwhText(data.kwh_since_reset)}${est}</b>
            <small><button class="link-btn" data-act="pwReset">Zurücksetzen</button></small></div>
          <div class="pw-stat"><span>Heute</span><b>${kwhText(data.kwh_today)}${est}</b><small>seit 0:00 Uhr</small></div>
        </div>
        <div class="list" style="margin-top:12px">${rows.map(r => `<div class="dev-row ${r.online ? '' : 'off'}" data-dev="${HO.esc(r.id)}">
          <div class="dev-ic ${r.online ? (r.on ? 'on' : '') : 'off'}">${HO.icon(r.kind || 'switch')}</div>
          <div class="r-main"><div class="r-name">${HO.esc(r.name)}</div>
            <div class="r-state">${HO.esc(r.room || 'Nicht zugeordnet')}${r.kwh_since_reset != null ? ` · seit ${HO.esc(sinceText(data.since))} ${r.estimated ? '≈ ' : ''}${HO.esc(kwhText(r.kwh_since_reset))}` : ''}</div></div>
          <b class="pw-val">${r.online ? watt(r.power_w) : 'offline'}</b></div>`).join('')
          || '<p class="muted">Kein Gerät meldet gerade Verbrauch.</p>'}</div>
        <p class="tiny muted">Gezeigt wird, was die Geräte selbst messen. Mit Zähler ist der Verbrauch exakt; meldet ein Gerät nur die Leistung,
          schätzt ComfyOS den Verbrauch aus Leistung × Zeit${data.estimated ? ' (mit ≈ markiert)' : ''}. Zeiten, in denen ComfyOS aus war, zählen dabei nicht.</p>
        <div class="section-head" style="margin-top:16px;flex-wrap:wrap;gap:8px"><h3 style="font-size:15px">Verlauf ${HO.proTag('energy_analytics')}</h3>
          ${canGraph ? `<div class="row wrap g8"><div class="seg sm">${[['sum', 'Alle zusammen'], ['each', 'Pro Gerät']].map(([k, l]) =>
            `<button data-pwmode="${k}" class="${k === pwMode ? 'active' : ''}">${l}</button>`).join('')}</div>
            <div class="seg sm">${[[24, '24 h'], [168, '7 T'], [744, '31 T']].map(([h, l]) =>
            `<button data-pwh="${h}" class="${h === pwHours ? 'active' : ''}">${l}</button>`).join('')}</div></div>` : ''}</div>
        <div id="pwChart"></div>
        ${canGraph ? `<div class="row" style="margin-top:10px"><button class="btn sm" data-act="pwExport">${HO.icon('download', 'sm')} Als CSV speichern</button></div>`
          : `<div class="notice" style="margin-top:8px">${HO.icon('bolt', 'sm')}<div>Der Verlauf als Graph und der CSV-Export gehören zu ComfyOS Pro.</div></div>`}`
      if (canGraph) drawChart()
    }
    // Load and draw the history – in total or per device, for the chosen period.
    const drawChart = async () => {
      const box = s.el.querySelector('#pwChart')
      if (!box) return
      box.innerHTML = '<div class="spinner"></div>'
      // per device: colours follow the device (sorted by name), not its rank – at most 8 series
      const ids = pwMode === 'each' ? rows.filter(r => r.power_w != null).sort((a, b) => HO.byName(a.name, b.name)).map(r => r.id).slice(0, 8) : []
      try {
        const ser = await HO.api(`power/series?hours=${pwHours}${ids.map(id => '&device_id=' + encodeURIComponent(id)).join('')}`)
        box.innerHTML = ''
        if (chart) chart.destroy()
        const series = ids.length ? (ser.series || []).map(x => Object.assign({}, x, { color: `var(--series-${ids.indexOf(x.id) + 1})` })) : null
        chart = HO.powerChart(box, ser.points, { hours: pwHours, series, hidden: pwHidden, onToggle: h => { pwHidden = h } })
        const foot = document.createElement('div')
        foot.className = 'small muted'
        foot.style.marginTop = '8px'
        const more = pwMode === 'each' && rows.filter(r => r.power_w != null).length > 8 ? ' · die 8 ersten Geräte nach Name' : ''
        foot.textContent = `Mittel ${HO.num(ser.avg_w)} W · Spitze ${HO.num(ser.peak_w)} W · ${String(ser.kwh).replace('.', ',')} kWh im Zeitraum (aus der Leistung geschätzt)${more}`
        box.appendChild(foot)
      } catch (e) {
        box.innerHTML = ''
        if (e.status === 402) HO.upsell(e.detail)
        else box.innerHTML = `<p class="small muted">${HO.esc(e.message)}</p>`
      }
    }
    s.el.addEventListener('click', async e => {
      const h = e.target.closest('[data-pwh]'), md = e.target.closest('[data-pwmode]'), ex = e.target.closest('[data-act="pwExport"]')
      const rs = e.target.closest('[data-act="pwReset"]'), row = e.target.closest('[data-dev]')
      if (h || md) {
        if (h) pwHours = +h.dataset.pwh
        if (md) pwMode = md.dataset.pwmode
        s.el.querySelectorAll('[data-pwh]').forEach(b => b.classList.toggle('active', +b.dataset.pwh === pwHours))
        s.el.querySelectorAll('[data-pwmode]').forEach(b => b.classList.toggle('active', b.dataset.pwmode === pwMode))
        return drawChart()
      }
      if (ex) { location.href = `/api/power/export?hours=${pwHours}`; return }
      if (rs) {
        if (!await HO.confirm('„Verbrauch seit …“ auf 0 setzen? Ab jetzt zählt ComfyOS neu. Der Verlauf bleibt erhalten.', { title: 'Verbrauch zurücksetzen', ok: 'Zurücksetzen' })) return
        try { await HO.api('power/reset', { method: 'POST' }); HO.toast('Verbrauch zurückgesetzt') } catch (err) { HO.toast(err.message, 'bad') }
        return draw()
      }
      if (row) { s.close(); const d = HO.state.overview.devices.find(x => x.id === row.dataset.dev); if (d) deviceSheet(d) }
    })
    draw()
    return s
  }
  let pwHours = 24, pwMode = 'sum', pwHidden = new Set()

  // Device tile in a room tile: icon, name, state and the fitting control (switch, cover, temperature) –
  // locked devices greyed out.
  function tileHTML(d) {
    const cls = HO.devClass(d)
    let act = ''
    if (d.online && d.control_item && d.locked) act = HO.lockedBtn(d)   // gesperrt: ausgegraut, Antippen zeigt den Hinweis
    else if (d.online && d.control_item) {
      if (d.kind === 'cover') act = `<div class="t-act cover">
        <button class="btn-icon sm up" data-cmd="cover" data-v="up" data-id="${HO.esc(d.id)}" aria-label="Öffnen" title="Öffnen">${HO.icon('chevron', 'sm')}</button>
        <button class="btn-icon sm" data-cmd="cover" data-v="stop" data-id="${HO.esc(d.id)}" aria-label="Anhalten" title="Anhalten">${HO.icon('pause', 'sm')}</button>
        <button class="btn-icon sm down" data-cmd="cover" data-v="down" data-id="${HO.esc(d.id)}" aria-label="Schließen" title="Schließen">${HO.icon('chevron', 'sm')}</button></div>`
      else if (d.kind === 'thermostat') act = `<div class="t-act"><button class="btn-icon sm" data-cmd="delta" data-v="-0.5" data-id="${HO.esc(d.id)}" aria-label="Kälter">${HO.icon('minus', 'sm')}</button><button class="btn-icon sm" data-cmd="delta" data-v="0.5" data-id="${HO.esc(d.id)}" aria-label="Wärmer">${HO.icon('plus', 'sm')}</button></div>`
      else if (d.kind === 'light' || d.kind === 'switch') act = `<button class="btn-icon sm ${d.on ? 'on' : ''} ${d._busy ? 'busy' : ''}" data-cmd="toggle" data-on="${d.on ? 0 : 1}" data-id="${HO.esc(d.id)}" aria-pressed="${!!d.on}" aria-label="${d.on ? 'Ausschalten' : 'Einschalten'}">${HO.icon('power', 'sm')}</button>`
      else if (HO.caps.has(d, 'playing', true)) act = `<button class="btn-icon sm ${d.playing ? 'on' : ''}" data-media-act="playing" data-on="${d.playing ? 0 : 1}" data-id="${HO.esc(d.id)}" aria-pressed="${!!d.playing}" aria-label="${d.playing ? 'Pause' : 'Wiedergabe'}">${HO.icon(d.playing ? 'pause' : 'play', 'sm')}</button>`
    }
    const lit = d.on || HO.playingNow(d)     // a speaker that plays stands out like a lamp that is on
    if (view === 'list') {
      return `<div class="dev-row ${d.online ? '' : 'off'} ${lit && d.online ? 'is-on' : ''}" data-dev="${HO.esc(d.id)}">
        <div class="dev-ic ${cls}" ${lampTint(d)}>${HO.icon(d.kind)}</div>
        <div class="r-main"><div class="r-name">${HO.esc(d.name)}</div><div class="r-state">${HO.esc(HO.devState(d))}</div></div>
        ${HO.devIssues(d).map(i => `<span class="t-flag ${i.k}" title="${HO.esc(i.t)}">${HO.icon(i.k === 'battery' ? 'battery' : i.k === 'offline' ? 'offline' : 'warn', 'sm')}</span>`).join('')}${act}</div>`
    }
    const iss = HO.devIssues(d)
    // lock only once: where the greyed-out switch with a lock is shown (HO.lockedBtn), no second mark is needed
    const lockFlag = d.locked && !(d.online && d.control_item) ? `<span class="t-flag lock" title="${HO.LOCK_HINT}">${HO.icon('lock', 'sm')}</span>` : ''
    return `<div class="tile ${d.online ? '' : 'off'} ${lit ? 'is-on' : ''} ${d.locked ? 'is-locked' : ''} ${iss.some(i => i.k === 'error' || i.k === 'alarm') ? 'has-error' : ''}" data-dev="${HO.esc(d.id)}">
      ${iss.length || lockFlag ? `<span class="t-flags">${lockFlag}${iss.map(i => `<span class="t-flag ${i.k}" title="${HO.esc(i.t)}">${HO.icon(i.k === 'battery' ? 'battery' : i.k === 'offline' ? 'offline' : 'warn', 'sm')}</span>`).join('')}</span>` : ''}
      <div class="t-top"><div class="dev-ic ${cls}" ${lampTint(d)}>${HO.icon(d.kind)}</div></div>
      <div class="t-name">${HO.esc(d.name)}</div>
      ${iss.filter(i => i.k === 'error' || i.k === 'alarm').map(i => `<div class="t-issue">${HO.esc(i.t)}</div>`).join('')}
      <div class="row between" style="margin-top:auto"><div class="t-state">${HO.esc(HO.devState(d))}</div>${act}</div></div>`
  }

  // Display name of a plugin (from the status line of the overview), otherwise its id
  const pluginName = id => { const p = ((HO.state.overview || {}).integrations || []).find(x => x.id === id); return p ? p.name : id }
  const lampTint = d => { const c = d.light ? HO.lightColors(d) : []; return c.length ? `style="color:rgb(${c[0].join(',')})"` : '' }
  // Background behind the room name: current light of the room, blurred
  const glowHTML = (devs, cls = 'rg-glow') => { const g = HO.lightGlow(devs); return g ? `<div class="${cls}" style="background:${g}"></div>` : '' }
  const sceneTag = key => { const sc = key && HO.lights.activeScene('room:' + key); return sc ? `<span class="rg-scene">${HO.icon('palette', 'sm')}${HO.esc(sc.name)}</span>` : '' }

  // Switchable lamps (sockets are left out of room/all switches)
  const switchable = d => d.kind === 'light' && HO.canControl(d)
  // Switch "all lamps in the room" in the head of a room tile; empty if the room has no switchable lamp.
  function roomSwitchHTML(n, all) {
    if (n === 'Nicht zugeordnet') return ''
    const lights = all.filter(d => d.room === n && d.room_id && switchable(d))
    if (!lights.length) return ''
    const on = lights.some(d => d.on), ids = [...new Set(lights.map(d => d.room_id))]
    return `<button class="room-switch ${on ? 'on' : ''}" role="switch" aria-checked="${on}" data-room-lights="${HO.esc(JSON.stringify(ids))}" data-on="${on ? 0 : 1}"
      data-name="${HO.esc(n)}" title="${on ? 'Alle Lampen im Raum ausschalten' : 'Alle Lampen im Raum einschalten'}" aria-label="Licht ${HO.esc(n)}"></button>`
  }

  /* Block "Räume und Geräte": room tiles by the chosen filter, per room temperature, light and device tiles.
     roomCards() returns the tiles in the order of the page as [id of the tile, HTML]. Since 0.30.3 the home page
     remembers what it wrote per tile (roomsDrawn, roomsKey): a change rewrites only the tiles it touches (patchRooms)
     – before, one lamp that switched rewrote every room and made the tile grid measure and distribute all of them. */
  let roomsDrawn = {}, roomsKey = ''
  // The room tiles of the page in their order: [id of the tile, its HTML].
  function roomCards(ov) {
    const match = d => filter === 'all' ? true : filter === 'heat' ? ['thermostat', 'sensor'].includes(d.kind)
      : filter === 'power' ? ['light', 'switch', 'media'].includes(d.kind) : filter === 'contact' ? ['contact', 'motion', 'smoke', 'cover', 'sensor', 'presence'].includes(d.kind)
        : HO.devIssues(d).length > 0
    const rooms = Object.fromEntries((ov.rooms || []).filter(r => r.room_id).map(r => [r.room_id, r]))
    const groups = {}
    for (const d of ov.devices.filter(match)) (groups[d.room] = groups[d.room] || { room_id: d.room_id, items: [] }).items.push(d)
    // Default order (without an own arrangement): earlier sorting from rooms.json, then alphabetical,
    // "Nicht zugeordnet" last. How the tiles stand is set by "Anordnen" since 0.19.3 (HO.tileGrid, page "home").
    const order = ov.room_order || []
    const rank = n => { const i = order.indexOf(groups[n].room_id); return i < 0 ? order.length : i }
    const names = Object.keys(groups).sort((a, b) => (a === 'Nicht zugeordnet') - (b === 'Nicht zugeordnet')
      || rank(a) - rank(b) || HO.byName(a, b))
    return names.map(n => [roomTileId(groups[n].room_id), roomCardHTML(n, groups[n], ov, rooms[groups[n].room_id])])
  }
  const ROOMS_EMPTY = `<div class="card empty">${HO.icon('device')}<div>Keine Geräte in dieser Auswahl.</div></div>`
  // The whole block as HTML (first drawing of the page, and whenever the set of tiles changes); remembers every tile.
  function devicesHTML(ov, cards) {
    cards = cards || roomCards(ov)
    roomsDrawn = {}
    for (const [id, html] of cards) roomsDrawn[id] = html
    roomsKey = cards.map(c => c[0]).join('|')
    return cards.map(c => c[1]).join('') || ROOMS_EMPTY
  }
  // Bring the block up to date: only the tiles whose content changed are replaced, in their place in the grid – the
  // others, their scroll positions and the distribution over the columns stay untouched. A tile the user tapped or
  // moved a slider in is written in any case (its state no longer comes from the HTML written last). If rooms were
  // added, removed or reordered, the block is written as a whole and the grid distributes anew.
  function patchRooms(box, ov) {
    if (!box) return false
    if (!box.__roomsWatched) {
      box.__roomsWatched = true
      const touched = e => { const card = e.target.closest && e.target.closest('.room-card'); if (card) card.__touched = true }
      ;['click', 'input', 'change'].forEach(type => box.addEventListener(type, touched, true))
    }
    const cards = roomCards(ov), key = cards.map(c => c[0]).join('|')
    const els = cards.map(c => document.getElementById(c[0]))
    const same = key === roomsKey && new Set(cards.map(c => c[0])).size === cards.length && els.every(el => el && box.contains(el))
    if (!same) {
      box.__html = null      // the block as a whole, in any case: single tiles may have been replaced since
      HO.setHTML(box, devicesHTML(ov, cards))
      if (rgrid) rgrid.refresh()
      return true
    }
    let wrote = false
    cards.forEach(([id, html], i) => {
      const el = els[i]
      if (roomsDrawn[id] === html && !el.__touched) return
      el.insertAdjacentHTML('afterend', html)
      el.parentNode.removeChild(el)
      roomsDrawn[id] = html
      wrote = true
    })
    return wrote
  }

  /* Room tiles (since 0.19.3): columns (1–3, chosen per device), distribution and "Anordnen" as on the System page
     through HO.tileGrid; the arrangement lives per number of columns in settings.json (layout.home, comes with /api/overview). */
  let rgrid = null, homeSaved = null
  const roomTileId = rid => 'rc-' + String(rid || 'none').replace(/[^A-Za-z0-9_:.-]/g, '_').slice(0, 60)
  // Set up the tile grid of the room tiles (arrangement from the settings).
  function mountRooms(page) {
    if (rgrid) rgrid.destroy()
    if (!homeSaved) homeSaved = HO.state.overview.layout_home || {}
    rgrid = HO.tileGrid({ page, box: page.querySelector('#devBox'), key: 'home', saved: homeSaved, item: '.room-card' })
  }
  // arranging: show all rooms (otherwise filtered-out rooms would be missing in the stored arrangement)
  function arrangeRooms(page) {
    if (!rgrid) return
    if (rgrid.arranging) { rgrid.arrange(false); return schedulePartial() }
    if (filter !== 'all') { filter = 'all'; render(page) }
    rgrid.arrange(true)
  }

  /* Room card: head always equally high (room name, one line of state, light switch) with the image of the active scene
     or the current light as the background; tapping opens the light settings of the room.
     Below it brightness, scenes and lamps; the other devices as small tiles.
     The cards are always open (cannot be collapsed); they are arranged with "Anordnen" in the head "Geräte". */
  function roomCardHTML(n, g, ov, info) {
    const rid = g.room_id
    const roomLamps = rid ? ov.devices.filter(d => d.room_id === rid && d.kind === 'light') : []
    const lamps = g.items.filter(d => d.kind === 'light').sort((a, b) => HO.byName(a.name, b.name))
    const others = g.items.filter(d => d.kind !== 'light')
    const on = roomLamps.filter(d => d.on && d.online)
    const sc = rid && roomLamps.length ? HO.lights.activeScene('room:' + rid) : null
    const glow = HO.lightGlow(roomLamps, 0.75), img = HO.lights.imgTag(sc, 'rc-img')
    const sub = [info && info.temperature != null ? HO.esc(HO.temp(info.temperature) + (info.target != null ? ' · Soll ' + HO.temp(info.target) : '')) : '',
      roomLamps.length ? (on.length ? `${on.length} von ${roomLamps.length} an` : 'Licht aus') : '',
      sc ? `${HO.icon('palette', 'sm')}${HO.esc(sc.name)}` : ''].filter(Boolean).join('<span class="rc-dot">·</span>')
    const list = view === 'list'
    const dim = !list && rid && lamps.some(d => d.light && d.online)
    // Only rooms with lamps have a tappable head (light settings); without light it is a plain heading
    return `<section class="room-card ${on.length ? 'lit' : ''} ${list ? 'compact' : ''}" id="${HO.esc(roomTileId(rid))}">
      <div class="rc-head ${glow || img ? 'lit' : ''}" ${rid && roomLamps.length ? `data-room-head="${HO.esc(rid)}" role="button" tabindex="0" title="Lichteinstellung"` : ''}>
        ${glow ? `<div class="rc-glow" style="background:${glow}"></div>` : ''}${img}
        <div class="rc-title"><h3>${HO.esc(n)}</h3><div class="rc-sub">${sub || '&nbsp;'}</div></div>
        ${roomSwitchHTML(n, ov.devices)}</div>
      <div class="rc-body">
        ${dim ? `<input type="range" class="lt-bri" min="1" max="100" value="${HO.lights.avgBrightness(lamps)}" data-room-bri="${HO.esc(rid)}" aria-label="Helligkeit ${HO.esc(n)}">` : ''}
        ${list ? `<div class="rc-list">${lamps.concat(others).map(tileHTML).join('')}</div>` : `
        ${rid && lamps.length ? HO.lights.sceneRowHTML({ room: rid }) : ''}
        ${lamps.length ? `<div class="lt-lamps">${lamps.map(HO.lights.lampRowHTML).join('')}</div>` : ''}
        ${others.length ? `<div class="tiles sm">${others.map(tileHTML).join('')}</div>` : ''}`}</div></section>`
  }

  // Switch between the two views of the home page. It stands at the same place in both – top right, on the phone to
  // the right of the greeting – so that switching never moves it. `solo` = the classic view; in the widget view the bar
  // of the board (#wgBar: pages, tools) follows as a line of its own and shares that line with the switch on wide screens.
  function viewBarHTML(solo) {
    const button = (id, icon, label) => `<button type="button" data-homeview="${id}" class="${homeView === id ? 'active' : ''}" role="tab" aria-selected="${homeView === id}"
      title="${label === 'Widgets' ? 'Widget-Ansicht' : 'Klassische Ansicht'}">${HO.icon(icon, 'sm')}<span class="lbl">${label}</span></button>`
    // widget view on a wide screen: a button hides the sidebar, so that the widgets get the whole width
    const nav = solo ? '' : `<button type="button" class="btn-icon sm nav-toggle ${navHidden ? 'on' : ''}" data-act="navToggle" aria-pressed="${navHidden}"
      title="${navHidden ? 'Seitenleiste einblenden' : 'Seitenleiste ausblenden'}" aria-label="${navHidden ? 'Seitenleiste einblenden' : 'Seitenleiste ausblenden'}">${HO.icon('sidebar', 'sm')}</button>`
    return `<div class="view-bar ${solo ? 'solo' : 'with-bar'}" id="viewBar"><div class="row g6 view-switch">${nav}<div class="seg view-seg" role="tablist" aria-label="Ansicht der Startseite">
      ${button('classic', 'home', 'Klassisch')}${button('widgets', 'grid', 'Widgets')}</div></div></div>`
  }
  /* Sidebar in the widget view (since 0.24): it can be hidden on a wide screen – remembered on this device
     (localStorage ho.navHidden). It only applies while the widget view is shown; every other page keeps its sidebar. */
  let navHidden = (() => { try { return localStorage.getItem('ho.navHidden') === '1' } catch (e) { return false } })()
  const applyNav = () => document.body.classList.toggle('nav-hidden', navHidden && alive && mode === 'home' && homeView === 'widgets')
  // Hide or show the sidebar (button in the bar of the widget view) and remember the choice.
  function toggleNav(button) {
    navHidden = !navHidden
    try { localStorage.setItem('ho.navHidden', navHidden ? '1' : '0') } catch (e) { }
    applyNav()
    const label = navHidden ? 'Seitenleiste einblenden' : 'Seitenleiste ausblenden'
    button.classList.toggle('on', navHidden)
    button.setAttribute('aria-pressed', navHidden)
    button.title = label
    button.setAttribute('aria-label', label)
  }
  // Choose the view of the home page, remember it on this device and draw the page anew.
  function setHomeView(next, page) {
    if (next === homeView || (next !== 'classic' && next !== 'widgets')) return
    homeView = next
    try { localStorage.setItem('ho.homeView', next) } catch (e) { }
    if (board) { board.destroy(); board = null }
    editing = false
    render(page)
    window.scrollTo(0, 0)
  }
  // Frame of the widget view: switch, weather warning and the board (widgets.js), which draws pages and widgets itself.
  function renderWidgets(page) {
    const ov = HO.state.overview
    warnShown = warnHTML(ov)
    setTitle()
    if (board) { board.destroy(); board = null }
    if (rgrid) { rgrid.destroy(); rgrid = null }
    if (fp) { fp.destroy(); fp = null }
    applyNav()
    page.innerHTML = `${viewBarHTML(false)}<div id="heroBox">${heroHTML(ov)}</div><div class="wg-bar" id="wgBar"></div>
      <div id="warnBox">${warnShown}</div>
      <div id="wgHost"><div class="card"><div class="spinner"></div></div></div>`
    mountBoard(page)
  }
  // Fetch the stored widget view (once per visit of the page) and mount the board.
  async function mountBoard(page) {
    const my = gen, host = page.querySelector('#wgHost')
    // one request per visit, shared by everyone who asks meanwhile (the page may be drawn twice while it opens)
    if (!widgetDoc) widgetDoc = HO.widgets.load().catch(e => { HO.toast('Widget-Ansicht nicht geladen: ' + e.message, 'bad'); return { pages: [] } })
    const doc = await widgetDoc
    if (my !== gen || !host || !document.body.contains(host) || homeView !== 'widgets' || !HO.state.overview) return
    if (board) board.destroy()
    board = HO.widgets.mount(host, { bar: page.querySelector('#wgBar'), home: HO.home, overview: HO.state.overview, doc })
  }
  /* What the widgets need from the home page (ctx.home in widgets.js): weather and warnings, formats, the overall state and
     the floor plan as a widget – with the same dialogs and the same way of saving as the classic view. */
  HO.home = {
    weather: () => wxNow(), warnings: () => wxWarnings(), fx: () => wxFx(),
    watt: v => watt(v), kwh: v => kwhText(v), issues: ov => issuesOf(ov),
    // Mount the floor plan into a widget: a camera view inside the tile, still or free (`still`), fed by the home page.
    // `floor` = id of the floor it shows; `floorNav` = buttons in the frame to change the floor.
    mountPlan(el, o) {
      const ov = HO.state.overview
      return HO.floorplan.mount(el, {
        camera: true, embed: true, still: !!o.still, floor: o.floor || '', floorNav: !!o.floorNav,
        plan: ov.floorplan, devices: ov.devices, rooms: ov.rooms, heat: !!o.heat,
        heatRange: ov.heat_range, weather: o.weather || null, onSave: savePlan,
        onDeviceClick: d => deviceSheet(d), onRoomClick: (r, info) => roomSheet(r, info), onGroupClick: g => groupSheet(g)
      })
    }
  }

  // Write the frame of the home page: fixed places for the blocks, mount the floor plan, build the tile grid of the rooms.
  // After that partial() keeps the blocks up to date.
  function render(page) {
    const ov = HO.state.overview
    if (!ov.status.configured) {
      page.innerHTML = `<section class="card hero"><div class="empty">${HO.icon('home', 'lg')}<h2 style="margin:8px 0">Noch nichts verbunden</h2>
        <p class="muted">Richte unter System → Plugins ein, was du hast: Home Base, Hue Bridge, MQTT oder Geräte im Netzwerk.</p>
        <button class="btn primary" data-goto="settings?tab=plugins">Zu den Plugins</button></div></section>`
      return
    }
    if (homeView === 'widgets') return renderWidgets(page)
    if (board) { board.destroy(); board = null }
    applyNav()
    const hasPlan = HO.floorplan.hasWalls(ov.floorplan)
    if (heat === null) heat = !!ov.floorplan.heatmap
    warnShown = warnHTML(ov)
    // tools above the floor plan: only the icons on the phone (.lbl is hidden there, the name is in the title)
    const tool = (act, icon, label, extra) => `<button class="btn sm fp-tool ${extra || ''}" data-act="${act}" title="${label}" aria-label="${label}">${HO.icon(icon, 'sm')}<span class="lbl">${label}</span></button>`
    setTitle()
    page.innerHTML = `${viewBarHTML(true)}<div id="heroBox">${heroHTML(ov)}</div>
      <div id="warnBox">${warnShown}</div>
      <section class="card home-card">
        <div class="card-head"><div><h3>Grundriss</h3><div class="small muted">${editing ? 'Bearbeiten – Änderungen werden automatisch gespeichert' : 'Antippen für Details'}</div></div>
        <div class="row fp-tools fp-toolscroll">${hasPlan && !editing ? `${floorSwitchHTML(ov, 'btn-icon sm')}<button class="btn-icon sm" data-act="fpZoomOut" title="Verkleinern" aria-label="Verkleinern">−</button><button class="btn-icon sm" data-act="fpZoomIn" title="Vergrößern" aria-label="Vergrößern">+</button><button class="btn-icon sm" data-act="fpFit" title="Ansicht zurücksetzen" aria-label="Ansicht zurücksetzen">${HO.icon('fit', 'sm')}</button>
          ${tool('fpNames', 'type', 'Namen')}${tool('fpFull', 'maximize', 'Vollbild')}${tool('fpHeat', 'flame', 'Heatmap', heat ? 'primary' : '').replace('<button ', `<button aria-pressed="${heat}" `)}${HO.weatherFx ? `<button class="btn sm fp-tool ${fxOn(ov) ? 'primary' : ''}" data-act="fpFx" aria-pressed="${fxOn(ov)}" aria-label="Wetter"
            title="Wettereffekte rund um den Grundriss: Regen, Schnee, Wind, Sonne, Sterne">${HO.icon('cloud', 'sm')}<span class="lbl">Wetter</span>${HO.proTag('floorplan_weather')}</button>` : ''}${effectsButton('btn sm fp-tool', 'primary', 'fpEffects', true)}` : ''}
        ${tool('roomManage', 'layers', 'Räume')}
        ${tool('fpEdit', editing ? 'check' : 'edit', editing ? 'Fertig' : 'Bearbeiten', editing ? 'primary' : '')}</div></div>
        <div id="fpEmpty" class="${hasPlan || editing ? 'hidden' : ''}"><div class="empty">${HO.icon('map', 'lg')}
          <div style="margin:6px 0 14px">Zeichne deinen Grundriss und setze die Geräte hinein – mit Raumtemperaturen auf einen Blick.</div>
          <div class="row" style="justify-content:center;flex-wrap:wrap"><button class="btn primary" data-act="fpAuto">${HO.icon('wand', 'sm')} Räume automatisch anlegen</button>
          <button class="btn" data-act="fpEdit">${HO.icon('wall', 'sm')} Wände zeichnen</button></div></div></div>
        <div id="fpHost" class="${hasPlan || editing ? '' : 'hidden'}"></div>
        <div id="fpFoot">${footHTML(ov)}</div>
        <div id="homeBox">${homeHTML(ov)}</div></section>
      <div id="infoBox">${infoHTML(ov)}</div>
      <div id="mediaBox">${HO.media.blockHTML(ov)}</div>
      <section class="section"><div class="section-head"><h3>Geräte</h3>
        <div class="row">${HO.cols.segHTML('home', HO.cols.chosen('home', 2))}
        <button class="btn sm" data-act="arrangeAuto" hidden title="Eigene Anordnung für diese Spaltenzahl vergessen">${HO.icon('refresh', 'sm')} Automatisch</button>
        <button class="btn sm" data-act="arrange" aria-pressed="false" title="Raumkacheln frei anordnen – je Spaltenzahl merkt sich ComfyOS eine eigene Anordnung">${HO.icon('move', 'sm')} Anordnen</button>
        <div class="seg"><button data-view="tiles" class="${view === 'tiles' ? 'active' : ''}" title="Kacheln" aria-label="Kacheln">${HO.icon('grid', 'sm')}</button><button data-view="list" class="${view === 'list' ? 'active' : ''}" title="Liste" aria-label="Liste">${HO.icon('list', 'sm')}</button></div></div></div>
        <div class="chips scroll" style="margin-bottom:14px">${[['all', 'Alle'], ['heat', 'Heizung'], ['power', 'Licht & Strom'], ['contact', 'Fenster & Sensoren'], ['issues', 'Probleme']]
          .map(([k, l]) => `<span class="chip ${filter === k ? 'active' : ''}" data-filter="${k}">${l}</span>`).join('')}</div>
        <div id="devBox">${devicesHTML(ov)}</div></section>`
    mountRooms(page)
    if (fp) fp.destroy()
    fp = HO.floorplan.mount(page.querySelector('#fpHost'), {
      plan: ov.floorplan, devices: ov.devices, rooms: ov.rooms, heat, heatRange: ov.heat_range, weather: wxFx(),
      floor: planFloor, onFloor: floorChanged,
      onSave: savePlan,
      onDeviceClick: d => deviceSheet(d),
      onAssign: (d, key, name) => assignRoom(d, key, name),
      onRoomClick: (r, info) => roomSheet(r, info),
      onGroupClick: g => groupSheet(g),
      onNewRoom: (r, existing) => newPlanRoom(r),
      onRoomsRenamed: HO.debounce(() => refresh(true), 800)
    })
    if (editing) fp.setEditing(true)
    syncFloorSwitch()
  }

  /* ---------- Floors of the floor plan (since 0.25): two buttons in the toolbar above the plan (home page) and in the
     bar of the full screen change the floor shown; between them stands its short name. Only with several floors. */
  function floorSwitchHTML(ov, button) {
    if (HO.floorplan.floorsOf(ov.floorplan).length < 2) return ''
    return `<span class="fp-floorsw" id="fpFloorSw"><button class="${button} down" data-act="fpFloorDown" title="Ein Stockwerk nach unten" aria-label="Ein Stockwerk nach unten">${HO.icon('chevron', 'sm')}</button>
      <b aria-live="polite"></b><button class="${button} up" data-act="fpFloorUp" title="Ein Stockwerk nach oben" aria-label="Ein Stockwerk nach oben">${HO.icon('chevron', 'sm')}</button></span>`
  }
  // Bring the floor switch up to date: name of the floor shown, the buttons end at the lowest and the highest floor.
  function syncFloorSwitch() {
    const box = document.getElementById('fpFloorSw')
    if (!box || !fp) return
    const list = fp.floors, at = list.findIndex(f => f.active), shown = list[at], name = box.querySelector('b')
    if (!shown) return
    name.textContent = shown.short; name.title = shown.name
    box.querySelector('.down').disabled = at <= 0
    box.querySelector('.up').disabled = at >= list.length - 1
  }
  // Another floor is shown (buttons, editor): remember it for this device and update the switch.
  function floorChanged(id) {
    planFloor = id
    try { localStorage.setItem('ho.fpFloor', id) } catch (e) { }
    syncFloorSwitch()
  }

  // weather effects: switched on, unlocked and possible with weather data
  const fxOn = ov => !!HO.weatherFx && HO.can('floorplan_weather') && ov.floorplan.weather !== false && ov.floorplan.fx !== false
  /* Button "Effekte" (since 0.28): the effects of the devices in the floor plan – the light of the lamps in the colour
     they shine, the sound waves of the speakers. Basic, remembered per browser (floorplan.js, `ho.fpEffects`). cls =
     classes of the button, on = class while switched on, label = with its name (home page; the full screen shows the
     symbol only). */
  function effectsButton(cls, on, act, label) {
    if (!HO.weatherFx) return ''
    const lit = HO.floorplan.effectsOn(), name = 'Effekte'
    return `<button class="${cls} ${lit ? on : ''}" data-act="${act}" aria-pressed="${lit}" aria-label="${name}"
      title="Effekte der Geräte im Grundriss: Licht der Lampen in ihrer Farbe, Schallwellen der Lautsprecher">${HO.icon('sparkle', 'sm')}${label ? `<span class="lbl">${name}</span>` : ''}</button>`
  }
  // Switch the effects of the devices on or off.
  function toggleEffects(btn, cls) {
    if (!fp) return
    const on = fp.toggleEffects()
    btn.classList.toggle(cls, on); btn.setAttribute('aria-pressed', on)
    HO.toast(on ? 'Effekte an' : 'Effekte aus')
  }
  // Button "Wetter": switch the weather effects on the floor plan on or off – with a hint if Pro, address or weather are
  // missing for it.
  function toggleFx(btn, cls) {
    const plan = HO.state.overview.floorplan
    if (!HO.can('floorplan_weather')) return HO.upsell({ title: 'Wettereffekte', message: 'Wettereffekte rund um den Grundriss gehören zu ComfyOS Pro.' })
    if (!plan.address) return HO.toast('Für Wettereffekte zuerst die Adresse eintragen: Grundriss → Bearbeiten.', 'warn', 6000)
    if (plan.weather === false) return HO.toast('Für Wettereffekte zuerst das Wetter einschalten: Grundriss → Bearbeiten → „Außentemperatur und Wetter anzeigen“.', 'warn', 6000)
    if (!fp) return
    const on = fp.toggleFx()
    plan.fx = on   // at once, so that a reload before saving does not jump back
    btn.classList.toggle(cls, on); btn.setAttribute('aria-pressed', on)
    HO.toast(on ? 'Wettereffekte an' : 'Wettereffekte aus')
  }

  // Save the floor plan and take over the stored state; after a changed address or weather setting fetch the weather
  // anew.
  async function savePlan(plan) {
    try {
      const before = (HO.state.overview.floorplan || {})
      const saved = await HO.api('floorplan', { method: 'PUT', body: plan })
      HO.state.overview.floorplan = saved
      // address or weather switch changed → fetch the weather anew
      if (saved.address !== before.address || saved.weather !== before.weather || saved.warnings !== before.warnings) { HO.state.weather = null; loadWeather(true).then(updateWeather) }
      const foot = document.getElementById('fpFoot'); if (foot) foot.innerHTML = footHTML(HO.state.overview)
    }
    catch (e) { HO.toast('Grundriss nicht gespeichert: ' + e.message, 'bad') }
  }
  // Take over new weather: effects and shadows on the floor plan, the line below the plan and the warning hint.
  function updateWeather() {
    const page = document.getElementById('page')
    if (!page || !HO.state.overview) return
    if (fp) fp.setWeather(wxFx())   // weather effects and building shadows (display only, no second query)
    if (mode === 'plan') { const box = page.querySelector('#pvWx'); if (box) box.innerHTML = pvWeatherHTML(); return }
    HO.blocks.draw('planFoot', page.querySelector('#fpFoot'), { overview: HO.state.overview, page })
    if (board) board.update(HO.state.overview)      // widget view: weather widget, floor plan widget
    renderWarn()
  }

  /* ---------- Blocks of the home page (contract: blocks.js). Every block has its fixed place in the frame of render()
     and is only rewritten when its content changes. From 0.23 (widget system) they become freely selectable
     widgets; the floor plan then joins as a block with a structure of its own (today: fp.update further down). */
  HO.blocks.register('hero', { title: 'Begrüßung', icon: 'home', render: ctx => heroHTML(ctx.overview) })
  HO.blocks.register('planFoot', { title: 'Ort und Wetter', icon: 'cloud', render: ctx => footHTML(ctx.overview) })
  HO.blocks.register('presence', { title: 'Zuhause / Abwesend', icon: 'home', render: ctx => homeHTML(ctx.overview) })
  HO.blocks.register('stats', { title: 'Licht, Szenen und Kennzahlen', icon: 'gauge', render: ctx => infoHTML(ctx.overview) })
  // playback: every speaker, no matter from which plugin – what counts is the capability `playing` (media.js)
  HO.blocks.register('media', { title: 'Wiedergabe', icon: 'media', needs: ['playing'], render: ctx => HO.media.blockHTML(ctx.overview) })
  HO.blocks.register('rooms', {
    title: 'Räume und Geräte', icon: 'layers', render: ctx => devicesHTML(ctx.overview),
    patch: (el, ctx) => patchRooms(el, ctx.overview),   // only the tiles that changed (since 0.30.3)
    after: () => { if (rgrid) rgrid.refresh() }      // new tiles: bring the grid up to date
  })
  // block -> its element in the frame of the home page, in the order of the page
  const HOME_BLOCKS = [['hero', '#heroBox'], ['planFoot', '#fpFoot'], ['presence', '#homeBox'], ['stats', '#infoBox'], ['media', '#mediaBox'], ['rooms', '#devBox']]

  // Redraw the blocks of the home page from the current state – each only if its content has changed – and
  // give the floor plan the fresh data.
  function partial() {
    const ov = HO.state.overview, page = document.getElementById('page')
    if (!page || !ov) return
    if (mode === 'plan') { if (fp) fp.update({ devices: ov.devices, rooms: ov.rooms, plan: ov.floorplan, heatRange: ov.heat_range }); renderPeek(); return }
    if (!ov.status.configured) return
    if (homeView === 'widgets') {
      // widget view: every widget draws what changed – not under a finger on a slider (the board itself waits while a
      // widget is dragged or a list of favourites is open)
      if (board && !sliding) board.update(ov)
      setTitle()
      renderWarn()
      if (HO.lights.onData) HO.lights.onData()
      return
    }
    const ctx = { overview: ov, page }
    // the floor plan first (since 0.30.3): it measures its frame, and measuring after the blocks were rewritten would
    // make the browser lay the whole page out in the middle of the script – and once more for the frame
    if (fp) fp.update({ devices: ov.devices, rooms: ov.rooms, plan: ov.floorplan, heatRange: ov.heat_range })
    for (const [id, selector] of HOME_BLOCKS) {
      // rooms: do not rewrite under the finger (a slider is being dragged) and not while arranging
      if (id === 'rooms' && (sliding || (rgrid && rgrid.arranging))) continue
      if (id === 'media' && sliding) continue     // the volume is being dragged right now
      if (id === 'media' && HO.media.choosing(page.querySelector(selector))) continue     // the list of favourites is open
      HO.blocks.draw(id, page.querySelector(selector), ctx)     // rooms: tile by tile (its `patch`, since 0.30.3)
    }
    setTitle()
    renderWarn()   // open windows change the hint
    if (HO.lights.onData) HO.lights.onData()
  }
  let sliding = false
  const onSlideStart = e => {
    if (e.target.matches && e.target.matches('#devBox input[type=range], #mediaBox input[type=range], #wgHost input[type=range]')) sliding = true
  }
  const onSlideEnd = () => {
    if (sliding) { sliding = false; schedulePartial() }
  }
  // The list of favourites was closed without a choice: catch up on what was not drawn meanwhile.
  const onFavoriteBlur = e => {
    if (e.target.matches && e.target.matches('select.media-fav')) schedulePartial()
  }

  // Optimistic states: show at once until server/device confirms them (or discard after 6 s).
  // Prevents jumping back when a reload still delivers the old state of the Home Base.
  const overrides = {}, lastTap = {}
  // a tapped state holds at least this long – older responses (the query was already running before the tap) do not turn it back
  const HOLD_MS = 3000
  // Protect switching states that were just tapped against older responses: as long as a tap is fresh, it keeps applying.
  function applyOverrides(devs) {
    const now = Date.now()
    for (const d of devs || []) {
      const o = overrides[d.id]
      if (!o) continue
      if (o.until < now || (d.on === o.on && now - o.at > HOLD_MS)) { delete overrides[d.id]; continue }
      d.on = o.on
    }
  }
  let rafPending = false
  // Refresh the blocks in the next frame – several messages in quick succession give one drawing.
  function schedulePartial() {
    if (rafPending) return
    rafPending = true
    requestAnimationFrame(() => { rafPending = false; partial() })
  }
  /* A control in a dialog is being dragged (colour wheel, brightness – since 0.30.3): the floor plan follows in every
     frame, its light is what one looks at; the blocks of the page lie dimmed below the dialog and follow at most every
     DRAG_BLOCKS ms. Rewriting them per pointer move made the browser layer and paint the whole page each time – the
     drag itself stuttered. Widget view and full screen draw as before (there the plan is part of partial()). */
  const DRAG_BLOCKS = 200
  let planPending = false, dragTimer = 0
  // Draw for a control that is being dragged: the floor plan in the next frame, the blocks a little later.
  function scheduleDrag() {
    if (homeView === 'widgets' || mode === 'plan' || !fp) return schedulePartial()
    if (!planPending) {
      planPending = true
      requestAnimationFrame(() => {
        planPending = false
        const ov = HO.state.overview
        if (fp && ov && mode !== 'plan') fp.update({ devices: ov.devices, rooms: ov.rooms, plan: ov.floorplan, heatRange: ov.heat_range })
      })
    }
    if (!dragTimer) dragTimer = setTimeout(() => { dragTimer = 0; schedulePartial() }, DRAG_BLOCKS)
  }
  /* Light values set by hand hold against older live messages (since 0.30.3): while a colour or brightness is dragged
     the lamp answers with the values of a moment ago – taking them over made the light in the plan jump back and forth.
     For ADJUST_HOLD ms after the last own change `light` and `brightness` of that lamp are not taken from the stream. */
  const ADJUST_HOLD = 1200, adjusted = {}
  const refreshSoon = HO.debounce(() => refresh(true), 400)
  // after a motion redraw once when the afterglow has expired
  let motionTimer = null
  // Remember reported motion and draw once more after the hold time, so that the display goes out again.
  function trackMotion(devs) {
    if (!HO.noteMotion(devs)) return
    clearTimeout(motionTimer)
    motionTimer = setTimeout(schedulePartial, HO.MOTION_HOLD + 300)
  }

  // Fetch the state of the home anew and draw. Responses for a page that was left meanwhile are discarded.
  async function refresh(quiet) {
    if (!alive) return
    const my = gen
    try {
      const data = await HO.api('overview')
      // page left meanwhile – late calls (sync response, live stream) must not overwrite another page either
      if (my !== gen || !alive) return
      applyOverrides(data.devices)
      trackMotion(data.devices)
      HO.ackedErrors = {}
      HO.state.overview = data
      if (HO.setConn) HO.setConn(data.status)
      const page = document.getElementById('page')
      if (mode === 'plan') partial()
      else if (!page.querySelector('#heroBox') && HO.state.overview.status.configured) render(page)
      else partial()
    } catch (e) { if (!quiet) HO.toast(e.message, 'bad') }
  }

  // live updates (Hue event stream, commands from other devices) through the shared stream (HO.live)
  let stream = null
  // Subscribe to live messages: single device values, scenes, effects and notifications arrive without a reload.
  function openStream() {
    if (stream) return
    stream = HO.live.on(m => {
      const ov = HO.state.overview
      if (!ov || !ov.devices) return
      if (m.type === 'reload') return refreshSoon()
      if (m.type === 'toast') return HO.toast(m.text, 'ok', 6000)   // Flow-Benachrichtigung
      if (m.type === 'scenes') { ov.scenes = { scenes: m.scenes, active: m.active }; return schedulePartial() }
      if (m.type === 'effects') { ov.effects = m.rooms || {}; return schedulePartial() }
      if (m.type !== 'device') return
      const d = ov.devices.find(x => x.id === m.id)
      if (!d) return refreshSoon()
      const o = overrides[m.id]
      // ignore the echo of an older own command shortly after a new tap
      if (o && m.on != null && m.on !== o.on) {
        if (Date.now() - o.at < HOLD_MS) return
        delete overrides[m.id]   // after that: a real change from outside
      }
      const own = (adjusted[m.id] || 0) > Date.now()     // just set by hand here: older light values do not count
      for (const k of HO.LIVE_FIELDS) {
        if (own && (k === 'light' || k === 'brightness')) continue
        if (m[k] !== undefined && (m[k] !== null || k === 'alarm' || k === 'locked')) d[k] = m[k]
      }
      trackMotion([d])
      schedulePartial()
    })
  }
  function closeStream() { if (stream) { stream(); stream = null } }
  // back in the app (phone from the background): refresh at once, the stream may have been paused
  function onVisible() { if (!document.hidden && !editing) refresh(true) }

  // Send a command to a device. The tapped state appears at once; if the command fails, it jumps back and a
  // message names the reason.
  async function command(id, state, label) {
    const d = (HO.state.overview.devices || []).find(x => x.id === id)
    if ('on' in state && d) {
      // already running or tapped just now – catch an accidental double tap
      if (d._busy || Date.now() - (lastTap[id] || 0) < 700) return
      lastTap[id] = Date.now()
      const prev = d.on
      d.on = state.on; d._busy = true
      overrides[id] = { on: state.on, at: Date.now(), until: Date.now() + 6000 }
      schedulePartial()
      try {
        return await HO.api(`devices/${encodeURIComponent(id)}/command`, { method: 'POST', body: { state } })
      } catch (e) {
        delete overrides[id]
        const cur = (HO.state.overview.devices || []).find(x => x.id === id)
        if (cur) cur.on = prev
        HO.toast(e.message, 'bad')
      } finally {
        const cur = (HO.state.overview.devices || []).find(x => x.id === id)
        if (cur) cur._busy = false
        d._busy = false
        schedulePartial()
      }
      return
    }
    try {
      const r = await HO.api(`devices/${encodeURIComponent(id)}/command`, { method: 'POST', body: { state } })
      HO.toast(label || `${r.name}: ${r.command}`)
      setTimeout(() => refresh(true), 1200)
      return r
    } catch (e) { HO.toast(e.message, 'bad') }
  }

  // Switch all lamps (roomIds = null) or the lamps of a room; show the state at once
  async function switchLights(on, roomIds, label) {
    const ov = HO.state.overview
    const hit = ov.devices.filter(d => switchable(d) && (!roomIds || roomIds.includes(d.room_id)))
    if (!hit.length) { HO.toast('Keine schaltbaren Lampen.', 'warn'); return }
    const now = Date.now()
    hit.forEach(d => { d.on = on; overrides[d.id] = { on, at: now, until: now + 8000 } }); schedulePartial()
    try {
      const r = await HO.api('lights', { method: 'POST', body: { on, room_ids: roomIds } })
      if (r.failed.length) { HO.toast(`${r.switched} geschaltet, ${r.failed.length} fehlgeschlagen: ${r.failed[0]}`, 'warn', 6000); refreshSoon() }
    } catch (e) { HO.toast(e.message, 'bad'); hit.forEach(d => delete overrides[d.id]); refreshSoon() }
  }

  // Set a house scene (sid) or end all scenes (null); switch the chips at once
  async function houseScene(sid) {
    const ov = HO.state.overview, st = ov.scenes || { scenes: [], active: {} }
    const sc = sid && st.scenes.find(s => s.id === sid)
    const now = Date.now() / 1000
    if (sc) sc.rooms.forEach(r => { st.active['room:' + r] = { scene: sid, since: now } })
    else st.active = {}
    schedulePartial()
    try {
      const r = sc ? await HO.api(`scenes/${encodeURIComponent(sid)}/apply-house`, { method: 'POST' })
        : await HO.api('scenes/stop', { method: 'POST', body: {} })
      if (r.failed && r.failed.length) HO.toast(`Teilweise: ${r.failed[0]}`, 'warn', 6000)
      else HO.toast(sc ? `„${sc.name}“ im ganzen Haus` : `${r.stopped} Szene${r.stopped === 1 ? '' : 'n'} beendet`)
    } catch (e) { HO.toast(e.message, 'bad'); refreshSoon() }
  }

  // Helligkeit aller dimmbaren Lampen eines Raums; sofort anzeigen
  async function roomBrightness(roomId, v) {
    const ov = HO.state.overview, now = Date.now()
    const hit = ov.devices.filter(d => switchable(d) && d.room_id === roomId)
    hit.forEach(d => { d.on = true; if (d.light) d.brightness = v; overrides[d.id] = { on: true, at: now, until: now + 8000 } })
    schedulePartial()
    try {
      const r = await HO.api('lights', { method: 'POST', body: { on: true, room_ids: [roomId], brightness: v } })
      if (r.failed.length) { HO.toast(`${r.switched} geschaltet, ${r.failed.length} fehlgeschlagen: ${r.failed[0]}`, 'warn', 6000); refreshSoon() }
    } catch (e) { HO.toast(e.message, 'bad'); refreshSoon() }
  }
  // Room head: light settings of the room (with the switches of the home page). Rooms without lamps no longer have a
  // tappable head – their devices stay operable on the tile (room details still through the floor plan).
  function openRoom(rid) {
    const ov = HO.state.overview
    if (!ov.devices.some(d => d.room_id === rid && d.kind === 'light')) return
    HO.lights.roomSheet(rid, {
      room: on => switchLights(on, [rid]),
      bri: v => roomBrightness(rid, v),
      toggle: (d, on) => command(d.id, { on }, `${d.name} ${on ? 'eingeschaltet' : 'ausgeschaltet'}`),
      open: d => deviceSheet(d)
    })
  }

  // Remove a device from ComfyOS after a confirmation (it stays at its source).
  async function removeDevice(d) {
    if (!await HO.confirm(d.adopted ? `„${d.name}" aus ComfyOS entfernen? Das Gerät bleibt bei seiner Quelle und lässt sich unter System bei seinem Plugin wieder übernehmen.`
      : `„${d.name}" aus ComfyOS entfernen? Seine Quelle (Home Base, Bridge …) bleibt unverändert; unter System lässt es sich wiederherstellen.`,
    { title: 'Aus ComfyOS entfernen', ok: 'Entfernen', danger: true })) return
    try { await HO.api('devices/remove', { method: 'POST', body: { device_id: d.id, removed: true } }); HO.toast(`${d.name} entfernt`) } catch (e) { HO.toast(e.message, 'bad') }
    return refresh(true)
  }

  // Assign a device to a room and say whether the hub has already taken over the change.
  async function assignRoom(d, key, name) {
    try {
      const r = await HO.api(`devices/${encodeURIComponent(d.id)}/room`, { method: 'PUT', body: { target: key } })
      if (r.note) HO.toast(`${d.name} → ${name || r.device.room} · ${r.note}`, 'ok', 5000)
      else if (r.warning) HO.toast(r.warning, 'warn', 6000)
      else HO.toast(`${d.name} → ${name || r.device.room} · auch in der Magenta-App`, 'ok', 4000)
    } catch (e) { HO.toast(e.message, 'bad') }
    return refresh(true)
  }

  // Dialog "Noch nicht übertragen": room changes the hub does not know yet, with "Jetzt übertragen".
  function pendingSheet() {
    const p = HO.state.overview.room_pending || {}
    const li = (arr, f) => arr.map(x => `<li>${f(x)}</li>`).join('')
    const sh = HO.sheet({ title: `Noch nicht übertragen (${hubName()})`, body: `<p class="small muted" style="margin-top:0">Der Grundriss ist der Master. Räume und Zuordnungen schreibt ComfyOS
      dorthin, wo die Räume geführt werden (${HO.esc(hubName())}) – dessen App zeigt dann dieselbe Raumliste. Diese Punkte sind dort noch nicht angekommen:</p>
      ${p.moves && p.moves.length ? `<h4>Geräte verschieben</h4><ul class="plist">${li(p.moves, m => `<b>${HO.esc(m.device)}</b>: ${HO.esc(m.from)} → ${HO.esc(m.to)}`)}</ul>` : ''}
      ${p.creates && p.creates.length ? `<h4>Räume anlegen</h4><ul class="plist">${li(p.creates, c => `<b>${HO.esc(c.name)}</b>`)}</ul>` : ''}
      ${p.renames && p.renames.length ? `<h4>Räume umbenennen</h4><ul class="plist">${li(p.renames, r => `${HO.esc(r.from)} → <b>${HO.esc(r.to)}</b>`)}</ul>` : ''}
      <p class="small muted">Räume, die es nur im Grundriss geben soll, im Editor mit einem bestehenden Raum verknüpfen oder per Wand mit einem Nachbarraum zusammenlegen.</p>`,
      actions: [{ label: 'Jetzt übertragen', icon: 'sync', kind: 'primary', onClick: async () => {
        try {
          const r = await HO.api('rooms/push', { method: 'POST' })
          const d = r.done
          HO.toast(r.errors.length ? 'Teilweise übertragen: ' + r.errors[0] : `Übertragen: ${d.creates} angelegt, ${d.renames} umbenannt, ${d.moves} verschoben`, r.errors.length ? 'warn' : 'ok', 6000)
        } catch (e) { HO.toast(e.message, 'bad') }
        return refresh(true)
      } }] })
    return sh
  }

  const hubName = () => HO.roomHub() || 'Hub'

  /* Further functions of a device: capabilities a plugin offers under a name of its own (`label` in `caps`) –
     values without a fixed key such as a mode, a threshold or a button. One fitting control per type;
     without write access, offline or locked only the value is shown. */
  function extrasHTML(d, extras) {
    // One line per function: its label and the control that fits its type.
    const row = c => {
      const k = HO.esc(c.key), v = d[c.key], can = c.writable && HO.canControl(d)
      const unit = c.unit ? ' ' + HO.esc(c.unit) : ''
      let input
      if (c.type === 'action') input = `<button class="btn sm" data-xact="${k}" ${can ? '' : 'disabled'}>Ausführen</button>`
      else if (!can) input = `<b>${v == null ? '–' : c.type === 'bool' ? (v ? 'Ja' : 'Nein') : HO.esc(String(v)) + unit}</b>`
      else if (c.type === 'bool') input = `<button class="btn sm ${v ? 'primary' : ''}" data-xbool="${k}" data-on="${v ? 0 : 1}" aria-pressed="${!!v}">${v == null ? 'Unbekannt' : v ? 'Ja' : 'Nein'}</button>`
      else if (c.type === 'enum') input = `<select data-xsel="${k}" aria-label="${HO.esc(c.label)}">${v == null ? '<option value="" selected>–</option>' : ''}${(c.options || []).map(o => `<option value="${HO.esc(o)}" ${String(v) === String(o) ? 'selected' : ''}>${HO.esc(o)}</option>`).join('')}</select>`
      else input = `<input type="number" data-xnum="${k}" aria-label="${HO.esc(c.label)}" value="${v == null ? '' : HO.esc(String(v))}"${c.minimum != null ? ` min="${HO.esc(String(c.minimum))}"` : ''}${c.maximum != null ? ` max="${HO.esc(String(c.maximum))}"` : ''} step="${c.step != null ? HO.esc(String(c.step)) : 'any'}" style="width:110px">${unit}`
      return `<div class="row between wrap" style="margin-top:10px"><span>${HO.esc(c.label)}</span><span class="row g8">${input}</span></div>`
    }
    return `<div class="card" style="background:var(--card-2);margin-bottom:14px"><div class="eyebrow">Weitere Funktionen</div>${extras.map(row).join('')}</div>`
  }
  // Wires the controls of the further functions in the open device dialog: every change is sent as a command.
  function bindExtras(s, d, extras) {
    const label = key => (extras.find(c => c.key === key) || {}).label || key
    // Sends one value to the device and remembers it in the device and in the overview on success.
    const send = async (key, value, text) => {
      const r = await command(d.id, { [key]: value }, `${d.name}: ${text}`)
      if (r) { d[key] = value; const cur = (HO.state.overview.devices || []).find(x => x.id === d.id); if (cur) cur[key] = value }
      return r
    }
    s.el.addEventListener('click', async e => {
      const act = e.target.closest('[data-xact]'), flip = e.target.closest('[data-xbool]')
      if (act) return send(act.dataset.xact, true, label(act.dataset.xact))
      if (!flip) return
      const key = flip.dataset.xbool, on = flip.dataset.on === '1'
      // a yes/no value can be a door lock: never switch it without a confirmation
      if (!await HO.confirm(`„${label(key)}“ auf ${on ? 'Ja' : 'Nein'} stellen?`, { title: d.name })) return
      if (await send(key, on, `${label(key)}: ${on ? 'ja' : 'nein'}`)) { s.close(); deviceSheet(d) }
    })
    s.el.addEventListener('change', e => {
      const sel = e.target.closest('[data-xsel]'), num = e.target.closest('[data-xnum]')
      if (sel && sel.value !== '') send(sel.dataset.xsel, sel.value, `${label(sel.dataset.xsel)}: ${sel.value}`)
      if (num && num.value !== '' && isFinite(+num.value)) send(num.dataset.xnum, +num.value, `${label(num.dataset.xnum)}: ${num.value}`)
    })
  }

  // Dialog of a device: state, controls, details of the source, history, change room and name, lock, remove.
  function deviceSheet(d) {
    const extras = (d.caps || []).filter(c => c.label && (c.writable || d[c.key] != null))
    const kv = [['Typ', HO.kindLabel[d.kind] || d.raw_type], ['Status', d.online ? 'Online' : 'Offline'],
      d.battery != null ? ['Batterie', d.battery + ' %' + (HO.lowBattery(d) ? ' – schwach' : '')] : null,
      d.last_seen ? ['Zuletzt gesehen', HO.relTime(d.last_seen)] : null, d.vendor ? ['Hersteller', d.vendor] : null,
      d.source && d.control && d.control.plugin === d.source.plugin
        ? ['Steuerung', (d.adopted ? 'direkt (übernommen, Raum in ComfyOS)' : 'direkt · live') + ' über ' + pluginName(d.source.plugin)] : null,
      d.source && d.source.renamable && d.source.name && d.source.name !== d.name ? ['Name bei der Quelle', d.source.name] : null,
      ...Object.entries(d.props || {}),
      !d.online && d.status_detail ? ['Hinweis', d.status_detail] : null].filter(Boolean)
    let ctl = d.link_missing ? `<div class="notice warn" style="margin-bottom:14px">${HO.icon('warn', 'sm')}<div>${HO.esc(pluginName(d.plugin))} führt dieses Gerät noch, bei ${HO.esc(d.missing_at || 'seiner Quelle')} gibt es es nicht mehr – es lässt sich nicht schalten.
      <div class="row wrap" style="margin-top:8px"><span data-plugin-actions></span><button class="btn sm danger" data-remove="1">${HO.icon('trash', 'sm')} Aus ComfyOS entfernen</button></div></div></div>` : ''
    if (d.online && d.control_item && d.kind === 'thermostat') {
      ctl = `<div class="card" style="background:var(--card-2);margin-bottom:14px"><div class="row between"><div><div class="small muted">Ist ${HO.temp(d.temperature, true)}</div><div class="eyebrow">Solltemperatur</div></div>
        <div class="stepper"><button class="btn-icon" data-s="-0.5">${HO.icon('minus')}</button><span class="val" id="sv">${HO.temp(d.target, true)}</span><button class="btn-icon" data-s="0.5">${HO.icon('plus')}</button></div></div>
        <div class="small muted" style="margin-top:8px">Batteriebetriebene Thermostate übernehmen den Wert beim nächsten Funkkontakt (bis ca. 15 Min.).</div></div>`
    } else if (d.online && d.control_item && d.kind === 'cover') {
      ctl = `<div class="card" style="background:var(--card-2);margin-bottom:14px">
        <div class="row between"><div><div class="eyebrow">Rollladen</div><div class="small muted" id="cvState">${HO.esc(HO.devState(d))}</div></div>
          <div class="row"><button class="btn sm" data-cv="up">${HO.icon('chevron', 'sm')} Auf</button>
            <button class="btn sm" data-cv="stop">${HO.icon('pause', 'sm')} Stop</button>
            <button class="btn sm" data-cv="down">${HO.icon('chevron', 'sm')} Zu</button></div></div>
        ${d.position != null ? `<input type="range" min="0" max="100" step="5" value="${d.position}" id="cvPos" aria-label="Position"
          style="width:100%;margin-top:12px"><div class="tiny muted">0 % = offen, 100 % = geschlossen</div>` : ''}</div>`
    } else if (HO.lights.controllable(d)) {
      ctl = `<div style="margin-bottom:14px">${HO.lights.controlHTML(d)}</div>`
    } else if (d.online && d.control_item && HO.caps.has(d, 'playing', true)) {
      ctl = `<div id="devMedia" style="margin-bottom:14px">${HO.media.cardHTML(d)}</div>`
    } else if (d.online && d.control_item && (HO.caps.has(d, 'on') || !extras.length)) {
      ctl = `<button class="btn ${d.on ? '' : 'primary'}" style="width:100%;margin-bottom:14px" data-t="${d.on ? 0 : 1}">${HO.icon('power', 'sm')} ${d.on ? 'Ausschalten' : 'Einschalten'}</button>`
    }
    if (extras.length) ctl += extrasHTML(d, extras)
    // Device lock: locked = no controls, only the hint; the checkbox is shown for every switchable device
    if (d.locked && !d.link_missing) {
      ctl = `<div class="notice" style="margin-bottom:14px">${HO.icon('lock', 'sm')}<div>Gesperrt – weder du noch Flows, Szenen oder „Alle Lichter“ schalten dieses Gerät, bis du es entsperrst.</div></div>`
    }
    if (d.control_item || d.locked) {
      ctl += `<label class="check" style="margin-bottom:4px"><input type="checkbox" id="devLock" ${d.locked ? 'checked' : ''}><span>${HO.icon('lock', 'sm')} Gerät sperren</span></label>
        <p class="tiny muted" style="margin:0 0 14px">Die Sperre wirkt nur in ComfyOS. Ein Wandschalter, die Hue-App oder die Magenta-App können das Gerät weiter schalten.</p>`
    }
    const vals = (d.values || []).map(v => `<dt>${HO.esc(v.label)}</dt><dd>${HO.esc(v.state)}</dd>`).join('')
    ctl += `<label class="field" style="margin-bottom:14px"><span>Name</span>
      <input type="text" id="devName" value="${HO.esc(d.custom_name || d.name)}" maxlength="80" autocomplete="off">
      ${d.custom_name ? '<span class="hint">Individueller Name – <a href="#" id="devNameReset">zurücksetzen</a></span>' : ''}</label>`
    // the room is only named here (since 0.30.3, wish of the operator): a device is assigned in the floor plan alone
    ctl += `<div class="field" style="margin-bottom:14px"><span>Raum</span>
      <div id="devRoom">${d.room_id ? HO.esc(d.room || '') : '<span class="muted">Nicht zugeordnet</span>'}</div>
      <span class="hint">Zuordnen im Grundriss: das Gerät dort in seinen Raum setzen.</span>
      ${d.room_pending ? `<span class="hint" style="color:var(--warn)">${HO.esc(hubName())}: noch in „${HO.esc(d.hb_room || 'kein Raum')}" – wird beim nächsten Übertragen nachgezogen.</span>` : ''}
      ${d.local_room && HO.roomHub() ? `<span class="hint">Nur in ComfyOS gespeichert – ${HO.esc(hubName())} kennt dieses Gerät nicht.</span>` : ''}</div>`
    const issues = HO.devIssues(d)
    // loads of the floor plan that hang on this device (since 0.30): say so – switching the device drives them
    const loads = HO.floorplan.loadsOf(HO.state.overview.floorplan).filter(l => l.device_id === d.id)
    const manyFloors = HO.floorplan.floorsOf(HO.state.overview.floorplan).length > 1
    const loadName = l => `${l.name || l.kind_label}${manyFloors ? ' (' + l.floor_label + ')' : ''}`
    const loadNote = loads.length ? `<div class="notice" style="margin-bottom:14px">${HO.icon('link', 'sm')}<div>
      ${loads.length === 1 ? `Der Verbraucher <b>${HO.esc(loadName(loads[0]))}</b> hängt an diesem Gerät – er leuchtet bzw. läuft im Grundriss, solange das Gerät an ist.`
    : 'Verbraucher an diesem Gerät: ' + loads.map(l => `<b>${HO.esc(loadName(l))}</b>`).join(', ') + ' – sie leuchten bzw. laufen im Grundriss, solange das Gerät an ist.'}</div></div>` : ''
    const s = HO.sheet({ title: d.name, body: `<div class="row" style="margin-bottom:14px"><div class="dev-ic ${HO.devClass(d)}">${HO.icon(d.kind)}</div><div><b>${HO.esc(HO.devState(d))}</b></div></div>
      ${issues.length ? `<div class="dev-issues">${issues.map(i => `<div class="t-flag ${i.k} wide">${HO.icon(i.k === 'battery' ? 'battery' : i.k === 'offline' ? 'offline' : 'warn', 'sm')}<span>${HO.esc(i.t)}</span>${i.k === 'error' ? `<button class="btn-ack" data-ack-error="${HO.esc(i.t)}" title="Ausblenden">${HO.icon('close', 'sm')}</button>` : ''}</div>`).join('')}</div>` : ''}${loadNote}${ctl}
      <dl class="kv">${kv.map(([k, v]) => `<dt>${HO.esc(k)}</dt><dd>${HO.esc(v)}</dd>`).join('')}</dl>
      ${vals ? `<details style="margin-top:14px"><summary class="small muted" style="cursor:pointer">Alle Werte</summary><dl class="kv" style="margin-top:10px">${vals}</dl></details>` : ''}
      ${d.link_missing ? '' : `<div class="form-actions" style="border-top:1px solid var(--line);padding-top:14px"><span data-plugin-actions></span><button class="btn sm ghost danger" data-remove="1">${HO.icon('trash', 'sm')} Aus ComfyOS entfernen</button></div>`}` })
    // actions the plugin of the device offers (e.g. Home Base: Wiederverbinden, Von der Home Base löschen)
    let pluginActs = []
    HO.plugins.deviceActions(d).then(acts => {
      const box = s.el.querySelector('[data-plugin-actions]')
      if (!box || !acts.length) return
      pluginActs = d.link_missing ? acts.filter(a => a.danger) : acts
      box.outerHTML = pluginActs.map((a, i) => `<button class="btn sm${a.danger ? (d.link_missing ? ' danger' : ' ghost danger') : ''}" data-plugin-act="${i}" data-action-key="${HO.esc(a.key)}">${a.icon ? HO.icon(a.icon, 'sm') + ' ' : ''}${HO.esc(a.label)}</button>`).join('')
    }).catch(() => { })
    if (HO.lights.controllable(d)) {
      HO.lights.bindControl(s.el, d, { onChange: (dev, dragging) => {
        overrides[d.id] = { on: d.on, at: Date.now(), until: Date.now() + 4000 }
        adjusted[d.id] = Date.now() + ADJUST_HOLD
        if (dragging) scheduleDrag(); else schedulePartial()
      } })
    }
    const lockBox = s.el.querySelector('#devLock')
    if (lockBox) lockBox.addEventListener('change', async () => {
      const want = lockBox.checked
      try {
        await HO.api(`devices/${encodeURIComponent(d.id)}/${want ? 'lock' : 'unlock'}`, { method: 'POST' })
        d.locked = want
        const cur = (HO.state.overview.devices || []).find(x => x.id === d.id); if (cur) cur.locked = want
        HO.toast(want ? `${d.name} gesperrt` : `${d.name} entsperrt`)
        s.close(); partial(); deviceSheet(cur || d)
      } catch (e) { lockBox.checked = !want; HO.toast(e.message, 'bad') }
    })
    const mediaBox = s.el.querySelector('#devMedia')
    if (mediaBox) {
      // tile in the dialog: redraw from the current state after every command and every live message
      const redraw = () => {
        const cur = (HO.state.overview.devices || []).find(x => x.id === d.id) || d
        // not while the list of favourites is open – it would close under the finger
        if (mediaBox.isConnected && !HO.media.choosing(mediaBox)) HO.setHTML(mediaBox, HO.media.cardHTML(cur))
        schedulePartial()
      }
      mediaBox.onclick = e => { HO.media.handle(e, redraw) }
      mediaBox.onchange = e => { HO.media.handle(e, redraw) }
      mediaBox.addEventListener('blur', e => { if (e.target.matches && e.target.matches('select.media-fav')) redraw() }, true)
    }
    bindExtras(s, d, extras)
    const cvPos = s.el.querySelector('#cvPos')
    if (cvPos) cvPos.addEventListener('change', () => command(d.id, { position: +cvPos.value }, `${d.name}: ${cvPos.value} % zu`))
    let target = d.target
    const nameInput = s.el.querySelector('#devName')
    // Message after renaming: whether the name also arrived at the source of the device, was shortened or failed there.
    const nameToast = (r, msg) => {
      const h = r && r.synced, where = d.source ? pluginName(d.source.plugin) : 'Quelle'
      if (h && !h.ok) return HO.toast(`${msg} – ${where}: ${h.error}`, 'warn', 6000)
      HO.toast(h && h.changed ? msg + (h.shortened ? ` · bei ${where} gekürzt auf „${h.name}“` : ` · auch bei ${where}`) : msg)
    }
    if (nameInput) {
      nameInput.addEventListener('change', async () => {
        const val = nameInput.value.trim()
        if (val === (d.custom_name || d.name)) return
        try {
          const r = await HO.api(`devices/${encodeURIComponent(d.id)}/name`, { method: 'PUT', body: { name: val || null } })
          nameToast(r, val ? `Umbenannt: ${val}` : 'Name zurückgesetzt')
        } catch (e) { HO.toast(e.message, 'bad') }
      })
    }
    s.el.addEventListener('click', async e => {
      const ackBtn = e.target.closest('[data-ack-error]')
      if (ackBtn) {
        const txt = ackBtn.dataset.ackError
        if (!HO.ackedErrors[d.id]) HO.ackedErrors[d.id] = new Set()
        HO.ackedErrors[d.id].add(txt)
        ackBtn.closest('.t-flag').remove()
        if (!s.el.querySelector('.dev-issues .t-flag')) { const di = s.el.querySelector('.dev-issues'); if (di) di.remove() }
        partial()
        return
      }
      if (e.target.closest('#devNameReset')) {
        e.preventDefault()
        try {
          const r = await HO.api(`devices/${encodeURIComponent(d.id)}/name`, { method: 'PUT', body: { name: null } })
          nameToast(r, 'Name zurückgesetzt'); s.close(); refresh(true)
        } catch (err) { HO.toast(err.message, 'bad') }
        return
      }
      const cv = e.target.closest('[data-cv]')
      if (cv) { command(d.id, { cover: cv.dataset.cv }, `${d.name}: ${cv.dataset.cv === 'up' ? 'öffnet' : cv.dataset.cv === 'down' ? 'schließt' : 'angehalten'}`); return }
      const pa = e.target.closest('[data-plugin-act]')
      if (pa) {
        const a = pluginActs[+pa.dataset.pluginAct]
        s.close()
        if (a) Promise.resolve(a.run(d, () => refresh(true))).catch(err => HO.toast(err.message, 'bad'))
        return
      }
      if (e.target.closest('[data-remove]')) { s.close(); return removeDevice(d) }
      const b = e.target.closest('[data-s],[data-t]'); if (!b) return
      if (b.dataset.t != null) { s.close(); return command(d.id, { on: b.dataset.t === '1' }, `${d.name} ${b.dataset.t === '1' ? 'eingeschaltet' : 'ausgeschaltet'}`) }
      target = Math.max(5, Math.min(30, (target || 20) + Number(b.dataset.s)))
      s.el.querySelector('#sv').textContent = HO.temp(target, true)
      sendTarget()
    })
    const sendTarget = HO.debounce(() => command(d.id, { target }, `${d.name}: Soll ${HO.temp(target, true)}`), 700)
  }

  // Name a newly drawn room or assign it to an existing room.
  async function newPlanRoom(room) {
    const options = (HO.state.overview.rooms || []).filter(r => r.room_id).sort((a, b) => HO.byName(a.name, b.name))
    const sh = HO.sheet({ title: 'Raum benennen', body: `<p class="small muted">${HO.roomHub() ? `Dieser Raum wird einem vorhandenen Raum (${HO.esc(hubName())}) zugeordnet oder bei Bedarf dort neu angelegt.` : 'Gib dem Raum einen Namen.'} Geräte erzeugen niemals selbst Räume.</p>
      <label class="field"><span>Raumname</span><input type="text" id="newPlanRoomName" list="homeRoomNames" maxlength="40" value="${HO.esc(room.name)}" autocomplete="off">
      <datalist id="homeRoomNames">${options.map(r => `<option value="${HO.esc(r.name)}"></option>`).join('')}</datalist></label>
      <p class="tiny muted">„Abbrechen“ entfernt den gerade gezeichneten Raum wieder (der letzte Schritt wird rückgängig gemacht).</p>`,
      actions: [{ label: 'Abbrechen', kind: 'ghost', onClick: () => {
        if (fp) fp.undo()
        HO.toast('Raum wieder entfernt.')
      } }, { label: 'Übernehmen', kind: 'primary', onClick: async () => {
        const name = sh.el.querySelector('#newPlanRoomName').value.trim()
        if (!name) { HO.toast('Bitte einen Raumnamen eingeben.', 'warn'); return true }
        try {
          const result = await HO.api('rooms', { method: 'POST', body: { name } })
          room.name = result.name; room.room_id = result.room_id
          await HO.api('floorplan', { method: 'PUT', body: fp.plan })
          sh.close()
          HO.toast(!result.room_id ? `Raum „${result.name}“ benannt.` : result.created ? `Raum „${result.name}“ angelegt.` : `Vorhandenen Raum „${result.name}“ verknüpft.`, 'ok')
          await refresh(true)
        } catch (e) { HO.toast(e.message, 'bad'); return true }
      } }] })
    setTimeout(() => { const i = sh.el.querySelector('#newPlanRoomName'); if (i) { i.focus(); i.select() } }, 0)
  }

  // Dialog "Räume": create, rename, delete (empty rooms only; without a hub a room is its walls – it is deleted
  // in the floor plan, so the button only explains that there).
  async function roomManagerSheet() {
    let rooms = []
    try { rooms = await HO.api('rooms/manage') } catch (e) { return HO.toast(e.message, 'bad') }
    const hub = HO.roomHub()
    const renderRows = () => rooms.map(r => `<div class="li" data-room="${HO.esc(r.room_id)}"><div class="grow"><b>${HO.esc(r.name)}</b><div class="small muted">${r.devices} Gerät${r.devices === 1 ? '' : 'e'}</div></div>
      <button class="btn-icon sm" data-rename="${HO.esc(r.room_id)}" title="Umbenennen">${HO.icon('edit','sm')}</button>
      <button class="btn-icon sm danger" data-delete="${HO.esc(r.room_id)}" ${!hub || r.devices ? 'disabled' : ''}
        title="${hub ? 'Löschen' : 'Ohne Hub ist ein Raum seine Wände – im Grundriss-Editor löschen'}">${HO.icon('trash','sm')}</button></div>`).join('')
    const sh = HO.sheet({ title: 'Raumverwaltung', body: `<div class="row between"><span class="small muted">${hub ? 'Alle Räume – geführt von ' + HO.esc(hub) : 'Alle Räume'}</span>${hub ? `<button class="btn sm primary" id="roomAdd">${HO.icon('plus','sm')} Raum</button>` : ''}</div>
      ${hub ? '' : '<p class="tiny muted">Räume legst du im Grundriss an: Wände zeichnen, Raum antippen und benennen.</p>'}
      <div class="list" id="roomManagerRows" style="margin-top:10px">${renderRows() || '<p class="muted">Noch keine Räume vorhanden.</p>'}</div>
      <p class="tiny muted">Räume mit Geräten können erst nach dem Umziehen aller Geräte gelöscht werden.</p>` })
    sh.el.addEventListener('click', async e => {
      const add = e.target.closest('#roomAdd'), ren = e.target.closest('[data-rename]'), del = e.target.closest('[data-delete]')
      try {
        if (add) {
          const name = await HO.prompt('Raum anlegen', '', { label: 'Raumname', ok: 'Anlegen' })
          if (!name) return
          await HO.api('rooms', { method: 'POST', body: { name } })
        } else if (ren) {
          const r = rooms.find(x => x.room_id === ren.dataset.rename)
          const name = await HO.prompt('Raum umbenennen', r.name, { label: 'Neuer Name', ok: 'Speichern' })
          if (!name) return
          await HO.api(`rooms/${encodeURIComponent(r.room_id)}/name`, { method: 'PUT', body: { name } })
        } else if (del) {
          const r = rooms.find(x => x.room_id === del.dataset.delete)
          if (!r || r.devices) return
          // What still points at this room (since 0.30): scenes, flows, widgets, heating routines. Deleting leaves
          // those references without a target (they fail quietly), so the confirmation names them beforehand.
          const used = [], needle = JSON.stringify(r.room_id)
          const sc = (((HO.state.overview || {}).scenes || {}).scenes || []).filter(s => (s.rooms || []).indexOf(r.room_id) >= 0)
          if (sc.length) used.push(`Szenen (${sc.map(s => s.name).join(', ')})`)
          try {
            const fl = ((await HO.api('flows')).flows || []).filter(f => JSON.stringify(f.nodes || []).indexOf(needle) >= 0)
            if (fl.length) used.push(`Flows (${fl.map(f => f.name).join(', ')})`)
          } catch (err) { /* without the flow list the hint is just shorter */ }
          try {
            const rt = ((await HO.api('routines')).routines || []).filter(x => JSON.stringify(x).indexOf(needle) >= 0)
            if (rt.length) used.push(`Heizpläne (${rt.map(x => x.name).join(', ')})`)
          } catch (err) { /* dito */ }
          try {
            const pages = (await HO.api('widgets')).pages || []
            const n = pages.reduce((a, pg) => a + (pg.widgets || []).filter(w => w.opts && (w.opts.room === r.room_id
              || (Array.isArray(w.opts.rooms) && w.opts.rooms.indexOf(r.room_id) >= 0))).length, 0)
            if (n) used.push(`${n} Widget${n === 1 ? '' : 's'} der Widget-Ansicht`)
          } catch (err) { /* dito */ }
          const warn = used.length ? ` Darauf verweisen noch: ${used.join(' · ')} – diese Verweise laufen danach ins Leere und müssen von Hand angepasst werden.` : ''
          if (!await HO.confirm(`Raum „${r.name}“ wirklich löschen?${warn} Die Raumfläche im Grundriss bleibt und ist danach unverknüpft.`,
            { title: 'Raum löschen', ok: 'Löschen', danger: true })) return
          await HO.api(`rooms/${encodeURIComponent(r.room_id)}`, { method: 'DELETE' })
        } else return
        rooms = await HO.api('rooms/manage')
        const box = sh.el.querySelector('#roomManagerRows'); if (box) box.innerHTML = renderRows()
        await refresh(true)
      } catch (err) { HO.toast(err.message, 'bad') }
    })
  }

  // Dialog of a room: scenes, temperatures and its devices.
  /* Dialog of a room in the floor plan – since 0.30 with everything the room card of the classic view offers
     (wish of the operator): room light switch, brightness, scenes, the lamps with their switches and the other
     devices as tiles with their controls. Clicks are handled here: the sheet lies outside the page, so the
     handlers of the home page do not reach it. */
  function roomSheet(r, info) {
    const ov = () => HO.state.overview
    const key = r.key || r.room_id
    const name = (info && info.name) || r.name || 'Raum'
    // Content of the sheet from the current overview: temperatures, room light switch, brightness, scenes, lamps, other devices.
    const body = () => {
      const devs = ov().devices.filter(d => key && d.room_id === key)
      const lamps = devs.filter(d => d.kind === 'light').sort((a, b) => HO.byName(a.name, b.name))
      const others = devs.filter(d => d.kind !== 'light').sort((a, b) => HO.byName(a.name, b.name))
      const on = lamps.filter(d => d.on && d.online), sw = lamps.filter(switchable)
      const glow = HO.lightGlow(lamps, 0.75)
      const dim = lamps.some(d => d.light && d.online)
      return `${info || sw.length ? `<div class="row between wrap g8" style="margin-bottom:12px">
          ${info ? `<div class="stats" style="margin:0"><div class="stat"><span>Ist</span><b>${HO.temp(info.temperature)}</b></div><div class="stat"><span>Soll</span><b>${HO.temp(info.target)}</b></div></div>` : `<span class="small muted">${lamps.length ? (on.length ? `${on.length} von ${lamps.length} an` : 'Licht aus') : ''}</span>`}
          ${sw.length ? `<button class="room-switch ${on.length ? 'on' : ''}" role="switch" aria-checked="${!!on.length}" data-room-lights data-on="${on.length ? 0 : 1}"
            title="${on.length ? 'Alle Lampen im Raum ausschalten' : 'Alle Lampen im Raum einschalten'}" aria-label="Licht ${HO.esc(name)}"></button>` : ''}</div>` : ''}
        ${dim ? `<input type="range" class="lt-bri" min="1" max="100" value="${HO.lights.avgBrightness(lamps)}" data-room-bri aria-label="Helligkeit ${HO.esc(name)}" style="margin-bottom:12px">` : ''}
        ${lamps.length ? `<div class="sheet-scenes">${HO.lights.sceneRowHTML({ room: key })}</div>` : ''}
        ${lamps.length ? `<div class="lt-lamps" style="position:relative">${glow ? `<div class="rc-glow" style="background:${glow}"></div>` : ''}${lamps.map(HO.lights.lampRowHTML).join('')}</div>` : ''}
        ${others.length ? `<div class="${view === 'list' ? 'rc-list' : 'tiles sm'}" style="margin-top:12px">${others.map(tileHTML).join('')}</div>` : ''}
        ${devs.length ? '' : '<p class="muted">Noch keine Geräte – im Grundriss ein Gerät in diesen Raum ziehen oder beim Gerät den Raum wählen.</p>'}`
    }
    const s = HO.sheet({ title: name, body: body() })
    const redraw = () => { if (s.el.isConnected) HO.setHTML(s.body(), body()) }
    s.el.addEventListener('click', async e => {
      if (HO.media.handle(e, redraw)) return                    // play/pause of a speaker tile
      const t = e.target.closest('[data-room-lights],[data-lt-toggle],[data-lt-open],[data-cmd],[data-dev]')
      if (!t) return
      if (t.dataset.roomLights != null) {
        e.stopPropagation(); t.disabled = true
        await switchLights(t.dataset.on === '1', [key], name)
        return redraw()
      }
      if (t.dataset.ltToggle) {
        const d = ov().devices.find(x => x.id === t.dataset.ltToggle)
        if (d) await command(d.id, { on: !d.on }, `${d.name} ${d.on ? 'ausgeschaltet' : 'eingeschaltet'}`)
        return redraw()
      }
      if (t.dataset.ltOpen) { s.close(); return deviceSheet(ov().devices.find(x => x.id === t.dataset.ltOpen)) }
      if (t.dataset.cmd) {
        e.stopPropagation()
        const d = ov().devices.find(x => x.id === t.dataset.id)
        if (!d) return
        if (t.dataset.cmd === 'toggle') await command(d.id, { on: t.dataset.on === '1' }, `${d.name} ${t.dataset.on === '1' ? 'eingeschaltet' : 'ausgeschaltet'}`)
        else if (t.dataset.cmd === 'cover') {
          const label = { up: 'öffnet', down: 'schließt', stop: 'angehalten' }[t.dataset.v]
          await command(d.id, { cover: t.dataset.v }, `${d.name} ${label}`)
        } else {
          const next = Math.max(5, Math.min(30, (d.target || 20) + Number(t.dataset.v)))
          d.target = next
          await command(d.id, { target: next }, `${d.name}: Soll ${HO.temp(next, true)}`)
        }
        return redraw()
      }
      if (t.dataset.dev) { s.close(); return deviceSheet(ov().devices.find(x => x.id === t.dataset.dev)) }
    })
    s.el.addEventListener('change', e => {
      const bri = e.target.closest('[data-room-bri]')
      if (bri) roomBrightness(key, +bri.value)
    })
  }

  // Device group in the floor plan: control members individually or all together, detach single ones again
  const groupSwitchable = d => (d.kind === 'light' || d.kind === 'switch') && HO.canControl(d)
  // Dialog of a device group in the floor plan: switch all, operate single devices, detach a device from the group.
  function groupSheet(g) {
    const devs = () => g.members.map(id => HO.state.overview.devices.find(d => d.id === id)).filter(Boolean)
    // Content of the group dialog from the current state.
    const body = () => {
      const ds = devs(), sw = ds.filter(groupSwitchable)
      return `${ds.some(d => d.kind === 'light') ? `<div class="sheet-scenes">${HO.lights.sceneRowHTML({ group: g.id })}</div>` : ''}
        ${sw.length > 1 ? `<div class="row" style="margin-bottom:12px"><button class="btn sm" data-gall="1">${HO.icon('light', 'sm')} Alle an</button>
          <button class="btn sm" data-gall="0">${HO.icon('power', 'sm')} Alle aus</button></div>` : ''}
        <div class="list">${ds.map(d => `<div class="dev-row ${d.online ? '' : 'off'}" data-gdev="${HO.esc(d.id)}"><div class="dev-ic ${HO.devClass(d)}">${HO.icon(d.kind)}</div>
          <div class="r-main"><div class="r-name">${HO.esc(d.name)}</div><div class="r-state">${HO.esc(HO.devState(d))}</div></div>
          <div class="row">${groupSwitchable(d) ? `<button class="btn-icon sm ${d.on ? 'primary' : ''}" data-gon="${d.on ? 0 : 1}" data-id="${HO.esc(d.id)}" aria-pressed="${!!d.on}" title="${d.on ? 'Ausschalten' : 'Einschalten'}">${HO.icon('power', 'sm')}</button>` : ''}
            <button class="btn sm ghost" data-gout="${HO.esc(d.id)}" title="Aus der Gruppe nehmen und einzeln platzieren">Herauslösen</button></div></div>`).join('')}</div>
        <p class="small muted" style="margin:12px 0 0">Gruppen legst du im Grundriss unter „Bearbeiten“ an: ein Gerät auf ein anderes ziehen.</p>`
    }
    const s = HO.sheet({ title: g.name || 'Gruppe', body: body() })
    const redraw = () => { s.body().innerHTML = body() }
    s.el.addEventListener('click', async e => {
      const t = e.target.closest('[data-gall],[data-gon],[data-gout],[data-gdev]')
      if (!t) return
      e.stopPropagation()
      if (t.dataset.gout) {
        if (!fp) return
        fp.ungroup(g.id, t.dataset.gout)
        HO.state.overview.floorplan = JSON.parse(JSON.stringify(fp.plan))
        HO.toast('Steht jetzt einzeln neben der Gruppe.')
        return g.members.length < 2 ? s.close() : redraw()
      }
      if (t.dataset.gon) { await command(t.dataset.id, { on: t.dataset.gon === '1' }); return redraw() }
      if (t.dataset.gall) {
        const on = t.dataset.gall === '1'
        await Promise.all(devs().filter(groupSwitchable).map(d => command(d.id, { on })))
        HO.toast(`${g.name || 'Gruppe'}: ${on ? 'eingeschaltet' : 'ausgeschaltet'}`)
        return redraw()
      }
      if (t.dataset.gdev) { s.close(); deviceSheet(devs().find(d => d.id === t.dataset.gdev)) }
    })
  }

  HO.pages.home = {
    title: 'ComfyOS',
    // Open the home page: draw at once from the last state, then refresh; set up live stream, controls and regular
    // reloading.
    async render(page) {
      editing = false; mode = 'home'; alive = true; fxFromHash()
      widgetDoc = null     // the widget view fresh from /api/widgets on every visit (it may have been changed on another device)
      const my = ++gen
      if (HO.state.overview) {
        // page change: draw at once from the last state (no blank frame, no flicker), then refresh
        render(page)
        refresh(true)
      } else {
        page.innerHTML = '<div class="card"><div class="spinner"></div></div>'
        await refresh()
        if (my !== gen) return
        if (!HO.state.overview) throw new Error('Übersicht konnte nicht geladen werden.')
        render(page)
      }
      loadWeather().then(() => { if (my === gen) updateWeather() })
      HO.kickSync().then(() => { if (my === gen) refresh(true) }).catch(() => {})  // trigger the sync (throttled, never twice)
      timer = setInterval(() => { if (!document.hidden && !editing) { refresh(true); pollWeather(my) } }, 30000)
      openStream()
      HO.lights.onScenes = schedulePartial
      document.addEventListener('visibilitychange', onVisible)
      sliding = false
      homeSaved = null     // gespeicherte Anordnung frisch aus /api/overview
      page.onpointerdown = onSlideStart
      window.addEventListener('pointerup', onSlideEnd); window.addEventListener('pointercancel', onSlideEnd)
      page.addEventListener('blur', onFavoriteBlur, true)     // blur does not bubble: listen on the way down
      page.onchange = e => {
        if (HO.media.handle(e, schedulePartial)) { sliding = false; return }     // volume, favourite
        const r = e.target.closest('[data-room-bri]')
        if (r) { sliding = false; roomBrightness(r.dataset.roomBri, +r.value) }
      }
      page.onkeydown = e => {
        const h = (e.key === 'Enter' || e.key === ' ') && e.target.closest && e.target.closest('[data-room-head]')
        if (h && e.target === h) { e.preventDefault(); openRoom(h.dataset.roomHead) }
      }
      page.onclick = async e => {
        if (HO.media.handle(e, schedulePartial)) return     // Wiedergabe, Titelwechsel, Stumm
        const t = e.target.closest('[data-home],[data-act],[data-cols],[data-view],[data-filter],[data-cmd],[data-dev],[data-goto],[data-lights],[data-room-lights],[data-lt-toggle],[data-lt-open],[data-room-head],[data-house],[data-house-off],[data-homeview]')
        if (!t) return
        if (t.dataset.homeview) return setHomeView(t.dataset.homeview, page)
        if (t.dataset.act === 'navToggle') return toggleNav(t)
        if (t.dataset.house || t.hasAttribute('data-house-off')) return houseScene(t.dataset.house || null)
        if (t.dataset.goto) return HO.go(t.dataset.goto)
        if (t.dataset.lights) return switchLights(t.dataset.lights === '1', null, 'Alle Lichter')
        if (t.dataset.roomLights) {
          e.stopPropagation()
          t.disabled = true
          return switchLights(t.dataset.on === '1', JSON.parse(t.dataset.roomLights), t.dataset.name)
        }
        if (t.dataset.roomHead) return openRoom(t.dataset.roomHead)
        if (t.dataset.ltOpen) return deviceSheet(HO.state.overview.devices.find(x => x.id === t.dataset.ltOpen))
        if (t.dataset.ltToggle) {
          const d = HO.state.overview.devices.find(x => x.id === t.dataset.ltToggle)
          return d && command(d.id, { on: !d.on }, `${d.name} ${d.on ? 'ausgeschaltet' : 'eingeschaltet'}`)
        }
        if (t.dataset.cmd) {
          e.stopPropagation()
          const d = HO.state.overview.devices.find(x => x.id === t.dataset.id)
          if (t.dataset.cmd === 'toggle') return command(t.dataset.id, { on: t.dataset.on === '1' }, `${d.name} ${t.dataset.on === '1' ? 'eingeschaltet' : 'ausgeschaltet'}`)
          if (t.dataset.cmd === 'cover') {
            const label = { up: 'öffnet', down: 'schließt', stop: 'angehalten' }[t.dataset.v]
            return command(t.dataset.id, { cover: t.dataset.v }, `${d.name} ${label}`)
          }
          const next = Math.max(5, Math.min(30, (d.target || 20) + Number(t.dataset.v)))
          d.target = next; partial()
          return command(t.dataset.id, { target: next }, `${d.name}: Soll ${HO.temp(next, true)}`)
        }
        if (t.dataset.dev) return deviceSheet(HO.state.overview.devices.find(x => x.id === t.dataset.dev))
        if (t.dataset.cols) { HO.cols.choose('home', +t.dataset.cols); if (rgrid) rgrid.relayout(); return }
        if (t.dataset.act === 'arrange') return arrangeRooms(page)
        if (t.dataset.act === 'arrangeAuto') { if (rgrid) rgrid.reset(); return }
        if (t.dataset.view) { view = t.dataset.view; try { localStorage.setItem('ho.view', view) } catch (err) { } render(page); return }
        if (t.dataset.filter) { filter = t.dataset.filter; render(page); return }
        if (t.dataset.home) {
          if (HO.state.overview.home.state === t.dataset.home) return
          HO.state.overview.home.state = t.dataset.home
          partial()
          try {
            const r = await HO.api('home', { method: 'POST', body: { state: t.dataset.home } })
            HO.syncToast(r.sync, t.dataset.home === 'AWAY' ? 'Abwesend' : 'Zuhause')
          } catch (err) { HO.toast(err.message, 'bad') }
          return refresh(true)
        }
        if (t.dataset.act === 'fpEdit') {
          if (editing && fp) heat = fp.heat
          editing = !editing
          if (!editing) await refresh(true)
          render(page)
          return
        }
        if (t.dataset.act === 'roomPending') return pendingSheet()
        if (t.dataset.act === 'power') return powerSheet()
        if (t.dataset.act === 'warn') return warnSheet()
        if (t.dataset.act === 'wx') return weatherSheet()
        if (t.dataset.act === 'warnHide') return hideWarnings()
        if (t.dataset.act === 'fpFx') return toggleFx(t, 'primary')
        if (t.dataset.act === 'fpEffects') return toggleEffects(t, 'primary')
        if (t.dataset.act === 'fpHeat') { heat = !heat; fp.setHeat(heat); t.classList.toggle('primary', heat); t.setAttribute('aria-pressed', heat); return }
        if (t.dataset.act === 'fpZoomIn') { if (fp) fp.zoomBy(1.2); return }
        if (t.dataset.act === 'fpZoomOut') { if (fp) fp.zoomBy(1 / 1.2); return }
        if (t.dataset.act === 'fpFit') { if (fp) fp.fit(); return }
        if (t.dataset.act === 'fpFloorUp' || t.dataset.act === 'fpFloorDown') { if (fp) fp.stepFloor(t.dataset.act === 'fpFloorUp' ? 1 : -1); return }
        if (t.dataset.act === 'fpNames') { if (fp) fp.toggleNames(); t.classList.toggle('primary', !!(fp && fp.plan.show_names)); return }
        if (t.dataset.act === 'roomManage') return roomManagerSheet()
        if (t.dataset.act === 'fpFull') {
          // own view #plan (the back button of browser/phone leads back here)
          HO.state.planHeat = fp ? fp.heat : heat
          location.hash = 'plan'
          return
        }
        if (t.dataset.act === 'issues') {
          filter = 'issues'; render(page)
          const box = page.querySelector('#devBox'); if (box) box.scrollIntoView({ behavior: 'smooth', block: 'start' })
          return
        }
        if (t.dataset.act === 'fpAuto') {
          editing = true; render(page); fp.startWithAutoRooms()
          HO.toast('Räume angelegt – Wände antippen und ziehen zum Anpassen.')
        }
      }
    },
    // Leave the home page: end reloading and live stream, tear down floor plan and tile grid.
    destroy() {
      alive = false; heat = null; gen++; clearInterval(timer); closeStream(); document.removeEventListener('visibilitychange', onVisible)
      HO.lights.onScenes = null
      if (board) board.destroy()           // a change not saved yet still goes out
      board = null
      applyNav()                           // the sidebar belongs to every other page again
      if (rgrid) rgrid.destroy()
      rgrid = null
      window.removeEventListener('pointerup', onSlideEnd); window.removeEventListener('pointercancel', onSlideEnd)
      const page = document.getElementById('page')
      if (page) { page.onpointerdown = null; page.onchange = null; page.onkeydown = null; page.removeEventListener('blur', onFavoriteBlur, true) }
      if (fp) fp.destroy(); fp = null
    }
  }

  /* ---------- Floor plan in full screen: own view #plan without navigation.
     Infinite surface with a camera (floorplan.js, camera: true): one finger pans, two fingers zoom, tapping shows details.
     A room opens a compact card at the bottom instead of the large dialog – the plan stays visible. */
  function pvWeatherHTML() {
    const w = wxNow(), list = wxWarnings(), n = list.length
    const severe = n && Math.max.apply(null, list.map(warnLevel)) >= 3
    const warn = n ? `<button class="pv-chip warn ${severe ? 'severe' : ''}" data-act="warn" title="Amtliche Wetterwarnung für deinen Ort">${HO.icon('warn', 'sm')}<b>${n === 1 ? 'Warnung' : n + ' Warnungen'}</b></button>` : ''
    if (!w) return warn
    return `<button class="pv-chip" data-act="pvWx" aria-expanded="${wxOpen}" title="Außentemperatur">${HO.icon('wx-' + w.icon, 'sm')}<b>${wxTemp(w.temperature)}</b><span class="muted">${HO.esc(w.label)} · ${HO.esc(w.place)}</span></button>${warn}
      ${wxOpen ? `<div class="pv-wx-more">${wxFactsHTML(w)}${wxProHTML(w)}
        <div class="tiny muted" style="margin-top:6px">Open-Meteo · übertragen wird nur der Ort</div></div>` : ''}`
  }

  // Short info on the tapped room in full screen: name, temperatures, light switch and its devices.
  function peekHTML() {
    const ov = HO.state.overview, r = peek
    const info = (ov.rooms || []).find(x => x.room_id === r.key)
    const name = info ? info.name : r.name
    const devs = ov.devices.filter(d => d.room_id === r.key).sort((a, b) => HO.byName(a.name, b.name))
    const temps = info && (info.temperature != null || info.target != null)
    return `${glowHTML(devs, 'pv-glow')}<div class="row between" style="align-items:flex-start;position:relative"><div style="min-width:0"><h3>${HO.esc(name)}</h3>${sceneTag(r.key)}
        ${temps ? `<div class="pv-temps"><span>Ist <b>${HO.temp(info.temperature)}</b></span>${info.target != null ? `<span>Soll <b>${HO.temp(info.target)}</b></span>` : ''}</div>` : ''}</div>
      <div class="row">${roomSwitchHTML(name, ov.devices)}<button class="btn-icon sm" data-act="pvPeekClose" aria-label="Schließen">${HO.icon('x', 'sm')}</button></div></div>
      ${devs.some(d => d.kind === 'light') ? HO.lights.sceneRowHTML({ room: r.key }) : ''}
      ${devs.length ? `<div class="chips">${devs.map(d => `<span class="chip" data-dev="${HO.esc(d.id)}">${HO.icon(d.kind, 'sm')}${HO.esc(d.name)} <span class="muted">${HO.esc(HO.devState(d))}</span></span>`).join('')}</div>`
        : '<p class="small muted" style="margin:8px 0 0">Keine Geräte in diesem Raum.</p>'}
      <div class="row" style="margin-top:12px"><button class="btn sm" data-act="pvRoom">${HO.icon('info', 'sm')} Details</button></div>`
  }
  // Show or hide the short info in full screen.
  function renderPeek() {
    const box = document.getElementById('pvPeek')
    if (!box) return
    box.classList.toggle('hidden', !peek)
    box.innerHTML = peek ? peekHTML() : ''
  }
  // Show a room in the bubble and highlight it in the plan (without an argument: close the bubble).
  function showPeek(r) {
    peek = r && r.key ? r : null
    if (fp) fp.highlight(peek ? peek.id : null)
    renderPeek()
  }

  // Write the frame of the full screen and mount the floor plan with a camera.
  function renderPlan(page) {
    const ov = HO.state.overview, hasPlan = HO.floorplan.hasWalls(ov.floorplan)
    page.innerHTML = `<div class="pv">
      <div class="pv-stage" id="pvStage"></div>
      ${hasPlan ? '' : `<div class="pv-empty">${HO.icon('map', 'lg')}<div>Noch kein Grundriss gezeichnet.</div>
        <button class="btn primary" data-act="pvBack">Zur Startseite</button></div>`}
      <div class="pv-bar pv-top"><button class="pv-btn" data-act="pvBack" title="Zurück" aria-label="Zurück">${HO.icon('back')}</button>
        <div class="pv-wx" id="pvWx">${pvWeatherHTML()}</div></div>
      ${hasPlan ? `<div class="pv-bar pv-tools">${floorSwitchHTML(ov, 'pv-btn')}
        <button class="pv-btn ${heat ? 'on' : ''}" data-act="pvHeat" title="Heatmap" aria-label="Heatmap" aria-pressed="${!!heat}">${HO.icon('flame', 'sm')}</button>
        <button class="pv-btn ${ov.floorplan.show_names ? 'on' : ''}" data-act="pvNames" title="Gerätenamen" aria-label="Gerätenamen" aria-pressed="${!!ov.floorplan.show_names}">${HO.icon('type', 'sm')}</button>
        ${HO.weatherFx ? `<button class="pv-btn ${fxOn(ov) ? 'on' : ''}" data-act="pvFx" title="Wettereffekte" aria-label="Wettereffekte" aria-pressed="${fxOn(ov)}">${HO.icon('cloud', 'sm')}</button>` : ''}
        ${effectsButton('pv-btn', 'on', 'pvEffects', false)}
        <button class="pv-btn" data-act="pvIn" title="Vergrößern" aria-label="Vergrößern">${HO.icon('plus', 'sm')}</button>
        <button class="pv-btn" data-act="pvOut" title="Verkleinern" aria-label="Verkleinern">${HO.icon('minus', 'sm')}</button>
        <button class="pv-btn" data-act="pvFit" title="Ganzen Grundriss zeigen" aria-label="Ganzen Grundriss zeigen">${HO.icon('fit', 'sm')}</button></div>` : ''}
      <div class="pv-peek hidden" id="pvPeek"></div></div>`
    if (fp) fp.destroy()
    fp = null
    if (!hasPlan) return
    fp = HO.floorplan.mount(page.querySelector('#pvStage'), {
      camera: true, plan: ov.floorplan, devices: ov.devices, rooms: ov.rooms, heat, heatRange: ov.heat_range, weather: wxFx(), onSave: savePlan,
      floor: planFloor, onFloor: id => { showPeek(null); floorChanged(id) },
      onDeviceClick: d => deviceSheet(d), onRoomClick: r => showPeek(r), onBackgroundClick: () => showPeek(null),
      onGroupClick: g => groupSheet(g)
    })
    syncFloorSwitch()
  }

  // back: like the back button if one came from the home page – otherwise directly to the home page
  function leavePlan() {
    if (HO.prevTab === 'home' && history.length > 1) history.back()
    else location.hash = 'home'
  }
  // Keys in full screen: Esc closes, + and − zoom, 0 shows the whole floor plan.
  function planKey(e) {
    if (document.querySelector('.sheet-bg') || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')) return
    if (e.key === 'Escape') { if (peek) showPeek(null); else leavePlan() }
    else if (!fp) return
    else if (e.key === '+' || e.key === '=') fp.zoomBy(1.4)
    else if (e.key === '-') fp.zoomBy(1 / 1.4)
    else if (e.key === '0') fp.fit()
  }

  HO.pages.plan = {
    title: 'Grundriss',
    chromeless: true,
    // Open the full screen: load the state, draw, set up live stream and controls.
    async render(page) {
      editing = false; mode = 'plan'; peek = null; wxOpen = false; alive = true; fxFromHash()
      const my = ++gen
      page.innerHTML = '<div class="pv"><div class="spinner" style="margin-top:40vh"></div></div>'
      if (!HO.state.overview) await refresh()
      if (my !== gen) return
      if (!HO.state.overview) throw new Error('Grundriss konnte nicht geladen werden.')
      heat = HO.state.planHeat != null ? HO.state.planHeat : !!HO.state.overview.floorplan.heatmap
      renderPlan(page)
      refresh(true)
      loadWeather().then(() => { if (my === gen) updateWeather() })
      timer = setInterval(() => {
        if (document.hidden) return
        refresh(true)
        loadWeather().then(() => { if (my === gen) updateWeather() })   // Backend cacht 15 Min.
      }, 30000)
      openStream()
      HO.lights.onScenes = schedulePartial
      document.addEventListener('visibilitychange', onVisible)
      document.addEventListener('keydown', planKey)
      page.onclick = async e => {
        const t = e.target.closest('[data-act],[data-dev],[data-room-lights]')
        if (!t) return
        if (t.dataset.roomLights) {
          t.disabled = true
          return switchLights(t.dataset.on === '1', JSON.parse(t.dataset.roomLights), t.dataset.name)
        }
        if (t.dataset.dev) return deviceSheet(HO.state.overview.devices.find(x => x.id === t.dataset.dev))
        const act = t.dataset.act
        if (act === 'pvBack') return leavePlan()
        if (act === 'pvPeekClose') return showPeek(null)
        if (act === 'pvRoom' && peek) {
          const info = (HO.state.overview.rooms || []).find(x => x.room_id === peek.key)
          return roomSheet(peek, info)
        }
        if (act === 'pvWx') { wxOpen = !wxOpen; return updateWeather() }
        if (act === 'warn') return warnSheet()
        if (act === 'pvFx') return toggleFx(t, 'on')
        if (act === 'pvEffects') return toggleEffects(t, 'on')
        if (!fp) return
        if (act === 'pvHeat') {
          heat = !heat; HO.state.planHeat = heat; fp.setHeat(heat)
          t.classList.toggle('on', heat); t.setAttribute('aria-pressed', heat)
        } else if (act === 'pvNames') {
          fp.toggleNames()
          const on = !!fp.plan.show_names
          t.classList.toggle('on', on); t.setAttribute('aria-pressed', on)
        } else if (act === 'fpFloorUp' || act === 'fpFloorDown') fp.stepFloor(act === 'fpFloorUp' ? 1 : -1)
        else if (act === 'pvIn') fp.zoomBy(1.4)
        else if (act === 'pvOut') fp.zoomBy(1 / 1.4)
        else if (act === 'pvFit') fp.fit()
      }
    },
    // Leave the full screen: unregister reloading, live stream and keys, tear down the floor plan.
    destroy() {
      alive = false; gen++; clearInterval(timer); closeStream(); HO.lights.onScenes = null
      document.removeEventListener('visibilitychange', onVisible); document.removeEventListener('keydown', planKey)
      if (fp) fp.destroy()
      fp = null; peek = null; heat = null; mode = 'home'
    }
  }
})(window.HO)
