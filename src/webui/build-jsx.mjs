import * as esbuild from 'esbuild'
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync, copyFileSync } from 'fs'
import { join, relative, dirname, extname, resolve } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const APP_DIR = resolve(__dirname, 'static/app')
const OUT_DIR = resolve(APP_DIR, 'compiled')
const WORKBENCH_DIR = resolve(__dirname, '../workbench-webui')

function collect(dir) {
  const files = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      files.push(...collect(full))
    } else if (entry.endsWith('.jsx')) {
      files.push(full)
    }
  }
  return files
}

async function build() {
  const workbenchFiles = existsSync(WORKBENCH_DIR) ? collect(WORKBENCH_DIR) : []
  const files = [...collect(APP_DIR), ...workbenchFiles]
  mkdirSync(OUT_DIR, { recursive: true })

  for (const file of files) {
    const srcDir = file.startsWith(WORKBENCH_DIR) ? WORKBENCH_DIR : APP_DIR
    const rel = relative(srcDir, file).replace(/\.jsx$/, '.js')
    const outFile = join(OUT_DIR, rel)
    mkdirSync(dirname(outFile), { recursive: true })

    if (rel === 'code/editor.js') {
      await esbuild.build({
        entryPoints: [file],
        outfile: outFile,
        bundle: true,
        format: 'iife',
        platform: 'browser',
        jsx: 'transform',
        target: 'es2020',
        logLevel: 'silent',
      })
    } else {
      const src = readFileSync(file, 'utf8')
      const result = await esbuild.transform(src, {
        loader: 'jsx',
        jsx: 'transform',
      })

      // Change top-level const to var to avoid redeclaration errors
      // across separate <script> tags (Babel standalone isolated per file)
      const code = result.code.replace(/^const /gm, 'var ')
      writeFileSync(outFile, code)
    }

    console.log(`✓ ${relative(srcDir, file)} → compiled/${rel}`)
  }

  const total = files.length
  console.log(`\nDone. ${total} JSX file${total > 1 ? 's' : ''} compiled to ${OUT_DIR}`)

  // ---- Copy pdfjs-dist assets ------------------------------------------------
  const PDFJS_SRC = resolve(__dirname, 'node_modules/pdfjs-dist')
  const PDFJS_DST = resolve(APP_DIR, 'pdfjs')
  if (existsSync(PDFJS_SRC)) {
    mkdirSync(join(PDFJS_DST, 'images'), { recursive: true })

    const files = [
      ['build/pdf.min.mjs', 'pdf.min.js'],
      ['build/pdf.worker.min.mjs', 'pdf.worker.min.js'],
      ['web/pdf_viewer.mjs', 'pdf_viewer.js'],
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
