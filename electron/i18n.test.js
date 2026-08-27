const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const mainPath = path.join(__dirname, 'main.js');
const preloadPath = path.join(__dirname, 'preload.js');
const mainSource = fs.readFileSync(mainPath, 'utf8');
const preloadSource = fs.readFileSync(preloadPath, 'utf8');

function frozenObject(name, nextMarker) {
  const start = `const ${name} = Object.freeze(`;
  const startIndex = mainSource.indexOf(start);
  assert.notEqual(startIndex, -1, `${name} declaration is missing`);
  const valueStart = startIndex + start.length;
  const endIndex = mainSource.indexOf(nextMarker, valueStart);
  assert.notEqual(endIndex, -1, `${name} end marker is missing`);
  const expression = mainSource.slice(valueStart, endIndex).trim().replace(/;$/, '').replace(/\)$/, '');
  return vm.runInNewContext(`(${expression})`);
}

function placeholders(value) {
  return [...String(value).matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
    .map((match) => match[1])
    .sort();
}

test('desktop and native menu catalogs have complete locale parity', () => {
  const catalogs = [
    frozenObject('DESKTOP_TRANSLATIONS', '\n\nconst MENU_TRANSLATIONS'),
    frozenObject('MENU_TRANSLATIONS', '\n\nconst BROWSER_PARTITION'),
  ];
  for (const catalog of catalogs) {
    assert.deepEqual(Object.keys(catalog.en).sort(), Object.keys(catalog.zh).sort());
    for (const key of Object.keys(catalog.en)) {
      assert.ok(String(catalog.en[key]).trim(), `English desktop translation ${key} is empty`);
      assert.ok(String(catalog.zh[key]).trim(), `Chinese desktop translation ${key} is empty`);
      assert.deepEqual(placeholders(catalog.en[key]), placeholders(catalog.zh[key]), `Placeholder mismatch for ${key}`);
    }
  }
});

test('desktop language controls browser requests and notifies renderer surfaces', () => {
  assert.match(mainSource, /'Accept-Language': desktopAcceptLanguage\(\)/);
  assert.match(mainSource, /webContents\.send\('desktop-language:changed', language\)/);
  assert.match(preloadSource, /onDesktopLanguageChanged:/);
  assert.match(preloadSource, /ipcRenderer\.on\('desktop-language:changed'/);
});
