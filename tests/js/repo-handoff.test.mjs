import assert from 'node:assert/strict';
import { test } from 'node:test';
import { repoHandoff } from '../../site/repo-handoff.js';

const baseline = '1'.repeat(40);
const revision = '2'.repeat(40);
const fileAt = (sha) => `https://github.com/example/app/blob/${sha}/go.mod`;
const old = { id: 'previous', entity: 'api', predicate: 'go-version', value: '1.23', sha: baseline };
const head = { ...old, id: 'latest', value: '1.25', sha: revision };
const added = { ...head, id: 'new-module', predicate: 'go-module', value: 'example/app' };
const missing = { ...old, id: 'worker', entity: 'worker' };
const changed = {
  entity: 'api', predicate: 'go-version', old: old.value, new: head.value,
  old_url: fileAt(baseline), new_url: fileAt(revision),
};

function graph(facts, fixed = false) {
  const nodes = new Map();
  const rels = [];
  function node(label, name, props) {
    const id = `${label}/${name}`;
    nodes.set(id, { id, label, props: { name, ...props } });
    return id;
  }
  for (const f of facts) {
    const source = node('Fact', f.id, { status: 'active', ...f });
    rels.push({ source, type: 'ABOUT', target: node('Entity', f.entity) });
    rels.push({ source, type: 'SOURCE', target: node('Source', f.sha, { uri: fileAt(f.sha) }) });
    if (fixed && f === head) rels.push({ source, type: 'SUPERSEDES', target: 'Fact/previous' });
  }
  return { nodes: [...nodes.values()], rels };
}

function scan() {
  return {
    repo: 'example/app', old: { sha: baseline }, head: { sha: revision },
    files: { old: 3, head: 4 }, files_skipped: { old: 0, head: 0 },
    blocked: true, fixed: { blocked: false }, proposed: 2, learned: 1,
    changed: [changed], unconfirmed: [],
    before: graph([old]), after: graph([old, head, added]), fixed_after: graph([old, head, added], true),
  };
}

const unpack = (text) => JSON.parse(text.slice(text.indexOf('\n\n') + 2));
const addition = { entity: added.entity, predicate: added.predicate, current: { value: added.value, source: fileAt(revision) } };

test('a passing correction exports graph-backed replacements and additions', () => {
  const text = repoHandoff(scan(), true);
  assert.match(text, /may be normalized or abbreviated/);
  assert.deepEqual(unpack(text), {
    repository: 'example/app', baseline, revision, review: 'Passed', filesRead: { baseline: 3, revision: 4 },
    changes: [{
      entity: old.entity, predicate: old.predicate,
      previous: { value: old.value, source: fileAt(baseline) },
      current: { value: head.value, source: fileAt(revision) },
    }],
    additions: [addition], unconfirmed: [],
  });
});

test('an additions-only scan uses the verdict and graph of the original review', () => {
  const body = { ...scan(), blocked: false, after: graph([old, added]), changed: [], proposed: 1 };
  assert.deepEqual(unpack(repoHandoff(body, false)).additions, [addition]);
  assert.equal(repoHandoff({ ...body, blocked: true }, false), null);
  assert.equal(repoHandoff({ ...body, after: graph([old]), proposed: 0 }, false), null);
});

test('blocked reviews and inconsistent graph or summary evidence cannot produce a handoff', () => {
  const original = scan();
  assert.equal(repoHandoff(original, false), null);
  for (const alter of [
    (b) => { b.fixed.blocked = true; },
    (b) => { delete b.before; },
    (b) => { b.fixed_after = b.after; },
    (b) => { b.fixed_after.rels.find((e) => e.type === 'SUPERSEDES').target = 'Fact/new-module'; },
    (b) => { b.fixed_after.rels = b.fixed_after.rels.filter((e) => e.type !== 'ABOUT'); },
    (b) => { b.fixed_after.nodes.find((n) => n.id === 'Fact/new-module').props.status = 'retracted'; },
    (b) => { b.fixed_after.nodes.find((n) => n.id === 'Source/' + revision).props.uri = fileAt('main'); },
    (b) => { b.changed[0].new = 'unreviewed'; },
    (b) => { b.proposed = 3; },
  ]) {
    const body = structuredClone(original);
    alter(body);
    assert.equal(repoHandoff(body, true), null, String(alter));
  }
});

test('coverage and unconfirmed baseline facts survive a partial handoff', () => {
  const pending = { ...missing, status: 'unconfirmed' };
  const body = {
    ...scan(), learned: 2, files_skipped: { old: 0, head: 2 }, before: graph([old, missing]),
    fixed_after: graph([old, pending, head, added], true),
    unconfirmed: [{ ...missing, old: missing.value, old_url: fileAt(baseline), reason: 'source-unread' }],
  };
  const packet = unpack(repoHandoff(body, true));
  assert.match(packet.review, /Verification still needed/);
  assert.match(packet.coverage, /Latest: read 4 of 6/);
  assert.deepEqual(packet.additions, [addition]);
  assert.deepEqual(packet.unconfirmed, [{
    entity: 'worker', predicate: missing.predicate, previousSource: fileAt(baseline), reason: 'source-unread',
  }]);
  assert.equal(repoHandoff({ ...body, unconfirmed: [] }, true), null);
});
