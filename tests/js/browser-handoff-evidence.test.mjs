import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const recorded = JSON.parse(readFileSync(
  new URL('../../demo/ab/browser-handoff-replication.json', import.meta.url), 'utf8',
));
const google = 'https://github.com/GoogleCloudPlatform/microservices-demo/blob/c7cb6d5bfd6ea3aed768634ebbcb98cb7a523712';
const tokio = 'https://github.com/tokio-rs/tokio/blob/0dfdc6ac20029a0c0271626379504318d4f1c7f2';
const expected = {
  'checkout-go': ['1.25.0', `${google}/src/checkoutservice/go.mod`],
  'shipping-toolchain': ['go1.27.1', `${google}/src/shippingservice/go.mod`],
  'macros-msrv': ['1.85', `${tokio}/tokio-macros/Cargo.toml`],
  'util-msrv': ['1.85', `${tokio}/tokio-util/Cargo.toml`],
};

test('recorded packets and session logs reproduce the reported handoff results', () => {
  for (const input of Object.values(recorded.inputs)) {
    assert.equal(createHash('sha256').update(input.packet).digest('hex'), input.sha256);
  }
  const groups = { handoff: [], 'source-only': [] };
  for (const session of Object.values(recorded.trials)) {
    const output = Object.fromEntries(session.answer.map(({ id, value, source }) => [id, [value, source]]));
    assert.equal(session.answer.length, Object.keys(expected).length);
    assert.deepEqual(output, expected);
    const reads = session.observedCalls.filter(({ operation }) => operation === 'read');
    const packets = session.observedCalls.filter(({ operation }) => operation === 'packet');
    groups[session.assigned].push({
      manifests: new Set(reads.map(({ subject, path }) => `${subject}/${path}`)).size,
      packets: packets.length,
    });
  }
  assert.deepEqual(groups, {
    handoff: [{ manifests: 1, packets: 2 }, { manifests: 1, packets: 2 }],
    'source-only': [{ manifests: 4, packets: 0 }, { manifests: 4, packets: 0 }],
  });
});
