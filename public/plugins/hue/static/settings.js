/* Philips Hue – content of the plugin's tile under System → Plugins.

   Not paired:  address field + "Verbinden" (the user then presses the link button on the bridge).
   Paired:      bridge name, address, and the lists "Neu auf der Bridge", "Zu prüfen", "Verbunden", "Entfernt".

   The tile talks to two places:
     tools.api('status' | 'pair' | 'forget' | 'devices/<id>')   own routes of the plugin (routes.py)
     tools.devices(), tools.adopt(), tools.syncNames()          generic routes ComfyOS offers for every plugin
   Text that comes from the bridge or the user is always written with HO.esc. */
(function (HO) {
  'use strict'

  const HINT_NOT_PAIRED = 'Verbindet ComfyOS direkt mit der Bridge: Schalten ohne Umweg und Änderungen aus der Hue-App sofort sichtbar. ' +
    'ComfyOS entscheidet, welche Lampen es führt.'
  const HINT_NEW_LAMP = 'Neue Lampe: zuerst in der Hue-App anlernen, dann hier „Neu einlesen“ und unter „Neu auf der Bridge“ importieren.'

  /* Room options for the import of a lamp; the room with the same name as the Hue room is preselected. */
  function roomOptions(rooms, hueRoomName) {
    const simplify = name => String(name || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue')
      .replace(/ß/g, 'ss').replace(/[^a-z0-9]/g, '')
    const homeBaseRooms = rooms.filter(room => room.room_id && !String(room.room_id).startsWith('plan:'))
    return '<option value="">Raum: automatisch</option>' + homeBaseRooms.map(room =>
      `<option value="${HO.esc(room.room_id)}" ${simplify(room.name) === simplify(hueRoomName) ? 'selected' : ''}>${HO.esc(room.name)}</option>`).join('')
  }

  function hostRow(host, buttonHTML) {
    return `<label class="field" style="margin-top:0"><span>Adresse der Hue Bridge</span>
      <div class="row"><input type="text" id="hueHost" value="${HO.esc(host || '')}" placeholder="192.168.178.x" autocomplete="off"
        style="flex:1;min-width:0">${buttonHTML}</div></label>`
  }

  function deleteFromBridgeButton(deviceResourceId, name) {
    return `<button class="btn sm ghost danger" data-hue-del="${HO.esc(deviceResourceId)}" data-name="${HO.esc(name)}">Von Bridge löschen</button>`
  }

  /* One row per lamp the bridge has but ComfyOS does not show yet. */
  function newLampRow(ui, rooms, lamp) {
    const row = { name: lamp.name, id: lamp.id, props: lamp.details || {}, room: lamp.room_hint ? 'Hue-Raum: ' + lamp.room_hint : '' }
    const reachable = lamp.online !== false
    const deviceResourceId = (lamp.extra || {}).device_resource_id
    const actions = `<select class="sm" data-import-room="${HO.esc(lamp.id)}">${roomOptions(rooms, lamp.room_hint)}</select>
      <button class="btn sm primary" data-import="${HO.esc(lamp.id)}" data-name="${HO.esc(lamp.name)}">${HO.icon('plus', 'sm')} Importieren</button>
      ${reachable || !deviceResourceId ? '' : deleteFromBridgeButton(deviceResourceId, lamp.name)}`
    return ui.mRow(row, 'new', actions, reachable ? (lamp.room_hint || '') : 'nicht erreichbar', true)
  }

  /* One row per lamp that needs attention: unreachable, reporting a problem, or gone from the bridge. */
  function checkRow(ui, lamp) {
    const deviceResourceId = (lamp.source || {}).device_resource_id
    const actions = [
      ui.removeBtn(lamp),
      lamp.status === 'unreachable' && deviceResourceId ? deleteFromBridgeButton(deviceResourceId, lamp.name) : '',
      lamp.status === 'missing' ? ui.sourceActions(lamp, ['delete']) : ''
    ].join('')
    return ui.mRow(lamp, lamp.status, actions)
  }

  function removedRow(ui, entry) {
    const actions = ui.restoreBtn(entry) + (entry.orphan ? ui.sourceActions(entry, ['delete']) : '')
    return ui.mRow(entry, 'removed', actions, entry.orphan ? 'nur noch auf der Home Base' : '')
  }

  function notPairedHTML(status, removedGroup) {
    const suggested = (status.suggested || [])[0]
    return `${hostRow(suggested, `<button class="btn primary" data-act="huePair">${HO.icon('link', 'sm')} Verbinden</button>`)}
      ${suggested ? '<span class="hint tiny muted">Adresse von der Home Base übernommen.</span>' : ''}<div id="hueMsg"></div>
      <p class="small muted" style="margin-top:12px">${HINT_NOT_PAIRED}</p>${removedGroup}`
  }

  function pairedHTML(ui, rooms, status, devices, removedGroup) {
    const lamps = devices.devices || [], newLamps = devices.new || []
    const toCheck = lamps.filter(lamp => lamp.status !== 'ok'), connected = lamps.filter(lamp => lamp.status === 'ok')
    const namesDiffer = devices.names_differ || 0
    const problem = status.error || devices.error
    return `
      ${problem ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>${HO.esc(problem)}${
        status.connected ? '' : ' – solange laufen bekannte Lampen über die Home Base.'}</div></div>` : ''}
      <dl class="kv" style="margin-bottom:14px"><dt>Bridge</dt><dd>${HO.esc(status.name)}</dd>
        <dt>Lampen in ComfyOS</dt><dd>${lamps.length}${toCheck.length ? ` · ${toCheck.length} zu prüfen` : ''}${newLamps.length ? ` · ${newLamps.length} neu` : ''}</dd></dl>
      ${hostRow(status.host, '<button class="btn" data-act="huePair">Übernehmen</button>')}<div id="hueMsg"></div>
      <div class="form-actions" style="margin-top:4px"><button class="btn" data-act="hueReload">${HO.icon('refresh', 'sm')} Neu einlesen</button>
        <button class="btn ghost danger" data-act="hueForget">Trennen</button></div>
      ${namesDiffer ? `<div class="notice" style="margin-top:12px">${HO.icon('info', 'sm')}<div class="grow">${
        namesDiffer === 1 ? 'Eine Lampe heißt' : namesDiffer + ' Lampen heißen'} in der Hue-App anders als in ComfyOS.
        ComfyOS ist führend: Umbenennen in ComfyOS ändert den Namen auch auf der Bridge.
        <div style="margin-top:8px"><button class="btn sm primary" data-act="hueNames">Namen auf die Bridge übertragen</button></div></div></div>` : ''}
      <div class="mgroups">
        ${ui.mGroup('Neu auf der Bridge', newLamps.map(lamp => newLampRow(ui, rooms, lamp)), false,
          'Lampen, die ComfyOS noch nicht führt. Importieren übernimmt sie mit dem gewählten Raum.')}
        ${ui.mGroup('Zu prüfen', toCheck.map(lamp => checkRow(ui, lamp)), false)}
        ${ui.mGroup('Verbunden', connected.map(lamp => ui.mRow(lamp, 'ok', ui.removeBtn(lamp))), false)}
        ${removedGroup}</div>
      <p class="tiny muted" style="margin-top:12px">${HINT_NEW_LAMP}</p>`
  }

  const PAIR_WAIT_MS = 45000        // how long the tile keeps asking after "Verbinden" was tapped
  const PAIR_RETRY_MS = 2500

  function linkButtonNotice(secondsLeft) {
    return `<div class="notice" style="margin:10px 0">${HO.icon('info', 'sm')}<div>
      <b>Jetzt die runde Link-Taste oben auf der Hue Bridge drücken.</b><br>
      <span class="small muted">Warte noch ${secondsLeft} s …</span></div></div>`
  }

  /* Pair with the bridge. Until the link button was pressed the bridge answers "link: true";
     the tile then keeps asking every few seconds, so the user only has to press the button. */
  async function pair(body, tools) {
    const host = ((body.querySelector('#hueHost') || {}).value || '').trim()
    if (!host) { HO.toast('Bitte die Adresse der Hue Bridge eingeben.', 'warn'); return }
    const giveUpAt = Date.now() + PAIR_WAIT_MS
    while (Date.now() < giveUpAt) {
      const answer = await tools.api('pair', { method: 'POST', body: { host } })
      if (!body.isConnected) return                 // the user left the page
      if (answer.ok) {
        HO.toast(`${answer.name} verbunden`, 'ok', 5000)
        HO.state.overview = null
        return tools.reloadAll()
      }
      const message = body.querySelector('#hueMsg')
      if (message) message.innerHTML = linkButtonNotice(Math.ceil((giveUpAt - Date.now()) / 1000))
      await new Promise(resolve => setTimeout(resolve, PAIR_RETRY_MS))
    }
    const message = body.querySelector('#hueMsg')
    if (message) {
      message.innerHTML = `<div class="notice warn" style="margin:10px 0">${HO.icon('warn', 'sm')}<div>
        Keine Link-Taste erkannt. Taste drücken und erneut auf „Verbinden" tippen.</div></div>`
    }
  }

  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-act],[data-import],[data-hue-del]')
    if (!target || !body.contains(target)) return
    const action = target.dataset.act
    try {
      if (target.dataset.import) {
        const roomSelect = body.querySelector(`[data-import-room="${CSS.escape(target.dataset.import)}"]`)
        target.disabled = true
        await tools.adopt(target.dataset.import, roomSelect ? roomSelect.value : null)
        HO.toast(`${target.dataset.name} importiert`)
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.hueDel) {
        const confirmed = await HO.confirm(`„${target.dataset.name}" endgültig von der Hue Bridge löschen? Sie verschwindet damit auch aus der Hue-App. ` +
          'Zurück nur durch erneutes Anlernen der Lampe.', { title: 'Von der Bridge löschen', ok: 'Löschen', danger: true })
        if (!confirmed) return
        await tools.api('devices/' + encodeURIComponent(target.dataset.hueDel), { method: 'DELETE' })
        HO.toast('Von der Bridge gelöscht')
        return tools.reload()
      }
      if (action === 'huePair') {
        target.disabled = true
        try { await pair(body, tools) } finally { target.disabled = false }
        return
      }
      if (action === 'hueReload') {
        await tools.reload()
        HO.toast('Neu eingelesen')
        return
      }
      if (action === 'hueNames') {
        target.disabled = true
        const result = await tools.syncNames()
        if (result.failed.length) HO.toast(`${result.synced} übertragen, ${result.failed.length} fehlgeschlagen: ${result.failed[0]}`, 'warn', 6000)
        else HO.toast(`${result.synced} Namen auf die Bridge übertragen`)
        return tools.reload()
      }
      if (action === 'hueForget') {
        const confirmed = await HO.confirm('Direkte Verbindung zur Hue Bridge trennen? Lampen, die die Home Base kennt, laufen dann wieder über die Home Base; ' +
          'importierte Nur-Bridge-Lampen verschwinden aus ComfyOS, bis die Bridge wieder verbunden ist.', { title: 'Hue trennen', ok: 'Trennen', danger: true })
        if (!confirmed) return
        await tools.api('forget', { method: 'POST' })
        HO.state.overview = null
        return tools.reloadAll()
      }
    } catch (error) {
      HO.toast(error.message, 'bad', 5000)
    }
  }

  HO.plugins.register('hue', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) {
      const ui = tools.ui
      const [status, devices] = await Promise.all([
        tools.api('status').catch(error => ({ configured: false, suggested: [], error: error.message })),
        tools.devices().catch(error => ({ devices: [], new: [], removed: [], error: error.message }))
      ])
      const openGroups = ui.openState(body)
      const removedGroup = ui.mGroup('Entfernt', (devices.removed || []).map(entry => removedRow(ui, entry)), false,
        'In ComfyOS ausgeblendet. Bridge und Home Base bleiben unverändert – Einträge verschwinden von selbst, wenn es das Gerät nirgends mehr gibt. ' +
        'Übrig gebliebene Einträge der Home Base lassen sich dort löschen.')
      body.innerHTML = status.configured ? pairedHTML(ui, tools.rooms, status, devices, removedGroup) : notPairedHTML(status, removedGroup)
      ui.restoreOpen(body, openGroups)
      body.onclick = event => handleClick(event, body, tools)
    }
  })
})(window.HO)
