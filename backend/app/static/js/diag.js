/* ComfyOS – diagnosis of this device (since 0.20, reachable under System at the very bottom or #diag)

   Shows what this device displays ComfyOS with and how well it copes – so that display and
   operating problems on older devices can be narrowed down without developer tools:
     Gerät        browser, screen, zoom, kind of input, page wider than the screen (HO.checkWidth, since 0.24.5)
     Darstellung  what the browser cannot do and how ComfyOS makes up for it (compat.js, compat.css)
     Leistung     performance tier (HO.perf: full or economical) with a manual choice, frame rate probe
     Zeiger       test area: shows which pointer events arrive on tapping, dragging and with two fingers
   None of this leaves the device; "Angaben kopieren" puts a summary into the clipboard. */
(function (HO) {
  'use strict'
  let stopProbe = null
  let widthFinding = null   // last page that was wider than the window: { tab, width, scroll, items }

  // Short name of an element for the finding: tag, id and the first two classes (SVG elements included).
  function describeElement(el) {
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2)
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls.length ? '.' + cls.join('.') : '')
  }
  // Is the element inside a container that cuts it off sideways (a scrolling row)? Then it widens nothing.
  function clippedSideways(el) {
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== 'visible') return true
    }
    return false
  }
  /* Page wider than the window (since 0.24.5): Safari on the iPad then zooms the page out or lets it slide sideways, and the
     bars end up offset. app.js calls this two seconds after every page. Cheap when everything fits (one comparison); only
     otherwise it looks for the outermost elements that stick out on the right and keeps them for the diagnosis page. */
  HO.checkWidth = function (tab) {
    const root = document.documentElement, width = root.clientWidth
    if (!width || root.scrollWidth <= width + 1 || !document.body) return
    const items = [], all = document.body.getElementsByTagName('*')
    for (let i = 0; i < all.length && items.length < 3; i++) {
      const el = all[i], r = el.getBoundingClientRect()
      if (!r.width || r.right <= width + 1) continue
      const parent = el.parentElement
      if (parent && parent !== document.body && parent.getBoundingClientRect().right > width + 1) continue   // the parent is the culprit
      if (clippedSideways(el)) continue
      items.push(`${describeElement(el)} (+${Math.round(r.right - width)} px)`)
    }
    widthFinding = { tab, width, scroll: root.scrollWidth, items }
  }
  // Text for the finding: fits, or which page was how much wider and through what.
  function widthText() {
    if (!widthFinding) return 'passt auf den Bildschirm'
    const f = widthFinding
    return `Seite „${f.tab}“ war ${f.scroll - f.width} px zu breit` + (f.items.length ? ': ' + f.items.join(', ') : '')
  }
  // Zoom of the visible area (1 = unzoomed); Safari on the iPad zooms out on its own when a page is too wide.
  const zoomText = () => window.visualViewport ? String(Math.round(window.visualViewport.scale * 100) / 100) : 'unbekannt'

  // browser and system from the user agent – for display only, nothing depends on it
  function describeBrowser() {
    const ua = navigator.userAgent || ''
    const pick = re => { const m = ua.match(re); return m ? m[1].replace(/_/g, '.') : '' }
    const touchMac = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1   // iPadOS reports itself as a Mac
    let system = /iPhone/.test(ua) ? 'iOS ' + pick(/OS ([\d_]+)/) : /iPad/.test(ua) ? 'iPadOS ' + pick(/OS ([\d_]+)/)
      : touchMac ? 'iPadOS' : /Android/.test(ua) ? 'Android ' + pick(/Android ([\d.]+)/) : /Windows/.test(ua) ? 'Windows'
        : /Mac OS X/.test(ua) ? 'macOS ' + pick(/Mac OS X ([\d_]+)/) : /Linux/.test(ua) ? 'Linux' : 'unbekannt'
    let browser = /Edg\//.test(ua) ? 'Edge ' + pick(/Edg\/([\d.]+)/) : /Firefox\//.test(ua) ? 'Firefox ' + pick(/Firefox\/([\d.]+)/)
      : /Chrome\//.test(ua) ? 'Chrome ' + pick(/Chrome\/([\d.]+)/) : /Safari\//.test(ua) ? 'Safari ' + pick(/Version\/([\d.]+)/) : 'unbekannt'
    return { system: system.trim(), browser: browser.trim() }
  }

  // What the browser can do. `need`: what ComfyOS needs it for; `fallback`: what applies if it is missing
  function features() {
    const css = (prop, value) => !!(window.CSS && CSS.supports && CSS.supports(prop, value))
    const noGap = document.documentElement.classList.contains('no-flexgap')
    return [
      { name: 'Abstände in Flex-Behältern (gap)', ok: !noGap, fallback: 'Ausweichregeln aktiv (compat.css)' },
      { name: 'Zeiger-Ereignisse (Pointer Events)', ok: !!window.PointerEvent, fallback: 'aus Touch-Ereignissen nachgebildet' },
      { name: 'Größenbeobachtung (ResizeObserver)', ok: /native code/.test(String(window.ResizeObserver)), fallback: 'träges Nachsehen einmal je Sekunde' },
      { name: 'Seitenverhältnis (aspect-ratio)', ok: css('aspect-ratio', '1'), fallback: 'feste Höhe' },
      { name: 'Weichzeichner hinter Flächen', ok: css('backdrop-filter', 'blur(2px)') || css('-webkit-backdrop-filter', 'blur(2px)'), fallback: 'Flächen ohne Unschärfe' },
      { name: 'Weiches Scrollen', ok: 'scrollBehavior' in document.documentElement.style, fallback: 'springt direkt' },
      { name: 'Sichtbarkeit beobachten (IntersectionObserver)', ok: !!window.IntersectionObserver, fallback: 'Wettereffekte pausieren nur bei verborgenem Tab' }
    ]
  }

  // All details as text to copy – without address, name or id of the device.
  function summary() {
    const b = describeBrowser(), f = features()
    return ['ComfyOS-Diagnose', `Browser: ${b.browser} auf ${b.system}`,
      `Bildschirm: ${window.innerWidth} × ${window.innerHeight} Punkte, Pixeldichte ${window.devicePixelRatio || 1}`,
      `Zoom: ${zoomText()} · Seitenbreite: ${widthText()}`,
      `Eingabe: ${navigator.maxTouchPoints > 0 ? 'Touch (' + navigator.maxTouchPoints + ' Finger)' : 'kein Touch'}`,
      `Leistungsstufe: ${HO.perf.low ? 'sparsam' : 'voll'} (${HO.perf.reason})`,
      ...f.map(x => `${x.ok ? 'ja  ' : 'nein'} ${x.name}${x.ok ? '' : ' – ' + x.fallback}`)].join('\n')
  }

  // frame rate probe: measure the distances between the frames for two seconds
  function measureFrames(done) {
    const gaps = []
    let last = 0, start = 0, raf = 0
    // One frame of the frame rate probe: remember the distance to the previous frame, evaluate after two seconds.
    const tick = t => {
      if (!start) start = t
      if (last) gaps.push(t - last)
      last = t
      if (t - start < 2000) raf = requestAnimationFrame(tick)
      else done(gaps)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }

  // Draw and set up the page "Diagnose": device, features of the browser, performance tier, frame rate and pointer probe.
  function render(page) {
    const b = describeBrowser(), mode = HO.perf.mode
    const row = (label, value) => `<div class="li"><div class="grow">${label}</div><div class="small">${value}</div></div>`
    page.innerHTML = `
      <div class="section-head"><div><h2>Diagnose</h2><div class="small muted">Womit dieses Gerät ComfyOS anzeigt – nichts davon verlässt das Gerät.</div></div>
        <div class="row"><button class="btn sm" data-act="copy">${HO.icon('list', 'sm')} Angaben kopieren</button><a class="btn sm ghost" href="#settings" style="text-decoration:none">Zurück</a></div></div>
      <section class="card" id="diagDevice"><div class="card-head"><div><div class="eyebrow">Gerät</div><h3>${HO.esc(b.browser)}</h3></div>${HO.icon('device')}</div>
        <div class="list">${row('System', HO.esc(b.system))}
          ${row('Bildschirm', `${window.innerWidth} × ${window.innerHeight} Punkte · Pixeldichte ${window.devicePixelRatio || 1}`)}
          ${row('Zoom', HO.esc(zoomText()))}
          ${row('Seitenbreite', HO.esc(widthText()))}
          ${row('Eingabe', navigator.maxTouchPoints > 0 ? `Touch (${navigator.maxTouchPoints} Finger)` : 'Maus oder Trackpad')}</div></section>
      <section class="card" id="diagFeatures"><div class="card-head"><div><div class="eyebrow">Darstellung</div><h3>Was der Browser kann</h3></div>${HO.icon('layers')}</div>
        <div class="list">${features().map(f => `<div class="li"><div class="grow">${f.name}${f.ok ? '' : `<div class="small muted">${f.fallback}</div>`}</div>
          <span class="badge ${f.ok ? 'ok' : 'warn'}">${f.ok ? 'vorhanden' : 'ausgeglichen'}</span></div>`).join('')}</div></section>
      <section class="card" id="diagPerf"><div class="card-head"><div><div class="eyebrow">Leistung</div><h3>Stufe: <span id="diagTier">${HO.perf.low ? 'sparsam' : 'voll'}</span></h3></div>${HO.icon('gauge')}</div>
        <p class="small muted">Sparsam verzichtet auf Unschärfe hinter Flächen und auf Weichzeichner im Grundriss; Wettereffekte laufen mit weniger Bildpunkten.
          „Automatisch“ richtet sich nach dem Browser und danach, ob die Wettereffekte auf diesem Gerät pünktlich mitkommen (jetzt: <span id="diagWhy">${HO.perf.reason}</span>).</p>
        <div class="seg" id="diagMode" role="group" aria-label="Leistungsstufe">${[['auto', 'Automatisch'], ['high', 'Voll'], ['low', 'Sparsam']]
          .map(([m, label]) => `<button type="button" data-mode="${m}" class="${mode === m ? 'active' : ''}" aria-pressed="${mode === m}">${label}</button>`).join('')}</div>
        <div class="form-actions" style="margin-top:14px"><button class="btn" data-act="frames">${HO.icon('gauge', 'sm')} Bildrate messen</button>
          <button class="btn ghost" data-act="reload">${HO.icon('refresh', 'sm')} Neu laden</button></div>
        <p class="small" id="diagFrames" aria-live="polite"></p></section>
      <section class="card" id="diagPointer"><div class="card-head"><div><div class="eyebrow">Zeiger</div><h3>Probefläche</h3></div>${HO.icon('move')}</div>
        <p class="small muted">Tippen, ziehen oder mit zwei Fingern spreizen: darunter steht, was beim Browser ankommt.</p>
        <div id="diagPad" style="height:160px;border-radius:14px;border:1px dashed var(--line-2);background:var(--card-2);touch-action:none;display:flex;align-items:center;justify-content:center;-webkit-user-select:none;user-select:none"><span class="muted small">hier tippen</span></div>
        <p class="small" id="diagPadOut" aria-live="polite">Noch nichts angekommen.</p></section>`

    const out = page.querySelector('#diagPadOut'), pad = page.querySelector('#diagPad'), down = new Map()
    const seen = { down: 0, move: 0, up: 0, cancel: 0, type: '', most: 0 }
    const show = () => {
      out.textContent = `Art: ${seen.type || '–'} · gedrückt ${seen.down}× · bewegt ${seen.move}× · losgelassen ${seen.up}× · abgebrochen ${seen.cancel}× · ` +
        `gleichzeitig höchstens ${seen.most} Zeiger`
    }
    // pointer probe: count events, record the kind of pointer and the largest number of simultaneous fingers.
    const onPointer = e => {
      seen.type = e.pointerType || 'unbekannt'
      if (e.type === 'pointerdown') { seen.down++; down.set(e.pointerId, 1); try { pad.setPointerCapture(e.pointerId) } catch (err) { } }
      else if (e.type === 'pointermove') seen.move++
      else { if (e.type === 'pointerup') seen.up++; else seen.cancel++; down.delete(e.pointerId) }
      seen.most = Math.max(seen.most, down.size)
      show()
    }
    ;['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].forEach(t => pad.addEventListener(t, onPointer))

    page.addEventListener('click', e => {
      const modeBtn = e.target.closest('[data-mode]'), act = (e.target.closest('[data-act]') || {}).dataset
      if (modeBtn) {
        HO.perf.set(modeBtn.dataset.mode)
        page.querySelectorAll('#diagMode button').forEach(x => { const on = x === modeBtn; x.classList.toggle('active', on); x.setAttribute('aria-pressed', on) })
        page.querySelector('#diagTier').textContent = HO.perf.low ? 'sparsam' : 'voll'
        page.querySelector('#diagWhy').textContent = HO.perf.reason
        HO.toast('Gilt ab sofort; Grundriss und Wettereffekte übernehmen die Stufe nach dem Neuladen.')
      } else if (act && act.act === 'reload') location.reload()
      else if (act && act.act === 'frames') {
        const line = page.querySelector('#diagFrames')
        line.textContent = 'Misst zwei Sekunden …'
        if (stopProbe) stopProbe()
        stopProbe = measureFrames(gaps => {
          stopProbe = null
          if (!gaps.length) { line.textContent = 'Keine Bilder gemessen.'; return }
          const mean = gaps.reduce((a, g) => a + g, 0) / gaps.length, worst = Math.max(...gaps), late = gaps.filter(g => g > 25).length
          line.textContent = `${Math.round(1000 / mean)} Bilder je Sekunde · längster Abstand ${Math.round(worst)} ms · ${late} von ${gaps.length} Bildern verspätet`
        })
      } else if (act && act.act === 'copy') {
        const text = summary()
        const fallback = () => HO.toast('Kopieren nicht möglich – die Angaben stehen auf dieser Seite.', 'warn')
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => HO.toast('Angaben kopiert', 'ok'), fallback)
        else fallback()
      }
    })
  }

  HO.pages.diag = {
    title: 'Diagnose',
    render,
    destroy() { if (stopProbe) { stopProbe(); stopProbe = null } }
  }
})(window.HO)
