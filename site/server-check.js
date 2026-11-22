// What the page asks the server and how it reads the answer. No DOM here, so
// tests/js/server-check.test.mjs can run it in node.

/** Where the review endpoint lives, or null when this copy of the page has no server. */
export function apiBase(hostname, search = '') {
  if (/\.github\.io$/.test(hostname)) return 'https://memory-pr.vercel.app'; // the Pages mirror has no server of its own
  if (/\.vercel\.app$/.test(hostname) || new URLSearchParams(search).has('api')) return '';
  return null;
}

/**
 * The request body for a draft, or the reason the server cannot check it. The server rebuilds
 * the page's memory by replaying the merged drafts, so a merge that was changed in the Cypher
 * console (`edited`) has no draft that describes it.
 */
export function serverRequest(landed, draft) {
  if (landed.some((merge) => merge.edited)) return { skip: 'merged-edit' };
  return { body: { merged: landed.map((merge) => merge.draft), draft } };
}

/** The server's answer next to the page's own verdict. Throws on an answer of the wrong shape. */
export function compare(mine, body) {
  if (!body || typeof body.blocked !== 'boolean' || !Array.isArray(body.rules)) throw new Error('shape');
  const rules = body.rules.map(String).sort();
  const own = [...new Set(mine.rules)].sort();
  const agrees = body.blocked === mine.blocked && JSON.stringify(rules) === JSON.stringify(own);
  return { state: agrees ? 'agrees' : 'differs', blocked: body.blocked, rules, ms: Number(body.elapsed_ms) || 0 };
}

/** The line under the verdict as { tone, text }, or null for no line. `edited`: this pull request was changed in the console after the server saw it. */
export function serverLine(server, edited) {
  if (!server || server.state === 'off') return null;
  if (server.state === 'skipped')
    return { tone: '', text: 'Server check skipped. You merged a pull request you had changed in the Cypher console, so the server cannot rebuild this memory from your drafts. Reset memory to compare again.' };
  if (edited)
    return { tone: '', text: 'Server check not shown. You changed this pull request in the Cypher console, and the server only saw the draft.' };
  if (server.state === 'checking') return { tone: '', text: 'Asking the server to check it too.' };
  if (server.state === 'agrees')
    return { tone: 'agrees', text: `Server check agrees. mempr check ran on the server and gave the same verdict in ${Math.round(server.ms)} ms.` };
  const theirs = server.blocked ? `blocked${server.rules.length ? ` by ${server.rules.join(', ')}` : ''}` : 'passed';
  return { tone: 'differs', text: `Server check differs. mempr check on the server says ${theirs}. It reviews against the committed memory plus what you merged here.` };
}
