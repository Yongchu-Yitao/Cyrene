import test from 'node:test';
import assert from 'node:assert/strict';
import { weatherKind, weatherPeriod } from './card-weather-model.mjs';
const day = new Date(2026, 10, 9, 12);
const night = new Date(2026, 10, 9, 21);
test('weather artwork follows actual conditions and explicit night choice', () => {
  for (const [condition, expected] of [['多云','partly_cloudy'],['阴，夜间小雨','rain'],['雷阵雨','storm'],['晴','clear'],['大雪','snow'],['雾','fog'],['overcast','cloudy'],['unavailable','unknown']]) {
    assert.equal(weatherKind({condition},day),expected);
  }
  assert.equal(weatherKind({condition:'晴', icon:'night'},night),'night');
  assert.equal(weatherKind({condition:'晴', icon:'invalid'},day),'clear');
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
  const context = {module:{exports:{}},require:name => name === 'react' ? React : {weatherKind, weatherPeriod}};
  vm.runInNewContext(transformSync(readFileSync(new URL('./card-weather.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,context);
  function nodes(node) {if (typeof node?.type === 'function') return nodes(node.type(node.props)); return !node || typeof node !== 'object' ? [] : [node,...React.Children.toArray(node.props?.children).flatMap(nodes)];}
  for (const condition of ['小雨','雷阵雨']) {
    const tree = context.module.exports.CardWeather({block:{condition,location:'广州',temperature:'19°C'},showMetadata:false});
    const all = nodes(tree);
    const frame = all.find(node => node.props?.className === 'weather-cloud-system');
    assert.ok(nodes(frame).some(node => node.props?.className === 'weather-cloud'));
    const rain = nodes(frame).find(node => node.props?.className === 'weather-rain');
    assert.deepEqual(nodes(rain).filter(node => node.type === 'path').map(node => node.props.d),condition === '雷阵雨' ? ['M44 106 v10','M120 106 v10'] : ['M52 106 v10','M80 106 v10','M108 106 v10']);
    assert.ok(!all.some(node => node.props?.className === 'wbc-weather-source'));
  }
});

test('all weather types follow the live clock without changing observations', () => {
  for (const hour of [0, 5, 6, 12, 17, 18, 23]) {
    const now = new Date(2026, 10, 9, hour);
    assert.equal(weatherPeriod(now), hour < 6 || hour >= 18 ? 'night' : 'day');
  }
  for (const icon of ['clear','night','partly_cloudy','cloudy','rain','snow','storm','fog']) {
    const block = Object.freeze({icon, condition:'天气', updated_at:'2026-11-09T12:00:00+08:00'});
    const clear = ['clear','night'].includes(icon);
    assert.equal(weatherKind(block, day), clear ? 'clear' : icon);
    assert.equal(weatherKind(block, night), clear ? 'night' : icon);
  }
});

test('day and night artwork retain each condition and never show a sun at night', async () => {
  const {readFileSync} = await import('node:fs');
  const {transformSync} = await import('esbuild');
  const vm = await import('node:vm');
  const React = await import('react');
  const context = {module:{exports:{}},require:name => name === 'react' ? React : {weatherKind, weatherPeriod}};
  vm.runInNewContext(transformSync(readFileSync(new URL('./card-weather.jsx',import.meta.url),'utf8'),{loader:'jsx',format:'cjs'}).code,context);
  function classes(node) {if (typeof node?.type === 'function') return classes(node.type(node.props)); return !node || typeof node !== 'object' ? [] : [node.props?.className,...React.Children.toArray(node.props?.children).flatMap(classes)];}
  for (const icon of ['clear','partly_cloudy','cloudy','rain','snow','storm','fog']) {
    for (const now of [day, night]) {
      const tree = context.module.exports.CardWeather({block:{icon},now});
      const all = classes(tree);
      assert.equal(tree.props['data-period'], now === night ? 'night' : 'day');
      assert.equal(all.includes('weather-sun'), now === day && ['clear','partly_cloudy'].includes(icon));
      assert.equal(all.includes('weather-moon'), now === night && ['clear','partly_cloudy'].includes(icon));
      if (icon === 'rain' || icon === 'storm') assert.ok(all.includes('weather-rain'));
      if (icon === 'storm') assert.ok(all.includes('weather-lightning'));
      if (icon === 'snow') assert.ok(all.includes('weather-snow'));
      if (icon === 'fog') assert.ok(all.includes('weather-fog'));
      if (['partly_cloudy','cloudy','rain','snow','storm'].includes(icon)) assert.ok(all.includes('weather-cloud'));
    }
  }
});
