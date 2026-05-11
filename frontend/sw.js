// SOSFiler Service Worker — PWA Offline Support
const CACHE_NAME = 'sosfiler-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/app.html',
  '/dashboard.html',
  '/chat-widget.css',
  '/chat-widget.js',
  '/manifest.json'
];

// Install — cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// Fetch — network first, cache fallback for pages; cache first for static
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // Skip API calls — always go to network
  if (url.pathname.startsWith('/api/')) {
    return;
  }
  
  // For HTML pages: network first, cache fallback
  if (event.request.mode === 'navigate' || event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request) || caches.match('/index.html'))
    );
    return;
  }
  
  // For other assets: cache first, network fallback
  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      });
    })
  );
});
