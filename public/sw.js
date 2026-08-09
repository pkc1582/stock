const CACHE_PREFIX = 'compound-asset-2045'
const CACHE_VERSION = 'v4'
const STATIC_CACHE = `${CACHE_PREFIX}-static-${CACHE_VERSION}`
const DATA_CACHE = `${CACHE_PREFIX}-data-${CACHE_VERSION}`
const BASE_PATH = '/stock/'
const CORE_ASSETS = [
  BASE_PATH,
  `${BASE_PATH}offline.html`,
  `${BASE_PATH}manifest.webmanifest`,
  `${BASE_PATH}icons/icon-192.png`,
  `${BASE_PATH}icons/icon-512.png`,
  `${BASE_PATH}icons/icon-maskable-512.png`,
  `${BASE_PATH}icons/apple-touch-icon.png`,
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && ![STATIC_CACHE, DATA_CACHE].includes(key))
          .map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  )
})

const cacheResponse = async (cacheName, request, response) => {
  if (!response || !response.ok || response.type !== 'basic') return response
  const cache = await caches.open(cacheName)
  await cache.put(request, response.clone())
  return response
}

const networkFirst = async (request, cacheName, fallbackUrl = null) => {
  try {
    const response = await fetch(request)
    return await cacheResponse(cacheName, request, response)
  } catch {
    const cached = await caches.match(request, { ignoreSearch: true })
    if (cached) return cached
    if (fallbackUrl) return caches.match(fallbackUrl)
    throw new Error('offline and no cached response')
  }
}

const staleWhileRevalidate = async (request) => {
  const cached = await caches.match(request, { ignoreSearch: true })
  const network = fetch(request)
    .then((response) => cacheResponse(STATIC_CACHE, request, response))
    .catch(() => null)
  return cached || network
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin || !url.pathname.startsWith(BASE_PATH)) return

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, STATIC_CACHE, `${BASE_PATH}offline.html`))
    return
  }

  if (url.pathname.startsWith(`${BASE_PATH}data/`)) {
    event.respondWith(networkFirst(request, DATA_CACHE))
    return
  }

  if (['script', 'style', 'image', 'font'].includes(request.destination)) {
    event.respondWith(staleWhileRevalidate(request))
  }
})
