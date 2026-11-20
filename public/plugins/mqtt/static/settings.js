/* MQTT – content of the plugin's tile under System → Plugins.

   The connection settings (address, port, user, password, TLS, discovery prefix) come from
   manifest.json and are drawn by ComfyOS above this content. This file adds:
     * the state of the connection
     * the device lists "Neu gefunden", "Zu prüfen", "Verbunden", "Entfernt", "Nicht unterstützt"
     * "Eigene Geräte": devices the user describes by their topics

   The tile talks to two places:
     tools.api('status' | 'manual' | 'manual/<key>')              own routes of the plugin (routes.py)
     tools.devices(), tools.adopt(), tools.adoptAll()             generic routes ComfyOS offers for every plugin
   Text that comes from the broker or the user is always written with HO.esc. */
(function (HO) {
  'use strict'

  const HINT_NOT_CONFIGURED = 'Trage oben die Adresse deines MQTT-Brokers ein und speichere. ComfyOS hört dann mit, ' +
    'welche Geräte sich dort melden. Der Broker muss im Heimnetz liegen.'
  const HINT_DISCOVERY = 'Geräte erscheinen von selbst, wenn sie sich per Home-Assistant-Discovery melden. ' +
    'In Zigbee2MQTT dafür unter „Settings → Home Assistant integration“ die Anbindung einschalten. ' +
    'Alles andere (z. B. Shelly, Tasmota) lässt sich unten als eigenes Gerät anlegen.'
  const HINT_NOT_SUPPORTED = 'Funktionen, die ComfyOS (noch) nicht darstellt oder deren Beschreibung es nicht lesen kann. ' +
    'Die Geräte selbst bleiben nutzbar.'

  /* One row per device the broker announces but ComfyOS does not show yet. */
  function newDeviceRow(ui, tools, device) {
    const row = { name: device.name, id: device.id, props: Object.assign({ Art: HO.kindLabel[device.kind] || device.kind },
      device.model ? { Modell: device.model } : {}, device.details || {}), room: device.room_hint ? 'Vorschlag: ' + device.room_hint : '' }
    const actions = `<select class="sm" data-adopt-room="${HO.esc(device.id)}">${tools.roomOptions(device.room_hint)}</select>
      <button class="btn sm primary" data-adopt="${HO.esc(device.id)}" data-name="${HO.esc(device.name)}">${HO.icon('plus', 'sm')} Übernehmen</button>`
    return ui.mRow(row, 'new', actions, HO.kindLabel[device.kind] || '')
  }

  function removedRow(ui, entry) {
    return ui.mRow(entry, 'removed', ui.restoreBtn(entry), '')
  }

  function notSupportedList(notes) {
    if (!notes.length) return ''
    return `<details class="mgroup" data-g="Nicht unterstützt"><summary><span>Nicht unterstützt</span><span class="badge">${notes.length}</span></summary>
      <p class="tiny muted" style="margin:4px 0 6px">${HINT_NOT_SUPPORTED}</p>
      <ul class="small muted" style="margin:0 0 8px 18px">${notes.map(note => `<li>${HO.esc(note)}</li>`).join('')}</ul></details>`
  }

  /* One row per device the user created by hand. */
  function manualDeviceRow(ui, device) {
    const row = { name: device.name, id: device.device_id, props: {
      'Zustands-Topic': device.state_topic, 'Befehls-Topic': device.command_topic,
      'Wert an / offen': device.payload_on, 'Wert aus / zu': device.payload_off, 'JSON-Schlüssel': device.json_key } }
    const actions = `<button class="btn sm ghost danger" data-manual-del="${HO.esc(device.key)}" data-name="${HO.esc(device.name)}">${HO.icon('trash', 'sm')} Löschen</button>`
    return ui.mRow(row, 'ok', actions, device.kind_label)
  }

  function manualFormHTML(status) {
    const kindOptions = (status.manual_kinds || []).map(([kind, label]) => `<option value="${HO.esc(kind)}">${HO.esc(label)}</option>`).join('')
    return `<details class="mgroup" data-g="Eigenes Gerät anlegen"><summary><span>Eigenes Gerät anlegen</span></summary>
      <form data-manual-form style="margin-top:8px">
        <label class="field"><span>Name</span><input type="text" name="name" maxlength="60" required autocomplete="off"></label>
        <label class="field"><span>Art</span><select name="kind">${kindOptions}</select></label>
        <label class="field"><span>Zustands-Topic</span><input type="text" name="state_topic" maxlength="200" placeholder="z. B. shellies/kueche/relay/0" autocomplete="off"></label>
        <label class="field" data-only-switchable><span>Befehls-Topic</span><input type="text" name="command_topic" maxlength="200" placeholder="z. B. shellies/kueche/relay/0/command" autocomplete="off"></label>
        <div class="row">
          <label class="field" style="flex:1"><span>Wert für an / offen</span><input type="text" name="payload_on" maxlength="60" value="ON" autocomplete="off"></label>
          <label class="field" style="flex:1"><span>Wert für aus / zu</span><input type="text" name="payload_off" maxlength="60" value="OFF" autocomplete="off"></label>
        </div>
        <label class="field"><span>JSON-Schlüssel (optional)</span><input type="text" name="json_key" maxlength="170" placeholder="z. B. temperature" autocomplete="off">
          <span class="hint tiny muted">Nur wenn die Nachricht JSON ist: der Schlüssel, unter dem der Wert steht.</span></label>
        <label class="field"><span>Raum</span><select name="room"></select></label>
        <div class="form-actions"><button class="btn primary" type="submit">${HO.icon('plus', 'sm')} Anlegen</button></div>
      </form></details>`
  }

  function contentHTML(ui, tools, status, devices) {
    if (!status.configured) return `<p class="small muted" style="margin-top:4px">${HINT_NOT_CONFIGURED}</p>`
    const shown = devices.devices || [], found = devices.new || []
    const toCheck = shown.filter(device => device.status !== 'ok'), connected = shown.filter(device => device.status === 'ok')
    const problem = status.connected ? devices.error : (status.message || devices.error)
    return `
      ${problem ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>${HO.esc(problem)}</div></div>` : ''}
      <dl class="kv" style="margin-bottom:14px"><dt>Broker</dt><dd>${HO.esc(status.host || '')}</dd>
        <dt>Geräte in ComfyOS</dt><dd>${shown.length}${toCheck.length ? ` · ${toCheck.length} zu prüfen` : ''}${found.length ? ` · ${found.length} neu` : ''}</dd></dl>
      <div class="form-actions" style="margin-top:4px"><button class="btn" data-act="mqttReload">${HO.icon('refresh', 'sm')} Neu einlesen</button>
        ${found.length > 1 ? `<button class="btn primary" data-act="mqttAdoptAll">${HO.icon('plus', 'sm')} Alle ${found.length} übernehmen</button>` : ''}</div>
      <div class="mgroups">
        ${ui.mGroup('Neu gefunden', found.map(device => newDeviceRow(ui, tools, device)), found.length > 0 && found.length <= 6,
          'Geräte, die sich am Broker melden und die ComfyOS noch nicht führt.')}
        ${ui.mGroup('Zu prüfen', toCheck.map(device => ui.mRow(device, device.status, ui.removeBtn(device))), false)}
        ${ui.mGroup('Verbunden', connected.map(device => ui.mRow(device, 'ok', ui.removeBtn(device))), false)}
        ${ui.mGroup('Entfernt', (devices.removed || []).map(entry => removedRow(ui, entry)), false,
          'In ComfyOS ausgeblendet. Am Broker bleibt alles unverändert.')}
        ${notSupportedList(status.not_supported || [])}
        ${ui.mGroup('Eigene Geräte', (status.manual || []).map(device => manualDeviceRow(ui, device)), false,
          'Von dir angelegt. Löschen entfernt das Gerät auch aus ComfyOS.')}
        ${manualFormHTML(status)}</div>
      <p class="tiny muted" style="margin-top:12px">${HINT_DISCOVERY}</p>`
  }

  /* Show the command topic only for kinds that can be switched. */
  function updateManualForm(form, status) {
    const isSwitchable = (status.switchable_kinds || []).indexOf(form.elements.kind.value) >= 0
    const commandField = form.querySelector('[data-only-switchable]')
    if (commandField) commandField.style.display = isSwitchable ? '' : 'none'
  }

  async function createManualDevice(form, tools) {
    const values = {}
    for (const name of ['name', 'kind', 'state_topic', 'command_topic', 'payload_on', 'payload_off', 'json_key']) {
      values[name] = form.elements[name].value.trim()
    }
    const created = await tools.api('manual', { method: 'POST', body: values })
    try {
      await tools.adopt(created.device_id, form.elements.room.value)
      HO.toast(`${created.name} angelegt`)
    } catch (error) {
      // without a connection to the broker the device list is not available yet
      HO.toast(`${created.name} angelegt – erscheint unter „Neu gefunden“, sobald der Broker verbunden ist.`, 'warn', 6000)
    }
    HO.state.overview = null
    return tools.reload()
  }

  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-act],[data-adopt],[data-manual-del]')
    if (!target || !body.contains(target)) return
    try {
      if (target.dataset.adopt) {
        const roomSelect = body.querySelector(`[data-adopt-room="${CSS.escape(target.dataset.adopt)}"]`)
        target.disabled = true
        await tools.adopt(target.dataset.adopt, roomSelect ? roomSelect.value : null)
        HO.toast(`${target.dataset.name} übernommen`)
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.manualDel) {
        const confirmed = await HO.confirm(`„${target.dataset.name}" löschen? Das Gerät verschwindet auch aus ComfyOS.`,
          { title: 'Eigenes Gerät löschen', ok: 'Löschen', danger: true })
        if (!confirmed) return
        await tools.api('manual/' + encodeURIComponent(target.dataset.manualDel), { method: 'DELETE' })
        HO.toast('Gelöscht')
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.act === 'mqttReload') {
        await tools.reload()
        HO.toast('Neu eingelesen')
        return
      }
      if (target.dataset.act === 'mqttAdoptAll') {
        target.disabled = true
        const result = await tools.adoptAll()
        HO.toast(`${result.adopted} Geräte übernommen – Räume lassen sich am Gerät zuordnen.`, 'ok', 5000)
        HO.state.overview = null
        return tools.reload()
      }
    } catch (error) {
      HO.toast(error.message, 'bad', 5000)
    }
  }

  HO.plugins.register('mqtt', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) {
      const ui = tools.ui
      const [status, devices] = await Promise.all([
        tools.api('status').catch(error => ({ configured: false, message: error.message })),
        tools.devices().catch(error => ({ devices: [], new: [], removed: [], error: error.message }))
      ])
      const openGroups = ui.openState(body)
      body.innerHTML = contentHTML(ui, tools, status, devices)
      ui.restoreOpen(body, openGroups)

      const form = body.querySelector('[data-manual-form]')
      if (form) {
        form.elements.room.innerHTML = tools.roomOptions('')
        updateManualForm(form, status)
        form.elements.kind.onchange = () => updateManualForm(form, status)
        form.onsubmit = async event => {
          event.preventDefault()
          try { await createManualDevice(form, tools) } catch (error) { HO.toast(error.message, 'bad', 5000) }
        }
      }
      body.onclick = event => handleClick(event, body, tools)
    }
  })
})(window.HO)
