/* ComfyOS – camera of the floor plan: recognise gestures and display them smoothly.

   The floor plan is an SVG; its section is the `viewBox`. Every change to it makes the browser repaint the whole
   image – with shadows and blurs that costs a multiple of a frame on older devices.
   Therefore since 0.20: during a gesture the image already painted is only moved and scaled (CSS
   `transform`, the graphics card does that), and only at rest is the section redrawn once.

   A camera is {cx, cy, s, cw, ch}: centre in plan units, s = pixels per unit, cw/ch = size of the
   drawing surface in pixels. Whoever holds the camera (floorplan.js) only states here how to read and set it.

   HO.planCamera.between(from, to)        -> {k, tx, ty}  scale and translation with which an image painted at
                                              `from` lies the way it would be painted at `to`
   HO.planCamera.gestures(surface, hooks) -> {zoomAt(factor, point)}
     hooks.enabled()      gestures allowed right now? (not in the editor)
     hooks.canPan()       does a single finger pan the plan? (view at overall size: no – it scrolls the page)
     hooks.wheel(e)       does this mouse wheel event zoom? (full screen: always; view: only with Ctrl or pinching on
                          the trackpad – the mouse wheel alone scrolls the page there)
     hooks.doubleClick()  does a double click zoom in? (full screen only)
     hooks.local(e)       pointer position in pixels of the drawing surface
     hooks.get()          current camera
     hooks.set(camera)    set the camera (the holder limits and displays it)
     hooks.limit(s)       limit the scale to the allowed range
     hooks.begin()        a gesture starts to take effect
     hooks.end()          the gesture is over: draw the section for good
     hooks.multi()        a second finger joined (a tap that was begun no longer counts)
     hooks.tap(target)    short tap without movement; target = element hit with data-kind, or null

   Gestures: one finger or the mouse pans, two fingers zoom and pan at once, mouse wheel and
   trackpad zoom (full screen always, view with Ctrl/pinching), a double click zooms in (full screen, mouse only). */
(function (HO) {
  'use strict'
  const TAP_MOVE = 8      // pixels: less movement still counts as a tap
  const TAP_MS = 650      // milliseconds: held longer is no longer a tap
  const MIN_SPAN = 10     // pixels: smallest distance of two fingers that is computed with

  // Scale and translation between two cameras on the same drawing surface (origin of the transform: top left)
  function between(from, to) {
    const k = to.s / from.s
    return { k, tx: to.cw / 2 * (1 - k) + (from.cx - to.cx) * to.s, ty: to.ch / 2 * (1 - k) + (from.cy - to.cy) * to.s }
  }

  // Point of the plan under a place of the drawing surface
  const planAt = (c, q) => [c.cx + (q[0] - c.cw / 2) / c.s, c.cy + (q[1] - c.ch / 2) / c.s]

  // Set up gestures on a drawing surface: drag, zoom with two fingers, mouse wheel, double click, tap. What the
  // camera makes of it is determined by the hooks; returns { zoomAt } for the buttons.
  function gestures(surface, hooks) {
    const pointers = new Map()      // pointer id -> position on the drawing surface
    let gesture = null, lastType = 'mouse', wheelTimer = 0
    const middle = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
    const span = (a, b) => Math.max(MIN_SPAN, Math.hypot(a[0] - b[0], a[1] - b[1]))
    const capture = id => { try { surface.setPointerCapture(id) } catch (err) { } }

    function startPan(at, moved) {
      const c = hooks.get()
      gesture = { mode: 'pan', start: at, centre: [c.cx, c.cy], since: Date.now(), moved, target: null }
    }
    // Two fingers are down: remember the distance and the point of the plan between them.
    function startPinch() {
      const [a, b] = [...pointers.values()], c = hooks.get()
      // the point of the plan between the fingers stays under the fingers
      gesture = { mode: 'pinch', span: span(a, b), scale: c.s, anchor: planAt(c, middle(a, b)), moved: true }
      hooks.begin()
    }
    // zoom around a point of the drawing surface: what lies there stays there
    function zoomAt(factor, at) {
      const c = hooks.get(), fixed = planAt(c, at), s = hooks.limit(c.s * factor)
      hooks.set({ s, cx: fixed[0] - (at[0] - c.cw / 2) / s, cy: fixed[1] - (at[1] - c.ch / 2) / s })
    }

    surface.addEventListener('pointerdown', e => {
      if (!hooks.enabled() || (e.pointerType === 'mouse' && e.button > 0)) return
      lastType = e.pointerType
      pointers.set(e.pointerId, hooks.local(e))
      if (pointers.size === 1) {
        if (!hooks.canPan()) { gesture = null; return }   // the one finger belongs to the page
        capture(e.pointerId)
        startPan(hooks.local(e), false)
        gesture.target = e.target.closest ? e.target.closest('[data-kind]') : null
      } else if (pointers.size === 2) {
        pointers.forEach((at, id) => capture(id))
        hooks.multi()
        startPinch()
      }
    })

    surface.addEventListener('pointermove', e => {
      if (!pointers.has(e.pointerId)) return
      const at = hooks.local(e)
      pointers.set(e.pointerId, at)
      if (!gesture) return
      const c = hooks.get()
      if (gesture.mode === 'pan') {
        const dx = at[0] - gesture.start[0], dy = at[1] - gesture.start[1]
        if (!gesture.moved && Math.hypot(dx, dy) < TAP_MOVE) return
        if (!gesture.moved) { gesture.moved = true; hooks.begin() }
        hooks.set({ s: c.s, cx: gesture.centre[0] - dx / c.s, cy: gesture.centre[1] - dy / c.s })
      } else if (pointers.size >= 2) {
        const [a, b] = [...pointers.values()], m = middle(a, b)
        const s = hooks.limit(gesture.scale * span(a, b) / gesture.span)
        hooks.set({ s, cx: gesture.anchor[0] - (m[0] - c.cw / 2) / s, cy: gesture.anchor[1] - (m[1] - c.ch / 2) / s })
      }
    })

    // A pointer lifts: from two fingers to one keep panning, otherwise end the gesture – a short tap without
    // movement counts as a tap.
    const release = e => {
      if (!pointers.has(e.pointerId)) return
      const was = gesture
      pointers.delete(e.pointerId)
      if (pointers.size === 1 && was) {
        // from two fingers to one: keep panning if one finger may do that
        if (hooks.canPan()) { startPan([...pointers.values()][0], true); return }
        gesture = null
        hooks.end()
        return
      }
      if (pointers.size) return
      gesture = null
      if (!was) return
      if (e.type === 'pointerup' && was.mode === 'pan' && !was.moved && Date.now() - was.since < TAP_MS) hooks.tap(was.target)
      hooks.end()
    }
    surface.addEventListener('pointerup', release)
    surface.addEventListener('pointercancel', release)

    surface.addEventListener('wheel', e => {
      if (!hooks.enabled() || !hooks.wheel(e)) return
      e.preventDefault()
      const c = hooks.get()
      hooks.begin()
      // trackpad: swiping with two fingers pans; mouse wheel and pinching (ctrlKey) zoom around the pointer
      if (!e.ctrlKey && e.deltaX) hooks.set({ s: c.s, cx: c.cx + e.deltaX / c.s, cy: c.cy + e.deltaY / c.s })
      else zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), hooks.local(e))
      clearTimeout(wheelTimer)
      wheelTimer = setTimeout(hooks.end, 160)
    }, { passive: false })

    surface.addEventListener('dblclick', e => {
      if (!hooks.enabled() || !hooks.doubleClick() || lastType !== 'mouse') return
      hooks.begin()
      zoomAt(1.8, hooks.local(e))
      hooks.end()
    })

    // Two fingers belong to the plan – otherwise the browser zooms or scrolls the page meanwhile.
    surface.addEventListener('touchmove', e => {
      if (hooks.enabled() && e.touches && e.touches.length > 1 && e.cancelable) e.preventDefault()
    }, { passive: false })
    surface.addEventListener('gesturestart', e => { if (hooks.enabled()) e.preventDefault() })   // iOS: eigene Zoomgeste der Seite

    return { zoomAt }
  }

  HO.planCamera = { between, gestures, planAt, TAP_MOVE, TAP_MS }
})(window.HO)
