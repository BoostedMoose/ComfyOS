/* Netzwerk – content of the plugin's tile under System → Plugins.

   The two timing settings come from manifest.json and are drawn by ComfyOS above this content.
   This file adds the list of watched devices ("Handy im WLAN") with their state and a form to add one.

   The tile talks to two places:
     tools.api('targets' | 'targets/<key>' | 'targets/<key>/check')   own routes of the plugin (routes.py)
     tools.devices(), tools.adopt()                                   generic routes ComfyOS offers for every plugin
   A new target is adopted right away, so it appears as a device without a second step.
   Text the user entered is always written with HO.esc. */
(function (HO) {
  'use strict'

  const HINT = 'ComfyOS versucht regelmäßig, das Gerät im Heimnetz zu erreichen. Jede Antwort zählt als „anwesend“ – auch ein abgelehnter ' +
    'Verbindungsversuch. Am zuverlässigsten ist eine feste Adresse (im Router einstellen) und ein Port, auf dem das Gerät antwortet: ' +
    'beim iPhone meist 62078, sonst 80 oder 443 probieren. „Jetzt prüfen“ zeigt, ob es klappt.'

  function stateBadge(target) {
    if (target.problem) return '<span class="badge bad">Fehler</span>'
    if (target.present === true) return '<span class="badge ok">Anwesend</span>'
    if (target.present === false) return '<span class="badge">Abwesend</span>'
    return '<span class="badge">unbekannt</span>'
  }

  /* One row per watched device. `isAdopted` is false when the user removed it from ComfyOS but kept the target. */
  function targetRow(target, isAdopted, tools) {
    const lastAnswer = target.last_answer_at ? HO.relTime(target.last_answer_at) : 'noch nie'
    return `<details class="mrow"><summary><span class="mrow-name">${HO.esc(target.name)}</span>
        <span class="mrow-sub small muted">${HO.esc(target.host)}:${target.port}</span>${stateBadge(target)}</summary>
      <div class="mrow-body">
        ${target.problem ? `<div class="small" style="color:var(--warn);margin-bottom:6px">${HO.esc(target.problem)}</div>` : ''}
        <dl class="kv small"><dt>Adresse</dt><dd>${HO.esc(target.host)}:${target.port}</dd><dt>Zuletzt erreichbar</dt><dd>${HO.esc(lastAnswer)}</dd></dl>
        <div class="row wrap" style="margin-top:10px">
          <button class="btn sm" data-check="${HO.esc(target.key)}">${HO.icon('refresh', 'sm')} Jetzt prüfen</button>
          ${isAdopted ? '' : `<select class="sm" data-adopt-room="${HO.esc(target.device_id)}">${tools.roomOptions('')}</select>
            <button class="btn sm primary" data-adopt="${HO.esc(target.device_id)}" data-name="${HO.esc(target.name)}">${HO.icon('plus', 'sm')} In ComfyOS zeigen</button>`}
          <button class="btn sm ghost danger" data-delete="${HO.esc(target.key)}" data-name="${HO.esc(target.name)}">${HO.icon('trash', 'sm')} Löschen</button>
        </div></div></details>`
  }

  function formHTML(tools) {
    return `<details class="mgroup" data-g="Gerät hinzufügen"><summary><span>Gerät hinzufügen</span></summary>
      <form data-target-form style="margin-top:8px">
        <label class="field"><span>Name</span><input type="text" name="name" maxlength="60" required placeholder="z. B. Handy Anna" autocomplete="off"></label>
        <div class="row">
          <label class="field" style="flex:2"><span>Adresse im Heimnetz</span><input type="text" name="host" maxlength="120" required placeholder="192.168.178.40" autocomplete="off"></label>
          <label class="field" style="flex:1"><span>Port</span><input type="number" name="port" min="1" max="65535" value="80" required></label>
        </div>
        <label class="field"><span>Raum</span><select name="room">${tools.roomOptions('')}</select></label>
        <div class="form-actions"><button class="btn primary" type="submit">${HO.icon('plus', 'sm')} Hinzufügen</button></div>
      </form></details>`
  }

  function contentHTML(tools, targetList, devices) {
    const notAdopted = new Set((devices.new || []).map(device => device.id))
    const targets = targetList.targets || []
    return `
      ${targets.length ? `<div class="mlist" style="margin-bottom:12px">${targets.map(target => targetRow(target, !notAdopted.has(target.device_id), tools)).join('')}</div>`
        : '<p class="small muted" style="margin-top:4px">Noch kein Gerät eingetragen.</p>'}
      <div class="mgroups">${formHTML(tools)}</div>
      <p class="tiny muted" style="margin-top:12px">${HINT}</p>`
  }

  function answerText(result) {
    if (result.answered === true) return `${result.name} antwortet`
    if (result.answered === false) return `${result.name} antwortet gerade nicht`
    return result.problem || 'Diese Adresse lässt sich nicht prüfen.'
  }

  async function createTarget(form, tools) {
    const values = { name: form.elements.name.value.trim(), host: form.elements.host.value.trim(), port: Number(form.elements.port.value) }
    const created = await tools.api('targets', { method: 'POST', body: values })
    await tools.adopt(created.device_id, form.elements.room.value)
    HO.toast(answerText(created), created.answered ? 'ok' : 'warn', 5000)
    HO.state.overview = null
    return tools.reloadAll()
  }

  async function handleClick(event, body, tools) {
    const target = event.target.closest('[data-check],[data-adopt],[data-delete]')
    if (!target || !body.contains(target)) return
    try {
      if (target.dataset.check) {
        target.disabled = true
        const result = await tools.api(`targets/${encodeURIComponent(target.dataset.check)}/check`, { method: 'POST' })
        HO.toast(answerText(result), result.answered ? 'ok' : 'warn')
        return tools.reload()
      }
      if (target.dataset.adopt) {
        const roomSelect = body.querySelector(`[data-adopt-room="${CSS.escape(target.dataset.adopt)}"]`)
        await tools.adopt(target.dataset.adopt, roomSelect ? roomSelect.value : null)
        HO.toast(`${target.dataset.name} wird in ComfyOS gezeigt`)
        HO.state.overview = null
        return tools.reload()
      }
      if (target.dataset.delete) {
        const confirmed = await HO.confirm(`„${target.dataset.name}" nicht mehr beobachten? Das Gerät verschwindet auch aus ComfyOS.`,
          { title: 'Gerät löschen', ok: 'Löschen', danger: true })
        if (!confirmed) return
        await tools.api('targets/' + encodeURIComponent(target.dataset.delete), { method: 'DELETE' })
        HO.toast('Gelöscht')
        HO.state.overview = null
        return tools.reloadAll()
      }
    } catch (error) {
      HO.toast(error.message, 'bad', 5000)
    }
  }

  HO.plugins.register('network', {
    /* Draw the tile content. Called when the System page opens and after every change. */
    async render(body, tools) {
      const [targetList, devices] = await Promise.all([
        tools.api('targets').catch(error => ({ targets: [], error: error.message })),
        tools.devices().catch(() => ({ devices: [], new: [], removed: [] }))
      ])
      const openGroups = tools.ui.openState(body)
      body.innerHTML = contentHTML(tools, targetList, devices)
      tools.ui.restoreOpen(body, openGroups)
      const form = body.querySelector('[data-target-form]')
      if (form) {
        form.onsubmit = async event => {
          event.preventDefault()
          try { await createTarget(form, tools) } catch (error) { HO.toast(error.message, 'bad', 5000) }
        }
      }
      body.onclick = event => handleClick(event, body, tools)
    }
  })
})(window.HO)
