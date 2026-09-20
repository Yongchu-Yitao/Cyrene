import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const web=new URL('../../../src/cyrene/workbench/webui/',import.meta.url);
const require=createRequire(new URL('package.json',web));
const {build}=require('esbuild');
for(const variant of ['baseline','candidate']) {
 await build({entryPoints:[fileURLToPath(new URL('browser.jsx',import.meta.url))],outfile:fileURLToPath(new URL(variant+'.js',import.meta.url)),
 plugins:[{name:'baseline-and-render-counts',setup(build){build.onLoad({filter:/features\/chat\/(messages|runtime-timeline)\.jsx$/},args=>{
 const name=args.path.split('/').at(-1);
 let contents=readFileSync(variant==='baseline'?new URL('baseline/src/cyrene/workbench/webui/frontend/features/chat/'+name,import.meta.url):args.path,'utf8');
 if(process.argv.includes('--count-renders'))contents=contents.replace(/(function WbcAssistantMessage(?:Body)?\(\{ msg, liveRuntime, onOpenFile, onRetryMessage, chatId \}\) \{)/,'$1 window.__auditAssistantCalls=(window.__auditAssistantCalls||0)+1;');
 return {loader:'jsx',contents};
 });}}],bundle:true,format:'iife',minify:true,define:{'process.env.NODE_ENV':'"production"'}});
}
