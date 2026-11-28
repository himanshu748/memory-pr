import { LEXICAL, NEIGHBORHOOD } from './search.js';

const BACKUP = 'payments-api--backup-job--nightly-pg-dump';
const RESTORE = 'payments-api--restore-drill--quarterly-pg-restore';

const rowKey = (row) => JSON.stringify(Object.keys(row).sort().map((key) => [key, row[key]]));

/** Observe recall, then propose the same retraction on a fresh copy of the vault. */
export function runRelationshipControl({ graph, rules, run, loadDb, linked }) {
  if (typeof linked !== 'boolean') throw new TypeError('Choose a linked or detached graph.');
  const isEdge = (r) => r.source === 'Fact/' + RESTORE && r.type === 'DERIVED_FROM' && r.target === 'Fact/' + BACKUP;
  const otherEdges = graph.rels.filter((r) => !isEdge(r));
  if (graph.rels.length - otherEdges.length !== 1) throw new Error('Expected one restore-drill dependency in the vault.');

  const db = loadDb(linked ? graph : { ...graph, rels: otherEdges });
  try {
    const hits = run(db, LEXICAL, { q: 'postgres' }).rows;
    const names = hits.map((hit) => hit.fact);
    const neighbors = names.length ? run(db, NEIGHBORHOOD, { names }).rows : [];
    const baseline = new Map(rules.map((r) => [r.id, new Set(run(db, r.cypher).rows.map(rowKey))]));
    const updated = run(db,
      "MATCH (f:Fact {name: $name, status: 'active'}) SET f.status = 'retracted' RETURN f.name AS fact",
      { name: BACKUP });
    if (updated.rows.length !== 1) throw new Error('Expected one active backup job to retract.');

    const blockers = [];
    for (const rule of rules) {
      for (const row of run(db, rule.cypher).rows) {
        if (rule.severity === 'block' && !baseline.get(rule.id).has(rowKey(row))) {
          blockers.push({ rule: rule.id, fact: row.fact, path: row.path });
        }
      }
    }
    return {
      keywordHits: hits.map(({ fact, value }) => ({ fact, value })),
      restoreHops: neighbors.find((row) => row.fact === RESTORE)?.hops ?? null,
      blocked: blockers.length > 0,
      blockers,
    };
  } finally {
    db.free();
  }
}
