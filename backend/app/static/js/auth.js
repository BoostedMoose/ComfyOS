/* ComfyOS – login and first setup */
(function (HO) {
  'use strict'

  HO.auth = {
    // Show the login page – on the first start "Passwort festlegen" instead.
    show(state) {
      const root = document.getElementById('root')
      const setup = !state.configured
      root.innerHTML = `<div class="auth"><div class="card auth-card">
        ${HO.logo('auth-logo')}
        <h1>ComfyOS</h1>
        <p class="muted small">${setup ? 'Willkommen! Lege ein Passwort für ComfyOS fest. Damit meldest du dich auf allen Geräten im Heimnetz an.' : 'Bitte anmelden'}</p>
        <form id="authForm" autocomplete="on">
          ${setup ? `
            <label class="field"><span>Neues Passwort</span><input type="password" id="pw1" autocomplete="new-password" minlength="6" required></label>
            <label class="field"><span>Passwort wiederholen</span><input type="password" id="pw2" autocomplete="new-password" minlength="6" required></label>
            ${state.needs_code ? `<label class="field"><span>Einrichtungscode</span><input type="text" id="code" placeholder="XXXX-XXXX" autocapitalize="characters" required>
              <span class="hint">Steht im Fenster, in dem ComfyOS läuft (bzw. nach install_pi.sh). Schützt davor, dass jemand anderes im WLAN ComfyOS zuerst einrichtet.</span></label>` : ''}
          ` : `<label class="field"><span>Passwort</span><input type="password" id="pw1" autocomplete="current-password" required></label>`}
          <div class="err" id="authErr"></div>
          <button class="btn primary" type="submit">${setup ? 'Einrichten' : 'Anmelden'}</button>
          ${setup ? '<div style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px"><p class="small muted">Daten aus einer früheren Installation? Richte erst das Passwort ein und spiele das Umzugspaket danach unter <b>System → Backup</b> ein.</p></div>' : ''}
        </form>
      </div></div>`
      const form = root.querySelector('#authForm'), err = root.querySelector('#authErr')
      setTimeout(() => root.querySelector('#pw1').focus(), 50)
      form.addEventListener('submit', async e => {
        e.preventDefault()
        err.textContent = ''
        const pw1 = root.querySelector('#pw1').value
        try {
          if (setup) {
            if (pw1 !== root.querySelector('#pw2').value) { err.textContent = 'Die Passwörter stimmen nicht überein.'; return }
            const code = root.querySelector('#code')
            await HO.api('auth/setup', { method: 'POST', body: { password: pw1, code: code ? code.value : null } })
          } else {
            await HO.api('auth/login', { method: 'POST', body: { password: pw1 } })
          }
          HO.boot()
        } catch (ex) { err.textContent = ex.message }
      })
    }
  }
})(window.HO)
