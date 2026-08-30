import * as esbuild from 'esbuild'
import { createHash } from 'crypto'
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync, copyFileSync, rmSync } from 'fs'
import { join, relative, dirname, extname, resolve, basename, sep } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BUILD_SCRIPT = fileURLToPath(import.meta.url)
const APP_DIR = resolve(__dirname, 'static/app')
const OUT_DIR = resolve(APP_DIR, 'compiled')
const WORKBENCH_DIR = resolve(__dirname, 'frontend')
const ASSETS_DIR = resolve(WORKBENCH_DIR, 'assets')
const MAX_APP_ENTRY_BYTES = 3.5 * 1024 * 1024
const TABLER_ICONS_DIR = resolve(__dirname, 'node_modules/@tabler/icons/icons/outline')
const SIMPLE_ICONS_DIR = resolve(__dirname, 'node_modules/simple-icons/icons')
const LOBE_ICONS_DIR = resolve(__dirname, 'node_modules/@lobehub/icons-static-svg/icons')
const SETTINGS_ICON_FILES = [
  'user.svg',
  'settings.svg',
  'palette.svg',
  'keyboard.svg',
  'box.svg',
  'route.svg',
  'server.svg',
  'arrow-up.svg',
  'arrow-down.svg',
  'robot.svg',
  'microphone.svg',
  'tools.svg',
  'messages.svg',
  'device-desktop-up.svg',
  'puzzle.svg',
  'package.svg',
  'webhook.svg',
  'code.svg',
  'plug-connected.svg',
  'wallet.svg',
  'chart-bar.svg',
  'database.svg',
  'info-circle.svg',
  'browser.svg',
  'chevron-down.svg',
  'devices.svg',
  'photo-video.svg',
  'download.svg',
  'reload.svg',
  'volume.svg',
  'volume-off.svg',
  'pin.svg',
  'pinned-off.svg',
  'player-pause.svg',
  'player-play.svg',
  'x.svg',
]
const PROVIDER_ICON_FILES = [
  [join(TABLER_ICONS_DIR, 'brand-openai.svg'), 'openai.svg'],
  [join(SIMPLE_ICONS_DIR, 'anthropic.svg'), 'anthropic.svg'],
  [join(LOBE_ICONS_DIR, 'gemini-color.svg'), 'gemini.svg'],
  [join(LOBE_ICONS_DIR, 'deepseek-color.svg'), 'deepseek.svg'],
  [join(LOBE_ICONS_DIR, 'minimax-color.svg'), 'minimax.svg'],
  [join(LOBE_ICONS_DIR, 'kimi.svg'), 'kimi.svg'],
  [join(LOBE_ICONS_DIR, 'zhipu-color.svg'), 'glm.svg'],
  [join(LOBE_ICONS_DIR, 'opencode.svg'), 'opencode.svg'],
  [join(LOBE_ICONS_DIR, 'openrouter-color.svg'), 'openrouter.svg'],
  [join(SIMPLE_ICONS_DIR, 'amd.svg'), 'amd.svg'],
  [join(SIMPLE_ICONS_DIR, 'ollama.svg'), 'ollama.svg'],
  [join(SIMPLE_ICONS_DIR, 'onnx.svg'), 'onnx.svg'],
]
const EXTENSION_ICON_FILES = [
  [join(SIMPLE_ICONS_DIR, 'python.svg'), 'python.svg'],
  [join(SIMPLE_ICONS_DIR, 'uv.svg'), 'uv.svg'],
  [join(TABLER_ICONS_DIR, 'tex.svg'), 'tex.svg'],
  [join(SIMPLE_ICONS_DIR, 'nodedotjs.svg'), 'nodejs.svg'],
  [join(SIMPLE_ICONS_DIR, 'bun.svg'), 'bun.svg'],
  [join(SIMPLE_ICONS_DIR, 'github.svg'), 'github.svg'],
  [join(SIMPLE_ICONS_DIR, 'go.svg'), 'go.svg'],
  [join(SIMPLE_ICONS_DIR, 'openjdk.svg'), 'java.svg'],
  [join(SIMPLE_ICONS_DIR, 'rust.svg'), 'rust.svg'],
  [join(SIMPLE_ICONS_DIR, 'deno.svg'), 'deno.svg'],
  [join(ASSETS_DIR, 'extension-icons/ripgrep.svg'), 'ripgrep.svg'],
  [join(ASSETS_DIR, 'extension-icons/jq.svg'), 'jq.svg'],
]
const VENDOR_DIR = resolve(__dirname, 'vendor')
const OFFICE_OUT_DIR = resolve(APP_DIR, 'office')
const OFFICE_ENTRIES = [
  [resolve(VENDOR_DIR, 'office-docx.js'), 'docx-viewer.js'],
  [resolve(VENDOR_DIR, 'office-pptx.js'), 'pptx-viewer.js'],
]
const INDEX_SOURCE = resolve(WORKBENCH_DIR, 'index.html')
const ELECTRON_MAIN_SOURCE = resolve(__dirname, '../../../../electron/main.js')
const PROJECT_FILE = resolve(__dirname, '../../../../pyproject.toml')

function projectVersion() {
  const source = readFileSync(PROJECT_FILE, 'utf8')
  const match = source.match(/^\s*version\s*=\s*"([^"]+)"/m)
  if (!match) throw new Error(`Unable to read project version from ${PROJECT_FILE}`)
  return match[1]
}

function collect(dir) {
  const files = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      files.push(...collect(full))
    } else if ((entry.endsWith('.jsx') || entry.endsWith('.mjs')) && !entry.includes('.test.')) {
      files.push(full)
    }
  }
  return files
}

function collectCss(dir) {
  const files = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      files.push(...collectCss(full))
    } else if (entry.endsWith('.css')) {
      files.push(full)
    }
  }
  return files
}

function collectAssets(dir) {
  const files = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      files.push(...collectAssets(full))
    } else {
      files.push(full)
    }
  }
  return files
}

function frontendRevision(files, cssFiles, assetFiles, indexTemplate) {
  const hash = createHash('sha256')
  const sources = [...files, ...cssFiles, ...assetFiles].sort()
  for (const file of sources) {
    hash.update(relative(WORKBENCH_DIR, file))
    hash.update('\0')
    hash.update(readFileSync(file))
    hash.update('\0')
  }
  // Ignore the previous query value so rebuilding identical sources remains
  // deterministic instead of hashing the last generated revision.
  hash.update(indexTemplate.replace(/(\?v=)[A-Za-z0-9.+-]+/g, '$1'))
  return `${projectVersion()}-${hash.digest('hex').slice(0, 10)}`
}

function svgMarkup(file) {
  return readFileSync(file, 'utf8').trim()
}

function inlineIconAssets(settingsIconFiles, providerIconFiles, extensionIconFiles) {
  const settings = Object.fromEntries(settingsIconFiles.map((file) => [
    basename(file, extname(file)),
    svgMarkup(file),
  ]))
  const providers = Object.fromEntries(providerIconFiles.map(([file, outputName]) => [
    basename(outputName, extname(outputName)),
    svgMarkup(file),
  ]))
  const extensions = Object.fromEntries(extensionIconFiles.map(([file, outputName]) => [
    basename(outputName, extname(outputName)),
    svgMarkup(file),
  ]))
  // Escape '<' so trusted local SVG markup cannot accidentally terminate the
  // inline script. JavaScript restores the original character at parse time.
  const payload = JSON.stringify({ settings, providers, extensions }).replace(/</g, '\\u003c')
  return `<script>window.CyreneIconAssets=Object.freeze(${payload});</script>`
}

function electronOverlayTemplate(electronSource, constantName) {
  const marker = `const ${constantName} = \``
  const start = electronSource.indexOf(marker)
  if (start < 0) throw new Error(`Unable to find ${constantName} in Electron main`)
  const contentStart = start + marker.length
  const end = electronSource.indexOf('\`;', contentStart)
  if (end < 0) throw new Error(`Unable to read ${constantName} from Electron main`)
  return electronSource.slice(contentStart, end)
}

async function buildWorkbenchBundles(files) {
  const esmBuild = await esbuild.build({
    entryPoints: {
      app: resolve(WORKBENCH_DIR, 'entry/app.jsx'),
      pdf: resolve(WORKBENCH_DIR, 'entry/pdf.jsx'),
    },
    outdir: OUT_DIR,
    bundle: true,
    format: 'esm',
    splitting: true,
    minify: true,
    chunkNames: 'chunks/[name]-[hash]',
    platform: 'browser',
    jsx: 'transform',
    target: 'es2020',
    supported: { 'template-literal': false },
    metafile: true,
    logLevel: 'silent',
  })
  const appOutput = Object.entries(esmBuild.metafile.outputs).find(([, output]) =>
    output.entryPoint
      && output.entryPoint.replace(/\\/g, '/').endsWith('/entry/app.jsx')
  )
  if (!appOutput) throw new Error('Workbench app entry was not emitted')
  const appEntryBytes = appOutput[1].bytes
  if (appEntryBytes > MAX_APP_ENTRY_BYTES) {
    throw new Error(
      `Workbench app entry is ${(appEntryBytes / 1024 / 1024).toFixed(2)} MiB; `
      + `budget is ${(MAX_APP_ENTRY_BYTES / 1024 / 1024).toFixed(2)} MiB`,
    )
  }
  const splitChunks = Object.values(esmBuild.metafile.outputs).filter((output) =>
    !output.entryPoint && output.bytes > 0
  ).length
  console.log(
    `✓ ESM surfaces (${relative(WORKBENCH_DIR, resolve(WORKBENCH_DIR, 'entry/app.jsx')).split(sep).join('/')}) `
    + `→ compiled/app.js, compiled/pdf.js (${files.length} source modules, `
    + `${(appEntryBytes / 1024 / 1024).toFixed(2)} MiB app entry, ${splitChunks} shared chunks)`,
  )
}

async function build() {
  const workbenchFiles = existsSync(WORKBENCH_DIR) ? collect(WORKBENCH_DIR) : []
  const cssFiles = existsSync(WORKBENCH_DIR) ? collectCss(WORKBENCH_DIR) : []
  const assetFiles = existsSync(ASSETS_DIR) ? collectAssets(ASSETS_DIR) : []
  const settingsIconFiles = SETTINGS_ICON_FILES.map((name) => join(TABLER_ICONS_DIR, name))
  const providerIconFiles = PROVIDER_ICON_FILES.map(([source]) => source)
  const files = workbenchFiles
  rmSync(OUT_DIR, { recursive: true, force: true })
  rmSync(OFFICE_OUT_DIR, { recursive: true, force: true })
  mkdirSync(OUT_DIR, { recursive: true })
  mkdirSync(OFFICE_OUT_DIR, { recursive: true })

  await buildWorkbenchBundles(files)

  // Office renderers are large and needed only after a DOCX/PPTX is opened.
  // Keep them out of the startup scripts and expose one small global API per
  // format so the split viewer can load the matching bundle on demand.
  for (const [entry, output] of OFFICE_ENTRIES) {
    await esbuild.build({
      entryPoints: [entry],
      outfile: join(OFFICE_OUT_DIR, output),
      bundle: true,
      format: 'iife',
      platform: 'browser',
      target: 'es2020',
      minify: true,
      logLevel: 'silent',
    })
    console.log(`✓ office/${output}`)
  }

  const indexTemplate = readFileSync(INDEX_SOURCE, 'utf8')
  const revisionSources = files.concat(
    OFFICE_ENTRIES.map(([entry]) => entry),
    settingsIconFiles,
    providerIconFiles,
    EXTENSION_ICON_FILES.map(([source]) => source),
    [BUILD_SCRIPT],
    [resolve(__dirname, 'package-lock.json')],
  )
  const revision = frontendRevision(revisionSources, cssFiles, assetFiles, indexTemplate)
  const indexHtml = indexTemplate
    .replace(
      '<!-- CYRENE_ICON_ASSETS -->',
      inlineIconAssets(settingsIconFiles, PROVIDER_ICON_FILES, EXTENSION_ICON_FILES),
    )
    .replace(
      /(\?v=)[A-Za-z0-9.+-]+/g,
      `$1${revision}`,
    )
  writeFileSync(join(APP_DIR, 'index.html'), indexHtml)
  console.log(`✓ index.html (${revision})`)

  // Browser chrome overlays run in separate WebContentsViews. Emitting their
  // existing inline templates as same-origin pages lets them load the exact
  // same bundled fonts as the main Workbench without weakening local CORS.
  const electronSource = readFileSync(ELECTRON_MAIN_SOURCE, 'utf8')
  const overlayDir = join(APP_DIR, 'electron')
  mkdirSync(overlayDir, { recursive: true })
  for (const [constantName, outputName] of [
    ['BROWSER_CHAT_OVERLAY_HTML', 'browser-chat-overlay.html'],
    ['BROWSER_TAB_PICKER_HTML', 'browser-tab-picker.html'],
  ]) {
    const overlayHtml = electronOverlayTemplate(electronSource, constantName).replace(
      '<head>',
      `<head><link rel="stylesheet" href="../fonts.css?v=${revision}">`,
    )
    writeFileSync(join(overlayDir, outputName), overlayHtml)
    console.log(`✓ electron/${outputName}`)
  }

  const reactAssets = [
    ['node_modules/react/umd/react.production.min.js', 'react.production.min.js'],
    ['node_modules/react-dom/umd/react-dom.production.min.js', 'react-dom.production.min.js'],
    ['node_modules/echarts/dist/echarts.min.js', 'echarts.min.js'],
  ]
  for (const [source, target] of reactAssets) {
    copyFileSync(resolve(__dirname, source), join(APP_DIR, target))
    console.log(`✓ ${target}`)
  }

  // CSS is maintained beside its owning source and copied to the one static
  // output namespace with the same relative path.
  for (const srcPath of cssFiles) {
    const rel = relative(WORKBENCH_DIR, srcPath)
    const outPath = join(APP_DIR, rel)
    mkdirSync(dirname(outPath), { recursive: true })
    copyFileSync(srcPath, outPath)
    console.log(`✓ ${rel}`)
  }

  // Fonts and their licenses are source-owned beside the Workbench and copied
  // into the same static namespace that PyInstaller packages for every OS.
  for (const srcPath of assetFiles) {
    const rel = relative(WORKBENCH_DIR, srcPath)
    const outPath = join(APP_DIR, rel)
    mkdirSync(dirname(outPath), { recursive: true })
    copyFileSync(srcPath, outPath)
    console.log(`✓ ${rel}`)
  }

  // Settings navigation uses Tabler's outline set so every destination has a
  // distinct, maintained icon instead of aliases that repeat another tab.
  const settingsIconDir = join(APP_DIR, 'settings-icons')
  mkdirSync(settingsIconDir, { recursive: true })
  for (const srcPath of settingsIconFiles) {
    const iconName = basename(srcPath)
    const target = join(settingsIconDir, iconName)
    copyFileSync(srcPath, target)
    console.log(`✓ settings-icons/${iconName}`)
  }

  const providerIconDir = join(APP_DIR, 'provider-icons')
  mkdirSync(providerIconDir, { recursive: true })
  for (const [srcPath, iconName] of PROVIDER_ICON_FILES) {
    copyFileSync(srcPath, join(providerIconDir, iconName))
    console.log(`✓ provider-icons/${iconName}`)
  }

  // ---- Copy pdfjs-dist assets ------------------------------------------------
  const PDFJS_SRC = resolve(__dirname, 'node_modules/pdfjs-dist')
  const PDFJS_DST = resolve(APP_DIR, 'pdfjs')
  if (existsSync(PDFJS_SRC)) {
    mkdirSync(join(PDFJS_DST, 'images'), { recursive: true })

    // Keep the core, worker, and viewer on PDF.js's official legacy build so
    // packaged desktop runtimes and supported source-mode browsers all receive
    // the same polyfills and execute the same asset set.
    const files = [
      ['legacy/build/pdf.min.mjs', 'pdf.min.js'],
      ['legacy/build/pdf.worker.min.mjs', 'pdf.worker.min.js'],
      ['legacy/web/pdf_viewer.mjs', 'pdf_viewer.js'],
      ['web/pdf_viewer.css', 'pdf_viewer.css'],
    ]
    for (const [src, dst] of files) {
      const srcPath = join(PDFJS_SRC, src)
      const dstPath = join(PDFJS_DST, dst)
      if (existsSync(srcPath)) {
        let content = readFileSync(srcPath, 'utf-8')
        // pdfjs-dist uses import.meta.url / export {} which are only valid in
        // ES modules. Replace/fix so the files work as regular <script> tags.
        content = content.replace(/import\.meta\.url/g, '"file://"')
        // Remove sourcemap reference (file renamed, map doesn't exist)
        content = content.replace(/\/\/# sourceMappingURL.*/g, '')
        if (dst === 'pdf.min.js') {
          // pdf.min.mjs already sets globalThis.pdfjsLib = {...} — its
          // export { ... } is redundant for classic script usage, just drop it.
          content = content.replace(/\bexport\s*\{[^}]*\};?/g, '/* export removed */')
          // Wrap in IIFE to avoid module-level const/let/class declarations
          // (like `const t=...`) leaking to global scope.
          content = '(function(){\n' + content + '\n})();'
        } else if (dst === 'pdf_viewer.js') {
          // Transform export { X as Y, Z } → globalThis.pdfjsViewer={Y:X, Z}
          // so viewer components are accessible as window.pdfjsViewer.XXX.
          content = content.replace(/\bexport\s*\{([\s\S]*?)\};?/g, (_m, body) => {
            const parts = body.split(',').map(s => s.trim()).filter(Boolean)
            const props = parts.map(p => {
              const asMatch = p.match(/^(\S+)\s+as\s+(\S+)$/)
              return asMatch ? `${asMatch[2]}: ${asMatch[1]}` : p
            })
            return `globalThis.pdfjsViewer={${props.join(',')}};`
          })
          content = '(function(){\n' + content + '\n})();'
        } else if (dst === 'pdf.worker.min.js') {
          // Worker — just remove export, keep globalThis.pdfjsWorker intact.
          content = content.replace(/\bexport\s*\{[^}]*\};?/g, '/* export removed */')
        }
        // Upstream legacy bundles contain whitespace-only Webpack marker lines.
        // Keep generated assets clean so repository-wide diff checks stay useful.
        content = content.replace(/[ \t]+$/gm, '')
        writeFileSync(dstPath, content)
        console.log(`✓ pdfjs/${dst}`)
      }
    }
    // Copy images
    const imgSrc = join(PDFJS_SRC, 'web', 'images')
    const imgDst = join(PDFJS_DST, 'images')
    if (existsSync(imgSrc)) {
      const entries = readdirSync(imgSrc)
      for (const f of entries) {
        copyFileSync(join(imgSrc, f), join(imgDst, f))
      }
      console.log(`✓ pdfjs/images/ (${entries.length} files)`)
    }
  }
}

build().catch(e => {
  console.error('Build failed:', e)
  process.exit(1)
})
