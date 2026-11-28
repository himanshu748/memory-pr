import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { chromium } from 'playwright';

// An in-repo API fixture keeps this browser test independent of changing upstream heads.
const repo = 'fixture/app';
const baseline = '1'.repeat(40);
const revision = '2'.repeat(40);
const old = { id: 'old', entity: 'api', predicate: 'go-version', value: '1.23', sha: baseline };
const fresh = { ...old, id: 'fresh', value: '1.25', sha: revision };
const added = { ...fresh, id: 'added', entity: 'worker', value: '1.26' };
const pending = { ...old, id: 'pending', entity: 'legacy' };
const source = (fact) => `https://github.com/${repo}/blob/${fact.sha}/${fact.entity}/go.mod`;

function graph(facts, fixed = false) {
  const nodes = new Map();
  const rels = [];
  function node(label, name, props) {
    const id = `${label}/${name}`;
    nodes.set(id, { id, label, props: { name, ...props } });
    return id;
  }
  for (const fact of facts) {
    const id = node('Fact', fact.id, {
      predicate: fact.predicate, value: fact.value, status: fact.status || 'active',
    });
    rels.push(
      { source: id, type: 'ABOUT', target: node('Entity', fact.entity) },
      { source: id, type: 'SOURCE', target: node('Source', fact.id, { uri: source(fact) }) },
    );
    if (fixed && fact === fresh) rels.push({ source: id, type: 'SUPERSEDES', target: 'Fact/old' });
  }
  return { nodes: [...nodes.values()], rels };
}

const commit = (sha, date) => ({ sha, date, url: `https://github.com/${repo}/commit/${sha}` });
const body = {
  repo, url: `https://github.com/${repo}`,
  old: commit(baseline, '2025-11-28'), head: commit(revision, '2026-11-28'),
  files: { old: 2, head: 2 }, files_skipped: { old: 0, head: 1 }, learned: 2, proposed: 2,
  changed: [{ entity: old.entity, predicate: old.predicate, old: old.value, new: fresh.value,
    old_url: source(old), new_url: source(fresh) }],
  unconfirmed: [{ entity: pending.entity, predicate: pending.predicate, old: pending.value,
    old_url: source(pending), path: 'legacy/go.mod', reason: 'source-unread' }],
  blocked: true, rules: ['contradiction'], review: { problems: 1 }, fixed: { blocked: false },
  before: graph([old, pending]),
  after: graph([old, { ...pending, status: 'unconfirmed' }, fresh, added]),
  fixed_after: graph([old, { ...pending, status: 'unconfirmed' }, fresh, added], true),
};

const site = new URL('../../site/', import.meta.url);
const types = { css: 'text/css', html: 'text/html', js: 'text/javascript', json: 'application/json',
  svg: 'image/svg+xml', wasm: 'application/wasm', woff2: 'font/woff2' };
const server = createServer(async (request, response) => {
  try {
    const { pathname } = new URL(request.url, 'http://127.0.0.1');
    if (pathname === '/api/learn') {
      response.writeHead(200, { 'Content-Type': types.json }).end(JSON.stringify(body));
      return;
    }
    if (!/^\/[a-z\d/._-]*$/i.test(pathname) || pathname.includes('..')) throw new Error('Invalid path');
    const file = pathname === '/' ? 'index.html' : pathname.slice(1);
    const data = await readFile(new URL(file, site));
    response.writeHead(200, { 'Content-Type': types[file.split('.').at(-1)] }).end(data);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
try {
  const browser = await chromium.launch({ executablePath: process.env.MEMPR_CHROMIUM });
  try {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({
        viewport: { width, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'],
      });
      try {
        const page = await context.newPage();
        await page.goto(`http://127.0.0.1:${server.address().port}/?api=1#real`);
        await page.getByRole('textbox', { name: 'Public GitHub repository' }).fill(repo);
        await page.getByRole('button', { name: 'Learn and review' }).click();
        const real = page.locator('#real');
        await real.getByText('Merge blocked', { exact: true }).waitFor();
        const handoff = real.getByText('Agent handoff', { exact: true });
        assert.equal(await handoff.count(), 0);
        await real.getByRole('button', { name: 'Mark them as replacing the old facts' }).click();
        await real.getByText('Needs verification', { exact: true }).waitFor();
        await handoff.click();
        const text = await real.getByRole('textbox', { name: 'Reviewed agent handoff' }).inputValue();
        const packet = JSON.parse(text.slice(text.indexOf('\n\n') + 2));
        assert.deepEqual(packet.changes.map((entry) => entry.current.source), [source(fresh)]);
        assert.deepEqual(packet.additions.map((entry) => entry.current.source), [source(added)]);
        assert.deepEqual(packet.unconfirmed.map((entry) => entry.previousSource), [source(pending)]);
        await real.getByRole('button', { name: 'Copy handoff' }).click();
        await real.getByText('Handoff copied.').waitFor();
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), text);
        await real.getByRole('button', { name: 'Undo', exact: true }).click();
        assert.equal(await handoff.count(), 0);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        console.log(`PASS ${width}px: blocked, partial, copied, and undo states`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
}
