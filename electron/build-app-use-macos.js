const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function buildMacHitTestHelper() {
  if (process.platform !== 'darwin') return;
  const source = path.join(__dirname, 'app-use-macos-hit-test.swift');
  const output = path.join(__dirname, 'app-use-macos-hit-test');
  const sourceMtime = Math.max(fs.statSync(source).mtimeMs, fs.statSync(__filename).mtimeMs);
  const outputMtime = fs.existsSync(output) ? fs.statSync(output).mtimeMs : 0;
  if (outputMtime >= sourceMtime) return;
  const slices = ['arm64', 'x86_64'].map((architecture) => `${output}.${architecture}`);
  try {
    for (let index = 0; index < slices.length; index += 1) {
      const architecture = index === 0 ? 'arm64' : 'x86_64';
      execFileSync('/usr/bin/xcrun', [
        'swiftc', source, '-O', '-target', `${architecture}-apple-macos12.0`,
        '-framework', 'ApplicationServices', '-o', slices[index],
      ], { stdio: 'inherit' });
    }
    execFileSync('/usr/bin/xcrun', ['lipo', '-create', ...slices, '-output', output], { stdio: 'inherit' });
    fs.chmodSync(output, 0o755);
  } finally {
    for (const slice of slices) fs.rmSync(slice, { force: true });
  }
}

module.exports = async function beforePack(context) {
  buildMacHitTestHelper();
  await require('./build-runtime-tools').buildRuntimeTools(context || {});
};
module.exports.buildMacHitTestHelper = buildMacHitTestHelper;

if (require.main === module) buildMacHitTestHelper();
