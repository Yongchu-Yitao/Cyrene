async (page) => {
 const passed=[], errors=[];const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
 const button=(name)=>page.getByRole('button',{name,exact:true});const label=(name)=>page.getByLabel(name,{exact:true});
 const body=()=>page.locator('body').innerText();const close=()=>button('关闭弹窗').click();
 const out='/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/output/playwright/';
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});

 await page.locator('input[type=file]').setInputFiles({name:'demo-note.txt',mimeType:'text/plain',buffer:Buffer.from('demo attachment')});await label('给 Cyrene 发消息').fill('帮我看看今天的安排');await button('发送消息').click();await page.getByRole('button',{name:'发送消息',exact:true}).isDisabled();await page.locator('.thinking').waitFor({state:'hidden'});assert((await body()).includes('demo-note.txt'),'Attachment metadata');await page.screenshot({path:out+'chat-desktop.png'});passed.push('Chat, task creation and attachment metadata');
 await page.reload();await button('任务看板').click();await page.getByRole('heading',{name:'准备下周计划',exact:true}).waitFor();passed.push('Reload persistence');
 await button('演示设置').click();await button('重置').click();await button('今天').click();await button('切换主题').click();assert(await page.locator('html').getAttribute('data-theme')==='light','Theme');await page.screenshot({path:out+'home-light.png'});await button('切换主题').click();await button('收起侧栏').click();assert(await page.locator('.original-sidebar.collapsed').count()===1,'Collapse sidebar');await button('展开侧栏').click();await button('收起右侧面板').click();assert(await page.locator('.context-panel').count()===0,'Collapse context');await button('展开右侧面板').click();passed.push('Theme, sidebar and context toggles');
 await page.screenshot({path:out+'home-desktop.png'});
 for(const [width,height] of [[1280,800],[900,800],[390,844]]){await page.setViewportSize({width,height});await page.screenshot({path:out+`home-${width}.png`});const g=await page.evaluate(()=>{const r=document.querySelector('.send-button').getBoundingClientRect();return {width:document.documentElement.scrollWidth,view:innerWidth,right:r.right,bottom:r.bottom,height:innerHeight}});assert(g.width<=g.view,`Horizontal overflow ${width}`);assert(g.right<=g.view&&g.bottom<=g.height,`Composer hidden ${width}`)}passed.push('Responsive widths 1280, 900, 390; no page overflow; composer visible');
 await page.setViewportSize({width:1586,height:992});if(await button('展开右侧面板').count())await button('展开右侧面板').click();await button('演示设置').click();await button('重置').click();await button('今天').click();await page.screenshot({path:out+'home-final.png'});
 assert(errors.length===0,'Console errors: '+errors.join(';'));console.log(JSON.stringify({passed,errors,final:'passed'},null,2));
}
