/* ComfyOS – start, login, navigation */
(function (HO) {
  'use strict'
  const TABS = [
    ['home', 'Start', 'home'],
    ['light', 'Szenen', 'palette'],
    ['heating', 'Heizplan', 'flame'],
    ['flow', 'Flow', 'workflow'],
    ['events', 'Ereignisse', 'activity'],
    ['settings', 'System', 'settings']
  ]
  let current = null, shown = false, version = ''

  // Draw the frame of the app: sidebar, header with connection pill, content area and the tabs on the phone.
  function shell() {
    const navBtn = ([id, label, ic]) => `<button data-tab="${id}">${HO.icon(ic)}<span>${label}</span></button>`
    document.getElementById('root').innerHTML = `<div class="app">
      <aside class="sidebar">
        <a class="brand" href="#home" data-home-link title="Zur Startseite">${HO.logo()}<div><b>ComfyOS</b><small>ZUHAUSE</small></div></a>
        <nav class="nav">${TABS.map(navBtn).join('')}</nav>
        <div class="sidefoot"><div class="row between"><span>Version ${HO.esc(version)}</span>
          <button class="btn-icon sm" data-act="logout" title="Abmelden" aria-label="Abmelden">${HO.icon('logout', 'sm')}</button></div></div>
      </aside>
      <div class="main">
        <header class="topbar">
          <div class="row grow"><a class="brand-sm" href="#home" data-home-link title="Zur Startseite" aria-label="Zur Startseite">${HO.logo()}</a><h1 id="pageTitle" style="margin:0"></h1></div>
          <div class="row"><button type="button" class="conn-pill" id="connPill" aria-label="Verbindungen – zu den Plugins"><b>…</b></button>
            <button class="btn-icon round" data-act="refresh" title="Aktualisieren" aria-label="Aktualisieren">${HO.icon('refresh')}</button></div>
        </header>
        <div class="preview-bar" id="previewBar" hidden></div>
        <div class="page" id="page"></div>
      </div>
      <nav class="bottomnav">${TABS.map(navBtn).join('')}</nav>
    </div>`
    const root = document.getElementById('root')
    root.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.tab === current) { HO.toTop(); return }
      location.hash = b.dataset.tab
    }))
    root.querySelector('[data-act=refresh]').addEventListener('click', redrawPage)
    // logo: to the home page (on the home page to the top)
    root.querySelectorAll('[data-home-link]').forEach(a => a.addEventListener('click', e => {
      e.preventDefault()
      if (current === 'home') HO.toTop()
      else location.hash = 'home'
    }))
    // connections belong to the plugins: tapping opens the tab "Plugins" under System
    root.querySelector('#connPill').addEventListener('click', () => { if (location.hash !== '#settings?tab=plugins') location.hash = 'settings?tab=plugins' })
    root.querySelector('#previewBar').addEventListener('click', async e => {
      if (!e.target.closest('[data-act=previewEnd]')) return
      try { await HO.api('license/preview', { method: 'DELETE' }) } catch (err) { HO.toast(err.message, 'bad') }
      await HO.refreshLicense()
      redrawPage()
    })
    root.querySelector('[data-act=logout]').addEventListener('click', async () => {
      await HO.api('auth/logout', { method: 'POST' }).catch(() => {})
      location.reload()
    })
    shown = true
  }

  /* Reload plan and licence; while the owner's preview is running, a bar is shown above every page */
  let previewTimer = null
  HO.refreshLicense = async function () {
    await HO.loadEnts()
    HO.license = await HO.api('license').catch(() => null)
    clearTimeout(previewTimer)
    const bar = document.getElementById('previewBar'), l = HO.license, pv = l && l.preview
    if (!bar) return l
    bar.hidden = !pv
    if (!pv) { bar.innerHTML = ''; return l }
    const end = new Date(pv.until * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
    const n = l.paused_flows
    bar.innerHTML = `${HO.icon('eye', 'sm')}<div class="grow">Testansicht <b>${HO.esc(l.plan_title)}</b> bis ${end} Uhr${n ? ` · ${n} ${n === 1 ? 'Flow pausiert' : 'Flows pausieren'}` : ''} · deine Daten bleiben unverändert</div>
      <button class="btn sm" data-act="previewEnd">Zurück zu ${HO.esc(l.licensed_title)}</button>`
    previewTimer = setTimeout(() => HO.refreshLicense().then(() => { if (shown) redrawPage() }),
      Math.min(Math.max(pv.until * 1000 - Date.now(), 0) + 2000, 2147483647))
    return l
  }

  /* Demo home (since 0.23): is this session in the demo? Then every page title says so ("System – DEMO", since 0.23.1
     instead of a bar above the page); "Zurücksetzen" and "Demo beenden" are under System. The own home keeps running
     unchanged meanwhile; ending brings the own dashboard back at once. */
  HO.demo = { active: false }
  HO.refreshDemo = async function () {
    HO.demo = await HO.api('demo').catch(() => ({ active: false }))
    document.body.classList.toggle('in-demo', !!HO.demo.active)
    return HO.demo
  }
  // Suffix of the page title in the demo (markup for the top bar, or plain text for the browser tab).
  HO.demoTitle = plain => !HO.demo.active ? '' : plain ? ' – DEMO' : ' <span class="pt-demo">– DEMO</span>'
  // Buttons of the demo (System page): end it (back to the own home page) or set up the sample home anew.
  HO.demoAction = async function (act) {
    try {
      if (act === 'demoStop') { await HO.api('demo/stop', { method: 'POST' }); location.hash = 'home'; location.reload() }
      if (act === 'demoReset' && await HO.confirm('Alles, was du in der Demo geändert hast, geht verloren.', { title: 'Demo zurücksetzen?', ok: 'Zurücksetzen' })) {
        await HO.api('demo/reset', { method: 'POST' })
        HO.toast('Demo-Wohnung neu eingerichtet', 'ok')
        redrawPage()
      }
    } catch (err) { HO.toast(err.message, 'bad', 5000) }
  }
  // Open the demo home for this session (tile under System) – the page starts anew on the home page of the demo.
  HO.startDemo = async function () {
    try {
      await HO.api('demo/start', { method: 'POST' })
      location.hash = 'home'
      location.reload()
    } catch (err) { HO.toast(err.message, 'bad', 5000) }
  }

  /* Connection pill at the top next to "Aktualisieren" (since 0.19.2, replaces the status dot; plain since 0.19.3):
     "Online" (up to 0.23 "Live") if all enabled services are connected, otherwise "Fehler" – the single services are named in the title.
     Disabled plugins do not count. Data: /api/status or status from /api/overview (core.connection_items). */
  const CONN_TEXT = { live: 'Online', ok: 'verbunden', bad: 'getrennt', error: 'Fehler', connecting: 'verbindet' }
  let connTimer = null
  HO.refreshConn = () => HO.api('status').then(s => HO.setConn(s)).catch(() => {})
  // Show the overall state in the pill. A plugin that is still coming up after its start (state `connecting`, since
  // 0.24) is no fault: the pill says "Verbindet" and asks again shortly, until it is connected or really failed.
  HO.setConn = st => {
    if (!st || typeof st !== 'object') return
    const el = document.getElementById('connPill')
    if (!el) return
    const items = st.items || [], bad = items.some(i => i.state === 'bad' || i.state === 'error')
    const connecting = !bad && items.some(i => i.state === 'connecting')
    el.className = 'conn-pill ' + (bad ? 'bad' : connecting ? 'wait' : st.configured ? 'ok' : '')
    el.title = (items.length ? items.map(i => `${i.name}: ${CONN_TEXT[i.state] || i.state}`).join(' · ') : (st.message || '')) + ' – zu den Plugins'
    el.innerHTML = `<span class="statusdot ${bad ? 'bad' : connecting ? 'spin' : st.configured ? 'ok' : ''}"></span><b>${!st.configured ? 'Nichts verbunden' : bad ? 'Fehler' : connecting ? 'Verbindet' : 'Online'}</b>`
    clearTimeout(connTimer)
    if (items.some(i => i.state === 'connecting')) connTimer = setTimeout(() => { if (shown && !document.hidden) HO.refreshConn() }, 4000)
  }

  /* Redraw the open page in place, keeping the parameters behind "?" (e.g. settings?tab=plugins). Without them the
     page would show its default view while the address still names the other one – and the button that leads
     there would do nothing, because the address does not change. */
  function redrawPage() { return HO.go(current + (HO.hashParams ? '?' + HO.hashParams : ''), true) }

  HO.go = async function (tab, force) {
    // Optional parameters behind "?" (e.g. #home?fx=thunder) stay in the address and are available in HO.hashParams
    tab = String(tab || '')
    const qi = tab.indexOf('?'), params = qi >= 0 ? tab.slice(qi + 1) : ''
    if (qi >= 0) tab = tab.slice(0, qi)
    if (!HO.pages[tab]) tab = 'home'
    const hashTab = location.hash.slice(1).split('?')[0]
    if (hashTab !== tab) {
      if (force || !current) history.replaceState(null, '', '#' + tab + (params ? '?' + params : ''))
      else { location.hash = tab + (params ? '?' + params : ''); return }   // hashchange calls go() again → the back button works (parameters stay, e.g. settings?tab=plugins)
    }
    const changed = params !== (HO.hashParams || '')
    HO.hashParams = params
    if (tab === current && !force && !changed) return
    const prev = current && HO.pages[current]
    if (prev && prev.destroy) { try { prev.destroy() } catch (e) { } }
    HO.prevTab = current
    current = tab
    document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab))
    const p = HO.pages[tab]
    // views without navigation (e.g. floor plan in full screen)
    document.body.classList.toggle('chromeless', !!p.chromeless)
    document.documentElement.classList.toggle('chromeless', !!p.chromeless)   // locks scrolling of the window (app.css)
    document.getElementById('pageTitle').innerHTML = HO.esc(p.title) + HO.demoTitle()
    document.title = (p.title === 'ComfyOS' ? 'ComfyOS' : 'ComfyOS · ' + p.title) + HO.demoTitle(true)
    // fresh element: old event handlers (onclick, onchange …) of the previous page drop away
    const old = document.getElementById('page'), page = old.cloneNode(false)
    old.replaceWith(page)
    if (!force) window.scrollTo(0, 0)
    try { await p.render(page) } catch (e) {
      if (current !== tab) return
      page.innerHTML = `<div class="card"><div class="empty">${HO.icon('warn')}<div><b>Konnte nicht geladen werden</b></div>
        <div class="small muted">${HO.esc(e.message)}</div><button class="btn" data-retry style="margin-top:12px">Erneut versuchen</button></div></div>`
      page.querySelector('[data-retry]').onclick = redrawPage
    }
    // once the content has settled: does anything make the page wider than the window? (finding on the diagnosis page)
    setTimeout(() => { if (current === tab && HO.checkWidth) HO.checkWidth(tab) }, 2000)
  }

  HO.onUnauthorized = () => {
    if (!shown) return
    shown = false
    const prev = current && HO.pages[current]
    if (prev && prev.destroy) { try { prev.destroy() } catch (e) { } }
    current = null
    document.body.classList.remove('chromeless')
    document.documentElement.classList.remove('chromeless')
    HO.boot()
  }

  HO.boot = async function () {
    let st
    try {
      const [s, h] = await Promise.all([HO.api('auth/state'), HO.api('health')])
      st = s; version = h.version
    } catch (e) {
      document.getElementById('root').innerHTML = `<div class="auth"><div class="card auth-card">${HO.logo('auth-logo')}
        <h1>ComfyOS</h1><p class="muted small">Server nicht erreichbar.</p><button class="btn primary" id="retryBoot">Erneut versuchen</button></div></div>`
      document.getElementById('retryBoot').addEventListener('click', () => location.reload())
      return
    }
    if (!st.logged_in) { shown = false; current = null; HO.auth.show(st); return }
    shell()
    await HO.refreshDemo()
    await HO.refreshLicense()
    try { var cfg = await HO.api('settings'); if (cfg.accent_color) HO.applyAccent(cfg.accent_color) } catch (e) {}
    HO.go(location.hash.slice(1) || 'home', true)
    HO.refreshConn()
  }

  window.addEventListener('hashchange', () => { if (shown) HO.go(location.hash.slice(1) || 'home') })
  // Back from the background (phone): show the connection anew
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && shown) HO.refreshConn()
  })
  HO.boot()
  // Field with a stored password (dots): select everything on entering, so that the input replaces the dots
  document.addEventListener('focusin', e => {
    const t = e.target
    if (t && t.matches && t.matches('input[type=password][data-saved]') && t.value === HO.PW_SAVED) setTimeout(() => { try { t.select() } catch (err) { } }, 0)
  })
})(window.HO)
