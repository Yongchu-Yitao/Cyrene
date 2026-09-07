import test from 'node:test';
import assert from 'node:assert/strict';
import { wbcBuildChatRailItems, partitionRailItems } from './rail-projection.mjs';

// The previous observable grouping rule, including overlapping groups and
// duplicate group IDs. This is an oracle, not a second production path.
function original(chats,groups){
 const list=Array.isArray(chats)?chats:[], rendered=new Set(),items=[];
 list.forEach(chat=>{
  const id=String(chat&&chat.id||'');
  const group=(Array.isArray(groups)?groups:[]).find(g=>Array.isArray(g.chatIds)&&g.chatIds.indexOf(id)>=0)||null;
  if(!group){items.push({kind:'chat',chat});return;}
  if(rendered.has(group.id))return;rendered.add(group.id);
  items.push({kind:'group',group,chats:list.filter(c=>group.chatIds.indexOf(String(c&&c.id||''))>=0)});
 });return items;
}

test('indexed grouping preserves order, overlapping membership and duplicate IDs',()=>{
 const chats=Array.from({length:12},(_,i)=>({id:String(i),title:'Chat '+i}));
 let seed=991;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed;};
 for(let i=0;i<200;i++){
  const groups=Array.from({length:5},(_,j)=>({id:'g'+(j%3),chatIds:Array.from({length:7},()=>String(rand()%12))}));
  groups.push(groups[0]);groups.push({id:'numeric',chatIds:[1,2]});
  const list=chats.filter(()=>rand()%3!==0);
  const actual=wbcBuildChatRailItems(list,groups);assert.deepEqual(actual,original(list,groups));
  for(const item of actual){if(item.kind==='chat')assert.ok(list.includes(item.chat));else assert.ok(groups.includes(item.group));}
 }
 const sparse=[chats[0],,chats[2]];assert.deepEqual(wbcBuildChatRailItems(sparse,[]),original(sparse,[]));
 assert.deepEqual(wbcBuildChatRailItems([],null),[]);
});

test('one-pass sections preserve relative order and item identity',()=>{
 const items=[{kind:'chat',chat:{id:1}},{kind:'group',group:{}},{kind:'chat',chat:{id:'2'}},{kind:'ignored'}];
 const result=partitionRailItems(items,new Set(['1']));assert.deepEqual(result,{groupItems:[items[1]],pinnedItems:[items[0]],recentItems:[items[2]]});
 assert.equal(result.pinnedItems[0],items[0]);
});
