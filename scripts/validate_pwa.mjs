import { access, readFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const dist = resolve(root, 'dist')
const requiredFiles = [
  'index.html',
  'manifest.webmanifest',
  'sw.js',
  'offline.html',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-maskable-512.png',
  'icons/apple-touch-icon.png',
]

for (const file of requiredFiles) {
  const path = resolve(dist, file)
  await access(path)
  const details = await stat(path)
  if (details.size === 0) throw new Error(`PWA output is empty: ${file}`)
}

const expectedIconSizes = {
  'icons/icon-192.png': [192, 192],
  'icons/icon-512.png': [512, 512],
  'icons/icon-maskable-512.png': [512, 512],
  'icons/apple-touch-icon.png': [180, 180],
}
for (const [file, [expectedWidth, expectedHeight]] of Object.entries(expectedIconSizes)) {
  const png = await readFile(resolve(dist, file))
  const isPng = png.subarray(1, 4).toString('ascii') === 'PNG'
  const width = png.readUInt32BE(16)
  const height = png.readUInt32BE(20)
  if (!isPng || width !== expectedWidth || height !== expectedHeight) {
    throw new Error(`Invalid PWA icon dimensions: ${file} (${width}x${height})`)
  }
}

const manifest = JSON.parse(await readFile(resolve(dist, 'manifest.webmanifest'), 'utf8'))
if (manifest.start_url !== '/stock/' || manifest.scope !== '/stock/') {
  throw new Error('PWA manifest must stay scoped to the GitHub Pages /stock/ path')
}
if (manifest.display !== 'standalone') throw new Error('PWA display mode must be standalone')
if (!Array.isArray(manifest.icons) || !manifest.icons.some((icon) => icon.sizes === '512x512' && icon.purpose === 'maskable')) {
  throw new Error('PWA manifest requires a 512x512 maskable icon')
}

const html = await readFile(resolve(dist, 'index.html'), 'utf8')
if (!html.includes('/stock/manifest.webmanifest')) throw new Error('Built HTML does not link the PWA manifest')
if (!html.includes('/stock/icons/apple-touch-icon.png')) throw new Error('Built HTML does not link the Apple touch icon')
const appScriptPath = html.match(/src="(\/stock\/assets\/[^"]+\.js)"/)?.[1]
if (!appScriptPath) throw new Error('Built HTML does not contain the application script')
const appScript = await readFile(resolve(dist, appScriptPath.replace('/stock/', '')), 'utf8')
if (!appScript.includes('sw.js') || !appScript.includes('beforeinstallprompt')) {
  throw new Error('Built application is missing PWA registration or install handling')
}

const serviceWorker = await readFile(resolve(dist, 'sw.js'), 'utf8')
if (!serviceWorker.includes("const BASE_PATH = '/stock/'")) throw new Error('Service worker scope path is invalid')

console.log('PWA validation passed: manifest, icons, offline page, and service worker are present')
