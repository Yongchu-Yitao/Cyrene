'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  DEVELOPMENT_MIGRATION_BACKUP,
  DEVELOPMENT_MIGRATION_MARKER,
  migrateLegacyDevelopmentData,
} = require('./development-data-migration');

function write(target, content) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, 'utf8');
}

test('development migration restores source-run state and preserves new state in a backup', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cyrene-development-migration-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const legacyUserDataDir = path.join(root, 'Cyrene');
  const developmentUserDataDir = path.join(root, 'Cyrene-dev');
  const sourceRoot = path.join(root, 'checkout');

  write(path.join(legacyUserDataDir, 'desktop_settings.json'), '{"theme":"dark"}');
  write(path.join(legacyUserDataDir, 'custom-tools', 'legacy.json'), 'legacy tool');
  write(path.join(legacyUserDataDir, 'data', 'packaged-only.txt'), 'packaged');
  write(path.join(developmentUserDataDir, 'Preferences'), 'new preferences');
  write(path.join(developmentUserDataDir, 'data', 'new-session.txt'), 'new session');
  write(path.join(sourceRoot, 'workspace', 'SOUL.md'), 'legacy workspace');
  write(path.join(sourceRoot, 'store', 'cyrene.db'), 'legacy database');
  write(path.join(sourceRoot, 'data', 'config.enc'), 'legacy configuration');

  const result = migrateLegacyDevelopmentData({
    developmentUserDataDir,
    legacyUserDataDir,
    sourceRoot,
    now: () => new Date('2026-08-29T04:00:00.000Z'),
  });

  assert.equal(result.migrated, true);
  assert.equal(fs.readFileSync(path.join(developmentUserDataDir, 'desktop_settings.json'), 'utf8'), '{"theme":"dark"}');
  assert.equal(fs.readFileSync(path.join(developmentUserDataDir, 'Preferences'), 'utf8'), 'new preferences');
  assert.equal(fs.readFileSync(path.join(developmentUserDataDir, 'data', 'config.enc'), 'utf8'), 'legacy configuration');
  assert.equal(fs.existsSync(path.join(developmentUserDataDir, 'data', 'packaged-only.txt')), false);
  assert.equal(
    fs.readFileSync(path.join(developmentUserDataDir, DEVELOPMENT_MIGRATION_BACKUP, 'data', 'new-session.txt'), 'utf8'),
    'new session',
  );
  assert.equal(fs.existsSync(path.join(developmentUserDataDir, DEVELOPMENT_MIGRATION_MARKER)), true);

  write(path.join(sourceRoot, 'data', 'config.enc'), 'changed after migration');
  const second = migrateLegacyDevelopmentData({
    developmentUserDataDir,
    legacyUserDataDir,
    sourceRoot,
  });
  assert.equal(second.migrated, false);
  assert.equal(second.reason, 'already-migrated');
  assert.equal(fs.readFileSync(path.join(developmentUserDataDir, 'data', 'config.enc'), 'utf8'), 'legacy configuration');
});
