// memory-pr browser view. No build step: plain ES module, Cytoscape for drawing, and
// Relate (WebAssembly) holding each graph so the visitor's Cypher runs on it live.

const REPO = 'https://github.com/himanshu748/memory-pr';
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const md = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const relKey = (r) => `${r.source}|${r.type}|${r.target}`;
const NODE_ID = /[A-Za-z][A-Za-z0-9_]*\/[A-Za-z0-9._-]+/g;

let data;
let relate = null; // the Relate WebAssembly module, or null when it could not start
let cy;
let views = [];
let view;
let problem = null; // the problem drawn on the graph
let nodeId = null; // the node shown in the detail card
const positions = new Map();

// ---------------------------------------------------------------- Relate

function run(db, query, params) {
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
function loadDb(graph) {
  const db = new relate.RelateDb();
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
}

/** Read the graph back out of Relate, so the drawing is what the database holds. */
function readGraph(db, source) {
  const meta = new Map(source.nodes.map((n) => [n.id, n]));
  const nodes = run(db, 'MATCH (n) RETURN labels(n)[0] AS label, properties(n) AS props').rows.map((row) => {
    const { stub, ...props } = row.props;
    const id = `${row.label}/${props.name}`;
    const known = meta.get(id) || {};
    return { id, label: row.label, props, stub: stub === true, file: known.file, text: known.text, issues: known.issues };
  });
  const rels = run(
    db,
    'MATCH (a)-[r]->(b) RETURN labels(a)[0] AS al, a.name AS an, type(r) AS type, ' +
      'labels(b)[0] AS bl, b.name AS bn, properties(r) AS props',
  ).rows.map((row) => ({ source: `${row.al}/${row.an}`, type: row.type, target: `${row.bl}/${row.bn}`, props: row.props }));
  const order = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  nodes.sort((a, b) => order(a.id, b.id));
  rels.sort((a, b) => order(relKey(a), relKey(b)));
  return { nodes, rels };
}

const rowKey = (row) => JSON.stringify(Object.keys(row).sort().map((k) => [k, row[k]]));

function ruleRows(db) {
  const found = new Map();
  for (const rule of data.rules) {
    try {
      found.set(rule.id, run(db, rule.cypher).rows);
    } catch {
      found.set(rule.id, []);
    }
  }
  return found;
}

/** The node ids a row names, as a bare note name or as `Label/name` inside a path. */
function idsIn(values, graph) {
  const known = new Set(graph.nodes.map((n) => n.id));
  const ids = new Set();
  for (const value of values) {
    if (typeof value !== 'string') continue;
    for (const n of graph.nodes) if (n.props.name === value) ids.add(n.id);
    for (const m of value.match(NODE_ID) || []) if (known.has(m)) ids.add(m);
  }
  return ids;
}

function withContext(ids, graph) {
  // What replaced a fact is part of why it is a problem, so draw it too.
  for (const r of graph.rels) if (r.type === 'SUPERSEDES' && ids.has(r.target)) ids.add(r.source);
  const rels = graph.rels.filter((r) => ids.has(r.source) && ids.has(r.target));
  return { nodes: [...ids], rels: rels.map((r) => [r.source, r.type, r.target]) };
}

/** Run every review rule in Relate and keep what the base graph did not already have. */
function liveFindings(v) {
  const found = [];
  const now = ruleRows(v.db);
  for (const rule of data.rules) {
    for (const row of now.get(rule.id)) {
      if (v.baseline.get(rule.id).has(rowKey(row))) continue;
      found.push({
        kind: 'finding',
        severity: rule.severity,
        rule: rule.id,
        title: rule.title,
        text: rule.message.replace(/\{(\w+)\}/g, (_, k) => row[k]),
        fix: rule.fix,
        row,
        ...withContext(idsIn(Object.values(row), v.graph), v.graph),
      });
    }
  }
  return found;
}

function openView(v) {
  if (v.db || !relate) return;
  v.db = loadDb(v.after);
  const base = loadDb(v.before);
  const rows = ruleRows(base);
  base.free();
  // On the memory itself every finding counts; on a pull request only the new ones do.
  v.baseline = new Map([...rows].map(([id, r]) => [id, new Set(v.pull ? r.map(rowKey) : [])]));
  refresh(v);
}

function refresh(v) {
  v.graph = readGraph(v.db, v.after);
  v.live = liveFindings(v);
}

function problemsOf(v) {
  const exported = v.pull ? v.pull.problems : [];
  if (!v.live) return exported;
  return [...exported.filter((p) => p.kind !== 'finding'), ...v.live];
}

// ---------------------------------------------------------------- drawing

const STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(label)', color: '#e8edf7', 'font-size': 10, 'font-family': 'Inter, system-ui, sans-serif',
      'text-wrap': 'wrap', 'text-valign': 'center', 'text-halign': 'center', 'line-height': 1.25,
      'background-color': '#2c3b5e', 'border-width': 1.5, 'border-color': '#56689a',
      width: 'data(w)', height: 'data(h)', shape: 'round-rectangle',
      'overlay-opacity': 0, 'transition-property': 'opacity', 'transition-duration': '150ms',
    },
  },
  { selector: 'node[kind = "Fact"]', style: { 'background-color': '#1f3a68', 'border-color': '#5b9dff' } },
  { selector: 'node.old', style: { 'background-color': '#2a2f3c', 'border-color': '#6b7385', color: '#9aa3b5', 'border-style': 'dashed' } },
  {
    selector: 'node[kind = "Entity"]',
    style: { shape: 'ellipse', 'background-color': '#f2a23a', 'border-color': '#ffd08a', color: '#1b1405', 'font-size': 12, 'font-weight': 700 },
  },
  { selector: 'node[kind = "Agent"]', style: { shape: 'ellipse', 'background-color': '#6d4bd6', 'border-color': '#a994f5' } },
  { selector: 'node[kind = "Source"]', style: { shape: 'round-tag', 'background-color': '#22524b', 'border-color': '#4fc3ae', 'font-size': 9 } },
  { selector: 'node[kind = "Predicate"]', style: { shape: 'round-diamond', 'background-color': '#3a3f52', 'border-color': '#8a91a8', 'font-size': 9 } },
  { selector: 'node.stub', style: { 'background-color': '#12151d', 'border-style': 'dotted', 'border-color': '#ff6b6b', color: '#ff9c9c' } },
  { selector: 'node.added', style: { 'border-color': '#3ddc84', 'border-width': 4 } },
  { selector: 'node.changed', style: { 'border-color': '#ffcf4d', 'border-width': 4 } },
  { selector: 'node.removed', style: { 'border-color': '#ff6b6b', 'border-width': 3, 'border-style': 'dashed', opacity: 0.6 } },
  {
    selector: 'edge',
    style: {
      width: 1.2, 'line-color': '#46506a', 'target-arrow-color': '#46506a', 'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8, 'curve-style': 'bezier', 'font-size': 8, color: '#aab3c8',
      'text-background-color': '#0d1220', 'text-background-opacity': 0.9, 'text-background-padding': 2,
      'text-rotation': 'autorotate', 'overlay-opacity': 0,
    },
  },
  { selector: 'edge[type = "SOURCE"], edge[type = "ASSERTED_BY"]', style: { 'line-style': 'dotted', 'line-color': '#394259', 'target-arrow-color': '#394259', width: 1 } },
  { selector: 'edge[type = "DERIVED_FROM"]', style: { 'line-color': '#ff9f43', 'target-arrow-color': '#ff9f43', width: 2.4, label: 'rests on' } },
  { selector: 'edge[type = "SUPERSEDES"]', style: { 'line-color': '#c77dff', 'target-arrow-color': '#c77dff', width: 2.4, label: 'replaces' } },
  { selector: 'edge.added', style: { 'line-color': '#3ddc84', 'target-arrow-color': '#3ddc84', width: 2.6 } },
  { selector: 'edge.removed', style: { 'line-color': '#ff6b6b', 'target-arrow-color': '#ff6b6b', 'line-style': 'dashed', width: 2, label: 'data(lost)' } },
  { selector: '.hidden', style: { display: 'none' } },
  { selector: '.dim', style: { opacity: 0.25 } },
  { selector: 'node.hit', style: { 'underlay-color': '#5b9dff', 'underlay-opacity': 0.45, 'underlay-padding': 7 } },
  { selector: 'node.hl', style: { 'underlay-color': '#ff4d6d', 'underlay-opacity': 0.5, 'underlay-padding': 8, opacity: 1 } },
  { selector: 'edge.hl', style: { width: 4, opacity: 1, label: 'data(name)', 'font-size': 9, color: '#fff', 'z-index': 9 } },
  { selector: 'node:selected', style: { 'underlay-color': '#ffffff', 'underlay-opacity': 0.35, 'underlay-padding': 6 } },
];

const EDGE_NAME = { ABOUT: 'about', SOURCE: 'source', ASSERTED_BY: 'written by', DERIVED_FROM: 'rests on', SUPERSEDES: 'replaces' };

/** Before and after in one graph: every node and link with what happened to it. */
function union(v) {
  const before = new Map(v.before.nodes.map((n) => [n.id, n]));
  const after = new Map(v.graph.nodes.map((n) => [n.id, n]));
  const nodes = new Map();
  for (const [id, n] of after) {
    const old = before.get(id);
    const same = old && JSON.stringify(old.props) === JSON.stringify(n.props) && !!old.stub === !!n.stub;
    nodes.set(id, { ...n, state: !old ? 'added' : same ? 'same' : 'changed', was: old });
  }
  for (const [id, n] of before) if (!after.has(id)) nodes.set(id, { ...n, state: 'removed' });
  const had = new Set(v.before.rels.map(relKey));
  const has = new Set(v.graph.rels.map(relKey));
  const rels = v.graph.rels.map((r) => ({ ...r, state: had.has(relKey(r)) ? 'same' : 'added' }));
  for (const r of v.before.rels) if (!has.has(relKey(r))) rels.push({ ...r, state: 'removed' });
  const replacedBy = new Map();
  for (const r of rels) {
    if (r.type !== 'SUPERSEDES' || r.state === 'removed') continue;
    if (nodes.get(r.source)?.props.status === 'active') replacedBy.set(r.target, r.source);
  }
  for (const n of nodes.values()) {
    n.replacedBy = replacedBy.get(n.id);
    n.empty = n.label === 'Fact' && !n.stub && n.props.predicate === undefined;
    n.old = n.label === 'Fact' && (n.replacedBy || n.props.status === 'retracted');
  }
  return { nodes, rels };
}

function nodeLabel(n) {
  if (n.label === 'Fact') {
    if (n.empty) return `${n.props.name}\n(empty note)`;
    if (n.stub) return `${n.props.name}\n(no note)`;
    return `${n.props.predicate}\n${n.props.value}`;
  }
  if (n.label === 'Source') {
    const text = n.props.uri ? n.props.uri.split('/').slice(-2).join('/') : n.props.name;
    return n.stub ? `${text}\n(no note)` : text;
  }
  return n.stub ? `${n.props.name}\n(no note)` : n.props.name;
}

function draw({ relayout = false } = {}) {
  const u = (view.union = union(view));
  const elements = [];
  for (const n of u.nodes.values()) {
    const label = nodeLabel(n);
    const lines = label.split('\n');
    const wide = Math.max(...lines.map((l) => l.length));
    const round = n.label === 'Entity' || n.label === 'Agent';
    elements.push({
      group: 'nodes',
      data: {
        id: n.id, label, kind: n.label,
        w: round ? Math.max(58, wide * 7 + 18) : wide * 6.2 + 18,
        h: round ? Math.max(58, wide * 3.2 + 18) : lines.length * 13 + 12,
      },
      classes: [n.state, n.old ? 'old' : '', n.stub ? 'stub' : ''].join(' '),
    });
  }
  for (const r of u.rels) {
    if (!u.nodes.has(r.source) || !u.nodes.has(r.target)) continue;
    const name = EDGE_NAME[r.type] || r.type.toLowerCase();
    elements.push({
      group: 'edges',
      data: { id: relKey(r), source: r.source, target: r.target, type: r.type, name, lost: `${name} (lost)` },
      classes: r.state,
    });
  }
  cy.elements().remove();
  cy.add(elements);
  layout(relayout);
  paint();
}

function layout(relayout) {
  const fresh = cy.nodes().filter((n) => !positions.has(n.id()));
  if (relayout || fresh.length === cy.nodes().length) {
    // Start from a circle in a fixed order, so the same memory always lays out the same way.
    const all = cy.nodes();
    all.forEach((n, i) => {
      const a = (2 * Math.PI * i) / all.length;
      n.position({ x: 550 + 300 * Math.cos(a), y: 380 + 300 * Math.sin(a) });
    });
    cy.layout({
      name: 'cose', animate: false, randomize: false, fit: false, padding: 30,
      nodeRepulsion: () => 26000, idealEdgeLength: (e) => (e.data('type') === 'ABOUT' ? 70 : 110),
      edgeElasticity: (e) => (e.data('type') === 'ABOUT' ? 200 : 40),
      nodeOverlap: 30, gravity: 0.35, numIter: 2500, componentSpacing: 60,
      boundingBox: { x1: 0, y1: 0, w: 1100, h: 760 },
    }).run();
  } else {
    cy.nodes().forEach((n) => {
      if (positions.has(n.id())) n.position(positions.get(n.id()));
    });
    // A new node goes next to what it links to, on the side away from the middle.
    const box = cy.nodes().difference(fresh).boundingBox();
    const mid = { x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 };
    fresh.forEach((n, i) => {
      const near = n.neighborhood('node').filter((m) => positions.has(m.id()));
      const anchor = near.filter('[kind = "Entity"], [kind = "Fact"]')[0] || near[0];
      if (!anchor) return n.position({ x: box.x1 + 60 + i * 90, y: box.y2 + 60 });
      const p = anchor.position();
      const len = Math.hypot(p.x - mid.x, p.y - mid.y) || 1;
      const turn = (i % 2 ? 1 : -1) * 0.5 * Math.ceil(i / 2);
      const dx = (p.x - mid.x) / len, dy = (p.y - mid.y) / len;
      n.position({
        x: p.x + 105 * (dx * Math.cos(turn) - dy * Math.sin(turn)),
        y: p.y + 105 * (dx * Math.sin(turn) + dy * Math.cos(turn)),
      });
    });
  }
  // Only what the memory itself holds keeps its place; a pull request's notes are placed each time.
  if (!view.pull) cy.nodes().forEach((n) => positions.set(n.id(), { ...n.position() }));
}

function paint({ fit = true } = {}) {
  const showProv = $('show-prov').checked;
  const focus = new Set(problem ? problem.nodes : []);
  const focusRels = new Set(problem ? problem.rels.map((r) => r.join('|')) : []);
  cy.batch(() => {
    cy.elements().removeClass('dim hl hidden');
    cy.nodes().forEach((n) => {
      const prov = n.data('kind') === 'Agent' || n.data('kind') === 'Source';
      const changed = n.hasClass('added') || n.hasClass('removed') || n.hasClass('stub');
      if (prov && !showProv && !focus.has(n.id()) && !changed && n.id() !== nodeId) n.addClass('hidden');
      if (problem) n.addClass(focus.has(n.id()) ? 'hl' : 'dim');
    });
    cy.edges().forEach((e) => {
      if (problem) e.addClass(focusRels.has(e.id()) ? 'hl' : 'dim');
    });
  });
  if (!fit) return;
  const target = problem ? cy.elements('.hl') : cy.elements(':visible');
  const eles = target.length ? target : cy.elements();
  cy.fit(eles, problem ? 70 : 24);
  if (cy.zoom() > 1.3) {
    cy.zoom(1.3);
    cy.center(eles);
  }
}

// ---------------------------------------------------------------- panels

function count(triples) {
  const kinds = { node: 0, rel: 0, prop: 0 };
  for (const t of triples) kinds[t[0]] += 1;
  const parts = [];
  if (kinds.node) parts.push(plural(kinds.node, 'note', 'notes'));
  if (kinds.rel) parts.push(plural(kinds.rel, 'link', 'links'));
  if (kinds.prop) parts.push(plural(kinds.prop, 'property', 'properties'));
  return parts.join(', ') || 'nothing';
}

const lit = (s) => `'${String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;

/** Cypher the visitor can run on the merged graph to repair a finding. */
function fixesFor(p) {
  const row = p.row;
  if (!row) return [];
  const retract = (name) =>
    `MATCH (f:Fact {name: ${lit(name)}})\nSET f.status = 'retracted'\nRETURN f.name AS fact, f.status AS status`;
  if (p.rule === 'contradiction') {
    const added = (name) => view.union.nodes.get(`Fact/${name}`)?.state === 'added';
    const [fresh, old] = added(row.other) && !added(row.fact) ? [row.other, row.fact] : [row.fact, row.other];
    return [
      ['Retract the new fact', retract(fresh)],
      [
        'Say the new fact replaces the old one',
        `MATCH (new:Fact {name: ${lit(fresh)}}), (old:Fact {name: ${lit(old)}})\n` +
          'CREATE (new)-[:SUPERSEDES]->(old)\nRETURN new.name AS replaces, old.name AS replaced',
      ],
    ];
  }
  if (p.rule === 'stale-derivation' || p.rule === 'unsourced-fact') return [['Retract this fact', retract(row.fact)]];
  if (p.rule === 'dangling-link') {
    const [label, ...rest] = row.target.split('/');
    return [
      [
        'Remove the link',
        `MATCH (a)-[r:\`${row.type}\`]->(b:\`${label}\` {name: ${lit(rest.join('/'))}})\nWHERE b.stub = true\nDELETE r`,
      ],
    ];
  }
  return [];
}

function renderViews() {
  $('views').innerHTML = views
    .map((v, i) => {
      const state = !v.pull ? '' : verdict(v).blocked ? 'blocked' : 'passed';
      const name = v.pull ? `<span class="dot ${state}"></span>${esc(v.short)}` : 'Shared memory';
      const sub = v.pull ? (state === 'blocked' ? 'blocked' : 'passes') : 'main';
      return `<button type="button" data-view="${i}" class="${v === view ? 'on' : ''}">${name}<small>${sub}</small></button>`;
    })
    .join('');
}

function verdict(v) {
  const list = problemsOf(v);
  const blocking = list.filter((p) => p.severity === 'block');
  return { list, blocked: blocking.length > 0, blocking: blocking.length };
}

function renderReview() {
  const v = view;
  const { list, blocked, blocking } = verdict(v);
  const parts = [];
  if (!v.pull) {
    parts.push(
      '<h2>The shared memory on <code>main</code></h2>',
      `<p>${plural(v.graph.nodes.length, 'note', 'notes')} and ${plural(v.graph.rels.length, 'link', 'links')}, ` +
        `read from the Markdown files in <a href="${REPO}/tree/main/memory"><code>memory/</code></a>. ` +
        'Every fact links to what it is about, the file it came from, and the agent that wrote it. ' +
        'Click a node to see where a fact comes from.</p>',
    );
    if (v.edited) parts.push(liveLine(v, list));
    if (list.length) parts.push(problemList(list));
    parts.push(
      '<h3>What a pull request does to it</h3><div class="pulls">',
      ...views.filter((x) => x.pull).map((x) => {
        const s = verdict(x);
        return (
          `<button type="button" class="pull" data-view="${views.indexOf(x)}">` +
          `<span class="tag ${s.blocked ? 'blocked' : 'passed'}">${s.blocked ? `blocked: ${plural(s.blocking, 'problem', 'problems')}` : 'passes'}</span>` +
          `<strong>${esc(x.title)}</strong><span>${esc(x.pull.author)} ${esc(x.pull.description)}</span></button>`
        );
      }),
      '</div>',
    );
  } else {
    const r = v.pull.review;
    parts.push(
      `<div class="verdict ${blocked ? 'blocked' : 'passed'}">${
        blocked ? `Merge blocked: ${plural(blocking, 'problem', 'problems')}` : 'Review passed: safe to merge'
      }</div>`,
      `<h2>${esc(v.title)}</h2>`,
      `<p class="meta"><strong>${esc(v.pull.author)}</strong> ${esc(v.pull.description)}. Branch <code>${esc(v.pull.id)}</code>.</p>`,
      `<p class="delta"><span class="plus">+ ${count(r.added)}</span><span class="minus">− ${count(r.removed)}</span></p>`,
    );
    if (list.length) parts.push(problemList(list));
    else parts.push('<p class="ok">Nothing contradicts, nothing is lost, every fact has a source.</p>');
    if (v.live) parts.push(liveLine(v, list));
    parts.push(
      '<details><summary>The comment the check posts on the pull request</summary>' +
        `<pre>${esc(v.pull.comment.replace('<!-- mempr-review -->\n', ''))}</pre></details>`,
    );
  }
  $('review').innerHTML = parts.join('');
}

function liveLine(v, list) {
  if (v.edited) {
    return (
      `<p class="live edited">You changed this graph. Relate re-ran the ${data.rules.length} review rules: ` +
      `${list.length ? plural(list.length, 'problem', 'problems') + ' now' : 'no problems now'}.</p>`
    );
  }
  const exported = v.pull.problems.filter((p) => p.kind === 'finding').length;
  const same = exported === v.live.length;
  return (
    `<p class="live ${same ? '' : 'edited'}">Relate ran the ${data.rules.length} review rules in this page ` +
    `${same ? 'and got the same result as' : `and found ${v.live.length}, against ${exported} from`} <code>mempr check</code>.</p>`
  );
}

function problemList(list) {
  return (
    '<ol class="problems">' +
    list
      .map((p, i) => {
        const fixes = relate ? fixesFor(p) : [];
        const actions = [
          p.rule ? `<button type="button" data-rule="${esc(p.rule)}">Run the rule</button>` : '',
          ...fixes.map(([label], f) => `<button type="button" data-fix="${i}:${f}">${esc(label)}</button>`),
        ].join('');
        return (
          `<li class="problem ${p.severity} ${p === problem ? 'on' : ''}" data-problem="${i}" tabindex="0">` +
          `<h4>${esc(p.title)}${p.severity === 'warn' ? ' <em>warning</em>' : ''}</h4>` +
          `<p>${md(p.text)}</p>${p.fix ? `<p class="fix">Fix: ${md(p.fix)}</p>` : ''}` +
          (actions ? `<div class="actions">${actions}</div>` : '') +
          '</li>'
        );
      })
      .join('') +
    '</ol>'
  );
}

function link(id) {
  const n = view.union.nodes.get(id);
  const text = !n ? id : n.label === 'Fact' && !n.empty && !n.stub ? `${n.props.predicate} = ${n.props.value}` : n.props.uri || n.props.name;
  return `<button type="button" class="chip" data-node="${esc(id)}">${esc(text)}</button>`;
}

function renderDetail() {
  const el = $('detail');
  const n = nodeId && view.union.nodes.get(nodeId);
  el.hidden = !n;
  if (!n) return;
  const out = (type) => view.union.rels.filter((r) => r.source === n.id && r.type === type);
  const inc = (type) => view.union.rels.filter((r) => r.target === n.id && r.type === type);
  const list = (rels, end) =>
    rels.map((r) => link(r[end]) + (r.state === 'same' ? '' : `<span class="state ${r.state}">${r.state === 'added' ? 'new' : 'lost'}</span>`)).join(' ');
  const rows = [];
  const row = (name, html) => html && rows.push(`<dt>${name}</dt><dd>${html}</dd>`);
  const states = { added: 'added by this pull request', removed: 'gone after the merge', changed: 'changed by this pull request' };
  const tags = [];
  if (states[n.state]) tags.push(`<span class="state ${n.state}">${states[n.state]}</span>`);
  if (n.stub) tags.push('<span class="state removed">linked to, but there is no note</span>');
  let title = esc(n.props.name);
  if (n.label === 'Fact' && !n.stub) {
    const about = out('ABOUT').map((r) => r.target.split('/')[1]).join(', ');
    if (n.empty) {
      tags.push('<span class="state removed">Relate reads this note as empty</span>');
    } else {
      title = `${esc(about)}: ${esc(n.props.predicate)} = ${esc(n.props.value)}`;
      tags.push(
        n.props.status === 'retracted'
          ? '<span class="state old">retracted</span>'
          : n.replacedBy
            ? '<span class="state old">replaced</span>'
            : '<span class="state current">current</span>',
      );
    }
    row('Statement', n.props.body ? esc(n.props.body) : '');
    row('About', list(out('ABOUT'), 'target'));
    row('Source', list(out('SOURCE'), 'target') || '<span class="state removed">no source</span>');
    row('Written by', list(out('ASSERTED_BY'), 'target') || '<span class="state removed">no author</span>');
    row('Replaces', list(out('SUPERSEDES'), 'target'));
    row('Replaced by', list(inc('SUPERSEDES'), 'source'));
    row('Rests on', list(out('DERIVED_FROM'), 'target'));
    row('Facts resting on it', list(inc('DERIVED_FROM'), 'source'));
  } else if (n.label === 'Entity') {
    row('Facts', list(inc('ABOUT'), 'source'));
  } else if (n.label === 'Agent') {
    row('Wrote', list(inc('ASSERTED_BY'), 'source'));
  } else if (n.label === 'Source') {
    if (n.props.uri) row('File', `<a href="${REPO}/blob/main/${esc(n.props.uri)}">${esc(n.props.uri)}</a>`);
    row('Facts from it', list(inc('SOURCE'), 'source'));
  }
  for (const [key, value] of Object.entries(n.props)) {
    if (['name', 'body', 'predicate', 'value', 'status', 'uri', 'type'].includes(key)) continue;
    const was = n.was && JSON.stringify(n.was.props[key]) !== JSON.stringify(value);
    row(esc(key), esc(typeof value === 'string' ? value : JSON.stringify(value)) + (was ? ' <span class="state changed">changed</span>' : ''));
  }
  for (const issue of n.issues || []) row('Problem', md(issue));
  const file = n.file
    ? `<details><summary>The note: <code>${esc(n.file)}</code></summary><pre>${esc(n.text)}</pre></details>`
    : '';
  el.innerHTML =
    `<button type="button" class="close" data-close aria-label="Close">×</button>` +
    `<p class="kind">${esc(n.label)}</p><h2>${title}</h2><p class="tags">${tags.join(' ')}</p>` +
    `<dl>${rows.join('')}</dl>${file}`;
}

// ---------------------------------------------------------------- console

const EXAMPLES = [
  [
    'Current facts about payments-api',
    "MATCH (f:Fact)-[:ABOUT]->(e:Entity {name: 'payments-api'})\nWHERE f.status = 'active'\n" +
      "  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(f) WHERE n.status = 'active' }\n" +
      'OPTIONAL MATCH (f)-[:SOURCE]->(s:Source)\nOPTIONAL MATCH (f)-[:ASSERTED_BY]->(a:Agent)\n' +
      'RETURN f.predicate AS predicate, f.value AS value, s.uri AS source, a.name AS author\nORDER BY predicate',
  ],
  [
    'What rests on the Postgres fact',
    "MATCH p = (f:Fact)-[:DERIVED_FROM*1..6]->(root:Fact {name: 'payments-api--database--postgres-15'})\n" +
      'RETURN f, length(p) AS hops, [n IN nodes(p) | n.value] AS chain\nORDER BY hops',
  ],
  [
    'Services on the same database',
    "MATCH (a:Fact {predicate: 'database'})-[:ABOUT]->(x:Entity),\n      (b:Fact {predicate: 'database'})-[:ABOUT]->(y:Entity)\n" +
      "WHERE a.value = b.value AND x.name < y.name AND a.status = 'active' AND b.status = 'active'\n" +
      'RETURN x, y, a.value AS database',
  ],
  ['Facts per agent', 'MATCH (f:Fact)-[:ASSERTED_BY]->(a:Agent)\nRETURN a.name AS agent, count(f) AS facts\nORDER BY facts DESC'],
  [
    'Break it: write a second port',
    "MATCH (e:Entity {name: 'checkout-web'}), (a:Agent {name: 'scout'})\n" +
      "CREATE (f:Fact {name: 'checkout-web--port--8080', predicate: 'port', value: '8080', status: 'active'})\n" +
      'CREATE (f)-[:ABOUT]->(e), (f)-[:ASSERTED_BY]->(a)\nRETURN f',
  ],
];

function renderConsole() {
  $('examples').innerHTML =
    EXAMPLES.map(([label], i) => `<button type="button" data-example="${i}">${esc(label)}</button>`).join('') +
    '<select id="rules" aria-label="Review rules"><option value="">Review rules (Cypher)…</option>' +
    data.rules.map((r) => `<option value="${esc(r.id)}">${esc(r.id)}: ${esc(r.title)}</option>`).join('') +
    '</select>';
  if (!$('query').value) $('query').value = EXAMPLES[0][1];
}

function renderTarget() {
  const what = view.pull ? `the graph after merging “${view.short}”` : 'the shared memory on main';
  $('target').textContent = relate
    ? `Runs in Relate, in this page, on ${what}${view.edited ? ' (with your changes)' : ''}.`
    : 'Relate could not start in this browser, so the console is off.';
  $('run').disabled = $('reset').disabled = !relate;
}

function cell(value) {
  if (value === null || value === undefined) return '<span class="null">null</span>';
  if (Array.isArray(value)) {
    const path = value.length && value.every((x) => x && typeof x === 'object' && x.kind);
    return value.map(cell).join(path ? ' ' : ', ');
  }
  if (typeof value === 'object') {
    if (value.kind === 'node') return link(`${value.labels[0]}/${value.properties.name}`);
    if (value.kind === 'relationship') return `<span class="rel">-[:${esc(value.type)}]-&gt;</span>`;
    return esc(JSON.stringify(value));
  }
  return esc(value);
}

function nodesIn(value, into) {
  if (Array.isArray(value)) value.forEach((x) => nodesIn(x, into));
  else if (value && typeof value === 'object' && value.kind === 'node') into.add(`${value.labels[0]}/${value.properties.name}`);
  else if (typeof value === 'string') idsIn([value], view.graph).forEach((id) => into.add(id));
}

function runQuery() {
  if (!relate) return;
  const query = $('query').value.trim();
  if (!query) return;
  const result = $('result');
  let out;
  try {
    out = run(view.db, query);
  } catch (err) {
    $('timing').textContent = '';
    result.innerHTML = `<pre class="error">${esc(err.message)}</pre>`;
    return;
  }
  const wrote = Object.entries(out.stats || {}).filter(([, n]) => n > 0);
  $('timing').textContent = `${plural(out.rows.length, 'row', 'rows')} in ${out.elapsed_ms.toFixed(1)} ms`;
  if (wrote.length) {
    view.edited = true;
    refresh(view);
    problem = null;
    draw();
    renderViews();
    renderReview();
    renderDetail();
    renderTarget();
  }
  const hits = new Set();
  out.rows.forEach((row) => Object.values(row).forEach((v) => nodesIn(v, hits)));
  cy.nodes().removeClass('hit');
  hits.forEach((id) => cy.getElementById(id).addClass('hit').removeClass('hidden'));
  const notes = [];
  if (wrote.length) notes.push(`Changed the graph: ${wrote.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.`);
  if (hits.size) notes.push(`${plural(hits.size, 'node is', 'nodes are')} marked on the graph.`);
  const head = notes.length ? `<p class="note">${notes.join(' ')}</p>` : '';
  if (!out.columns.length) {
    result.innerHTML = head || '<p class="note">Done. The query returned no columns.</p>';
    return;
  }
  const shown = out.rows.slice(0, 200);
  result.innerHTML =
    head +
    '<div class="table"><table><thead><tr>' +
    out.columns.map((c) => `<th>${esc(c)}</th>`).join('') +
    '</tr></thead><tbody>' +
    (shown.length
      ? shown.map((row) => `<tr>${out.columns.map((c) => `<td>${cell(row[c])}</td>`).join('')}</tr>`).join('')
      : `<tr><td colspan="${out.columns.length}" class="null">no rows</td></tr>`) +
    '</tbody></table></div>';
}

function setQuery(text, go = true) {
  $('query').value = text;
  if (go) runQuery();
  if (window.matchMedia('(max-width: 900px)').matches) $('query').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ---------------------------------------------------------------- wiring

function show(v, { keepHash = false } = {}) {
  view = v;
  openView(v);
  if (!v.graph) v.graph = v.after;
  const list = problemsOf(v);
  problem = list.find((p) => p.severity === 'block') || null;
  nodeId = null;
  cy.nodes().removeClass('hit');
  draw();
  renderViews();
  renderReview();
  renderDetail();
  renderTarget();
  $('result').innerHTML = '';
  $('timing').textContent = '';
  $('graph-note').textContent = v.pull
    ? 'Green: added by the pull request. Red dashed: gone after the merge. Glowing: the problem selected on the right.'
    : 'Drag to move, scroll or pinch to zoom, click a node for its source and author.';
  if (!keepHash) history.replaceState(null, '', v.pull ? `#pr=${encodeURIComponent(v.short)}` : location.pathname + location.search);
}

function selectProblem(i) {
  const p = verdict(view).list[i];
  problem = p === problem ? null : p;
  paint();
  renderReview();
}

function selectNode(id) {
  nodeId = id;
  cy.nodes().unselect();
  const n = id && cy.getElementById(id);
  if (n && n.length) {
    n.removeClass('hidden').select();
  }
  renderDetail();
}

function wire() {
  document.addEventListener('click', (event) => {
    const t = event.target.closest('button, [data-problem]');
    if (!t) return;
    const d = t.dataset;
    if (d.view !== undefined) show(views[Number(d.view)]);
    else if (d.rule !== undefined) setQuery(data.rules.find((r) => r.id === d.rule).cypher.trim());
    else if (d.fix !== undefined) {
      const [i, f] = d.fix.split(':').map(Number);
      setQuery(fixesFor(verdict(view).list[i])[f][1], false);
      $('query').focus();
    } else if (d.node !== undefined) {
      selectNode(d.node);
      const n = cy.getElementById(d.node);
      if (n.length) cy.animate({ center: { eles: n }, duration: 200 });
    } else if (d.close !== undefined) selectNode(null);
    else if (d.example !== undefined) setQuery(EXAMPLES[Number(d.example)][1]);
    else if (d.problem !== undefined) selectProblem(Number(d.problem));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches?.('[data-problem]')) selectProblem(Number(event.target.dataset.problem));
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && event.target === $('query')) runQuery();
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'rules' && event.target.value) {
      setQuery(data.rules.find((r) => r.id === event.target.value).cypher.trim());
      event.target.value = '';
    }
    if (event.target.id === 'show-prov') paint();
  });
  $('run').addEventListener('click', runQuery);
  $('fit').addEventListener('click', () => {
    problem = null;
    paint();
    renderReview();
  });
  $('reset').addEventListener('click', () => {
    view.db.free();
    view.db = null;
    view.edited = false;
    show(view);
  });
  cy.on('tap', 'node', (event) => selectNode(event.target.id()));
  cy.on('tap', (event) => event.target === cy && selectNode(null));
}

function renderLegend() {
  const items = [
    ['fact', 'Fact'], ['old', 'Replaced or retracted'], ['entity', 'Entity'], ['agent', 'Agent'], ['source', 'Source'],
    ['line derived', 'rests on'], ['line supersedes', 'replaces'], ['added', 'Added'], ['removed', 'Lost'],
  ];
  $('legend').innerHTML = items.map(([cls, text]) => `<span><i class="key ${cls}"></i>${text}</span>`).join('');
}

async function startRelate() {
  try {
    const mod = await import('./vendor/relate/relate.js');
    await mod.default();
    const db = new mod.RelateDb();
    run(db, 'RETURN 1 AS ok');
    db.free();
    relate = mod;
    $('engine').textContent = 'Relate is running in this page';
    $('engine').classList.add('ok');
  } catch (err) {
    console.error(err);
    $('engine').textContent = 'Relate could not start: showing the exported review';
    $('engine').classList.add('off');
  }
}

async function main() {
  renderLegend();
  const [response] = await Promise.all([fetch('data/demo.json'), startRelate()]);
  data = await response.json();
  views = [
    { pull: null, short: 'main', title: 'Shared memory', before: data.memory, after: data.memory },
    ...data.pulls.map((pull) => ({
      pull,
      short: pull.id.replace(/^agent\//, ''),
      title: pull.title.replace(/^memory:\s*/, ''),
      before: pull.before || data.memory,
      after: pull.after,
    })),
  ];
  cy = cytoscape({ container: $('cy'), style: STYLE, minZoom: 0.2, maxZoom: 3, boxSelectionEnabled: false });
  window.mempr = { cy, views }; // for the curious, in the browser console
  wire();
  renderConsole();
  // Open every graph up front so each tab shows its verdict from Relate.
  views.forEach((v) => {
    openView(v);
    if (!v.graph) v.graph = v.after;
  });
  view = views[0];
  draw(); // lay out the memory first, so its notes keep their place in every view
  const wanted = decodeURIComponent((location.hash.match(/pr=([^&]+)/) || [])[1] || '');
  show(views.find((v) => v.pull && v.short === wanted) || views[0], { keepHash: true });
  new ResizeObserver(() => {
    cy.resize();
    paint();
  }).observe($('cy'));
}

main().catch((err) => {
  console.error(err);
  $('review').innerHTML = `<p class="error">Could not load the demo data: ${esc(err.message)}. Serve this folder over HTTP, for example <code>python -m http.server -d site</code>.</p>`;
});
