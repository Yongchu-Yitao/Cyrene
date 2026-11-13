import test from 'node:test';
import assert from 'node:assert/strict';
import { requiresVisionModel } from './composer-vision.mjs';

test('image drafts require confirmed vision capability and react to model changes', () => {
  const images = [{ kind: 'image', name: 'screenshot.png' }];
  assert.equal(requiresVisionModel(images, undefined), true);
  assert.equal(requiresVisionModel(images, { capabilities: ['chat'] }), true);
  assert.equal(requiresVisionModel(images, { capabilities: ['chat', 'vision'] }), false);
  assert.equal(requiresVisionModel([], undefined), false);
  assert.equal(requiresVisionModel([{ name: 'paper.pdf' }], { capabilities: ['chat'] }), false);
  assert.equal(requiresVisionModel([{ content_type: 'image/png' }], {}), true);
  assert.equal(requiresVisionModel([{ name: 'image.JPG', content_type: 'application/octet-stream' }], {}), true);
});
