// Unit tests for web/lib (run with `npm test`; Node's built-in test runner, no extra dependency).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { safeNext } from '../lib/format.ts';

const BASE = 'https://www.visuara.si';
const landsOn = (path) => new URL(path, BASE).host;

test('same-site destinations are kept', () => {
  assert.equal(safeNext('/dashboard/requests'), '/dashboard/requests');
  assert.equal(safeNext('/dashboard/campaigns/7?tab=report#top'), '/dashboard/campaigns/7?tab=report#top');
});

test('missing or relative destinations fall back to the dashboard', () => {
  for (const n of [undefined, '', 'dashboard', 'https://evil.example/', 'javascript:alert(1)']) assert.equal(safeNext(n), '/dashboard', String(n));
});

// D-SEC-01: browsers drop tab, CR and LF from URLs and read `\` as `/`, so these became `//evil.example`.
test('no input sends the browser to another site', () => {
  const attacks = ['//evil.example', '/\\evil.example', '/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\t\\evil.example',
    '/.//evil.example', '/%2e//evil.example', '/x/..//evil.example', '/\u0000/evil.example', '/' + 'a'.repeat(3000)];
  for (const n of attacks) {
    const out = safeNext(n);
    assert.ok(out.startsWith('/') && !out.startsWith('//'), `${JSON.stringify(n)} gave ${JSON.stringify(out)}`);
    assert.equal(landsOn(out), 'www.visuara.si', `${JSON.stringify(n)} lands on ${landsOn(out)}`);
  }
});
