import test from 'node:test';
import assert from 'node:assert/strict';
import {harness} from './hook-harness.test.mjs';
import {normalizePermissionMode} from './behavior.mjs';
const deps={wbcNormalizePermissionMode:(value,fallback)=>normalizePermissionMode(value,fallback,['default','auto','full'])};

test('saved false is distinct from defaults and unavailable capabilities clear activations',()=>{
 const h=harness('./composer-settings.jsx',deps);const mod=h.run(m=>m);
 const chat={soulActive:false,workspaceActive:false,shortTermMemoryActive:false,projectMemoryActive:false,permissionMode:' FULL ',contextActivations:{mcpServers:[' one ','one'],skills:['skill'],pluginPacks:['pack']}};
 const initial=mod.resolveComposerSettings(chat);assert.equal(initial.mode,'full');assert.equal(initial.soulActive,false);assert.equal(initial.shortTermMemoryActive,false);
 const next=mod.resolveComposerSettings(chat,{soulAvailable:true,workspaceAvailable:true,mcpAvailable:false,skillsAvailable:true,pluginPacksAvailable:false,contextOptions:{soul:{selected:true},workspace:{selected:true}}});
 assert.equal(next.soulActive,false);assert.equal(next.workspaceActive,false);assert.equal(next.contextActivations.mcpServers.length,0);assert.equal(next.contextActivations.skills[0],'skill');
 assert.equal(initial.contextActivations.mcpServers[0],'one');
 const disabled=mod.resolveComposerSettings(null,{soulAvailable:false,workspaceAvailable:false,mcpAvailable:false,skillsAvailable:false,pluginPacksAvailable:false,contextOptions:null});
 assert.equal(disabled.soulActive,false);assert.equal(disabled.workspaceActive,false);assert.equal(disabled.shortTermMemoryActive,true);
});

test('one restore replaces all settings while individual updates retain stable actions and no-op identity',()=>{
 const h=harness('./composer-settings.jsx',deps);h.run(m=>m.useWbcComposerSettings(null));
 const initial=h.value.settings,actions=h.value.actions;
 actions.setSoulActive(true);h.flush();assert.equal(h.value.settings,initial);assert.equal(h.value.actions,actions);
 actions.setSoulActive(false);actions.setProjectMemoryActive(false);h.flush();assert.equal(h.value.settings.soulActive,false);assert.equal(h.value.settings.projectMemoryActive,false);
 const restored={...initial,mode:'full',workspaceActive:false};actions.restore(restored);h.flush();assert.equal(h.value.settings,restored);assert.equal(h.value.settings.projectMemoryActive,true);
 actions.setMode(v=>v==='full'?'auto':'full');h.flush();assert.equal(h.value.settings.mode,'auto');assert.equal(h.value.settings.workspaceActive,false);h.unmount();
});
