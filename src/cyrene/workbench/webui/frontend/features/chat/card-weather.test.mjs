import test from 'node:test';
import assert from 'node:assert/strict';
import { weatherKind } from './card-weather-model.mjs';
test('weather artwork follows actual conditions and explicit night choice', () => {
  for (const [condition, expected] of [['多云','partly_cloudy'],['阴，夜间小雨','rain'],['雷阵雨','storm'],['晴','clear'],['大雪','snow'],['雾','fog'],['overcast','cloudy'],['unavailable','unknown']]) {
    assert.equal(weatherKind({condition}),expected);
  }
  assert.equal(weatherKind({condition:'晴', icon:'night'}),'night');
  assert.equal(weatherKind({condition:'晴', icon:'invalid'}),'clear');
});

test('opening welcome cards only reads saved content and passes complete snapshots', async () => {
  const {readFileSync} = await import('node:fs');
  const source = readFileSync(new URL('./assistant-home-cards.jsx',import.meta.url),'utf8');
  assert.ok(source.includes("'/api/assistant/cards/home'"));
  assert.ok(source.includes('initialCard={card}'));
  assert.ok(!source.includes('/refresh'));
  assert.ok(!source.includes("method: 'POST'"));
  assert.ok(!source.includes('busy.current'));
});

test('rain shares the moving cloud frame and falls below its center', async () => {
  const {readFileSync} = await import('node:fs');
  const {transformSync} = await import('esbuild');
  const vm = await import('node:vm');
  const React = await import('react');
  const context = {module:{exports:{}},require:name => name === 'react' ? React : {weatherKind}};
  vm.runInNewContext(transformSync(readFileSync(new URL('./card-weather.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,context);
  function nodes(node) {return !node || typeof node !== 'object' ? [] : [node,...React.Children.toArray(node.props?.children).flatMap(nodes)];}
  for (const condition of ['小雨','雷阵雨']) {
    const tree = context.module.exports.CardWeather({block:{condition,location:'广州',temperature:'19°C'},showMetadata:false});
    const all = nodes(tree);
    const frame = all.find(node => node.props?.className === 'weather-cloud-system');
    assert.ok(nodes(frame).some(node => node.props?.className === 'weather-cloud'));
    const rain = nodes(frame).find(node => node.props?.className === 'weather-rain');
    assert.deepEqual(nodes(rain).filter(node => node.type === 'path').map(node => node.props.d),['M52 106 v10','M80 106 v10','M108 106 v10']);
    assert.ok(!all.some(node => node.props?.className === 'wbc-weather-source'));
  }
});
