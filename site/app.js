import { apiBase, fetchServerReview, serverLine, serverRequest } from './server-check.js';
import { again, prLine, submitPullRequest, watchPullRequest, whyNot } from './github-pr.js';
import { LEXICAL, NEIGHBORHOOD } from './search.js';

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
let engine = 'starting'; // 'starting', 'ready' or 'off'
const calm = matchMedia('(prefers-reduced-motion: reduce)');
const coarse = matchMedia('(pointer: coarse)');
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
  // A note is a labelled node with a name; anything else a visitor creates is left undrawn.
  const named = 'n.name IS NOT NULL AND size(labels(n)) > 0';
  const nodes = run(db, `MATCH (n) WHERE ${named} RETURN labels(n)[0] AS label, properties(n) AS props`).rows.map((row) => {
    const { stub, ...props } = row.props;
    const id = `${row.label}/${props.name}`;
    const known = meta.get(id) || {};
    return { id, label: row.label, props, stub: stub === true, file: known.file, text: known.text, issues: known.issues };
  });
  const rels = run(
    db,
    'MATCH (a)-[r]->(b) WHERE a.name IS NOT NULL AND b.name IS NOT NULL AND size(labels(a)) > 0 AND size(labels(b)) > 0 ' +
      'RETURN labels(a)[0] AS al, a.name AS an, type(r) AS type, ' +
      'labels(b)[0] AS bl, b.name AS bn, properties(r) AS props',
  ).rows.map((row) => ({ source: `${row.al}/${row.an}`, type: row.type, target: `${row.bl}/${row.bn}`, props: row.props }));
  const order = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  nodes.sort((a, b) => order(a.id, b.id));
  rels.sort((a, b) => order(relKey(a), relKey(b)));
  return { nodes, rels };
}

const rowKey = (row) => JSON.stringify(Object.keys(row).sort().map((k) => [k, row[k]]));

/** A pull request is reviewed with its base branch's rules, which can differ from main's. */
const rulesOf = (v) => (v && v.pull && v.pull.rules) || data.rules;

function ruleRows(db, rules) {
  const found = new Map();
  for (const rule of rules) {
    try {
      found.set(rule.id, run(db, rule.cypher).rows);
    } catch (error) {
      throw new Error(`${rule.id}: ${error.message}`);
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
  const now = ruleRows(v.db, rulesOf(v));
  for (const rule of rulesOf(v)) {
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
  if (v.apply) v.apply(v.db); // a visitor's draft: the change is written with Cypher
  const base = loadDb(v.before);
  try {
    const rows = ruleRows(base, rulesOf(v));
    // On the memory itself every finding counts; on a pull request only the new ones do.
    v.baseline = new Map([...rows].map(([id, r]) => [id, new Set(v.pull ? r.map(rowKey) : [])]));
    v.baseRuleError = null;
  } catch (error) {
    v.baseRuleError = error.message;
  } finally {
    base.free();
  }
  refresh(v);
}

function refresh(v) {
  v.graph = readGraph(v.db, v.after);
  for (const note of v.notes || []) Object.assign(v.graph.nodes.find((n) => n.id === note.id) || {}, note);
  v.live = null;
  v.ruleError = v.baseRuleError;
  if (v.ruleError) return;
  try {
    v.live = liveFindings(v);
  } catch (error) {
    v.ruleError = error.message;
  }
}

function problemsOf(v) {
  if (v.ruleError) return [{
    kind: 'review-error', severity: 'block', title: 'Review incomplete', text: v.ruleError, nodes: [], rels: [],
  }];
  const exported = v.pull ? v.pull.problems : [];
  if (!v.live) return exported;
  return [...exported.filter((p) => p.kind !== 'finding'), ...v.live];
}

// ---------------------------------------------------------------- drawing

// The graph is drawn on a canvas, so it reads the page's colour tokens from style.css here.
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
const C = Object.fromEntries(
  ['base', 'surface', 'ink', 'muted', 'rule', 'faint', 'bad', 'bad-wash', 'good', 'good-wash'].map((n) => [n, token(n)]),
);
const FONT = 'Geist, "Helvetica Neue", Helvetica, Arial, sans-serif';
const STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(label)', color: C.ink, 'font-size': 11, 'font-family': FONT,
      'text-wrap': 'wrap', 'text-valign': 'center', 'text-halign': 'center', 'line-height': 1.25,
      'background-color': C.surface, 'border-width': 1.2, 'border-color': C.ink,
      width: 'data(w)', height: 'data(h)', shape: 'round-rectangle',
      'overlay-opacity': 0, 'transition-property': 'opacity', 'transition-duration': '150ms',
    },
  },
  { selector: 'node.old', style: { 'background-color': C.faint, 'border-color': '#a19e94', color: C.muted, 'border-style': 'dashed' } },
  {
    selector: 'node[kind = "Entity"]',
    style: { shape: 'ellipse', 'background-color': C.ink, 'border-color': C.ink, color: C.base, 'font-size': 12, 'font-weight': 600 },
  },
  { selector: 'node[kind = "Agent"]', style: { shape: 'ellipse', 'border-color': C.muted, color: C.muted } },
  { selector: 'node[kind = "Source"]', style: { shape: 'round-tag', 'background-color': C.faint, 'border-color': C.muted, color: C.muted, 'font-size': 9 } },
  { selector: 'node[kind = "Predicate"]', style: { shape: 'round-diamond', 'background-color': C.faint, 'border-color': C.muted, color: C.muted, 'font-size': 9 } },
  { selector: 'node.stub', style: { 'background-color': C.surface, 'border-style': 'dotted', 'border-color': C.bad, color: C.bad } },
  { selector: 'node.added', style: { 'border-color': C.good, 'border-width': 3.5, 'background-color': C['good-wash'], color: C.ink } },
  { selector: 'node.changed', style: { 'border-color': C.ink, 'border-width': 3.5 } },
  { selector: 'node.removed', style: { opacity: 0.7, 'border-color': C.bad, 'border-width': 2.5, 'border-style': 'dashed', 'background-color': C.surface, color: C.bad } },
  {
    selector: 'edge',
    style: {
      width: 1, 'line-color': '#a19e94', 'target-arrow-color': '#a19e94', 'target-arrow-shape': 'triangle',
      'arrow-scale': 0.8, 'curve-style': 'bezier', 'font-size': 8.5, 'font-family': FONT, color: C.muted,
      'text-background-color': C.surface, 'text-background-opacity': 0.95, 'text-background-padding': 2,
      'text-rotation': 'autorotate', 'overlay-opacity': 0,
    },
  },
  { selector: 'edge[type = "SOURCE"], edge[type = "ASSERTED_BY"]', style: { 'line-style': 'dotted', 'line-color': C.rule, 'target-arrow-color': C.rule } },
  { selector: 'edge[type = "DERIVED_FROM"]', style: { 'line-color': C.ink, 'target-arrow-color': C.ink, width: 2, label: 'rests on' } },
  { selector: 'edge[type = "SUPERSEDES"]', style: { 'line-color': C.ink, 'target-arrow-color': C.ink, 'line-style': 'dashed', width: 2, label: 'replaces' } },
  { selector: 'edge.added', style: { 'line-color': C.good, 'target-arrow-color': C.good, width: 2.2 } },
  { selector: 'edge.removed', style: { 'line-color': C.bad, 'target-arrow-color': C.bad, 'line-style': 'dashed', width: 2, label: 'data(lost)', color: C.bad } },
  { selector: '.hidden', style: { display: 'none' } },
  { selector: '.dim', style: { opacity: 0.3 } },
  { selector: 'node.hit', style: { 'underlay-color': C.ink, 'underlay-opacity': 0.14, 'underlay-padding': 7 } },
  { selector: 'node.hl', style: { 'underlay-color': C.bad, 'underlay-opacity': 0.22, 'underlay-padding': 8, opacity: 1 } },
  { selector: 'edge.hl', style: { width: 3.5, 'line-color': C.bad, 'target-arrow-color': C.bad, opacity: 1, label: 'data(name)', 'font-size': 9.5, color: C.bad, 'z-index': 9 } },
  { selector: 'node:selected', style: { 'underlay-color': C.ink, 'underlay-opacity': 0.18, 'underlay-padding': 6 } },
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
    n.old = n.label === 'Fact' && (n.replacedBy || ['retracted', 'unconfirmed'].includes(n.props.status));
  }
  return { nodes, rels };
}

function nodeLabel(n) {
  if (n.label === 'Fact') {
    if (n.empty) return `${n.props.name}\n(empty note)`;
    if (n.stub) return `${n.props.name}\n(no note)`;
    return `${n.props.predicate ?? n.props.name}\n${n.props.value ?? ''}`.trim();
  }
  if (n.label === 'Source') {
    const text = n.props.uri ? n.props.uri.split('/').slice(-2).join('/') : n.props.name;
    return n.stub ? `${text}\n(no note)` : text;
  }
  return n.stub ? `${n.props.name}\n(no note)` : String(n.props.name);
}

/** Cytoscape elements for a set of notes and links, each with its state as a class. */
function elementsOf(nodes, rels) {
  const elements = [];
  const ids = new Set();
  for (const n of nodes) {
    ids.add(n.id);
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
  for (const r of rels) {
    if (!ids.has(r.source) || !ids.has(r.target)) continue;
    const name = EDGE_NAME[r.type] || r.type.toLowerCase();
    elements.push({
      group: 'edges',
      data: { id: relKey(r), source: r.source, target: r.target, type: r.type, name, lost: `${name} (lost)` },
      classes: r.state,
    });
  }
  return elements;
}

function draw({ relayout = false } = {}) {
  const u = (view.union = union(view));
  cy.elements().remove();
  cy.add(elementsOf(u.nodes.values(), u.rels));
  layout(relayout);
  paint();
}

/** A real repository has many services: each gets its own block (service, its facts, their files), so nothing overlaps. */
function clusterLayout() {
  const GAP = 14, PAD = 56;
  const byId = (a, b) => (a.id() < b.id() ? -1 : 1);
  const home = new Map();
  const entities = cy.nodes('[kind = "Entity"]').sort(byId);
  const groups = entities.map((e) => ({ entity: e, facts: [], files: [] }));
  const of = new Map(groups.map((g) => [g.entity.id(), g]));
  cy.nodes('[kind = "Fact"]').sort(byId).forEach((f) => {
    const about = f.outgoers('edge[type = "ABOUT"]').targets()[0];
    const g = about && of.get(about.id());
    if (g) g.facts.push(f), home.set(f.id(), g);
  });
  groups.forEach((g) => g.facts.forEach((f) => f.outgoers('edge[type = "SOURCE"]').targets().forEach((src) => {
    if (!home.has(src.id())) g.files.push(src), home.set(src.id(), g);
  })));
  const rest = cy.nodes().filter((n) => !home.has(n.id()) && !of.has(n.id())).sort(byId);
  const rowsOf = (nodes, per) => {
    const rows = [];
    for (let i = 0; i < nodes.length; i += per) rows.push(nodes.slice(i, i + per));
    return rows;
  };
  const size = (row) => ({
    w: row.reduce((t, n) => t + n.data('w'), 0) + GAP * (row.length - 1),
    h: Math.max(...row.map((n) => n.data('h'))),
  });
  for (const g of groups) {
    g.rows = [[g.entity], ...rowsOf(g.facts, 2), ...rowsOf(g.files, 2)];
    g.w = Math.max(...g.rows.map((r) => size(r).w));
    g.h = g.rows.reduce((t, r) => t + size(r).h, 0) + GAP * (g.rows.length - 1);
  }
  const area = groups.reduce((t, g) => t + (g.w + PAD) * (g.h + PAD), 0);
  // Shaped like the canvas, so Whole graph fits a phone too.
  const aspect = Math.min(2, Math.max(0.5, cy.width() / cy.height() || 1.6));
  const width = Math.max(Math.sqrt(area * aspect), ...groups.map((g) => g.w));
  const place = (row, cx, y) => {
    const { w, h } = size(row);
    let x = cx - w / 2;
    for (const n of row) {
      n.position({ x: x + n.data('w') / 2, y: y + h / 2 });
      x += n.data('w') + GAP;
    }
    return h;
  };
  // Agents and anything not about a service go along the top.
  let y = 0;
  if (rest.length) y += place(rest, width / 2, 0) + PAD * 1.5;
  let x = 0, shelf = 0;
  for (const g of groups) {
    if (x > 0 && x + g.w > width) (y += shelf + PAD), (x = 0), (shelf = 0);
    let gy = y;
    for (const r of g.rows) gy += place(r, x + g.w / 2, gy) + GAP;
    x += g.w + PAD;
    shelf = Math.max(shelf, g.h);
  }
}

function layout(relayout) {
  const fresh = cy.nodes().filter((n) => !positions.has(n.id()));
  if (view.real) {
    clusterLayout();
  } else if (relayout || fresh.length === cy.nodes().length) {
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

function paint({ fit = true, glide = true } = {}) {
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
  const from = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  cy.stop();
  frame();
  if (calm.matches || !glide || !framed) return void (framed = true);
  // Travel from the last framing to the new one, so a switch reads as one graph changing.
  const to = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  cy.viewport(from);
  cy.animate({ zoom: to.zoom, pan: to.pan }, { duration: 520, easing: 'ease-in-out-cubic' });
}

let framed = false;
function frame() {
  const target = problem ? cy.elements('.hl') : cy.elements(':visible');
  const eles = target.length ? target : cy.elements();
  // Show the whole graph when its labels stay readable; otherwise frame the problem.
  cy.fit(cy.elements(':visible'), 28);
  if (cy.zoom() >= 0.72 || !target.length) return;
  cy.fit(eles, Math.min(problem ? 70 : 24, Math.round(cy.width() * 0.045))); // a narrow screen gives the path the room
  if (cy.zoom() > 1.15) {
    cy.zoom(1.15);
    cy.center(eles);
  }
}

/** What changed arrives: added notes pop in, lost ones fade back, the problem path pulses once. */
function enliven({ changes = true, on = cy } = {}) {
  if (calm.matches) return;
  const settle = (ele) => () => ele.removeStyle();
  const rest = (n) => (n.hasClass('dim') ? 0.3 : n.hasClass('removed') && !n.hasClass('hl') ? 0.7 : 1);
  if (changes) {
    on.nodes('.added').forEach((n, i) => {
      n.style({ opacity: 0, 'border-width': 14 });
      n.delay(260 + i * 70).animate({ style: { opacity: rest(n), 'border-width': 3.5 } }, { duration: 420, easing: 'ease-out-cubic', complete: settle(n) });
    });
    on.nodes('.removed').forEach((n) => {
      n.style({ opacity: 1 });
      n.delay(260).animate({ style: { opacity: rest(n) } }, { duration: 700, complete: settle(n) });
    });
  }
  on.edges('.hl').forEach((e) => {
    e.delay(changes ? 720 : 120).animate({ style: { width: 9 } }, { duration: 200 }).animate({ style: { width: 3.5 } }, { duration: 420, complete: settle(e) });
  });
  on.nodes('.hl').forEach((n) => {
    if (n.hasClass('added') && changes) return;
    n.delay(changes ? 720 : 120).animate({ style: { 'underlay-opacity': 0.6, 'underlay-padding': 16 } }, { duration: 200 })
      .animate({ style: { 'underlay-opacity': 0.22, 'underlay-padding': 8 } }, { duration: 420, complete: settle(n) });
  });
}

// ---------------------------------------------------------------- panels

/** What a visitor's pull request adds and removes, counted the way an exported review is. */
function delta(v) {
  const u = v.union || union(v);
  const of = (state) => [
    ...[...u.nodes.values()].filter((n) => n.state === state).map((n) => ['node', n.id]),
    ...u.rels.filter((r) => r.state === state).map((r) => ['rel', relKey(r)]),
  ];
  const changed = [...u.nodes.values()].filter((n) => n.state === 'changed').map((n) => ['prop', n.id]);
  return { added: [...of('added'), ...changed], removed: of('removed') };
}

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

const reviewState = (blocked, unconfirmed) => blocked ? 'blocked' : unconfirmed ? 'unconfirmed' : 'passed';
const REVIEW_CHIP = { blocked: 'Blocked', unconfirmed: 'Needs verification', passed: 'Passes' };

function renderViews() {
  $('views').innerHTML = views
    .map((v, i) => {
      const state = v.pull ? reviewState(verdict(v).blocked, v.unconfirmed?.length) : '';
      const tone = state === 'unconfirmed' ? 'base' : state;
      const chip = v.pull ? `<span class="chip-state ${tone}">${REVIEW_CHIP[state]}</span>` : '<span class="chip-state base">main</span>';
      const name = v.mine ? 'Your pull request' : v.pull ? `${esc(v.pull.author)}` : 'Shared memory';
      const sub = v.pull ? esc(v.pull.description) : 'What every agent reads today';
      return `<button type="button" data-view="${i}" class="${v === view ? 'on' : ''} ${state}">${chip}<strong>${name}</strong><small>${sub}</small></button>`;
    })
    .join('');
  // The cobalt marker slides from the last selected card to this one.
  const nav = $('views');
  const on = nav.querySelector('.on');
  const mark = nav.appendChild(document.createElement('i'));
  mark.className = 'marker';
  const to = on ? `translate(${on.offsetLeft}px, ${on.offsetTop}px) scaleX(${on.offsetWidth / 100})` : '';
  mark.style.transform = markAt || to;
  requestAnimationFrame(() => (mark.style.transform = markAt = to));
  if (on && nav.scrollWidth > nav.clientWidth + 4) {
    nav.scrollTo({ left: on.offsetLeft - (nav.clientWidth - on.offsetWidth) / 2, behavior: calm.matches ? 'auto' : 'smooth' });
  }
}
let markAt = '';

/** The first screen's proof: the same finding the review reports, read from the data. */
function renderProof(v) {
  if (!v.pull) return;
  const list = problemsOf(v);
  const c = list.find((x) => x.rule === 'contradiction' && x.row);
  $('proof-who').textContent = v.pull.id;
  if (c) {
    const { entity, predicate, fact, other, value, other_value: otherValue } = c.row;
    if (![entity, predicate, value, otherValue].every((x) => typeof x === 'string')) return;
    const inMemory = (name) => data.memory.nodes.some((n) => n.id === `Fact/${name}`);
    const [fresh, old] = inMemory(fact) && !inMemory(other) ? [otherValue, value] : [value, otherValue];
    $('proof-new').textContent = fresh;
    $('proof-entity').textContent = entity;
    $('proof-old').textContent = old;
    $('proof-top-card').className = 'fact new';
    $('proof-top-note').textContent = `${predicate}, added by this pull request`;
    $('proof-edge-a').textContent = 'about';
    $('proof-edge-b').textContent = 'about';
    $('proof-bot-note').textContent = `${predicate}, already in memory`;
    $('proof-stamp').textContent = 'Blocked. Two facts contradict.';
    $('proof-foot').textContent = 'One service, two active databases. The path is the proof. Open it in the graph.';
    return;
  }
  // A clean merge that drops a fact and its links: show the path that disappears.
  const d = list.find((x) => x.kind === 'dropped');
  if (!d) return;
  const name = (prefix) => d.nodes.find((n) => n.startsWith(prefix));
  const fact = data.memory.nodes.find((n) => n.id === name('Fact/'));
  const source = data.memory.nodes.find((n) => n.id === name('Source/'));
  const uri = source?.props?.uri || '';
  $('proof-new').textContent = uri.split('/').slice(-2).join('/') || 'the evidence';
  $('proof-entity').textContent = fact?.props?.value || 'the fact';
  $('proof-old').textContent = (name('Entity/') || '').split('/').pop() || 'the entity';
  $('proof-top-card').className = 'fact lost';
  $('proof-top-note').textContent = 'the file the fact cites';
  $('proof-edge-a').textContent = 'source';
  $('proof-edge-b').textContent = 'about';
  $('proof-bot-note').textContent = 'the entity it describes, already in memory';
  $('proof-stamp').textContent = `Blocked. ${d.title}.`;
  $('proof-foot').textContent = 'Git calls this merge clean. The fact and the links to its evidence disappear anyway.';
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
        const state = reviewState(s.blocked, x.unconfirmed?.length);
        const label = s.blocked ? `blocked: ${plural(s.blocking, 'problem', 'problems')}` : REVIEW_CHIP[state];
        return (
          `<button type="button" class="pull" data-view="${views.indexOf(x)}">` +
          `<span class="tag ${state}">${label}</span>` +
          `<strong>${esc(x.title)}</strong><span>${esc(x.pull.author)} ${esc(x.pull.description)}</span></button>`
        );
      }),
      '</div>',
    );
  } else {
    const r = v.pull.review || delta(v);
    const unconfirmed = v.unconfirmed?.length || 0;
    const state = reviewState(blocked, unconfirmed);
    parts.push(
      `<div class="verdict ${state}">${
        v.ruleError ? 'Review incomplete' : blocked ? `Merge blocked: ${plural(blocking, 'problem', 'problems')}` : unconfirmed ? 'Needs verification' : 'Review passed: safe to merge'
      }</div>`,
      `<h2>${esc(v.title)}</h2>`,
      `<p class="meta"><strong>${esc(v.pull.author)}</strong> ${esc(v.pull.description)}. Branch <code>${esc(v.pull.id)}</code>.</p>`,
      `<p class="delta"><span class="plus">+ ${count(r.added)}</span><span class="minus">− ${count(r.removed)}</span></p>`,
    );
    if (list.length) parts.push(problemList(list));
    if (unconfirmed) parts.push(`<p><a href="#real-out">${plural(unconfirmed, 'older fact needs', 'older facts need')} verification.</a></p>`);
    else if (!list.length) parts.push('<p class="ok">Nothing contradicts, nothing is lost, every fact has a source.</p>');
    if (v.mine && !v.edited && !v.ruleError) parts.push(`<p class="live">Relate reviewed your pull request in this page with the ${rulesOf(v).length} rules <code>mempr check</code> runs. <a href="#write">Change it</a>.</p>`);
    else if (v.live) parts.push(liveLine(v, list));
    if (v.pull.comment) parts.push(
      '<details><summary>The comment the check posts on the pull request</summary>' +
        `<pre>${esc(v.pull.comment.replace('<!-- mempr-review -->\n', ''))}</pre></details>`,
    );
  }
  $('review').innerHTML = parts.join('');
}

function liveLine(v, list) {
  if (v.ruleError) return '';
  if (v.edited) {
    return (
      `<p class="live edited">You changed this graph. Relate re-ran the ${rulesOf(view).length} review rules: ` +
      `${list.length ? plural(list.length, 'problem', 'problems') + ' now' : 'no problems now'}.</p>`
    );
  }
  const signature = (findings) => JSON.stringify(findings.map((p) => JSON.stringify([p.rule, p.severity, p.text])).sort());
  const exported = v.pull.problems.filter((p) => p.kind === 'finding');
  const same = signature(exported) === signature(v.live);
  return (
    `<p class="live ${same ? '' : 'edited'}">Relate ran the ${rulesOf(view).length} review rules in this page ` +
    `${same ? 'and matched the findings from' : 'and found a difference from the findings in'} <code>mempr check</code>.</p>`
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
          (p.rule ? cypherBlock(p.rule, rulesOf(view)) : '') +
          (actions ? `<div class="actions">${actions}</div>` : '') +
          '</li>'
        );
      })
      .join('') +
    '</ol>'
  );
}

function cypherBlock(ruleId, rules) {
  const rule = (rules || []).find((r) => r.id === ruleId);
  if (!rule || !rule.cypher) return '';
  return `<details class="cypher"><summary>Cypher for <code>${esc(ruleId)}</code></summary><pre>${esc(rule.cypher.trim())}</pre></details>`;
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
          : n.props.status === 'unconfirmed'
            ? '<span class="state old">unconfirmed</span>'
            : n.replacedBy
              ? '<span class="state old">replaced</span>'
              : '<span class="state current">current</span>',
      );
    }
    const evidence = view.unconfirmed?.find((fact) => `Fact/${fact.fact}` === n.id);
    if (evidence) {
      row('Latest scan', esc(UNCONFIRMED_REASON[evidence.reason]));
      row('Historical source', `<a href="${esc(evidence.old_url)}" target="_blank" rel="noopener">${esc(evidence.path)}</a>`);
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
    `<dl>${rows.join('')}</dl>${editable(n)}${file}`;
}

/** A current fact in the shared memory can be replaced or retracted by a pull request of your own. */
function editable(n) {
  const held = memory.nodes.find((m) => m.id === n.id);
  if (n.label !== 'Fact' || n.stub || n.empty || !held || held.props.status !== 'active') return '';
  return (
    '<div class="actions">' +
    `<button type="button" data-replace-fact="${esc(n.props.name)}">Replace with a new value</button>` +
    `<button type="button" data-retract-fact="${esc(n.props.name)}">Retract it</button></div>`
  );
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
      "  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(a) WHERE n.status = 'active' }\n" +
      "  AND NOT EXISTS { MATCH (n:Fact)-[:SUPERSEDES]->(b) WHERE n.status = 'active' }\n" +
      'RETURN x, y, a.value AS database',
  ],
  ['Facts per agent', 'MATCH (f:Fact)-[:ASSERTED_BY]->(a:Agent)\nRETURN a.name AS agent, count(f) AS facts\nORDER BY facts DESC'],
  [
    'Break it: write a second port',
    "MATCH (e:Entity {name: 'checkout-web'}), (a:Agent {name: 'scout'})\n" +
      "MERGE (f:Fact {name: 'checkout-web--port--8080'})\n" +
      "SET f.predicate = 'port', f.value = '8080', f.status = 'active'\n" +
      'MERGE (f)-[:ABOUT]->(e)\nMERGE (f)-[:ASSERTED_BY]->(a)\nRETURN f.name AS fact, f.value AS port',
  ],
  [
    'Write: retract the Postgres fact',
    "MATCH (f:Fact {name: 'payments-api--database--postgres-15'})\nSET f.status = 'retracted'\nRETURN f.name AS fact, f.status AS status",
  ],
];
const writes = (label) => /^(Break it|Write)/.test(label);

function renderConsole() {
  $('examples').innerHTML =
    EXAMPLES.map(([label], i) => `<button type="button" data-example="${i}" class="${writes(label) ? 'write-preset' : ''}">${esc(label)}</button>`).join('') +
    '<select id="rules" aria-label="Review rules"><option value="">Review rules (Cypher)</option>' +
    rulesOf(view).map((r) => `<option value="${esc(r.id)}">${esc(r.id)}: ${esc(r.title)}</option>`).join('') +
    '</select>';
  if (!$('query').value) $('query').value = EXAMPLES[0][1];
}

function renderTarget() {
  const what = view.pull ? `the graph after merging “${view.short}”` : 'the shared memory on main';
  $('target').textContent = relate
    ? `Runs in Relate, in this page, on ${what}${view.edited ? ' (with your changes)' : ''}.`
    : engine === 'starting'
      ? 'Relate is loading. The console turns on when it is ready.'
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

let pending = 0;
/** Show that the query is running, then its result. The query itself takes about a millisecond. */
function runQuery() {
  if (!relate || !$('query').value.trim()) return;
  const box = $('console');
  clearTimeout(pending);
  box.classList.remove('done');
  if (calm.matches) return execQuery();
  box.classList.add('running');
  pending = setTimeout(() => {
    execQuery();
    box.classList.remove('running');
    box.classList.add('done');
  }, 180);
}

function runSearch(text) {
  const result = $('result');
  const q = text.trim();
  // Search the shared memory. A pull request's merged graph can be the damaged one,
  // and that is the wrong place to ask what the agents know.
  const memoryView = views.find((v) => !v.pull) || view;
  openView(memoryView);
  if (!memoryView.db) {
    result.innerHTML = '<p class="outcome-empty">Relate is still loading. Try the search again in a moment.</p>';
    return;
  }
  if (q.length < 2) {
    result.innerHTML = '<p class="outcome-empty">Type at least two characters. No model and no key: this is a keyword plus the derivation path.</p>';
    return;
  }
  let hits, connected;
  try {
    hits = run(memoryView.db, LEXICAL, { q }).rows;
    connected = hits.length ? run(memoryView.db, NEIGHBORHOOD, { names: hits.map((hit) => hit.fact) }).rows : [];
  } catch (err) {
    result.innerHTML = `<pre class="error">${esc(err.message)}</pre>`;
    return;
  }
  const line = (row, how) => {
    const entity = row.entity || '(no entity)';
    const hops = row.hops ? `, ${row.hops} hop${row.hops === 1 ? '' : 's'}` : '';
    return `<li><code>${esc(entity)}</code> ${esc(row.predicate)} = ${esc(row.value)} <em>${esc(how)}${hops}</em></li>`;
  };
  const items = [
    ...hits.map((row) => line(row, 'match')),
    ...connected.map((row) => line(row, row.via)),
  ];
  cy.nodes().removeClass('hit');
  for (const row of [...hits, ...connected]) {
    const node = cy.getElementById(`Fact/${row.fact}`);
    if (node.length) node.addClass('hit').removeClass('hidden');
  }
  $('timing').textContent = `${plural(hits.length, 'match', 'matches')}, ${plural(connected.length, 'connected fact', 'connected facts')}`;
  result.innerHTML =
    (items.length ? `<ul class="search-hits">${items.join('')}</ul>` : '<p class="outcome-empty">Nothing matches. Try another word.</p>') +
    '<details class="cypher" open><summary>Cypher for this search</summary>' +
    `<pre>${esc(LEXICAL)}</pre><pre>${esc(NEIGHBORHOOD)}</pre></details>`;
  $('console').scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth', block: 'nearest' });
}

function execQuery() {
  const query = $('query').value.trim();
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
    renderPlay(); // a write to your own pull request changes its verdict there too
  }
  const hits = new Set();
  out.rows.forEach((row) => Object.values(row).forEach((v) => nodesIn(v, hits)));
  cy.nodes().removeClass('hit');
  hits.forEach((id) => cy.getElementById(id).addClass('hit').removeClass('hidden'));
  if (!calm.matches) {
    // The returned rows and the nodes they name light up together.
    cy.nodes('.hit').forEach((n) => {
      n.animate({ style: { 'underlay-opacity': 0.5, 'underlay-padding': 15 } }, { duration: 200 })
        .animate({ style: { 'underlay-opacity': 0.14, 'underlay-padding': 7 } }, { duration: 500, complete: () => n.removeStyle() });
    });
  }
  const notes = [];
  if (wrote.length) {
    const n = verdict(view).blocking;
    notes.push(
      `Changed the graph: ${wrote.map(([k, c]) => `${c} ${k.replace(/_/g, ' ')}`).join(', ')}. ` +
        `The review reran: ${n ? plural(n, 'problem blocks', 'problems block') + ' the merge' : 'nothing blocks the merge'}. ` +
        '<a href="#review">See the review</a>.',
    );
    $('review').classList.remove('swap');
    void $('review').offsetWidth;
    $('review').classList.add('swap');
  }
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

let typing = 0;
/** Load a query into the box as if typed, then run it. */
function setQuery(text, go = true) {
  const box = $('query');
  cancelAnimationFrame(typing);
  clearTimeout(pending);
  if (window.matchMedia('(max-width: 900px)').matches) box.scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth', block: 'center' });
  const done = () => {
    box.value = text;
    if (go) runQuery();
  };
  if (calm.matches) return done();
  const start = performance.now();
  const span = Math.min(480, 140 + text.length * 2);
  const step = (now) => {
    const k = Math.min(1, (now - start) / span);
    box.value = text.slice(0, Math.ceil(text.length * k));
    if (k < 1) typing = requestAnimationFrame(step);
    else done();
  };
  typing = requestAnimationFrame(step);
}

// ---------------------------------------------------------------- write a memory
// The visitor plays an agent: a form becomes a Markdown note, the note becomes a pull request
// against a copy of the memory in Relate, and the same rules review it. Typed text is data:
// it reaches Cypher only as parameters and the page only through esc().

let memory; // the page's shared memory: the committed data plus what the visitor merged
let mini; // the small graph beside the form
const play = { draft: null, view: null, tab: 'note', mine: [], count: 0, starters: [], error: '', merged: '', landed: [] };
const PREDICATES = ['database', 'port', 'owner', 'runtime', 'calls', 'queue', 'region', 'retries'];

/** As `slug` in src/mempr/memory.py. */
const slug = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
const blank = (agent = 'visitor') => ({
  kind: 'fact', agent, entity: '', predicate: '', value: '', source: '',
  supersedes: [], derived_from: [], retract: [], about: true, source_status: '',
});
const clean = (d) => ({
  ...blank(), ...d,
  agent: slug(d.agent || ''), entity: slug(d.entity || ''), predicate: slug(d.predicate || ''),
  value: String(d.value || '').trim(), source: String(d.source || '').trim(),
  supersedes: [...(d.supersedes || [])], derived_from: [...(d.derived_from || [])],
  preserve_value: d.preserve_value === true,
  retract: [...(d.retract || [])],
  about: d.about !== false,
  source_status: d.source_status === 'retracted' ? 'retracted' : '',
});

function sha1(text) {
  const bytes = [...new TextEncoder().encode(text)];
  const bits = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  for (let i = 7; i >= 0; i--) bytes.push(i > 3 ? 0 : (bits >>> (i * 8)) & 255);
  const h = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
  const rol = (x, n) => (x << n) | (x >>> (32 - n));
  for (let o = 0; o < bytes.length; o += 64) {
    const w = [];
    for (let i = 0; i < 80; i++) {
      w[i] = i < 16 ? (bytes[o + i * 4] << 24) | (bytes[o + i * 4 + 1] << 16) | (bytes[o + i * 4 + 2] << 8) | bytes[o + i * 4 + 3] : rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    let [a, b, c, d, e] = h;
    for (let i = 0; i < 80; i++) {
      const [f, k] = i < 20 ? [(b & c) | (~b & d), 0x5a827999] : i < 40 ? [b ^ c ^ d, 0x6ed9eba1] : i < 60 ? [(b & c) | (b & d) | (c & d), 0x8f1bbcdc] : [b ^ c ^ d, 0xca62c1d6];
      [a, b, c, d, e] = [(rol(a, 5) + f + e + k + w[i]) | 0, a, rol(b, 30), c, d];
    }
    [a, b, c, d, e].forEach((x, i) => (h[i] = (h[i] + x) | 0));
  }
  return h.map((x) => (x >>> 0).toString(16).padStart(8, '0')).join('');
}

const factOf = (name) => memory.nodes.find((n) => n.id === `Fact/${name}`);

/** The note a fact is written to, as `_free_name` in src/mempr/memory.py picks it. */
function factName(d) {
  if (d.kind === 'retract') return d.target;
  const base = `${d.entity}--${d.predicate}--${slug(d.value)}`;
  const held = factOf(base);
  return held && String(held.props.value) !== d.value ? `${base}-${sha1(d.value).slice(0, 6)}` : base;
}

/** The Markdown file the pull request would add or change, in the format `mempr add-fact` writes. */
function noteOf(d) {
  const name = factName(d);
  const file = `memory/Fact/${name}.md`;
  if (d.kind === 'retract') {
    const held = factOf(name);
    return { id: `Fact/${name}`, file, text: held && held.text ? held.text.replace(/^status: .*$/m, 'status: retracted') : '' };
  }
  const plain = /^[A-Za-z][A-Za-z0-9._/ -]*$/.test(d.value) && !/^(true|false|null|yes|no|on|off)$/i.test(d.value);
  const links = (key, targets) => (targets.length ? [`${key}:`, ...targets.map((t) => `  - "[[${t}]]"`)] : []);
  const text = [
    '---', 'type: Fact', `name: ${name}`, `predicate: ${d.predicate}`, 'status: active', `value: ${plain ? d.value : JSON.stringify(d.value)}`,
    ...(d.preserve_value ? ['preserve_value: true'] : []),
    ...links('ABOUT', d.about === false ? [] : [`Entity/${d.entity}`]),
    ...links('ASSERTED_BY', [`Agent/${d.agent}`]),
    ...links('SOURCE', d.source ? [`Source/${slug(d.source)}`] : []),
    ...links('SUPERSEDES', d.supersedes.map((f) => `Fact/${f}`)),
    ...links('DERIVED_FROM', (d.derived_from || []).map((f) => `Fact/${f}`)),
    '---', '', `${d.entity} ${d.predicate}: ${d.value}`, '',
  ].join('\n');
  return { id: `Fact/${name}`, file, text };
}

const ready = (d) => (d.kind === 'retract' ? !!factOf(d.target) : !!(d.agent && d.entity && d.predicate && d.value));

// The same statements `mempr add-fact` and `mempr retract` run, in src/mempr/memory.py.
const ADD_FACT =
  'MERGE (e:Entity {name: $entity}) MERGE (a:Agent {name: $agent}) MERGE (f:Fact {name: $name}) ' +
  "SET f.predicate = $predicate, f.value = $value, f.status = 'active', f.body = $statement " +
  'MERGE (f)-[:ABOUT]->(e) MERGE (f)-[:ASSERTED_BY]->(a) RETURN f.name AS fact';
const ADD_ORPHAN =
  'MERGE (a:Agent {name: $agent}) MERGE (f:Fact {name: $name}) ' +
  "SET f.predicate = $predicate, f.value = $value, f.status = 'active', f.body = $statement " +
  'MERGE (f)-[:ASSERTED_BY]->(a) RETURN f.name AS fact';
const DROP_ABOUT = 'MATCH (:Fact {name: $name})-[r:ABOUT]->() DELETE r';
const ADD_SOURCE =
  'MATCH (f:Fact {name: $name}) MERGE (s:Source {name: $source}) SET s.uri = $uri MERGE (f)-[:SOURCE]->(s) RETURN s.name AS source';
const SOURCE_STATUS = "MATCH (s:Source {name: $source}) SET s.status = $status RETURN s.name AS source";
const SUPERSEDE = 'MATCH (f:Fact {name: $name}), (o:Fact {name: $other}) MERGE (f)-[:SUPERSEDES]->(o) RETURN o.name AS other';
const DERIVE = 'MATCH (f:Fact {name: $name}), (o:Fact {name: $other}) MERGE (f)-[:DERIVED_FROM]->(o) RETURN o.name AS other';
const RETRACT = "MATCH (f:Fact {name: $name}) SET f.status = 'retracted' RETURN f.name AS fact";

function applyDraft(db, d) {
  const name = factName(d);
  if (d.kind === 'retract') {
    for (const fact of [name, ...d.retract]) run(db, RETRACT, { name: fact });
    return;
  }
  // Stating a fact again makes it current: nothing that replaced it still does.
  run(db, 'MATCH (:Fact)-[r:SUPERSEDES]->(:Fact {name: $name}) DELETE r', { name });
  const statement = `${d.entity} ${d.predicate}: ${d.value}`;
  const params = { name, entity: d.entity, agent: d.agent, predicate: d.predicate, value: d.value, statement };
  run(db, d.about === false ? ADD_ORPHAN : ADD_FACT, params);
  if (d.preserve_value) run(db, 'MATCH (f:Fact {name: $name}) SET f.preserve_value = true', { name });
  if (d.about === false) run(db, DROP_ABOUT, { name });
  if (d.source) run(db, ADD_SOURCE, { name, source: slug(d.source), uri: d.source });
  if (d.source_status) run(db, SOURCE_STATUS, { source: slug(d.source), status: d.source_status });
  for (const other of d.supersedes) run(db, SUPERSEDE, { name, other });
  for (const other of d.derived_from || []) {
    const linked = run(db, DERIVE, { name, other });
    if (!linked.rows.length) throw new Error(`no fact named ${other}`);
  }
  for (const other of d.retract) run(db, RETRACT, { name: other });
}

/** A draft as a pull request against the page's memory: the same kind of view as the scripted ones. */
function pullOf(raw) {
  const d = clean(raw);
  const title = d.kind === 'retract' ? `Retract ${d.target}` : `${d.entity} ${d.predicate}: ${d.value}`;
  const v = {
    pull: { id: `you/${d.agent || 'visitor'}`, author: d.agent, title: `memory: ${title}`, description: title, problems: [], rules: data.rules },
    short: 'yours', title, before: memory, after: memory, mine: true, draft: d,
    apply: (db) => applyDraft(db, d),
    notes: [noteOf(d)],
  };
  openView(v);
  return v;
}

/** The verdict on a draft, for the check that the page and `mempr check` agree. */
function reviewDraft(raw) {
  const v = pullOf(raw);
  const out = { blocked: verdict(v).blocked, rules: [...new Set(verdict(v).list.filter((p) => p.severity === 'block').map((p) => p.rule))].sort() };
  v.db.free();
  return out;
}

function openPull(entry) {
  const d = clean(play.draft);
  play.error = play.merged = '';
  if (!ready(d)) play.error = 'Fill in the agent, what it is about, what you learned and its value first.';
  else if (!relate) play.error = engine === 'starting' ? 'Relate is still loading. Try again in a moment.' : 'Relate could not start in this browser, so the review cannot run here.';
  if (play.error) return renderPlay();
  let v;
  try {
    v = pullOf(d);
  } catch (err) {
    play.error = `Relate refused this change: ${err.message}`;
    return renderPlay();
  }
  const old = play.view;
  if (old) {
    views.splice(views.indexOf(old), 1);
    old.db?.free();
  }
  views.push(v);
  play.view = v;
  play.draft = d;
  const mineOf = entry || play.mine.find((m) => m.view === old && !m.merged);
  if (mineOf) Object.assign(mineOf, { draft: d, view: v });
  else play.mine.push({ id: ++play.count, draft: d, view: v, merged: false });
  play.tab = 'graph';
  serverCheck(v);
  if (view === old) show(v);
  else renderViews();
  renderPlay({ fresh: true });
}

// The same draft is also sent to the server, where `mempr check` reviews it against the
// committed memory. The page keeps its own verdict if that second check is unavailable.
const API = apiBase(location.hostname, location.search);

if (API !== null) {
  // Say so before anything is sent: here a draft also goes to the server.
  const where = document.getElementById('where-it-runs');
  if (where) where.textContent = 'The review runs in this page. Your draft is also sent to this site\u2019s server for a second check. It is not stored.';
}

function serverCheck(v) {
  if (API === null || v.ruleError) return;
  const request = serverRequest(play.landed, v.draft);
  if (request.skip) {
    v.server = { state: 'skipped', why: request.skip };
    return;
  }
  const mine = verdict(v);
  const own = { blocked: mine.blocked, rules: mine.list.filter((p) => p.severity === 'block').map((p) => p.rule) };
  v.server = { state: 'checking' };
  const settle = (result) => {
    v.server = result;
    if (play.view === v) renderPlay();
  };
  void fetchServerReview(API, request.body, own).then(settle);
}

// A real pull request on GitHub, where this deployment's server can ask for one.
const github = { enabled: false };
if (API !== null) {
  fetch(`${API}/api/pull-requests`)
    .then((response) => (response.ok ? response.json() : null))
    .then((body) => {
      github.enabled = Boolean(body && body.enabled === true);
      if (github.enabled && play.view) renderPlay();
    })
    .catch(() => {}); // no server: the button stays hidden
}

function openOnGithub(v) {
  if (!github.enabled || v.ruleError || (v.github && !again(v.github)) || whyNot(v.draft, play.landed, v.edited)) return;
  const show = (state) => {
    v.github = state;
    if (play.view === v) renderPlay();
  };
  show({ state: 'sending' });
  void submitPullRequest(API, v.draft).then((state) => {
    show(state);
    if (state.state === 'queued') void watchPullRequest(API, state.id, show);
  });
}

function githubHtml(v) {
  if (!github.enabled) return '';
  const line = prLine(v.github);
  if (line) {
    const link = line.link ? ` <a href="${esc(line.link)}" target="_blank" rel="noopener">${esc(line.label)}</a>` : '';
    return `<p class="server github ${line.tone}" role="status">${esc(line.text)}${link}</p>`;
  }
  const why = whyNot(v.draft, play.landed, v.edited);
  if (why) return `<p class="server github">${esc(why)}</p>`;
  return '<p class="server github">Open this draft as a real pull request on GitHub. The draft is sent to this site\u2019s server and becomes public there, and it is closed after 30 minutes.</p>';
}

function serverLineHtml(v) {
  const line = serverLine(v.server, v.edited);
  return line ? `<p class="server ${line.tone}">${esc(line.text)}</p>` : '';
}

function mergePull() {
  const v = play.view;
  if (!v || verdict(v).blocked) return;
  memory = { nodes: v.graph.nodes, rels: v.graph.rels };
  const main = views[0];
  main.db?.free();
  Object.assign(main, { before: memory, after: memory, db: null, graph: null, live: null, edited: false });
  openView(main);
  views.splice(views.indexOf(v), 1);
  v.db?.free();
  play.mine.find((m) => m.view === v).merged = true;
  play.view = null;
  play.merged = v.title;
  play.landed.push({ draft: v.draft, edited: Boolean(v.edited) }); // the server replays these before it reviews the next draft
  play.draft = blank(v.draft.agent);
  play.tab = 'note';
  if (view === v) show(main);
  else {
    if (view === main) show(main);
    renderViews();
  }
  renderPlay({ fresh: true });
}

function resetMemory() {
  const main = views[0];
  if (play.view) {
    views.splice(views.indexOf(play.view), 1);
    play.view.db?.free();
  }
  memory = data.memory;
  main.db?.free();
  Object.assign(main, { before: memory, after: memory, db: null, graph: null, live: null, edited: false });
  if (relate) openView(main);
  else main.graph = memory;
  Object.assign(play, { draft: blank(), view: null, tab: 'note', mine: [], count: 0, error: '', merged: '', landed: [] });
  show(view.mine ? main : view);
  renderPlay();
}

/** One-click fixes that edit the draft; the review then runs again. */
function draftFixes(p, d) {
  const row = p.row || {};
  const own = factName(d);
  if (p.rule === 'contradiction') {
    const old = row.fact === own ? row.other : row.fact;
    return [
      ['Replace the old fact', (x) => x.supersedes.push(old)],
      ['Retract the old fact', (x) => x.retract.push(old)],
    ];
  }
  if (p.rule === 'unsourced-fact' && row.fact === own) return [['Add a source', (x) => (x.source = sourceFor(x.entity))]];
  if (p.rule === 'stale-derivation') {
    const stale = verdict(play.view).list.filter((q) => q.rule === 'stale-derivation').map((q) => q.row.fact);
    return [[stale.length > 1 ? `Retract the ${stale.length} facts that rest on it` : 'Retract the fact that rests on it', (x) => x.retract.push(...stale.filter((f) => !x.retract.includes(f)))]];
  }
  return [];
}

const sources = () => memory.nodes.filter((n) => n.label === 'Source' && n.props.uri).map((n) => n.props.uri);
const sourceFor = (entity) => sources().find((uri) => uri.includes(`/${entity}/`)) || sources()[0] || 'docs/notes.md';

/** A short name for a source file: its own name, or its folder when several files share the name. */
function sourceLabel(uri, all) {
  const parts = uri.split('/');
  const file = parts.at(-1);
  return parts.length > 1 && all.filter((u) => u.split('/').at(-1) === file).length > 1 ? parts.at(-2) : file;
}

/** Facts the draft could replace: the current ones for the same entity and predicate. */
function rivals(d) {
  const about = new Set(memory.rels.filter((r) => r.type === 'ABOUT' && r.target === `Entity/${d.entity}`).map((r) => r.source));
  // As in recall: a fact is replaced only while the fact that replaced it is still active.
  const active = new Set(memory.nodes.filter((n) => n.props.status === 'active').map((n) => n.id));
  const replaced = new Set(memory.rels.filter((r) => r.type === 'SUPERSEDES' && active.has(r.source)).map((r) => r.target));
  return memory.nodes.filter(
    (n) => about.has(n.id) && !replaced.has(n.id) && n.props.status === 'active' && n.props.predicate === d.predicate && n.props.name !== factName(d),
  );
}

function miniGraph(d, v) {
  const u = v ? union(v) : null;
  const known = new Map((u ? [...u.nodes.values()] : memory.nodes).map((n) => [n.id, n]));
  const own = `Fact/${factName(d)}`;
  const want = new Set([own, `Entity/${d.entity}`, `Agent/${d.agent}`, ...d.supersedes.map((f) => `Fact/${f}`), ...d.retract.map((f) => `Fact/${f}`)]);
  if (d.source) want.add(`Source/${slug(d.source)}`);
  if (d.kind === 'retract') {
    want.clear();
    want.add(own);
    d.retract.forEach((f) => want.add(`Fact/${f}`));
    memory.rels.filter((r) => r.source === own || (r.target === own && r.type !== 'ABOUT')).forEach((r) => want.add(r.source).add(r.target));
  }
  const first = v && verdict(v).list.find((p) => p.severity === 'block');
  for (const p of v ? verdict(v).list : []) p.nodes.forEach((id) => want.add(id));
  const label = (id) => id.split('/')[0];
  const nodes = [...want].filter((id) => !id.endsWith('/')).map((id) => {
    const n = known.get(id);
    if (n) return u ? n : { ...n, state: id === own && d.kind === 'retract' ? 'changed' : 'same', old: d.retract.includes(n.props.name) || d.supersedes.includes(n.props.name) || (id === own && d.kind === 'retract') };
    const props = id === own ? { name: factName(d), predicate: d.predicate, value: d.value } : { name: id.slice(id.indexOf('/') + 1), uri: d.source };
    return { id, label: label(id), props, state: 'added' };
  });
  const drafted = d.kind === 'retract' ? [] : [
    { source: own, type: 'ABOUT', target: `Entity/${d.entity}` },
    { source: own, type: 'ASSERTED_BY', target: `Agent/${d.agent}` },
    ...(d.source ? [{ source: own, type: 'SOURCE', target: `Source/${slug(d.source)}` }] : []),
    ...d.supersedes.map((f) => ({ source: own, type: 'SUPERSEDES', target: `Fact/${f}` })),
  ].map((r) => ({ ...r, state: 'added' }));
  const rels = u ? u.rels : [...memory.rels.map((r) => ({ ...r, state: 'same' })).filter((r) => !drafted.some((x) => relKey(x) === relKey(r))), ...drafted];
  const box = $('mini');
  if (!mini) {
    mini = cytoscape({ container: box, style: STYLE, minZoom: 0.3, maxZoom: 2, boxSelectionEnabled: false, userZoomingEnabled: false, userPanningEnabled: false, autoungrabify: true });
    new ResizeObserver(fitMini).observe(box);
  }
  mini.elements().remove();
  mini.add(elementsOf(nodes, rels));
  if (first) {
    const path = new Set(first.rels.map((r) => r.join('|')));
    mini.nodes().forEach((n) => first.nodes.includes(n.id()) && n.addClass('hl'));
    mini.edges().forEach((e) => path.has(e.id()) && e.addClass('hl'));
  }
  // Two columns: the new fact with its source and author on the left, what it is about and
  // the facts it meets on the right.
  const entity = `Entity/${d.entity}`;
  const side = { [`Source/${slug(d.source)}`]: [-105, -92], [own]: [-105, 0], [`Agent/${d.agent}`]: [-105, 96], [entity]: [110, 0] };
  const others = mini.nodes().filter((n) => !side[n.id()]);
  const facts = others.filter('[kind = "Fact"]');
  mini.nodes().forEach((n) => side[n.id()] && n.position({ x: side[n.id()][0], y: side[n.id()][1] }));
  facts.forEach((n, i) => n.position({ x: 110, y: 78 + i * 52 }));
  others.difference(facts).forEach((n, i) => n.position({ x: -105, y: 180 + i * 56 }));
  fitMini();
}

/** Frame the small graph: everything while its labels stay readable, otherwise what changed. */
function fitMini() {
  mini.resize();
  mini.fit(undefined, 22);
  if (mini.zoom() >= 0.85) return void (mini.zoom() > 1.3 && (mini.zoom(1.3), mini.center()));
  const changed = mini.elements('.added, .changed, .hl, .old');
  mini.zoom(0.85);
  mini.center(changed.length ? changed.union(changed.connectedNodes()) : undefined);
}

function renderPlay({ fresh = false } = {}) {
  if (!data || !$('draft')) return;
  if (!play.draft) play.draft = blank();
  const d = play.draft;
  const c = clean(d);
  const set = (id, value) => { if (document.activeElement !== $(id) && $(id).value !== value) $(id).value = value; };
  set('d-agent', d.agent); set('d-entity', d.entity); set('d-predicate', d.predicate); set('d-value', d.value);
  const retracting = d.kind === 'retract';
  for (const id of ['d-entity', 'd-predicate', 'd-value']) $(id).disabled = retracting;
  const chip = (attr, value, text, on) => `<button type="button" class="chip-pick ${on ? 'on' : ''}" ${attr}="${esc(value)}" title="${esc(value)}">${esc(text)}</button>`;
  const agents = [...new Set(['visitor', ...memory.nodes.filter((n) => n.label === 'Agent').map((n) => n.props.name)])];
  $('d-agents').innerHTML = agents.map((a) => chip('data-agent', a, a, c.agent === a)).join('');
  $('d-entities').innerHTML = memory.nodes.filter((n) => n.label === 'Entity').map((n) => `<option value="${esc(n.props.name)}">`).join('');
  const preds = [...new Set([...memory.nodes.filter((n) => n.label === 'Fact' && n.props.predicate).map((n) => n.props.predicate), ...PREDICATES])].sort();
  $('d-predicates').innerHTML = preds.map((x) => `<option value="${esc(x)}">`).join('');
  const uris = [...new Set([...sources(), ...(c.source ? [c.source] : [])])];
  $('d-sources').innerHTML = retracting ? '<span class="quiet">A retraction keeps the fact and its source, and marks it retracted.</span>'
    : uris.map((u) => chip('data-source', u, sourceLabel(u, uris), c.source === u)).join('') + chip('data-source', '', 'No source', !c.source);
  const options = rivals(c);
  $('d-replaces-field').hidden = retracting || !options.length;
  $('d-replaces').innerHTML = '<option value="">Nothing. This is a new fact.</option>' +
    options.map((n) => `<option value="${esc(n.props.name)}" ${c.supersedes.includes(n.props.name) ? 'selected' : ''}>${esc(n.props.value)} (Fact/${esc(n.props.name)})</option>`).join('');
  $('d-also').hidden = !c.retract.length && !retracting;
  $('d-also').innerHTML = retracting ? `This pull request retracts ${[c.target, ...c.retract].map((f) => `<code>Fact/${esc(f)}</code>`).join(', ')}.`
    : `Also retracts ${c.retract.map((f) => `<code>Fact/${esc(f)}</code>`).join(', ')}.`;
  const button = $('open-pull');
  button.textContent = engine === 'starting' ? 'Relate is loading' : play.view ? 'Review again' : 'Open pull request';
  button.classList.toggle('loading', engine === 'starting');
  button.setAttribute('aria-disabled', String(!relate));

  const note = ready(c) ? noteOf(c) : null;
  $('note-path').textContent = note ? note.file : 'memory/Fact/';
  $('note').classList.toggle('empty', !note);
  if (note) $('note').textContent = note.text;
  else {
    $('note').innerHTML =
      '<b>The note your pull request writes appears here. Fill in the form, or start from an example.</b>' +
      esc('---\ntype: Fact\nname: …\npredicate: …\nstatus: active\nvalue: …\nABOUT:\n  - "[[Entity/…]]"\nASSERTED_BY:\n  - "[[Agent/…]]"\nSOURCE:\n  - "[[Source/…]]"\n---');
  }
  document.querySelectorAll('.tabs [data-tab]').forEach((t) => t.classList.toggle('on', t.dataset.tab === play.tab));
  $('note').hidden = play.tab !== 'note';
  $('mini').hidden = play.tab !== 'graph';
  if (play.tab === 'graph' && ready(c)) {
    const current = play.view && JSON.stringify(play.view.draft) === JSON.stringify(c) ? play.view : null;
    miniGraph(c, current);
    if (fresh && current) enliven({ on: mini });
  } else if (mini) mini.elements().remove();

  $('starters').innerHTML = play.starters.map((s, i) => `<button type="button" data-starter="${i}">${esc(s.starter)}</button>`).join('');
  $('outcome').innerHTML = outcomeHtml(c);
  $('outcome').classList.remove('play');
  if (fresh) {
    void $('outcome').offsetWidth; // restart the verdict's stamp
    $('outcome').classList.add('play');
    // On a narrow screen the verdict is below the form: bring it into view.
    const box = $('outcome').getBoundingClientRect();
    if (box.top > innerHeight * 0.75 || box.top < 60) $('outcome').scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth', block: 'start' });
  }
  $('mine').innerHTML = play.mine.length || memory !== data.memory
    ? '<span class="label">Your pull requests</span>' +
      play.mine.map((m) => {
        const state = m.merged ? 'merged' : verdict(m.view).blocked ? 'blocked' : 'passed';
        const text = { merged: 'Merged', blocked: 'Blocked', passed: 'Passes' }[state];
        return `<button type="button" class="mine-item ${m.view === play.view ? 'on' : ''}" data-mine="${m.id}" ${m.merged ? 'disabled' : ''}>` +
          `<span class="chip-state ${state === 'merged' ? 'base' : state}">${text}</span>${esc(m.view.title)}</button>`;
      }).join('') +
      '<button type="button" class="mine-reset" data-reset-memory>Reset memory</button>'
    : '';
}

function outcomeHtml(c) {
  const v = play.view;
  if (play.error) return `<p class="outcome-empty error">${esc(play.error)}</p>`;
  if (play.merged) {
    return `<div class="stampbox passed"><strong>Merged</strong><span>${esc(play.merged)}</span></div>` +
      '<p class="adds">It is part of the shared memory now. Your next pull request is reviewed against it, so you can contradict yourself.</p>';
  }
  if (!v) return '<p class="outcome-empty">The review appears here: what the pull request adds, each problem with its path, and the verdict.</p>';
  if (v.ruleError) return `<p class="outcome-empty error">Review incomplete. ${esc(v.ruleError)}</p>`;
  const stale = JSON.stringify(v.draft) !== JSON.stringify(c);
  const { list, blocked, blocking } = verdict(v);
  const u = union(v);
  const added = [...u.nodes.values()].filter((n) => n.state === 'added').length;
  const links = u.rels.filter((r) => r.state === 'added').length;
  const changed = [...u.nodes.values()].filter((n) => n.state === 'changed').length;
  const problems = list.map((p, i) => {
    const fixes = stale ? [] : draftFixes(p, v.draft);
    return `<li class="problem ${p.severity}"><h4>${esc(p.title)}${p.severity === 'warn' ? ' <em>warning</em>' : ''}</h4><p>${md(p.text)}</p>` +
      (p.rule ? cypherBlock(p.rule, rulesOf(v)) : '') +
      (fixes.length ? `<div class="actions">${fixes.map(([label], f) => `<button type="button" data-draft-fix="${i}:${f}">${esc(label)}</button>`).join('')}</div>` : '') + '</li>';
  }).join('');
  return (
    `<div class="stampbox ${blocked ? 'blocked' : 'passed'}"><strong>${blocked ? 'Merge blocked' : 'Review passed'}</strong>` +
    `<span>${blocked ? plural(blocking, 'problem', 'problems') : 'Safe to merge'}</span></div>` +
    `<p class="adds">Memory review of <code>${esc(v.pull.id)}</code>. Adds ${plural(added, 'note', 'notes')} and ${plural(links, 'link', 'links')}` +
    `${changed ? `, changes ${plural(changed, 'note', 'notes')}` : ''}. Relate ran the ${rulesOf(v).length} rules that <code>mempr check</code> runs.</p>` +
    (problems ? `<ol class="problems">${problems}</ol>` : '') +
    (stale ? '<p class="adds">You changed the draft. Review it again.</p>' : serverLineHtml(v) + githubHtml(v)) +
    '<div class="outcome-actions">' +
    (!stale && github.enabled && (!v.github || again(v.github)) && !whyNot(v.draft, play.landed, v.edited) ? '<button type="button" class="btn" data-open-github>Open it on GitHub</button>' : '') +
    (!blocked && !stale ? '<button type="button" class="btn primary" data-merge-pull>Merge into memory</button>' : '') +
    '<a class="btn" href="#explorer" data-open="yours">See it on the full graph</a></div>'
  );
}

function wirePlay() {
  const form = $('draft');
  if (!form) return;
  form.addEventListener('input', (event) => {
    const d = play.draft;
    const name = event.target.name;
    if (name === 'replaces') d.supersedes = event.target.value ? [event.target.value] : [];
    else if (name in d) d[name] = event.target.value;
    if (name === 'entity' || name === 'predicate') d.supersedes = [];
    play.error = play.merged = '';
    renderPlay();
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    openPull();
  });
}

/** Clicks in the playground and on a fact's Replace and Retract buttons. */
function playClick(t) {
  const d = t.dataset;
  const go = (draft, { open = false, focus = '' } = {}) => {
    Object.assign(play, { draft, error: '', merged: '' });
    if (open) openPull();
    else renderPlay();
    if (focus) {
      $('write').scrollIntoView();
      $(focus).focus({ preventScroll: true });
    }
  };
  if (d.starter !== undefined) go({ ...blank(), ...structuredClone(play.starters[Number(d.starter)].draft) }, { open: !!relate });
  else if (d.agent !== undefined) go({ ...play.draft, agent: d.agent });
  else if (d.source !== undefined) go({ ...play.draft, source: d.source });
  else if (d.tab !== undefined) { play.tab = d.tab; renderPlay(); }
  else if (t.id === 'clear-draft') { play.tab = 'note'; go(blank(play.draft.agent)); }
  else if (d.draftFix !== undefined) {
    const [i, f] = d.draftFix.split(':').map(Number);
    const next = clean(play.view.draft);
    draftFixes(verdict(play.view).list[i], next)[f][1](next);
    go(next, { open: true });
  } else if (d.mergePull !== undefined) mergePull();
  else if (d.openGithub !== undefined) openOnGithub(play.view);
  else if (d.resetMemory !== undefined) resetMemory();
  else if (d.mine !== undefined) {
    const entry = play.mine.find((m) => m.id === Number(d.mine));
    play.draft = clean(entry.draft);
    openPull(entry);
  } else if (d.replaceFact !== undefined || d.retractFact !== undefined) {
    const n = factOf(d.replaceFact ?? d.retractFact);
    const entity = (memory.rels.find((r) => r.source === n.id && r.type === 'ABOUT') || { target: '/' }).target.split('/')[1];
    const source = memory.rels.find((r) => r.source === n.id && r.type === 'SOURCE');
    const uri = source && memory.nodes.find((m) => m.id === source.target)?.props.uri;
    selectNode(null);
    play.tab = 'note';
    if (d.retractFact !== undefined) go({ ...blank(play.draft?.agent), kind: 'retract', target: n.props.name, entity, predicate: n.props.predicate, value: String(n.props.value) }, { focus: 'open-pull' });
    else go({ ...blank(play.draft?.agent), entity, predicate: n.props.predicate, source: uri || '', supersedes: [n.props.name] }, { focus: 'd-value' });
  } else return false;
  return true;
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
  enliven();
  renderViews();
  renderReview();
  $('review').classList.remove('swap');
  void $('review').offsetWidth; // restart the verdict's transition
  $('review').classList.add('swap');
  renderDetail();
  renderTarget();
  renderConsole(); // the rules on offer are the ones this view is reviewed with
  $('result').innerHTML = '';
  $('timing').textContent = '';
  $('graph-note').textContent = v.pull
    ? 'Green: added by the pull request. Red dashed: gone after the merge. Solid red: the problem selected on the right.'
    : 'Drag to move, scroll or pinch to zoom, click a node for its source and author.';
  if (!keepHash) history.replaceState(null, '', `#pr=${encodeURIComponent(v.pull ? v.short : 'main')}`);
}

function selectProblem(i) {
  const p = verdict(view).list[i];
  problem = p === problem ? null : p;
  paint();
  enliven({ changes: false });
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
    else if (d.rule !== undefined) setQuery(rulesOf(view).find((r) => r.id === d.rule).cypher.trim());
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
    else if (playClick(t)) return;
    else if (d.problem !== undefined && !event.target.closest('details')) selectProblem(Number(d.problem));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches?.('[data-problem]')) selectProblem(Number(event.target.dataset.problem));
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && event.target === $('query')) runQuery();
  });
  document.addEventListener('click', (event) => {
    // Links to a section scroll there without replacing `#pr=…`, which names the open graph.
    const jump = event.target.closest?.('a[href^="#"]');
    // A modified click opens the link in a new tab or window: leave it to the browser.
    if (!jump || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const v = views.find((x) => x.pull && x.short === jump.dataset.open);
    if (v && v !== view) show(v);
    const section = document.getElementById(jump.getAttribute('href').slice(1));
    if (!section) return;
    event.preventDefault();
    section.scrollIntoView();
  });
  const searchForm = $('search-form');
  if (searchForm) searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    runSearch($('search-q').value);
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'rules' && event.target.value) {
      setQuery(rulesOf(view).find((r) => r.id === event.target.value).cypher.trim());
      event.target.value = '';
    }
    if (event.target.id === 'show-prov') paint();
  });
  $('run').addEventListener('click', runQuery);
  wirePlay();
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
    renderPlay();
  });
  $('views').addEventListener('pointermove', (event) => {
    const card = event.target.closest('button');
    if (!card) return;
    const box = card.getBoundingClientRect();
    card.style.setProperty('--px', `${event.clientX - box.left}px`);
    card.style.setProperty('--py', `${event.clientY - box.top}px`);
  }, { passive: true });
  const touch = () => ($('touch').hidden = !coarse.matches);
  coarse.addEventListener('change', touch);
  touch();
  touchGraph($('touch'));
  cy.on('tap', 'node', (event) => selectNode(event.target.id()));
  cy.on('tap', (event) => event.target === cy && selectNode(null));
}

/**
 * Touch handling for the graph. The layer over the canvas lets a vertical swipe scroll the
 * page (`touch-action: pan-y`), so the graph never traps the scroll. A sideways drag moves
 * the graph, two fingers zoom and move it, and a tap picks the nearest node.
 */
function touchGraph(el) {
  const points = new Map();
  let start = null, moved = false, pinch = null;
  const at = (e) => {
    const box = el.getBoundingClientRect();
    return { x: e.clientX - box.left, y: e.clientY - box.top };
  };
  el.addEventListener('pointerdown', (e) => {
    points.set(e.pointerId, at(e));
    pinch = null;
    if (points.size === 1) [start, moved] = [at(e), false];
    cy.stop();
  });
  el.addEventListener('pointermove', (e) => {
    if (!points.has(e.pointerId)) return;
    const prev = points.get(e.pointerId), now = at(e);
    points.set(e.pointerId, now);
    if (points.size === 1) {
      if (Math.hypot(now.x - start.x, now.y - start.y) > 8) moved = true;
      if (moved) cy.panBy({ x: now.x - prev.x, y: now.y - prev.y });
    } else if (points.size === 2) {
      const [a, b] = [...points.values()];
      const next = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
      if (pinch) {
        cy.zoom({ level: (cy.zoom() * next.d) / pinch.d, renderedPosition: next.mid });
        cy.panBy({ x: next.mid.x - pinch.mid.x, y: next.mid.y - pinch.mid.y });
      }
      pinch = next;
      moved = true;
    }
  });
  const end = (e) => {
    const had = points.delete(e.pointerId);
    pinch = null;
    if (had && e.type === 'pointerup' && !moved && !points.size) tapAt(start);
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
}

/** Select the node nearest to a tap. Every node counts as at least 44 px wide and tall. */
function tapAt(p) {
  let best = null, gap = Infinity;
  cy.nodes(':visible').forEach((n) => {
    const b = n.renderedBoundingBox({ includeLabels: false });
    const x = (b.x1 + b.x2) / 2, y = (b.y1 + b.y2) / 2;
    if (Math.abs(p.x - x) > Math.max(b.w, 44) / 2 || Math.abs(p.y - y) > Math.max(b.h, 44) / 2) return;
    const d = Math.hypot(p.x - x, p.y - y);
    if (d < gap) [best, gap] = [n, d];
  });
  selectNode(best ? best.id() : null);
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
    engine = 'ready';
    $('engine').textContent = 'Relate is running in this page';
    $('engine').classList.add('ok');
  } catch (err) {
    console.error(err);
    engine = 'off';
    $('engine').textContent = 'Relate could not start: showing the exported review';
    $('engine').classList.add('off');
  }
}

const AGENT_RECALL = `MATCH (f:Fact)-[:ABOUT]->(:Entity {name: 'payments-api'})
WHERE f.predicate = 'database' AND f.status = 'active'
OPTIONAL MATCH (f)-[:SOURCE]->(s:Source)
OPTIONAL MATCH (f)-[:SUPERSEDES]->(old:Fact)
RETURN f.value AS database, s.uri AS source, old.value AS replaced`;

function wireAgentComparison(starting) {
  const section = $('agent-comparison');
  const trial = $('agent-trial');
  const button = $('agent-recall');
  const status = $('agent-recall-status');
  const cards = [...section.querySelectorAll('[data-agent-run]')];
  $('agent-recall-query').textContent = AGENT_RECALL;
  function showTrial() {
    section.dataset.trial = trial.value;
    for (const variant of section.querySelectorAll('[data-agent-trial]')) {
      variant.hidden = variant.dataset.agentTrial !== trial.value;
    }
    for (const card of cards) {
      const output = card.querySelector('.agent-recall');
      output.hidden = true;
      output.replaceChildren();
    }
    status.textContent = '';
  }
  showTrial();
  trial.addEventListener('change', showTrial);
  button.addEventListener('click', async () => {
    button.disabled = true;
    trial.disabled = true;
    status.textContent = 'Running the recall on both published memory snapshots.';
    try {
      await starting;
      if (!relate) throw new Error('Relate could not start. The original check output is linked above.');
      const file = trial.value === 'fresh' ? 'agent-comparison-fresh.json' : 'agent-comparison.json';
      const response = await fetch(`data/${file}`);
      if (!response.ok) throw new Error('The archived memory snapshots could not load.');
      const snapshots = await response.json();
      const results = cards.map((card) => {
        const snapshot = snapshots[card.dataset.agentRun];
        const db = loadDb(snapshot.graph);
        try {
          const rows = run(db, AGENT_RECALL).rows;
          if (rows.length !== 1) throw new Error('The snapshot did not return one current database fact.');
          return { card, snapshot, ...rows[0] };
        } finally {
          db.free();
        }
      });
      for (const { card, snapshot, database, source, replaced } of results) {
        const output = card.querySelector('.agent-recall');
        const uri = new URL(source, snapshot.url + '/');
        const link = document.createElement('a');
        link.href = uri.href;
        link.textContent = uri.pathname.split('/').at(-1);
        output.replaceChildren(`Relate recalls ${database}. `, 'Source: ', link);
        if (replaced) output.append(`. Replaces ${replaced}.`);
        output.hidden = false;
      }
      status.textContent = 'Relate ran the same Cypher on both snapshots in this browser.';
    } catch (err) {
      status.textContent = err.message;
    } finally {
      button.disabled = false;
      trial.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- real repository

const REAL_PICKS = ['GoogleCloudPlatform/microservices-demo', 'open-telemetry/opentelemetry-demo', 'tokio-rs/tokio'];
const real = { body: null, fixed: false, view: null, busy: false, error: '' };
const short7 = (sha) => esc(String(sha).slice(0, 7));
const day = (iso) => esc(String(iso).slice(0, 10));
const UNCONFIRMED_REASON = {
  'source-removed': 'Source file removed',
  'source-unread': 'Source file outside the scan limits',
  'fact-not-found': 'Source file read, value no longer found',
};

function realView(b, fixed) {
  const side = fixed ? b.fixed : b;
  return {
    pull: {
      id: 'agent/builder',
      author: 'builder',
      description: `read ${b.repo.split('/')[1]} at ${String(b.head.sha).slice(0, 7)}`,
      title: `memory: ${b.repo} at its latest commit`,
      problems: side.problems,
      review: side.review,
      comment: side.comment,
    },
    short: b.repo.split('/')[1],
    title: `${b.repo}, learned ${b.old.date.slice(0, 10)}, read ${b.head.date.slice(0, 10)}`,
    before: b.before,
    after: fixed ? b.fixed_after : b.after,
    real: true,
    unconfirmed: b.unconfirmed || [],
  };
}

const COUNT_WORDS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine'];

function pullHeading(andReal) {
  const n = data.pulls.length;
  const word = COUNT_WORDS[n] || String(n);
  const agents = `${word} pull request${n === 1 ? '' : 's'} from ${word.toLowerCase()} agent${n === 1 ? '' : 's'}`;
  return andReal ? `${agents}, and one real repository` : agents;
}

function exploreReal() {
  if (!real.body) return;
  const v = realView(real.body, real.fixed);
  if (real.view) {
    views.splice(views.indexOf(real.view), 1);
    real.view.db?.free();
  }
  views.push(v);
  real.view = v;
  $('explorer-title').textContent = pullHeading(true);
  show(v);
  $('explorer').scrollIntoView({ behavior: calm.matches ? 'auto' : 'smooth' });
}

function renderReal() {
  const out = $('real-out');
  const go = $('real-go');
  go.classList.toggle('loading', real.busy);
  go.setAttribute('aria-disabled', String(real.busy));
  if (real.busy) {
    out.innerHTML = '<p class="outcome-empty">Reading the files on GitHub at both commits, then writing two memories and reviewing them with Relate.</p>';
    return;
  }
  if (real.error) {
    out.innerHTML = `<p class="real-error">${esc(real.error)}</p>`;
    return;
  }
  const b = real.body;
  if (!b) {
    out.innerHTML = '';
    return;
  }
  const side = real.fixed ? b.fixed : b;
  const n = b.changed.length;
  const unconfirmed = b.unconfirmed || [];
  const state = reviewState(side.blocked, unconfirmed.length);
  const summary = side.blocked
    ? `${plural(side.review.problems, 'problem', 'problems')}: ${side.rules.join(', ')}`
    : unconfirmed.length ? plural(unconfirmed.length, 'unconfirmed fact', 'unconfirmed facts')
      : n ? 'every change says what it replaces' : 'no existing values changed';
  const rows = b.changed.map(
    (c, i) =>
      `<tr style="--i:${i}" class="${real.fixed ? 'fixed' : ''}"><td><code>${esc(c.entity)}</code><small class="what">${esc(c.predicate)}</small></td><td><code>${esc(c.predicate)}</code></td>` +
      `<td class="was"><a href="${esc(c.old_url)}" target="_blank" rel="noopener"><code>${esc(c.old)}</code></a></td>` +
      `<td class="now"><a href="${esc(c.new_url)}" target="_blank" rel="noopener"><code>${esc(c.new)}</code></a></td></tr>`,
  );
  for (const c of unconfirmed) {
    rows.push(
      `<tr style="--i:${rows.length}"><td><code>${esc(c.entity)}</code><small class="what">${esc(c.predicate)}</small></td><td><code>${esc(c.predicate)}</code></td>` +
      `<td><a href="${esc(c.old_url)}" title="${esc(c.path)}" target="_blank" rel="noopener"><code>${esc(c.old)}</code></a></td>` +
      `<td>${esc(UNCONFIRMED_REASON[c.reason])}</td></tr>`,
    );
  }
  out.innerHTML =
    '<div class="real-side">' +
    `<div class="stampbox ${state}"><strong>${side.blocked ? 'Merge blocked' : unconfirmed.length ? 'Needs verification' : 'Review passed'}</strong>` +
    `<span>${esc(summary)}</span></div>` +
    `<p><strong>scout</strong> read <a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.repo)}</a> at ` +
    `<a href="${esc(b.old.url)}" target="_blank" rel="noopener"><code>${short7(b.old.sha)}</code></a> on ${day(b.old.date)} and wrote ${plural(b.learned, 'fact', 'facts')}. ` +
    `<strong>builder</strong> read <a href="${esc(b.head.url)}" target="_blank" rel="noopener"><code>${short7(b.head.sha)}</code></a>, its latest commit (${day(b.head.date)}), and proposed ${plural(b.proposed, 'fact', 'facts')}. ` +
    `${n ? `${n === b.proposed ? (n === 1 ? 'It changes' : 'Each changes') : `${n} of them change`} a value scout wrote.` : 'None of them changes a value scout wrote.'}</p>` +
    (n
      ? real.fixed
        ? '<p>Each new fact now links the one it replaces with <code>SUPERSEDES</code>, so every agent reads one current value.</p>'
        : '<p>Merged as is, the memory would hold two active values for each of these, and agents would read whichever came first.</p>'
      : '') +
    '<div class="outcome-actions">' +
    (n ? `<button type="button" class="btn" id="real-fix">${real.fixed ? 'Undo' : 'Mark them as replacing the old facts'}</button>` : '') +
    '<button type="button" class="btn primary" id="real-explore">Explore the graph</button></div></div>' +
    (rows.length
      ? `<table class="moved"><thead><tr><th>About</th><th>What</th><th>${day(b.old.date)}</th><th>${day(b.head.date)}</th></tr></thead><tbody>${rows.join('')}</tbody></table>`
      : `<p class="outcome-empty">Read ${plural(b.files.head, 'file', 'files')} (Dockerfiles, package.json, go.mod, pyproject.toml, Cargo.toml, .nvmrc). Try a different starting point or another repository.</p>`);
  out.classList.remove('play');
  void out.offsetWidth;
  out.classList.add('play');
  $('real-fix')?.addEventListener('click', () => {
    real.fixed = !real.fixed;
    renderReal();
    if (real.view && view === real.view) exploreReal();
  });
  $('real-explore').addEventListener('click', exploreReal);
}

async function learnReal(repo, period) {
  if (API === null) {
    real.error = 'This copy of the page has no server. Open https://memory-pr.vercel.app/ to read a repository.';
    return renderReal();
  }
  const ask = (real.ask = (real.ask || 0) + 1);
  Object.assign(real, { busy: true, error: '', fixed: false });
  renderReal();
  try {
    const query = new URLSearchParams({ repo });
    if (period === 'previous') query.set('baseline', 'previous');
    else query.set('months', period);
    const response = await fetch(`${API}/api/learn?${query}`);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `the server answered ${response.status}`);
    if (ask === real.ask) real.body = body;
  } catch (err) {
    if (ask === real.ask) real.error = `Could not read ${repo}: ${err.message}`;
  }
  if (ask !== real.ask) return;
  real.busy = false;
  renderReal();
}

function wireReal() {
  $('real-picks').innerHTML = REAL_PICKS.map((r) => `<button type="button" data-repo="${esc(r)}">${esc(r)}</button>`).join('');
  $('real-picks').addEventListener('click', (event) => {
    const pick = event.target.closest('[data-repo]');
    if (!pick) return;
    $('real-repo').value = pick.dataset.repo;
    learnReal(pick.dataset.repo, $('real-months').value);
  });
  $('real-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const repo = $('real-repo').value.trim().replace(/^https?:\/\/github\.com\//, '').replace(/\.git$|\/$/g, '');
    if (repo) learnReal(repo, $('real-months').value);
  });
}

async function main() {
  renderLegend();
  // The graph is drawn from the committed data straight away. Relate is a large download,
  // so it attaches when it arrives and then runs the review and the console.
  const starting = startRelate();
  wireAgentComparison(starting);
  data = await (await fetch('data/demo.json')).json();
  memory = data.memory;
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
  cy = cytoscape({ container: $('cy'), style: STYLE, minZoom: 0.1, maxZoom: 3, boxSelectionEnabled: false });
  window.mempr = { cy, views, review: reviewDraft }; // for the curious, and for the check that the page agrees with mempr check
  wire();
  wireReal();
  renderConsole();
  // Open every graph up front so each tab shows its verdict from Relate.
  views.forEach((v) => {
    openView(v);
    if (!v.graph) v.graph = v.after;
  });
  view = views[0];
  draw(); // lay out the memory first, so its notes keep their place in every view
  const wanted = decodeURIComponent((location.hash.match(/pr=([^&]+)/) || [])[1] || '');
  // Open on the proof: the clean merge that silently drops links, else any blocked pull.
  const first =
    views.find((v) => v.pull && problemsOf(v).some((x) => x.kind === 'dropped')) ||
    views.find((v) => v.pull && verdict(v).blocked) ||
    views[0];
  show(views.find((v) => (v.pull ? v.short : 'main') === wanted) || first, { keepHash: true });
  renderProof(first);
  $('explorer-title').textContent = pullHeading(false);
  new ResizeObserver(() => {
    cy.resize();
    paint({ glide: false });
  }).observe($('cy'));
  renderPlay();
  fetch('data/playground-cases.json')
    .then((r) => r.json())
    .then((file) => {
      play.starters = file.cases.filter((c) => c.starter);
      renderPlay();
    })
    .catch(() => {}); // the starters are a convenience; the form works without them
  await starting;
  renderPlay();
  if (!relate) return renderTarget();
  views.forEach((v) => openView(v));
  // The review now comes from Relate: point the graph at its finding, not the exported copy.
  const now = problem && problemsOf(view).find((p) => p.rule === problem.rule && p.severity === problem.severity);
  if (now) {
    problem = now;
    paint({ fit: false });
  }
  renderViews();
  renderReview();
  renderDetail();
  renderTarget();
}

main().catch((err) => {
  console.error(err);
  $('review').innerHTML = `<p class="error">Could not load the demo data: ${esc(err.message)}. Serve this folder over HTTP, for example <code>python -m http.server -d site</code>.</p>`;
});
