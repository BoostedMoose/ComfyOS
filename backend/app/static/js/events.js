/* ComfyOS – Ereignisse & Logs */
(function (HO) {
  'use strict'
  const TYPES = { heating: ['flame', 'Heizung'], window: ['contact', 'Fenster'], device: ['device', 'Geräte'], battery: ['battery', 'Batterie'],
    command: ['power', 'Befehle'], home: ['home', 'Zuhause'], magenta: ['cloud', 'Magenta-Sync'], connection: ['globe', 'Verbindung'],
    update: ['download', 'Update'], system: ['settings', 'System'], room: ['map', 'Räume'], hue: ['light', 'Hue'], flow: ['workflow', 'Flow'],
    security: ['shield', 'Sicherheit'], scene: ['palette', 'Szenen'], license: ['tag', 'Lizenz'] }
  const SHORT = 10   // so viele Ereignisse stehen sofort da; der Rest klappt auf
  let filter = 'all', expanded = false

  // Heading of a day: "Heute", "Gestern" or the weekday with the date.
  function dayLabel(ts) {
    const d = new Date(ts), t = new Date(), y = new Date(Date.now() - 86400000)
    if (d.toDateString() === t.toDateString()) return 'Heute'
    if (d.toDateString() === y.toDateString()) return 'Gestern'
    return d.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' })
  }

  // Load and draw events and size of the logs: filter, list by days, per log export and clearing.
  async function render(page) {
    const [ev, es, hs, ps, ms] = await Promise.all([HO.api('events?limit=400'), HO.api('logs/events/stats'),
      HO.api('logs/heating/stats'), HO.api('logs/power/stats'), HO.api('logs/messages/stats')])
    const present = [...new Set(ev.map(e => e.type))].filter(t => TYPES[t])
    const all = ev.filter(e => filter === 'all' || e.type === filter), list = expanded ? all : all.slice(0, SHORT)
    let html = '', last = ''
    for (const e of list) {
      const dl = dayLabel(e.ts)
      if (dl !== last) { html += `<div class="ev-day">${dl}</div>`; last = dl }
      const [ic] = TYPES[e.type] || ['info']
      html += `<div class="ev"><div class="dev-ic">${HO.icon(ic, 'sm')}</div><div><div class="ev-msg">${HO.esc(e.message)}</div>
        <div class="ev-meta">${new Date(e.ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })} · ${HO.esc((TYPES[e.type] || [0, e.type])[1])}</div></div></div>`
    }
    const card = (cat, title, st, unit) => `<div class="stat"><span>${title}</span><b>${st.entries}</b><span>${unit} · ${(st.bytes / 1024).toLocaleString('de-DE', { maximumFractionDigits: 1 })} KB</span>
      <div class="row" style="margin-top:10px"><a class="btn sm" href="/api/logs/${cat}/export" download>${HO.icon('download', 'sm')}</a>
      <button class="btn sm danger" data-clear="${cat}">${HO.icon('trash', 'sm')}</button></div></div>`
    const logs = `<section class="card"><div class="card-head"><div><h3>Logs</h3><div class="small muted">Automatische Aufbewahrung: 13 Monate (Jahresabdeckung) · JSONL pro Monat · Export je Kategorie</div></div></div>
      <div class="stats" style="margin-top:0">${card('events', 'Ereignisse', es, 'Einträge')}${card('heating', 'Heizverlauf', hs, 'Messpunkte')}${card('power', 'Stromverbrauch', ps, 'Messpunkte')}${card('messages', 'Meldungen', ms, 'Einträge')}</div></section>`
    page.innerHTML = `<section class="section"><div class="chips scroll" style="margin-bottom:6px"><span class="chip ${filter === 'all' ? 'active' : ''}" data-f="all">Alle</span>
        ${present.map(t => `<span class="chip ${filter === t ? 'active' : ''}" data-f="${t}">${HO.icon(TYPES[t][0], 'sm')}${TYPES[t][1]}</span>`).join('')}</div>
        <div class="card" style="padding-top:4px">${html || `<div class="empty">${HO.icon('activity')}<div>Noch keine Ereignisse.</div></div>`}
          ${all.length > SHORT ? `<button class="ev-more" data-more aria-expanded="${expanded}">${HO.icon('chevron', 'sm')}
            ${expanded ? 'Nur die letzten ' + SHORT + ' anzeigen' : 'Alle ' + all.length + ' anzeigen'}</button>` : ''}</div></section>
      ${logs}`
  }

  HO.pages.events = {
    title: 'Ereignisse',
    // Open the page "Ereignisse" and set up the controls (filter, show more, clear logs).
    async render(page) {
      page.innerHTML = '<div class="card"><div class="spinner"></div></div>'
      expanded = false
      await render(page)
      page.onclick = async e => {
        const t = e.target.closest('[data-f],[data-clear],[data-more]')
        if (!t) return
        if (t.dataset.f) { filter = t.dataset.f; expanded = false; return render(page) }
        if (t.hasAttribute('data-more')) {
          expanded = !expanded
          await render(page)
          if (!expanded) { const top = page.querySelector('.section'); if (top && top.scrollIntoView) top.scrollIntoView({ block: 'start' }) }
          return
        }
        const names = { events: 'alle Ereignisse', heating: 'den gesamten Heizverlauf', power: 'den gesamten Stromverlauf', messages: 'alle Meldungen' }
        if (!await HO.confirm(`Wirklich ${names[t.dataset.clear]} löschen? Tipp: vorher exportieren.`, { ok: 'Löschen', danger: true })) return
        await HO.api(`logs/${t.dataset.clear}/clear`, { method: 'POST' })
        HO.toast('Gelöscht'); render(page)
      }
    },
    destroy() { }
  }
})(window.HO)
