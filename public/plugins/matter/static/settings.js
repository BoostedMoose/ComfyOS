/* Matter – content of the plugin's tile under System → Plugins.

   Not set up:   hint + "Einrichten" (downloads Node.js from nodejs.org – only on this button, with version, size and
                 checksum shown before).
   Running:      1. Kopplung       open the commissioning window, pairing code and QR code, paired controllers
                 2. Freigaben      per device "für Matter sichtbar" (off by default); search; all, a room or a floor plan
                                   group at once; counter (no limit since 0.21.1, a hint above 50)
                 3. Matter-Geräte  add a device of another maker with its share code, list, remove

   The tile talks to two places:
     tools.api('status' | 'runtime/install' | 'commissioning' | 'controllers/<n>' | 'shares' | 'controller/…')
                                              own routes of the plugin (routes.py)
     tools.devices(), tools.adopt()           generic routes ComfyOS offers for every plugin
   Names of devices and controllers are foreign text and always written with HO.esc. */
(function (HO) {
  'use strict'

  const KIND_LABELS = {
    on_off_light: 'Lampe', dimmable_light: 'Lampe (dimmbar)', color_temperature_light: 'Lampe (Weißton)',
    extended_color_light: 'Lampe (Farbe)', on_off_plug: 'Schalter', thermostat: 'Thermostat', window_covering: 'Rollladen',
    contact_sensor: 'Kontakt', occupancy_sensor: 'Bewegung', temperature_sensor: 'Temperatur', humidity_sensor: 'Feuchte',
    smoke_alarm: 'Rauchmelder'
  }
  const HINT_SETUP = 'Matter verbindet ComfyOS mit Alexa, Apple Home und Google Home – ohne Skill, ohne Konto, nur im Heimnetz. ' +
    'Dafür läuft neben ComfyOS ein Beiprogramm (matter.js unter Node.js). Es ist eingesperrt: Es kann keine Dateien lesen oder ' +
    'schreiben und keine Programme starten.'
  const HINT_PORTS = 'Matter spricht nur im Heimnetz. Die Matter-Ports gehören nie nach außen – keine Portfreigabe im Router.'
  const HINT_SHARE = 'Nur was hier eingeschaltet ist, sehen Alexa, Apple Home und Google Home. Ein gesperrtes Gerät bleibt ' +
    'sichtbar, lässt sich von dort aber nicht schalten.'
  const HINT_CONTROLLER = 'In der App des Geräts (oder in Alexa, Apple Home, Google Home) „Teilen“ bzw. „Mit weiterer App koppeln“ ' +
    'wählen und den angezeigten Code hier eintragen. Gekoppelt wird über das Netz – Thread-Geräte nur, wenn ein Border Router im ' +
    'Haus ist und dieser Rechner dessen Routen annimmt.'
  let pollTimer = null
  let headRefreshedAt = 0
  let lastStatus = {}            // last answer of GET status – the bulk switches work on it
  let lastGroups = []            // device groups of the floor plan: [{id, name, members}]
  let searchText = ''            // search in the share list; kept while the tile redraws

  /* 34970112332 -> "3497-011-2332": the way Matter prints the manual pairing code. */
  function formatCode(code) {
    const digits = String(code || '')
    return digits.length === 11 ? `${digits.slice(0, 4)}-${digits.slice(4, 7)}-${digits.slice(7)}` : digits
  }

  /* Draw the QR code from its rows of "0" and "1" as an SVG: dark modules on white, with the quiet zone it needs. */
  function qrSVG(rows) {
    if (!rows || !rows.length) return ''
    const quiet = 4, size = rows.length + quiet * 2
    let path = ''
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        if (row.charAt(x) === '1') path += `M${x + quiet} ${y + quiet}h1v1h-1z`
      }
    })
    return `<svg viewBox="0 0 ${size} ${size}" width="168" height="168" role="img" aria-label="QR-Code zum Koppeln" shape-rendering="crispEdges"
      style="border-radius:8px;flex:none"><rect width="${size}" height="${size}" fill="white"></rect><path d="${path}" fill="black"></path></svg>`
  }

  /* Tile content while Node.js is missing: what will be downloaded, from where, and the button. */
  function setupHTML(status) {
    const runtime = status.runtime || {}
    if (!runtime.supported) {
      return `<p class="small muted" style="margin-top:4px">${HINT_SETUP}</p>
        <div class="notice warn">${HO.icon('warn', 'sm')}<div>${HO.esc(runtime.unsupported_text || 'Für dieses System gibt es kein Node.js.')}</div></div>`
    }
    return `<p class="small muted" style="margin-top:4px">${HINT_SETUP}</p>
      ${status.problem ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>${HO.esc(status.problem)}</div></div>` : ''}
      <div class="notice">${HO.icon('globe', 'sm')}<div class="grow">„Einrichten“ lädt einmalig Node.js ${HO.esc(runtime.version)} von
        <b>${HO.esc(runtime.host)}</b> (rund ${runtime.megabytes} MB) und prüft die Prüfsumme. Dabei erfährt nodejs.org die
        IP-Adresse dieses Anschlusses. Sonst wird nichts geladen – auch später nicht.
        <details style="margin-top:6px"><summary class="tiny muted">Einzelheiten</summary>
          <dl class="kv small"><dt>Datei</dt><dd>${HO.esc(runtime.file)}</dd><dt>SHA-256</dt><dd style="word-break:break-all">${HO.esc(runtime.sha256)}</dd></dl>
        </details></div></div>
      <div class="form-actions"><button class="btn primary" data-act="install">${HO.icon('plus', 'sm')} Einrichten</button></div>`
  }

  /* Section "Kopplung": paired controllers, the button to open the window, and – while it is open – code and QR code. */
  function pairingHTML(status) {
    const pairing = status.pairing, controllers = status.controllers || []
    // One row per controller, built like the device rows of the other lists: the name, and when opened the field to rename it.
    const rows = controllers.map(controller => `<details class="mrow"><summary><span class="mrow-name">${HO.esc(controller.name)}</span>
        <span class="mrow-sub small muted">Controller ${controller.number}</span><span class="badge ok">gekoppelt</span></summary>
      <div class="mrow-body">
        <label class="field" style="margin-top:0"><span>Name in ComfyOS</span>
          <div class="row"><input type="text" maxlength="40" value="${HO.esc(controller.name)}" data-controller-name="${controller.index}"
            autocomplete="off" style="flex:1;min-width:0"><button class="btn sm" data-rename="${controller.index}">Speichern</button></div>
          <span class="hint tiny muted">Leer lassen = der Name, den der Controller mitbringt.</span></label>
        <div class="row wrap" style="margin-top:10px">
          <button class="btn sm ghost danger" data-controller="${controller.index}" data-name="${HO.esc(controller.name)}">${HO.icon('trash', 'sm')} Entfernen</button>
        </div></div></details>`)
    const openUntil = pairing && pairing.open_until ? new Date(pairing.open_until * 1000) : null
    const codeBlock = pairing && openUntil ? `<div class="row g8 wrap" style="align-items:center;margin-top:10px">${qrSVG(pairing.rows)}
        <div class="grow"><div class="eyebrow">Zahlencode</div>
          <div style="font-size:1.4rem;letter-spacing:.06em;font-variant-numeric:tabular-nums" data-code>${HO.esc(formatCode(pairing.manual))}</div>
          <p class="tiny muted" style="margin:6px 0 0">Kopplung offen bis ${HO.esc(openUntil.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }))} Uhr.
            In der Alexa-, Home- oder Google-Home-App „Gerät hinzufügen → Matter“ wählen und den Code scannen oder eintippen.</p>
          <div style="margin-top:8px"><button class="btn sm" data-act="closeWindow">Kopplung schließen</button></div></div></div>` : ''
    return `<details class="mgroup" data-g="Kopplung" open><summary><span>Kopplung</span><span class="badge">${controllers.length}</span></summary>
      ${controllers.length ? `<div class="mlist" style="margin-top:6px">${rows.join('')}</div>` : '<p class="small muted" style="margin:6px 0 0">Noch kein Controller gekoppelt.</p>'}
      ${codeBlock || `<div class="form-actions"><button class="btn primary" data-act="openWindow">${HO.icon('plus', 'sm')} Kopplung öffnen</button></div>
        <p class="tiny muted" style="margin:6px 0 0">Öffnet die Bridge für 15 Minuten und zeigt Zahlencode und QR-Code. Jeder weitere Controller koppelt auf demselben Weg.</p>`}
      </details>`
  }

  /* One row of the share list: name, kind, and the switch "für Matter sichtbar". */
  function shareRow(item) {
    const on = item.shared
    return `<div class="row g8 mt-share" data-share-row data-name="${HO.esc(String(item.name || '').toLowerCase())}" style="align-items:center;padding:6px 0">
      <span class="grow" style="min-width:0"><span>${HO.esc(item.name)}</span>
        <span class="small muted"> · ${HO.esc(KIND_LABELS[item.kind] || '')}${item.locked ? ' · gesperrt' : ''}</span></span>
      <button class="room-switch ${on ? 'on' : ''}" role="switch" aria-checked="${on}" data-share="${HO.esc(item.key)}" data-on="${on ? 0 : 1}"
        title="Für Matter sichtbar" aria-label="${HO.esc(item.name)} für Matter sichtbar"></button></div>`
  }

  /* Switch for a whole set of devices (all, a room, a floor plan group): on when all are shared, "teilweise" in between. */
  function bulkSwitch(bulkKey, items, label) {
    const shared = items.filter(item => item.shared).length
    const allOn = items.length > 0 && shared === items.length
    return `<span class="small muted" style="margin-right:6px">${shared ? (allOn ? 'alle' : `${shared} von ${items.length}`) : ''}</span>
      <button class="room-switch ${allOn ? 'on' : ''} ${shared && !allOn ? 'part' : ''}" role="switch" aria-checked="${allOn}" data-bulk="${HO.esc(bulkKey)}"
        data-on="${allOn ? 0 : 1}" title="${HO.esc(label)}" aria-label="${HO.esc(label)}"></button>`
  }

  /* A foldable block of the share list (room, group, the home) with a switch for all of it in its head. */
  function shareBlock(groupKey, title, items, bulkKey, hint) {
    if (!items.length) return ''
    return `<details class="mgroup mt-block" data-g="${HO.esc(groupKey)}"><summary><span class="grow">${HO.esc(title)}</span>
        ${bulkSwitch(bulkKey, items, `${title}: alle für Matter sichtbar`)}</summary>
      ${hint ? `<p class="tiny muted" style="margin:2px 0 4px">${hint}</p>` : ''}${items.map(shareRow).join('')}</details>`
  }

  /* Devices of ComfyOS by bulk key: all, a room or a floor plan group (members come from the floor plan). */
  function bulkItems(bulkKey) {
    const offered = (lastStatus.offered || []).filter(item => item.group !== 'home')
    if (bulkKey === 'all') return offered
    if (bulkKey.indexOf('room:') === 0) return offered.filter(item => (item.room || 'Nicht zugeordnet') === bulkKey.slice(5))
    if (bulkKey.indexOf('group:') === 0) {
      const group = lastGroups.find(g => g.id === bulkKey.slice(6))
      const members = (group && group.members) || []
      return offered.filter(item => members.indexOf(item.key) >= 0)
    }
    if (bulkKey === 'home') return (lastStatus.offered || []).filter(item => item.group === 'home')
    return []
  }

  /* Section "Freigaben": counter, search, all on/off, the home as devices, floor plan groups, then the devices by room. */
  function sharesHTML(status) {
    const offered = status.offered || [], shares = status.shares || { used: 0 }
    const devices = offered.filter(item => item.group !== 'home')
    const rooms = devices.map(item => item.room || 'Nicht zugeordnet').filter((room, i, all) => all.indexOf(room) === i)
      .sort((a, b) => a.localeCompare(b, 'de'))
    const groupBlocks = lastGroups.map(group => shareBlock('Gruppe ' + group.id, group.name || 'Gruppe', bulkItems('group:' + group.id),
      'group:' + group.id))
    const tooMany = shares.used > (shares.advice || 50)
    return `<details class="mgroup" data-g="Freigaben"><summary><span>Für Matter sichtbar</span>
        <span class="badge" data-share-count>${shares.used} freigegeben</span></summary>
      <p class="tiny muted" style="margin:4px 0 8px">${HINT_SHARE}</p>
      ${tooMany ? `<div class="notice warn" style="margin-bottom:8px">${HO.icon('warn', 'sm')}<div class="small">Mehr als ${shares.advice || 50} Geräte an einer Bridge:
        Alexa wird damit bei manchen Echo-Modellen langsam oder verliert Geräte. Klappt etwas nicht, zuerst die Zahl verringern.</div></div>` : ''}
      <div class="row g8 wrap" style="align-items:center;margin-bottom:6px">
        <input type="text" data-share-search placeholder="Gerät suchen …" value="${HO.esc(searchText)}" autocomplete="off" style="flex:1;min-width:140px">
        <button class="btn sm" data-bulk="all" data-on="1">${HO.icon('check', 'sm')} Alle</button>
        <button class="btn sm ghost" data-bulk="all" data-on="0">Keine</button></div>
      <div class="mgroups">
        ${shareBlock('Die Wohnung als Geräte', 'Die Wohnung als Geräte', offered.filter(item => item.group === 'home'), 'home',
          '„&lt;Raum&gt; Licht“, „Alle Lichter“, Hausszenen und „Zuhause“ – für Sprachbefehle ohne Gruppen in der Alexa- oder Home-App.')}
        ${groupBlocks.length ? `<div class="eyebrow" style="margin:10px 0 2px">Gruppen aus dem Grundriss</div>${groupBlocks.join('')}` : ''}
        ${rooms.length ? `<div class="eyebrow" style="margin:10px 0 2px">Räume</div>` : ''}
        ${rooms.map(room => shareBlock('Raum ' + room, room, bulkItems('room:' + room), 'room:' + room)).join('')}
      </div>
      ${offered.length ? '' : '<p class="small muted">Noch keine Geräte in ComfyOS.</p>'}</details>`
  }

  /* Filter the share list by the search text: only matching rows, blocks with a hit are opened. */
  function applySearch(body) {
    const text = searchText.trim().toLowerCase()
    body.querySelectorAll('[data-share-row]').forEach(row => { row.style.display = !text || row.dataset.name.indexOf(text) >= 0 ? '' : 'none' })
    body.querySelectorAll('details.mt-block').forEach(block => {
      const hits = block.querySelectorAll('[data-share-row]:not([style*="none"])').length
      block.style.display = !text || hits ? '' : 'none'
      if (text && hits) block.open = true
    })
  }

  /* Short overview at the top of a running tile: controllers, shared devices, devices of other makers. */
  function summaryHTML(status) {
    const stat = (value, label) => `<div class="mt-stat"><b>${value}</b><span class="tiny muted">${label}</span></div>`
    const nodes = status.nodes || []
    return `<div class="mt-stats">${stat((status.controllers || []).length, 'Controller gekoppelt')}
      ${stat((status.shares || {}).used || 0, 'für Matter sichtbar')}${stat(nodes.length, 'Matter-Geräte eingebunden')}</div>`
  }

  /* What the device attestation found, in plain words. */
  function attestationNote(findings) {
    const list = findings || []
    if (list.some(finding => finding.indexOf('error:') === 0 || finding.indexOf('Test') >= 0)) return 'Herkunft unbekannt (Testzertifikat)'
    if (list.length) return 'Herkunft nicht online geprüft'
    return ''
  }

  /* One commissioned Matter device with its "Entfernen" button. */
  function nodeRow(node) {
    const note = attestationNote(node.attestation)
    return `<div class="row g8" style="align-items:center;margin-top:6px">
      <span class="grow" style="min-width:0">${HO.esc(node.name)}<span class="small muted"> · ${HO.esc(node.vendor || 'Matter')}
        · ${node.devices} ${node.devices === 1 ? 'Gerät' : 'Geräte'}${node.online ? '' : ' · nicht erreichbar'}${note ? ' · ' + HO.esc(note) : ''}</span></span>
      <button class="btn sm ghost danger" data-node="${HO.esc(node.node)}" data-name="${HO.esc(node.name)}">${HO.icon('trash', 'sm')} Entfernen</button></div>`
  }

  /* One device of a commissioned node that ComfyOS does not show yet. */
  function newDeviceRow(ui, tools, device) {
    const row = { name: device.name, id: device.id, props: device.details || {}, room: '' }
    const actions = `<select class="sm" data-adopt-room="${HO.esc(device.id)}">${tools.roomOptions(device.room_hint)}</select>
      <button class="btn sm primary" data-adopt="${HO.esc(device.id)}" data-name="${HO.esc(device.name)}">${HO.icon('plus', 'sm')} Übernehmen</button>`
    return ui.mRow(row, 'new', actions, device.model || '', true)
  }

  /* Section "Matter-Geräte einbinden": the form for the share code, commissioned nodes and their devices. */
  function controllerHTML(ui, tools, status, devices) {
    const nodes = status.nodes || [], shown = devices.devices || [], found = devices.new || []
    return `<details class="mgroup" data-g="Matter-Geräte"><summary><span>Matter-Geräte einbinden</span><span class="badge">${nodes.length}</span></summary>
      <p class="tiny muted" style="margin:4px 0 6px">${HINT_CONTROLLER}</p>
      ${nodes.map(nodeRow).join('')}
      <form data-pair-form style="margin-top:10px"><label class="field" style="margin-top:0"><span>Code aus „Teilen“</span>
        <div class="row"><input type="text" name="code" maxlength="260" required placeholder="1234-567-8901 oder MT:…" autocomplete="off"
          inputmode="text" style="flex:1;min-width:0"><button class="btn primary" type="submit">${HO.icon('plus', 'sm')} Gerät hinzufügen</button></div></label></form>
      </details>
      ${ui.mGroup('Neu über Matter', found.map(device => newDeviceRow(ui, tools, device)), found.length > 0 && found.length <= 6,
        'Über Matter eingebunden, aber noch nicht in ComfyOS übernommen.')}
      ${ui.mGroup('Über Matter verbunden', shown.map(device => ui.mRow(device, device.status, ui.removeBtn(device))), false)}
      ${ui.mGroup('Entfernt', (devices.removed || []).map(entry => ui.mRow(entry, 'removed', ui.restoreBtn(entry))), false)}`
  }

  /* Whole tile content for a running plugin. */
  function contentHTML(ui, tools, status, devices) {
    if (!status.runtime || !status.runtime.installed) return setupHTML(status)
    const notice = status.problem || (status.online ? '' : 'Matter startet gerade …')
    return `${notice ? `<div class="notice ${status.problem ? 'warn' : ''}" style="margin-bottom:12px">${HO.icon(status.problem ? 'warn' : 'refresh', 'sm')}<div>${HO.esc(notice)}</div></div>` : ''}
      ${status.online ? summaryHTML(status) : ''}
      <div class="mgroups">${status.online ? pairingHTML(status) : ''}${sharesHTML(status)}${status.online ? controllerHTML(ui, tools, status, devices) : ''}</div>
      <p class="tiny muted" style="margin-top:12px">${HO.icon('shield', 'sm')} ${HINT_PORTS}</p>`
  }

  /* Clicks in the tile: set up, open and close the window, remove a controller, share a device, adopt, remove a node. */
  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-act],[data-controller],[data-rename],[data-share],[data-bulk],[data-adopt],[data-node]')
    if (!target || !body.contains(target)) return
    if (target.dataset.bulk && target.closest('summary')) event.preventDefault()     // switch in a block head: do not fold
    try {
      if (target.dataset.bulk) {
        const items = bulkItems(target.dataset.bulk), shared = target.dataset.on === '1'
        if (!items.length) return
        if (target.dataset.bulk === 'all' && !shared && !await HO.confirm('Alle Geräte für Matter ausblenden? Sie verschwinden aus Alexa, Apple Home und Google Home.',
          { title: 'Keine freigeben', ok: 'Ausblenden', danger: true })) return
        target.disabled = true
        await tools.api('shares', { method: 'PUT', body: { keys: items.map(item => item.key), shared } })
        return tools.reload()
      }
      if (target.dataset.act === 'install') {
        target.disabled = true
        HO.toast('Node.js wird geladen …', 'ok', 4000)
        await tools.api('runtime/install', { method: 'POST' })
        HO.toast('Matter ist eingerichtet')
        HO.refreshConn()
        return tools.reloadAll()
      }
      if (target.dataset.act === 'openWindow' || target.dataset.act === 'closeWindow') {
        await tools.api('commissioning', { method: 'POST', body: { open: target.dataset.act === 'openWindow' } })
        return tools.reload()
      }
      if (target.dataset.rename) {
        const field = body.querySelector(`[data-controller-name="${CSS.escape(target.dataset.rename)}"]`)
        const answer = await tools.api('controllers/' + encodeURIComponent(target.dataset.rename) + '/name',
          { method: 'PUT', body: { name: field ? field.value.trim() : '' } })
        HO.toast(`Heißt jetzt „${answer.name}“`)
        return tools.reload()
      }
      if (target.dataset.controller) {
        const confirmed = await HO.confirm(`„${target.dataset.name}“ von ComfyOS trennen? Die Geräte verschwinden dort; neu koppeln geht jederzeit.`,
          { title: 'Controller entfernen', ok: 'Entfernen', danger: true })
        if (!confirmed) return
        await tools.api('controllers/' + encodeURIComponent(target.dataset.controller), { method: 'DELETE' })
        HO.toast('Entfernt')
        return tools.reload()
      }
      if (target.dataset.share) {
        target.disabled = true
        await tools.api('shares', { method: 'PUT', body: { key: target.dataset.share, shared: target.dataset.on === '1' } })
        return tools.reload()
      }
      if (target.dataset.adopt) {
        const roomSelect = body.querySelector(`[data-adopt-room="${CSS.escape(target.dataset.adopt)}"]`)
        target.disabled = true
        await tools.adopt(target.dataset.adopt, roomSelect ? roomSelect.value : null)
        HO.toast(`${target.dataset.name} übernommen`)
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.node) {
        const confirmed = await HO.confirm(`„${target.dataset.name}“ aus ComfyOS entfernen? ComfyOS meldet sich bei dem Gerät ab; in seiner eigenen App bleibt es.`,
          { title: 'Matter-Gerät entfernen', ok: 'Entfernen', danger: true })
        if (!confirmed) return
        const result = await tools.api('controller/nodes/' + encodeURIComponent(target.dataset.node), { method: 'DELETE' })
        HO.toast(result.clean ? 'Entfernt' : 'Entfernt – das Gerät war nicht erreichbar und kennt ComfyOS noch.', result.clean ? 'ok' : 'warn', 6000)
        HO.state.overview = null
        return tools.reload()
      }
    } catch (error) {
      target.disabled = false
      HO.toast(error.message, 'bad', 6000)
    }
  }

  /* Commission a Matter device with the code from the form. This can take a minute. */
  async function pairDevice(form, tools) {
    const button = form.querySelector('button')
    button.disabled = true
    HO.toast('Gerät wird gekoppelt – das dauert bis zu einer Minute …', 'ok', 6000)
    try {
      await tools.api('controller/pair', { method: 'POST', body: { code: form.elements.code.value.trim() } })
      HO.toast('Gerät eingebunden – unter „Neu über Matter“ übernehmen.', 'ok', 6000)
      HO.state.overview = null
      await tools.reload()
    } finally {
      button.disabled = false
    }
  }

  /* While the commissioning window is open or Matter is starting, look again every few seconds – unless the user types. */
  function pollWhile(body, tools, status) {
    if (pollTimer) clearTimeout(pollTimer)
    const waiting = status.runtime && status.runtime.installed && (!status.online || (status.pairing && status.pairing.open_until))
    if (!waiting) return
    pollTimer = setTimeout(() => {
      pollTimer = null
      const typing = document.activeElement && body.contains(document.activeElement) && document.activeElement.tagName === 'INPUT'
      if (body.isConnected && !typing) tools.reload()
    }, 5000)
  }

  HO.plugins.register('matter', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) {
      const ui = tools.ui
      const [status, devices, plan] = await Promise.all([
        tools.api('status').catch(error => ({ runtime: { installed: true }, problem: error.message })),
        tools.devices().catch(() => ({ devices: [], new: [], removed: [] })),
        HO.api('floorplan').catch(() => ({}))
      ])
      lastStatus = status
      lastGroups = HO.floorplan.groupsOf(plan).filter(group => group.id && (group.members || []).length)
      // The head of the tile (badge "Online") is drawn by ComfyOS from the plugin list. When Matter has come up or gone
      // down since then, fetch that list again – at most every few seconds, so a flapping state cannot loop.
      const shownConnected = Boolean(tools.plugin.status && tools.plugin.status.connected)
      const isInstalled = Boolean(status.runtime && status.runtime.installed)
      if (isInstalled && Boolean(status.online) !== shownConnected && Date.now() - headRefreshedAt > 3000) {
        headRefreshedAt = Date.now()
        HO.refreshConn()
        return tools.reloadAll()
      }
      const openGroups = ui.openState(body)
      body.innerHTML = contentHTML(ui, tools, status, devices)
      ui.restoreOpen(body, openGroups)
      const form = body.querySelector('[data-pair-form]')
      if (form) {
        form.onsubmit = async event => {
          event.preventDefault()
          try { await pairDevice(form, tools) } catch (error) { HO.toast(error.message, 'bad', 8000) }
        }
      }
      body.onclick = event => handleClick(event, body, tools)
      const search = body.querySelector('[data-share-search]')
      if (search) {
        search.oninput = () => { searchText = search.value; applySearch(body) }
        applySearch(body)
      }
      pollWhile(body, tools, status)
    }
  })
})(window.HO)
