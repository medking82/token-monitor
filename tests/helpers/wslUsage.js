'use strict';

const wslUsage = require('../../src/shared/wslUsage');

// Host collector tests count native scans, not scans of the developer's running
// WSL distros. Keep that external source empty unless a case injects its own WSL
// fixture through collector options. WSL discovery/transport has separate tests.
// Install before freshCollector() captures these module exports; node:test
// restores the originals after each case.
function installWslUsageGuard(test) {
  test.beforeEach((t) => {
    t.mock.method(wslUsage, 'collectWslUsage', async () => ({
      bundle: wslUsage.emptyWslBundle(), detected: []
    }));
    t.mock.method(wslUsage, 'probeWslState', () => 'not-installed');
  });
}

module.exports = { installWslUsageGuard };
