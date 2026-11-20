/* ComfyOS Flow – automations: list, editor (full screen), field widgets, templates.
   The nodes are described by static/flow_nodes.json (shared with the engine) – there is no code per node
   here, only widgets per field type. Roles, n+1 ports and "when" are handled by HO.flowSpec (flow_canvas.js). */
(function (HO) {
  'use strict'
  const V = ((document.currentScript && document.currentScript.src) || '').split('v=')[1] || ''
  const FS = HO.flowSpec
  const CATS = ['source', 'logic', 'action']
  let SPECS = null, byType = {}
  let D = { devices: [], rooms: [], routines: [], flows: [], vars: {}, scenes: [], groups: [] }   // choice lists for the fields
  const ss = { get: k => { try { return sessionStorage.getItem(k) } catch (e) { return null } },
    set: (k, v) => { try { sessionStorage.setItem(k, v) } catch (e) { } } }
  const clone = o => JSON.parse(JSON.stringify(o))
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

  // Load the catalogue of the nodes (flow_nodes.json) – once per session – and make it known to the editor.
  async function specs() {
    if (!SPECS) {
      const r = await fetch('/static/flow_nodes.json?v=' + V, { credentials: 'same-origin' })
      if (!r.ok) throw new Error('Bausteine konnten nicht geladen werden.')
      SPECS = await r.json()
      byType = Object.fromEntries(SPECS.nodes.map(n => [n.type, n]))
      FS.use(SPECS)
    }
    return SPECS
  }
  // Load everything nodes choose from: devices, rooms, heating routines, flows, scenes and groups of the floor plan.
  async function loadLists() {
    const [devices, rooms, routines, list, scenes, plan] = await Promise.all([HO.api('devices').catch(() => []), HO.api('rooms').catch(() => []),
      HO.api('routines').catch(() => ({ routines: [] })), HO.api('flows').catch(() => ({ flows: [], vars: {} })),
      HO.api('scenes').catch(() => ({ scenes: [] })), HO.api('floorplan').catch(() => ({}))])
    D = { devices: devices.slice().sort((a, b) => HO.byName(a.name, b.name)), rooms: rooms.filter(r => r.room_id),
      routines: routines.routines || [], flows: list.flows || [], vars: list.vars || {}, scenes: scenes.scenes || [],
      groups: HO.floorplan.groupsOf(plan).filter(g => g.id) }
    return list
  }

  /* ---------- Ports: the shape shows the kind (circle = signal, square = yes/no) */
  HO.flowPortText = (p, dir) => p.kind === 'bool'
    ? (dir === 'out' ? 'Ja/Nein – an eine Bedingung von „Wenn / Dann“, „Solange“, „Haltezeit“ oder „Nicht“ anschließen'
      : p.grow ? 'Bedingung – einen Ja/Nein-Ausgang anschließen; für jede weitere erscheint ein neuer freier Anschluss'
        : 'Bedingung – ein Ja/Nein-Ausgang; eine neue Linie ersetzt die alte')
    : dir === 'out' ? 'Signal – weiter zum nächsten Baustein' : 'Signal-Eingang – ein ankommendes Signal startet diesen Baustein'
  const portSym = p => `<span class="fl-sym k-${p.kind}"></span>`
  // Collapsible legend "Anschlüsse" of a node: every input and output with its meaning.
  function infoHTML(n) {
    const rows = [...FS.ins(n, E ? E.flow.edges : []).filter(p => !p.free || p.id.slice(-1) === '1').map(p => [p, 'in']), ...FS.outs(n).map(p => [p, 'out'])]
    return `<details class="fl-info"><summary>Anschlüsse</summary>
      ${rows.length ? `<div class="fl-legend">${rows.map(([p, dir]) => `<div>${portSym(p)}<b>${dir === 'in' ? 'Eingang' : 'Ausgang'}${p.label ? ` „${HO.esc(p.label)}“` : ''}</b><span class="muted">${HO.esc(HO.flowPortText(p, dir))}</span></div>`).join('')}</div>`
        : '<p class="small muted">Keine Anschlüsse.</p>'}</details>`
  }

  /* ---------- Devices, targets, properties */
  const devOf = id => D.devices.find(d => d.id === id)
  const roomName = id => (D.rooms.find(r => r.room_id === id) || {}).name || 'Raum fehlt'
  const groupOf = id => D.groups.find(g => g.id === id)
  const caps = d => Object.keys(FS.props).filter(p => d[p] != null)   // property = the device reports a value for it
  const fieldKinds = (f, n) => f.kinds_by ? (f.kinds_by.map[FS.value(n, f.kinds_by.key)] || null) : (f.kinds || null)
  // Devices a target selection means (single ones, group members, all in the rooms)
  function targetList(t) {
    t = t || {}
    const ids = new Set(t.devices || []), rooms = new Set(t.rooms || [])
    ;(t.groups || []).forEach(g => { const gr = groupOf(g); if (gr) (gr.members || []).forEach(m => ids.add(m)) })
    return D.devices.filter(d => t.all || ids.has(d.id) || rooms.has(d.room_id))
  }
  const targetEmpty = t => !t || !((t.devices || []).length || (t.rooms || []).length || (t.groups || []).length || t.all)
  // Properties "Gerätestatus" offers: union for "at least one", intersection for "all"
  function propsFor(n) {
    const all = Object.keys(FS.props), sets = targetList(FS.value(n, 'target')).map(caps).filter(s => s.length)
    if (!sets.length) return all
    const any = all.filter(p => sets.some(s => s.indexOf(p) >= 0))
    if (FS.value(n, 'match') !== 'all') return any
    const every = all.filter(p => sets.every(s => s.indexOf(p) >= 0))
    return every.length ? every : any
  }

  /* ---------- Anzeige von Werten */
  function daysText(v) {
    const s = [...new Set((Array.isArray(v) ? v : []).map(Number))].sort()
    const k = s.join(',')
    if (s.length === 7) return 'täglich'
    if (!s.length) return 'nie'
    return k === '0,1,2,3,4' ? 'Mo–Fr' : k === '5,6' ? 'Sa–So' : s.map(d => HO.dayNames[d]).join(', ')
  }
  // Duration in seconds as a short text ("5 Min.", "2 Std.", "1 Min. 30 s").
  function durText(v) {
    const s = Math.round(Number(v) || 0)
    if (!s) return '0'
    if (s % 3600 === 0) return s / 3600 + ' Std.'
    if (s % 60 === 0) return s / 60 + ' Min.'
    return s < 60 ? s + ' s' : Math.floor(s / 60) + ' Min. ' + (s % 60) + ' s'
  }
  const MOMENT = /^(sunrise|sunset)([+-]\d{1,3})?$/
  // Make a point in time readable: "Sonnenaufgang +15 Min." instead of "sunrise+15"; a time of day stays as it is.
  function momentText(v) {
    const m = MOMENT.exec(v || '')
    if (!m) return v || '–'
    const off = parseInt(m[2] || '0', 10)
    return (m[1] === 'sunrise' ? 'Sonnenaufgang' : 'Sonnenuntergang') + (off ? ` ${off > 0 ? '+' : '−'}${Math.abs(off)} Min.` : '')
  }
  // Device selection as text: rooms, groups and devices by name, with many devices only the number.
  function targetText(t, f) {
    t = t || {}
    const parts = []
    if (t.all) parts.push(f.all || 'alle')
    ;(t.rooms || []).forEach(r => parts.push(roomName(r)))
    ;(t.groups || []).forEach(g => parts.push((groupOf(g) || {}).name || 'Gruppe fehlt'))
    const devs = t.devices || []
    if (devs.length && devs.length <= 2 && !parts.length) devs.forEach(d => parts.push((devOf(d) || {}).name || 'Gerät fehlt'))
    else if (devs.length) parts.push(devs.length + ' Gerät' + (devs.length > 1 ? 'e' : ''))
    return parts.length ? parts.join(', ') : (f.optional ? (f.empty || 'alle') : '…')
  }
  const numText = (v, unit) => v == null || v === '' ? '' : HO.num(v) + (unit ? (unit[0] === '.' ? '' : ' ') + unit : '')
  // Buttons of the devices chosen in the node (trigger "Taster"), each id once – in the order the devices name them.
  function buttonsOf(n) {
    const t = FS.value(n, 'target') || {}, seen = {}, out = []
    ;(t.devices || []).map(devOf).filter(Boolean).forEach(d => (d.buttons || []).forEach(b => {
      if (!seen[b.id]) { seen[b.id] = true; out.push(b) }
    }))
    return out
  }
  // Value of a field the way it appears in the summary on the node – depending on the type of the field.
  function showVal(f, v, n) {
    const prop = FS.props[FS.value(n, 'prop')] || {}
    switch (f.widget) {
      case 'target': return targetText(v, f)
      // yes/no properties are already named by the state ("Bewegung erkannt") – otherwise the word would appear twice
      case 'property': return !v ? '…' : !FS.props[v] ? 'Eigenschaft fehlt' : FS.props[v].type === 'bool' ? '' : FS.props[v].label
      case 'propbool': return (v === 'no' ? prop.no : prop.yes) || ''
      case 'propnum': return numText(v == null ? prop.default : v, prop.unit)
      case 'moment': return momentText(v)
      case 'weekday': return daysText(v)
      case 'select': return ((f.options || []).find(o => o[0] === String(v)) || [0, v == null ? '' : v])[1]
      case 'duration':
        if (f.key === 'hold_on') return Number(v) ? 'beginnt nach ' + durText(v) : ''
        if (f.key === 'hold_off') return Number(v) ? 'endet nach ' + durText(v) : ''
        return durText(v)
      case 'number': return numText(v, f.unit)
      case 'rooms': return v && v.length ? v.map(roomName).join(', ') : 'alle Räume'
      case 'routine': return v ? ((D.routines.find(r => r.key === v) || {}).name || 'Routine fehlt') : '…'
      case 'flow': return v ? ((D.flows.find(r => r.id === v) || {}).name || 'Flow fehlt') : (f.optional ? 'jeder Flow' : '…')
      case 'scene': return v ? ((D.scenes.find(x => x.id === v) || {}).name || 'Szene fehlt') : (f.optional ? 'jede Szene' : '…')
      case 'button': return v ? ((buttonsOf(n).find(b => b.id === v) || {}).name || 'Taste ' + v) : 'jede Taste'
      case 'color': return v || ''
      case 'toggle': return v ? 'ja' : 'nein'
      case 'time': return v || '–'
      default: return v == null ? '' : String(v)
    }
  }
  const cfgVal = (n, f) => { const v = (n.config || {})[f.key]; return v === undefined ? f.default : v }
  // Summary of a node from the template of its catalogue entry; hidden fields drop out.
  function summary(n, sp) {
    const tpl = sp.summary && typeof sp.summary === 'object' ? sp.summary[FS.role(n)] : sp.summary
    if (!tpl) return ''
    return tpl.replace(/\{(\w+)\}/g, (_, k) => {
      const f = FS.field(sp, k)
      return f && FS.when(n, f.when) ? showVal(f, cfgVal(n, f), n) : ''
    }).replace(/\s+/g, ' ').replace(/(?: ·)+ ·/g, ' ·').replace(/^[\s·]+|[\s·]+$/g, '')
  }
  // Shortcoming the editor shows on the card (and that colours the cog orange)
  function problem(n, sp) {
    const edges = E ? E.flow.edges : []
    for (const f of sp.fields || []) {
      const v = cfgVal(n, f)
      if (!FS.when(n, f.when)) continue
      if (f.widget === 'target') {
        if (!f.optional && targetEmpty(v)) return 'Ziel wählen'
        if (v && (v.devices || []).length && D.devices.length && !(v.devices || []).some(devOf) && !(v.rooms || []).length && !(v.groups || []).length) return 'Gerät nicht gefunden'
      }
      if (f.widget === 'property' && !v) return 'Eigenschaft wählen'
      if (f.widget === 'routine' && !v) return 'Routine wählen'
      if (f.widget === 'flow' && !f.optional && !v) return 'Flow wählen'
      if (f.widget === 'scene' && !f.optional && !v) return 'Szene wählen'
      if (f.widget === 'scene' && v && D.scenes.length && !D.scenes.some(x => x.id === v)) return 'Szene fehlt'
      if (f.widget === 'var' && !String(v || '').trim()) return 'Namen eingeben'
      if (f.required && !String(v || '').trim()) return 'Adresse eingeben'
    }
    const needsCond = n.type === 'hold' || n.type === 'not' || (n.type === 'loop' && FS.value(n, 'mode') === 'while')
    if (needsCond && !edges.some(e => e.to === n.id && FS.split(String(e.toPort))[0] === 'c')) return 'Bedingung anschließen'
    return ''
  }
  // Does a setting that is not visible on the card differ from the default? (cog filled)
  function customized(n, sp) {
    const tpl = sp.summary && typeof sp.summary === 'object' ? sp.summary[FS.role(n)] : sp.summary
    const shown = new Set(((tpl || '').match(/\{(\w+)\}/g) || []).map(s => s.slice(1, -1)))
    return (sp.fields || []).some(f => {
      const v = (n.config || {})[f.key]
      if (f.widget === 'token' || v === undefined || !FS.when(n, f.when)) return false
      if (shown.has(f.key) && !f.advanced && Number(v) !== 0) return false
      return f.default !== undefined ? !same(v, f.default) : !(v === null || v === '' || (typeof v === 'object' && targetEmpty(v)))
    })
  }
  // Defaults for new nodes
  function defaults(sp) {
    const c = {}
    if (sp.roles) c.role = Object.keys(sp.roles)[0]
    for (const f of sp.fields || []) {
      if (['token', 'target', 'property', 'propnum'].indexOf(f.widget) >= 0) continue
      if (f.widget === 'scene') { if (!f.optional && D.scenes[0]) c[f.key] = D.scenes[0].id; continue }
      if (f.default !== undefined) c[f.key] = clone(f.default)
    }
    return c
  }
  const isTrigger = n => FS.role(n) === 'trigger' || n.type === 'hold'
  // One sentence that describes the whole flow: "Wenn Auslöser → Aktionen" (for the list).
  function describe(f) {
    const nodes = f.nodes || [], part = n => { const sp = byType[n.type], s = sp ? summary(n, sp) : ''; return s ? `${sp.label} (${s})` : (sp ? sp.label : n.type) }
    const trig = nodes.filter(n => byType[n.type] && isTrigger(n)).map(part)
    const acts = nodes.filter(n => (byType[n.type] || {}).category === 'action' && n.type !== 'timer').map(part)
    if (!trig.length && !acts.length) return 'Noch leer – im Editor Bausteine hinzufügen.'
    const s = `Wenn ${trig.join(' oder ') || '…'} → ${acts.join(', ') || '…'}`
    return s.length > 160 ? s.slice(0, 157) + '…' : s
  }

  /* ---------- Field widgets (one function per field type; new nodes need no editor code) */
  const opt = (v, label, sel) => `<option value="${HO.esc(v)}" ${sel ? 'selected' : ''}>${HO.esc(label)}</option>`
  const seg = (opts, v) => `<div class="seg">${opts.map(([k, l]) => `<button type="button" data-opt="${HO.esc(k)}" class="${String(v) === k ? 'active' : ''}">${HO.esc(l)}</button>`).join('')}</div>`
  const UNITS = [[1, 'Sek.'], [60, 'Min.'], [3600, 'Std.']]
  // number with a possible minus: text field + "±" – the iOS numeric keyboard has no minus
  const numInput = (f, v) => (f.min == null || f.min < 0)
    ? `<input type="text" inputmode="decimal" autocomplete="off" value="${HO.esc(v == null ? '' : String(v).replace('.', ','))}"><button type="button" class="btn sm fl-sign" data-sign aria-label="Vorzeichen wechseln" title="Vorzeichen wechseln">±</button>`
    : `<input type="number" inputmode="decimal" value="${HO.esc(v == null ? '' : v)}" min="${f.min}" ${f.max != null ? `max="${f.max}"` : ''} step="${f.step || 1}">`
  const WIDGETS = {
    // Field "Geräte": selection from all lamps/devices, rooms, groups and single devices – only what fits the
    // node is offered.
    target(f, v, n) {
      const t = v || {}, kinds = fieldKinds(f, n), wide = f.wide_kinds || kinds
      const fits = (d, ks) => (!ks || ks.indexOf(d.kind) >= 0) && (!f.props || caps(d).length > 0) && (!f.buttons || (d.buttons || []).length > 0)
      const list = D.devices.filter(d => fits(d, kinds))
      const rooms = D.rooms.filter(r => D.devices.some(d => d.room_id === r.room_id && fits(d, wide)))
      const groups = D.groups.filter(g => (g.members || []).some(m => { const d = devOf(m); return d && fits(d, kinds) }))
      const selD = new Set(t.devices || []), selR = new Set(t.rooms || []), selG = new Set(t.groups || [])
      const gone = (t.devices || []).filter(id => !devOf(id)).length
      const names = [...new Set(list.map(d => d.room || 'Ohne Raum'))].sort(HO.byName)
      return `<div class="fl-target">
        ${f.all && FS.when(n, f.all_when) ? `<div class="chips"><span class="chip ${t.all ? 'active' : ''}" data-tall>${HO.esc(f.all)}</span></div>` : ''}
        ${rooms.length ? `<div class="fl-tlabel">Räume</div><div class="chips">${rooms.map(r => `<span class="chip ${selR.has(r.room_id) ? 'active' : ''}" data-troom="${HO.esc(r.room_id)}">${HO.esc(r.name)}</span>`).join('')}</div>` : ''}
        ${groups.length ? `<div class="fl-tlabel">Gerätegruppen</div><div class="chips">${groups.map(g => `<span class="chip ${selG.has(g.id) ? 'active' : ''}" data-tgroup="${HO.esc(g.id)}">${HO.esc(g.name || 'Gruppe')}</span>`).join('')}</div>` : ''}
        <div class="fl-tlabel">Einzelne Geräte${selD.size ? ` (${selD.size} gewählt)` : ''}</div>
        ${names.length > 1 ? `<select class="fl-dfilter">${opt('', 'Alle Räume', true)}${names.map(r => opt(r, r)).join('')}</select>` : ''}
        <div class="fl-devlist">${list.map(d =>
          `<label class="check fl-devitem" data-did="${HO.esc(d.id)}" data-room="${HO.esc(d.room || 'Ohne Raum')}"><input type="checkbox" ${selD.has(d.id) ? 'checked' : ''}><span>${HO.esc(d.name)}<span class="muted small"> · ${HO.esc(d.room || 'Ohne Raum')}</span></span></label>`).join('')}</div>
        ${!list.length ? '<span class="hint">Keine passenden Geräte gefunden (Plugin verbunden?).</span>' : ''}
        ${gone ? `<span class="hint">${gone} gewählte${gone === 1 ? 's Gerät gibt' : ' Geräte gibt'} es nicht mehr.</span>` : ''}
        ${f.hint ? `<span class="hint">${HO.esc(f.hint)}</span>` : ''}</div>`
    },
    // Field "Eigenschaft": offered is what the chosen devices report; a choice that no longer exists stays visible.
    property(f, v, n) {
      const avail = propsFor(n)
      const missing = v && avail.indexOf(v) < 0 ? opt(v, ((FS.props[v] || {}).label || v) + ' (bei diesen Geräten nicht vorhanden)', true) : ''
      return `<select>${opt('', '— Eigenschaft wählen —', !v)}${missing}${avail.map(p => opt(p, FS.props[p].label, p === v)).join('')}</select>
        <span class="hint">Angeboten wird, was die gewählten Geräte melden.</span>`
    },
    propbool(f, v, n) {
      const p = FS.props[FS.value(n, 'prop')] || {}
      return seg([['yes', p.yes || 'ja'], ['no', p.no || 'nein']], v || 'yes')
    },
    propnum(f, v, n) {
      const p = FS.props[FS.value(n, 'prop')] || {}
      return `<div class="fl-num">${numInput(p, v == null ? p.default : v)}${p.unit ? `<span>${HO.esc(p.unit)}</span>` : ''}</div>`
    },
    // Field "Räume": chips for selecting and deselecting; without a selection the node applies to all.
    rooms(f, v) {
      const sel = new Set(v || [])
      return D.rooms.length ? `<div class="chips">${D.rooms.map(r => `<span class="chip ${sel.has(r.room_id) ? 'active' : ''}" data-room="${HO.esc(r.room_id)}">${HO.esc(r.name)}</span>`).join('')}</div>`
        : '<span class="hint">Keine Räume bekannt – gilt für alle.</span>'
    },
    time(f, v) { return `<input type="time" value="${HO.esc(v || '')}" step="60">` },
    // Field "Zeitpunkt": time of day or sunrise/sunset with an offset in minutes.
    moment(f, v) {
      const m = MOMENT.exec(v || ''), kind = m ? m[1] : 'time', off = m ? parseInt(m[2] || '0', 10) : 0
      return `${seg([['time', 'Uhrzeit'], ['sunrise', 'Sonnenaufgang'], ['sunset', 'Sonnenuntergang']], kind)}
        <div class="fl-num fl-mtime" ${kind === 'time' ? '' : 'hidden'}><input type="time" value="${HO.esc(m ? '18:00' : (v || '18:00'))}" step="60"></div>
        <div class="fl-num fl-moff" ${kind === 'time' ? 'hidden' : ''}>${numInput({}, off)}<span>Min. Versatz</span></div>`
    },
    // Field "Wochentage": one chip per day, plus "Werktage", "Wochenende" and "Alle".
    weekday(f, v) {
      const sel = new Set((v || []).map(Number))
      return `<div class="chips">${HO.dayNames.map((n, d) => `<span class="chip ${sel.has(d) ? 'active' : ''}" data-day="${d}">${n}</span>`).join('')}</div>
        <div class="chips" style="margin-top:6px"><span class="chip" data-days="0,1,2,3,4">Werktage</span><span class="chip" data-days="5,6">Wochenende</span><span class="chip" data-days="0,1,2,3,4,5,6">Alle</span></div>`
    },
    number(f, v) {
      return `<div class="fl-num">${numInput(f, v)}${f.unit ? `<span>${HO.esc(f.unit)}</span>` : ''}</div>`
    },
    // Field "Dauer": number with a unit – shown in the largest unit that divides evenly.
    duration(f, v) {
      const s = Math.round(Number(v) || 0), u = [...UNITS].reverse().find(([k]) => s && s % k === 0) || UNITS[1]
      return `<div class="fl-num"><input type="number" inputmode="numeric" min="0" step="1" value="${s ? s / u[0] : 0}">
        <select>${UNITS.map(([k, l]) => opt(k, l, k === u[0])).join('')}</select></div>`
    },
    select(f, v) {
      const o = f.options || []
      return o.length <= 3 && o.every(x => x[1].length <= 16) ? seg(o, v) : `<select>${o.map(([k, l]) => opt(k, l, String(v) === k)).join('')}</select>`
    },
    toggle(f, v) { return `<label class="check"><input type="checkbox" ${v ? 'checked' : ''}><span>Ein</span></label>` },
    text(f, v) { return `<input type="text" value="${HO.esc(v)}" maxlength="${f.max || 120}" autocomplete="off">` },
    textarea(f, v) { return `<textarea rows="3" maxlength="${f.max || 2000}">${HO.esc(v)}</textarea>` },
    routine(f, v) {
      return `<select>${opt('', '— Routine wählen —', !v)}${D.routines.map(r => opt(r.key, r.name + (r.active ? ' (aktiv)' : ''), r.key === v)).join('')}</select>`
    },
    flow(f, v) {
      const own = E && E.flow ? E.flow.id : null
      return `<select>${opt('', f.optional ? 'Jeder Flow' : '— Flow wählen —', !v)}${D.flows.filter(x => x.id !== own && !x.outdated).map(x => opt(x.id, x.name, x.id === v)).join('')}</select>`
    },
    var(f, v) {
      return `<input type="text" value="${HO.esc(v)}" maxlength="40" list="flVars" autocomplete="off">
        <datalist id="flVars">${Object.keys(D.vars).map(k => `<option value="${HO.esc(k)}"></option>`).join('')}</datalist>`
    },
    // Field "Szene": selection of the scenes; a deleted one stays visible as "Szene fehlt".
    scene(f, v) {
      const missing = v && !D.scenes.some(x => x.id === v) ? opt(v, 'Szene fehlt', true) : ''
      return `<select>${opt('', f.optional ? 'Jede Szene' : '— Szene wählen —', !v)}${missing}${D.scenes.map(x => opt(x.id, x.name, x.id === v)).join('')}</select>
        ${!D.scenes.length ? '<span class="hint">Noch keine Szenen – unter „Szenen“ anlegen.</span>' : ''}`
    },
    // Field "Taste" of the trigger "Taster": the buttons of the chosen devices; a choice that no longer exists stays visible.
    button(f, v, n) {
      const list = buttonsOf(n)
      const missing = v && !list.some(b => b.id === v) ? opt(v, `Taste ${v} (bei diesen Tastern nicht vorhanden)`, true) : ''
      return `<select>${opt('', 'Jede Taste', !v)}${missing}${list.map(b => opt(b.id, b.name, b.id === v)).join('')}</select>
        <span class="hint">${list.length ? 'Angeboten werden die Tasten der einzeln gewählten Taster.' : 'Erst oben einen Taster wählen.'}</span>`
    },
    color(f, v) {
      return `<div class="row"><input type="color" value="${HO.esc(/^#[0-9a-f]{6}$/i.test(v || '') ? v : '#ffffff')}" style="width:56px;height:36px;padding:2px;border-radius:10px">
        <span class="hint">Lampen ohne Farbe bekommen den passenden Weißton.</span></div>`
    },
    // Field "Adresse" of a webhook trigger: for copying, with an example call.
    token(f, v) {
      if (!v) return '<span class="hint">Die Adresse entsteht beim Speichern (einen Moment warten und erneut öffnen).</span>'
      const url = `${location.origin}/api/flows/hook/${v}`
      return `<div class="fl-token">${HO.esc(url)}</div><div class="row" style="margin-top:6px"><button type="button" class="btn sm" data-copy="${HO.esc(url)}">${HO.icon('copy', 'sm')} Kopieren</button>
        <span class="hint">Aufruf: POST, z. B. <code>curl -X POST ${HO.esc(url)}</code></span></div>`
    }
  }
  function fieldHTML(f, n) {
    const cfg = n.config || {}, v = cfg[f.key] === undefined ? f.default : cfg[f.key]
    return `<div class="field fl-field" data-k="${HO.esc(f.key)}" data-w="${f.widget}" ${FS.when(n, f.when) ? '' : 'hidden'}><span>${HO.esc(f.label)}${f.pro ? ' ' + HO.proTag('flow_pro_nodes') : ''}</span>${(WIDGETS[f.widget] || WIDGETS.text)(f, v, n)}</div>`
  }
  // All fields of a node: with several roles first "Arbeitet als", then the fields, rarely used ones under
  // "Erweitert".
  function fieldsHTML(sp, n) {
    const roles = FS.roles(sp), role = FS.role(n)
    const main = (sp.fields || []).filter(f => !f.advanced), adv = (sp.fields || []).filter(f => f.advanced)
    const advShown = adv.some(f => FS.when(n, f.when))
    return (roles.length > 1 ? `<div class="field fl-field fl-rolebox" data-k="role" data-w="role"><span>Arbeitet als</span>${seg(roles.map(r => [r, SPECS.roles[r].label]), role)}
        <span class="hint">${HO.esc(role === 'trigger' ? 'Auslöser: startet den Ablauf von sich aus.' : 'Bedingung: sagt Ja oder Nein und wird an „Wenn / Dann“ angeschlossen.')}</span></div>` : '')
      + main.map(f => fieldHTML(f, n)).join('')
      + (adv.length ? `<details class="fl-adv" ${advShown ? '' : 'hidden'}><summary>Feinheiten</summary>${adv.map(f => fieldHTML(f, n)).join('')}</details>` : '')
  }
  // Read the value of a field. undefined = invalid input (marked red, the old value stays)
  function readNumber(inp, f) {
    const raw = String(inp.value).trim().replace(',', '.').replace('−', '-')
    if (raw === '') { inp.classList.remove('invalid'); return f.default == null ? 0 : f.default }
    if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(raw)) { inp.classList.add('invalid'); return undefined }
    inp.classList.remove('invalid')
    return Math.min(f.max == null ? 1e9 : f.max, Math.max(f.min == null ? -1e9 : f.min, Number(raw)))
  }
  // Read the value of a field from the dialog – depending on the type of the field.
  function readField(box, f, n) {
    const q = s => box.querySelector(s)
    switch (f.widget) {
      case 'property': case 'routine': case 'flow': case 'scene': case 'button': return q('select').value || null
      case 'color': return q('input').value
      case 'target': {
        const t = {}, pick = (sel, key) => [...box.querySelectorAll(sel)].map(c => c.dataset[key])
        const devs = [...box.querySelectorAll('.fl-devitem input:checked')].map(c => c.closest('.fl-devitem').dataset.did)
        const rooms = pick('[data-troom].active', 'troom'), groups = pick('[data-tgroup].active', 'tgroup')
        if (devs.length) t.devices = devs
        if (rooms.length) t.rooms = rooms
        if (groups.length) t.groups = groups
        if (q('[data-tall].active')) t.all = true
        return t
      }
      case 'rooms': return [...box.querySelectorAll('[data-room].active')].map(c => c.dataset.room)
      case 'weekday': return [...box.querySelectorAll('[data-day].active')].map(c => +c.dataset.day)
      case 'number': return readNumber(q('input'), f)
      case 'propnum': return readNumber(q('input'), FS.props[FS.value(n, 'prop')] || {})
      case 'duration': {
        const x = parseFloat(String(q('input').value).replace(',', '.'))
        const s = Math.round((isNaN(x) ? 0 : x) * Number(q('select').value))
        return Math.min(f.max == null ? 864000 : f.max, Math.max(f.min == null ? 0 : f.min, s))
      }
      case 'moment': {
        const kind = (q('[data-opt].active') || {}).dataset ? q('[data-opt].active').dataset.opt : 'time'
        if (kind === 'time') return q('.fl-mtime input').value || '18:00'
        const off = readNumber(q('.fl-moff input'), { min: -180, max: 180, default: 0 })
        if (off === undefined) return undefined
        return kind + (off ? (off > 0 ? '+' : '') + Math.round(off) : '')
      }
      case 'role': case 'propbool':
      case 'select': { const a = q('[data-opt].active'); return a ? a.dataset.opt : (q('select') ? q('select').value : f.default) }
      case 'toggle': return q('input').checked
      case 'textarea': return q('textarea').value
      default: return q('input') ? q('input').value.trim() : undefined
    }
  }
  // Read all visible fields of the dialog as the settings of the node.
  function readAll(body, sp, n) {
    const out = {}
    const all = (FS.roles(sp).length > 1 ? [{ key: 'role', widget: 'role' }] : []).concat(sp.fields || [])
    for (const f of all) {
      if (f.widget === 'token') continue
      const box = body.querySelector(`.fl-field[data-k="${CSS.escape(f.key)}"]`)
      if (!box || box.hidden) continue
      const v = readField(box, f, n)
      if (v !== undefined) out[f.key] = v
    }
    return out
  }
  // bring dependent values up to date: property fitting the devices, devices fitting the action
  function settle(n, sp, before) {
    const cfg = n.config
    for (const f of sp.fields || []) {
      if (f.widget === 'target' && f.kinds_by && cfg[f.key] && before[f.kinds_by.key] !== cfg[f.kinds_by.key]) {
        const kinds = fieldKinds(f, n), t = cfg[f.key]
        if (kinds && t.devices) { t.devices = t.devices.filter(id => { const d = devOf(id); return d && kinds.indexOf(d.kind) >= 0 }); if (!t.devices.length) delete t.devices }
        if (!FS.when(n, f.all_when)) delete t.all
      }
      if (f.widget === 'property') {
        const avail = propsFor(n), tf = (sp.fields || []).find(x => x.widget === 'target')
        if (tf && !targetEmpty(cfg[tf.key]) && (!cfg[f.key] || avail.indexOf(cfg[f.key]) < 0)) cfg[f.key] = avail[0] || null
        if (before[f.key] !== cfg[f.key]) delete cfg.value       // new property: its default value applies
      }
    }
  }

  /* ---------- Templates (ready-made graphs; devices are pre-filled with the first fitting one) */
  const N = (type, x, y, config = {}) => ({ type, x: x * 280, y: y * 170, config })
  const T = kinds => ({ __dev: kinds })          // target: first device of this kind
  const DARK = { role: 'condition', from: 'sunset', to: 'sunrise' }
  const MOTION = { role: 'trigger', target: T(['motion']), prop: 'motion', is: 'yes' }
  const LAMP = T(['light', 'switch'])
  const TEMPLATES = [
    { name: 'Licht an, solange Bewegung da ist', desc: 'Bewegung beginnt → Licht an; 2 Minuten ohne Bewegung → Licht aus. Bleibt an, solange jemand da ist.',
      nodes: [N('device_state', 0, 0, { ...MOTION, hold_off: 120 }), N('set_device', 1, 0, { do: 'switch', target: LAMP, to: 'on' }),
        N('set_device', 1, 1, { do: 'switch', target: LAMP, to: 'off' })],
      edges: [[0, 'out', 1, 'in'], [0, 'end', 2, 'in']] },
    { name: 'Flurlicht bei Bewegung – nur wenn es dunkel ist', desc: 'Wie oben, aber eingeschaltet wird nur zwischen Sonnenuntergang und Sonnenaufgang',
      nodes: [N('device_state', 0, 0, { ...MOTION, hold_off: 120 }), N('when', 1, 0), N('time', 0, 1, DARK),
        N('set_device', 2, 0, { do: 'switch', target: LAMP, to: 'on' }), N('set_device', 2, 1, { do: 'switch', target: LAMP, to: 'off' })],
      edges: [[0, 'out', 1, 'in'], [2, 'val', 1, 'c1'], [1, 'yes', 3, 'in'], [0, 'end', 4, 'in']] },
    { name: 'Alles aus um Mitternacht', desc: 'Jeden Tag um 0:00 alle Lampen aus',
      nodes: [N('time', 0, 0, { role: 'trigger', at: '00:00' }), N('set_device', 1, 0, { do: 'switch', target: { all: true }, to: 'off' })],
      edges: [[0, 'out', 1, 'in']] },
    { name: 'Abwesend → alle Lichter aus', desc: 'Wechselt ComfyOS auf Abwesend, gehen alle Lampen aus',
      nodes: [N('presence', 0, 0, { role: 'trigger', state: 'AWAY' }), N('set_device', 1, 0, { do: 'switch', target: { all: true }, to: 'off' })],
      edges: [[0, 'out', 1, 'in']] },
    { name: 'Heizung runter, solange das Fenster offen ist', desc: 'Fenster auf → Thermostat 12 °C; Fenster zu → wieder 20 °C',
      nodes: [N('device_state', 0, 0, { role: 'trigger', target: T(['contact']), prop: 'open', is: 'yes', hold_on: 60 }),
        N('set_device', 1, 0, { do: 'temp', target: T(['thermostat']), temp_mode: 'abs', temp: 12 }),
        N('set_device', 1, 1, { do: 'temp', target: T(['thermostat']), temp_mode: 'abs', temp: 20 })],
      edges: [[0, 'out', 1, 'in'], [0, 'end', 2, 'in']] },
    { name: 'Begrüßungslicht an der Haustür', desc: 'Tür öffnet im Dunkeln → Licht an, nach 10 Minuten wieder aus',
      nodes: [N('device_state', 0, 0, { role: 'trigger', target: T(['contact']), prop: 'open', is: 'yes' }), N('when', 1, 0), N('time', 0, 1, DARK),
        N('set_device', 2, 0, { do: 'switch', target: LAMP, to: 'on' }), N('timer', 3, 0, { wait: 600 }), N('set_device', 4, 0, { do: 'switch', target: LAMP, to: 'off' })],
      edges: [[0, 'out', 1, 'in'], [2, 'val', 1, 'c1'], [1, 'yes', 3, 'in'], [3, 'out', 4, 'in'], [4, 'out', 5, 'in']] },
    { name: 'Licht per Play-Knopf umschalten', desc: 'Ein Knopf in der Flow-Liste schaltet die Lampe um',
      nodes: [N('play', 0, 0, { role: 'trigger', label: 'Licht' }), N('set_device', 1, 0, { do: 'switch', target: LAMP, to: 'toggle' })],
      edges: [[0, 'out', 1, 'in']] },
    { name: 'Anwesenheitssimulation', desc: 'Abends bei Abwesenheit zufällig eine Lampe schalten – wirkt bewohnt',
      nodes: [N('interval', 0, 0, { role: 'trigger', every: 1800, from: '18:00', to: '23:30' }), N('when', 1, 0), N('presence', 0, 1, { role: 'condition', state: 'AWAY' }),
        N('random', 2, 0, { mode: 'chance', p: 60 }), N('random', 3, 0, { mode: 'delay', min: 60, max: 900 }),
        N('set_device', 4, 0, { do: 'switch', target: LAMP, to: 'toggle' })],
      edges: [[0, 'out', 1, 'in'], [2, 'val', 1, 'c1'], [1, 'yes', 3, 'in'], [3, 'o1', 4, 'in'], [4, 'o1', 5, 'in']] },
    { name: 'Batterie-Warnung', desc: 'Wird eine Batterie schwach, kommt eine Benachrichtigung',
      nodes: [N('device_signal', 0, 0, { role: 'trigger', signal: 'battery', below: 15 }), N('notify', 1, 0, { text: 'Batterie schwach: {device} ({value})', toast: true, push: true })],
      edges: [[0, 'out', 1, 'in']] },
    { name: 'Sensor-Watchdog', desc: 'Stündlich prüfen, ob ein Sensor seit 6 Stunden nichts gemeldet hat (höchstens einmal am Tag melden)',
      nodes: [N('interval', 0, 0, { role: 'trigger', every: 3600 }), N('when', 1, 0), N('stale', 0, 1, { target: T(['thermostat', 'sensor', 'motion', 'contact']), for: 21600 }),
        N('rate', 2, 0, { mode: 'throttle', wait: 86400 }), N('notify', 3, 0, { text: 'Ein Sensor meldet seit 6 Stunden nichts – Batterie/Funk prüfen', toast: true, push: true })],
      edges: [[0, 'out', 1, 'in'], [2, 'val', 1, 'c1'], [1, 'yes', 3, 'in'], [3, 'out', 4, 'in']] },
    { name: 'Fehler-Alarm', desc: 'Scheitert eine Automatisierung, kommt sofort ein Hinweis',
      nodes: [N('flow_error', 0, 0, { role: 'trigger' }), N('notify', 1, 0, { text: 'Flow „{trigger}“: {value}', toast: true, push: true })], edges: [[0, 'out', 1, 'in']] }
  ]
  // Create a flow from a template: placeholders for devices are filled with the first fitting device.
  function fromTemplate(t) {
    // Replace a placeholder of the template by a fitting device (empty if there is none).
    const fill = c => {
      for (const k of Object.keys(c)) {
        if (c[k] && c[k].__dev) { const d = D.devices.find(x => c[k].__dev.indexOf(x.kind) >= 0); c[k] = d ? { devices: [d.id] } : {} }
      }
      return c
    }
    const nodes = t.nodes.map((n, i) => ({ id: 'n' + (i + 1), type: n.type, x: n.x, y: n.y, config: fill({ ...defaults(byType[n.type]), ...clone(n.config) }) }))
    const edges = t.edges.map(([a, ap, b, bp], i) => ({ id: 'e' + (i + 1), from: nodes[a].id, fromPort: ap, to: nodes[b].id, toPort: bp }))
    return { name: t.name, nodes, edges }
  }

  /* ---------- Live connection (SSE): steps of the engine for list and editor */
  let stream = null, onFlow = null
  // Subscribe to the live messages of the flows (one subscription for list and editor).
  function openStream(handler) {
    onFlow = handler
    if (stream) return
    stream = HO.live.on(m => {
      if (m.type === 'toast') return HO.toast(m.text, 'ok', 6000)
      if (m.type === 'flow' && onFlow) onFlow(m)
    })
  }
  // Abo der Live-Meldungen beenden.
  function closeStream() { onFlow = null; if (stream) { stream(); stream = null } }

  /* ---------- Liste */
  let gen = 0
  const L = { fired: {}, refreshT: null }
  // Open a flow in the editor (the id survives a reload of the page).
  function openEditor(id) {
    HO.state.flowId = id
    ss.set('ho.flowId', id)
    location.hash = 'flowEdit'
  }
  // What a flow is doing right now, as a short line: still waiting, counting, holding a state.
  function liveLine(f) {
    const live = f.live || {}, ids = Object.keys(live)
    const until = ids.map(k => live[k].until).filter(Boolean).sort()[0]
    if (until) {
      const s = Math.max(0, Math.round(until - Date.now() / 1000))
      return `wartet noch ${s >= 60 ? Math.round(s / 60) + ' Min.' : s + ' s'}`
    }
    if (ids.some(k => live[k].round)) return 'Schleife läuft'
    return ids.some(k => live[k].on) ? 'Auslöser-Zustand gilt gerade' : ''
  }
  // Card of a flow in the list: name, description, switch, last run or error – outdated flows only with a
  // hint and "Löschen".
  function flowCardHTML(f) {
    const info = f.info || {}, id = HO.esc(f.id)
    if (f.outdated) {
      return `<div class="card fl-item outdated" data-flow="${id}">
        <div class="row" style="align-items:flex-start"><div class="grow"><b>${HO.esc(f.name)}</b> <span class="badge warn">Veraltet</span>
          <div class="small muted fl-desc">Aus einer älteren ComfyOS-Version – läuft nicht mehr und lässt sich nicht bearbeiten. Bitte neu bauen und diesen Eintrag löschen.</div></div>
          <button class="btn sm ghost danger" data-del="${id}">${HO.icon('trash', 'sm')} Löschen</button></div></div>`
    }
    const plays = (f.nodes || []).filter(n => n.type === 'play' && FS.role(n) === 'trigger')
    const live = liveLine(f), fired = Date.now() - (L.fired[f.id] || 0) < 1800
    return `<div class="card fl-item ${f.enabled ? '' : 'off'} ${fired ? 'fired' : ''} ${live ? 'live' : ''}" data-open="${id}" data-flow="${id}">
      <div class="row" style="align-items:flex-start"><div class="grow"><b>${HO.esc(f.name)}</b>${f.paused ? ' <span class="badge warn">Pausiert</span>' : ''}
        <div class="small muted fl-desc">${HO.esc(f.description || describe(f))}</div></div>
        <button class="room-switch ${f.enabled ? 'on' : ''}" data-toggle="${id}" role="switch" aria-checked="${!!f.enabled}" aria-label="${HO.esc(f.name)} ein/aus"></button>
        <button class="btn-icon sm plain" data-menu="${id}" aria-label="Weitere Aktionen">${HO.icon('more')}</button></div>
      ${plays.length && f.enabled ? `<div class="chips">${plays.map(n => `<button class="chip fl-btn" data-play="${id}" data-node="${HO.esc(n.id)}">${HO.icon('play', 'sm')}${HO.esc((n.config || {}).label || 'Los')}</button>`).join('')}</div>` : ''}
      <div class="tiny muted fl-status">${live ? `<span class="fl-run"><i></i>${HO.esc(live)}</span> · ` : ''}${info.last_run ? `Zuletzt ${HO.relTime(info.last_run)}${info.last_trigger ? ' · ' + HO.esc(info.last_trigger) : ''}` : 'Noch nie ausgelöst'}${info.last_error ? ` · <span class="fl-err">Fehler: ${HO.esc(info.last_error)}</span>` : ''}</div></div>`
  }
  /* ---------- Groups and order (since 0.21.1): order of the list = order in the store; flows without a group
     are at the top, every group below as a collapsible section (the device remembers collapsed groups). */
  const groupKey = 'ho.flowGroupsClosed'
  // Names of the collapsed groups of this device.
  function closedGroups() {
    try { return JSON.parse(localStorage.getItem(groupKey) || '[]') } catch (e) { return [] }
  }
  // Remember the opening or collapsing of a group.
  function rememberGroup(name, open) {
    const closed = closedGroups().filter(g => g !== name)
    if (!open) closed.push(name)
    try { localStorage.setItem(groupKey, JSON.stringify(closed.slice(-50))) } catch (e) { /* private window: just not remembered */ }
  }
  // Group names in the order in which their first flow appears in the list.
  const groupNames = flows => flows.map(f => f.group || '').filter((g, i, all) => g && all.indexOf(g) === i)
  // List of the flows: first those without a group, then one section per group with the number.
  function groupedHTML(flows) {
    const closed = closedGroups()
    const loose = flows.filter(f => !f.group)
    const sections = groupNames(flows).map(name => {
      const members = flows.filter(f => f.group === name)
      return `<details class="fl-group" data-group="${HO.esc(name)}" ${closed.indexOf(name) >= 0 ? '' : 'open'}>
        <summary><span class="fl-group-name">${HO.icon('layers', 'sm')} ${HO.esc(name)}</span><span class="badge">${members.length}</span></summary>
        <div class="fl-list">${members.map(flowCardHTML).join('')}</div></details>`
    })
    return `${loose.length ? `<div class="fl-list">${loose.map(flowCardHTML).join('')}</div>` : ''}${sections.join('')}`
  }
  // Put a flow into a group, create a new one or take it out of the group.
  function chooseGroup(page, f) {
    const names = groupNames(D.flows).filter(g => g !== f.group)
    const save = async group => {
      try { await HO.api(`flows/${encodeURIComponent(f.id)}/group`, { method: 'PUT', body: { group } }); renderList(page) } catch (e) { HO.toast(e.message, 'bad') }
    }
    const newGroup = async () => {
      const g = await HO.prompt('Neue Gruppe', '', { label: 'Name der Gruppe', ok: 'Übernehmen' })
      if (g) save(g)
    }
    HO.menu('Gruppe für „' + f.name + '“', names.map(g => ({ label: g, icon: 'layers', onClick: () => save(g) }))
      .concat([{ label: 'Neue Gruppe …', icon: 'plus', onClick: newGroup }],
        f.group ? [{ label: 'Aus der Gruppe nehmen', icon: 'x', onClick: () => save('') }] : []))
  }
  // Move a flow by one place within its group (or among the flows without a group).
  async function moveFlow(page, f, step) {
    const ids = D.flows.map(x => x.id)
    const mates = D.flows.filter(x => (x.group || '') === (f.group || '')).map(x => x.id)
    const other = mates[mates.indexOf(f.id) + step]
    if (!other) return
    const a = ids.indexOf(f.id), b = ids.indexOf(other)
    ids[a] = other; ids[b] = f.id
    try { await HO.api('flows/order', { method: 'PUT', body: { ids } }); renderList(page) } catch (e) { HO.toast(e.message, 'bad') }
  }
  // Is the flow at the very top (-1) or the very bottom (1) of its group?
  function atEdge(f, step) {
    const mates = D.flows.filter(x => (x.group || '') === (f.group || ''))
    return mates[step < 0 ? 0 : mates.length - 1] === f
  }

  // Page "Automatisierungen": head with templates and import, the cards of the flows, below them the variables.
  function listHTML(list) {
    const flows = list.flows || []
    const vars = Object.entries(list.vars || {})
    return `<div class="section-head"><div><h2 style="margin:0">Automatisierungen</h2>
        <div class="small muted">Laufen in ComfyOS, solange ComfyOS läuft (auf dem Pi rund um die Uhr). Hue reagiert sofort, Home-Base-Sensoren nach etwa 3 Sekunden.</div></div>
        <div class="row"><button class="btn sm" data-act="templates">${HO.icon('star', 'sm')} Vorlagen</button>
        <button class="btn sm" data-act="import">${HO.icon('upload', 'sm')} Import</button>
        <button class="btn sm primary" data-act="new">${HO.icon('plus', 'sm')} Neu</button></div></div>
      ${list.problems && list.problems.length ? `<div class="notice warn" style="margin-bottom:12px">${HO.icon('warn', 'sm')}<div>Bausteine unvollständig: ${HO.esc(list.problems.join('; '))}</div></div>` : ''}
      ${flows.length ? groupedHTML(flows)
      : `<div class="card empty">${HO.icon('workflow', 'lg')}<h3 style="margin:8px 0">Noch keine Automatisierungen</h3>
          <p class="muted">Baue aus Auslösern, Bedingungen und Aktionen eigene Abläufe – oder starte mit einer Vorlage.</p>
          <div class="row" style="justify-content:center;flex-wrap:wrap"><button class="btn primary" data-act="templates">${HO.icon('star', 'sm')} Vorlage wählen</button>
          <button class="btn" data-act="new">${HO.icon('plus', 'sm')} Leer beginnen</button></div></div>`}
      ${vars.length ? `<section class="card section"><div class="eyebrow" style="margin-bottom:8px">Variablen</div><div class="chips">${vars.map(([k, v]) =>
        `<span class="chip ${v === true ? 'active' : ''}" data-var="${HO.esc(k)}" title="${typeof v === 'boolean' ? 'Antippen zum Umschalten' : ''}">${HO.esc(k)}: <b>${typeof v === 'boolean' ? (v ? 'Ja' : 'Nein') : typeof v === 'number' ? HO.num(v) : HO.esc(v)}</b></span>`).join('')}</div></section>` : ''}`
  }
  /* ---------- Import / export (JSON, e.g. created by an AI) */
  function downloadJSON(obj, name) {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' }))
    a.download = name; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 2000)
  }
  // Dialog "Import": paste JSON or choose a file, have it checked, take it over – plus a template to copy for
  // working with an AI.
  async function importSheet(page) {
    const info = await HO.api('flows/import').catch(() => ({ nodes: [], properties: [], flows: [], devices: [] }))
    const devs = info.devices || []
    const first = kinds => { const d = devs.find(x => kinds.indexOf(x.kind) >= 0); return d ? d.id : kinds[0] + '-id' }
    const devLines = devs.map(d => `  "${d.id}"  ${d.name || ''}  [${d.kind}${d.room ? ', Raum ' + d.room + ' = "' + (d.room_id || '') + '"' : ''}]  Eigenschaften: ${(d.props || []).join(', ') || '–'}`).join('\n')
    const catLines = (info.nodes || []).map(n => {
      const ports = n.roles ? Object.keys(n.roles).map(r => `role=${r} out:[${n.roles[r].join(',')}]`).join(' | ') : `in:[${(n.inputs || []).join(',')}] out:[${(n.outputs || []).join(',')}]`
      const fields = (n.fields || []).map(f => f.key + (f.options ? '∈{' + f.options.join('|') + '}' : '') + (f.default != null && f.default !== '' ? '=' + JSON.stringify(f.default) : '')).join(', ')
      return `  "${n.type}" (${n.label})${n.pro ? ' [Pro]' : ''}  ${ports}  Felder:{${fields}}`
    }).join('\n')
    const propLines = (info.properties || []).map(p => `  "${p.id}" ${p.label} (${p.type === 'bool' ? 'is: yes|no' : 'op: gt|lt|eq + value' + (p.unit ? ' in ' + p.unit : '')})`).join('\n')
    const lamp = { devices: [first(['light', 'switch'])] }
    const example = {
      flows: [{
        name: 'Flurlicht bei Bewegung', description: 'Licht an, solange Bewegung da ist – nur wenn es dunkel ist', enabled: false,
        nodes: [
          { id: 'n1', type: 'device_state', x: 0, y: 0, config: { role: 'trigger', target: { devices: [first(['motion'])] }, prop: 'motion', is: 'yes', hold_off: 120 } },
          { id: 'n2', type: 'when', x: 280, y: 0, config: { match: 'all' } },
          { id: 'n3', type: 'time', x: 0, y: 170, config: { role: 'condition', from: 'sunset', to: 'sunrise' } },
          { id: 'n4', type: 'set_device', x: 560, y: 0, config: { do: 'switch', target: lamp, to: 'on' } },
          { id: 'n5', type: 'set_device', x: 560, y: 170, config: { do: 'switch', target: lamp, to: 'off' } }
        ],
        edges: [
          { from: 'n1', fromPort: 'out', to: 'n2', toPort: 'in' },
          { from: 'n3', fromPort: 'val', to: 'n2', toPort: 'c1' },
          { from: 'n2', fromPort: 'yes', to: 'n4', toPort: 'in' },
          { from: 'n1', fromPort: 'end', to: 'n5', toPort: 'in' }
        ]
      }]
    }
    const prompt = `Erstelle ComfyOS-Automatisierungen (Flows) als JSON. Antworte ausschließlich mit gültigem JSON in diesem Format:\n\n${JSON.stringify(example, null, 2)}\n\nRegeln:\n- Jeder Knoten hat: id (eindeutig, z. B. "n1"), type (aus der Liste), x/y (Ganzzahl für das Layout, etwa 280 je Spalte und 170 je Zeile), config (Felder des Typs)\n- Rolle: Bausteine mit role sind mit "role": "trigger" ein Auslöser (Signal-Ausgänge, meist out; Zustands-Auslöser haben out = beginnt und end = endet) und mit "role": "condition" eine Bedingung (Ausgang val)\n- Bedingungen (val) werden an die Eingänge c1, c2, … von "when" (Wenn/Dann: Ausgänge yes/no), "loop" oder "hold" angeschlossen; "not" kehrt eine Bedingung um (Eingang c, Ausgang val)\n- target ist ein Objekt: {"devices": [Geräte-IDs], "rooms": [Raum-IDs], "groups": [Gruppen-IDs]}; bei set_device mit do=switch zusätzlich {"all": true} für alle Lampen\n- device_state vergleicht eine Eigenschaft (prop) der Geräte; hold_off = Sekunden, die der Zustand vorbei sein muss, bevor "end" kommt (z. B. Licht bleibt an, solange Bewegung da ist)\n- Zeitangaben: "18:00", "sunrise", "sunset", mit Versatz in Minuten "sunset+15" oder "sunrise-30"; Dauern in Sekunden\n- Fehlende Felder werden mit dem Standardwert belegt\n- enabled: false → der Flow startet ausgeschaltet${info.flows && info.flows.length ? '\n- Bestehende Flows (werden bei gleichem Namen ersetzt): ' + info.flows.join(', ') : ''}\n\nEigenschaften für device_state:\n${propLines}\n${devs.length ? `\nGeräte auf diesem System:\n${devLines}\n` : ''}\nVerfügbare Bausteine (type → Anschlüsse → Felder):\n${catLines}\n\nMeine Wünsche: `
    let checked = null
    const s = HO.sheet({ title: 'Flows importieren', wide: true, body: `
      <p class="small muted" style="margin-top:0">JSON-Datei wählen oder Text einfügen. Bausteine und Verbindungen werden geprüft; vor dem Übernehmen siehst du, was passiert.</p>
      <label class="btn sm">${HO.icon('upload', 'sm')} Datei wählen<input type="file" accept=".json,application/json,text/plain" id="flImpFile" hidden></label>
      <label class="field"><span>Inhalt</span><textarea id="flImpText" rows="8" spellcheck="false" placeholder='{ "flows": [ … ] }' style="font-family:ui-monospace,monospace;font-size:12px"></textarea></label>
      <details style="margin-top:10px"><summary class="small muted" style="cursor:pointer">Format &amp; Vorlage für eine KI</summary>
        <p class="small muted">Den Prompt kopieren, in ChatGPT/Claude o. Ä. einfügen, Wünsche ergänzen – die Antwort hier einfügen. Der Prompt enthält die Namen und IDs deiner Geräte und Räume.</p>
        <pre class="tiny" style="white-space:pre-wrap;max-height:200px;overflow:auto;background:var(--bg-2);padding:10px;border-radius:10px">${HO.esc(JSON.stringify(example, null, 2))}</pre>
        <div class="row wrap"><button class="btn sm" data-imp="prompt">${HO.icon('copy', 'sm')} KI-Prompt kopieren</button>
          <button class="btn sm" data-imp="example">Beispiel einfügen</button>
          <button class="btn sm" data-imp="export">${HO.icon('download', 'sm')} Aktuelle Flows exportieren</button></div></details>
      <div id="flImpResult" style="margin-top:14px"></div>
      <div class="form-actions"><button class="btn" data-imp="check">Prüfen</button><button class="btn primary" data-imp="apply" disabled>Übernehmen</button></div>` })
    const txt = s.el.querySelector('#flImpText'), out = s.el.querySelector('#flImpResult'), applyBtn = s.el.querySelector('[data-imp="apply"]')
    const invalidate = () => { checked = null; applyBtn.disabled = true }
    txt.addEventListener('input', invalidate)
    s.el.querySelector('#flImpFile').addEventListener('change', async e => {
      const f = e.target.files[0]; if (!f) return
      txt.value = await f.text(); invalidate(); check()
    })
    const result = r => (r.flows || []).map(e => `<div class="li" style="display:block">
        <div class="row between"><b>${HO.esc(e.name)}</b><span class="badge ${e.errors && e.errors.length ? 'bad' : e.action === 'create' ? 'accent' : 'warn'}">${e.errors && e.errors.length ? 'Fehler' : e.action === 'create' ? 'wird angelegt' : 'wird ersetzt'}</span></div>
        <div class="small muted">${e.node_count} Bausteine · ${e.edge_count} Verbindungen${e.description ? ' · ' + HO.esc(e.description) : ''}</div>
        ${(e.warnings || []).map(w => `<div class="small" style="color:var(--warn)">${HO.esc(w)}</div>`).join('')}
        ${(e.errors || []).map(x => `<div class="small" style="color:var(--bad)">${HO.esc(x)}</div>`).join('')}</div>`).join('')
    // Have pasted JSON checked by the server and show the result per flow; only what was checked can be taken over.
    async function check() {
      if (!txt.value.trim()) { HO.toast('Bitte zuerst eine Datei wählen oder JSON einfügen.', 'warn'); return }
      try {
        const r = await HO.api('flows/import', { method: 'POST', body: txt.value, raw: 'application/json' })
        out.innerHTML = `<div class="list">${result(r)}</div>${r.ok ? '' : '<p class="small" style="color:var(--bad)">Bitte die Fehler korrigieren und erneut prüfen.</p>'}`
        checked = r.ok ? txt.value : null; applyBtn.disabled = !r.ok
      } catch (e) { out.innerHTML = `<div class="notice warn">${HO.icon('warn', 'sm')}<div>${HO.esc(e.message)}</div></div>`; invalidate() }
    }
    s.el.addEventListener('click', async e => {
      const b = e.target.closest('[data-imp]'); if (!b) return
      const act = b.dataset.imp
      try {
        if (act === 'prompt') {
          const done = await HO.copy(prompt)
          HO.toast(done ? 'Prompt kopiert – Wünsche ergänzen und an die KI schicken'
            : 'Kopieren nicht möglich – bitte den Text markieren.', done ? 'ok' : 'warn')
        }
        else if (act === 'example') { txt.value = JSON.stringify(example, null, 2); invalidate() }
        else if (act === 'export') downloadJSON(await HO.api('flows/export'), 'flows.json')
        else if (act === 'check') await check()
        else if (act === 'apply' && checked) {
          b.disabled = true
          const r = await HO.api('flows/import?apply=true', { method: 'POST', body: checked, raw: 'application/json' })
          s.close()
          HO.toast(`Importiert: ${r.done.map(d => d.name).join(', ')}`, 'ok')
          await renderList(page)          // show at once – the page stays the same, a page change would reload nothing
        }
      } catch (err) { HO.toast(err.message, 'bad', 6000); if (act === 'apply') b.disabled = false }
    })
  }

  // Dialog "Vorlagen": tapping a template creates the flow and opens it in the editor.
  function templatesSheet() {
    const s = HO.sheet({ title: 'Vorlagen', wide: true, body: `<p class="small muted" style="margin-top:0">Geräte sind mit dem ersten passenden vorbelegt – im Editor antippen zum Anpassen. Neue Automatisierungen starten ausgeschaltet.</p>
      <div class="menu-list">${TEMPLATES.map((t, i) => `<button data-t="${i}">${HO.icon('wand')}<span><b>${HO.esc(t.name)}</b>${t.nodes.some(n => byType[n.type].pro) ? ' ' + HO.proTag('flow_pro_nodes') : ''}<br><span class="small muted">${HO.esc(t.desc)}</span></span></button>`).join('')}</div>` })
    s.el.querySelector('.menu-list').addEventListener('click', async e => {
      const b = e.target.closest('[data-t]')
      if (!b) return
      s.close()
      try {
        await loadLists()
        const f = await HO.api('flows', { method: 'POST', body: fromTemplate(TEMPLATES[+b.dataset.t]) })
        openEditor(f.id)
      } catch (err) { HO.toast(err.message, 'bad') }
    })
  }
  // Load and draw the list of the flows (discarded if the page has changed meanwhile).
  async function renderList(page) {
    const my = gen
    const [, list] = await Promise.all([specs(), loadLists()])
    if (my !== gen) return
    page.innerHTML = listHTML(list)
  }
  // Menu of a flow: edit, group, move, rename, test, copy, delete.
  function flowMenu(page, f) {
    const moves = [atEdge(f, -1) ? null : { label: 'Nach oben', icon: 'back', onClick: () => moveFlow(page, f, -1) },
      atEdge(f, 1) ? null : { label: 'Nach unten', icon: 'download', onClick: () => moveFlow(page, f, 1) }].filter(Boolean)
    HO.menu(f.name, [
      { label: 'Bearbeiten', icon: 'edit', onClick: () => openEditor(f.id) },
      { label: f.group ? `Gruppe: ${f.group}` : 'In Gruppe legen', icon: 'layers', onClick: () => chooseGroup(page, f) }].concat(moves, [
      { label: 'Umbenennen', icon: 'type', onClick: async () => {
        const name = await HO.prompt('Umbenennen', f.name)
        if (!name) return
        try { await HO.api(`flows/${encodeURIComponent(f.id)}/name`, { method: 'PUT', body: { name } }); renderList(page) } catch (e) { HO.toast(e.message, 'bad') }
      } },
      { label: 'Testlauf (alle Auslöser)', icon: 'play', onClick: () => testRun(f.id, () => renderList(page)) },
      { label: 'Duplizieren', icon: 'copy', onClick: async () => {
        try { await HO.api(`flows/${encodeURIComponent(f.id)}/duplicate`, { method: 'POST' }); HO.toast('Kopie angelegt (ausgeschaltet)'); renderList(page) } catch (e) { HO.toast(e.message, 'bad') }
      } },
      { label: 'Löschen', icon: 'trash', danger: true, onClick: () => removeFlow(page, f) }
    ]))
  }
  async function removeFlow(page, f) {
    if (!await HO.confirm(`„${f.name}“ endgültig löschen?`, { ok: 'Löschen', danger: true })) return
    try { await HO.api(`flows/${encodeURIComponent(f.id)}`, { method: 'DELETE' }); renderList(page) } catch (e) { HO.toast(e.message, 'bad') }
  }
  // Show the result of a test run as a message: the trace, the error or the hint that nothing happened.
  function showResult(r, none) {
    const err = r.info && r.info.last_error
    if (err) HO.toast(err, 'bad', 6000)
    else HO.toast(r.trace.length ? r.trace.join(' · ') : none, r.trace.length ? 'ok' : 'warn', 6000)
  }
  // Run a flow once by hand and show the trace.
  async function testRun(id, after) {
    try {
      showResult(await HO.api(`flows/${encodeURIComponent(id)}/run`, { method: 'POST' }),
        'Nichts ausgeführt – Bedingungen nicht erfüllt oder keine Aktion verbunden.')
    } catch (e) { HO.toast(e.message, 'bad') }
    if (after) after()
  }

  HO.pages.flow = {
    title: 'Flow',
    // Open the page "Automatisierungen": draw the list, subscribe to live messages (triggered flows light up),
    // set up the controls.
    async render(page) {
      gen++
      page.innerHTML = '<div class="spinner" style="margin-top:20vh"></div>'
      await renderList(page)
      // live: triggered flows light up briefly; wait times and the last state follow
      const my = gen
      openStream(m => {
        if (my !== gen) return
        if (m.ev === 'run' && !m.cont) {
          L.fired[m.flow] = Date.now()
          const card = page.querySelector(`.fl-item[data-flow="${CSS.escape(m.flow)}"]`)
          if (card) { card.classList.remove('fired'); void card.offsetWidth; card.classList.add('fired') }
        }
        if (m.ev === 'end' || m.ev === 'wait' || m.ev === 'idle' || m.ev === 'state') {
          clearTimeout(L.refreshT)
          L.refreshT = setTimeout(() => { if (my === gen && !document.querySelector('.sheet-bg')) renderList(page) }, 900)
        }
      })
      // remember the opening and collapsing of a group ("toggle" does not bubble – therefore in the capture phase)
      if (!page.dataset.flowGroupsBound) {
        page.dataset.flowGroupsBound = '1'
        page.addEventListener('toggle', e => {
          if (e.target.classList && e.target.classList.contains('fl-group')) rememberGroup(e.target.dataset.group, e.target.open)
        }, true)
      }
      page.onclick = async e => {
        const t = e.target.closest('[data-act],[data-toggle],[data-menu],[data-play],[data-var],[data-del],[data-open]')
        if (!t) return
        const f = id => D.flows.find(x => x.id === id)
        try {
          if (t.dataset.act === 'new') {
            const name = await HO.prompt('Neue Automatisierung', '', { ok: 'Anlegen' })
            if (!name) return
            const r = await HO.api('flows', { method: 'POST', body: { name } })
            openEditor(r.id)
          } else if (t.dataset.act === 'templates') templatesSheet()
          else if (t.dataset.act === 'import') importSheet(page)
          else if (t.dataset.del) removeFlow(page, f(t.dataset.del))
          else if (t.dataset.toggle) {
            const on = !t.classList.contains('on')
            t.disabled = true
            await HO.api(`flows/${encodeURIComponent(t.dataset.toggle)}/enabled`, { method: 'PUT', body: { enabled: on } })
            await renderList(page)
          } else if (t.dataset.menu) flowMenu(page, f(t.dataset.menu))
          else if (t.dataset.play) {
            t.disabled = true
            showResult(await HO.api(`flows/${encodeURIComponent(t.dataset.play)}/play/${encodeURIComponent(t.dataset.node)}`, { method: 'POST' }),
              'Ausgelöst – keine Aktion lief (Bedingungen?)')
            await renderList(page)
          } else if (t.dataset.var) {
            const v = D.vars[t.dataset.var]
            if (typeof v !== 'boolean') return
            await HO.api('flows/vars', { method: 'PUT', body: { [t.dataset.var]: !v } })
            await renderList(page)
          } else if (t.dataset.open) openEditor(t.dataset.open)
        } catch (err) { HO.toast(err.message, 'bad'); renderList(page) }
      }
    },
    destroy() { gen++; clearTimeout(L.refreshT); closeStream() }
  }

  /* ---------- Editor */
  let E = null
  const insets = () => {
    const wide = window.matchMedia('(min-width: 900px)').matches
    return { l: wide ? 252 : 0, t: 118, r: 64, b: wide ? 16 : 70 }
  }
  // Palette of the nodes, ordered by category.
  function paletteHTML() {
    const item = sp => `<button class="fl-pal-item cat-${sp.category}" data-type="${sp.type}" title="${HO.esc(sp.help || sp.label)}">${HO.icon(sp.icon || 'device', 'sm')}<span>${HO.esc(sp.label)}</span>${sp.pro ? '<i class="fl-pro-tag">Pro</i>' : ''}</button>`
    return `<div class="fl-pal-head"><b>Bausteine</b><span class="tiny muted fl-pal-tip">ziehen oder antippen</span>
        <button class="btn-icon sm plain fl-pal-close" data-act="palClose" aria-label="Schließen">${HO.icon('x', 'sm')}</button></div>
      <div class="fl-pal-body">${CATS.map(c => {
        const items = SPECS.nodes.filter(n => n.category === c)
        return `<div class="fl-pal-cat"><div class="eyebrow">${HO.esc(SPECS.categories[c].label)}</div>
          ${c === 'source' ? '<div class="tiny muted fl-pal-note">Jeder ist Auslöser oder Bedingung – einstellbar im Baustein.</div>' : ''}
          <div class="fl-pal-items">${items.filter(n => !n.pro).concat(items.filter(n => n.pro)).map(item).join('')}</div></div>`
      }).join('')}</div>`
  }
  // Frame of the editor: drawing surface, head with name and save state, tools, palette.
  function editorHTML(f) {
    const tool = (act, icon, title) => `<button class="pv-btn sm" data-act="${act}" title="${title}" aria-label="${title}">${HO.icon(icon, 'sm')}</button>`
    return `<div class="fl">
      <div class="fl-stage-wrap" id="flStage"></div>
      <div class="fl-empty ${f.nodes.length ? 'hidden' : ''}" id="flEmpty">${HO.icon('workflow', 'lg')}<div>Zieh einen <b>Auslöser</b> aus den Bausteinen auf die Fläche,<br>dann Aktionen – und verbinde die Punkte.</div></div>
      <div class="pv-bar pv-top">
        <div class="row g8" style="min-width:0"><button class="pv-btn" data-act="back" title="Zurück" aria-label="Zurück">${HO.icon('back')}</button>
          <button class="pv-chip fl-name" data-act="rename" title="Umbenennen"><b id="flName">${HO.esc(f.name)}</b><span class="muted" id="flSaved">Gespeichert</span></button>
          <button class="pv-chip fl-descbtn" data-act="desc" title="Beschreibung bearbeiten"><span class="small muted" id="flDesc">${HO.esc(f.description || '')}</span></button></div>
        <div class="row g8" style="flex:none">
          <button class="pv-btn" data-act="replay" title="Letzten Durchgang zeigen" aria-label="Letzten Durchgang zeigen">${HO.icon('activity', 'sm')}</button>
          <div class="pv-chip fl-on"><span class="small">Aktiv</span><button class="room-switch ${f.enabled ? 'on' : ''}" data-act="enable" role="switch" aria-checked="${!!f.enabled}" aria-label="Automatisierung ein/aus"></button></div></div></div>
      <div class="pv-bar fl-edit" id="flEdit">
        ${tool('undo', 'undo', 'Rückgängig')}${tool('redo', 'redo', 'Wiederholen')}<span class="fl-sep"></span>
        ${tool('pick', 'select', 'Auswählen: mehrere Bausteine antippen oder einen Rahmen ziehen')}${tool('copy', 'copy', 'Kopieren')}${tool('paste', 'paste', 'Einfügen')}${tool('del', 'trash', 'Auswahl löschen')}</div>
      <aside class="fl-pal" id="flPal">${paletteHTML()}</aside>
      <button class="pv-chip fl-fab" data-act="pal">${HO.icon('plus', 'sm')}<b>Baustein</b></button>
      <div class="pv-bar pv-tools">
        <button class="pv-btn" data-act="zin" title="Vergrößern" aria-label="Vergrößern">${HO.icon('plus', 'sm')}</button>
        <button class="pv-btn" data-act="zout" title="Verkleinern" aria-label="Verkleinern">${HO.icon('minus', 'sm')}</button>
        <button class="pv-btn" data-act="fit" title="Alles zeigen" aria-label="Alles zeigen">${HO.icon('fit', 'sm')}</button></div></div>`
  }
  // Show the save state in the head ("Gespeichert", "Ungespeichert …", "Speichert …").
  const setSaved = t => {
    const el = document.getElementById('flSaved')
    if (!el) return
    el.textContent = t
    el.parentElement.classList.toggle('dirty', t !== 'Gespeichert')   // am Handy: Punkt statt Text
  }
  // toolbar: only offer what is possible right now
  function tools() {
    const bar = document.getElementById('flEdit')
    if (!bar || !E) return
    const set = (act, on) => { const b = bar.querySelector(`[data-act="${act}"]`); if (b) b.disabled = !on }
    set('undo', E.canUndo); set('redo', E.canRedo)
    set('copy', E.selNodes > 0); set('del', E.selNodes > 0 || E.selEdge)
    set('paste', E.canvas && E.canvas.hasClip())
    const pick = bar.querySelector('[data-act="pick"]')
    if (pick) pick.classList.toggle('on', !!E.picking)
  }
  // The flow was changed: show the state and save shortly.
  function dirty() {
    if (!E) return
    setSaved('Ungespeichert …')
    const empty = document.getElementById('flEmpty')
    if (empty) empty.classList.toggle('hidden', E.flow.nodes.length > 0)
    clearTimeout(E.saveT)
    E.saveT = setTimeout(save, 700)
  }
  // Save the flow. If a save is already running, another follows afterwards – no change is ever lost.
  async function save() {
    const ed = E
    if (!ed) return
    clearTimeout(ed.saveT); ed.saveT = null
    if (ed.saving) { ed.again = true; return ed.saving }
    setSaved('Speichert …')
    ed.saving = (async () => {
      try {
        const r = await HO.api(`flows/${encodeURIComponent(ed.flow.id)}`, { method: 'PUT', body: { name: ed.flow.name, description: ed.flow.description || '', nodes: ed.flow.nodes, edges: ed.flow.edges } })
        // take over webhook tokens created by the server
        for (const n of r.nodes) { const m = ed.flow.nodes.find(x => x.id === n.id); if (m && n.config.token) m.config.token = n.config.token }
        ed.state = r.state || ed.state
        if (E === ed) setSaved('Gespeichert')
      } catch (e) { if (E === ed) setSaved('Nicht gespeichert'); HO.toast(e.message, 'bad') }
    })()
    await ed.saving
    ed.saving = null
    if (ed.again) { ed.again = false; return save() }
  }
  // Wait until a pending or running save is done.
  async function flush() {
    if (!E) return
    if (E.saveT) await save()
    else if (E.saving) await E.saving
  }
  // start a trigger by hand (test): ▶ on the card or buttons in the node dialog
  async function fireNode(n, port) {
    if (!E) return
    await flush()
    try {
      const r = await HO.api(`flows/${encodeURIComponent(E.flow.id)}/fire/${encodeURIComponent(n.id)}`, { method: 'POST', body: port ? { port } : {} })
      showResult(r, 'Ausgelöst – keine Aktion lief (Bedingungen nicht erfüllt oder nichts verbunden).')
      if (E && r.live) E.canvas.setLive(r.live)
    } catch (err) { HO.toast(err.message, 'bad') }
  }
  // What the engine currently knows about the node: whether its state holds, what passed last.
  function inspectHTML(n) {
    const st = (E.state || {})[n.id] || {}
    const parts = []
    if (isTrigger(n) && byType[n.type] && FS.outs(n).some(p => p.id === 'end') && typeof st.on === 'boolean') {
      parts.push(`<p class="small muted">Der Zustand gilt gerade: <b>${st.on ? 'ja' : 'nein'}</b>${st.until ? ` – Wechsel ${HO.relTime(st.until)}` : ''}</p>`)
    }
    if (n.type === 'inspect') {
      parts.push(!st.at ? '<div class="notice">Noch nicht erreicht.</div>'
        : `<div class="card fl-probe" style="background:var(--card-2)"><div class="eyebrow">Zuletzt erreicht</div>
          <dl class="kv"><dt>Wann</dt><dd>${HO.relTime(st.at)}</dd><dt>Auslöser</dt><dd>${HO.esc(st.trigger || '–')}</dd>
          ${st.device ? `<dt>Gerät</dt><dd>${HO.esc(st.device)}</dd>` : ''}${st.value !== '' && st.value != null ? `<dt>Wert</dt><dd>${HO.esc(st.value)}</dd>` : ''}</dl></div>`)
    }
    if (n.type === 'toggle') parts.push(`<p class="small muted">Das nächste Signal geht an: <b>${st.last === 'a' ? 'B' : 'A'}</b></p>`)
    if (n.type === 'loop' && st.loop) parts.push(`<p class="small muted">Läuft gerade – Runde <b>${st.loop.round || 0}</b></p>`)
    return parts.join('')
  }
  // Buttons "Testen" of a trigger: start the run from its output – the actions really run.
  function testHTML(n) {
    if (!isTrigger(n)) return ''
    const outs = FS.outs(n)
    return `<div class="fl-test"><div class="eyebrow">Testen</div>
      <div class="row wrap">${outs.map(p => `<button type="button" class="btn sm" data-fire="${HO.esc(p.id)}">${HO.icon('play', 'sm')} ${HO.esc(outs.length > 1 ? '„' + (p.label || p.id) + '“ auslösen' : 'Jetzt auslösen')}</button>`).join('')}</div>
      <span class="hint">Startet den Ablauf von hier, als wäre der Auslöser eingetreten – die Aktionen laufen wirklich.</span></div>`
  }
  // Dialog of a node: help, fields, current state, testing, deleting. Changes apply at once and are
  // saved.
  function nodeSheet(n) {
    const sp = byType[n.type]
    if (!sp) return
    const catLabel = FS.role(n) ? SPECS.roles[FS.role(n)].label : (SPECS.categories[sp.category] || {}).label
    const body = () => `<div class="row wrap fl-cat"><span class="badge">${HO.icon((SPECS.categories[sp.category] || {}).icon || 'info', 'sm')}${HO.esc(catLabel || '')}</span>${sp.pro ? '<span class="badge">Pro</span>' : ''}</div>
      ${sp.help ? `<p class="small muted" style="margin-top:0">${HO.esc(sp.help)}</p>` : ''}
      ${(sp.fields || []).length || FS.roles(sp).length > 1 ? fieldsHTML(sp, n) : '<p class="muted small">Dieser Baustein braucht keine Einstellungen – einfach verbinden.</p>'}
      ${testHTML(n)}<div class="fl-inspect">${inspectHTML(n)}</div>${infoHTML(n)}`
    const s = HO.sheet({ title: sp.label, body: body(), actions: [
      { label: 'Löschen', kind: 'ghost danger', icon: 'trash', onClick: () => E.canvas.removeNode(n.id) },
      { label: 'Kopie', kind: 'ghost', icon: 'copy', onClick: () => { E.canvas.duplicateNode(n.id) } },
      { label: 'Fertig', kind: 'primary' }] })
    // Redraw the dialog while keeping scroll position, an opened "Erweitert" and the device filter.
    const redraw = () => {
      const b = s.body(), top = b.scrollTop, adv = !!b.querySelector('.fl-adv[open]'), filter = (b.querySelector('.fl-dfilter') || {}).value
      b.innerHTML = body()
      const a = b.querySelector('.fl-adv'); if (a && adv) a.open = true
      const f = b.querySelector('.fl-dfilter'); if (f && filter) { f.value = filter; filterDevices(f) }
      b.scrollTop = top
    }
    const filterDevices = sel => {
      sel.closest('.fl-field').querySelectorAll('.fl-devitem').forEach(el => { el.style.display = (!sel.value || el.dataset.room === sel.value) ? '' : 'none' })
    }
    // fields others depend on (when, device list, properties): rebuild the dialog afterwards
    const drivers = new Set(['role', 'target', 'prop', 'match'])
    ;(sp.fields || []).forEach(f => { Object.keys(f.when || {}).forEach(k => drivers.add(k)); if (f.kinds_by) drivers.add(f.kinds_by.key); if (f.widget === 'moment') drivers.add(f.key) })
    // Take over changed fields (with a step for undo) and bring ports up to date that depend on them.
    const apply = key => {
      const before = clone(n.config || {})
      const next = { ...before, ...readAll(s.body(), sp, n) }
      if (same(before, next)) return
      E.canvas.snap()
      n.config = next
      settle(n, sp, before)
      const gone = E.canvas.configChanged(n.id)
      if (gone) HO.toast(`${gone} Verbindung${gone > 1 ? 'en' : ''} entfernt – der Anschluss gibt es mit dieser Einstellung nicht mehr.`, 'warn', 5000)
      dirty()
      if (!key || drivers.has(key)) redraw()
    }
    s.body().addEventListener('change', e => {
      if (e.target.classList.contains('fl-dfilter')) return filterDevices(e.target)
      const box = e.target.closest('.fl-field')
      apply(box && box.dataset.k)
    })
    s.body().addEventListener('click', async e => {
      const c = e.target.closest('[data-day],[data-days],[data-room],[data-troom],[data-tgroup],[data-tall],[data-opt],[data-copy],[data-fire],[data-sign]')
      if (!c) return
      if (c.dataset.copy) {
        if (await HO.copy(c.dataset.copy)) HO.toast('Adresse kopiert')
        else HO.toast('Kopieren nicht möglich – bitte markieren.', 'warn')
        return
      }
      if (c.dataset.fire !== undefined) return fireNode(n, c.dataset.fire)
      const box = c.closest('.fl-field')
      if (c.dataset.sign !== undefined) {
        const inp = c.parentElement.querySelector('input'), v = inp.value.trim()
        inp.value = v.charAt(0) === '-' || v.charAt(0) === '−' ? v.slice(1) : (v ? '-' + v : '-')
        if (inp.value === '-') { inp.focus(); return }
      } else if (c.dataset.days) {
        const want = new Set(c.dataset.days.split(',').map(Number))
        box.querySelectorAll('[data-day]').forEach(x => x.classList.toggle('active', want.has(+x.dataset.day)))
      } else if (c.dataset.opt !== undefined) {
        c.parentElement.querySelectorAll('[data-opt]').forEach(x => x.classList.toggle('active', x === c))
      } else c.classList.toggle('active')
      apply(box && box.dataset.k)
    })
    // fetch the live state (inspector, toggle, state) fresh on opening
    HO.api(`flows/${encodeURIComponent(E.flow.id)}`).then(r => {
      if (!E || !document.body.contains(s.el)) return
      E.state = r.state || {}
      const box = s.body().querySelector('.fl-inspect')
      if (box) box.innerHTML = inspectHTML(n)
    }).catch(() => {})
    return s
  }

  // Drag nodes out of the palette (mouse, pen) or tap them (touch).
  // Touch: no pointerCapture, so that the list stays scrollable.
  function palettePointer(stageEl) {
    const pal = document.getElementById('flPal')
    let d = null
    const wide = () => window.matchMedia('(min-width: 900px)').matches
    const close = () => pal.classList.remove('open')
    // Drag a node out of the palette: with the mouse an image appears at the pointer; on a touch device the palette
    // scrolls instead.
    const onMove = e => {
      if (!d || e.pointerId !== d.id) return
      if (!d.ghost) {
        if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 8) return
        if (d.touch) { d = null; return }     // on touch: scrolling takes over
        const sp = byType[d.type]
        d.ghost = document.createElement('div')
        d.ghost.className = `fl-ghost cat-${sp.category}`
        d.ghost.innerHTML = `${HO.icon(sp.icon || 'device', 'sm')}<b>${HO.esc(sp.label)}</b>`
        document.body.appendChild(d.ghost)
        if (!wide()) close()
      }
      d.ghost.style.left = e.clientX + 'px'; d.ghost.style.top = e.clientY + 'px'
      const over = document.elementFromPoint(e.clientX, e.clientY)
      d.ok = !!(over && stageEl.contains(over) && !pal.contains(over))
      d.ghost.classList.toggle('no', !d.ok)
    }
    // Release: over the drawing surface the node is created there; without dragging it counts as a tap (node into the
    // centre).
    const onUp = e => {
      if (!d || e.pointerId !== d.id) return
      const was = d
      d = null
      if (was.ghost) {
        was.ghost.remove()
        if (was.ok && e.type === 'pointerup') E.canvas.addNode(was.type, e.clientX, e.clientY)
      } else if (e.type === 'pointerup' && Math.hypot(e.clientX - was.x0, e.clientY - was.y0) < 8) {
        E.canvas.addNode(was.type)
        close()
      }
    }
    pal.addEventListener('pointerdown', e => {
      const it = e.target.closest('.fl-pal-item')
      if (!it || (e.pointerType === 'mouse' && e.button > 0)) return
      const isTouch = e.pointerType === 'touch'
      d = { type: it.dataset.type, id: e.pointerId, x0: e.clientX, y0: e.clientY, ghost: null, ok: false, touch: isTouch }
      if (!isTouch) try { it.setPointerCapture(e.pointerId) } catch (err) { }
    })
    pal.addEventListener('pointermove', onMove)
    pal.addEventListener('pointerup', onUp)
    pal.addEventListener('pointercancel', e => { if (d && e.pointerId === d.id) d = null })
  }

  function editorKey(e) {
    if (document.querySelector('.sheet-bg') || /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || '')) return
    if (e.key === 'Escape' && E && !E.canvas.selected()) leave()
  }
  // Leave the editor: save first, then back to the list.
  async function leave() {
    await flush()
    if (HO.prevTab === 'flow' && history.length > 1) history.back()
    else location.hash = 'flow'
  }

  HO.pages.flowEdit = {
    title: 'Flow-Editor',
    chromeless: true,
    // Open the editor: load the flow, build the drawing surface, show live messages (test run, wait times), set up
    // the controls.
    async render(page) {
      const my = ++gen
      const id = HO.state.flowId || ss.get('ho.flowId')
      if (!id) { location.hash = 'flow'; return }
      page.innerHTML = '<div class="fl"><div class="spinner" style="margin-top:40vh"></div></div>'
      let flow
      try {
        [, flow] = await Promise.all([specs(), HO.api(`flows/${encodeURIComponent(id)}`), loadLists()])
      } catch (e) {
        if (my !== gen) return
        HO.toast(e.message, 'bad')
        location.hash = 'flow'
        return
      }
      if (my !== gen) return
      if (flow.outdated) {
        HO.toast('Diese Automatisierung ist veraltet und lässt sich nicht mehr bearbeiten – bitte neu bauen.', 'warn', 6000)
        location.hash = 'flow'
        return
      }
      E = { flow, state: flow.state || {}, saveT: null, saving: null, again: false, canvas: null,
        canUndo: false, canRedo: false, selNodes: 0, selEdge: false, picking: false }
      page.innerHTML = editorHTML(flow)
      const stageEl = page.querySelector('#flStage')
      E.canvas = HO.flowCanvas.mount(stageEl, {
        flow, specs: SPECS, summary, problem, defaults, customized, insets,
        canFire: n => isTrigger(n),
        onChange: dirty,
        onNodeTap: n => nodeSheet(n),
        onFire: n => fireNode(n),
        onHint: t => HO.toast(t, 'warn', 2200),
        onSelect: (nodes, edge) => { if (E) { E.selNodes = nodes; E.selEdge = edge; tools() } },
        onHistory: (u, r) => { if (E) { E.canUndo = u; E.canRedo = r; tools() } }
      })
      E.canvas.setLive(flow.live || {})
      tools()
      palettePointer(stageEl)
      document.addEventListener('keydown', editorKey)
      // live: show the steps of this flow on the surface
      openStream(m => {
        if (!E || m.flow !== E.flow.id) return
        E.canvas.live(m)
        if (m.ev === 'end' && m.error && !document.querySelector('.sheet-bg')) HO.toast('Fehler im Ablauf: ' + m.error, 'bad', 6000)
      })
      page.onclick = async e => {
        const t = e.target.closest('[data-act]')
        if (!t || !E) return
        const act = t.dataset.act, c = E.canvas
        if (act === 'back') return leave()
        if (act === 'pal') return document.getElementById('flPal').classList.add('open')
        if (act === 'palClose') return document.getElementById('flPal').classList.remove('open')
        if (act === 'zin') return c.zoomBy(1.25)
        if (act === 'zout') return c.zoomBy(1 / 1.25)
        if (act === 'fit') return c.fit()
        if (act === 'undo') return c.undo()
        if (act === 'redo') return c.redo()
        if (act === 'del') return c.removeSelected()
        if (act === 'copy') { const k = c.copy(); if (k) HO.toast(`${k} Baustein${k > 1 ? 'e' : ''} kopiert`); return tools() }
        if (act === 'paste') { if (!c.paste()) HO.toast('Nichts zum Einfügen – erst Bausteine auswählen und kopieren.', 'warn'); return }
        if (act === 'pick') {
          E.picking = !E.picking
          c.setSelectMode(E.picking)
          if (E.picking) HO.toast('Auswählen: Bausteine antippen oder einen Rahmen ziehen', 'ok', 2600)
          return tools()
        }
        if (act === 'replay') {
          try {
            const r = await HO.api(`flows/${encodeURIComponent(E.flow.id)}`)
            if (!E) return
            if (!r.last) return HO.toast('Seit dem Start von ComfyOS gab es noch keinen Durchgang.', 'warn')
            c.setLive(r.live || {})
            c.replay(r.last)
            HO.toast(`Letzter Durchgang ${HO.relTime(r.last.at)}${r.last.trigger ? ' · ' + r.last.trigger : ''}${r.last.error ? ' · Fehler: ' + r.last.error : ''}`, r.last.error ? 'bad' : 'ok', 5000)
          } catch (err) { HO.toast(err.message, 'bad') }
          return
        }
        if (act === 'rename') {
          const name = await HO.prompt('Umbenennen', E.flow.name)
          if (!name || !E) return
          E.flow.name = name
          document.getElementById('flName').textContent = name
          return dirty()
        }
        if (act === 'desc') {
          const desc = await HO.prompt('Beschreibung', E.flow.description || '')
          if (desc === null || !E) return
          E.flow.description = desc || undefined
          document.getElementById('flDesc').textContent = desc || ''
          return dirty()
        }
        if (act === 'enable') {
          const on = !E.flow.enabled
          await flush()
          try {
            const r = await HO.api(`flows/${encodeURIComponent(E.flow.id)}/enabled`, { method: 'PUT', body: { enabled: on } })
            E.flow.enabled = on
            t.classList.toggle('on', on); t.setAttribute('aria-checked', on)
            c.setLive(r.live || {})
            const open = E.flow.nodes.filter(n => byType[n.type] && problem(n, byType[n.type])).length
            if (on && open) HO.toast(`Eingeschaltet – ${open} Baustein${open > 1 ? 'e brauchen' : ' braucht'} noch Einstellungen.`, 'warn', 5000)
            else HO.toast(on ? 'Automatisierung läuft' : 'Automatisierung ausgeschaltet')
          } catch (err) { HO.toast(err.message, 'bad') }
        }
      }
    },
    // Leave the editor: save the last change, tear down drawing surface and subscriptions.
    destroy() {
      gen++
      document.removeEventListener('keydown', editorKey)
      closeStream()
      if (E) {
        if (E.saveT) save()             // still save the last change
        if (E.canvas) E.canvas.destroy()
      }
      document.querySelectorAll('.fl-ghost').forEach(g => g.remove())
      E = null
    }
  }
})(window.HO)
