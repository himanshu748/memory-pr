import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runRelationshipControl } from '../../site/relationship-control.js';
import { initSync, RelateDb } from '../../site/vendor/relate/relate.js';

const read = (path) => readFileSync(new URL(path, import.meta.url));
initSync({ module: read('../../site/vendor/relate/relate_bg.wasm') });
const { memory: graph, rules } = JSON.parse(read('../../site/data/demo.json'));
const replay = JSON.parse(read('../../demo/ab/relationship-control.json'));

function run(db, query, params) {
  const out = JSON.parse(db.execute(query, params ? JSON.stringify(params) : null));
  if (out.error) throw new Error(out.error);
  return out;
}

function loadDb({ nodes, rels }) {
  const db = new RelateDb();
  try {
    for (const { label, props, stub } of nodes) {
      run(db, `CREATE (n:${label}) SET n = $props`, { props: stub ? { ...props, stub: true } : props });
    }
    for (const { source, type, target, props } of rels) {
      const [a, from] = source.split('/');
      const [b, to] = target.split('/');
      run(db, `MATCH (a:${a} {name:$from}), (b:${b} {name:$to}) CREATE (a)-[r:${type}]->(b) SET r = $props`,
        { from, to, props });
    }
    return db;
  } catch (error) {
    db.free();
    throw error;
  }
}

test('both browser configurations reproduce the CLI replay on the shipped WASM engine', () => {
  const originalGraph = JSON.stringify(graph);
  for (const linked of [true, false, true]) {
    const actual = runRelationshipControl({ graph, rules, run, loadDb, linked });
    const expected = linked ? replay.linked : replay.detached;
    assert.deepEqual(actual, {
      keywordHits: expected.keyword_hits, restoreHops: expected.restore_drill_hops,
      blocked: expected.proposal_blocked, blockers: expected.blockers,
    });
  }
  assert.equal(JSON.stringify(graph), originalGraph);
});

test('a query error frees the private database and propagates', () => {
  let freed = false;
  assert.throws(() => runRelationshipControl({
    graph, rules, linked: false,
    loadDb: () => ({ free() { freed = true; } }),
    run() { throw new Error('Failed to query'); },
  }), /Failed to query/);
  assert.equal(freed, true);
});
