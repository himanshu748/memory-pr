// Hybrid retrieval in the page. The text is the same as LEXICAL and NEIGHBORHOOD
// in src/mempr/search.py; tests/test_search.py fails if the two drift.
// Keyword match, then facts up to 6 hops along DERIVED_FROM. No model, no key.

export const LEXICAL = `MATCH (hit:Fact)-[:ABOUT]->(e:Entity)
WHERE hit.status = 'active'
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(hit) WHERE n.status = 'active' }
OPTIONAL MATCH (hit)-[:SOURCE]->(src:Source)
WITH hit, e, collect(DISTINCT src.uri) AS sources
WHERE toLower(e.name) CONTAINS toLower($q)
   OR toLower(hit.predicate) CONTAINS toLower($q)
   OR toLower(coalesce(hit.value, '')) CONTAINS toLower($q)
   OR toLower(coalesce(hit.body, '')) CONTAINS toLower($q)
   OR any(u IN sources WHERE toLower(coalesce(u, '')) CONTAINS toLower($q))
RETURN hit.name AS fact, e.name AS entity, hit.predicate AS predicate,
       hit.value AS value, sources
ORDER BY entity, predicate, value`;

export const NEIGHBORHOOD = `MATCH (hit:Fact) WHERE hit.name IN $names
MATCH p = (child:Fact)-[:DERIVED_FROM*1..6]->(hit)
WHERE child.status = 'active'
  AND NOT child.name IN $names
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(child) WHERE n.status = 'active' }
OPTIONAL MATCH (child)-[:ABOUT]->(e:Entity)
RETURN DISTINCT child.name AS fact, e.name AS entity, child.predicate AS predicate,
       child.value AS value, length(p) AS hops, 'follows' AS via
UNION
MATCH (hit:Fact) WHERE hit.name IN $names
MATCH p = (hit)-[:DERIVED_FROM*1..6]->(root:Fact)
WHERE root.status = 'active'
  AND NOT root.name IN $names
  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(root) WHERE n.status = 'active' }
OPTIONAL MATCH (root)-[:ABOUT]->(e:Entity)
RETURN DISTINCT root.name AS fact, e.name AS entity, root.predicate AS predicate,
       root.value AS value, length(p) AS hops, 'supports' AS via`;
