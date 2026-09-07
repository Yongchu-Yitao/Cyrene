import test from 'node:test';
import assert from 'node:assert/strict';
import {sessionMenuResources} from '../features/shell/topbar-resource-projection.mjs';

test('cached and refreshed resource projection preserves identity and browser availability',()=>{
 const files=[{path:'file'}],browser={url:'url'},resources={files,browser};
 assert.deepEqual(sessionMenuResources(true,resources),resources);
 assert.equal(sessionMenuResources(true,resources).files,files);
 assert.equal(sessionMenuResources(false,resources).browser,null);
 assert.equal(sessionMenuResources(false,resources).files,files);
 assert.deepEqual(sessionMenuResources(true,null),{browser:null,files:[]});
 assert.deepEqual(sessionMenuResources(true,{files:{}}),{browser:null,files:[]});
});
