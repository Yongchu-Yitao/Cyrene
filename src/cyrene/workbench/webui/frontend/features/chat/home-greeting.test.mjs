import test from 'node:test';
import assert from 'node:assert/strict';
import {currentHomeGreeting,presentHomeSpec} from './home-greeting.mjs';
const at = hour => new Date(2026,10,9,hour,0);

test('cached salutations follow local display time including day boundaries', () => {
  for(const [hour,expected] of [[0,'夜深了'],[4,'夜深了'],[5,'早上好'],[10,'早上好'],[11,'中午好'],[12,'中午好'],[13,'下午好'],[17,'下午好'],[18,'晚上好'],[22,'晚上好'],[23,'夜深了']]) {
    assert.equal(currentHomeGreeting('晚上好，资料已经整理好了。',at(hour)),expected+'，资料已经整理好了。');
  }
  assert.equal(currentHomeGreeting('**晚上好**，继续昨天的任务。',at(8)),'**早上好**，继续昨天的任务。');
  assert.equal(currentHomeGreeting('### 晚上好\n有两件事待确认。',at(14)),'### 下午好\n有两件事待确认。');
});

test('only overview salutations change, leaving task content, weather and stored data untouched', () => {
  const weather={type:'weather',advice:'今晚可能下雨'};
  const spec={home_kind:'overview',title:'晚上好',subtitle:'有两件事待确认。',blocks:[{type:'text',text:'晚上好。资料已整理。'},weather,{type:'action_list',title:'今日已完成',items:[]}]};
  const before=JSON.stringify(spec), view=presentHomeSpec(spec,at(8));
  assert.equal(view.title,'早上好');assert.equal(view.blocks[0].text,'早上好。资料已整理。');
  assert.equal(view.subtitle,spec.subtitle);assert.equal(view.blocks[1],weather);
  assert.equal(JSON.stringify(spec),before);
  const resume={...spec,home_kind:'resume'};assert.equal(presentHomeSpec(resume,at(8)),resume);
  for(const text of ['欢迎回来','今天从这里继续','昨天他说晚上好。','“晚上好”是邮件标题']) assert.equal(currentHomeGreeting(text,at(8)),text);
});
