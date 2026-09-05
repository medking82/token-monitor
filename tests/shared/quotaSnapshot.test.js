'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { createQuotaSnapshot, serializeQuotaSnapshot } = require('../../src/shared/quotaSnapshot');
const { writeQuotaSnapshotAtomic } = require('../../src/shared/quotaSnapshotWriter');
const { mapClaudeUsageToProvider } = require('../../src/shared/limitCollector');

const accountKey = `sha256:${'a'.repeat(64)}`;

test('serializes a privacy filtered snapshot with row observation time', () => {
  const snapshot = createQuotaSnapshot({ updatedAt: '2026-01-01T00:00:00.000Z', providers: [{
    provider: 'claude', accountKey, accountEmail: 'hidden@example.com', status: 'ok', source: 'web',
    updatedAt: '2026-01-01T00:01:00.000Z', balance: { quotaGroup: 'pool-a' },
    windows: [{ kind: 'weekly', limitId: 'weekly-a', remainingPercent: 42, windowMinutes: 10080, resetsAt: '2026-01-02T00:00:00Z' }]
  }] }, { now: () => new Date('2026-01-01T00:02:00Z'), appVersion: '0.54.0' });
  const expectedAccount = crypto.createHash('sha256').update('claude\0hidden@example.com\0').digest('hex');
  const provider = snapshot.providers[0];
  assert.deepEqual({ ...provider, windows: provider.windows.map(({ id, ...window }) => window) }, {
    provider: 'claude', account_key: expectedAccount, observed_at: '2026-01-01T00:01:00.000Z',
    status: 'ok', source: 'web', windows: [{
      pool: null, window_minutes: 10080,
      remaining_percent: 42, resets_at: '2026-01-02T00:00:00.000Z'
    }]
  });
  assert.match(provider.windows[0].id, /^sha256:[a-f0-9]{64}$/u);
  const text = serializeQuotaSnapshot(snapshot);
  assert.doesNotMatch(text, /hidden@example|accountEmail|accountLabel|sourceDetail/);
  assert.equal(snapshot.generated_at, '2026-01-01T00:02:00.000Z');
  assert.equal(snapshot.providers[0].observed_at, '2026-01-01T00:01:00.000Z');
});

test('unknown source and stale row time become unavailable without invented values', () => {
  const snapshot = createQuotaSnapshot({ providers: [{
    provider: 'codex', accountKey: 'raw-secret', status: 'ok', source: 'surprise source', updatedAt: 'bad',
    windows: [{ kind: 'daily', remainingPercent: 0 }]
  }] }, { now: () => new Date('2026-01-01T00:00:00Z') });
  assert.equal(snapshot.providers[0].account_key, null);
  assert.equal(snapshot.providers[0].status, 'unavailable');
  assert.equal(snapshot.providers[0].source, null);
  assert.equal(snapshot.providers[0].windows[0].remaining_percent, 0);
  assert.equal(snapshot.providers[0].windows[0].resets_at, null);
});

test('uses normalized Antigravity labels and real Claude mapper durations', () => {
  const antigravity = {
    provider: 'antigravity', accountKey, accountEmail: 'known@example.invalid', status: 'ok', source: 'rpc',
    updatedAt: '2026-01-01T00:00:00Z', windows: [
      { kind: 'weekly', label: 'Gemini Models', remainingPercent: 80, windowMinutes: 10080 },
      { kind: 'weekly', label: 'Claude and GPT models', remainingPercent: 60, windowMinutes: 10080 }
    ]
  };
  const claude = mapClaudeUsageToProvider({
    five_hour: { utilization: 0 }, seven_day: { utilization: 0 },
    limits: [{ kind: 'weekly_scoped', scope: { model: { display_name: 'fable' } }, utilization: 0 }]
  }, { accountKey, updatedAt: '2026-01-01T00:00:00Z', source: 'oauth' });
  const snapshot = createQuotaSnapshot({ providers: [antigravity, claude] }, { now: () => new Date('2026-01-01T00:01:00Z') });
  assert.deepEqual(snapshot.providers[0].windows.map((window) => window.pool), ['Gemini Models', 'Claude and GPT models']);
  assert.equal(snapshot.providers[1].windows[0].window_minutes, 300);
  assert.equal(snapshot.providers[1].windows[1].window_minutes, 10080);
  assert.equal(snapshot.providers[1].windows[2].window_minutes, 10080);
});

test('duplicate provider identities fail closed', () => {
  const row = { provider: 'codex', accountKey, status: 'ok', source: 'rpc', updatedAt: '2026-01-01T00:00:00Z', windows: [] };
  assert.throws(() => createQuotaSnapshot({ providers: [row, { ...row }] }), /duplicate provider identity/);
  const unavailable = createQuotaSnapshot({ providers: [] }, { now: () => new Date('2026-01-01T00:01:00Z') });
  assert.deepEqual(unavailable.providers, []);
  assert.equal(unavailable.kind, 'token-monitor-quota-snapshot');
});

test('anonymous rows remain unavailable without collapsing distinct rows', () => {
  const snapshot = createQuotaSnapshot({ providers: [
    { provider: 'claude', status: 'ok', source: 'web', updatedAt: '2026-01-01T00:00:00Z', windows: [] },
    { provider: 'claude', status: 'ok', source: 'web', updatedAt: '2026-01-01T00:00:00Z', windows: [] }
  ] });
  assert.equal(snapshot.providers.length, 2);
  assert.equal(snapshot.providers[0].account_key, null);
  assert.equal(snapshot.providers[0].status, 'unavailable');
});

test('writes one quota file atomically', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'token-monitor-quota-'));
  const file = path.join(directory, 'observations', 'token-monitor-quota.json');
  const snapshot = createQuotaSnapshot({ providers: [] }, { now: () => new Date('2026-01-01T00:00:00Z') });
  writeQuotaSnapshotAtomic(file, snapshot);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), snapshot);
  assert.deepEqual(fs.readdirSync(path.dirname(file)), ['token-monitor-quota.json']);
  fs.rmSync(directory, { recursive: true, force: true });
});

test('a failed provider update replaces usable quota with unavailable state', () => {
  const usable = createQuotaSnapshot({ providers: [{
    provider: 'claude', accountKey, accountEmail: 'identity-known', status: 'ok', source: 'web',
    updatedAt: '2026-01-01T00:00:00Z', windows: [{ kind: 'weekly', remainingPercent: 80 }]
  }] }, { now: () => new Date('2026-01-01T00:01:00Z') });
  const failed = createQuotaSnapshot({ providers: [{
    provider: 'claude', accountKey, status: 'unavailable', source: '',
    updatedAt: '2026-01-01T00:02:00Z', windows: []
  }] }, { now: () => new Date('2026-01-01T00:03:00Z') });
  assert.equal(usable.providers[0].status, 'ok');
  assert.equal(failed.providers[0].status, 'unavailable');
  assert.equal(failed.providers[0].observed_at, '2026-01-01T00:02:00.000Z');
});

test('atomic write failure leaves existing bytes intact', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'token-monitor-quota-failure-'));
  const file = path.join(directory, 'quota.json');
  const snapshot = createQuotaSnapshot({ providers: [] }, { now: () => new Date('2026-01-01T00:00:00Z') });
  writeQuotaSnapshotAtomic(file, snapshot);
  const previous = fs.readFileSync(file, 'utf8');
  const failingFs = {
    mkdirSync: fs.mkdirSync.bind(fs),
    writeFileSync() { throw new Error('disk full'); },
    renameSync: fs.renameSync.bind(fs),
    existsSync: fs.existsSync.bind(fs),
    unlinkSync: fs.unlinkSync.bind(fs)
  };
  assert.throws(() => writeQuotaSnapshotAtomic(file, snapshot, failingFs), /disk full/);
  assert.equal(fs.readFileSync(file, 'utf8'), previous);
  fs.rmSync(directory, { recursive: true, force: true });
});
