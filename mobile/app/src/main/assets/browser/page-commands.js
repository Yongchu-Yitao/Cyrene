(function(method, args) {
  try {
    const state = window.__cyreneNativeDOM || (window.__cyreneNativeDOM = { refs: new Map(), sequence: 0 });
    const visible = el => { const r=el.getBoundingClientRect(), s=getComputedStyle(el);return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden"; };
    const info = () => ({ ok:true, url:location.href, title:document.title, status:200 });
    const text = el => String(el.innerText || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim();
    const box = el => {const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
    const elements = () => Array.from(document.querySelectorAll('a[href],button,input,textarea,select,[role="button"],[contenteditable="true"]')).filter(visible);
    const element = () => {
      const el = args.ref ? state.refs.get(String(args.ref)) : args.selector ? document.querySelector(args.selector) : null;
      if (!el || !el.isConnected || !visible(el)) throw Error("Element unavailable; take a fresh snapshot");
      return el;
    };
    const links = () => Array.from(document.querySelectorAll('a[href]')).filter(visible).slice(0,200).map(el=>({text:text(el),url:el.href}));
    if (method === "snapshot" || method === "inspect") {
      state.refs.clear();
      const list=elements().slice(0, Math.max(1,Math.min(200,args.maxElements||80))).map(el=>{
        const ref="a"+(++state.sequence);state.refs.set(ref,el);
        return {ref,tag:el.tagName.toLowerCase(),role:el.getAttribute("role")||"",text:text(el).slice(0,Math.min(args.textLimit||160,1000)),inputType:el.getAttribute("type")||"",box:box(el),rect:{x:box(el).x,y:box(el).y,w:box(el).width,h:box(el).height},href:el.href||"",accept:el.getAttribute("accept")||"",multiple:!!el.multiple};
      });
      return {...info(),text:(document.body?.innerText||"").slice(0,Math.max(0,Math.min(50000,args.maxChars??8000))),elements:list,links:links()};
    }
    if (method === "visibleLinkMatches") return {...info(),matches:links().filter(link=>link.url===args.url)};
    if (["click","clickRef","clickText","clickAt"].includes(method)) {
      let el;
      if (method === "clickText") el=elements().find(el=>args.exact?text(el)===args.text:text(el).includes(args.text));
      else if(method === "clickAt") el=document.elementFromPoint(Number(args.x),Number(args.y));
      else el=element();
      if(!el||!visible(el)) throw Error("Clickable element not found");
      el.scrollIntoView({block:"center"});el.click();return {...info(),box:box(el)};
    }
    if (method === "type" || method === "typeRef") {
      const el=element();if(el.disabled||el.readOnly||el.type==="file") throw Error("Element is not editable");
      el.focus();
      if(el.isContentEditable) el.textContent=String(args.text||"");
      else {const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:el instanceof HTMLInputElement?HTMLInputElement.prototype:null;
        if(!proto) throw Error("Element is not a text field");Object.getOwnPropertyDescriptor(proto,"value").set.call(el,String(args.text||""));}
      el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));
      if(args.submit&&el.form)el.form.requestSubmit();return {...info(),box:box(el)};
    }
    if(method === "scroll") {window.scrollBy(Number(args.x??args.deltaX??0),Number(args.y??args.deltaY??500));return info();}
    if(method === "waitFor") return {...info(),matched:(!args.selector||!!document.querySelector(args.selector))&&(!args.text||(document.body?.innerText||"").includes(args.text))&&(!args.urlContains||location.href.includes(args.urlContains))};
    if(method === "setMuted") {document.querySelectorAll("audio,video").forEach(el=>el.muted=!!args.muted);return info();}
    return {ok:false,code:"ANDROID_BROWSER_UNSUPPORTED",error:"Unsupported Android page command: "+method};
  } catch(error) {return {ok:false,code:"ANDROID_BROWSER_PAGE_ERROR",error:String(error.message||error)};}
})
