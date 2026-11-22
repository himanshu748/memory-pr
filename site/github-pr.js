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
  if (state.state === 'failed' || state.state === 'closed' || state.state === 'refused') return true;
  return state.state === 'open' && ['success', 'failure', 'error'].includes(state.check);
}

/** What to show for a pull request's state: { tone, text, link, label }. */
export function prLine(state) {
  if (!state) return null;
  const link = safeUrl(state.url) || safeUrl(state.find_url);
  if (state.state === 'sending') return { tone: '', text: 'Asking GitHub to open it.' };
  if (state.state === 'refused') return { tone: 'differs', text: state.note || 'The server could not ask GitHub. Try again in a minute.' };
  if (state.state === 'queued') return { tone: '', text: 'GitHub is opening the pull request. This takes about half a minute.' };
  if (state.state === 'failed')
    return { tone: 'differs', text: 'GitHub did not open the pull request. It refuses when three are open or twenty were opened in the last 24 hours.', link: safeUrl(state.run_url), label: 'See the run' };
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
