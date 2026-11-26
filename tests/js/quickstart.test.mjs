import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync, symlinkSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('../../quickstart.sh', import.meta.url));

test('the playground starts without curl and refuses an occupied port', { timeout: 15_000 }, async (t) => {
  const bin = mkdtempSync(join(tmpdir(), 'mempr-quickstart-'));
  t.after(() => rmSync(bin, { recursive: true, force: true }));
  const python = execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim();
  symlinkSync(python, join(bin, 'python3'));
  const env = { ...process.env, PATH: bin, BROWSER: 'unavailable-browser' };

  const socket = createServer();
  socket.listen(0, '127.0.0.1');
  await once(socket, 'listening');
  const { port } = socket.address();
  try {
    const blocked = spawnSync(script, [String(port)], { env, encoding: 'utf8', timeout: 5_000 });
    assert.equal(blocked.status, 1);
    assert.match(blocked.stderr, /already serving something else/);
    assert.equal(blocked.stdout, '');
  } finally {
    await new Promise((resolve, reject) => socket.close((error) => error ? reject(error) : resolve()));
  }

  const child = spawn(script, [String(port)], { env, stdio: ['ignore', 'pipe', 'ignore'] });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill();
      await once(child, 'close');
    }
  });
  const started = new Promise((resolve, reject) => {
    child.stdout.setEncoding('utf8');
    let output = '';
    child.stdout.on('data', (data) => {
      output += data;
      if (output.includes('\n')) resolve(output);
    });
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`Quickstart exited: ${code}`)));
  });
  assert.match(await started, /Serving http:\/\/127\.0\.0\.1:/);
  const app = await fetch(`http://127.0.0.1:${port}/`);
  assert.equal(app.status, 200);
  await app.text();
  const data = await fetch(`http://127.0.0.1:${port}/data/demo.json`);
  assert.equal(data.status, 200);
  assert.equal(typeof await data.json(), 'object');
});
