const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DesktopSettings } = require('./desktop-settings-owner');

function fixture(t) {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cyrene-settings-owner-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const registered=new Set(); const calls=[];
  const settings=new DesktopSettings({
    app:{getPath:()=>directory,setLoginItemSettings:()=>calls.push('login')},
    globalShortcut:{isRegistered:key=>registered.has(key), unregister:key=>registered.delete(key),
      register:key=>{if(key==='occupied')return false;registered.add(key);return true;}},
    supportsLoginItem:true,
    DEFAULT_DESKTOP_SETTINGS:{settingsRevision:0,launchAtLogin:false,runInBackground:false,language:'en',quickChatEnabled:false,quickChatShortcut:'Ctrl+Space'},
    normalizeDesktopLanguage:value=>value==='zh'?'zh':'en',getDesktopLanguage:value=>value.language,
    syncTrayWithSettings:()=>calls.push('tray'),rebuildApplicationMenu:()=>calls.push('menu'),
    broadcastDesktopLanguage:()=>calls.push('language'),destroyQuickChatWindow:()=>calls.push('destroy'),
    openQuickChat:async()=>{},appendErrorLog:()=>{},
  });
  return {settings,registered,calls};
}

test('settings CAS rejects stale writers and failed shortcut replacement preserves previous registration', t=>{
  const {settings,registered}=fixture(t);
  settings.saveDesktopSettings({runInBackground:true,quickChatEnabled:true},0);
  assert.throws(()=>settings.saveDesktopSettings({language:'zh'},0),{code:'revision_conflict'});
  const result=settings.saveDesktopSettings({quickChatShortcut:'occupied',language:'zh'},1);
  assert.equal(result.shortcutUpdateOk,false);
  assert.equal(settings.readDesktopSettings().language,'zh');
  assert.ok(registered.has('Ctrl+Space'));
  assert.equal(result.quickChatShortcutRegistered,false);
});

test('disabling residency and reset dispose shortcut/window with the original side effect order',t=>{
  const {settings,registered,calls}=fixture(t);
  settings.saveDesktopSettings({runInBackground:true,quickChatEnabled:true},0);
  calls.length=0;
  const result=settings.saveDesktopSettings({runInBackground:false},1);
  assert.equal(result.quickChatEnabled,false);
  assert.equal(registered.size,0);
  assert.deepEqual(calls,['login','tray','destroy']);
  assert.equal(settings.resetDesktopSettings().settingsRevision,3);
});
