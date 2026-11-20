/* Home Base (QIVICON) – content of the plugin's tile under System → Plugins.

   Sections of the tile
     connection    address, password, "Verbinden & speichern", "Verbindung testen", "Trennen"
     devices       "Zu prüfen", "Verbunden", "Neu gefunden" (inbox of the Home Base), "Entfernt"; "Gerät hinzufügen"
     Magenta sync  optional Telekom login that keeps heating routines and home/away in step with the Magenta app

   The tile talks to two places:
     tools.api('status' | 'connect' | 'test' | 'forget' | 'magenta' | 'inbox' | …)   own routes of the plugin (routes.py)
     tools.devices()                                                                 generic route ComfyOS offers for every plugin

   The plugin also offers actions for its devices wherever ComfyOS shows a device (device sheet, lists of other
   tiles): "Wiederverbinden" and "Von der Home Base löschen" – see deviceActions(). The pairing assistant lives in
   pairing.js and is loaded on first use.
   Text that comes from the Home Base or the user is always written with HO.esc. */
(function (HO) {
  'use strict'

  const PLUGIN_ID = 'qivicon'
  const DEFAULT_ADDRESS = 'https://qivicon'
  let pairingScript = null

  /* Load the pairing assistant (HO.pairing) once. */
  function loadPairing(version) {
    if (HO.pairing) return Promise.resolve()
    if (!pairingScript) {
      pairingScript = new Promise((resolve, reject) => {
        const script = document.createElement('script')
        script.src = `/plugins/${PLUGIN_ID}/static/pairing.js?v=${encodeURIComponent(version || '')}`
        script.onload = resolve
        script.onerror = () => { pairingScript = null; reject(new Error('Der Anlern-Assistent konnte nicht geladen werden.')) }
        document.head.appendChild(script)
      })
    }
    return pairingScript
  }

  /* Status of a device on the Home Base. It travels in the device row (hb_status) or, in the lists of the
     settings page, among the extra values. */
  function hubStatus(device) {
    return device.hb_status || (device.extra && device.extra.hb_status) || null
  }
  function hubDetail(device) {
    return device.hb_detail || (device.extra && device.extra.hb_detail) || null
  }

  /* Actions the Home Base offers for one of its devices. ComfyOS shows them in the device sheet and in lists.
     run(device, done): done() is called when the device list should be read again. */
  function deviceActions(device) {
    const actions = []
    const isHueThing = String(device.id).indexOf('hue:') === 0      // Hue lamps are reconnected in the Hue app
    if (!device.link_missing && device.online === false && hubStatus(device) && !isHueThing) {
      actions.push({ key: 'reconnect', label: 'Wiederverbinden', icon: 'link',
        run: async (target, done) => { await loadPairing(); HO.pairing.reconnect(target, done) } })
    }
    actions.push({ key: 'delete', label: 'Von der Home Base löschen', icon: 'trash', danger: true,
      run: async (target, done) => { await loadPairing(); if (await HO.pairing.remove(target)) done() } })
    return actions
  }

  function connectionHTML(status) {
    const connected = !!status.connected
    return `${connected ? `<dl class="kv" style="margin-bottom:16px"><dt>Gateway</dt><dd>${HO.esc(status.gateway || '–')}</dd>
        <dt>Adresse</dt><dd>${HO.esc(status.endpoint || status.address)}</dd></dl>`
      : status.configured && status.message ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>${HO.esc(status.message)}</div></div>` : ''}
      <form data-hb-form>
        <label class="field" style="margin-top:0"><span>Adresse der Home Base</span><input type="text" data-hb-url value="${HO.esc(status.address || DEFAULT_ADDRESS)}"
          placeholder="https://qivicon oder https://192.168.178.x" autocomplete="off"></label>
        <label class="field"><span>Passwort der Home Base</span><input type="password" data-hb-pass autocomplete="off" ${HO.pwAttrs(status.password_saved)}
          placeholder="${status.password_saved ? '' : 'Passwort der QIVICON-Weboberfläche'}"></label>
        <label class="check" style="margin-top:12px"><input type="checkbox" data-hb-tls ${status.verify_tls ? 'checked' : ''}> TLS-Zertifikat streng prüfen</label>
        <div class="form-actions"><button class="btn primary" type="submit">Verbinden &amp; speichern</button>
          ${status.configured ? '<button class="btn" type="button" data-hb-act="test">Verbindung testen</button><button class="btn ghost danger" type="button" data-hb-act="forget">Trennen</button>' : ''}</div>
      </form>`
  }

  function devicesHTML(ui, view, inbox) {
    if (!view || view.error) return `<p class="small muted">${HO.esc((view && view.error) || 'Geräteliste nicht verfügbar.')}</p>`
    const actionButtons = device => ui.removeBtn(device) + ui.actionButtons(deviceActions(device), device, PLUGIN_ID)
    const toCheck = view.devices.filter(device => device.status !== 'ok'), fine = view.devices.filter(device => device.status === 'ok')
    const found = (inbox || []).filter(entry => entry.flag !== 'IGNORED')
    return `<div class="row between" style="margin:16px 0 10px"><span class="small muted">Geräte der Home Base</span>
        <button class="btn sm primary" data-hb-act="pair">${HO.icon('plus', 'sm')} Gerät hinzufügen</button></div>
      <div class="mgroups">
      ${ui.mGroup('Zu prüfen', toCheck.map(device => ui.mRow(device, device.status, actionButtons(device),
        hubDetail(device) === 'GONE' ? 'Von der Home Base getrennt' : undefined)), false,
        'Offline, Fehler oder Batterie schwach. Getrennte Geräte lassen sich hier wiederverbinden.')}
      ${ui.mGroup('Verbunden', fine.map(device => ui.mRow(device, 'ok', ui.removeBtn(device))), false)}
      ${ui.mGroup('Neu gefunden', found.map(entry => ui.mRow({ name: entry.label, id: entry.uid, type: entry.type }, 'inbox',
        `<button class="btn sm primary" data-hb-approve="${HO.esc(entry.uid)}" data-label="${HO.esc(entry.label)}">${HO.icon('plus', 'sm')} Einbinden</button>`,
        entry.type, true)), false, 'Von der Home Base neu entdeckte Geräte. „Einbinden" nimmt sie auf – danach erscheinen sie in der Geräteliste.')}
      ${ui.mGroup('Entfernt', view.removed.map(entry => ui.mRow(entry, 'removed', ui.restoreBtn(entry), '')), false,
        'In ComfyOS ausgeblendet; in der Home Base unverändert.')}
      ${!toCheck.length && !fine.length ? '<p class="small muted">Keine Geräte.</p>' : ''}
      ${view.elsewhere ? `<p class="tiny muted" style="margin-top:8px">${view.elsewhere === 1 ? 'Ein Gerät steht' : view.elsewhere + ' Geräte stehen'}
        bei dem Plugin, das sie steuert (z. B. <b>Philips Hue</b>).</p>` : ''}</div>`
  }

  function magentaHTML(status) {
    const sync = status.sync || {}
    const badge = sync.configured ? (sync.ok === false ? ['bad', 'Fehler'] : ['ok', 'Online']) : ['', 'Aus']
    return `<div class="https-box" data-hb-magenta><div class="row between"><b>Magenta-Sync</b> <span class="badge ${badge[0]}">${badge[1]}</span></div>
      <p class="small muted">Optional, für den Umstieg: übernimmt Heizroutinen und Zuhause/Abwesend aus der Magenta-App und gleicht alle
        ${Math.round((sync.interval || 300) / 60)} Minuten sowie nach jeder Änderung automatisch ab. ComfyOS funktioniert auch ohne.</p>
      <div class="notice" style="margin:8px 0 12px">${HO.icon('cloud', 'sm')}<div>Verbindet sich mit Telekom-Servern: gesendet werden dein Telekom-Login,
        Heizpläne und Zuhause/Abwesend. Ohne Einrichtung baut ComfyOS diese Verbindung nie auf.</div></div>
      ${sync.configured ? `<div class="sync-line" style="margin:4px 0 14px">${HO.syncLine(Object.assign({}, sync, { pending: 0 }))}</div>` : ''}
      <form data-hb-telekom>
        <label class="field"><span>Telekom-E-Mail</span><input type="email" data-tk-user value="${HO.esc(status.telekom_username || '')}" autocomplete="username"
          placeholder="name@example.de"></label>
        <label class="field"><span>Telekom-Passwort</span><input type="password" data-tk-pass autocomplete="off" ${HO.pwAttrs(status.telekom_saved)}
          placeholder="${status.telekom_saved ? '' : 'Passwort des Telekom-Kontos'}"></label>
        <div class="form-actions"><button class="btn primary" type="submit">${sync.configured ? 'Speichern' : 'Aktivieren'}</button>
          ${sync.configured ? `<button class="btn" type="button" data-hb-act="syncnow">${HO.icon('sync', 'sm')} Jetzt abgleichen</button>
            <button class="btn ghost danger" type="button" data-hb-act="tkforget">Entfernen</button>` : ''}</div>
      </form></div>`
  }

  async function render(body, tools) {
    const ui = tools.ui
    const status = await tools.api('status')
    let view = null, inbox = null
    if (status.connected) {
      const answers = await Promise.all([tools.devices().catch(error => ({ error: error.message })),
        tools.api('inbox').catch(() => ({ inbox: null }))])
      view = answers[0]; inbox = answers[1].inbox
    }
    const open = ui.openState(body)
    body.innerHTML = connectionHTML(status) + (status.connected ? devicesHTML(ui, view, inbox) : '') + magentaHTML(status)
    ui.restoreOpen(body, open)
    // changed(): the connection itself changed -> redraw the whole tile with its header.
    // listChanged(): only devices changed -> redraw the content, groups stay open as they were.
    const changed = () => { HO.state.overview = null; return tools.reloadAll() }
    const listChanged = () => { HO.state.overview = null; return tools.reload() }
    const version = tools.plugin.version

    body.onsubmit = async event => {
      event.preventDefault()
      const form = event.target, button = form.querySelector('[type=submit]')
      button.disabled = true
      try {
        if (form.matches('[data-hb-form]')) {
          const answer = await tools.api('connect', { method: 'POST', body: { base_url: form.querySelector('[data-hb-url]').value,
            password: HO.pwValue(form.querySelector('[data-hb-pass]')) || null, verify_tls: form.querySelector('[data-hb-tls]').checked } })
          HO.toast(answer.message, answer.connected ? 'ok' : 'bad', 5000)
          if (answer.connected && HO.refreshConn) HO.refreshConn()
        } else if (form.matches('[data-hb-telekom]')) {
          const answer = await tools.api('magenta', { method: 'POST', body: { username: form.querySelector('[data-tk-user]').value,
            password: HO.pwValue(form.querySelector('[data-tk-pass]')) || null } })
          HO.toast(answer.error ? 'Gespeichert, aber Abgleich fehlgeschlagen: ' + answer.error : 'Magenta-Sync aktiv – Routinen übernommen',
            answer.error ? 'warn' : 'ok', 5000)
        }
        await changed()
      } catch (error) { HO.toast(error.message, 'bad', 5000); button.disabled = false }
    }

    body.onclick = async event => {
      const target = event.target.closest('[data-hb-act],[data-hb-approve]')
      if (!target) return
      const action = target.dataset.hbAct
      try {
        if (target.dataset.hbApprove) {
          target.disabled = true
          const label = target.dataset.label || target.dataset.hbApprove
          await tools.api(`inbox/${encodeURIComponent(target.dataset.hbApprove)}/approve`, { method: 'POST', body: { label: target.dataset.label || '' } })
          HO.toast(`${label} eingebunden`)
          return listChanged()
        }
        if (action === 'pair') { await loadPairing(version); return HO.pairing.wizard(listChanged) }
        if (action === 'test') {
          const answer = await tools.api('test', { method: 'POST' })
          HO.toast(answer.connected ? 'Verbindung ok' : answer.message, answer.connected ? 'ok' : 'bad')
          return tools.reload()
        }
        if (action === 'forget') {
          if (!await HO.confirm('Verbindung zur Home Base trennen? Adresse und Passwort werden gelöscht. Geräte der Home Base verschwinden aus ComfyOS, ' +
            'bis du sie wieder verbindest – Grundriss, Räume, Heizpläne und Flows bleiben erhalten.', { title: 'Home Base trennen', ok: 'Trennen', danger: true })) return
          await tools.api('forget', { method: 'POST' })
          HO.toast('Home Base getrennt')
          return changed()
        }
        if (action === 'syncnow') {
          const sync = await HO.api('sync?force=true', { method: 'POST' })
          HO.toast(sync.ok ? 'Abgeglichen' : 'Abgleich fehlgeschlagen: ' + sync.last_error, sync.ok ? 'ok' : 'bad', 5000)
          return tools.reload()
        }
        if (action === 'tkforget') {
          if (!await HO.confirm('Magenta-Sync entfernen? Die Routinen bleiben in ComfyOS erhalten.', { ok: 'Entfernen', danger: true })) return
          await tools.api('magenta/forget', { method: 'POST' })
          return changed()
        }
      } catch (error) { HO.toast(error.message, 'bad', 5000); target.disabled = false }
    }
  }

  HO.plugins.register(PLUGIN_ID, { render, deviceActions })
})(window.HO)
