/* ComfyOS – plan symbols: the shapes a device (or a group) can take in the floor plan instead of the standard bubble.

   Every symbol is drawn from above, like the floor plan itself, with its front towards the bottom edge (the back of a
   sofa, the screen side of a TV board and the head of a bed are at the top) – the user turns it to the wall it stands at.

   Drawing rules (keep them when adding a symbol):
   - box of 48 × 48 units, the shape is centred on (24, 24); `w`/`h` = the extent it really covers (for the status dot,
     the marks, the name below and the selection frame); `round: true` for circular shapes
   - plain elements are bodies (filled surface with an outline), class `f` = a face lying on a body (cushion, pillow,
     platter), class `l` = a line without a fill, class `k` = a solid ink mark (screen, knob, light) – colours and line
     width come from app.css (`.fp-sym`), never from the symbol
   - details at least 4 units apart: the symbol must still read at 30 pixels (group bubble, small plans)
   - keys are stored in floorplan.json (`devices[id].furniture`, `groups[].icon`) – never rename one */
(function (HO) {
  'use strict'
  const leaf = 'M24 22C19.5 17.5 20 11 24 7c4 4 4.5 10.5 0 15z', sprout = 'M24 22.5c-2.8-3-2.6-7.5 0-10.5 2.6 3 2.8 7.5 0 10.5z'
  const blade = 'M24 23c-4.5-2.5-5.5-7.5-2-11.5 4 2 5 7.5 2 11.5z'
  const turned = (path, cls, angles) => angles.map(a => `<path${cls ? ` class="${cls}"` : ''} transform="rotate(${a} 24 24)" d="${path}"/>`).join('')

  HO.planSymbols = {
    // entertainment and office
    tv: { label: 'Fernseher', w: 42, h: 13,
      svg: '<rect x="3" y="17.5" width="42" height="13" rx="2.5"/><rect class="k" x="7" y="21.5" width="34" height="3.2" rx="1.6"/><path class="l" d="M19 27.5h10"/>' },
    soundbar: { label: 'Soundbar', w: 40, h: 9,
      svg: '<rect x="4" y="19.5" width="40" height="9" rx="4.5"/><circle class="k" cx="12" cy="24" r="1.1"/><circle class="k" cx="18" cy="24" r="1.1"/><circle class="k" cx="24" cy="24" r="1.1"/><circle class="k" cx="30" cy="24" r="1.1"/><circle class="k" cx="36" cy="24" r="1.1"/>' },
    speaker: { label: 'Lautsprecher', w: 22, h: 22,
      svg: '<rect x="13" y="13" width="22" height="22" rx="6"/><circle class="f" cx="24" cy="24" r="6.5"/><circle class="k" cx="24" cy="24" r="1.8"/>' },
    hifi: { label: 'HiFi-Anlage', w: 40, h: 26,
      svg: '<rect x="4" y="11" width="40" height="26" rx="3"/><circle class="f" cx="19" cy="24" r="9.5"/><circle class="l" cx="19" cy="24" r="4"/><circle class="k" cx="19" cy="24" r="1.2"/><path class="l" d="M37 15.5v9L30.5 31M34.5 33.5h5"/><circle class="k" cx="37" cy="15.5" r="1.6"/>' },
    console: { label: 'Spielkonsole', w: 34, h: 16,
      svg: '<path d="M14 16h20a7 7 0 0 1 7 7v2a6 6 0 0 1-11 3.3l-.8-1.3H18.8l-.8 1.3A6 6 0 0 1 7 25v-2a7 7 0 0 1 7-7z"/><path class="l" d="M12.5 22h5.5M15.2 19.3v5.4"/><circle class="k" cx="31" cy="20.5" r="1.2"/><circle class="k" cx="34.2" cy="23.2" r="1.2"/>' },
    computer: { label: 'Computer', w: 29, h: 19,
      svg: '<rect class="k" x="10" y="14" width="28" height="3.2" rx="1.6"/><path class="l" d="M19 20h10"/><rect x="10" y="24" width="20" height="9" rx="2"/><path class="l" d="M13.5 27h13M13.5 30h13"/><rect x="33" y="24.5" width="5" height="8" rx="2.5"/>' },
    printer: { label: 'Drucker', w: 28, h: 30,
      svg: '<rect class="f" x="15" y="9" width="18" height="8" rx="1"/><rect class="f" x="15" y="31" width="18" height="8" rx="1"/><rect x="10" y="15" width="28" height="18" rx="3"/><path class="l" d="M15 20h18M18.5 36h11"/><circle class="k" cx="33" cy="28" r="1.1"/>' },
    router: { label: 'Router', w: 26, h: 23,
      svg: '<path class="l" d="M16 21.5v-7M32 21.5v-7"/><circle class="k" cx="16" cy="13.5" r="1.3"/><circle class="k" cx="32" cy="13.5" r="1.3"/><rect x="11" y="21.5" width="26" height="14" rx="3"/><circle class="k" cx="17" cy="28.5" r="1.1"/><circle class="k" cx="21.5" cy="28.5" r="1.1"/><circle class="k" cx="26" cy="28.5" r="1.1"/>' },
    camera: { label: 'Kamera', w: 24, h: 30,
      svg: '<rect x="17" y="8" width="14" height="10" rx="2.5"/><rect class="k" x="21" y="18" width="6" height="2.5" rx="1"/><path class="l" d="M21.5 23.5 12 38M26.5 23.5 36 38M15.5 33a14 14 0 0 0 17 0"/>' },
    // light
    'floor-lamp': { label: 'Stehlampe', w: 36, h: 36,
      svg: '<path class="l" d="M12 36C14 26 22 18 32 16"/><circle cx="12" cy="36" r="5.5"/><circle class="k" cx="12" cy="36" r="1.4"/><circle cx="32" cy="16" r="10"/><circle class="f" cx="32" cy="16" r="4.2"/>' },
    'ceiling-lamp': { label: 'Deckenleuchte', w: 22, h: 22, round: true,
      svg: '<circle cx="24" cy="24" r="11"/><path class="l" d="M16.2 16.2l15.6 15.6M31.8 16.2 16.2 31.8"/>' },
    'light-bar': { label: 'Lichtleiste', w: 40, h: 21,
      svg: '<rect x="4" y="20.5" width="40" height="7" rx="3.5"/><path class="l" d="M9.5 24h29M12 16v-2.5M24 16v-2.5M36 16v-2.5M12 32v2.5M24 32v2.5M36 32v2.5"/>' },
    // furniture
    sofa: { label: 'Sofa', w: 40, h: 20,
      svg: '<rect x="4" y="14" width="40" height="20" rx="3"/><rect class="f" x="9.5" y="20" width="14" height="11" rx="2"/><rect class="f" x="24.5" y="20" width="14" height="11" rx="2"/>' },
    'corner-sofa': { label: 'Ecksofa', w: 40, h: 34,
      svg: '<path d="M7 7h34a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H25v13a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V10a3 3 0 0 1 3-3z"/><rect class="f" x="10" y="13" width="12" height="9" rx="2"/><rect class="f" x="23.5" y="13" width="15" height="9" rx="2"/><rect class="f" x="10" y="23.5" width="12" height="14.5" rx="2"/>' },
    armchair: { label: 'Sessel', w: 22, h: 22,
      svg: '<rect x="13" y="13" width="22" height="22" rx="4"/><rect class="f" x="18" y="19" width="12" height="13" rx="2"/>' },
    bed: { label: 'Doppelbett', w: 32, h: 38,
      svg: '<rect x="8" y="5" width="32" height="38" rx="3"/><rect class="f" x="11" y="8" width="12" height="8" rx="2.5"/><rect class="f" x="25" y="8" width="12" height="8" rx="2.5"/><path class="l" d="M8 21h32M31 21q1 7 9 8"/>' },
    'single-bed': { label: 'Einzelbett', w: 20, h: 38,
      svg: '<rect x="14" y="5" width="20" height="38" rx="3"/><rect class="f" x="17.5" y="8" width="13" height="8" rx="2.5"/><path class="l" d="M14 21h20M27 21q1 6 7 7"/>' },
    table: { label: 'Esstisch', w: 42, h: 32,
      svg: '<rect class="f" x="13" y="8" width="8" height="4.5" rx="1.5"/><rect class="f" x="27" y="8" width="8" height="4.5" rx="1.5"/><rect class="f" x="13" y="35.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="27" y="35.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="3" y="20" width="4.5" height="8" rx="1.5"/><rect class="f" x="40.5" y="20" width="4.5" height="8" rx="1.5"/><rect x="9" y="14" width="30" height="20" rx="2.5"/>' },
    'round-table': { label: 'Runder Tisch', w: 36, h: 36, round: true,
      svg: '<rect class="f" x="20" y="6" width="8" height="4.5" rx="1.5"/><rect class="f" x="20" y="37.5" width="8" height="4.5" rx="1.5"/><rect class="f" x="6" y="20" width="4.5" height="8" rx="1.5"/><rect class="f" x="37.5" y="20" width="4.5" height="8" rx="1.5"/><circle cx="24" cy="24" r="10.5"/>' },
    desk: { label: 'Schreibtisch', w: 40, h: 30,
      svg: '<rect x="4" y="9" width="40" height="18" rx="2.5"/><rect class="k" x="15" y="12" width="18" height="2.6" rx="1.3"/><rect class="f" x="17" y="18.5" width="14" height="5" rx="1.2"/><rect class="f" x="18" y="30" width="12" height="9" rx="3"/>' },
    plant: { label: 'Pflanze', w: 34, h: 34, round: true,
      svg: turned(sprout, 'f', [60, 180, 300]) + turned(leaf, '', [0, 120, 240]) + '<circle class="k" cx="24" cy="24" r="1.6"/>' },
    bathtub: { label: 'Badewanne', w: 38, h: 20,
      svg: '<rect x="5" y="14" width="38" height="20" rx="5"/><rect class="f" x="9" y="17.5" width="30" height="13" rx="6.5"/><circle class="k" cx="14.5" cy="24" r="1.2"/>' },
    // household
    fridge: { label: 'Kühlschrank', w: 24, h: 25,
      svg: '<rect x="12" y="11" width="24" height="21" rx="3"/><rect class="f" x="12" y="32" width="24" height="4" rx="1.5"/><path class="l" d="M24 16v11M19.2 18.8l9.6 5.4M28.8 18.8l-9.6 5.4"/>' },
    washer: { label: 'Waschmaschine', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><circle class="f" cx="24" cy="26" r="7"/><circle class="l" cx="24" cy="26" r="3.2"/><path class="l" d="M16 15.5h5"/><circle class="k" cx="31.5" cy="15.5" r="1.1"/>' },
    dishwasher: { label: 'Spülmaschine', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><path class="l" d="M12 18h24M20 15h8"/><circle class="f" cx="19.2" cy="27" r="4.2"/><circle class="f" cx="28.8" cy="27" r="4.2"/><circle class="k" cx="19.2" cy="27" r="1.1"/><circle class="k" cx="28.8" cy="27" r="1.1"/>' },
    stove: { label: 'Herd', w: 24, h: 24,
      svg: '<rect x="12" y="12" width="24" height="24" rx="3"/><circle class="f" cx="18.5" cy="18.5" r="3.6"/><circle class="f" cx="29.5" cy="18.5" r="3.6"/><circle class="f" cx="18.5" cy="29.5" r="3.6"/><circle class="f" cx="29.5" cy="29.5" r="3.6"/>' },
    coffee: { label: 'Kaffeemaschine', w: 20, h: 24,
      svg: '<rect class="f" x="17" y="26" width="14" height="10" rx="2"/><rect x="14" y="12" width="20" height="16" rx="3"/><path class="l" d="M18.5 17h11"/><circle cx="24" cy="31.5" r="3"/><circle class="k" cx="24" cy="31.5" r="1.3"/>' },
    fan: { label: 'Ventilator', w: 28, h: 28, round: true,
      svg: '<circle cx="24" cy="24" r="14"/>' + turned(blade, 'f', [0, 120, 240]) + '<circle class="k" cx="24" cy="24" r="1.8"/>' },
    vacuum: { label: 'Saugroboter', w: 26, h: 26, round: true,
      svg: '<circle cx="24" cy="24" r="13"/><circle class="f" cx="24" cy="20" r="4.5"/><circle class="k" cx="24" cy="20" r="1.2"/><path class="l" d="M14.5 29.5a11 11 0 0 0 19 0"/>' },
    car: { label: 'Auto', w: 27, h: 40,
      svg: '<rect x="13" y="4" width="22" height="40" rx="7"/><path class="f" d="M16.5 15c5-2 10-2 15 0l-1.5 6H18z"/><path class="f" d="M18 33h12l1.5 4.5c-5 1.5-10 1.5-15 0z"/><path class="l" d="M18 21v12M30 21v12M13 16l-2.5 1M35 16l2.5 1"/>' }
  }

  // A symbol as a small picture of its own (editor: symbol picker).
  HO.planSymbolIcon = key => {
    const symbol = HO.planSymbols[key]
    return symbol ? `<svg class="fp-sym-pic" viewBox="0 0 48 48" aria-hidden="true"><g class="fp-sym">${symbol.svg}</g></svg>` : ''
  }
})(window.HO)
