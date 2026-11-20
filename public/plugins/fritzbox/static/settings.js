/* FRITZ!Box – content of the plugin's tile under System → Plugins.

   Address, user, password, query interval and the presence switch come from manifest.json and are drawn by ComfyOS
   above this content. This file adds the state of the connection, "Jetzt abfragen" and – with presence switched
   on – the choice of devices of the home network whose presence ComfyOS reports.

   The tile talks to the plugin's own routes (routes.py) through tools.api('status' | 'hosts' | 'presence' | 'refresh').
   Text from the box (device names) is always written with HO.esc. */
(function (HO) {
  'use strict'

  const HINT = 'Die Smart-Home-Geräte der FRITZ!Box erscheinen von selbst. Taster (FRITZ!DECT 400/440) melden jeden Druck – ' +
    'du findest sie in Flow beim Auslöser „Taster“. ComfyOS fragt die Box im eingestellten Takt; das Kennwort verlässt das Heimnetz nie.'

  // Line with the state of the connection (or what is still missing).
  function stateLine(status) {
    if (!status.configured) return `<p class="small muted">${HO.esc(status.message || 'Adresse und Kennwort eintragen.')}</p>`
    const badge = status.connected ? '<span class="badge ok">verbunden</span>' : '<span class="badge bad">getrennt</span>'
    return `<div class="row between" style="margin-bottom:10px"><span class="small">${HO.esc(status.message || '')}</span>${badge}</div>`
  }

  /* Group "Anwesenheit" (only with presence switched on): the picked devices; after "Geräte im Heimnetz laden" the
     whole list of the box with a checkbox each. */
  function presenceHTML(status, hosts) {
    if (!status.presence) return ''
    const picked = status.presence_hosts || []
    const list = hosts ? `<div class="mlist" style="margin-top:8px">${hosts.map(host => `<label class="check fl-devitem">
        <input type="checkbox" data-host="${HO.esc(host.mac)}" data-name="${HO.esc(host.name)}" ${host.picked ? 'checked' : ''}>
        <span>${HO.esc(host.name)}<span class="muted small"> · ${HO.esc(host.ip || host.mac)}${host.active ? ' · aktiv' : ''}</span></span></label>`).join('')}</div>
        <div class="form-actions"><button class="btn primary sm" data-save-presence>${HO.icon('check', 'sm')} Auswahl speichern</button></div>`
      : `<div class="form-actions"><button class="btn sm" data-load-hosts>${HO.icon('refresh', 'sm')} Geräte im Heimnetz laden</button></div>`
    return `<details class="mgroup" data-g="Anwesenheit" open><summary><span>Anwesenheit</span><span class="badge">${picked.length}</span></summary>
      <p class="tiny muted" style="margin:4px 0 6px">Gewählte Geräte (z. B. Handys) melden „anwesend“, solange sie im Heimnetz der Box sind.</p>
      ${picked.length ? `<div class="small">${picked.map(host => HO.esc(host.name)).join(', ')}</div>` : '<div class="small muted">Noch kein Gerät gewählt.</div>'}
      ${list}</details>`
  }

  // Draw the content; `hosts` is the loaded list of the home network (null = not loaded yet).
  async function render(body, tools, hosts) {
    const status = await tools.api('status').catch(error => ({ configured: false, message: error.message }))
    const openGroups = tools.ui.openState(body)
    body.innerHTML = `${stateLine(status)}
      ${status.configured ? `<div class="form-actions" style="margin-top:0"><button class="btn sm" data-refresh>${HO.icon('refresh', 'sm')} Jetzt abfragen</button></div>` : ''}
      <div class="mgroups">${presenceHTML(status, hosts)}</div>
      <p class="tiny muted" style="margin-top:12px">${HINT}</p>`
    tools.ui.restoreOpen(body, openGroups)
    body.onclick = event => handleClick(event, body, tools)
  }

  // Buttons of the tile: ask now, load the devices of the home network, store the choice.
  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-refresh],[data-load-hosts],[data-save-presence]')
    if (!target || !body.contains(target)) return
    try {
      target.disabled = true
      if (target.hasAttribute('data-refresh')) {
        await tools.api('refresh', { method: 'POST' })
        HO.toast('FRITZ!Box abgefragt', 'ok')
        return tools.reloadAll()
      }
      if (target.hasAttribute('data-load-hosts')) {
        const result = await tools.api('hosts')
        return render(body, tools, result.hosts || [])
      }
      if (target.hasAttribute('data-save-presence')) {
        const hosts = [...body.querySelectorAll('[data-host]:checked')].map(box => ({ mac: box.dataset.host, name: box.dataset.name }))
        await tools.api('presence', { method: 'PUT', body: { hosts } })
        HO.toast('Auswahl gespeichert', 'ok')
        HO.state.overview = null
        return tools.reloadAll()
      }
    } catch (error) {
      target.disabled = false
      HO.toast(error.message, 'bad', 5000)
    }
  }

  HO.plugins.register('fritzbox', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) { return render(body, tools, null) }
  })
})(window.HO)
