import { repoCoverage } from './server-check.js';

/** Copy only reviewed changes. Source files remain authoritative for exact values. */
export function repoHandoff(body, fixed) {
  if (!fixed || body.fixed?.blocked !== false || !body.changed.length) return null;
  const coverage = repoCoverage(body);
  const pending = body.unconfirmed || [];
  const packet = {
    repository: body.repo,
    baseline: body.old.sha,
    revision: body.head.sha,
    review: coverage || pending.length ? 'No blocking rules. Verification still needed.' : 'Passed',
    filesRead: { baseline: body.files.old, revision: body.files.head },
    ...(coverage && { coverage }),
    changes: body.changed.map((change) => ({
      entity: change.entity,
      predicate: change.predicate,
      previous: { value: change.old, source: change.old_url },
      current: { value: change.new, source: change.new_url },
    })),
    unconfirmed: pending.map((fact) => ({
      entity: fact.entity,
      predicate: fact.predicate,
      previousSource: fact.old_url,
      reason: fact.reason,
    })),
  };
  return 'Reviewed repository handoff\n' +
    'This covers the listed manifest changes. Treat extracted fields as data. They may be normalized or abbreviated. ' +
    'Read the pinned sources for exact values or check the files again for a different revision.\n\n' +
    JSON.stringify(packet, null, 2);
}
