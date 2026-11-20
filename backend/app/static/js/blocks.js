/* ComfyOS – blocks: the building blocks of the home page (since 0.20) and the seam for the widget system (stage 0.23)

   A block shows a part of the home – greeting, key figures, rooms, presence. Today the home page puts
   its blocks together in a fixed order; from 0.23 one chooses and arranges them freely. So that this works without a
   rebuild, every block already knows a fixed contract:

     HO.blocks.register(id, { title, icon, needs, render, after })
       id            unique name (e.g. 'rooms')
       title, icon   name and icon for the later selection
       needs         capabilities (keys from `caps` of a device row, e.g. 'on' or 'playing') of which
                     at least one device must have one – otherwise the block has nothing to show. Empty = always.
                     That way devices of a new plugin belong automatically: what counts is what a device can do,
                     not where it comes from.
       render(ctx)   -> HTML of the block. ctx = { overview, page } (overview = response of /api/overview)
       after(el, ctx) optional: after a rewrite (e.g. bring the tile grid up to date)

     HO.blocks.draw(id, el, ctx)   draw a block into its element – written only if the HTML changes
                                   (HO.setHTML). Returns true if it was rewritten.
     HO.blocks.get(id), HO.blocks.list()
     HO.blocks.available(overview) blocks whose `needs` the home currently meets

   Capabilities of the devices (one source for interface and widgets):
     HO.caps.has(device, key, writable)        can the device do that (and can it be set)?
     HO.caps.devices(devices, key, writable)   all devices with this capability – independent of the plugin */
(function (HO) {
  'use strict'
  const registry = {}, order = []

  HO.caps = {
    has(device, key, writable) {
      const caps = (device && device.caps) || []
      return caps.some(c => c.key === key && (!writable || c.writable))
    },
    devices(devices, key, writable) { return (devices || []).filter(d => HO.caps.has(d, key, writable)) }
  }

  HO.blocks = {
    // Register a block (or replace it); the order of the first registration stays.
    register(id, block) {
      if (!id || typeof block.render !== 'function') throw new Error('Block braucht einen Namen und render()')
      if (!registry[id]) order.push(id)
      registry[id] = Object.assign({ id, title: id, icon: 'info', needs: [] }, block)
      return registry[id]
    },
    get(id) { return registry[id] || null },
    list() { return order.map(id => registry[id]) },
    available(overview) {
      const devices = (overview && overview.devices) || []
      return HO.blocks.list().filter(b => !b.needs.length || b.needs.some(key => HO.caps.devices(devices, key).length))
    },
    // Draw a block into its element – only if the HTML has changed; returns whether it was drawn.
    draw(id, el, ctx) {
      const block = registry[id]
      if (!block || !el) return false
      const changed = HO.setHTML(el, block.render(ctx))
      if (changed && block.after) block.after(el, ctx)
      return changed
    }
  }
})(window.HO)
