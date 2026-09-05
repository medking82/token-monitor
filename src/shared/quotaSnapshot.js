'use strict';

const crypto = require('node:crypto');

const VALID_STATUSES = new Set([
  'ok', 'disabled', 'notConfigured', 'unauthorized', 'rateLimited',
  'sourceRateLimited', 'unavailable', 'error'
]);
const VALID_SOURCES = new Set(['oauth', 'cli', 'web', 'rpc', 'local', 'api']);

function iso(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function pseudonym(value) {
  if (typeof value !== 'string') return null;
  if (/^sha256:[a-f0-9]{64}$/u.test(value)) return value.slice(7);
  return /^[a-f0-9]{64}$/u.test(value) ? value : null;
}

function exportedAccountKey(provider, row) {
  const email = typeof row?.accountEmail === 'string' ? row.accountEmail.trim().toLowerCase() : '';
  if ((provider === 'claude' || provider === 'antigravity') && !email) return null;
  if (email) {
    return crypto.createHash('sha256').update(provider).update('\0').update(email).update('\0').digest('hex');
  }
  return pseudonym(row?.accountKey);
}

function stableWindowId(provider, window) {
  const identity = [
    provider,
    window.limitId || '',
    window.kind || '',
    window.label || '',
    window.metric || '',
    window.source || '',
    window.additional === true ? 'additional' : 'primary'
  ].join(':');
  return `sha256:${crypto.createHash('sha256').update(identity).digest('hex')}`;
}

function createQuotaSnapshot(limits, { now = () => new Date(), appVersion = null } = {}) {
  const generatedAt = iso(now() instanceof Date ? now().toISOString() : now());
  if (!generatedAt) throw new TypeError('generated_at must be an ISO timestamp');
  const rows = Array.isArray(limits?.providers) ? limits.providers : [];
  const seen = new Set();
  const providers = rows.map((row, rowIndex) => {
    const provider = typeof row?.provider === 'string' ? row.provider.trim().toLowerCase() : '';
    if (!provider) throw new TypeError('provider identity is missing');
    const accountKey = exportedAccountKey(provider, row);
    // A null key means identity is unavailable, not that all anonymous rows
    // are the same account. Keep each such row observable and unavailable;
    // only duplicate established identities fail closed.
    const identity = accountKey
      ? `${provider}\0${accountKey}`
      : `${provider}\0anonymous\0${rowIndex}`;
    if (seen.has(identity)) throw new TypeError(`duplicate provider identity: ${provider}`);
    seen.add(identity);
    const source = typeof row.source === 'string' ? row.source.trim().toLowerCase() : '';
    const observedAt = iso(row.updatedAt);
    const sourceKnown = VALID_SOURCES.has(source);
    const statusKnown = VALID_STATUSES.has(row.status);
    const identityRequired = provider === 'claude' || provider === 'antigravity';
    const rawWindows = Array.isArray(row.windows) ? row.windows : [];
    const windows = rawWindows.map((window) => ({
      id: stableWindowId(provider, window),
      pool: provider === 'antigravity'
        ? (window.label === 'Gemini Models' || window.label === 'Claude and GPT models'
          ? window.label
          : null)
        : null,
      window_minutes: Number.isFinite(window.windowMinutes) ? window.windowMinutes : null,
      remaining_percent: Number.isFinite(window.remainingPercent)
        ? window.remainingPercent
        : null,
      resets_at: iso(window.resetsAt)
    }));
    const unknownAntigravityPool = provider === 'antigravity'
      && windows.some((window, index) => !["Gemini Models", "Claude and GPT models"].includes(rawWindows[index]?.label));
    return {
      provider,
      account_key: accountKey,
      observed_at: observedAt,
      status: sourceKnown && observedAt && statusKnown && (!identityRequired || accountKey)
        && !unknownAntigravityPool
        ? row.status
        : 'unavailable',
      source: sourceKnown ? source : null,
      windows
    };
  });
  return {
    kind: 'token-monitor-quota-snapshot',
    schema_version: 1,
    generated_at: generatedAt,
    app_version: typeof appVersion === 'string' && appVersion.trim() ? appVersion.trim() : null,
    providers
  };
}

function serializeQuotaSnapshot(snapshot) {
  if (!snapshot || snapshot.kind !== 'token-monitor-quota-snapshot' || snapshot.schema_version !== 1) {
    throw new TypeError('invalid quota snapshot');
  }
  return `${JSON.stringify(snapshot, null, 2)}\n`;
}

module.exports = { createQuotaSnapshot, serializeQuotaSnapshot };
