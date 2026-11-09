import test from 'node:test';
import assert from 'node:assert/strict';
import {createGripLogoMood} from './grip-logo-mood.mjs';
test('expressions require sustained intent and keep at least 900ms between changes',()=>{
 const mood=createGripLogoMood();mood.update(20,0,0);
 assert.equal(mood.sample(200),'');assert.equal(mood.sample(220),'curious');
 mood.update(160,0,300);assert.equal(mood.sample(600),'curious');assert.equal(mood.sample(1120),'strain');
});
test('repeated direction changes trigger anger without flickering on a single reversal',()=>{
 const mood=createGripLogoMood();mood.update(60,0,0);assert.equal(mood.sample(220),'curious');
 mood.update(-60,0,300);assert.equal(mood.sample(520),'curious');
 mood.update(60,0,550);mood.update(-60,0,800);assert.equal(mood.sample(1020),'curious');assert.equal(mood.sample(1120),'annoyed');
});
