'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

// npm's packaging reader requires SemVer, while Cyrene also ships four-part
// stable releases. Stage only the reader input; packaged metadata, OS versions,
// and artifact names keep the exact public release number via extraMetadata.
function packagingVersion(version) {
  const match = /^(\d+\.\d+\.\d+)\.(\d+)$/.exec(version);
  return match ? { version: match[1], revision: match[2], publicVersion: version } : null;
}

function buildRelease(args, { directory = path.resolve(__dirname, '..'), run = spawnSync } = {}) {
  const manifestPath = path.join(directory, 'package.json');
  const original = fs.readFileSync(manifestPath, 'utf8');
  const manifest = JSON.parse(original);
  const mapped = packagingVersion(manifest.version);
  const commandArgs = [require.resolve('electron-builder/cli.js'), ...args];
  if (mapped) {
    commandArgs.push(`--config.extraMetadata.version=${mapped.publicVersion}`,
      `--config.buildVersion=${mapped.publicVersion}`, `--config.buildNumber=${mapped.revision}`);
  }
  try {
    if (mapped) fs.writeFileSync(manifestPath, JSON.stringify({ ...manifest, version: mapped.version }, null, 2) + '\n');
    const result = run(process.execPath, commandArgs, { cwd: directory, stdio: 'inherit' });
    if (result.error) throw result.error;
    return result.status ?? 1;
  } finally {
    if (mapped) fs.writeFileSync(manifestPath, original);
  }
}

if (require.main === module) process.exitCode = buildRelease(process.argv.slice(2));
module.exports = { packagingVersion, buildRelease };
