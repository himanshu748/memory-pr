import assert from 'node:assert/strict';
import { test } from 'node:test';

import { again, prLine, safeUrl, settled, watchPullRequest, whyNot } from '../../site/github-pr.js';

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

test('an expired status watch keeps the GitHub link without inviting another dispatch', () => {
  const state = { state: 'status-timeout', url: 'https://github.com/himanshu748/memory-pr/pull/41' };
  assert.match(prLine(state).text, /stopped checking/);
  assert.equal(prLine(state).link, state.url);
  assert.equal(settled(state), true);
  assert.equal(again(state), false);
});

test('a stalled status read is aborted and the next read can finish the watch', async (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const final = { state: 'open', check: 'success' };
  const requests = [];
  t.mock.method(globalThis, 'fetch', (url, options) => {
    requests.push({ url, ...options });
    if (requests.length > 1) return Promise.resolve({ ok: true, json: async () => final });
    return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
  });
  const states = [];
  const watched = watchPullRequest('https://example.test', 'accepted id', (state) => states.push(state));
  t.mock.timers.tick(5_000);
  await new Promise(setImmediate);
  assert.equal(requests[0].url, 'https://example.test/api/pull-requests/accepted%20id');
  assert.equal(requests[0].method, undefined);
  t.mock.timers.tick(10_000);
  await new Promise(setImmediate);
  assert.equal(requests[0].signal.aborted, true);
  t.mock.timers.tick(5_000);
  await watched;
  assert.deepEqual(states, [final]);
});

test('an accepted request has a wall-clock limit while retaining its known proof link', async (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const url = 'https://github.com/himanshu748/memory-pr/pull/41';
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => ({ state: 'open', check: 'pending', url }) }));
  const states = [];
  const watched = watchPullRequest('https://example.test', 'id', (state) => states.push(state));
  for (let tick = 0; tick < 60; tick++) {
    t.mock.timers.tick(5_000);
    await new Promise(setImmediate);
  }
  await watched;
  assert.equal(states.length, 60);
  assert.deepEqual(states.at(-1), { state: 'status-timeout', url });
});
