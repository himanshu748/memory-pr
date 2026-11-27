// A real pull request on GitHub from a page draft: when the page offers it and how it words
// what the server says. No DOM here, so tests/js/github-pr.test.mjs can run it in node.

const REPO_URL = 'https://github.com/himanshu748/memory-pr/';
const WORD = /^[a-z0-9]+(?:[.-][a-z0-9]+)*$/;
const SOURCE = /^[a-z0-9]+(?:[.-][a-z0-9]+)*(?:\/[a-z0-9]+(?:[.-][a-z0-9]+)*)*$/;

/** A link the page may show: only ones into this repository on GitHub. */
export const safeUrl = (url) => (typeof url === 'string' && url.startsWith(REPO_URL) ? url : null);

/**
 * Why this draft cannot become a pull request on GitHub, or '' when it can. GitHub only gets
 * short lowercase words, and the pull request is against the committed memory, so nothing
 * merged in this page may come first. The server checks all of this again.
 */
export function whyNot(draft, landed, edited) {
  if (edited) return 'You changed this pull request in the Cypher console. GitHub would get the draft, not your change.';
  if (landed.length) return 'On GitHub the pull request is against the committed memory, without what you merged here. Reset memory to open one.';
  if (draft.kind !== 'fact') return 'Only a new fact can be opened on GitHub, not a retraction.';
  if ((draft.derived_from || []).length || draft.about === false || draft.source_status)
    return 'This change uses a graph link the GitHub form does not send. Review it in the page, or with mempr check.';
  for (const key of ['agent', 'entity', 'predicate', 'value'])
    if (!WORD.test(draft[key] || '') || draft[key].length > 60)
      return `For GitHub, ${key} must be lowercase letters, digits, dots and hyphens.`;
  if (!SOURCE.test(draft.source || '') || draft.source.length > 100)
    return 'For GitHub, the source must be a lowercase path such as docs/design.md.';
  return '';
}

/** True when polling can stop: the pull request has its check, is closed, or never opened. */
export function settled(state) {
  if (!state) return false;
  if (state.state === 'failed' || state.state === 'closed' || state.state === 'refused' || state.state === 'status-timeout') return true;
  return state.state === 'open' && ['success', 'failure', 'error'].includes(state.check);
}

/** Watch one accepted request. Stalled reads cannot outlast the five-minute watch. */
export async function watchPullRequest(api, id, show) {
  const deadline = Date.now() + 300_000;
  const endpoint = `${api}/api/pull-requests/${encodeURIComponent(id)}`;
  let pullUrl;
  let searchUrl;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, Math.min(5_000, deadline - Date.now())));
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    const request = new AbortController();
    const timer = setTimeout(() => request.abort(), Math.min(10_000, remaining));
    let state;
    try {
      const response = await fetch(endpoint, { signal: request.signal });
      if (response.ok) state = await response.json();
    } catch {
      // Try the next status read without sending another creation request.
    } finally {
      clearTimeout(timer);
    }
    if (!state || typeof state.state !== 'string') continue;
    pullUrl = safeUrl(state.url) || pullUrl;
    searchUrl = safeUrl(state.find_url) || searchUrl;
    show(state);
    if (settled(state)) return;
  }
  show({ state: 'status-timeout', url: pullUrl || searchUrl || `${REPO_URL}pulls` });
}

/** True when the request came to nothing and the visitor may send it again. */
export const again = (state) => Boolean(state) && (state.state === 'refused' || state.state === 'failed');

/** What to show for a pull request's state: { tone, text, link, label }. */
export function prLine(state) {
  if (!state) return null;
  const link = safeUrl(state.url) || safeUrl(state.find_url);
  if (state.state === 'sending') return { tone: '', text: 'Asking GitHub to open it.' };
  if (state.state === 'refused') return { tone: 'differs', text: state.note || 'The server could not ask GitHub. Try again in a minute.' };
  if (state.state === 'queued') return { tone: '', text: 'GitHub is opening the pull request. This takes about half a minute.' };
  if (state.state === 'status-timeout')
    return { tone: '', text: 'This page stopped checking after five minutes. Check GitHub for the current status.', link, label: 'Look on GitHub' };
  if (state.state === 'failed')
    return { tone: 'differs', text: 'GitHub did not open the pull request. It refuses when three are open or twenty were opened in the last 24 hours, and drops a request that arrives while two others wait. You can try again.', link: safeUrl(state.run_url), label: 'See the run' };
  if (state.state === 'closed')
    return { tone: '', text: `Pull request #${Number(state.number)} is closed. Playground pull requests are closed after 30 minutes.`, link, label: 'See it on GitHub' };
  if (state.state === 'open') {
    const label = `Pull request #${Number(state.number)} on GitHub`;
    if (state.check === 'success') return { tone: 'agrees', text: 'It is open on GitHub and memory-review passed.', link, label };
    if (state.check === 'failure' || state.check === 'error')
      return { tone: 'differs', text: 'It is open on GitHub and memory-review blocks the merge.', link, label };
    if (state.check === 'unknown') return { tone: '', text: 'It is open on GitHub. The check state is not known here.', link, label };
    return { tone: '', text: 'It is open on GitHub. memory-review is running.', link, label };
  }
  return { tone: '', text: 'GitHub is not answering this server right now. Use the link to look.', link, label: 'Look on GitHub' };
}
