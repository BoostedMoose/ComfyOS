/* ComfyOS – simple form fields from a description
   Used for the settings of a plugin (manifest.json → "settings"): text, number, toggle, select, password.
   HO.fields.html(fields, values) builds the form, HO.fields.read(root, fields) reads it back.
   Password fields never get the stored value: if one is stored, dots are shown (HO.pwAttrs);
   left unchanged it stays as it is. */
(function (HO) {
  'use strict'
  const attr = (k, v) => v == null ? '' : ` ${k}="${HO.esc(v)}"`

  // One settings field of a plugin as HTML – depending on its kind: switch, number, choice, password or text.
  function fieldHTML(f, value) {
    const key = HO.esc(f.key), hint = f.hint ? `<span class="hint">${HO.esc(f.hint)}</span>` : ''
    if (f.widget === 'toggle') {
      return `<label class="check" style="margin-top:12px"><input type="checkbox" data-field="${key}" ${value ? 'checked' : ''}>
        <span>${HO.esc(f.label)}${f.hint ? ` <span class="muted small">(${HO.esc(f.hint)})</span>` : ''}</span></label>`
    }
    let input
    if (f.widget === 'select') {
      input = `<select data-field="${key}">${(f.options || []).map(o =>
        `<option value="${HO.esc(o[0])}" ${String(o[0]) === String(value) ? 'selected' : ''}>${HO.esc(o[1])}</option>`).join('')}</select>`
    } else if (f.widget === 'number') {
      input = `<input type="number" data-field="${key}" value="${HO.esc(value == null ? '' : value)}"${attr('min', f.min)}${attr('max', f.max)}${attr('step', f.step)}>`
    } else if (f.widget === 'password') {
      input = `<input type="password" data-field="${key}" autocomplete="off" ${HO.pwAttrs(!!value)}>`
    } else {
      input = `<input type="text" data-field="${key}" maxlength="400" autocomplete="off" value="${HO.esc(value == null ? '' : value)}">`
    }
    return `<label class="field"><span>${HO.esc(f.label)}</span>${input}${hint}</label>`
  }

  HO.fields = {
    html: (fields, values) => (fields || []).map(f => fieldHTML(f, (values || {})[f.key])).join(''),
    // Read the values of the fields from a form; empty number fields and passwords that were not re-entered drop out.
    read(root, fields) {
      const out = {}
      for (const f of fields || []) {
        const el = root.querySelector(`[data-field="${f.key}"]`)
        if (!el) continue
        if (f.widget === 'toggle') out[f.key] = el.checked
        else if (f.widget === 'number') { if (el.value !== '' && isFinite(+el.value)) out[f.key] = +el.value }
        else if (f.widget === 'password') { if (HO.pwValue(el)) out[f.key] = HO.pwValue(el) }
        else out[f.key] = el.value
      }
      return out
    }
  }
})(window.HO)
