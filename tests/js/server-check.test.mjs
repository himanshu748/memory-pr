import assert from 'node:assert/strict';
import { test } from 'node:test';

import { apiBase, compare, fetchServerReview, MAX_MERGED, serverLine, serverRequest } from '../../site/server-check.js';

const draft = (value) => ({ agent: 'visitor', entity: 'payments-api', predicate: 'database', value, source: 'docs/design.md' });

test('the server is used on Vercel and from the Pages mirror, and nowhere else', () => {
  assert.equal(apiBase('memory-pr.vercel.app'), '');
  assert.equal(apiBase('himanshu748.github.io'), 'https://memory-pr.vercel.app');
  assert.equal(apiBase('localhost'), null);
  assert.equal(apiBase('localhost', '?api=1'), '');
  assert.equal(apiBase('vercel.app.example.com'), null);
});

test('merged drafts are replayed in order before the new one', () => {
  const landed = [{ draft: draft('a'), edited: false }, { draft: draft('b'), edited: false }];
  assert.deepEqual(serverRequest(landed, draft('c')), { body: { merged: [draft('a'), draft('b')], draft: draft('c') } });
  assert.deepEqual(serverRequest([], draft('c')), { body: { merged: [], draft: draft('c') } });
});

test('a merge that was changed in the Cypher console cannot be replayed, so nothing is sent', () => {
  const landed = [{ draft: draft('a'), edited: false }, { draft: draft('b'), edited: true }];
  const request = serverRequest(landed, draft('c'));
  assert.deepEqual(request, { skip: 'merged-edit' });
  const line = serverLine({ state: 'skipped' }, false);
  assert.match(line.text, /^Server check skipped\./);
  assert.match(line.text, /Reset memory/);
  assert.doesNotMatch(line.text, /agrees|differs/);
});

test('more merged drafts than the server replays is skipped, not sent to a refusal', () => {
  const landed = Array.from({ length: MAX_MERGED + 1 }, () => ({ draft: draft('x'), edited: false }));
  const request = serverRequest(landed, draft('c'));
  assert.deepEqual(request, { skip: 'too-many-merges' });
  const line = serverLine({ state: 'skipped', why: request.skip }, false);
  assert.match(line.text, /^Server check skipped\./);
  assert.match(line.text, /more than 12 pull requests/);
  assert.match(line.text, /Reset memory/);
  // At the cap the request still goes out: the cutoff is exactly one merge later.
  assert.equal(serverRequest(landed.slice(0, MAX_MERGED), draft('c')).skip, undefined);
});

test('the answer is compared on the verdict and the set of blocking rules', () => {
  const mine = { blocked: true, rules: ['contradiction', 'contradiction', 'stale-derivation'] };
  assert.equal(compare(mine, { blocked: true, rules: ['stale-derivation', 'contradiction'], elapsed_ms: 57.7 }).state, 'agrees');
  assert.equal(compare(mine, { blocked: true, rules: ['contradiction'], elapsed_ms: 1 }).state, 'differs');
  assert.equal(compare(mine, { blocked: false, rules: [], elapsed_ms: 1 }).state, 'differs');
  assert.equal(compare({ blocked: false, rules: [] }, { blocked: false, rules: [] }).state, 'agrees');
  assert.throws(() => compare(mine, { error: 'nope' }));
  assert.throws(() => compare(mine, null));
});

test('the line says what happened, and nothing when there is no server', () => {
  assert.equal(serverLine(undefined, false), null);
  assert.equal(serverLine({ state: 'off' }, false), null);
  assert.equal(serverLine({ state: 'checking' }, false).tone, '');
  const agrees = serverLine({ state: 'agrees', blocked: true, rules: ['contradiction'], ms: 57.7 }, false);
  assert.deepEqual([agrees.tone, /in 58 ms/.test(agrees.text)], ['agrees', true]);
  const differs = serverLine({ state: 'differs', blocked: true, rules: ['contradiction'], ms: 5 }, false);
  assert.match(differs.text, /says blocked by contradiction/);
  assert.match(serverLine({ state: 'differs', blocked: false, rules: [], ms: 5 }, false).text, /says passed/);
});

test('a pull request changed in the console after the check shows no verdict from the server', () => {
  const line = serverLine({ state: 'agrees', blocked: true, rules: ['contradiction'], ms: 5 }, true);
  assert.equal(line.tone, '');
  assert.match(line.text, /^Server check not shown\./);
});

test('an unavailable server check identifies the browser verdict', () => {
  const line = serverLine({ state: 'unavailable' }, false);
  assert.equal(line.tone, '');
  assert.match(line.text, /^Server check unavailable\./);
  assert.match(line.text, /review in this browser/);
});

test('a stalled server request settles as unavailable at the deadline', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const request = { draft: draft('x'), merged: [] };
  const calls = [];
  t.mock.method(globalThis, 'fetch', (url, init) => {
    calls.push({ url, init });
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
    });
  });
  const result = fetchServerReview('', request, { blocked: false, rules: [] });
  assert.equal(calls.length, 1);
  const [{ url, init }] = calls;
  assert.equal(url, '/api/review');
  assert.deepEqual(JSON.parse(init.body), request);
  t.mock.timers.tick(14_999);
  assert.equal(init.signal.aborted, false);
  t.mock.timers.tick(1);
  assert.equal(init.signal.aborted, true);
  assert.deepEqual(await result, { state: 'unavailable' });
});

test('a completed server check still compares the two verdicts', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, json: async () => ({
    blocked: false, rules: [], elapsed_ms: 25,
  }) }));
  assert.deepEqual(await fetchServerReview('', { draft: draft('x'), merged: [] }, { blocked: false, rules: [] }),
    { state: 'agrees', blocked: false, rules: [], ms: 25 });
});
