/* ComfyOS – pair devices, reconnect them and delete them from the Home Base (procedures as in QIVICON) */
window.HO = window.HO || {};
(function (HO) {
  'use strict'
  const enc = encodeURIComponent
  const alive = s => document.body.contains(s.el)
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const mmss = sec => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
  const img = (src, cls) => src ? `<img class="${cls}" src="${HO.esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''
  const thumb = src => src ? img(src, 'pair-thumb') : `<span class="pair-thumb">${HO.icon('device')}</span>`
  const stepsHTML = steps => steps.length ? `<ol class="pair-steps">${steps.map(st =>
    `<li>${st.title ? `<b>${HO.esc(st.title)}</b>` : ''}<p>${HO.esc(st.text)}</p>${img(st.image, 'pair-step-img')}</li>`).join('')}</ol>` : ''
  const doneHTML = (title, text) => `<div class="pair-done">${HO.icon('check')}<h3>${HO.esc(title)}</h3>${text ? `<p class="small">${HO.esc(text)}</p>` : ''}</div>`
  const backBtn = label => `<button class="btn sm ghost pair-back" data-back>${HO.icon('back', 'sm')} ${HO.esc(label)}</button>`

  HO.pairing = {}

  /* ---------- Assistant: maker -> device with instructions -> search now -> confirmation */
  HO.pairing.wizard = function (onDone) {
    let cat = null, byId = {}, vendorIdx = null, det = null, query = '', view = 0, onBack = null, added = false, rooms = []
    const handled = new Set(), pending = []   // devices that newly appeared: already set up, or still to be named
    HO.api('rooms').then(r => { rooms = (r || []).filter(x => x.room_id && !String(x.room_id).startsWith('plan:')) }).catch(() => {})
    const roomSelect = uid => rooms.length ? `<label class="field" style="margin:0"><span>Raum</span><select data-room-for="${HO.esc(uid)}">
        <option value="">— später zuordnen —</option>${rooms.map(r => `<option value="${HO.esc(r.room_id)}">${HO.esc(r.name)}</option>`).join('')}</select></label>` : ''
    const s = HO.sheet({ title: 'Gerät hinzufügen', wide: true, body: '<div class="spinner"></div>' })
    const close = s.close
    s.close = () => { close(); if (added && onDone) onDone() }
    const show = (html, back) => { view++; onBack = back || null; s.body().innerHTML = html; s.body().scrollTop = 0; return view }

    const devRows = (list, withVendor) => list.length ? list.map(d => `<button class="pair-dev" data-dev="${HO.esc(d.id)}">${thumb(d.image)}
        <span class="pair-dev-t"><span>${HO.esc(d.name)}${d.beta ? ' <span class="badge warn">Beta</span>' : ''}</span>
        <span class="tiny muted">${HO.esc(withVendor ? [d.vendor, d.subtitle].filter(Boolean).join(' · ') : d.subtitle)}</span></span>${HO.icon('chevron', 'sm')}</button>`).join('')
      : '<p class="small muted" style="padding:8px 12px">Nichts gefunden.</p>'
    const vendorRows = () => cat.vendors.map((v, i) => `<button data-vendor="${i}"><span class="grow">${HO.esc(v.name)}</span>
        <span class="badge">${v.devices.length}</span>${HO.icon('chevron', 'sm')}</button>`).join('')
    function listHTML() {
      const q = query.trim().toLowerCase()
      if (!q) return vendorRows()
      return devRows(Object.values(byId).filter(d => `${d.name} ${d.subtitle} ${d.vendor}`.toLowerCase().includes(q)).slice(0, 80), true)
    }

    function vendorsView() {
      show(`${cat.notice ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('info', 'sm')}<div>${HO.esc(cat.notice)}</div></div>` : ''}
        <p class="small muted" style="margin-top:0">Wähle den Hersteller und dann dein Gerät – du bekommst die passende Anleitung zum Anlernen.</p>
        ${cat.source === 'cloud' ? `<p class="tiny muted">${HO.icon('cloud', 'sm')} Hersteller, Bilder und Anleitungen lädt ComfyOS aus dem Gerätekatalog der Telekom (mit dem Katalog-Zugang deiner Home Base). Dein Browser verbindet sich dabei nicht selbst mit Telekom.</p>` : ''}
        <input type="search" id="pairQ" placeholder="Hersteller oder Gerät suchen" value="${HO.esc(query)}" autocomplete="off" style="width:100%;margin-bottom:10px">
        <div class="menu-list" id="pairList">${listHTML()}</div>`)
    }
    function devicesView(i) {
      vendorIdx = i
      const v = cat.vendors[i]
      show(`${backBtn('Hersteller')}<h3 style="margin:0 0 10px">${HO.esc(v.name)}</h3>
        <div class="menu-list">${devRows(v.devices.map(d => ({ ...d, vendor: v.name })), false)}</div>`, vendorsView)
    }
    async function prepareView(id) {
      const entry = byId[id]
      const back = vendorIdx != null && cat.vendors[vendorIdx].name === entry.vendor ? () => devicesView(vendorIdx) : vendorsView
      const my = show(`${backBtn('Zurück')}<div class="spinner"></div>`, back)
      try { det = await HO.api('plugins/qivicon/pairing/devices/' + enc(id)) } catch (e) { det = { ...entry, steps: [], reset: [], hints: [] }; HO.toast(e.message, 'warn') }
      if (!alive(s) || my !== view) return
      const action = det.setup === 'app'
        ? `<div class="notice">${HO.icon('info', 'sm')}<div>Dieses Gerät sucht die Home Base nicht selbst – es wird in der Magenta-SmartHome-App eingerichtet (z. B. per Bluetooth). Danach erscheint es automatisch in ComfyOS.</div></div>`
        : `<button class="btn primary" data-search>${HO.icon('refresh', 'sm')} Jetzt suchen</button>`
      show(`${backBtn('Zurück')}
        <div class="pair-head">${img(det.image, 'pair-hero')}<div><div class="eyebrow">${HO.esc(det.vendor || '')}</div>
          <h3>${HO.esc(det.name)}</h3>${det.subtitle ? `<div class="tiny muted">${HO.esc(det.subtitle)}</div>` : ''}</div></div>
        ${stepsHTML(det.steps)}
        ${det.hints.length ? `<details class="pair-more"><summary class="small muted">Tipps &amp; Hinweise</summary>${stepsHTML(det.hints)}</details>` : ''}
        ${det.setup === 'steps' ? `<div class="notice" style="margin-top:12px">${HO.icon('info', 'sm')}<div>Nach dem Finden braucht dieses Gerät weitere Angaben (z. B. WLAN oder Zugangsdaten). Die Einrichtung schließt du dann in der QIVICON-Oberfläche ab.</div></div>` : ''}
        <div class="pair-actions">${action}</div>`, back)
    }
    async function searchView() {
      const d = det
      let r
      try { r = await HO.api('plugins/qivicon/pairing/scan', { method: 'POST', body: { binding: d.binding } }) } catch (e) { return HO.toast(e.message, 'bad', 5000) }
      if (!alive(s)) return
      const my = show(`<div class="pair-wait" id="pairWait"><div class="spinner"></div><h3>Suche läuft …</h3>
          <p class="small muted">Bringe ${HO.esc(d.name)} jetzt in den Verbindungsmodus, falls noch nicht geschehen.</p>
          <div class="pair-count" id="pairCount">${mmss(r.timeout)}</div></div><div id="pairFound"></div>`, () => prepareView(d.id))
      const end = Date.now() + r.timeout * 1000, seen = new Set()
      let left = r.timeout
      while (alive(s) && my === view) {
        left = Math.max(0, Math.round((end - Date.now()) / 1000))
        const cnt = s.el.querySelector('#pairCount')
        if (cnt) cnt.textContent = mmss(left)
        try {
          const found = await HO.api('plugins/qivicon/pairing/found?types=' + enc(d.types.join(',')))
          if (!alive(s) || my !== view) return
          const box = s.el.querySelector('#pairFound')
          for (const f of found.filter(f => !seen.has(f.uid))) {
            seen.add(f.uid)
            box.insertAdjacentHTML('beforeend', `<div class="pair-found"><div class="row">${thumb(d.image)}<div class="grow"><b>Gefunden: ${HO.esc(d.name)}</b>
                <div class="tiny muted">${HO.esc(f.uid)}</div></div></div>
              ${d.setup === 'steps' ? '<p class="small muted" style="margin:0">Einrichtung bitte in der QIVICON-Oberfläche abschließen – danach erscheint das Gerät in ComfyOS.</p>'
                : `<label class="field" style="margin:0"><span>Name</span><input type="text" data-name-for="${HO.esc(f.uid)}" value="${HO.esc(d.name)}" maxlength="40"></label>
                   ${roomSelect(f.uid)}<button class="btn primary" data-approve="${HO.esc(f.uid)}">${HO.icon('plus', 'sm')} Einbinden</button>`}</div>`)
          }
        } catch (e) { /* next round */ }
        try {   // devices the Home Base includes directly without the inbox
          const fresh = await HO.api('plugins/qivicon/pairing/new')
          if (!alive(s) || my !== view) return
          for (const n of fresh) if (!handled.has(n.id) && !pending.some(p => p.id === n.id)) pending.push(n)
          if (pending.length) return setupView()
        } catch (e) { /* next round */ }
        if (left <= 0) break
        await sleep(3000)
      }
      if (!alive(s) || my !== view) return
      const wait = s.el.querySelector('#pairWait')
      wait.innerHTML = seen.size
        ? `<h3>Suche beendet</h3><p class="small muted">Gefundene Geräte unten einbinden.</p>`
        : `<h3>Kein Gerät gefunden</h3><p class="small muted">Ist das Gerät im Verbindungsmodus und nah genug an der Home Base?</p>
           ${det.reset.length ? `<div style="text-align:left"><p class="small"><b>War es schon einmal verbunden? Dann zuerst zurücksetzen:</b></p>${stepsHTML(det.reset)}</div>` : ''}`
      wait.insertAdjacentHTML('beforeend', `<div class="pair-actions"><button class="btn" data-back>Zurück</button><button class="btn primary" data-search>${HO.icon('refresh', 'sm')} Erneut suchen</button></div>`)
    }
    async function approve(uid, btn) {
      const name = (s.el.querySelector(`[data-name-for="${CSS.escape(uid)}"]`) || {}).value || det.name
      const roomSel = s.el.querySelector(`[data-room-for="${CSS.escape(uid)}"]`)
      const room = roomSel && roomSel.value ? roomSel.value : null
      btn.disabled = true
      let r
      try {
        r = await HO.api(`plugins/qivicon/inbox/${enc(uid)}/approve`, { method: 'POST', body: { label: name.trim() || det.name, room } })
      } catch (e) { btn.disabled = false; return HO.toast(e.message, 'bad', 5000) }
      added = true; handled.add(uid); HO.state.overview = null
      if (r.room_error) HO.toast('Raum konnte nicht zugeordnet werden: ' + r.room_error, 'warn', 6000)
      const roomName = room && !r.room_error ? (rooms.find(x => x.room_id === room) || {}).name : null
      show(`${doneHTML('Gerät erfolgreich verbunden!', `„${name.trim() || det.name}“ ist jetzt in ComfyOS${roomName ? ` · ${roomName}` : ' – den Raum ordnest du in der Übersicht zu'}.`)}
        <div class="pair-actions"><button class="btn" data-more>${HO.icon('plus', 'sm')} Weiteres Gerät</button><button class="btn primary" data-close>Fertig</button></div>`)
    }

    // connected: assign name and room, so that the setup is complete
    function setupView() {
      const n = pending[0]
      added = true; HO.state.overview = null
      const opts = rooms.map(r => `<option value="${HO.esc(r.room_id)}" ${r.room_id === n.room_id ? 'selected' : ''}>${HO.esc(r.name)}</option>`).join('')
      show(`${doneHTML('Gerät erfolgreich verbunden!', 'Gib ihm noch einen Namen und ordne es einem Raum zu.')}
        <label class="field"><span>Name</span><input type="text" id="setupName" value="${HO.esc(n.name)}" maxlength="80" autocomplete="off"></label>
        ${rooms.length ? `<label class="field"><span>Raum</span><select id="setupRoom">${n.room_id ? '' : '<option value="">— später zuordnen —</option>'}${opts}</select></label>` : ''}
        <div class="pair-actions"><button class="btn primary" data-setup-save>${HO.icon('check', 'sm')} Einrichtung abschließen</button></div>`)
    }
    async function setupSave(btn) {
      const n = pending[0]
      const name = s.el.querySelector('#setupName').value.trim()
      const sel = s.el.querySelector('#setupRoom'), room = sel && sel.value ? sel.value : null
      btn.disabled = true
      try {
        if (name && name !== n.name) await HO.api(`devices/${enc(n.id)}/name`, { method: 'PUT', body: { name } })
        if (room && room !== n.room_id) {
          const r = await HO.api(`devices/${enc(n.id)}/room`, { method: 'PUT', body: { target: room } })
          if (r.warning) HO.toast(r.warning, 'warn', 6000)
        }
      } catch (e) { btn.disabled = false; return HO.toast(e.message, 'bad', 5000) }
      pending.shift(); handled.add(n.id); HO.state.overview = null
      const label = name || n.name, roomName = room ? (rooms.find(x => x.room_id === room) || {}).name : null
      HO.toast(`${label} eingerichtet${roomName ? ` · ${roomName}` : ''}`)
      if (pending.length) return setupView()
      show(`${doneHTML('Einrichtung abgeschlossen!', `„${label}“ ist jetzt in ComfyOS${roomName ? ` · ${roomName}` : ''}.`)}
        <div class="pair-actions"><button class="btn" data-more>${HO.icon('plus', 'sm')} Weiteres Gerät</button><button class="btn primary" data-close>Fertig</button></div>`)
    }

    s.el.addEventListener('input', e => {
      if (e.target.id !== 'pairQ') return
      query = e.target.value
      s.el.querySelector('#pairList').innerHTML = listHTML()
    })
    s.el.addEventListener('click', e => {
      const t = e.target.closest('[data-back],[data-vendor],[data-dev],[data-search],[data-approve],[data-more],[data-setup-save]')
      if (!t) return
      if (t.dataset.setupSave != null) return setupSave(t)
      if (t.dataset.back != null) return onBack && onBack()
      if (t.dataset.vendor != null) return devicesView(+t.dataset.vendor)
      if (t.dataset.dev) return prepareView(t.dataset.dev)
      if (t.dataset.search != null) { t.disabled = true; return searchView().finally(() => { t.disabled = false }) }
      if (t.dataset.approve) return approve(t.dataset.approve, t)
      if (t.dataset.more != null) { query = ''; vendorIdx = null; return vendorsView() }
    })

    HO.api('plugins/qivicon/pairing/catalog').then(c => {
      if (!alive(s)) return
      cat = c
      cat.vendors.forEach(v => v.devices.forEach(d => { byId[d.id] = { ...d, vendor: v.name } }))
      vendorsView()
    }).catch(e => { if (alive(s)) s.body().innerHTML = `<p class="small muted">${HO.esc(e.message)}</p>` })
    return s
  }

  /* ---------- Reconnecting (the device was already paired and lost its connection) */
  HO.pairing.reconnect = async function (dev, onDone) {
    let ok = false
    const s = HO.sheet({ title: 'Wiederverbinden', body: '<div class="spinner"></div>' })
    const close = s.close
    s.close = () => { close(); if (ok && onDone) onDone() }
    let info
    try { info = await HO.api(`plugins/qivicon/things/${enc(dev.id)}/reconnect`) } catch (e) {
      if (alive(s)) s.body().innerHTML = `<p class="small muted">${HO.esc(e.message)}</p>`
      return
    }
    if (!alive(s)) return
    const head = `<div class="pair-head">${img(info.image, 'pair-hero')}<div><div class="eyebrow">${HO.esc(dev.room || '')}</div><h3>${HO.esc(dev.name)}</h3>
      <div class="tiny muted">${HO.esc(info.status.detail === 'GONE' ? 'Von der Home Base getrennt' : 'Kein Kontakt zur Home Base')}</div></div></div>`
    if (!info.available) {
      s.body().innerHTML = `${head}<p>Für diesen Gerätetyp bietet die Home Base kein Wiederverbinden an.</p>
        <p class="small muted">${dev.id.startsWith('hue:') ? 'Hue-Lampen verbindest du in der Hue-App neu. Ein übrig gebliebener Eintrag lässt sich von der Home Base löschen.'
          : 'Du kannst das Gerät löschen und über „Gerät hinzufügen“ neu anlernen.'}</p>
        <div class="pair-actions"><button class="btn ghost danger" data-del>${HO.icon('trash', 'sm')} Von der Home Base löschen</button></div>`
      s.el.addEventListener('click', async e => {
        if (!e.target.closest('[data-del]')) return
        close()
        if (await HO.pairing.remove(dev) && onDone) onDone()
      })
      return
    }
    const step = info.steps[0] || { label: '', timeout: 180 }
    const intro = () => {
      s.body().innerHTML = `${head}<p class="small"><b>${HO.esc(step.label || 'Bitte setze das Gerät auf den Werkszustand zurück.')}</b></p>
        ${stepsHTML(info.reset)}<p class="tiny muted">Name, Raum und Einstellungen bleiben erhalten.</p>
        <div class="pair-actions"><button class="btn primary" data-go>Weiter</button></div>`
    }
    async function go(btn) {
      btn.disabled = true
      let r
      try { r = await HO.api(`plugins/qivicon/things/${enc(dev.id)}/reconnect`, { method: 'POST' }) } catch (e) { btn.disabled = false; return HO.toast(e.message, 'bad', 5000) }
      if (!alive(s)) return
      s.body().innerHTML = `<div class="pair-wait"><div class="spinner"></div><h3>Warte auf ${HO.esc(dev.name)} …</h3>
        <p class="small muted">Das Gerät meldet sich nach dem Zurücksetzen von selbst bei der Home Base.</p><div class="pair-count" id="rcCount">${mmss(r.timeout)}</div></div>`
      const end = Date.now() + r.timeout * 1000
      while (alive(s)) {
        const left = Math.max(0, Math.round((end - Date.now()) / 1000))
        const cnt = s.el.querySelector('#rcCount')
        if (cnt) cnt.textContent = mmss(left)
        try {
          const st = await HO.api(`plugins/qivicon/things/${enc(dev.id)}/state`)
          if (st.connected) {
            ok = true; HO.state.overview = null
            if (alive(s)) s.body().innerHTML = `${doneHTML('Gerät erfolgreich wiederverbunden!', dev.name)}<div class="pair-actions"><button class="btn primary" data-close>Fertig</button></div>`
            return
          }
        } catch (e) { /* next round */ }
        if (left <= 0) break
        await sleep(3000)
      }
      if (!alive(s)) return
      s.body().innerHTML = `${head}<p><b>Keine Rückmeldung vom Gerät.</b></p><p class="small muted">Setze es noch einmal zurück und versuche es erneut. Batterien prüfen; das Gerät sollte nah an der Home Base sein.</p>
        ${stepsHTML(info.reset)}<div class="pair-actions"><button class="btn primary" data-go>Erneut versuchen</button></div>`
    }
    intro()
    s.el.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (b) go(b) })
  }

  /* ---------- Delete from the Home Base for good */
  HO.pairing.remove = async function (dev) {
    if (!await HO.confirm(`„${dev.name}“ endgültig von der Home Base löschen? Es verschwindet auch aus der Magenta-App und aus ComfyOS. Zurück nur durch erneutes Anlernen.`,
      { title: 'Von der Home Base löschen', ok: 'Löschen', danger: true })) return false
    try {
      await HO.api(`plugins/qivicon/things/${enc(dev.id)}`, { method: 'DELETE' })
    } catch (e) { HO.toast(e.message, 'bad', 5000); return false }
    HO.toast(`${dev.name} von der Home Base gelöscht`); HO.state.overview = null
    return true
  }
})(window.HO)
