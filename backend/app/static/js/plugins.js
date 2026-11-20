/* ComfyOS – plugins under System
   One tile per plugin: name, state, active switch, settings from the manifest (HO.fields) and – if the
   plugin brings an interface of its own (static/settings.js) – its content.

   A plugin registers its interface like this (file public/plugins/<id>/static/settings.js):
     HO.plugins.register('<id>', { async render(body, tools) { … }, deviceActions(device) { … } })
   deviceActions (optional) returns actions the plugin offers for one of its devices – ComfyOS shows them in the
   device menu and in the lists: [{ key, label, icon, danger, run(device, done) }] (e.g. "Wiederverbinden").
   body  = the element of the tile the plugin draws into
   tools = { plugin, api(path, opts), devices(), adopt(id, room), adoptAll(), syncNames(), rooms, roomOptions(suggestion),
             ui, reload(), reloadAll() }
           adoptAll() adopts all found devices at once; roomOptions() returns the <option> list of the rooms
           (the room named like the suggestion is preselected).
           reload() only redraws the content of this tile, reloadAll() fetches the state of all plugins anew (after pairing/unpairing).
           api() calls the plugin's own routes under /api/plugins/<id>/…; ui are the list building blocks of the System page.
   A plugin too outputs foreign text only through HO.esc. */
(function (HO) {
  'use strict'
  const tiles = {}, loading = {}
  let list = [], rooms = []
  let actionSeq = 0
  const actionRuns = {}        // drawn action buttons: number -> { action, device }

  const find = id => list.find(p => p.id === id)
  const ORIGIN = { builtin: 'eingebaut', user: 'nachgerüstet' }

  // Status badge of a plugin: error, not approved, off, not connected, unreachable or connected.
  function badge(p) {
    if (p.state === 'error') return '<span class="badge bad">Fehler</span>'
    if (p.state === 'new') return '<span class="badge warn">nicht freigegeben</span>'
    if (p.state !== 'active') return '<span class="badge">Aus</span>'
    const st = p.status || {}
    if (st.configured === false) return '<span class="badge">nicht verbunden</span>'
    if (st.connected === false) return '<span class="badge bad">nicht erreichbar</span>'
    return `<span class="badge ok">${st.live ? 'Online' : 'Verbunden'}</span>`
  }

  // Tile of a plugin: head with badge and switch, description, error or approval hint, settings,
  // connections to the outside, helper program and rights (from the manifest) and the area for its own interface.
  function cardHTML(p) {
    const on = p.state === 'active'
    const lan = (p.egress || []).filter(e => e.scope === 'lan'), net = (p.egress || []).filter(e => e.scope !== 'lan')
    const rights = (p.access || []).map(a => HO.esc(a.text)).join(', ')
    return `<section class="card plugin-card" id="plugin-${HO.esc(p.id)}" data-plugin="${HO.esc(p.id)}">
      <div class="card-head"><div><div class="eyebrow">Plugin · ${ORIGIN[p.origin] || ''}</div><h3>${HO.esc(p.name)}</h3></div>
        <div class="row">${badge(p)}${p.state === 'new' || p.state === 'error' && !p.version ? '' : `<button class="room-switch ${on ? 'on' : ''}" role="switch"
          aria-checked="${on}" data-plugin-toggle="${on ? 0 : 1}" title="${on ? 'Ausschalten' : 'Einschalten'}" aria-label="${HO.esc(p.name)} ein/aus"></button>`}</div></div>
      ${p.description ? `<p class="small muted" style="margin:0 0 10px">${HO.esc(p.description)}</p>` : ''}
      ${p.state === 'error' ? `<div class="notice warn" style="margin-bottom:10px">${HO.icon('warn', 'sm')}<div>${HO.esc(p.error || 'Das Plugin läuft nicht.')}</div></div>` : ''}
      ${p.state === 'new' ? `<div class="notice warn">${HO.icon('shield', 'sm')}<div class="grow">Dieses Plugin wurde nachgerüstet (data/plugins) und läuft noch nicht.
        Ein Plugin ist Programmcode mit allen Rechten von ComfyOS – gib nur Plugins frei, deren Herkunft du kennst.
        ${net.length ? `<br>Es verbindet sich mit: <b>${net.map(e => HO.esc(e.hosts.join(', '))).join('; ')}</b>.` : lan.length ? '<br>Es spricht nur Geräte im Heimnetz an' + (lan.some(e => e.udp) ? ' – auch per UDP (z. B. für eine Suche).' : '.') : '<br>Es erklärt keine Netzwerkverbindungen.'}
        ${p.helper ? `<br>Es startet ein Beiprogramm: <b>${HO.esc(p.helper)}</b>` : ''}
        ${rights ? `<br>Es darf: <b>${rights}</b>.` : ''}
        <div style="margin-top:8px"><button class="btn sm primary" data-plugin-trust>${HO.icon('check', 'sm')} Freigeben und einschalten</button></div></div></div>` : ''}
      ${on && (p.settings || []).length ? `<form data-plugin-form>${HO.fields.html(p.settings, p.values)}
        <div class="form-actions"><button class="btn primary" type="submit">Speichern</button></div></form>` : ''}
      ${on && net.length ? `<p class="tiny muted" style="margin-top:10px">${HO.icon('globe', 'sm')} Verbindet sich mit ${net.map(e => HO.esc(e.hosts.join(', ')) + ' (' + HO.esc(e.desc) + ')').join('; ')}.</p>` : ''}
      ${on && lan.length && !net.length ? `<p class="tiny muted" style="margin-top:10px">${HO.icon('home', 'sm')} Spricht nur im Heimnetz: ${lan.map(e => HO.esc(e.desc) + (e.udp ? ' (auch per UDP, z. B. für die Suche)' : '')).join('; ')}.</p>` : ''}
      ${on && p.helper ? `<p class="tiny muted" style="margin-top:10px" data-plugin-helper>${HO.icon('shield', 'sm')} Startet ein Beiprogramm: ${HO.esc(p.helper)}</p>` : ''}
      ${on && rights ? `<p class="tiny muted" style="margin-top:10px" data-plugin-access>${HO.icon('shield', 'sm')} Darf ${rights}.</p>` : ''}
      <div class="plugin-body" data-plugin-body></div></section>`
  }

  /* Head tile above all plugins (since 0.21.1, before that a closing tile in the grid): add own plugins */
  function headerHTML() {
    return `<section class="card plugin-head" id="pluginInfo"><div class="card-head"><div><div class="eyebrow">Eigene nachrüsten</div><h3>Plugins</h3></div>
        <button class="btn sm" data-plugin-rescan>${HO.icon('refresh', 'sm')} Neu suchen</button></div>
      <p class="small muted" style="margin:0">Geräteanbindungen sind Plugins. Eigene Plugins kommen als Ordner nach <code>data/plugins/</code>
        (Vorlage: <code>public/plugins/_template</code>); nach „Neu suchen“ erscheinen sie hier und laufen erst nach deiner Freigabe.</p></section>`
  }

  // Load the own interface of a plugin (static/settings.js) – once per plugin.
  function loadScript(p) {
    if (!p.script || tiles[p.id]) return Promise.resolve()
    if (!loading[p.id]) {
      loading[p.id] = new Promise(resolve => {
        const s = document.createElement('script')
        s.src = `/plugins/${encodeURIComponent(p.id)}/static/settings.js?v=${encodeURIComponent(p.version || '')}`
        s.onload = resolve
        s.onerror = () => { delete loading[p.id]; resolve() }
        document.head.appendChild(s)
      })
    }
    return loading[p.id]
  }

  /* <option> list of the rooms for "Übernehmen"; preselected is the room named like the suggestion of the source */
  function roomOptions(hint) {
    const simple = name => String(name || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue')
      .replace(/ß/g, 'ss').replace(/[^a-z0-9]/g, '')
    return '<option value="">Raum: später zuordnen</option>' + rooms.filter(r => r.room_id).map(r =>
      `<option value="${HO.esc(r.room_id)}" ${hint && simple(r.name) === simple(hint) ? 'selected' : ''}>${HO.esc(r.name)}</option>`).join('')
  }

  // Tools a plugin gets for its tile: own API, devices, adopting, rooms, building blocks of the interface,
  // reloading.
  function toolsFor(page, p) {
    const base = 'plugins/' + encodeURIComponent(p.id) + '/'
    return {
      plugin: p, rooms, roomOptions, ui: HO.plugins.ui,
      api: (path, opts) => HO.api(base + path, opts),
      devices: () => HO.api(base + 'devices'),
      adopt: (deviceId, room) => HO.api(base + 'adopt', { method: 'POST', body: { device_id: deviceId, room: room || null } }),
      adoptAll: () => HO.api(base + 'adopt_all', { method: 'POST' }),
      syncNames: () => HO.api(base + 'names/sync', { method: 'POST' }),
      reload: () => renderTile(page, p.id),
      reloadAll: () => HO.plugins.refresh(page)      // fetch the state anew: head and switches of the tiles too
    }
  }

  /* Buttons for actions of a plugin on a device (see deviceActions above) – for lists in the tiles */
  function actionButtons(actions, device, pluginId) {
    return (actions || []).map(a => {
      const n = String(++actionSeq)
      actionRuns[n] = { action: a, device: Object.assign({ plugin: pluginId }, device) }
      return `<button class="btn sm${a.danger ? ' ghost danger' : ''}" data-plugin-action="${n}" data-action-key="${HO.esc(a.key)}" data-id="${HO.esc(device.id || '')}">${a.icon ? HO.icon(a.icon, 'sm') + ' ' : ''}${HO.esc(a.label)}</button>`
    }).join('')
  }
  /* Actions the source of a device offers (e.g. the Home Base for a leftover entry) –
     for tiles of other plugins. keys restricts to certain actions. */
  function sourceActions(device, keys) {
    const tile = tiles[device.plugin]
    if (!tile || !tile.deviceActions) return ''
    let acts = []
    try { acts = tile.deviceActions(device) || [] } catch (e) { }
    return actionButtons(keys ? acts.filter(a => keys.indexOf(a.key) >= 0) : acts, device, device.plugin)
  }

  // Draw the own interface of an enabled plugin into its tile; an error in it stays in the tile.
  async function renderTile(page, id) {
    const p = find(id), body = page.querySelector(`[data-plugin="${CSS.escape(id)}"] [data-plugin-body]`)
    if (!p || !body || p.state !== 'active') return
    await loadScript(p)
    const tile = tiles[id]
    if (!tile || !body.isConnected) return
    try { await tile.render(body, toolsFor(page, p)) } catch (e) {
      body.innerHTML = `<p class="small muted">${HO.esc(e.message || 'Die Kachel konnte nicht gezeichnet werden.')}</p>`
    }
  }

  // Dialog "Plugin freigeben": only enable foreign code after the ComfyOS password was entered.
  async function trust(page, p) {
    const s = HO.sheet({ title: `${p.name} freigeben`, body: `<p class="small muted">Zur Sicherheit bitte das ComfyOS-Passwort eingeben.</p>
      <form id="trustForm"><label class="field"><span>ComfyOS-Passwort</span><input type="password" id="trustPw" autocomplete="current-password"></label>
        <div class="form-actions"><button class="btn primary" type="submit">Freigeben und einschalten</button></div></form>` })
    s.el.querySelector('#trustForm').onsubmit = async e => {
      e.preventDefault()
      try {
        await HO.api(`plugins/${encodeURIComponent(p.id)}/enabled`, { method: 'PUT', body: { enabled: true, password: s.el.querySelector('#trustPw').value } })
        s.close(); HO.toast(`${p.name} eingeschaltet`); HO.state.overview = null
        HO.refreshConn()
        await HO.plugins.refresh(page)
      } catch (err) { HO.toast(err.message, 'bad', 5000) }
    }
  }

  // Clicks in the tab "Plugins": device actions, search again, enabling and disabling (disabling with a confirmation), approving.
  async function onClick(page, e) {
    const card = e.target.closest('[data-plugin]'), p = card && find(card.dataset.plugin)
    try {
      const act = e.target.closest('[data-plugin-action]'), run = act && actionRuns[act.dataset.pluginAction]
      if (run) return await run.action.run(run.device, () => { HO.state.overview = null; return HO.plugins.reload(page) })
      if (e.target.closest('[data-plugin-rescan]')) {
        const r = await HO.api('plugins/rescan', { method: 'POST' })
        HO.toast(r.plugins.length > list.length ? 'Neues Plugin gefunden' : 'Kein neues Plugin gefunden')
        return HO.plugins.refresh(page, r)
      }
      if (!p) return
      const toggle = e.target.closest('[data-plugin-toggle]')
      if (toggle) {
        const on = toggle.dataset.pluginToggle === '1'
        if (!on && !await HO.confirm(`${p.name} ausschalten? Seine Geräte verschwinden aus ComfyOS, bis du es wieder einschaltest. Einstellungen bleiben erhalten.`,
          { title: 'Plugin ausschalten', ok: 'Ausschalten' })) return
        await HO.api(`plugins/${encodeURIComponent(p.id)}/enabled`, { method: 'PUT', body: { enabled: on } })
        HO.toast(`${p.name} ${on ? 'eingeschaltet' : 'ausgeschaltet'}`); HO.state.overview = null
        HO.refreshConn()
        return HO.plugins.refresh(page)
      }
      if (e.target.closest('[data-plugin-trust]')) return trust(page, p)
    } catch (err) { HO.toast(err.message, 'bad', 5000) }
  }

  // Save the settings of a plugin and reload the tiles.
  async function onSubmit(page, e) {
    const form = e.target.closest('[data-plugin-form]'), card = form && form.closest('[data-plugin]'), p = card && find(card.dataset.plugin)
    if (!p) return
    e.preventDefault()
    try {
      await HO.api(`plugins/${encodeURIComponent(p.id)}/settings`, { method: 'PUT', body: { values: HO.fields.read(form, p.settings) } })
      HO.toast('Gespeichert'); HO.state.overview = null
      HO.refreshConn()
      await HO.plugins.refresh(page)
    } catch (err) { HO.toast(err.message, 'bad', 5000) }
  }

  HO.plugins = {
    ui: { actionButtons, sourceActions },       // list building blocks of the System page (settings.js registers the others)
    register(id, tile) { tiles[id] = tile },
    /* Actions the plugin of a device offers for this device (loads its interface when needed) */
    async deviceActions(device) {
      const id = device && device.plugin
      const meta = id && (find(id) || ((HO.state.overview || {}).integrations || []).find(p => p.id === id))
      if (!meta || !meta.script) return []
      await loadScript(meta)
      const tile = tiles[id]
      try { return tile && tile.deviceActions ? tile.deviceActions(device) || [] : [] } catch (e) { return [] }
    },
    /* Kacheln aller Plugins (Daten aus GET /api/plugins) – frei anordnbar im Raster, erkannt an [data-plugin]. */
    sectionHTML(data, roomList) {
      list = (data && data.plugins) || []; rooms = roomList || []
      return list.map(cardHTML).join('')
    },
    /* Head tile "Neu suchen" – always stands above the grid, never in it (since 0.21.1) */
    headerHTML,
    /* after insertion into the document: bind clicks (once per page), draw the own interfaces of the plugins */
    async mount(page) {
      if (!page.querySelector('#pluginInfo')) return
      if (!page.dataset.pluginsBound) {
        page.dataset.pluginsBound = '1'
        const mine = e => e.target.closest && e.target.closest('[data-plugin], #pluginInfo')
        page.addEventListener('click', e => { if (mine(e)) onClick(page, e) })
        page.addEventListener('submit', e => { if (mine(e)) onSubmit(page, e) })
      }
      // load all interfaces first: a tile may offer actions of another plugin (ui.sourceActions)
      await Promise.all(list.filter(p => p.state === 'active').map(loadScript))
      return Promise.all(list.map(p => renderTile(page, p.id)))
    },
    /* only redraw the contents of the tiles (e.g. after "Entfernen" in a list) */
    reload: page => Promise.all(list.map(p => renderTile(page, p.id))),
    /* Fetch the state anew and replace the tiles in their place (after enabling/disabling, saving, searching again);
       new plugins come after the last plugin, vanished ones drop out. onChange (System page) arranges afterwards. */
    async refresh(page, data) {
      const info = page.querySelector('#pluginInfo')
      if (!info) return
      const fresh = data || await HO.api('plugins')
      const before = list.map(p => p.id)
      list = fresh.plugins || []
      const tpl = document.createElement('div')
      let last = null
      for (const p of list) {
        tpl.innerHTML = cardHTML(p)
        const card = tpl.firstElementChild, old = page.querySelector(`[data-plugin="${CSS.escape(p.id)}"]`)
        if (old) old.replaceWith(card)
        else if (last) last.after(card)
        else (page.querySelector('#sysGrid') || info.parentNode).appendChild(card)     // loose tile: the grid sorts it in
        last = card
      }
      before.filter(id => !find(id)).forEach(id => { const c = page.querySelector(`[data-plugin="${CSS.escape(id)}"]`); if (c) c.remove() })
      tpl.innerHTML = headerHTML()
      info.replaceWith(tpl.firstElementChild)
      if (HO.plugins.onChange) HO.plugins.onChange(page)
      return HO.plugins.mount(page)
    },
    onChange: null
  }
})(window.HO)
