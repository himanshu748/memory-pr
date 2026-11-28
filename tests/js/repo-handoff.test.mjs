import assert from 'node:assert/strict';
import { test } from 'node:test';
import { repoHandoff } from '../../site/repo-handoff.js';

const baseline = '1'.repeat(40);
const revision = '2'.repeat(40);
const fileAt = (sha) => `https://github.com/example/app/blob/${sha}/go.mod`;
const scan = {
  repo: 'example/app',
  old: { sha: baseline },
  head: { sha: revision },
  files: { old: 3, head: 4 },
  fixed: { blocked: false },
  changed: [{
    entity: 'api', predicate: 'go-version', old: '1.23.0', new: '1.25.0',
    old_url: fileAt(baseline), new_url: fileAt(revision),
  }],
};

const unpack = (text) => JSON.parse(text.slice(text.indexOf('\n\n') + 2));

test('a handoff needs applied replacements and a passing fixed review', () => {
  assert.equal(repoHandoff(scan, false), null);
  assert.equal(repoHandoff({ ...scan, fixed: { blocked: true } }, true), null);
  assert.equal(repoHandoff({ ...scan, changed: [] }, true), null);
});

test('the handoff pins each changed value to the commit and source actually reviewed', () => {
  const text = repoHandoff(scan, true);
  assert.match(text, /may be normalized or abbreviated/);
  assert.deepEqual(unpack(text), {
    repository: 'example/app',
    baseline,
    revision,
    review: 'Passed',
    filesRead: { baseline: 3, revision: 4 },
    changes: [{
      entity: 'api', predicate: 'go-version',
      previous: { value: '1.23.0', source: fileAt(baseline) },
      current: { value: '1.25.0', source: fileAt(revision) },
    }],
    unconfirmed: [],
  });
});

test('a partial handoff preserves missing evidence and never lists it as current', () => {
  const packet = unpack(repoHandoff({
    ...scan,
    files_skipped: { old: 0, head: 2 },
    unconfirmed: [{
      entity: 'worker', predicate: 'python-requires',
      old: '>=3.10', old_url: fileAt(baseline), reason: 'source-unread',
    }],
  }, true));
  assert.match(packet.review, /Verification still needed/);
  assert.match(packet.coverage, /Latest: read 4 of 6/);
  assert.equal(packet.changes.length, 1);
  assert.deepEqual(packet.unconfirmed, [{
    entity: 'worker', predicate: 'python-requires', previousSource: fileAt(baseline), reason: 'source-unread',
  }]);
});
