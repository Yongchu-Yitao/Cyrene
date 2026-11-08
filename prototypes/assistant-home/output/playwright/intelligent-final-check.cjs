async(page)=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const root='/Users/syw/Documents/playground/Cyrene/prototypes/assistant-home/output/playwright/';
 await page.route('https://tile.openstreetmap.org/**',route=>route.abort());
 await page.reload();
 await page.getByText('部分地图底图未能加载，地点列表仍可使用。',{exact:true}).waitFor();
 await page.unroute('https://tile.openstreetmap.org/**');
 await page.reload();
 await page.waitForFunction(()=>{const tiles=[...document.querySelectorAll('.leaflet-tile-pane img')];return tiles.length>0&&tiles.every(t=>t.complete&&t.naturalWidth>0)&&!document.querySelector('.map-loading')},null,{timeout:30000});
 await page.getByRole('button',{name:'调整旅行条件',exact:true}).click();
 await page.getByLabel('旅行天数',{exact:true}).selectOption('3');
 await page.getByLabel('旅行预算',{exact:true}).fill('2000');
 await page.getByRole('button',{name:'更新行程',exact:true}).click();
 await page.getByRole('heading',{name:'杭州 3 日旅行'}).waitFor();
 await page.locator('.workspace-scroll').evaluate(el=>el.scrollTop=0);
 await page.screenshot({path:root+'travel-map-final.png'});
 const sizes=[];
 for(const width of [1280,900,390]){
  await page.setViewportSize({width,height:width===390?844:900});
  await page.waitForFunction(()=>{const tiles=[...document.querySelectorAll('.leaflet-tile-pane img')];return tiles.length>0&&tiles.every(t=>t.complete&&t.naturalWidth>0)&&!document.querySelector('.map-loading')},null,{timeout:30000});
  const m=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,send:document.querySelector('.send-button').getBoundingClientRect().right}));
  if(m.scroll>m.width||m.send>m.width)throw Error('Overflow '+width);
  await page.screenshot({path:root+`intelligent-travel-${width}.png`});sizes.push(m);
 }
 await page.setViewportSize({width:1586,height:992});
 await page.getByRole('navigation',{name:'打开的工作区'}).getByRole('button',{name:'智能工作区',exact:true}).click();
 await page.screenshot({path:root+'intelligent-home-final.png'});
 if(errors.length)throw Error(errors.join(';'));
 return {result:'passed',targetedChecks:['Map tile failure status','All visible map tiles loaded','Travel settings update','Responsive layout and send button at 1280,900,390','No page runtime errors'],sizes,errors};
}
