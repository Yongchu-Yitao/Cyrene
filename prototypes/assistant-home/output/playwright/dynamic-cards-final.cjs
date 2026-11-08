async(page)=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const b=name=>page.getByRole('button',{name,exact:true});
 const out='/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/output/playwright/';
 await page.frameLocator('.generated-report').getByRole('heading',{level:1}).waitFor();
 await b('固定风险与限制').click();await page.locator('[data-card="risks"][data-origin="user"]').waitFor();
 await b('取消固定风险与限制').click();await page.locator('[data-card="risks"][data-origin="agent"]').waitFor();
 await b('移除卡片风险与限制').click();await page.locator('[data-card="risks"]').waitFor({state:'hidden'});
 await b('管理常驻卡片').click();await b('恢复已隐藏的 Agent 卡片').click();await page.locator('[data-card="risks"][data-origin="agent"]').waitFor();await b('管理常驻卡片').click();
 for(const title of ['杭州 · 地图','杭州 · 旅行预算','杭州 · 出行提示'])await b('折叠'+title).click();
 await page.reload();await b('展开杭州 · 地图').waitFor();await page.frameLocator('.generated-report').getByRole('heading',{level:1}).waitFor();await page.locator('.resident-stack').evaluate(el=>el.scrollTop=0);await page.screenshot({path:out+'dynamic-cards-research.png',scale:'css'});
 const id=await page.evaluate(()=>localStorage.getItem('cyrene-intelligent-demo-id'));const s=await(await page.request.get(`http://127.0.0.1:4173/api/demo/${id}`)).json();
 if(s.shelf.pins.length!==4||s.travel.city.name!=='上海'||s.shelf.pins.find(p=>p.type==='budget').snapshot.city.name!=='杭州')throw Error('Persisted task/pin mismatch');
 if(errors.length)throw Error(errors.join(';'));
 return {result:'passed',pins:s.shelf.pins.map(p=>({type:p.type,taskId:p.taskId,city:p.snapshot?.city?.name})),currentTravel:s.travel.city.name,checks:['Pin/unpin restores generated card','Hide/restore generated card','Collapsed pins persist on reload','Old task snapshots persist in server state'],errors};
}
