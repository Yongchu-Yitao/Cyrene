'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DEVELOPMENT_MIGRATION_VERSION = 1;
const DEVELOPMENT_MIGRATION_MARKER = `.legacy-development-data-migrated-v${DEVELOPMENT_MIGRATION_VERSION}.json`;
const DEVELOPMENT_MIGRATION_BACKUP = `.legacy-development-data-backup-v${DEVELOPMENT_MIGRATION_VERSION}`;
const LEGACY_RUNTIME_DIRECTORIES = Object.freeze(['workspace', 'store', 'data', 'backups']);
const TRANSIENT_PROFILE_ENTRIES = new Set([
  'Cache',
  'Code Cache',
  'DawnGraphiteCache',
  'DawnWebGPUCache',
  'GPUCache',
  'DevToolsActivePort',
  'SingletonCookie',
  'SingletonLock',
  'SingletonSocket',
]);

function copyTree(source, destination, { overwrite = false } = {}) {
  const stat = fs.lstatSync(source);
  if (stat.isSymbolicLink()) return;
  if (stat.isDirectory()) {
    fs.mkdirSync(destination, { recursive: true });
    for (const name of fs.readdirSync(source)) {
      copyTree(path.join(source, name), path.join(destination, name), { overwrite });
    }
    return;
  }
  if (!stat.isFile()) return;
  if (!overwrite && fs.existsSync(destination)) return;
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.copyFileSync(source, destination);
}

function copyLegacyProfile(legacyUserDataDir, developmentUserDataDir) {
  if (!fs.existsSync(legacyUserDataDir)) return false;
  if (path.resolve(legacyUserDataDir) === path.resolve(developmentUserDataDir)) return false;
  let copied = false;
  for (const name of fs.readdirSync(legacyUserDataDir)) {
    if (
      LEGACY_RUNTIME_DIRECTORIES.includes(name)
      || TRANSIENT_PROFILE_ENTRIES.has(name)
      || name.startsWith('.com.github.Electron.')
    ) {
      continue;
    }
    copyTree(
      path.join(legacyUserDataDir, name),
      path.join(developmentUserDataDir, name),
      { overwrite: false },
    );
    copied = true;
  }
  return copied;
}

function migrateLegacyRuntime(sourceRoot, developmentUserDataDir) {
  const backupRoot = path.join(developmentUserDataDir, DEVELOPMENT_MIGRATION_BACKUP);
  const migratedDirectories = [];
  for (const name of LEGACY_RUNTIME_DIRECTORIES) {
    const source = path.join(sourceRoot, name);
    if (!fs.existsSync(source) || !fs.lstatSync(source).isDirectory()) continue;

    const destination = path.join(developmentUserDataDir, name);
    const backup = path.join(backupRoot, name);
    if (fs.existsSync(destination) && !fs.existsSync(backup)) {
      fs.mkdirSync(backupRoot, { recursive: true });
      fs.renameSync(destination, backup);
    }
    copyTree(source, destination, { overwrite: true });
    migratedDirectories.push(name);
  }
  return migratedDirectories;
}

function writeMigrationMarker(markerPath, payload) {
  const temporary = `${markerPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  fs.renameSync(temporary, markerPath);
}

function migrateLegacyDevelopmentData({
  developmentUserDataDir,
  legacyUserDataDir,
  sourceRoot,
  now = () => new Date(),
}) {
  const destination = path.resolve(developmentUserDataDir);
  const markerPath = path.join(destination, DEVELOPMENT_MIGRATION_MARKER);
  if (fs.existsSync(markerPath)) {
    return { migrated: false, markerPath, reason: 'already-migrated' };
  }

  fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
  const profileCopied = copyLegacyProfile(path.resolve(legacyUserDataDir), destination);
  const runtimeDirectories = migrateLegacyRuntime(path.resolve(sourceRoot), destination);
  const payload = {
    version: DEVELOPMENT_MIGRATION_VERSION,
    migratedAt: now().toISOString(),
    legacyUserDataDir: path.resolve(legacyUserDataDir),
    legacyRuntimeRoot: path.resolve(sourceRoot),
    profileCopied,
    runtimeDirectories,
    backupDirectory: path.join(destination, DEVELOPMENT_MIGRATION_BACKUP),
  };
  writeMigrationMarker(markerPath, payload);
  return { migrated: true, markerPath, ...payload };
}

module.exports = {
  DEVELOPMENT_MIGRATION_BACKUP,
  DEVELOPMENT_MIGRATION_MARKER,
  migrateLegacyDevelopmentData,
};
