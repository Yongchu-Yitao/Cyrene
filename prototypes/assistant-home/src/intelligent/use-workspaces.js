import {useEffect,useState,useRef,useCallback} from 'react';
export function useWorkspaces(){
 const [id]=useState(()=>{const key='cyrene-intelligent-demo-id';let value;try{value=localStorage.getItem(key);}catch{}if(!value||!/^[a-zA-Z0-9-]{16,64}$/.test(value)){value=crypto.randomUUID();try{localStorage.setItem(key,value);}catch{}}return value;});
 const [state,setState]=useState(null),[connected,setConnected]=useState(false),[error,setError]=useState(''),[pending,setPending]=useState(false);const stateRef=useRef(null),queue=useRef(Promise.resolve());
 const receive=useCallback(next=>{stateRef.current=next;setState(next);},[]);
 useEffect(()=>{let mounted=true;const stream=new EventSource(`/api/demo/${id}/events`);stream.onmessage=e=>{try{const next=JSON.parse(e.data);if(mounted){receive(next);setConnected(true);}}catch{setError('工作区数据格式异常，请刷新重试。');}};stream.onerror=()=>{if(mounted)setConnected(false);};return()=>{mounted=false;stream.close();};},[id,receive]);
 const dispatch=useCallback((kind,action,payload={})=>{
  const execute=async()=>{if(!stateRef.current){setError('工作区尚未连接，请稍后重试。');return null;}setPending(true);setError('');try{const response=await fetch(`/api/demo/${id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,action,payload,revision:stateRef.current[kind].revision,requestId:crypto.randomUUID()}),signal:AbortSignal.timeout(12000)});const next=await response.json();if(!response.ok){if(next.state)receive(next.state);throw Error(next.error||'操作失败');}receive(next);return next;}catch(e){setError(e.message);return null;}finally{setPending(false);}};
  const next=queue.current.then(execute,execute);queue.current=next.catch(()=>{});return next;
 },[id,receive]);
 return {state,connected,error,clearError:()=>setError(''),pending,dispatch,download:(kind)=>{const a=document.createElement('a');a.href=`/api/demo/${id}/export?kind=${kind}`;a.download=`cyrene-${kind}.md`;a.click();}};
}
