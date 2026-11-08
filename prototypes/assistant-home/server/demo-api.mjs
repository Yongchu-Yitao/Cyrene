import {readFileSync,mkdirSync,writeFileSync,renameSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {newSession,act,finish,exportMarkdown} from './demo-engine.mjs';
import {refreshCards,applyCardAction} from './resident-cards.mjs';
const stateFile=resolve('.demo-state/workspaces.json');
let db={};try{db=JSON.parse(readFileSync(stateFile,'utf8'));}catch{}
for(const session of Object.values(db))for(const kind of ['research','travel']){const w=session[kind];if(w&&['generating','running'].includes(w.status)){w.status='review';w.log.push({text:'服务重新启动，可继续操作。',time:Date.now()});}}
for(const session of Object.values(db))refreshCards(session);
const listeners=new Map(),timers=new Set(),seen=new Map();
function persist(){mkdirSync(dirname(stateFile),{recursive:true});writeFileSync(stateFile+'.tmp',JSON.stringify(db));renameSync(stateFile+'.tmp',stateFile);}
function publish(id){refreshCards(db[id]);persist();for(const response of listeners.get(id)||[])response.write(`data: ${JSON.stringify(db[id])}\n\n`);}
function schedule(fn,delay){const timer=setTimeout(()=>{timers.delete(timer);fn();},delay);timers.add(timer);}
function run(id,kind,type){const steps=type==='report'?['读取选定资料与补充要求','组装结论、引用与界面','已生成可继续编辑的成果']:kind==='travel'?['识别目的地、天数与预算','准备地理位置与行程组件','地图与旅行工作区已就绪']:['识别研究主题与目标','准备来源选择与需求表单','资料工作区已就绪'];steps.forEach((text,index)=>schedule(()=>{const w=db[id]?.[kind];if(!w)return;w.log.push({text,time:Date.now()});w.log=w.log.slice(-15);if(index===2)finish(w,type);w.revision++;publish(id);},450+index*650));}
const json=(res,code,data)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
async function readBody(req){let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>32000)throw Error('提交内容过长。');}return JSON.parse(raw||'{}');}
export function demoApi(){return {name:'cyrene-demo-api',configureServer(server){server.middlewares.use(async(req,res,next)=>{
 const url=new URL(req.url,'http://localhost');if(!url.pathname.startsWith('/api/demo/'))return next();
 const match=url.pathname.match(/^\/api\/demo\/([a-zA-Z0-9-]{16,64})(?:\/(events|action|export))?$/);if(!match)return json(res,404,{error:'演示接口不存在。'});
 const [,id,route]=match;if(!db[id]){if(Object.keys(db).length>=128)return json(res,429,{error:'演示会话过多，请清理本地演示数据。'});db[id]=refreshCards(newSession());persist();}
 if(req.method==='GET'&&route==='events'){res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});res.write(`data: ${JSON.stringify(db[id])}\n\n`);if(!listeners.has(id))listeners.set(id,new Set());listeners.get(id).add(res);const heartbeat=setInterval(()=>res.write(': keepalive\n\n'),15000);req.on('close',()=>{clearInterval(heartbeat);listeners.get(id)?.delete(res);});return;}
 if(req.method==='GET'&&route==='export'){try{const kind=url.searchParams.get('kind');if(!['research','travel'].includes(kind))throw Error('工作区不存在。');const content=exportMarkdown(db[id][kind]);res.writeHead(200,{'Content-Type':'text/markdown; charset=utf-8','Content-Disposition':`attachment; filename="cyrene-${kind}.md"`});res.end(content);}catch(e){json(res,400,{error:e.message});}return;}
 if(req.method==='GET'&&!route)return json(res,200,db[id]);
 if(req.method!=='POST'||route!=='action')return json(res,405,{error:'请求方式不支持。'});
 try{const body=await readBody(req);if(!['research','travel','shelf'].includes(body.kind))throw Error('工作区不存在。');if(!/^[a-zA-Z0-9-]{8,80}$/.test(body.requestId||''))throw Error('缺少操作标识。');const cacheKey=id+':'+body.requestId;if(seen.has(cacheKey))return json(res,200,db[id]);const current=db[id][body.kind];if(body.revision!==current.revision)return json(res,409,{error:'工作区已更新，请重试当前操作。',state:db[id]});if(body.kind==='shelf'){const draft=JSON.parse(JSON.stringify(db[id]));applyCardAction(draft,body.action,body.payload);draft.shelf.revision=current.revision+1;db[id]=draft;seen.set(cacheKey,Date.now());if(seen.size>3000)seen.delete(seen.keys().next().value);publish(id);return json(res,200,db[id]);}const draft=JSON.parse(JSON.stringify(current));const outcome=act(draft,body.action,body.payload);const updated=outcome.workspace||draft;updated.revision=current.revision+1;db[id][body.kind]=updated;seen.set(cacheKey,Date.now());if(seen.size>3000)seen.delete(seen.keys().next().value);publish(id);json(res,200,db[id]);if(outcome.run)run(id,body.kind,outcome.run);
 }catch(e){json(res,400,{error:e.message||'操作失败。'});}
 });server.httpServer?.on('close',()=>{for(const timer of timers)clearTimeout(timer);for(const clients of listeners.values())for(const client of clients)client.end();});}};}
