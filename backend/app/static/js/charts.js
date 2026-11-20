/* ComfyOS – chart measured/target (SVG in real pixel size, so that text stays sharp) */
(function (HO) {
  'use strict'

  // Distance of the grid lines fitting the span of the temperature axis.
  function niceStep(span) {
    if (span <= 3) return 0.5
    if (span <= 8) return 1
    if (span <= 16) return 2
    return 5
  }

  /* points: [{t: epochSeconds, ist: number|null, soll: number|null, valve: 0..100|null}]
     lane: true = own lane below the chart (same time axis, own scale closed…open): the reported valve opening,
     otherwise – if the thermostat reports no valve position – the heating demand derived from "target above measured" (hatched). */
  HO.tempChart = function (el, points, { hours = 24, height = 240, lane = false } = {}) {
    let ro, laneMode = null
    // Draw the chart in the current width: axes, measured as a curve, target as steps, below it valve opening or
    // heating demand, crosshair.
    const draw = () => {
      const W = Math.max(280, el.clientWidth), H = height
      const m = { l: 38, r: 12, t: 14, b: 26 }
      const now = Date.now() / 1000, t0 = now - hours * 3600
      const pts = points.filter(p => p.t >= t0)
      if (pts.length < 2) {
        el.innerHTML = `<div class="empty">${HO.icon('activity')}<div>Noch zu wenige Messwerte.</div><div class="tiny">ComfyOS zeichnet alle 5 Minuten auf, solange es läuft.</div></div>`
        return
      }
      const vals = pts.flatMap(p => [p.ist, p.soll]).filter(v => v != null)
      let lo = Math.floor(Math.min(...vals) - 0.5), hi = Math.ceil(Math.max(...vals) + 0.5)
      if (hi - lo < 4) { const c = (hi + lo) / 2; lo = Math.floor(c - 2); hi = Math.ceil(c + 2) }
      const X = t => m.l + (t - t0) / (now - t0) * (W - m.l - m.r)
      const Y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b)
      const step = niceStep(hi - lo)
      // valve lane: 'valve' = measured, 'demand' = derived (same rule as HO.devClass: target > measured + 0.3)
      laneMode = !lane ? null : pts.some(p => p.valve != null) ? 'valve' : pts.some(p => p.soll != null && p.ist != null) ? 'demand' : null
      const LG = laneMode ? 20 : 0, LH = laneMode ? 56 : 0
      const lT = H - m.b + LG, lB = lT + LH, total = laneMode ? lB + m.b : H, axY = total - 6
      const laneVal = p => laneMode === 'valve' ? p.valve : (p.soll != null && p.ist != null ? (p.soll > p.ist + 0.3 ? 100 : 0) : null)
      const VY = v => lB - v / 100 * (LH - 8)
      let laneSVG = '', laneSum = ''
      if (laneMode) {
        // step curve: a value holds until the next reading; gaps (> 3 measuring intervals) interrupt
        let area = '', top = '', prev = null, openS = 0, openW = 0
        const close = () => { if (prev) { const xe = X(Math.min(now, prev.t + 300)).toFixed(1); area += `H${xe}V${lB}Z`; top += `H${xe}` } }
        pts.forEach((p, i) => {
          const v = laneVal(p)
          if (v == null) { close(); prev = null; return }
          const x = X(p.t).toFixed(1), y = VY(v).toFixed(1)
          if (!prev || p.t - prev.t > 900) { close(); area += `M${x} ${lB}V${y}`; top += `M${x} ${y}` }
          else { area += `H${x}V${y}`; top += `H${x}V${y}` }
          prev = p
          const nx = pts[i + 1], dur = nx && nx.t - p.t <= 900 ? nx.t - p.t : 300
          if (v > 0) { openS += dur; openW += v * dur }
        })
        close()
        const hm = sec => { const h = Math.floor(sec / 3600), mi = Math.round(sec % 3600 / 60); return h ? `${h} h${mi ? ' ' + mi + ' min' : ''}` : `${mi} min` }
        const measured = laneMode === 'valve'
        laneSVG = `<rect class="lane-bg" x="${m.l}" y="${lT - 4}" width="${W - m.l - m.r}" height="${LH + 4}" rx="6"/>
          <line class="gl" x1="${m.l}" x2="${W - m.r}" y1="${lB}" y2="${lB}"/>
          <text class="ax" x="${m.l - 6}" y="${(VY(100) + 4).toFixed(1)}" text-anchor="end">auf</text>
          <text class="ax" x="${m.l - 6}" y="${lB}" text-anchor="end">zu</text>
          ${area ? `<path class="${measured ? 'valve-area' : 'demand-area'}" d="${area}"/><path class="${measured ? 'valve-line' : 'demand-line'}" d="${top}"/>` : ''}
          <text class="lane-t" x="${m.l}" y="${lT - 8}">${measured ? 'Ventil' : 'Heizbedarf (abgeleitet)'}</text>`
        laneSum = measured
          ? (openS ? `Ventil im Zeitraum ${hm(openS)} geöffnet · im Mittel ${Math.round(openW / openS)} % auf, wenn offen` : 'Ventil im Zeitraum durchgehend zu')
          : `Das Thermostat meldet keine Ventilstellung – Heizbedarf aus „Soll über Ist“ abgeleitet${openS ? `: etwa ${hm(openS)}` : ': keiner'}`
      }
      let grid = ''
      for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
        grid += `<line class="gl" x1="${m.l}" x2="${W - m.r}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>` +
          `<text class="ax" x="${m.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${HO.num(v)}°</text>`
      }
      // x axis
      // align ticks with the local time (00:00, 06:00 …), fewer on narrow screens
      const xStep = hours <= 24 ? (W < 520 ? 6 : 3) * 3600 : 24 * 3600
      const off = new Date(t0 * 1000).getTimezoneOffset() * 60
      let xt = Math.ceil((t0 - off) / xStep) * xStep + off, xa = ''
      for (; xt <= now; xt += xStep) {
        const d = new Date(xt * 1000)
        const label = hours <= 24 ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : HO.dayNames[(d.getDay() + 6) % 7]
        xa += `<text class="ax" x="${X(xt).toFixed(1)}" y="${axY}" text-anchor="middle">${label}</text>`
      }
      // lines with gaps (> 3 measuring intervals without a value)
      const gap = 3 * 300
      // Readings as a smoothed curve; gaps in the recording interrupt the line.
      const smoothLine = (key) => {
        const segs = [], cur = []
        for (const p of pts) {
          if (p[key] == null) { if (cur.length) { segs.push(cur.slice()); cur.length = 0 }; continue }
          if (cur.length && p.t - cur[cur.length - 1].t > gap) { segs.push(cur.slice()); cur.length = 0 }
          cur.push(p)
        }
        if (cur.length) segs.push(cur)
        let d = ''
        for (const seg of segs) {
          if (!seg.length) continue
          const px = seg.map(p => X(p.t)), py = seg.map(p => Y(p[key]))
          d += `M${px[0].toFixed(1)} ${py[0].toFixed(1)}`
          if (seg.length === 2) { d += `L${px[1].toFixed(1)} ${py[1].toFixed(1)}`; continue }
          for (let i = 0; i < seg.length - 1; i++) {
            const x0 = px[Math.max(0, i - 1)], y0 = py[Math.max(0, i - 1)]
            const x1 = px[i], y1 = py[i], x2 = px[i + 1], y2 = py[i + 1]
            const x3 = px[Math.min(seg.length - 1, i + 2)], y3 = py[Math.min(seg.length - 1, i + 2)]
            const cp1x = x1 + (x2 - x0) / 6, cp1y = y1 + (y2 - y0) / 6
            const cp2x = x2 - (x3 - x1) / 6, cp2y = y2 - (y3 - y1) / 6
            d += `C${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`
          }
        }
        return d
      }
      // Values as a step line (the target holds until the next change); gaps interrupt the line.
      const steppedLine = (key) => {
        let d = '', prev = null
        for (const p of pts) {
          if (p[key] == null) { prev = null; continue }
          const x = X(p.t).toFixed(1), y = Y(p[key]).toFixed(1)
          if (!prev || p.t - prev.t > gap) d += `M${x} ${y}`
          else d += `H${x}V${y}`
          prev = p
        }
        return d
      }
      const ist = smoothLine('ist'), soll = steppedLine('soll')
      const firstIst = pts.find(p => p.ist != null), lastIst = [...pts].reverse().find(p => p.ist != null)
      const area = ist && firstIst && lastIst ? `${ist}L${X(lastIst.t).toFixed(1)} ${H - m.b}L${X(firstIst.t).toFixed(1)} ${H - m.b}Z` : ''
      el.innerHTML = `<svg width="${W}" height="${total}" viewBox="0 0 ${W} ${total}">
        <defs><linearGradient id="istGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--accent)" stop-opacity=".30"/><stop offset="1" style="stop-color:var(--accent)" stop-opacity="0"/></linearGradient>
          <linearGradient id="valveGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--valve)" stop-opacity=".75"/><stop offset="1" style="stop-color:var(--valve)" stop-opacity=".2"/></linearGradient>
          <pattern id="demandHatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" style="stroke:var(--valve)" stroke-width="2" stroke-opacity=".55"/></pattern></defs>
        ${grid}${xa}${laneSVG}
        ${area ? `<path class="ist-area" d="${area}"/>` : ''}
        ${soll ? `<path class="soll" d="${soll}"/>` : ''}
        ${ist ? `<path class="ist" d="${ist}"/>` : ''}
        <line class="cross" y1="${m.t}" y2="${laneMode ? lB : H - m.b}" x1="-10" x2="-10"/>
        <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${total}" fill="transparent" class="hit"/>
      </svg><div class="chart-tip hidden"></div>${laneSum ? `<div class="lane-sum small muted">${HO.esc(laneSum)}</div>` : ''}`
      const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'), tip = el.querySelector('.chart-tip')
      // Show the crosshair and the values at the data point closest to the pointer.
      const move = e => {
        const r = svg.getBoundingClientRect(), x = e.clientX - r.left
        const t = t0 + (x - m.l) / (W - m.l - m.r) * (now - t0)
        let best = pts[0]
        for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p
        const bx = X(best.t)
        cross.setAttribute('x1', bx); cross.setAttribute('x2', bx)
        const d = new Date(best.t * 1000)
        tip.innerHTML = `<b>${d.toLocaleString('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</b><br>` +
          `<span style="color:var(--accent-text)">Ist ${HO.temp(best.ist, true)}</span>` + (best.soll != null ? ` · <span style="color:var(--soll)">Soll ${HO.temp(best.soll, true)}</span>` : '') +
          (laneMode === 'valve' && best.valve != null ? `<br>Ventil ${best.valve > 0 ? best.valve + ' % auf' : 'zu'}`
            : laneMode === 'demand' && laneVal(best) != null ? `<br>Heizbedarf ${laneVal(best) ? 'ja' : 'nein'} <span class="muted">(abgeleitet)</span>` : '')
        tip.classList.remove('hidden')
        tip.style.left = Math.min(Math.max(bx - 60, 0), W - 170) + 'px'
      }
      const leave = () => { tip.classList.add('hidden'); cross.setAttribute('x1', -10); cross.setAttribute('x2', -10) }
      const hit = svg.querySelector('.hit')
      hit.addEventListener('pointermove', move)
      hit.addEventListener('pointerdown', move)
      hit.addEventListener('pointerleave', leave)
    }
    draw()
    if (window.ResizeObserver) {
      let w = el.clientWidth
      ro = new ResizeObserver(() => { if (Math.abs(el.clientWidth - w) > 8) { w = el.clientWidth; draw() } })
      ro.observe(el)
    }
    return { redraw: draw, destroy: () => ro && ro.disconnect(), get lane() { return laneMode } }
  }

  /* Power consumption in watts.
     points: [{t: epochSeconds, w: number}] = sum (one area in the accent colour).
     opts.series: [{id, name, color, points}] = one line per device (colours in a fixed order, CSS --series-N);
     the legend below hides and shows series by tapping (opts.hidden: set of the hidden ids). */
  HO.powerChart = function (el, points, { hours = 24, height = 200, series = null, hidden = null, onToggle = null } = {}) {
    let ro
    const off = hidden || new Set()
    // Draw the chart in the current width: axes, sum as an area or one line per device, legend, crosshair.
    const draw = () => {
      const W = Math.max(280, el.clientWidth), H = height
      const m = { l: 44, r: 12, t: 14, b: 26 }
      const now = Date.now() / 1000, t0 = now - hours * 3600
      const multi = !!(series && series.length)
      const sets = multi
        ? series.map(s => ({ id: s.id, name: s.name, color: s.color, pts: s.points.filter(p => p.t >= t0) }))
        : [{ id: '_sum', name: 'Summe', color: 'var(--accent)', pts: points.filter(p => p.t >= t0) }]
      const shown = sets.filter(s => !multi || !off.has(s.id))
      const legend = multi ? `<div class="pw-legend" role="group" aria-label="Geräte ein- und ausblenden">${sets.map(s =>
        `<button type="button" data-ser="${HO.esc(s.id)}" aria-pressed="${!off.has(s.id)}" title="${off.has(s.id) ? 'Einblenden' : 'Ausblenden'}">
          <i style="background:${s.color}"></i><span>${HO.esc(s.name)}</span></button>`).join('')}</div>` : ''
      const all = shown.reduce((a, s) => a.concat(s.pts), [])
      if (sets.every(s => s.pts.length < 2)) {
        el.innerHTML = `<div class="empty">${HO.icon('bolt')}<div>Noch zu wenige Messwerte.</div>
          <div class="tiny">ComfyOS zeichnet den Verbrauch alle 5 Minuten auf, solange es läuft.</div></div>`
        return
      }
      const max = Math.max(10, ...all.map(p => p.w))
      const hi = Math.ceil(max / 10) * 10
      const X = t => m.l + (t - t0) / (now - t0) * (W - m.l - m.r)
      const Y = v => m.t + (1 - v / hi) * (H - m.t - m.b)
      let grid = ''
      for (let i = 0; i <= 4; i++) {
        const v = hi / 4 * i
        grid += `<line class="gl" x1="${m.l}" x2="${W - m.r}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>` +
          `<text class="ax" x="${m.l - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${HO.num(v)}</text>`
      }
      const xStep = hours <= 24 ? (W < 520 ? 6 : 3) * 3600 : (hours <= 168 ? 24 : (W < 520 ? 7 : 3) * 24) * 3600
      const tz = new Date(t0 * 1000).getTimezoneOffset() * 60
      let xt = Math.ceil((t0 - tz) / xStep) * xStep + tz, xa = ''
      for (; xt <= now; xt += xStep) {
        const d = new Date(xt * 1000)
        const label = hours <= 24 ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
          : hours <= 168 ? HO.dayNames[(d.getDay() + 6) % 7] : d.toLocaleDateString('de-DE', { day: 'numeric', month: 'numeric' })
        xa += `<text class="ax" x="${X(xt).toFixed(1)}" y="${H - 6}" text-anchor="middle">${label}</text>`
      }
      const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)} ${Y(p.w).toFixed(1)}`).join('')
      let marks = ''
      if (multi) {
        for (const s of shown) if (s.pts.length > 1) marks += `<path class="pw-line" d="${path(s.pts)}" style="stroke:${s.color}"/>`
      } else {
        const pts = sets[0].pts, line = path(pts)
        marks = `<path class="ist-area" d="${line}L${X(pts[pts.length - 1].t).toFixed(1)} ${H - m.b}L${X(pts[0].t).toFixed(1)} ${H - m.b}Z" fill="url(#pwGrad)"/><path class="ist" d="${line}"/>`
      }
      el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Leistung in Watt über ${hours <= 24 ? '24 Stunden' : Math.round(hours / 24) + ' Tage'}">
        <defs><linearGradient id="pwGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--accent)" stop-opacity=".30"/><stop offset="1" style="stop-color:var(--accent)" stop-opacity="0"/></linearGradient></defs>
        ${grid}${xa}${marks}
        <line class="cross" y1="${m.t}" y2="${H - m.b}" x1="-10" x2="-10"/>
        <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" class="hit"/>
      </svg><div class="chart-tip hidden"></div><div class="tiny muted" style="text-align:right">Watt</div>${legend}`
      const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'), tip = el.querySelector('.chart-tip')
      const ref = (shown.find(s => s.pts.length) || sets[0]).pts
      const near = (pts, t) => { let best = null; for (const p of pts) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p; return best }
      // Show the crosshair and the values at the point in time closest to the pointer.
      const move = e => {
        if (!ref.length) return
        const r = svg.getBoundingClientRect(), x = e.clientX - r.left
        const best = near(ref, t0 + (x - m.l) / (W - m.l - m.r) * (now - t0))
        const bx = X(best.t)
        cross.setAttribute('x1', bx); cross.setAttribute('x2', bx)
        const when = new Date(best.t * 1000).toLocaleString('de-DE', { weekday: 'short', day: hours > 168 ? 'numeric' : undefined, month: hours > 168 ? 'numeric' : undefined, hour: '2-digit', minute: '2-digit' })
        if (multi) {
          const rows = shown.map(s => ({ s, p: near(s.pts, best.t) })).filter(x => x.p && Math.abs(x.p.t - best.t) < 1)
            .sort((a, b) => b.p.w - a.p.w)
          const sum = rows.reduce((a, x) => a + x.p.w, 0)
          tip.innerHTML = `<b>${when}</b>` + rows.map(x => `<div class="pw-tip-row"><i style="background:${x.s.color}"></i>${HO.esc(x.s.name)}<b>${HO.num(x.p.w)} W</b></div>`).join('') +
            (rows.length > 1 ? `<div class="pw-tip-row">Zusammen<b>${HO.num(Math.round(sum * 10) / 10)} W</b></div>` : '')
        } else tip.innerHTML = `<b>${when}</b><br><span>${HO.num(best.w)} W</span>`
        tip.classList.remove('hidden')
        tip.style.left = Math.min(Math.max(bx - 60, 0), W - 190) + 'px'
      }
      const hit = svg.querySelector('.hit')
      hit.addEventListener('pointermove', move)
      hit.addEventListener('pointerdown', move)
      hit.addEventListener('pointerleave', () => { tip.classList.add('hidden'); cross.setAttribute('x1', -10); cross.setAttribute('x2', -10) })
      el.querySelectorAll('[data-ser]').forEach(b => b.addEventListener('click', () => {
        const id = b.dataset.ser
        if (off.has(id)) off.delete(id)
        else if (sets.length - off.size > 1) off.add(id)   // one series always stays visible
        if (onToggle) onToggle(off)
        draw()
      }))
    }
    draw()
    if (window.ResizeObserver) {
      let w = el.clientWidth
      ro = new ResizeObserver(() => { if (Math.abs(el.clientWidth - w) > 8) { w = el.clientWidth; draw() } })
      ro.observe(el)
    }
    return { redraw: draw, destroy: () => ro && ro.disconnect() }
  }
})(window.HO)
