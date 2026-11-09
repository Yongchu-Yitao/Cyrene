import test from 'node:test';
import assert from 'node:assert/strict';
import { cardMapStyles } from './card-map-style.mjs';
test('legacy map markers and connectors follow the live application accent', () => {
  const style = cardMapStyles({ places: [] });
  assert.equal(style.placeColor({}), 'var(--accent)');
  assert.equal(style.routeColor({}), 'var(--accent)');
  assert.deepEqual(style.groups, []);
});
test('group colors match legend, inherit only shared endpoints, and survive selection changes', () => {
  const block = { groups: [{ group_id: 'old', label: '老城区' }, { group_id: 'lake', label: '湖区' }], places: [{ place_id: 'a', group_id: 'old' }, { place_id: 'b', group_id: 'old' }, { place_id: 'c', group_id: 'lake' }] };
  const style = cardMapStyles(block);
  assert.equal(style.placeColor(block.places[0]), style.groups[0].color);
  assert.notEqual(style.groups[0].color, style.groups[1].color);
  assert.equal(style.routeColor({ from_place_id: 'a', to_place_id: 'b' }), style.groups[0].color);
  assert.equal(style.routeColor({ from_place_id: 'a', to_place_id: 'c', group_id: 'lake' }), style.groups[1].color);
  assert.equal(style.routeColor({ from_place_id: 'a', to_place_id: 'c' }), 'var(--accent)');
  assert.equal(style.placeColor({}), 'var(--accent)');
  const recolored = cardMapStyles(block, '#169b70');
  assert.equal(recolored.placeColor(block.places[0]), '#169b70');
  assert.ok(recolored.groups.every(group => group.color.includes('#169b70')));
  assert.notEqual(recolored.groups[1].color, style.groups[1].color);
  assert.deepEqual(cardMapStyles({ ...block, selection: { field_id: 'pick' } }).groups, style.groups);
});
