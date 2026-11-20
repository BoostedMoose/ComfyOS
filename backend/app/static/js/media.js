/* ComfyOS – playback: the speaker tile (since 0.22)

   Shows and controls every device that has the capability `playing` – no matter which plugin delivers it (HO.caps). The
   home page shows the tiles as the block "Wiedergabe" (overview.js registers it), the device dialog shows the same
   tile for a single device; from 0.23 it becomes the widget "Wiedergabe".

     HO.media.devices(devices)          all speakers (capability `playing`), sorted by name
     HO.media.stateText(device)         short state: "Spielt · Titel", "Pausiert"
     HO.media.cardHTML(device)          tile of a speaker: cover picture, title, artist, playback, volume, favourites
     HO.media.blockHTML(overview)       block of the home page – empty if there is no speaker
     HO.media.handle(event, done)       carry out a click or change in a tile; true = it was a tile.
                                        `done()` is called after every change to the device (redraw)
     HO.media.send(device, state, done)      send a command; the tapped state appears at once
     HO.media.choosing(box)             true while the choice "Favorit starten" in `box` is open – whoever redraws
                                        the tiles waits that long, otherwise the list closes under the finger

   Sound of this computer (since 0.22.3): speakers with the capability `live_audio` get the button "Ton dieses
   Rechners". The browser asks what to share (whole screen with "Systemaudio teilen", or one tab), this file turns the
   sound into plain 16-bit samples and sends them to ComfyOS piece by piece (/api/audio/live/…); the speaker
   fetches them from there as a stream. Only browsers that can share sound offer the button (Chrome and Edge on
   a computer, page opened over HTTPS). Firefox cannot share sound; there the sound comes from a recording
   device the user picks (since 0.22.4) – "Stereomix" of Windows or a virtual audio cable.

   Title, artist, room and favourite names are foreign text and always go through HO.esc. The browser fetches cover
   pictures only from our own server (/api/media/image/…); if one is missing, the speaker icon is shown. */
(function (HO) {
  'use strict'

  const HOLD_MS = 3000          // a tapped state holds this long against older messages
  const held = {}               // device id -> { bis, werte }: states tapped just now

  const LIVE_RATES = [48000, 44100]   // sample rates ComfyOS passes on
  const LIVE_SEND_SECONDS = 0.25      // this much sound goes out per request
  const LIVE_MAX_WAITING = 24         // pieces that may wait for the network; beyond that the oldest are dropped
  const LIVE_TITLE = 'Ton dieses Rechners abspielen'
  let live = null                     // the running transmission of this page: { ids, stream, context, node, parts, size, waiting, … }

  const speaker = d => HO.caps.has(d, 'playing')
  const can = (d, key) => HO.canControl(d) && HO.caps.has(d, key, true)

  // Lay values tapped just now over the reported state until the device confirms them or the hold time is up.
  function shown(d) {
    const h = held[d.id]
    if (!h) return d
    if (h.until < Date.now()) { delete held[d.id]; return d }
    return Object.assign({}, d, h.values)
  }

  // Short state for lists and tiles: "Spielt · Titel", "Pausiert" or "Kein Kontakt".
  function stateText(d) {
    const s = shown(d)
    if (!s.online) return 'Kein Kontakt'
    if (s.playing == null) return HO.kindLabel[s.kind] || 'Gerät'
    const title = s.media && s.media.title
    return s.playing ? 'Spielt' + (title ? ' · ' + title : '') : 'Pausiert'
  }

  // Second line below the title: artist, otherwise album, plus the source (radio, TV …).
  function subText(media) {
    return [media.artist || media.album, media.source].filter(Boolean).join(' · ')
  }

  function artHTML(media) {
    const image = media && media.image
    return `<div class="media-art">${HO.icon('media')}${image ? `<span class="media-img" style="background-image:url('${HO.esc(image)}')"></span>` : ''}</div>`
  }

  // Playback buttons: back, play/pause, next – only what the device accepts; locked: the lock.
  function buttonsHTML(d, s) {
    if (d.online && d.control_item && d.locked) return HO.lockedBtn(d)
    if (!can(d, 'playing')) return ''
    const id = HO.esc(d.id)
    const skip = (key, label, cls) => can(d, key)
      ? `<button class="btn-icon sm ${cls}" data-media-act="${key}" data-id="${id}" aria-label="${label}" title="${label}">${HO.icon('chevron', 'sm')}</button>` : ''
    const sending = livePlaying(d)
    const liveBtn = can(d, 'live_audio') && liveSupported()
      ? `<button class="btn-icon sm ${sending ? 'on' : ''}" data-media-live="${id}" aria-pressed="${sending}" aria-label="${LIVE_TITLE}" title="${sending ? 'Ton dieses Rechners beenden' : LIVE_TITLE}">${HO.icon('cast', 'sm')}</button>` : ''
    return `<div class="row g6 media-keys">${skip('previous', 'Vorheriger Titel', 'media-prev')}
      <button class="btn-icon ${s.playing ? 'on' : ''}" data-media-act="playing" data-on="${s.playing ? 0 : 1}" data-id="${id}"
        aria-pressed="${!!s.playing}" aria-label="${s.playing ? 'Pause' : 'Wiedergabe'}" title="${s.playing ? 'Pause' : 'Wiedergabe'}">${HO.icon(s.playing ? 'pause' : 'play')}</button>
      ${skip('next', 'Nächster Titel', '')}${liveBtn}</div>`
  }

  // Volume: mute button, slider and value. Without write access the slider is only there for display.
  function volumeHTML(d, s) {
    if (s.volume == null) return ''
    const id = HO.esc(d.id), able = can(d, 'volume')
    return `<div class="row g8 media-vol">
      ${can(d, 'muted') ? `<button class="btn-icon sm ${s.muted ? 'on' : ''}" data-media-act="muted" data-on="${s.muted ? 0 : 1}" data-id="${id}"
        aria-pressed="${!!s.muted}" aria-label="${s.muted ? 'Ton an' : 'Stumm'}" title="${s.muted ? 'Ton an' : 'Stumm'}">${HO.icon(s.muted ? 'x' : 'media', 'sm')}</button>` : ''}
      <input type="range" min="0" max="100" step="1" value="${Math.round(s.volume)}" data-media-vol="${id}" aria-label="Lautstärke" ${able ? '' : 'disabled'}>
      <span class="small muted media-vol-val">${Math.round(s.volume)} %</span></div>`
  }

  // Choice "Favorit starten" – only if the device names favourites and they can be started.
  function favoritesHTML(d, media) {
    const names = (media && media.favorites) || []
    if (!names.length || !can(d, 'favorite')) return ''
    return `<select class="sm media-fav" data-media-fav="${HO.esc(d.id)}" aria-label="Favorit starten"><option value="">Favorit starten …</option>${
      names.map(name => `<option value="${HO.esc(name)}">${HO.esc(name)}</option>`).join('')}</select>`
  }

  // Tile of a speaker: cover picture, room (with group), title and artist, buttons, volume, favourites.
  function cardHTML(d) {
    const s = shown(d), media = s.media || {}
    const group = (media.group || []).length ? 'mit ' + media.group.join(', ') : ''
    const title = !s.online ? 'Kein Kontakt' : media.title || (s.playing ? 'Spielt' : 'Pausiert')
    return `<div class="media-card ${s.online ? '' : 'off'} ${s.playing && s.online ? 'is-on' : ''}" data-media="${HO.esc(d.id)}">
      ${artHTML(s.online ? media : null)}
      <div class="media-main">
        <div class="media-room" data-dev="${HO.esc(d.id)}">${HO.esc(d.name)}${group ? ` <span class="tiny muted">${HO.esc(group)}</span>` : ''}</div>
        <div class="media-title">${HO.esc(title)}</div>
        <div class="small muted media-sub">${HO.esc(s.online ? subText(media) : (d.status_detail || ''))}</div>
      </div>
      ${buttonsHTML(d, s)}
      ${s.online ? volumeHTML(d, s) : ''}
      ${s.online ? favoritesHTML(d, media) : ''}</div>`
  }

  function devices(list) {
    return (list || []).filter(speaker).sort((a, b) => HO.byName(a.name, b.name))
  }

  // Block of the home page: all speakers as tiles; without a speaker it stays empty (and thus invisible).
  function blockHTML(overview) {
    const list = devices(overview && overview.devices)
    if (!list.length) return ''
    return `<section class="section"><div class="section-head"><h3>Wiedergabe</h3></div>
      <div class="media-grid">${list.map(cardHTML).join('')}</div></section>`
  }

  // Send a command. The tapped values apply at once and until the device reports them; if the command fails,
  // the display jumps back and a message names the reason.
  async function send(d, state, done) {
    const values = {}
    for (const key of ['playing', 'volume', 'muted']) if (state[key] !== undefined) values[key] = state[key]
    if (Object.keys(values).length) held[d.id] = { until: Date.now() + HOLD_MS, values }
    if (done) done()
    try {
      await HO.api(`devices/${encodeURIComponent(d.id)}/command`, { method: 'POST', body: { state } })
    } catch (e) {
      delete held[d.id]
      HO.toast(e.message, 'bad')
    }
    if (done) done()
  }

  const find = id => (((HO.state.overview || {}).devices) || []).find(x => x.id === id)

  /* ---------- Sound of this computer -> speaker */
  // Firefox takes the sound from a recording device and needs no screen sharing for it (see liveStart).
  const liveFromInput = () => /Firefox\//.test(navigator.userAgent)
  // Can this browser share the sound of the computer and hand it over as samples?
  function liveSupported() {
    if (HO.demo && HO.demo.active) return false     // the demo speaker plays nothing real (demo.py)
    const devices = navigator.mediaDevices
    if (!devices || !(window.AudioContext || window.webkitAudioContext)) return false
    return !!(liveFromInput() ? devices.getUserMedia : devices.getDisplayMedia)
  }
  const livePlaying = d => !!(live && live.ids.indexOf(d.id) >= 0)

  // Let the speaker play the running transmission (through the shared switching path: device lock, event log).
  async function liveJoin(session, d) {
    await HO.api('audio/live/play', { method: 'POST', body: { device_id: d.id } })
    if (session.ids.indexOf(d.id) < 0) session.ids.push(d.id)
  }

  // Start a transmission for the speaker: ask the browser for the sound, set up the conversion, tell ComfyOS and
  // the speaker. If a transmission of this page already runs, the speaker simply joins it.
  async function liveStart(d, done) {
    if (live) {
      try { await liveJoin(live, d) } catch (e) { HO.toast(e.message, 'bad') }
      if (done) done()
      return
    }
    // Firefox offers the sharing dialog but never passes sound along. It can record from an input of the computer
    // instead: if the user picks "Stereomix" (or a virtual audio cable) there, that is the sound of the computer.
    const fromInput = liveFromInput()
    let stream
    try {
      if (fromInput) {
        HO.toast('Firefox: im Dialog als Mikrofon „Stereomix“ (oder ein virtuelles Audiokabel) wählen – das ist der Ton des Rechners.')
        // no echo cancelling, noise filter or level control: they are made for voices and would mangle music
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 2 } })
      } else {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
      }
    } catch (e) {
      return      // the user closed the browser's dialog: nothing to do
    }
    const tracks = stream.getAudioTracks()
    if (!tracks.length) {
      stream.getTracks().forEach(t => t.stop())
      HO.toast('Kein Ton geteilt. Im Dialog des Browsers „Systemaudio teilen“ (ganzer Bildschirm) oder „Tab-Audio teilen“ anhaken – das geht in Chrome und Edge.', 'bad')
      return
    }
    const AudioCtx = window.AudioContext || window.webkitAudioContext
    let context
    // Firefox cannot connect a recording to a context with another rate than the device's: there the device decides.
    try { context = fromInput ? new AudioCtx() : new AudioCtx({ sampleRate: LIVE_RATES[0] }) } catch (e) { context = new AudioCtx() }
    if (context.resume) context.resume().catch(() => { })     // some browsers start it paused; if it stays so, no sound goes out
    const rate = Math.round(context.sampleRate)
    const session = { ids: [], stream, context, node: null, rate, parts: [], size: 0, waiting: [], sending: false, open: false, done }
    if (LIVE_RATES.indexOf(rate) < 0) {
      liveRelease(session)
      HO.toast(`Die Abtastrate dieses Rechners (${rate} Hz) kann ComfyOS nicht weitergeben.`, 'bad')
      return
    }
    live = session
    // The picture is not needed. Its track stays alive, because some browsers end the whole sharing with it.
    const source = context.createMediaStreamSource(new MediaStream(tracks))
    const node = context.createScriptProcessor(4096, 2, 2)
    const silent = context.createGain()
    silent.gain.value = 0               // the node only runs while it leads to an output; nothing is heard twice
    node.onaudioprocess = e => liveCollect(session, e.inputBuffer)
    source.connect(node); node.connect(silent); silent.connect(context.destination)
    session.node = node
    tracks[0].addEventListener('ended', () => { if (live === session) liveStop() })    // "Freigabe beenden" in the browser
    if (done) done()
    try {
      await HO.api('audio/live/start', { method: 'POST', body: { rate } })
      session.open = true               // from now on pieces go out – the speaker needs them to start
      await liveJoin(session, d)
      if (live === session) HO.toast(`${d.name} spielt den Ton dieses Rechners`)
    } catch (e) {
      HO.toast(e.message, 'bad')
      if (live === session) await liveStop()
    }
    if (done) done()
  }

  // Take the sound the browser just delivered: to 16-bit samples, left and right in turn; send once enough is there.
  function liveCollect(session, buffer) {
    if (live !== session || !session.open) return
    const left = buffer.getChannelData(0), right = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : left
    const pcm = new Int16Array(left.length * 2)
    for (let i = 0; i < left.length; i++) {
      const l = Math.max(-1, Math.min(1, left[i])), r = Math.max(-1, Math.min(1, right[i]))
      pcm[2 * i] = l < 0 ? l * 32768 : l * 32767
      pcm[2 * i + 1] = r < 0 ? r * 32768 : r * 32767
    }
    session.parts.push(pcm)
    session.size += pcm.length
    if (session.size < session.rate * 2 * LIVE_SEND_SECONDS) return
    const piece = new Int16Array(session.size)
    let at = 0
    for (const part of session.parts) { piece.set(part, at); at += part.length }
    session.parts = []
    session.size = 0
    session.waiting.push(piece.buffer)
    if (session.waiting.length > LIVE_MAX_WAITING) session.waiting.shift()    // the network is too slow: rather a gap than an ever growing delay
    liveSend(session)
  }

  // Send the waiting pieces one after the other (in order). If ComfyOS says the transmission is over, stop here too.
  async function liveSend(session) {
    if (session.sending) return
    session.sending = true
    while (live === session && session.waiting.length) {
      const body = session.waiting.shift()
      try {
        await HO.api('audio/live/chunk', { method: 'POST', body, raw: 'application/octet-stream' })
      } catch (e) {
        if (e.status === 409 || e.status === 401) {
          HO.toast('Die Übertragung des Tons wurde beendet.', 'bad')
          liveStop(true)
          break
        }
        // anything else (a short network hiccup): this piece is lost, the next one follows
      }
    }
    session.sending = false
  }

  // Give back what the browser lent: the sharing and the audio context.
  function liveRelease(session) {
    if (session.node) { session.node.onaudioprocess = null; session.node.disconnect() }
    session.stream.getTracks().forEach(t => t.stop())
    if (session.context.close) session.context.close().catch(() => { })    // already closed: nothing left to do
  }

  // End the transmission: release the browser's sharing, tell ComfyOS (it stops the speakers) and redraw.
  // `serverKnows` = ComfyOS ended it itself, no need to tell it.
  async function liveStop(serverKnows) {
    const session = live
    if (!session) return
    live = null
    liveRelease(session)
    if (!serverKnows) {
      try { await HO.api('audio/live/stop', { method: 'POST' }) } catch (e) { HO.toast(e.message, 'bad') }
    }
    if (session.done) session.done()
  }

  // Is the choice "Favorit starten" inside `box` in use right now? An open list has the focus; it gives it back
  // after choosing (handle) or when the user taps somewhere else.
  function choosing(box) {
    const active = document.activeElement
    return !!(box && active && active.matches && active.matches('select.media-fav') && box.contains(active))
  }

  // Carry out a click (buttons) or change (volume, favourite) in a tile. true = the event belonged here.
  function handle(event, done) {
    const target = event.target
    if (!target.closest) return false
    if (event.type === 'change') {
      const volume = target.closest('[data-media-vol]'), favorite = target.closest('[data-media-fav]')
      const d = find(volume ? volume.dataset.mediaVol : favorite ? favorite.dataset.mediaFav : '')
      if (!d) return !!(volume || favorite)
      if (favorite) favorite.blur()      // chosen: the tiles may be redrawn again (choosing)
      if (volume) send(d, { volume: Number(volume.value) }, done)
      else if (favorite.value) send(d, { favorite: favorite.value }, done)
      return true
    }
    const liveButton = target.closest('[data-media-live]')
    if (liveButton) {
      event.stopPropagation()
      const d = find(liveButton.dataset.mediaLive)
      if (d && livePlaying(d)) liveStop()
      else if (d) liveStart(d, done)
      return true
    }
    const button = target.closest('[data-media-act]')
    if (!button) return false
    event.stopPropagation()
    const d = find(button.dataset.id), act = button.dataset.mediaAct
    if (!d) return true
    const state = {}
    state[act] = act === 'playing' || act === 'muted' ? button.dataset.on === '1' : true
    send(d, state, done)
    return true
  }

  HO.media = { devices, stateText, cardHTML, blockHTML, handle, send, choosing }
})(window.HO)
