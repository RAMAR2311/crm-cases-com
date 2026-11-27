const CACHE_NAME = 'cases-app-v3';
const ASSETS_TO_CACHE = [
    '/',
    '/static/manifest.json',
    '/static/img/cases.png?v=3',
    '/static/img/icons/icon-192x192.png?v=3',
    '/static/img/icons/icon-512x512.png?v=3',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                return cache.addAll(ASSETS_TO_CACHE);
            })
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        return caches.delete(cache);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    // Para las peticiones de navegación o de API, usamos estrategia Network First
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Hacemos una copia para guardar en caché
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    // Evitamos guardar en caché POST/PUT o extensiones raras si es necesario
                    if(event.request.method === 'GET' && !event.request.url.includes('/api/')){
                        cache.put(event.request, responseClone);
                    }
                });
                return response;
            })
            .catch(() => {
                // Fallback a caché si no hay red
                return caches.match(event.request);
            })
    );
});
