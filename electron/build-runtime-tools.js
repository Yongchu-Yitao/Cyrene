const crypto = require('crypto');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const UV_VERSION = '0.11.28';
const MISE_VERSION = '2026.8.5';

const TARGETS = {
  'darwin-arm64': {
    key: 'macos-arm64', uv: ['uv-aarch64-apple-darwin.tar.gz', '33540eb7c883ab857eff79bd5ac2aa31fe27b595abecb4a9c003a2c998447232'],
    mise: ['mise-v2026.8.5-macos-arm64', '0268084c853545dc4a81acc0a494965a784a8935f3aa53728f0703398dc0cdbd'],
  },
  'darwin-x64': {
    key: 'macos-x64', uv: ['uv-x86_64-apple-darwin.tar.gz', '2ad79983127ffca7d77b77ce6a24278d7e4f7b817a1acf72fea5f8124b4aac5e'],
    mise: ['mise-v2026.8.5-macos-x64', 'acb65a5dd836a45ee5214bfe6b881a2cf721f4ae587c5f108bbb868eaf6bebff'],
  },
  'win32-arm64': {
    key: 'windows-arm64', uv: ['uv-aarch64-pc-windows-msvc.zip', '3248109afad3ec59baad299d324ff53de17e2d9a3b3e21580ffd26744b11e036'],
    mise: ['mise-v2026.8.5-windows-arm64.exe', '0f94d7ac1bd9c3f04d8420d936fbf9cb64c58d7aec3f91dfd89c626985d62f78'],
  },
  'win32-x64': {
    key: 'windows-x64', uv: ['uv-x86_64-pc-windows-msvc.zip', '0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b'],
    mise: ['mise-v2026.8.5-windows-x64.exe', '4dc594bf1964ac4c49a63216c88dadbad924bbcc0e59408cbdfcbe0872d528ab'],
  },
  'linux-arm64': {
    key: 'linux-arm64', uv: ['uv-aarch64-unknown-linux-gnu.tar.gz', '03e9fe0a81b0718d0bc84625de3885df6cc3f89a8b6af6121d6b9f6113fb6533'],
    mise: ['mise-v2026.8.5-linux-arm64', 'd2bde76b1f87ab50b6f456e05332bb02de56a6bf3c5d19343cc3661e5d294681'],
  },
  'linux-x64': {
    key: 'linux-x64', uv: ['uv-x86_64-unknown-linux-gnu.tar.gz', 'e490a6464492183c5d4534a5527fb4440f7f2bb2f228162ad7e4afe076dc0224'],
    mise: ['mise-v2026.8.5-linux-x64', 'ee362b6d96c648e27325a8bc7ee866bde4fffc20c88c777c5eb5c3b5c6f3e226'],
  },
};

function sha256(file) {
  const digest = crypto.createHash('sha256');
  digest.update(fs.readFileSync(file));
  return digest.digest('hex');
}

async function download(url, destination, expected) {
  if (!fs.existsSync(destination) || sha256(destination) !== expected) {
    const response = await fetch(url, { redirect: 'follow' });
    if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
    fs.writeFileSync(destination, Buffer.from(await response.arrayBuffer()));
  }
  const actual = sha256(destination);
  if (actual !== expected) throw new Error(`Checksum mismatch for ${path.basename(destination)}: ${actual}`);
}

function findFile(root, wanted) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const found = findFile(candidate, wanted);
      if (found) return found;
    } else if (entry.name === wanted) return candidate;
  }
  return null;
}

function resolveArch(context) {
  const value = context && context.arch;
  if (value === 'arm64' || String(value) === '3') return 'arm64';
  if (value === 'x64' || String(value) === '1') return 'x64';
  return process.arch === 'arm64' ? 'arm64' : 'x64';
}

async function buildRuntimeTools(context = {}) {
  const platform = context.electronPlatformName || process.platform;
  const arch = resolveArch(context);
  const target = TARGETS[`${platform}-${arch}`];
  if (!target) throw new Error(`Unsupported runtime-tools target: ${platform}-${arch}`);

  const outputRoot = path.join(__dirname, 'runtime-tools');
  const output = path.join(outputRoot, target.key);
  const cache = path.join(__dirname, '.runtime-tools-cache');
  fs.rmSync(outputRoot, { recursive: true, force: true });
  fs.mkdirSync(output, { recursive: true });
  fs.mkdirSync(cache, { recursive: true });

  const [uvAsset, uvDigest] = target.uv;
  const uvArchive = path.join(cache, uvAsset);
  await download(`https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${uvAsset}`, uvArchive, uvDigest);
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-uv-'));
  try {
    if (uvAsset.endsWith('.zip')) {
      execFileSync('tar', ['-xf', uvArchive, '-C', temp]);
    } else {
      execFileSync('tar', ['-xzf', uvArchive, '-C', temp]);
    }
    const executable = platform === 'win32' ? 'uv.exe' : 'uv';
    const extracted = findFile(temp, executable);
    if (!extracted) throw new Error(`uv executable missing from ${uvAsset}`);
    fs.copyFileSync(extracted, path.join(output, executable));
    if (platform !== 'win32') fs.chmodSync(path.join(output, executable), 0o755);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }

  const [miseAsset, miseDigest] = target.mise;
  const miseCache = path.join(cache, miseAsset);
  await download(`https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/${miseAsset}`, miseCache, miseDigest);
  const miseExecutable = platform === 'win32' ? 'mise.exe' : 'mise';
  fs.copyFileSync(miseCache, path.join(output, miseExecutable));
  if (platform !== 'win32') fs.chmodSync(path.join(output, miseExecutable), 0o755);

  fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify({ platform, arch, uv: { version: UV_VERSION, sha256: uvDigest }, mise: { version: MISE_VERSION, sha256: miseDigest } }, null, 2));
}

module.exports = { buildRuntimeTools, TARGETS, UV_VERSION, MISE_VERSION };

if (require.main === module) {
  buildRuntimeTools({ electronPlatformName: process.argv[2], arch: process.argv[3] }).catch((error) => { console.error(error); process.exitCode = 1; });
}
