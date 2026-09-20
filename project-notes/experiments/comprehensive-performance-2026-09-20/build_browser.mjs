import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const web=new URL('../../../src/cyrene/workbench/webui/',import.meta.url);
const require=createRequire(new URL('package.json',web));
const {build}=require('esbuild');
await build({entryPoints:[fileURLToPath(new URL('browser.jsx',import.meta.url))],outfile:fileURLToPath(new URL('browser-bundle.js',import.meta.url)),
 bundle:true,format:'iife',minify:true,define:{'process.env.NODE_ENV':'"production"'}});
