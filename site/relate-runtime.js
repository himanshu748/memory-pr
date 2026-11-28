export function run(db, query, params) {
  const out = JSON.parse(db.execute(query, params ? JSON.stringify(params) : null));
  if (out.error) throw new Error(out.error);
  return out;
}

function groupBy(items, key) {
  const groups = new Map();
  for (const item of items) {
    const k = key(item);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(item);
  }
  return groups;
}

/** Load an exported graph into a new in-memory Relate database, with Cypher. */
export function importGraph(RelateDb, graph) {
  const db = new RelateDb();
  try {
    for (const [label, nodes] of groupBy(graph.nodes, (n) => n.label)) {
      const rows = nodes.map((n) => (n.stub ? { ...n.props, stub: true } : n.props));
      run(db, `UNWIND $rows AS row CREATE (n:\`${label}\`) SET n = row`, { rows });
    }
    const label = (id) => id.split('/')[0];
    const name = (id) => id.slice(id.indexOf('/') + 1);
    const groups = groupBy(graph.rels, (r) => `${label(r.source)}|${r.type}|${label(r.target)}`);
    for (const [key, rels] of groups) {
      const [from, type, to] = key.split('|');
      run(
        db,
        `UNWIND $rows AS row MATCH (a:\`${from}\` {name: row.a}), (b:\`${to}\` {name: row.b}) ` +
          `CREATE (a)-[r:\`${type}\`]->(b) SET r = row.props`,
        { rows: rels.map((r) => ({ a: name(r.source), b: name(r.target), props: r.props })) },
      );
    }
    return db;
  } catch (error) {
    db.free();
    throw error;
  }
}
