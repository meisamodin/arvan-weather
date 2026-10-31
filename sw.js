// اروان ودر — service worker
const V = 'aw-v1.22.0';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'privacy.html', 'tools.html', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/maskable-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // App shell: network first (so updates arrive), cache fallback for offline
  if (url.origin === location.origin) {
    e.respondWith(fetch(req).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(V).then(c => c.put(req, cp)); }
      return r;
    }).catch(() => caches.match(req).then(r => r || caches.match('index.html'))));
    return;
  }
  // Font: stale-while-revalidate
  if (url.hostname === 'cdn.jsdelivr.net') {
    e.respondWith(caches.open(V).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
  // Weather APIs and map tiles go straight to the network (the app caches weather data itself)
});

// Morning summary via Periodic Background Sync (installed Chrome/Android PWA only)
const WCF={0:'آسمان صاف',1:'عمدتاً صاف',2:'نیمه‌ابری',3:'ابری',45:'مه',48:'مه',51:'نم‌نم باران',53:'نم‌نم باران',55:'نم‌نم باران',61:'باران سبک',63:'باران',65:'باران شدید',71:'برف سبک',73:'برف',75:'برف سنگین',80:'رگبار',81:'رگبار',82:'رگبار شدید',85:'رگبار برف',86:'رگبار برف',95:'رعد و برق',96:'رعد و برق و تگرگ',99:'رعد و برق و تگرگ'};
self.addEventListener('periodicsync', e => {
  if (e.tag !== 'aw-daily') return;
  e.waitUntil((async () => {
    const cc = await caches.open('aw-cfg'); const r = await cc.match('cfg'); if (!r) return;
    const cfg = await r.json(); if (!cfg.morning || !cfg.city) return;
    const h = new Date().getHours(); if (h < 5 || h > 11) return;
    const day = new Date().toDateString(), last = await cc.match('last');
    if (last && (await last.text()) === day) return;
    const c = cfg.city, j = await (await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${c.lat}&longitude=${c.lon}&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&current=temperature_2m&timezone=auto&forecast_days=1`)).json();
    const f = x => { const v = cfg.t === 'f' ? x * 9 / 5 + 32 : x; return Math.round(v).toLocaleString('fa-IR') + '°'; };
    const d = j.daily;
    await self.registration.showNotification('🌤️ هوای امروز ' + c.name, { body: `${WCF[d.weather_code[0]] || ''} · ${f(d.temperature_2m_min[0])} تا ${f(d.temperature_2m_max[0])} · احتمال بارش ${d.precipitation_probability_max[0].toLocaleString('fa-IR')}٪ · الان ${f(j.current.temperature_2m)}`, icon: 'icons/icon-192.png', badge: 'icons/icon-192.png', tag: 'aw-morning', dir: 'rtl', lang: 'fa' });
    await cc.put('last', new Response(day));
  })().catch(() => {}));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    for (const c of cs) if ('focus' in c) return c.focus();
    return self.clients.openWindow('./');
  }));
});
