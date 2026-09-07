const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { BrowserVideoFullscreen } = require('./browser-video-fullscreen');

class Window extends EventEmitter {
  constructor(options={}) { super();this.options=options;this.full=false;this.destroyed=false;this.contentView={removeChildView:()=>{}}; }
  isDestroyed(){return this.destroyed;}
  isFullScreen(){return this.full;}
  setFullScreen(value){this.full=value;}
  getContentSize(){return [800,600];}
  getBounds(){return {x:0,y:0,width:800,height:600};}
  setMenuBarVisibility(){}
  show(){}
  destroy(){this.destroyed=true;this.emit('closed');}
}
for (const platform of ['mac','windows','linux']) {
  test(`fullscreen preserves ${platform} window ownership and cleanup`, async () => {
    const main=new Window();const events=[];
    const view={webContents:{isDestroyed:()=>false}};const tab={id:'tab',view};
    const owner=new BrowserVideoFullscreen({mainWindow:()=>main,isQuitting:()=>false,
      isMac:platform==='mac',isWindows:platform==='windows',isLinux:platform==='linux',BrowserWindow:Window,
      screen:{getDisplayMatching:()=>({bounds:{width:1920,height:1080}})},windowTitle:()=> 'Video',
      tab:()=>tab,tabForView:v=>v===view?tab:null,activate:id=>events.push(['active',id]),
      syncAttachedView:()=>events.push('sync'),emitState:()=>events.push('state'),resetAttachment:()=>events.push('reset')});
    await owner.enterVideoFullscreen(view);
    assert.equal(owner.videoFullscreen.active,true);
    const external=owner.videoFullscreenWindow;
    if(platform==='mac'){
      assert.equal(external.options.webPreferences.sandbox,true);
      assert.equal(external.isFullScreen(),true);assert.equal(main.full,false);
    }else{assert.equal(main.full,true);assert.equal(main.listenerCount('resize'),1);}
    owner.finishVideoFullscreen({});assert.equal(owner.videoFullscreen.active,true);
    owner.finishVideoFullscreen(view);
    assert.equal(owner.videoFullscreen.active,false);assert.equal(main.full,false);
    assert.equal(main.listenerCount('resize'),0);assert.equal(main.listenerCount('leave-full-screen'),0);
    if(external)assert.equal(external.destroyed,true);
    assert.ok(events.includes('reset'));
  });
}
