/* ComfyOS – System: Plugins, Zuhause/Abwesend, Erreichbarkeit, Sicherheit, Update */
(function (HO) {
  'use strict'

  // Show the overlay "ComfyOS startet neu" and wait until the server answers again (on request: with the new version) –
  // then reload the page.
  function waitForRestart(target) {
    const ov = document.createElement('div')
    ov.className = 'overlay'
    ov.innerHTML = `<div class="card" style="text-align:center;max-width:340px"><div class="spinner"></div><h3>ComfyOS startet neu</h3>
      <p class="muted small" id="rsMsg">Update auf ${HO.esc(target || 'die neue Version')} wird geladen …</p></div>`
    document.body.appendChild(ov)
    let tries = 0
    // Einmal je Sekunde nachfragen, ob ComfyOS wieder da ist.
    const poll = async () => {
      tries++
      try {
        const h = await fetch('/api/health', { cache: 'no-store' }).then(r => r.json())
        if (!target || h.version === target) { location.reload(); return }
      } catch (e) { }
      if (tries > 90) ov.querySelector('#rsMsg').textContent = 'Dauert ungewöhnlich lange. Läuft ComfyOS noch? Seite später neu laden.'
      setTimeout(poll, 2000)
    }
    setTimeout(poll, 2500)
  }

  // Switch HTTPS on or off. If ComfyOS restarts for that, the new address is shown in the overlay.
  async function switchHttps(on, regenerate) {
    const r = await HO.api('https', { method: 'POST', body: { enabled: on, regenerate } })
    if (!r.restarting) { HO.toast('Gespeichert – wirkt nach dem nächsten Start von ComfyOS.', 'warn', 6000); return }
    const ov = document.createElement('div')
    ov.className = 'overlay'
    ov.innerHTML = `<div class="card" style="text-align:center;max-width:360px"><div class="spinner"></div><h3>ComfyOS startet neu</h3>
      <p class="muted small">Gleich geht es weiter unter<br><a href="${HO.esc(r.url)}">${HO.esc(r.url)}</a>${on ? '<br>Der Browser fragt einmal nach dem Zertifikat – bestätigen.' : ''}</p></div>`
    document.body.appendChild(ov)
    setTimeout(() => { location.href = r.url }, 6000)
  }

  // Tile "Serversetup": set up or remove ComfyOS as a service, with state and log of the setup.
  function srvHTML(v) {
    const head = (badge) => `<div class="card-head"><div><div class="eyebrow">Raspberry Pi</div><h3>Serversetup</h3></div>${badge}</div>`
    if (!v.supported) return head('<span class="badge">nicht verfügbar</span>') +
      '<p class="small muted">Nur auf dem Raspberry Pi (bzw. Linux mit systemd). Dort richtet diese Kachel ComfyOS als Dienst ein, der beim Start automatisch läuft, und setzt die Adresse http://comfyos/.</p>'
    const run = v.active === 'active', badge = v.job_running ? '<span class="badge warn">läuft …</span>'
      : v.installed ? `<span class="badge ${run ? 'ok' : 'bad'}">${run ? 'Dienst aktiv' : 'Dienst ' + HO.esc(v.active || 'aus')}</span>` : '<span class="badge">nicht eingerichtet</span>'
    return head(badge) + `<p class="small muted">Richtet ComfyOS als Systemdienst ein: startet automatisch mit dem Pi, nach Abstürzen und Updates neu, auf Port 80 – erreichbar unter <b>http://${HO.esc(v.installed ? v.hostname : 'comfyos')}/</b>.</p>
      <dl class="kv" style="margin:12px 0"><dt>Gerät</dt><dd>${HO.esc(v.device)}</dd><dt>Netzwerkname</dt><dd>${HO.esc(v.hostname)}</dd>
        <dt>Autostart</dt><dd>${v.enabled === 'enabled' ? 'ja' : 'nein'}</dd><dt>Diese Sitzung</dt><dd>${v.as_service ? 'läuft als Dienst' : 'manuell gestartet'}</dd></dl>
      ${v.sudo ? '' : `<div class="notice warn">${HO.icon('warn', 'sm')}<div>Keine Administratorrechte ohne Passwort. Einmalig im Terminal im ComfyOS-Ordner: <code>sudo ./install_pi.sh</code></div></div>`}
      <label class="field"><span>Adresse im Heimnetz</span><div class="row"><span class="muted">http://</span><input type="text" id="srvName" value="${HO.esc(v.installed ? v.hostname : 'comfyos')}" maxlength="32" style="max-width:180px"><span class="muted">/</span></div></label>
      <div class="form-actions"><button class="btn primary" data-act="srvInstall" ${v.job_running || !v.sudo ? 'disabled' : ''}>${v.installed ? 'Neu einrichten' : 'Dienst einrichten'}</button>
        ${v.installed ? `<button class="btn ghost danger" data-act="srvRemove" ${v.job_running || !v.sudo ? 'disabled' : ''}>Rückgängig machen</button>` : ''}</div>
      ${v.log ? `<pre class="tiny muted" style="white-space:pre-wrap;max-height:180px;overflow:auto;background:var(--bg-2);padding:10px;border-radius:10px;margin-top:12px">${HO.esc(v.log)}</pre>` : ''}`
  }
  /* ---------- Device administration: technical, collapsible lists – building blocks for the tiles of the plugins */
  const MSTATUS = { missing: ['bad', 'Fehlt auf der Bridge'], unreachable: ['warn', 'Nicht erreichbar'], check: ['warn', 'Prüfen'],
    ok: ['ok', 'OK'], new: ['accent', 'Neu'], removed: ['', 'Entfernt'], inbox: ['accent', 'Gefunden'] }
  let roomsCache = []
  const normName = s => String(s || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss').replace(/[^a-z0-9]/g, '')

  // Details of a device as a list: room, id, type, maker, further properties, battery, last seen.
  function propsKV(r) {
    const rows = [['Raum', r.room], ['ID', r.id], ['Typ', r.type], ['Hersteller', r.vendor],
      ...Object.entries(r.props || {}), r.source && r.source.name && r.source.name !== r.name ? ['Name bei der Quelle', r.source.name] : null,
      r.battery != null ? ['Batterie', r.battery + ' %'] : null, r.last_seen ? ['Zuletzt gesehen', HO.relTime(r.last_seen)] : null,
      r.detail ? ['Hinweis', r.detail] : null].filter(x => x && x[1] != null && x[1] !== '')
    return `<dl class="kv small">${rows.map(([k, v]) => `<dt>${HO.esc(k)}</dt><dd>${HO.esc(v)}</dd>`).join('')}</dl>`
  }
  // Collapsible row of a device in the lists of the plugins: name, status, details and actions.
  function mRow(r, status, actions, sub, open) {
    const [cls, label] = MSTATUS[status] || MSTATUS.ok
    return `<details class="mrow" ${open ? 'open' : ''}><summary><span class="mrow-name">${HO.esc(r.name)}</span>
        <span class="mrow-sub small muted">${HO.esc(sub != null ? sub : (r.room || ''))}</span><span class="badge ${cls}">${label}</span></summary>
      <div class="mrow-body">${(r.issues || []).length ? `<div class="small" style="color:var(--warn);margin-bottom:6px">${r.issues.map(HO.esc).join(' · ')}</div>` : ''}
        ${propsKV(r)}${actions ? `<div class="row wrap" style="margin-top:10px">${actions}</div>` : ''}</div></details>`
  }
  // Collapsible group of device rows with a count and an optional hint; empty it gives nothing.
  function mGroup(title, rows, open, hint) {
    if (!rows.length) return ''
    return `<details class="mgroup" data-g="${title}" ${open ? 'open' : ''}><summary><span>${title}</span><span class="badge">${rows.length}</span></summary>
      ${hint ? `<p class="tiny muted" style="margin:4px 0 6px">${hint}</p>` : ''}<div class="mlist">${rows.join('')}</div></details>`
  }
  const removeBtn = r => `<button class="btn sm ghost danger" data-remove="${HO.esc(r.id)}" data-name="${HO.esc(r.name)}" data-imported="${r.adopted ? 1 : 0}">${HO.icon('trash', 'sm')} Aus ComfyOS entfernen</button>`
  const restoreBtn = r => `<button class="btn sm" data-restore="${HO.esc(r.id)}">Wiederherstellen</button>`
  // keep opened/collapsed groups and rows after a redraw
  const openState = box => Object.fromEntries([...box.querySelectorAll('details.mgroup')].map(d => [d.dataset.g, d.open]))
  function restoreOpen(box, open) {
    box.querySelectorAll('details.mgroup').forEach(d => { if (d.dataset.g in open) d.open = open[d.dataset.g] })
  }

  // list building blocks for the tiles of the plugins (see plugins.js: tools.ui)
  Object.assign(HO.plugins.ui, { mRow, mGroup, removeBtn, restoreBtn, openState, restoreOpen })

  /* ---------- Licence: state, redeeming, preview and generator (both owner only) */
  const PLAN_TITLE = { basic: 'Basic', pro: 'Pro', owner: 'Owner' }
  const fmtDay = ts => new Date(ts * 1000).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' })
  let issuerOpen = false
  // Tile "Lizenz": plan in effect, its sources, redeem a key or file – for the owner additionally the preview and
  // issuing licences.
  function licenseHTML(l) {
    const head = badge => `<div class="card-head"><div><div class="eyebrow">Plan</div><h3>Lizenz</h3></div>${badge}</div>`
    if (!l) return head('') + '<p class="small muted">Nicht verfügbar.</p>'
    const SRC = { owner: 'Owner-Lizenz', gift: 'Lizenzdatei', key: 'Gekaufter Schlüssel' }
    const pv = l.preview
    const rows = l.sources.map(s => `<div class="li"><div class="grow"><b>${HO.esc(SRC[s.source] || s.source)}${s.plan ? ' · ' + HO.esc(PLAN_TITLE[s.plan] || s.plan) : ''}</b>${s.name ? ` <span class="muted small">für ${HO.esc(s.name)}</span>` : ''}
        <div class="small muted">${HO.esc(s.detail)}${s.expires ? ` Gültig bis ${fmtDay(s.expires)}.` : ''}${s.key_hint ? ` Schlüssel ${HO.esc(s.key_hint)}.` : ''}</div></div>
        <span class="badge ${s.active ? 'ok' : 'bad'}">${s.active ? 'aktiv' : 'ungültig'}</span></div>`).join('')
    const seg = p => `<button type="button" data-preview="${p}" class="${(pv ? pv.plan : 'owner') === p ? 'active' : ''}">${PLAN_TITLE[p]}</button>`
    const preview = !l.can_preview ? '' : `<div class="https-box"><div class="field"><span>Testansicht <span class="muted small">(nur Owner)</span></span>
        <p class="small muted" style="margin:0">Zeigt ComfyOS so, wie es Nutzer mit Basic oder Pro erleben: Sperren greifen wirklich, und Flows, die der Plan nicht ausführt, pausieren so lange. Deine Daten bleiben unverändert. Die Ansicht endet von selbst.</p>
        <div class="row wrap" style="margin-top:6px"><div class="seg">${seg('basic')}${seg('pro')}${seg('owner')}</div>
          <select id="pvMinutes" aria-label="Dauer der Testansicht" style="width:auto"><option value="30">30 Minuten</option><option value="60" selected>1 Stunde</option><option value="240">4 Stunden</option><option value="1440">24 Stunden</option></select></div>
        ${pv ? `<p class="small" style="margin:8px 0 0">Aktiv bis ${new Date(pv.until * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })} Uhr${l.paused_flows ? ` · ${l.paused_flows} ${l.paused_flows === 1 ? 'Flow pausiert' : 'Flows pausieren'}` : ''}.</p>` : ''}</div></div>`
    const is = l.issuer || {}
    const issuedRows = (is.issued || []).map(x => `<div class="li"><div class="grow"><b>${HO.esc(PLAN_TITLE[x.plan] || x.plan)}${x.name ? ' · ' + HO.esc(x.name) : ''}</b>
        <div class="small muted">Nr. ${HO.esc(x.id)} · ausgestellt ${fmtDay(x.issued)}${x.exp ? ' · gültig bis ' + fmtDay(x.exp) : ' · ohne Ablauf'}${x.iid ? ' · nur Gerät ' + HO.esc(x.iid) : ''}</div></div></div>`).join('')
    const issuer = !is.available ? '' : `<details class="https-box" id="issuerBox" ${issuerOpen ? 'open' : ''}><summary style="cursor:pointer"><b>Lizenz-Generator</b> <span class="muted small">(nur Owner)</span></summary>
        ${is.ready ? `<p class="small muted">Stellt eine Lizenz aus, die der Empfänger unter System → Lizenz einlöst – ohne Internet. Jede Ausstellung verlangt dein ComfyOS-Passwort.</p>
          <form id="issueForm"><div class="grid cols-2">
              <label class="field"><span>Plan</span><select id="issPlan"><option value="pro">Pro</option><option value="owner">Owner</option></select></label>
              <label class="field" style="margin-top:0"><span>Laufzeit</span><select id="issDays"><option value="">ohne Ablauf</option><option value="30">30 Tage</option><option value="90">90 Tage</option><option value="365">1 Jahr</option></select></label></div>
            <label class="field"><span>Empfänger <span class="muted small">(steht in der Lizenz)</span></span><input type="text" id="issName" maxlength="80" autocomplete="off"></label>
            <label class="field"><span>Geräte-Kennung <span class="muted small">(optional – dann gilt die Lizenz nur auf diesem einen Gerät)</span></span><input type="text" id="issIid" maxlength="16" autocomplete="off" spellcheck="false" placeholder="16 Zeichen, steht beim Empfänger unter System → Lizenz"></label>
            <label class="field"><span>Dein ComfyOS-Passwort</span><input type="password" id="issPw" autocomplete="current-password" required></label>
            <div class="form-actions"><button class="btn primary" type="submit">${HO.icon('tag', 'sm')} Lizenz erstellen</button>
              <button class="btn ghost danger" type="button" data-act="signRemove">Signaturschlüssel entfernen</button></div></form>
          ${issuedRows ? `<div class="field" style="margin-top:14px"><span>Ausgestellt</span><div class="list">${issuedRows}</div></div>` : ''}`
        : `<p class="small muted">Zum Ausstellen braucht diese Installation deinen Signaturschlüssel (<code>OWNER_SIGN_KEY</code> aus <code>tools/build_secrets.env</code>). Er wird nur auf diesem Gerät gespeichert, nie angezeigt und nie übertragen.</p>
          <div class="notice warn" style="margin-bottom:10px">${HO.icon('warn', 'sm')}<div class="small">Wer dieses Gerät und dein ComfyOS-Passwort hat, kann damit Lizenzen ausstellen. Hinterlege den Schlüssel nur auf deiner eigenen Installation.</div></div>
          <form id="signForm"><label class="field"><span>Signaturschlüssel</span><input type="password" id="signKey" autocomplete="off" spellcheck="false" required></label>
            <label class="field"><span>Dein ComfyOS-Passwort</span><input type="password" id="signPw" autocomplete="current-password" required></label>
            <div class="form-actions"><button class="btn" type="submit">Schlüssel hinterlegen</button></div></form>`}</details>`
    return head(`<span class="badge ${pv ? 'warn' : l.licensed_plan === 'basic' ? '' : 'accent'}">${pv ? 'Testansicht: ' : ''}${HO.esc(l.plan_title)}</span>`) + `
      <div class="list">${rows || '<div class="li small muted">Keine Lizenz hinterlegt – ComfyOS läuft als Basic.</div>'}</div>
      <form id="licForm" style="margin-top:14px"><label class="field"><span>Lizenz einlösen</span>
          <textarea id="licText" rows="2" maxlength="8192" autocomplete="off" spellcheck="false" placeholder="Lizenzschlüssel oder Inhalt der Lizenzdatei einfügen"></textarea></label>
        <div class="form-actions"><button class="btn primary" type="submit">Einlösen</button>
          <label class="btn">${HO.icon('upload', 'sm')} Lizenzdatei<input type="file" id="licFile" accept=".comfyos-license,.json,.txt" hidden></label>
          ${l.sources.some(s => s.source === 'key') ? '<button class="btn" type="button" data-act="licCheck">Jetzt prüfen</button>' : ''}
          ${l.sources.some(s => s.source !== 'owner') ? '<button class="btn ghost danger" type="button" data-act="licRemove">Lizenz entfernen</button>' : ''}</div></form>
      <p class="tiny muted" style="margin-top:10px">${l.provider ? `Ein gekaufter Schlüssel wird beim Einlösen und danach etwa alle 24 Stunden bei <b>${HO.esc(l.provider)}</b> geprüft. Gesendet werden nur der Schlüssel und die anonyme Geräte-Kennung. Lizenzdateien und die Owner-Lizenz funktionieren ohne Internet.`
        : 'Lizenzen werden auf dieser Installation nur lokal geprüft – dabei wird nichts ins Internet gesendet.'}</p>
      <dl class="kv" style="margin-top:8px"><dt>Geräte-Kennung</dt><dd><code>${HO.esc(l.instance_id)}</code> <button class="btn-icon sm" type="button" data-act="iidCopy" data-value="${HO.esc(l.instance_id)}" aria-label="Kopieren">${HO.icon('copy', 'sm')}</button></dd></dl>
      ${preview}${issuer}`
  }

  // Put text into the clipboard; without the Clipboard API through an invisible text field.
  async function copyText(text) {
    if (await HO.copy(text)) return true
    const ta = document.createElement('textarea')
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'
    document.body.appendChild(ta); ta.select()
    let ok = false
    try { ok = document.execCommand('copy') } catch (e) { }
    ta.remove()
    return ok
  }

  // Dialog "Lizenz erstellt": short form to copy, details of the licence, download the file.
  function issuedSheet(r) {
    const d = r.license
    HO.sheet({ title: 'Lizenz erstellt', body: `<p class="small muted">Schick dem Empfänger die Kurzform oder die Datei. Eingelöst wird sie unter System → Lizenz – ohne Internet.</p>
        <label class="field"><span>Kurzform</span><textarea rows="6" readonly spellcheck="false">${HO.esc(r.key)}</textarea></label>
        <dl class="kv" style="margin-top:12px"><dt>Nummer</dt><dd>${HO.esc(d.id)}</dd><dt>Plan</dt><dd>${HO.esc(PLAN_TITLE[d.plan] || d.plan)}</dd>
          ${d.name ? `<dt>Empfänger</dt><dd>${HO.esc(d.name)}</dd>` : ''}<dt>Gültig</dt><dd>${d.exp ? 'bis ' + fmtDay(d.exp) : 'ohne Ablauf'}</dd>
          ${d.iid ? `<dt>Nur Gerät</dt><dd>${HO.esc(d.iid)}</dd>` : ''}</dl>`,
      actions: [{ label: 'Fertig', kind: 'ghost' },
        { label: 'Kopieren', icon: 'copy', onClick: async () => { HO.toast(await copyText(r.key) ? 'Kurzform kopiert' : 'Kopieren nicht möglich – bitte von Hand markieren', 'ok'); return true } },
        { label: 'Als Datei', icon: 'download', kind: 'primary', onClick: () => {
          const a = document.createElement('a')
          a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 1)], { type: 'application/json' }))
          a.download = `comfyos-${d.plan}-${d.id}.comfyos-license`
          document.body.appendChild(a); a.click(); a.remove()
          setTimeout(() => URL.revokeObjectURL(a.href), 4000)
          return true
        } }] })
  }

  /* System page in two tabs (since 0.19.3): "Einstellungen" (#settings) and "Plugins" (#settings?tab=plugins).
     Both have the same head (tabs, columns, arranging) and each its own tile arrangement (tileGrid with the
     key "system" or "plugins"). The tab is part of the address – back button and links (connection pill,
     "Zu den Plugins") thereby work as with a page of its own. */
  const sysTab = () => /(^|&)tab=plugins(&|$)/.test(HO.hashParams || '') ? 'plugins' : 'system'
  // Head of the System page: tabs Einstellungen/Plugins, column choice, "Automatisch" and "Anordnen".
  function headHTML(tab) {
    return `<div class="section-head sys-head">
        <div class="seg sys-tabs" role="tablist">${[['system', 'Einstellungen', 'settings'], ['plugins', 'Plugins', 'layers']].map(([k, l, ic]) =>
          `<button type="button" role="tab" data-systab="${k}" class="${k === tab ? 'active' : ''}" aria-selected="${k === tab}">${HO.icon(ic, 'sm')}${l}</button>`).join('')}</div>
        <div class="row">${HO.cols.segHTML(tab, HO.cols.chosen(tab, 2))}
          <button class="btn sm" data-act="arrangeAuto" hidden title="Eigene Anordnung für diese Spaltenzahl vergessen">${HO.icon('refresh', 'sm')} Automatisch</button>
          <button class="btn sm" data-act="arrange" aria-pressed="false" title="Kacheln frei anordnen – je Spaltenzahl merkt sich ComfyOS eine eigene Anordnung">${HO.icon('move', 'sm')} Anordnen</button></div></div>`
  }
  // Set up the tile grid of the tab shown; it sorts in new plugin tiles itself.
  function mountGrid(page, s, tab) {
    if (grid) grid.destroy()
    grid = HO.tileGrid({ page, box: page.querySelector('#sysGrid'), key: tab, saved: ((s && s.layout) || {})[tab] })
    HO.plugins.onChange = () => { if (grid) grid.adopt() }
  }

  // Tab "Plugins": at the top the head tile (add own plugins, "Neu suchen"), below it one tile per plugin
  async function renderPlugins(page) {
    const [s, plug] = await Promise.all([HO.api('settings').catch(() => ({})), HO.api('plugins').catch(() => ({ plugins: [] })),
      HO.api('rooms').catch(() => []).then(r => { roomsCache = r })])
    page.innerHTML = headHTML('plugins') + HO.plugins.headerHTML() + `<div id="sysGrid">${HO.plugins.sectionHTML(plug, roomsCache)}</div>`
    mountGrid(page, s, 'plugins')
    HO.plugins.mount(page)
  }

  /* Demo home (since 0.23): tile "Demo-Wohnung" under Einstellungen – opens the sample home for this session. While the
     session is in the demo, the tab shows only what belongs to the demo: system settings, backups and updates
     belong to the real home and stay hidden (the server refuses them in the demo anyway). */
  function demoCardHTML() {
    return `<section class="card" id="demoCard"><div class="card-head"><div><div class="eyebrow">Ausprobieren</div><h3>Demo-Wohnung</h3></div>${HO.icon('home')}</div>
        <p class="small muted">Eine Beispielwohnung mit Lampen, Heizung, Fenstern, Taster und Lautsprecher – zum Zeigen und Ausprobieren. Nur diese Sitzung wechselt in die Demo: deine eigene Wohnung, deine Automationen und deine Einstellungen bleiben unberührt und laufen weiter. „Demo beenden“ bringt dich zurück zu deinem Dashboard.</p>
        <div class="form-actions"><button class="btn primary" data-act="demoStart">${HO.icon('play', 'sm')} Demo-Wohnung ansehen</button></div></section>`
  }
  // Tab "Einstellungen" while this session is in the demo: only the card of the demo with its two buttons.
  function renderDemo(page) {
    if (grid) { grid.destroy(); grid = null }
    page.innerHTML = headHTML('system') + `<div class="grid cols-2"><section class="card"><div class="card-head"><div><div class="eyebrow">Demo</div><h3>Du bist in der Demo-Wohnung</h3></div>${HO.icon('home')}</div>
        <p class="small muted">Alles hier ist ein Beispiel: Geräte, Grundriss, Szenen, Heizplan und Flows. Was du änderst, bleibt in der Demo. Flows werden gezeigt, laufen in der Demo aber nicht. Systemeinstellungen, Backups und Updates gehören zu deiner eigenen Wohnung und sind ausgeblendet.</p>
        <div class="form-actions"><button class="btn ghost" data-act="demoReset">Zurücksetzen</button><button class="btn primary" data-act="demoStop">Demo beenden</button></div></section></div>`
    // the work is done by app.js (HO.demoAction) – the page title says "– DEMO" on every page
    page.onclick = e => {
      const tab = e.target.closest('[data-systab]')
      if (tab) { if (tab.dataset.systab === 'plugins') location.hash = 'settings?tab=plugins'; return }
      const t = e.target.closest('[data-act]')
      if (t) HO.demoAction(t.dataset.act)
    }
  }

  // Load and draw the tab "Einstellungen": all tiles of the System page, then the tile grid and the content that
  // loads later.
  async function render(page) {
    if (sysTab() === 'plugins') return renderPlugins(page)
    if (HO.demo && HO.demo.active) return renderDemo(page)
    const [s, net, up, sync, home, rs, srv, tls, lic] = await Promise.all([HO.api('settings'), HO.api('network'), HO.api('update'),
      HO.api('sync/status'), HO.api('home'), HO.api('routines'), HO.api('serversetup').catch(() => ({ supported: false })),
      HO.api('https').catch(() => null), HO.api('license').catch(() => null)])
    const routineOpts = sel => `<option value="">–</option>` + rs.routines.map(r => `<option value="${HO.esc(r.key)}" ${r.key === sel ? 'selected' : ''}>${HO.esc(r.name)}</option>`).join('')
    page.innerHTML = headHTML('system') + `
      <div id="sysGrid">

      <section class="card" id="homeCard"><div class="card-head"><div><div class="eyebrow">Heizung</div><h3>Zuhause / Abwesend</h3></div>${HO.icon('home')}</div>
        <p class="small muted">Welche Heizroutine gilt, wenn du zuhause bist – und welche, wenn alle weg sind. Umgeschaltet wird auf der Startseite oder per Flow.</p>
        <div class="grid cols-2"><label class="field"><span class="small muted">Zuhause</span><select data-map="present">${routineOpts(home.present_routine)}</select></label>
          <label class="field" style="margin-top:0"><span class="small muted">Abwesend</span><select data-map="away">${routineOpts(home.away_routine)}</select></label></div>
        ${sync.configured ? `<div class="sync-line" style="margin-top:12px">${HO.syncLine(sync)}</div>` : ''}
      </section>

      <section class="card" id="netCard"><div class="card-head"><div><div class="eyebrow">Heimnetz</div><h3>Erreichbarkeit</h3></div>${HO.icon('globe')}</div>
        <p class="small muted">So öffnest du ComfyOS am Handy (im selben WLAN). Tipp: zum Home-Bildschirm hinzufügen – dann startet ComfyOS wie eine App.</p>
        <div class="list">${net.urls.map(u => `<div class="li"><div class="grow"><div class="small muted">${HO.esc(u.label)}</div><a href="${HO.esc(u.url)}" style="overflow-wrap:anywhere">${HO.esc(u.url)}</a></div>
          <button class="btn-icon sm" data-copy="${HO.esc(u.url)}" aria-label="Kopieren">${HO.icon('copy', 'sm')}</button></div>`).join('')}</div>
        ${tls ? `<div class="https-box">
          <label class="check"><input type="checkbox" id="httpsOn" ${tls.enabled ? 'checked' : ''}><span><b>HTTPS verwenden</b> <span class="muted small">(verschlüsselt, Port ${tls.https_port}; HTTP leitet dann um)</span></span></label>
          <div class="small muted" style="margin:6px 0 0 28px">${tls.active ? `${HO.icon('lock', 'sm')} Diese Verbindung ist verschlüsselt.` : tls.enabled ? 'Eingeschaltet – wirkt nach dem Neustart.' : '<b>Empfohlen:</b> Aus – Passwort und Sitzung gehen unverschlüsselt durchs WLAN und könnten dort mitgelesen werden.'}
            ${tls.cert && tls.cert.self_signed ? ` Selbst signiertes Zertifikat (gültig bis ${HO.esc(tls.cert.not_after)}): Der Browser fragt je Gerät einmal nach – „Trotzdem fortfahren" bzw. „Zertifikat anzeigen → vertrauen".` : ''}
            ${tls.cert && tls.cert.sha256 ? `<details style="margin-top:6px"><summary style="cursor:pointer">Fingerabdruck zum Vergleichen</summary><code style="overflow-wrap:anywhere">${HO.esc(tls.cert.sha256)}</code></details>` : ''}</div>
          ${tls.launcher ? '' : `<div class="notice" style="margin-top:10px">${HO.icon('info', 'sm')}<div>ComfyOS wurde noch über den alten Startbefehl gestartet. Damit HTTPS wirkt, einmal das <b>Serversetup</b> erneut ausführen (Pi) bzw. <code>start.sh</code>/<code>start.bat</code> neu starten.</div></div>`}
          ${tls.enabled ? `<button class="btn sm" data-act="httpsRegen" style="margin-top:10px">Zertifikat neu erstellen</button>` : ''}
          <div class="notice" style="margin-top:10px">${HO.icon('info', 'sm')}<div class="small">Nach einem Protokollwechsel (HTTP ↔ HTTPS) werden vorhandene Sitzungs-Cookies ungültig. Falls die Anmeldung danach nicht funktioniert: im Browser die Cookies für diese Seite löschen (Safari: Einstellungen → Datenschutz → Website-Daten → „comfyos" entfernen; Chrome/Firefox: Entwicklertools → Anwendung/Speicher → Cookies löschen), dann neu anmelden.</div></div>
          </div>` : ''}
        ${net.is_own_name && net.port80 ? '' : `<div class="notice" style="margin-top:12px">${HO.icon('info', 'sm')}<div>Für die kurze Adresse <b>${net.scheme || 'http'}://comfyos/</b>: auf dem Raspberry Pi <code>sudo ./install_pi.sh</code> ausführen (setzt Name und Port 80). Auf diesem Rechner (${HO.esc(net.hostname)}) geht auch: in der FRITZ!Box unter Heimnetz → Netzwerk das Gerät auf „comfyos" umbenennen – dann klappt <b>${net.scheme || 'http'}://comfyos${net.port80 ? '' : ':' + net.port}/</b>.</div></div>`}
      </section>

      <section class="card" id="srvCard">${srvHTML(srv)}</section>

      <section class="card" id="secCard"><div class="card-head"><div><div class="eyebrow">Zugang</div><h3>Sicherheit</h3></div>${HO.icon('shield')}</div>
        <dl class="kv" style="margin-bottom:6px"><dt>Zugangsdaten</dt><dd>${s.secret_backend === 'keyring' ? 'Schlüsselbund des Systems' : 'geschützte Datei (nur dieser Benutzer)'}</dd></dl>
        ${s.secret_backend === 'keyring' ? '' : '<p class="tiny muted" style="margin-bottom:14px">Geschützt durch Dateirechte. Eine Kopie der SD-Karte enthält deine Zugangsdaten – Backups bitte sicher aufbewahren.</p>'}
        <details class="mgroup" id="sessGroup"><summary><span>Angemeldete Geräte</span><span class="badge" id="sessBadge"></span></summary>
          <div class="list" id="sessList" style="margin-top:6px"><div class="li small muted">Wird geladen …</div></div>
          <p class="tiny muted" style="margin:6px 0 2px">Abmeldung nach ${s.session_idle_days || 30} Tagen ohne Nutzung.</p></details>
        <form id="pwForm"><label class="field"><span>Aktuelles ComfyOS-Passwort</span><input type="password" id="pwOld" autocomplete="current-password"></label>
          <label class="field"><span>Neues Passwort</span><input type="password" id="pwNew" autocomplete="new-password" minlength="6"></label>
          <div class="form-actions"><button class="btn" type="submit">Passwort ändern</button></div></form>
        <form id="hostsForm" style="border-top:1px solid var(--line);margin-top:16px;padding-top:4px">
          <label class="field"><span>Weitere Adressen <span class="muted small">(z. B. Name im VPN, durch Komma getrennt)</span></span>
            <input type="text" id="extraHosts" maxlength="600" placeholder="comfyos.example-vpn.net" value="${HO.esc((s.allowed_hosts || []).join(', '))}"></label>
          <p class="tiny muted">IP-Adressen und Namen im Heimnetz (comfyos, *.local, *.fritz.box …) funktionieren immer. Andere Namen lehnt ComfyOS zum Schutz vor manipulierten Webseiten ab, bis du sie hier einträgst.</p>
          <div class="form-actions"><button class="btn" type="submit">Adressen speichern</button></div></form>
        <div class="form-actions" style="border-top:1px solid var(--line);padding-top:16px"><button class="btn" data-act="logoutall">Andere Geräte abmelden</button><button class="btn ghost danger" data-act="logout">${HO.icon('logout', 'sm')} Abmelden</button></div>
      </section>

      <section class="card" id="accentCard"><div class="card-head"><div><div class="eyebrow">Darstellung · Pro</div><h3>Akzentfarbe</h3></div>${HO.icon('palette')}</div>
        <p class="small muted">Die Akzentfarbe bestimmt Buttons, Links und Hervorhebungen in ComfyOS.</p>
        <div id="accentPicks" class="chips" style="margin:12px 0">
          ${['#109482','#2196f3','#9c27b0','#e91e63','#ff9800','#4caf50','#f44336','#607d8b',''].map(c => {
            const active = (s.accent_color || '') === c
            return c
              ? '<button class="btn sm' + (active ? ' primary' : '') + '" data-accent="' + c + '" style="min-width:40px;background:' + c + ';border-color:' + c + ';color:#fff">' + (active ? HO.icon('check', 'sm') : '&nbsp;') + '</button>'
              : '<button class="btn sm' + (active ? ' primary' : '') + '" data-accent="">Standard</button>'
          }).join('')}
          <label class="btn sm" style="position:relative;overflow:hidden">Eigene …<input type="color" id="accentCustom" value="${HO.esc(s.accent_color || '#109482')}" style="position:absolute;opacity:0;width:100%;height:100%;cursor:pointer;top:0;left:0"></label>
        </div></section>

      <section class="card" id="pushCard"><div class="card-head"><div><div class="eyebrow">Flow</div><h3>Benachrichtigungen</h3></div>${HO.icon('bell')}</div>
        <p class="small muted">Wird geladen …</p></section>

      <section class="card" id="egressCard"><div class="card-head"><div><div class="eyebrow">Datenschutz</div><h3>Externe Verbindungen</h3></div>${HO.icon('globe')}</div>
        <p class="small muted">Wird geladen …</p></section>

      <section class="card" id="sysInfo"><div class="card-head"><div><div class="eyebrow">Server</div><h3>Systemauslastung</h3></div>${HO.icon('gauge')}</div>
        <p class="small muted">Wird geladen …</p></section>

      <section class="card" id="licenseCard">${licenseHTML(lic)}</section>

      <section class="card" id="backupCard"><div class="card-head"><div><div class="eyebrow">Datensicherung</div><h3>Backup & Restore</h3></div>${HO.icon('download')}</div>
        <p class="small muted">Erstelle Backups von Grundriss, Routinen, Flows, Einstellungen und mehr. Maximal 10 Backups werden aufbewahrt.</p>
        <div id="backupList" class="list"><div class="li small muted">Wird geladen …</div></div>
        <div class="form-actions" style="margin-top:12px"><button class="btn primary" data-act="backupCreate">${HO.icon('download', 'sm')} Backup erstellen</button>
          <label class="btn">${HO.icon('upload', 'sm')} Wiederherstellen<input type="file" accept=".zip,application/zip" id="restoreFile" hidden></label>
          <button class="btn" data-act="exportLogs">${HO.icon('list', 'sm')} Logs exportieren</button></div>
        <details class="mgroup" style="margin-top:14px"><summary><span>Umzugspaket</span></summary>
          <p class="small muted" style="margin:8px 0">Exportiert Einstellungen, Räume, Routinen, Flows und Zugangsdaten als verschlüsseltes Paket zum Mitnehmen auf eine neue Installation.</p>
          <div class="form-actions"><button class="btn" data-act="migrateExport">${HO.icon('download', 'sm')} Paket exportieren</button>
            <label class="btn">${HO.icon('upload', 'sm')} Paket einspielen<input type="file" accept=".zip,application/zip" id="migrateImport" hidden></label></div>
        </details></section>

      <section class="card" id="updateCard"><div class="card-head"><div><div class="eyebrow">Version ${HO.esc(up.current)}</div><h3>Update</h3></div>${HO.icon('download')}</div>
        <p class="small muted">Neue Versionen erscheinen als Release auf GitHub. Logs, Routinen, Grundriss, Einstellungen und Zugangsdaten bleiben erhalten; vorher wird ein Backup angelegt.</p>
        <p class="tiny muted">ComfyOS fragt GitHub nur, wenn du auf „Nach Updates suchen“ tippst. GitHub sieht dabei deine IP-Adresse und die installierte Version – sonst nichts.</p>
        <div id="ghBox" class="list" style="margin-bottom:12px"><div class="li"><div class="grow small muted">Noch nicht gesucht.</div>
          <button class="btn sm primary" data-act="ghCheck">${HO.icon('refresh', 'sm')} Nach Updates suchen</button></div></div>
        <p class="tiny muted">Alternativ eine <b>comfyos-&lt;version&gt;.zip</b> neben den ComfyOS-Ordner (<code style="overflow-wrap:anywhere">${HO.esc(up.folder)}</code>) legen oder hier hochladen.</p>
        <div class="list">${up.candidates.length ? up.candidates.map(c => `<div class="li"><div class="grow"><b>${HO.esc(c.file)}</b>
            <div class="small muted">${c.ok ? 'Version ' + HO.esc(c.version) + (c.newer ? ' · neuer' : ' · nicht neuer als installiert') : HO.esc(c.error)}</div></div>
            ${c.ok ? `<button class="btn sm ${c.newer ? 'primary' : ''}" data-install="${HO.esc(c.file)}" data-version="${HO.esc(c.version)}" data-newer="${c.newer ? 1 : 0}">Installieren</button>` : ''}</div>`).join('')
          : '<div class="li muted small">Kein Update-Paket gefunden.</div>'}</div>
        <div class="form-actions"><label class="btn">${HO.icon('upload', 'sm')} ZIP hochladen<input type="file" accept=".zip,application/zip" id="upFile" hidden></label>
          <button class="btn ghost" data-act="restart">${HO.icon('refresh', 'sm')} Neu starten</button></div>
        ${up.backups.length ? `<p class="tiny muted" style="margin-top:12px">Backups: ${up.backups.map(b => HO.esc(b.file)).join(', ')}</p>` : ''}
      </section>
      ${demoCardHTML()}</div>
      <div class="row diag-link" style="justify-content:center;margin-top:18px"><a class="btn sm ghost" href="#diag" style="text-decoration:none">${HO.icon('gauge', 'sm')} Diagnose dieses Geräts</a></div>`
    mountGrid(page, s, 'system')
    if (srv.job_running) pollSrv(page)
    loadSysInfo(page)
    loadSessions(page)
    loadEgress(page)
    loadPush(page)
    loadBackups(page)
  }

  const fmtWhen = ts => ts ? new Date(ts * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–'

  // Load the list of the signed-in devices (with "Abmelden" per device).
  async function loadSessions(page) {
    const el = page.querySelector('#sessList')
    if (!el) return
    try {
      const r = await HO.api('auth/sessions')
      el.innerHTML = r.sessions.map(x => `<div class="li"><div class="grow"><b>${HO.esc(x.device)}</b>${x.current ? ' <span class="badge ok">dieses Gerät</span>' : ''}
          <div class="small muted">${x.ip ? HO.esc(x.ip) + ' · ' : ''}zuletzt ${HO.esc(fmtWhen(x.last_seen))}</div></div>
          ${x.current ? '' : `<button class="btn sm ghost danger" data-sess-del="${HO.esc(x.id)}">Abmelden</button>`}</div>`).join('') ||
        '<div class="li small muted">Keine Sitzungen.</div>'
      const badge = page.querySelector('#sessBadge')
      if (badge) badge.textContent = r.sessions.length || ''
    } catch (e) { el.innerHTML = '<div class="li small muted">Nicht verfügbar.</div>' }
  }

  /* ---------- Notifications: Web Push for the flow node "Benachrichtigung" */
  const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && window.isSecureContext
  // Convert URL-safe Base64 into bytes (key for Web Push).
  function b64ToBytes(s) {
    const raw = atob((s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'))
    const out = new Uint8Array(raw.length)
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
    return out
  }
  // Short name of this device for the list of notifications, e.g. "iPhone (Safari)".
  function deviceLabel() {
    const ua = navigator.userAgent || ''
    const dev = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows-PC' : /Linux/.test(ua) ? 'Linux' : 'Gerät'
    const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : ''
    return br ? `${dev} (${br})` : dev
  }
  const askPermission = () => new Promise(res => {
    const p = Notification.requestPermission(res)      // older Safari versions only know the callback
    if (p && p.then) p.then(res)
  })
  // Tile "Benachrichtigungen": state on this device, switching on and off, test message, list of all devices.
  async function loadPush(page) {
    const el = page.querySelector('#pushCard')
    if (!el) return
    const head = el.querySelector('.card-head').outerHTML
    let st = null, mine = null
    // Load the state of the notifications and draw the tile – with the reason if it does not work here.
    async function draw() {
      try { st = await HO.api('push/status') } catch (e) { el.innerHTML = head + '<p class="small muted">Nicht verfügbar.</p>'; return }
      mine = null
      if (pushSupported()) {
        try { const reg = await navigator.serviceWorker.getRegistration('/'); mine = reg ? await reg.pushManager.getSubscription() : null } catch (e) { }
      }
      const why = !st.available ? 'Auf dieser Installation fehlt noch das Paket für Push (pywebpush). Es wird beim Update bzw. beim nächsten Start von ComfyOS nachinstalliert.'
        : !window.isSecureContext ? 'Push geht nur über eine sichere Verbindung. Öffne ComfyOS über die https-Adresse (siehe Erreichbarkeit).'
        : !pushSupported() ? 'Dieser Browser kann hier keine Push-Nachrichten empfangen. Auf iPhone und iPad: ComfyOS über „Teilen → Zum Home-Bildschirm“ installieren und von dort öffnen.' : ''
      const subs = st.subscriptions || []
      el.innerHTML = head + `<p class="small muted">Flows können mit dem Baustein „Benachrichtigung“ eine Push-Nachricht schicken – auch wenn ComfyOS gerade nicht geöffnet ist. Jedes Gerät schaltet das hier einmal für sich ein.</p>
        <p class="tiny muted">Die Nachricht läuft über den Push-Dienst deines Browser-Herstellers (Apple, Google, Mozilla oder Microsoft). Sie ist Ende-zu-Ende-verschlüsselt: der Dienst sieht nur die Push-Adresse dieses Geräts, nicht den Text.</p>
        ${why ? `<div class="notice">${HO.icon('info', 'sm')}<div>${HO.esc(why)}</div></div>`
          : `<label class="check" style="margin:12px 0"><input type="checkbox" id="pushOn" ${mine ? 'checked' : ''}><span>Benachrichtigungen auf diesem Gerät</span></label>`}
        ${subs.length ? `<div class="list">${subs.map(x => `<div class="li"><div class="grow"><b>${HO.esc(x.label || 'Gerät')}</b>
          <div class="small muted">${HO.esc(x.service || '')}${x.last_ok ? ' · zuletzt zugestellt ' + HO.esc(fmtWhen(x.last_ok)) : ''}</div></div>
          <button class="btn sm ghost danger" data-push-del="${HO.esc(x.id)}">Entfernen</button></div>`).join('')}</div>
          <div class="form-actions"><button class="btn sm" data-push-test>${HO.icon('bell', 'sm')} Test senden</button></div>` : ''}`
    }
    // Switch notifications on for this device: ask for permission, register the service worker, store the subscription
    // with the server.
    async function enable() {
      if (await askPermission() !== 'granted') throw new Error('Benachrichtigungen sind für diese Seite im Browser blockiert.')
      const reg = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      const want = { userVisibleOnly: true, applicationServerKey: b64ToBytes(st.public_key) }
      let sub
      try { sub = await reg.pushManager.subscribe(want) } catch (e) {
        const old = await reg.pushManager.getSubscription()     // subscription for an old key: renew
        if (!old) throw e
        await old.unsubscribe()
        sub = await reg.pushManager.subscribe(want)
      }
      await HO.api('push/subscribe', { method: 'POST', body: { subscription: sub.toJSON(), label: deviceLabel() } })
    }
    el.addEventListener('change', async e => {
      if (e.target.id !== 'pushOn') return
      e.target.disabled = true
      try {
        if (e.target.checked) { await enable(); HO.toast('Benachrichtigungen auf diesem Gerät eingeschaltet') }
        else if (mine) {
          const endpoint = mine.endpoint
          await mine.unsubscribe()
          await HO.api('push/unsubscribe', { method: 'POST', body: { endpoint } })
          HO.toast('Benachrichtigungen auf diesem Gerät ausgeschaltet')
        }
      } catch (err) { HO.toast(err.message || 'Benachrichtigungen konnten nicht eingeschaltet werden.', 'bad', 6000) }
      draw(); loadEgress(page)
    })
    el.addEventListener('click', async e => {
      const del = e.target.closest('[data-push-del]'), test = e.target.closest('[data-push-test]')
      if (!del && !test) return
      e.stopPropagation()
      try {
        if (del) { await HO.api('push/unsubscribe', { method: 'POST', body: { id: del.dataset.pushDel } }); HO.toast('Gerät entfernt'); draw(); loadEgress(page) }
        else {
          test.disabled = true
          const r = await HO.api('push/test', { method: 'POST' })
          HO.toast(r.sent ? `Test an ${r.sent} Gerät${r.sent === 1 ? '' : 'e'} gesendet` : 'Der Test konnte nicht zugestellt werden.', r.sent ? 'ok' : 'bad', 5000)
          draw()
        }
      } catch (err) { HO.toast(err.message, 'bad', 5000); draw() }
    })
    draw()
  }

  const EGRESS_DOT = { on: 'ok' }     // state of a connection -> colour of its dot: green = active, grey = everything else
  // Tile "Externe Verbindungen" (short since 0.23.1): per target a dot for the state (green = the service is active,
  // grey = off or only on demand – since 0.24 no yellow any more, nothing is wrong there; the word is in the tooltip
  // and for screen readers), a short title, the purpose in one line and a compact line with what is sent and when.
  // The hosts are in the tooltip of the entry.
  async function loadEgress(page) {
    const el = page.querySelector('#egressCard')
    if (!el) return
    try {
      const r = await HO.api('system/egress')
      el.innerHTML = el.querySelector('.card-head').outerHTML +
        `<p class="small muted">Keine Telemetrie. Nur diese Verbindungen verlassen dein Heimnetz:</p>
        <div class="list egress-list">${r.connections.map(c => `<div class="li egress"${c.hosts.length ? ` title="Ziel: ${HO.esc(c.hosts.join(', '))}"` : ''}>
          <span class="statusdot ${EGRESS_DOT[c.state] || ''}" role="img" aria-label="${HO.esc(c.state_text)}" title="${HO.esc(c.state_text)}"></span>
          <div class="grow"><b>${HO.esc(c.title)}</b><div class="small muted">${HO.esc(c.purpose)}</div>
          <div class="tiny muted">Sendet ${HO.esc(c.data)} · ${HO.esc(c.trigger)}${c.last_used ? ' · zuletzt ' + HO.esc(fmtWhen(c.last_used)) : ''}</div></div></div>`).join('')}</div>`
    } catch (e) { el.querySelector('p').textContent = 'Nicht verfügbar.' }
  }

  // Bytes as a readable size (B, KB, MB, GB).
  function fmtB(b) { if (!b) return '0 B'; if (b < 1024) return b + ' B'; if (b < 1048576) return (b / 1024).toFixed(0) + ' KB'; if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'; return (b / 1073741824).toFixed(1) + ' GB' }
  // Share in whole per cent.
  function pct(used, total) { return total > 0 ? Math.round(used / total * 100) : 0 }
  // Bar for a load; yellow from 75 %, red from 90 %. own = share of ComfyOS in it in per cent of the whole:
  // it lies hatched at the start of the bar (never wider than the load itself).
  function bar(p, own) {
    const cls = p > 90 ? 'danger' : p > 75 ? 'warn' : '', full = Math.min(100, p)
    return `<div class="sys-bar"><div class="sys-fill ${cls}" style="width:${full}%"></div><div class="sys-own" style="width:${own == null ? 0 : Math.max(0, Math.min(full, own))}%"></div></div>`
  }

  // Tile "Systemauslastung": load, memory, disk space, temperature and uptime – with warnings on
  // bottlenecks.
  async function loadSysInfo(page) {
    const el = page.querySelector('#sysInfo')
    if (!el) return
    try {
      const s = await HO.api('system/info')
      const alerts = []
      let body = '<dl class="kv" style="margin:0">'
      body += `<dt>Plattform</dt><dd>${HO.esc(s.platform)} ${HO.esc(s.machine)}</dd>`
      if (s.uptime != null) {
        const h = Math.floor(s.uptime / 3600), m = Math.floor((s.uptime % 3600) / 60)
        body += `<dt>Laufzeit</dt><dd>${h > 24 ? Math.floor(h / 24) + ' T ' + (h % 24) + ' Std' : h + ' Std ' + m + ' Min'}</dd>`
      }
      if (s.cpu_temp != null) {
        body += `<dt>CPU-Temperatur</dt><dd>${s.cpu_temp.toFixed(1)} °C</dd>`
        if (s.cpu_temp > 80) alerts.push('CPU-Temperatur kritisch hoch')
      }
      body += '</dl>'
      // share of ComfyOS (own): CPU time and memory of the own process, space for data, logs, backups
      const own = s.own || {}
      if (s.load != null) {
        const lp = Math.round(s.load[0] / s.cpu_count * 100)
        body += `<div class="sys-row"><span class="small">CPU</span><span class="small muted">${s.load[0].toFixed(2)} / ${s.cpu_count} Kerne (${lp} %)</span></div>${bar(lp, own.cpu)}`
        if (lp > 90) alerts.push('CPU-Auslastung über 90 %')
      }
      if (s.ram) {
        const rp = pct(s.ram.used, s.ram.total)
        body += `<div class="sys-row"><span class="small">RAM</span><span class="small muted">${fmtB(s.ram.used)} / ${fmtB(s.ram.total)} (${rp} %)</span></div>${bar(rp, own.ram == null ? null : own.ram / s.ram.total * 100)}`
        if (rp > 90) alerts.push('Speicher fast voll')
      }
      if (s.disk) {
        const dp = pct(s.disk.used, s.disk.total)
        body += `<div class="sys-row"><span class="small">Speicherplatz</span><span class="small muted">${fmtB(s.disk.used)} / ${fmtB(s.disk.total)} (${dp} %)</span></div>${bar(dp, own.disk == null ? null : own.disk / s.disk.total * 100)}`
        if (dp > 90) alerts.push('Festplatte fast voll')
      }
      if (own.cpu != null || own.ram != null || own.disk != null) {
        body += `<p class="tiny muted sys-legend"><span class="sys-key"></span>Schraffiert: der Anteil von ComfyOS – Rechenzeit und Arbeitsspeicher des Dienstes, beim Speicherplatz seine Daten, Logs und Backups.</p>`
      }
      if (alerts.length) body += `<div class="notice warn" style="margin-top:12px">${HO.icon('warn', 'sm')}<div>${alerts.join('. ')}.</div></div>`
      // only adjust the bars if the structure stays the same – that way they glide on polling (CSS transition) instead of jumping
      const fresh = document.createElement('div')
      fresh.innerHTML = body
      const oldFills = el.querySelectorAll('.sys-fill'), newFills = fresh.querySelectorAll('.sys-fill')
      const oldOwn = el.querySelectorAll('.sys-own'), newOwn = fresh.querySelectorAll('.sys-own')
      const sameShape = el.querySelectorAll('.sys-row').length === fresh.querySelectorAll('.sys-row').length && oldFills.length === newFills.length && oldFills.length
        && oldOwn.length === newOwn.length && !el.querySelector('.sys-legend') === !fresh.querySelector('.sys-legend')
      if (sameShape && !el.querySelector('.notice') === !fresh.querySelector('.notice')) {
        el.querySelector('dl.kv').outerHTML = fresh.querySelector('dl.kv').outerHTML
        el.querySelectorAll('.sys-row').forEach((row, i) => { row.innerHTML = fresh.querySelectorAll('.sys-row')[i].innerHTML })
        oldFills.forEach((fill, i) => { fill.className = newFills[i].className; fill.style.width = newFills[i].style.width })
        oldOwn.forEach((part, i) => { part.style.width = newOwn[i].style.width })
        const notice = el.querySelector('.notice')
        if (notice) notice.outerHTML = fresh.querySelector('.notice').outerHTML
      } else el.innerHTML = el.querySelector('.card-head').outerHTML + body
    } catch (e) {
      const p = el.querySelector('p')
      if (p) p.textContent = 'Nicht verfügbar.'   // while polling the last values stay
    }
  }
  // system load live: poll every SYS_POLL_MS as long as the page is open and visible
  const SYS_POLL_MS = 5000
  let sysTimer = null
  // Refresh the system load regularly as long as the page is open and visible.
  function pollSysInfo(page) {
    clearInterval(sysTimer)
    sysTimer = setInterval(() => {
      if (!page.isConnected) { clearInterval(sysTimer); return }
      if (!document.hidden) loadSysInfo(page)
    }, SYS_POLL_MS)
  }

  // Load the list of the backups (download, delete).
  async function loadBackups(page) {
    const el = page.querySelector('#backupList')
    if (!el) return
    try {
      const r = await HO.api('backups')
      el.innerHTML = r.backups.length ? r.backups.map(b => `<div class="li"><div class="grow"><b>${HO.esc(b.file)}</b>
        <div class="small muted">${fmtWhen(b.created)} · ${fmtB(b.size)}</div></div>
        <a class="btn sm" href="/api/backups/${encodeURIComponent(b.file)}" download>${HO.icon('download', 'sm')}</a>
        <button class="btn sm ghost danger" data-act="backupDel" data-file="${HO.esc(b.file)}">Löschen</button></div>`).join('')
        : '<div class="li small muted">Noch keine Backups.</div>'
    } catch (e) { el.innerHTML = '<div class="li small muted">Nicht verfügbar (Pro-Funktion).</div>' }
  }

  /* Live: if a device becomes reachable or unreachable (or the device list changes), redraw the lists in the
     plugin tiles – otherwise "Nicht erreichbar" stayed until the page was reloaded. Never in the middle of an
     input in a tile (then try once more later). */
  let liveOff = null, liveTimer = null
  // Keep the tab "Plugins" up to date: reload on changed devices – but not while someone is typing into a field.
  function watchDevices(page) {
    if (liveOff) return
    // Reload the plugin tiles; if an input field is active right now, try once more later.
    const redraw = () => {
      const focus = document.activeElement
      if (!page.isConnected) return
      if (focus && focus.closest && focus.closest('[data-plugin]') && focus.matches('input, select, textarea')) { liveTimer = setTimeout(redraw, 3000); return }
      HO.plugins.reload(page)
    }
    liveOff = HO.live.on(m => {
      if (m.type !== 'reload' && !(m.type === 'device' && 'online' in m)) return
      clearTimeout(liveTimer)
      liveTimer = setTimeout(redraw, 600)   // mehrere Meldungen auf einmal = einmal zeichnen
    })
  }

  let grid = null     // Kachel-Raster des offenen Reiters (HO.tileGrid)

  HO.pages.settings = {
    title: 'System',
    // Open the System page: draw and set up all controls (forms, buttons, uploads, tab change).
    async render(page) {
      page.innerHTML = '<div class="card"><div class="spinner"></div></div>'
      await render(page)
      watchDevices(page)
      pollSysInfo(page)
      const rerender = () => render(page)
      page.onsubmit = async e => {
        e.preventDefault()
        // forms of the plugin tiles belong to the plugins (plugins.js or their settings.js) – do nothing here,
        // otherwise the whole page would be redrawn and an input that was begun would be lost
        if (e.target.closest('[data-plugin], #pluginInfo')) return
        const f = e.target, btn = f.querySelector('[type=submit]')
        btn.disabled = true
        try {
          if (f.id === 'pwForm') {
            const r = await HO.api('auth/password', { method: 'POST', body: { old: f.querySelector('#pwOld').value, new: f.querySelector('#pwNew').value } })
            HO.toast(r.revoked ? `Passwort geändert – ${r.revoked} andere Geräte abgemeldet` : 'Passwort geändert')
          } else if (f.id === 'hostsForm') {
            const hosts = f.querySelector('#extraHosts').value.split(/[,\s]+/).filter(Boolean)
            const r = await HO.api('security/hosts', { method: 'PUT', body: { hosts } })
            HO.toast(r.allowed_hosts.length ? 'Gespeichert: ' + r.allowed_hosts.join(', ') : 'Keine weiteren Adressen')
          } else if (f.id === 'licForm') {
            const text = f.querySelector('#licText').value.trim()
            if (!text) { HO.toast('Bitte einen Lizenzschlüssel oder den Inhalt einer Lizenzdatei einfügen', 'warn'); return }
            const r = await HO.api('license', { method: 'POST', body: { license: text } })
            HO.toast(`Lizenz eingelöst – Plan: ${r.licensed_title}`, 'ok', 5000)
            await HO.refreshLicense()
          } else if (f.id === 'signForm') {
            issuerOpen = true
            await HO.api('license/issuer/key', { method: 'PUT', body: { key: f.querySelector('#signKey').value.trim(), password: f.querySelector('#signPw').value } })
            HO.toast('Signaturschlüssel hinterlegt')
          } else if (f.id === 'issueForm') {
            issuerOpen = true
            const days = f.querySelector('#issDays').value
            const r = await HO.api('license/issue', { method: 'POST', body: { plan: f.querySelector('#issPlan').value, name: f.querySelector('#issName').value,
              instance_id: f.querySelector('#issIid').value.trim() || null, days: days ? Number(days) : null, password: f.querySelector('#issPw').value } })
            await rerender()
            issuedSheet(r)
            return
          }
          rerender()
        } catch (err) { HO.toast(err.message, 'bad', 5000) } finally { btn.disabled = false }
      }
      page.onchange = async e => {
        const t = e.target
        try {
          if (t.dataset.map) {
            const r = await HO.api('home/mapping', { method: 'PUT', body: { [t.dataset.map]: t.value || null } })
            HO.syncToast(r.sync, 'Zuordnung gespeichert')
          } else if (t.id === 'httpsOn') {
            await switchHttps(t.checked, false)
          } else if (t.id === 'upFile' && t.files[0]) {
            HO.toast('Lade hoch …', 'warn', 2000)
            const info = await HO.api('update/upload', { method: 'POST', body: t.files[0], raw: 'application/zip' })
            HO.toast(`Paket ${info.version} bereit`); rerender()
          } else if (t.id === 'licFile' && t.files[0]) {
            if (t.files[0].size > 8192) { t.value = ''; throw new Error('Das ist keine ComfyOS-Lizenzdatei.') }
            page.querySelector('#licText').value = (await t.files[0].text()).trim()
            t.value = ''
            HO.toast('Lizenzdatei geladen – jetzt „Einlösen“ tippen')
          } else if (t.id === 'accentCustom') {
            const r = await HO.api('settings/accent', { method: 'PUT', body: { color: t.value } }); HO.applyAccent(r.accent_color || ''); rerender()
          } else if (t.id === 'migrateImport' && t.files[0]) {
            const pw = await HO.prompt('Passwort des Umzugspakets', { label: 'Passwort', value: '' })
            if (!pw) { t.value = ''; return }
            if (!await HO.confirm('Umzugspaket einspielen? Einstellungen und Daten werden überschrieben. ComfyOS startet danach neu.', { ok: 'Einspielen', danger: true })) { t.value = ''; return }
            HO.toast('Wird eingespielt …', 'warn', 3000)
            const r = await HO.api('backups/migrate/import', { method: 'POST', body: t.files[0], raw: 'application/zip', headers: { 'X-Migration-Password': encodeURIComponent(pw) } })
            HO.toast(`Umzugspaket eingespielt: ${(r.restored || []).join(', ')}`, 'ok', 5000)
            t.value = ''; await HO.api('restart', { method: 'POST' }); waitForRestart(null)
          } else if (t.id === 'restoreFile' && t.files[0]) {
            if (!await HO.confirm('Backup wiederherstellen? Grundriss, Routinen, Flows und Einstellungen werden überschrieben. ComfyOS startet danach neu.', { ok: 'Wiederherstellen', danger: true })) { t.value = ''; return }
            HO.toast('Wird wiederhergestellt …', 'warn', 3000)
            const r = await HO.api('backups/restore', { method: 'POST', body: t.files[0], raw: 'application/zip' })
            HO.toast(`Wiederhergestellt: ${r.restored.join(', ')}`, 'ok', 5000)
            await HO.api('restart', { method: 'POST' }); waitForRestart(null)
          }
        } catch (err) { HO.toast(err.message, 'bad', 5000) }
      }
      page.onclick = async e => {
        const ab = e.target.closest('[data-accent]')
        if (ab) { try { const r = await HO.api('settings/accent', { method: 'PUT', body: { color: ab.dataset.accent } }); HO.applyAccent(r.accent_color || ''); rerender() } catch (err) { HO.toast(err.message, 'bad', 5000) } return }
        const t = e.target.closest('[data-act],[data-cols],[data-systab],[data-copy],[data-install],[data-remove],[data-restore],[data-sess-del],[data-preview]')
        if (!t) return
        if (t.dataset.systab) { if (t.dataset.systab !== sysTab()) location.hash = t.dataset.systab === 'plugins' ? 'settings?tab=plugins' : 'settings'; return }
        if (t.dataset.cols) { HO.cols.choose(sysTab(), +t.dataset.cols); if (grid) grid.relayout(); return }
        if (t.dataset.act === 'arrange') { if (grid) grid.arrange(!grid.arranging); return }
        if (t.dataset.act === 'arrangeAuto') { if (grid) grid.reset(); return }
        if (t.dataset.act === 'demoStart') { await HO.startDemo(); return }
        const reloadLists = () => HO.plugins.reload(page)
        try {
          if (t.dataset.preview) {
            const plan = t.dataset.preview
            if (plan === 'owner') await HO.api('license/preview', { method: 'DELETE' })
            else {
              const r = await HO.api('license/preview', { method: 'POST', body: { plan, minutes: Number(page.querySelector('#pvMinutes').value) || 60 } })
              HO.toast(`Testansicht ${PLAN_TITLE[plan]}${r.paused_flows ? ` – ${r.paused_flows} ${r.paused_flows === 1 ? 'Flow pausiert' : 'Flows pausieren'} so lange` : ''}`, 'warn', 5000)
            }
            await HO.refreshLicense(); return rerender()
          }
          if (t.dataset.act === 'licCheck') { const r = await HO.api('license/check', { method: 'POST' }); HO.toast(`Geprüft – Plan: ${r.licensed_title}`); await HO.refreshLicense(); return rerender() }
          if (t.dataset.act === 'licRemove') {
            if (!await HO.confirm('Lizenzdatei und gekauften Schlüssel von diesem Gerät entfernen? Eine Owner-Lizenz bleibt bestehen. Deine Daten bleiben erhalten.', { title: 'Lizenz entfernen', ok: 'Entfernen', danger: true })) return
            await HO.api('license', { method: 'DELETE' }); HO.toast('Lizenz entfernt'); await HO.refreshLicense(); return rerender()
          }
          if (t.dataset.act === 'signRemove') {
            if (!await HO.confirm('Signaturschlüssel von diesem Gerät entfernen? Bereits ausgestellte Lizenzen bleiben gültig; neue kannst du dann nur noch mit tools/mint_gift.py oder nach erneutem Hinterlegen ausstellen.', { title: 'Signaturschlüssel entfernen', ok: 'Entfernen', danger: true })) return
            issuerOpen = true
            await HO.api('license/issuer/key', { method: 'DELETE' }); HO.toast('Signaturschlüssel entfernt'); return rerender()
          }
          if (t.dataset.act === 'iidCopy') { HO.toast(await copyText(t.dataset.value) ? 'Geräte-Kennung kopiert' : 'Kopieren nicht möglich'); return }
          if (t.dataset.sessDel) {
            await HO.api(`auth/sessions/${encodeURIComponent(t.dataset.sessDel)}`, { method: 'DELETE' })
            HO.toast('Gerät abgemeldet'); return loadSessions(page)
          }
          if (t.dataset.remove) {
            const imported = t.dataset.imported === '1'
            if (!await HO.confirm(imported ? `„${t.dataset.name}" aus ComfyOS entfernen? Das Gerät bleibt bei seiner Quelle und steht dort wieder unter „Neu".`
              : `„${t.dataset.name}" aus ComfyOS entfernen? Bei seiner Quelle (Home Base, Bridge …) bleibt das Gerät unverändert; unter „Entfernt" lässt es sich wiederherstellen.`,
            { title: 'Aus ComfyOS entfernen', ok: 'Entfernen', danger: true })) return
            await HO.api('devices/remove', { method: 'POST', body: { device_id: t.dataset.remove, removed: true } })
            HO.toast(`${t.dataset.name} entfernt`); HO.state.overview = null; return reloadLists()
          }
          if (t.dataset.restore) {
            await HO.api('devices/remove', { method: 'POST', body: { device_id: t.dataset.restore, removed: false } })
            HO.toast('Wiederhergestellt'); HO.state.overview = null; return reloadLists()
          }
          if (t.dataset.copy) { HO.toast(await HO.copy(t.dataset.copy) ? 'Adresse kopiert' : 'Kopieren nicht möglich', 'ok'); return }
          if (t.dataset.act === 'httpsRegen') { if (await HO.confirm('Alle Geräte müssen das neue Zertifikat danach einmal bestätigen.', { title: 'Zertifikat neu erstellen?', ok: 'Neu erstellen' })) await switchHttps(true, true); return }
          if (t.dataset.install) {
            const newer = t.dataset.newer === '1'
            if (!await HO.confirm(newer ? `Version ${t.dataset.version} installieren? ComfyOS startet danach kurz neu.`
              : `Version ${t.dataset.version} ist nicht neuer als die installierte. Trotzdem installieren?`, { ok: 'Installieren', danger: !newer })) return
            const r = await HO.api('update/apply', { method: 'POST', body: { file: t.dataset.install, force: !newer } })
            waitForRestart(r.to); return
          }
          const act = t.dataset.act
          if (act === 'ghCheck') {
            const box = page.querySelector('#ghBox')
            box.innerHTML = '<div class="li small muted">Frage GitHub ab …</div>'
            try {
              const g = await HO.api('update/github')
              const when = g.published ? new Date(g.published).toLocaleDateString('de-DE') : ''
              box.innerHTML = `<div class="li"><div class="grow"><b>${HO.esc(g.name || g.tag)}</b>
                <div class="small muted">Version ${HO.esc(g.version)}${when ? ' · ' + HO.esc(when) : ''} · ${g.newer ? 'neuer als installiert' : 'aktuell installiert ist ' + HO.esc(g.current)}${g.asset ? '' : ' · kein Paket im Release'}</div></div>
                ${g.asset ? `<button class="btn sm ${g.newer ? 'primary' : ''}" data-act="ghInstall" data-tag="${HO.esc(g.tag)}" data-version="${HO.esc(g.version)}" data-newer="${g.newer ? 1 : 0}">Installieren</button>` : ''}
                <button class="btn sm ghost" data-act="ghCheck" title="Erneut suchen">${HO.icon('refresh', 'sm')}</button></div>
                ${g.notes ? `<div class="li"><div class="grow small muted" style="white-space:pre-wrap;max-height:180px;overflow:auto">${HO.esc(g.notes)}</div></div>` : ''}`
            } catch (err) {
              box.innerHTML = `<div class="li"><div class="grow small muted">${HO.esc(err.message)}</div><button class="btn sm" data-act="ghCheck">Erneut</button></div>`
            }
            return
          }
          if (act === 'ghInstall') {
            const newer = t.dataset.newer === '1'
            if (!await HO.confirm(newer ? `Version ${t.dataset.version} von GitHub laden und installieren? ComfyOS startet danach kurz neu.`
              : `Version ${t.dataset.version} ist nicht neuer als die installierte. Trotzdem installieren?`, { ok: 'Installieren', danger: !newer })) return
            t.disabled = true; HO.toast('Lade Release …', 'warn', 4000)
            const r = await HO.api('update/github', { method: 'POST', body: { tag: t.dataset.tag, force: !newer } })
            waitForRestart(r.to); return
          }
          if (act === 'logoutall') { const r = await HO.api('auth/logout-all', { method: 'POST' }); HO.toast(`${r.revoked} Geräte abgemeldet`); rerender() }
          else if (act === 'logout') { await HO.api('auth/logout', { method: 'POST' }); location.reload() }
          else if (act === 'srvInstall' || act === 'srvRemove') {
            const name = (page.querySelector('#srvName') || {}).value || 'comfyos'
            const msg = act === 'srvInstall' ? `ComfyOS als Dienst einrichten und Netzwerkname „${name}" setzen? Dauert 1–3 Minuten.`
              : 'Dienst entfernen und alten Netzwerknamen wiederherstellen? ComfyOS startet danach nicht mehr automatisch.'
            if (!await HO.confirm(msg, { ok: act === 'srvInstall' ? 'Einrichten' : 'Entfernen', danger: act === 'srvRemove' })) return
            const v = await HO.api('serversetup', { method: 'POST', body: { action: act === 'srvInstall' ? 'install' : 'uninstall', hostname: name } })
            page.querySelector('#srvCard').innerHTML = srvHTML(v); pollSrv(page)
          }
          else if (act === 'restart') { if (await HO.confirm('ComfyOS jetzt neu starten?', { ok: 'Neu starten' })) { await HO.api('restart', { method: 'POST' }); waitForRestart(null) } }
          else if (act === 'backupCreate') { HO.toast('Backup wird erstellt …', 'warn', 2000); await HO.api('backups', { method: 'POST' }); HO.toast('Backup erstellt', 'ok'); loadBackups(page) }
          else if (act === 'backupDel') { if (await HO.confirm(`Backup „${t.dataset.file}" löschen?`, { ok: 'Löschen', danger: true })) { await HO.api(`backups/${encodeURIComponent(t.dataset.file)}`, { method: 'DELETE' }); HO.toast('Gelöscht'); loadBackups(page) } }
          else if (act === 'exportLogs') { location.href = '/api/logs/export' }
          else if (act === 'migrateExport') {
            const pw = await HO.prompt('Passwort für das Umzugspaket', { label: 'Passwort (zum Entschlüsseln auf der Zielinstallation)', value: '' })
            if (!pw) return
            const r = await fetch('/api/backups/migrate/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
            if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || 'Export fehlgeschlagen') }
            const blob = await r.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'comfyos-migration.zip'; a.click(); URL.revokeObjectURL(a.href)
          }
        } catch (err) { HO.toast(err.message, 'bad', 5000) }
      }
    },
    // Leave the page: tear down timers, live subscription and tile grid.
    destroy() {
      clearTimeout(srvTimer); clearTimeout(liveTimer); clearInterval(sysTimer); if (liveOff) { liveOff(); liveOff = null }
      if (grid) grid.destroy()
      grid = null; HO.plugins.onChange = null
    }
  }
})(window.HO)
