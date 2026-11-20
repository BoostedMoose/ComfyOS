/* Sonos – content of the plugin's tile under System → Plugins.

   No speakers yet:  "Lautsprecher suchen" (a search in the home network) or the address of one speaker by hand.
   Speakers known:   the household – every room with what it does and whom it plays with – and the lists
                     "Neu bei Sonos" (rooms ComfyOS does not show yet), "Zu prüfen", "Verbunden", "Entfernt".

   The tile talks to two places:
     tools.api('status' | 'search' | 'connect' | 'forget' | 'group' | 'ungroup')   own routes of the plugin (routes.py)
     tools.devices(), tools.adopt(), tools.adoptAll()                               generic routes ComfyOS offers for every plugin
   Room names come from the speakers and are always written with HO.esc. */
(function (HO) {
  'use strict'

  const HINT_START = 'ComfyOS spricht die Lautsprecher direkt im Heimnetz an – ohne Sonos-Konto und ohne Cloud. ' +
    'Die Suche fragt einmal per Rundruf im Heimnetz, wer ein Sonos-Lautsprecher ist; sonst wird nichts gesucht.'
  const HINT_UPNP = 'Antwortet kein Lautsprecher: In der Sonos-App unter Konto → Datenschutz & Sicherheit „UPnP“ einschalten.'

  function hostRow() {
    return `<label class="field"><span>Oder die Adresse eines Lautsprechers</span>
      <div class="row"><input type="text" id="sonosHost" placeholder="192.168.178.x" autocomplete="off" maxlength="120"
        style="flex:1;min-width:0"><button class="btn" data-act="sonosConnect">Übernehmen</button></div></label>`
  }

  function startHTML(status, removedGroup) {
    const known = (status.suggested || {}).count || 0
    return `${known ? `<div class="notice" style="margin-bottom:12px">${HO.icon('info', 'sm')}<div>Ein anderes Plugin kennt
        ${known === 1 ? 'einen Sonos-Lautsprecher' : known + ' Sonos-Lautsprecher'}. Nach der Suche steuert ComfyOS sie direkt.</div></div>` : ''}
      <div class="form-actions" style="margin-top:0"><button class="btn primary" data-act="sonosSearch">${HO.icon('refresh', 'sm')} Lautsprecher suchen</button></div>
      ${hostRow()}
      <p class="small muted" style="margin-top:12px">${HINT_START}</p>
      <p class="tiny muted">${HINT_UPNP}</p>${removedGroup}`
  }

  function roomState(room) {
    if (!room.online) return '<span class="badge bad">antwortet nicht</span>'
    return room.playing ? '<span class="badge ok">spielt</span>' : '<span class="badge">pausiert</span>'
  }

  /* One row per room of the household, with the controls to group it. `adopted` = ComfyOS shows the room as a device. */
  function householdRow(room, rooms, adopted) {
    const others = rooms.filter(other => other.device_id !== room.device_id && other.online)
    const together = room.group.length ? 'spielt mit ' + room.group.join(', ') : 'spielt allein'
    const groupControls = !adopted || !room.online ? ''
      : (room.group.length ? `<button class="btn sm" data-ungroup="${HO.esc(room.device_id)}">Aus der Gruppe nehmen</button>` : '') +
        (others.length ? `<select class="sm" data-group-target="${HO.esc(room.device_id)}"><option value="">Mitspielen bei …</option>${
          others.map(other => `<option value="${HO.esc(other.device_id)}">${HO.esc(other.name)}</option>`).join('')}</select>` : '')
    return `<details class="mrow"><summary><span class="mrow-name">${HO.esc(room.name)}</span>
        <span class="mrow-sub small muted">${HO.esc(room.model || 'Sonos')}</span>${roomState(room)}</summary>
      <div class="mrow-body">
        <dl class="kv small"><dt>Adresse</dt><dd>${HO.esc(room.host)}</dd>
          <dt>Boxen</dt><dd>${room.boxes}</dd><dt>Gruppe</dt><dd>${HO.esc(together)}</dd></dl>
        ${groupControls ? `<div class="row wrap" style="margin-top:10px">${groupControls}</div>` : ''}
        ${adopted ? '' : '<p class="tiny muted" style="margin-top:8px">Erst nach dem Übernehmen lässt sich der Raum gruppieren.</p>'}
      </div></details>`
  }

  function newRoomRow(ui, tools, device) {
    const row = { name: device.name, id: device.id, props: device.details || {}, room: device.room_hint ? 'Sonos-Raum: ' + device.room_hint : '' }
    const actions = `<select class="sm" data-adopt-room="${HO.esc(device.id)}">${tools.roomOptions(device.room_hint)}</select>
      <button class="btn sm primary" data-adopt="${HO.esc(device.id)}" data-name="${HO.esc(device.name)}">${HO.icon('plus', 'sm')} Übernehmen</button>`
    return ui.mRow(row, 'new', actions, device.online === false ? 'antwortet nicht' : '', true)
  }

  function householdHTML(ui, tools, status, devices, removedGroup) {
    const shown = devices.devices || [], found = devices.new || []
    const notAdopted = new Set(found.map(device => device.id))
    const toCheck = shown.filter(device => device.status !== 'ok'), connected = shown.filter(device => device.status === 'ok')
    return `
      ${status.problem ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>${HO.esc(status.problem)}</div></div>` : ''}
      <dl class="kv" style="margin-bottom:14px"><dt>Räume</dt><dd>${status.rooms.length}</dd>
        <dt>Favoriten</dt><dd>${status.favorites}</dd></dl>
      <div class="mlist" style="margin-bottom:12px">${status.rooms.map(room => householdRow(room, status.rooms, !notAdopted.has(room.device_id))).join('')}</div>
      <div class="form-actions" style="margin-top:4px"><button class="btn" data-act="sonosSearch">${HO.icon('refresh', 'sm')} Neu suchen</button>
        ${found.length > 1 ? `<button class="btn primary" data-act="sonosAdoptAll">${HO.icon('plus', 'sm')} Alle übernehmen</button>` : ''}
        <button class="btn ghost danger" data-act="sonosForget">Vergessen</button></div>
      <div class="mgroups">
        ${ui.mGroup('Neu bei Sonos', found.map(device => newRoomRow(ui, tools, device)), false,
          'Räume, die ComfyOS noch nicht zeigt. Übernehmen fügt sie mit dem gewählten Raum hinzu.')}
        ${ui.mGroup('Zu prüfen', toCheck.map(device => ui.mRow(device, device.status, ui.removeBtn(device))), false)}
        ${ui.mGroup('Verbunden', connected.map(device => ui.mRow(device, 'ok', ui.removeBtn(device))), false)}
        ${removedGroup}</div>
      <p class="tiny muted" style="margin-top:12px">${HINT_UPNP}</p>`
  }

  function foundText(answer) {
    return answer.found === 1 ? 'Ein Raum gefunden' : `${answer.found} Räume gefunden`
  }

  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-act],[data-adopt],[data-ungroup]')
    if (!target || !body.contains(target)) return
    const action = target.dataset.act
    try {
      if (target.dataset.adopt) {
        const roomSelect = body.querySelector(`[data-adopt-room="${CSS.escape(target.dataset.adopt)}"]`)
        target.disabled = true
        await tools.adopt(target.dataset.adopt, roomSelect ? roomSelect.value : null)
        HO.toast(`${target.dataset.name} übernommen`)
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.ungroup) {
        target.disabled = true
        await tools.api('ungroup', { method: 'POST', body: { device_id: target.dataset.ungroup } })
        return tools.reload()
      }
      if (action === 'sonosSearch') {
        target.disabled = true
        try {
          const answer = await tools.api('search', { method: 'POST' })
          HO.toast(foundText(answer), 'ok', 5000)
        } finally { target.disabled = false }
        HO.state.overview = null
        return tools.reloadAll()
      }
      if (action === 'sonosConnect') {
        const host = ((body.querySelector('#sonosHost') || {}).value || '').trim()
        if (!host) { HO.toast('Bitte die Adresse eines Lautsprechers eingeben.', 'warn'); return }
        target.disabled = true
        try {
          const answer = await tools.api('connect', { method: 'POST', body: { host } })
          HO.toast(foundText(answer), 'ok', 5000)
        } finally { target.disabled = false }
        HO.state.overview = null
        return tools.reloadAll()
      }
      if (action === 'sonosAdoptAll') {
        target.disabled = true
        const result = await tools.adoptAll()
        HO.toast(`${result.adopted} übernommen`)
        HO.state.overview = null
        return tools.reload()
      }
      if (action === 'sonosForget') {
        const confirmed = await HO.confirm('Die Sonos-Lautsprecher vergessen? Übernommene Räume verschwinden aus ComfyOS, bis du wieder suchst. ' +
          'An den Lautsprechern selbst ändert sich nichts.', { title: 'Sonos vergessen', ok: 'Vergessen', danger: true })
        if (!confirmed) return
        await tools.api('forget', { method: 'POST' })
        HO.state.overview = null
        return tools.reloadAll()
      }
    } catch (error) {
      HO.toast(error.message, 'bad', 6000)
    }
  }

  /* "Mitspielen bei …": choosing a room puts this one into its group. */
  async function handleChange(event, body, tools) {
    const select = event.target.closest('[data-group-target]')
    if (!select || !body.contains(select) || !select.value) return
    try {
      await tools.api('group', { method: 'POST', body: { device_id: select.dataset.groupTarget, target_id: select.value } })
    } catch (error) {
      HO.toast(error.message, 'bad', 6000)
    }
    return tools.reload()
  }

  HO.plugins.register('sonos', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) {
      const ui = tools.ui
      const [status, devices] = await Promise.all([
        tools.api('status').catch(error => ({ configured: false, rooms: [], suggested: {}, problem: error.message })),
        tools.devices().catch(() => ({ devices: [], new: [], removed: [] }))
      ])
      const openGroups = ui.openState(body)
      const removedGroup = ui.mGroup('Entfernt', (devices.removed || []).map(entry => ui.mRow(entry, 'removed', ui.restoreBtn(entry))), false,
        'In ComfyOS ausgeblendet. An den Lautsprechern ändert sich nichts.')
      body.innerHTML = status.configured ? householdHTML(ui, tools, status, devices, removedGroup) : startHTML(status, removedGroup)
      ui.restoreOpen(body, openGroups)
      body.onclick = event => handleClick(event, body, tools)
      body.onchange = event => handleChange(event, body, tools)
    }
  })
})(window.HO)
