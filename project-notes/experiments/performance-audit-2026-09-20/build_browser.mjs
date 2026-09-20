import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const web=new URL('../../../src/cyrene/workbench/webui/',import.meta.url);
const require=createRequire(new URL('package.json',web));
const {build}=require('esbuild');
await build({entryPoints:[fileURLToPath(new URL('browser.jsx',import.meta.url))],outfile:fileURLToPath(new URL('browser-bundle.js',import.meta.url)),
 plugins:process.argv.includes('--count-renders')?[{name:'count-renders',setup(build){build.onLoad({filter:/features\/chat\/messages\.jsx$/},args=>({loader:'jsx',contents:readFileSync(args.path,'utf8').replace('function WbcAssistantMessage({ msg, liveRuntime, onOpenFile, onRetryMessage, chatId }) {','function WbcAssistantMessage({ msg, liveRuntime, onOpenFile, onRetryMessage, chatId }) { window.__auditAssistantCalls=(window.__auditAssistantCalls||0)+1;')}));}}]:[],bundle:true,format:'iife',minify:true,define:{'process.env.NODE_ENV':'"production"'}});
