// A capability-scoped, read-only origin for one local HTML directory.
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const TYPES = {
  '.html': 'text/html', '.htm': 'text/html', '.css': 'text/css',
  '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
  '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.ttf': 'font/ttf', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4',
  '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.webm': 'video/webm',
  '.wasm': 'application/wasm', '.csv': 'text/csv',
};
function within(file, root) {
  const relative = path.relative(root, file);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}
async function createLocalPreview(filePath, workspace) {
  const workspaceRoot = await fs.realpath(workspace);
  const file = await fs.realpath(filePath);
  if (!within(file, workspaceRoot) || path.relative(workspaceRoot, file).split(path.sep).some(part => part.startsWith('.')) || !/\.html?$/i.test(file) || !(await fs.stat(file)).isFile()) {
    throw new Error('Preview requires an HTML file inside the active workspace.');
  }
  const root = path.dirname(file);
  const token = crypto.randomBytes(24).toString('hex');
  const prefix = `/${token}/`;
  let origin = '';
  const server = http.createServer(async (req, res) => {
    try {
      if (!['GET', 'HEAD'].includes(req.method) || req.headers.host !== new URL(origin).host) {
        res.writeHead(403).end(); return;
      }
      const pathname = new URL(req.url, origin).pathname;
      if (!pathname.startsWith(prefix)) { res.writeHead(404).end(); return; }
      const relative = decodeURIComponent(pathname.slice(prefix.length));
      if (relative.split(/[\\/]/).some(part => part.startsWith('.')) || relative.includes('\0')) {
        res.writeHead(403).end(); return;
      }
      const candidate = await fs.realpath(path.resolve(root, relative));
      const type = TYPES[path.extname(candidate).toLowerCase()];
      if (!within(candidate, root) || path.relative(root, candidate).split(path.sep).some(part => part.startsWith('.')) || !type || !(await fs.stat(candidate)).isFile()) {
        res.writeHead(403).end(); return;
      }
      const body = await fs.readFile(candidate);
      res.writeHead(200, {
        'Content-Type': type,
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'",
      });
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch (_) { res.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  server.unref();
  origin = `http://127.0.0.1:${server.address().port}`;
  return {
    url: `${origin}${prefix}${encodeURIComponent(path.basename(file))}`,
    allows(url) {
      return url === 'about:blank' || url.startsWith('data:') || url.startsWith(`blob:${origin}/`) || url.startsWith(`${origin}${prefix}`);
    },
    close() { server.close(); server.closeAllConnections(); },
  };
}
module.exports = { createLocalPreview };
