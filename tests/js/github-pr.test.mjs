import assert from 'node:assert/strict';
import { test } from 'node:test';

import { again, prLine, safeUrl, settled, whyNot } from '../../site/github-pr.js';

const draft = (more = {}) => ({ kind: 'fact', agent: 'visitor', entity: 'payments-api', predicate: 'database', value: 'mysql-8.4', source: 'docs/design.md', ...more });

test('a plain lowercase draft against the committed memory can go to GitHub', () => {
  assert.equal(whyNot(draft(), [], false), '');
});

test('anything else says why not', () => {
  assert.match(whyNot(draft(), [{ draft: draft() }], false), /Reset memory/);
  assert.match(whyNot(draft(), [], true), /Cypher console/);
  assert.match(whyNot(draft({ kind: 'retract' }), [], false), /retraction/);
  assert.match(whyNot(draft({ value: 'MySQL 8' }), [], false), /value must be lowercase/);
  assert.match(whyNot(draft({ value: '$(id)' }), [], false), /value/);
  assert.match(whyNot(draft({ entity: 'x'.repeat(61) }), [], false), /entity/);
  assert.match(whyNot(draft({ source: '' }), [], false), /source/);
  assert.match(whyNot(draft({ derived_from: ['payments-api--database--postgres-15'] }), [], false), /graph link/);
  assert.match(whyNot(draft({ about: false }), [], false), /graph link/);
  assert.match(whyNot(draft({ source_status: 'retracted' }), [], false), /graph link/);
  assert.match(whyNot(draft({ source: '../x' }), [], false), /source/);
});

test('only links into the repository are shown', () => {
  assert.equal(safeUrl('https://github.com/himanshu748/memory-pr/pull/3'), 'https://github.com/himanshu748/memory-pr/pull/3');
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('https://github.com/himanshu748/memory-pr.evil.example/'), null);
  assert.equal(safeUrl(undefined), null);
  assert.equal(prLine({ state: 'open', number: 3, check: 'pending', url: 'https://evil.example/' }).link, null);
});

test('polling stops once the check has reported, or there will be no pull request', () => {
  assert.equal(settled({ state: 'queued' }), false);
  assert.equal(settled({ state: 'unknown' }), false);
  assert.equal(settled({ state: 'open', check: 'pending' }), false);
  assert.equal(settled({ state: 'open', check: 'unknown' }), false);
  assert.equal(settled({ state: 'open', check: 'failure' }), true);
  assert.equal(settled({ state: 'open', check: 'success' }), true);
  assert.equal(settled({ state: 'failed' }), true);
  assert.equal(settled({ state: 'closed' }), true);
  assert.equal(settled({ state: 'refused' }), true);
});

test('each state has its own words', () => {
  const url = 'https://github.com/himanshu748/memory-pr/pull/41';
  assert.equal(prLine(null), null);
  assert.match(prLine({ state: 'queued' }).text, /opening/);
  assert.deepEqual(prLine({ state: 'open', number: 41, check: 'failure', url }), {
    tone: 'differs', text: 'It is open on GitHub and memory-review blocks the merge.', link: url, label: 'Pull request #41 on GitHub',
  });
  assert.equal(prLine({ state: 'open', number: 41, check: 'success', url }).tone, 'agrees');
  assert.match(prLine({ state: 'open', number: 41, check: 'pending', url }).text, /running/);
  assert.match(prLine({ state: 'refused', note: '3 playground pull requests are already open.' }).text, /already open/);
  assert.match(prLine({ state: 'unknown', find_url: 'https://github.com/himanshu748/memory-pr/pulls?q=x' }).link, /pulls\?q=x/);
  assert.match(prLine({ state: 'closed', number: 41, url }).text, /30 minutes/);
});

test('a request that came to nothing can be sent again', () => {
  assert.equal(again({ state: 'refused' }), true);
  assert.equal(again({ state: 'failed' }), true);
  assert.equal(again({ state: 'queued' }), false);
  assert.equal(again({ state: 'open', check: 'failure' }), false);
  assert.equal(again(undefined), false);
  assert.match(prLine({ state: 'failed' }).text, /try again/);
});
