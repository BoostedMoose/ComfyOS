/* ComfyOS – service worker, only for push notifications (flow node "Benachrichtigung").
   Deliberately without a fetch handler: the app is never served from a cache, updates take effect at once. */
self.addEventListener('install', function () { self.skipWaiting() })
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()) })

self.addEventListener('push', function (e) {
  var d = {}
  try { d = e.data ? e.data.json() : {} } catch (err) { d = { body: e.data ? e.data.text() : '' } }
  e.waitUntil(self.registration.showNotification(d.title || 'ComfyOS', {
    body: d.body || '', icon: '/static/img/icon-192.png', badge: '/static/img/icon-192.png',
    data: { url: d.url || '/' }
  }))
})

self.addEventListener('notificationclick', function (e) {
  e.notification.close()
  var url = (e.notification.data && e.notification.data.url) || '/'
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) { if ('focus' in list[i]) return list[i].focus() }
    return self.clients.openWindow(url)
  }))
})
