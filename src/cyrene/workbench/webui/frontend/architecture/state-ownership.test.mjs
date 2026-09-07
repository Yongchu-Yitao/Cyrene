import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {summarizeStructure} from '../../build/complexity-report.mjs';

test('moving a callback to another module cannot hide its domain decision total',()=>{
 const before=summarizeStructure({'electron/a.js':{module_lines:100},'electron/a.js::owner':{decisions:4,lines:90},'electron/a.js::owner.callback':{decisions:8,lines:30}});
 const after=summarizeStructure({'electron/a.js':{module_lines:70},'electron/a.js::owner':{decisions:4,lines:60},'electron/b.js':{module_lines:30},'electron/b.js::callback':{decisions:8,lines:30}});
 assert.equal(before.electron.decisions,12);assert.equal(after.electron.decisions,12);assert.equal(after.electron.moduleLines,100);
});

test('split selection clients cannot reintroduce independently writable slot maps',()=>{
 const directory=new URL('../features/chat/',import.meta.url);
 for(const file of fs.readdirSync(directory).filter(f=>/\.(jsx|mjs)$/.test(f)&&!f.includes('.test.'))){
  const source=fs.readFileSync(new URL(file,directory),'utf8');
  assert.doesNotMatch(source,/\bset(?:SideAgent|Artifact|Change|Resource)SplitByChat\b/,file);
 }
 const page=fs.readFileSync(new URL('page.jsx',directory),'utf8');
 assert.match(page,/useWbcSplitSelection\(\)/);
});
