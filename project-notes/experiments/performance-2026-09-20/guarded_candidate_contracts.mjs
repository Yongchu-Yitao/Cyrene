// Run existing timeline contracts against the unapplied candidate in memory.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
const web=new URL('../../../src/cyrene/workbench/webui/',import.meta.url);
const require=createRequire(new URL('package.json',web));
const esbuild=require('esbuild');
const testURL=new URL('frontend/features/chat/runtime-timeline.test.mjs',web);
const baseline=readFileSync(new URL('frontend/features/chat/runtime-timeline.jsx',web),'utf8');
const candidate=readFileSync(new URL('proposed-runtime-timeline.jsx',import.meta.url),'utf8');
const testSource=readFileSync(testURL,'utf8').replaceAll('import.meta.url',JSON.stringify(testURL.href));
const compiled=esbuild.transformSync(testSource,{loader:'js',format:'cjs'}).code;
function injectedRequire(name){
  if(name==='esbuild')return {...esbuild,transformSync:(source,options)=>esbuild.transformSync(source===baseline?candidate:source,options)};
  return require(name);
}
const module={exports:{}};
new Function('require','module','exports',compiled)(injectedRequire,module,module.exports);
