import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runRelationshipControl } from '../../site/relationship-control.js';
import { importGraph, run } from '../../site/relate-runtime.js';
import { initSync, RelateDb } from '../../site/vendor/relate/relate.js';

const read = (path) => readFileSync(new URL(path, import.meta.url));
initSync({ module: read('../../site/vendor/relate/relate_bg.wasm') });
const { memory: graph, rules } = JSON.parse(read('../../site/data/demo.json'));
const replay = JSON.parse(read('../../demo/ab/relationship-control.json'));

test('both browser configurations reproduce the CLI replay on the shipped WASM engine', () => {
  const originalGraph = JSON.stringify(graph);
  for (const linked of [true, false, true]) {
    const actual = runRelationshipControl({ graph, rules, run, loadDb: (value) => importGraph(RelateDb, value), linked });
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

test('an import query error frees the database before the browser can use it', () => {
  let freed = false;
  class FailingRelateDb {
    execute() { return JSON.stringify({ error: 'Failed to import' }); }
    free() { freed = true; }
  }
  assert.throws(() => importGraph(FailingRelateDb, graph), /Failed to import/);
  assert.equal(freed, true);
});
