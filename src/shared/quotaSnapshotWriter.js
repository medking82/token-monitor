'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { serializeQuotaSnapshot } = require('./quotaSnapshot');

function writeQuotaSnapshotAtomic(filePath, snapshot, fsImpl = fs) {
  const directory = path.dirname(filePath);
  fsImpl.mkdirSync(directory, { recursive: true });
  const temporaryPath = `${filePath}.tmp-${process.pid}-${crypto.randomUUID()}`;
  try {
    fsImpl.writeFileSync(temporaryPath, serializeQuotaSnapshot(snapshot), 'utf8');
    fsImpl.renameSync(temporaryPath, filePath);
  } finally {
    try { if (fsImpl.existsSync(temporaryPath)) fsImpl.unlinkSync(temporaryPath); } catch (_) {}
  }
  return filePath;
}

module.exports = { writeQuotaSnapshotAtomic };
