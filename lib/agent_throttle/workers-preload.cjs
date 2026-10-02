// Lower Jest/Playwright worker counts in package-manager scripts. No effect on other Node processes.
'use strict';
try {
  const script = process.argv[1] || '';
  const jest = /(^|\/)(jest(-cli)?\/bin\/jest\.js|\.bin\/jest)$/.test(script);
  const pw = /(^|\/)(@playwright\/test\/cli\.js|playwright(-core)?\/cli\.js|\.bin\/playwright)$/.test(script) &&
    process.argv.slice(2).includes('test');
  const w = Number(pw ? process.env.SOLO_W_PW : process.env.SOLO_W);
  const c = Number(process.env.SOLO_CORES);
  if ((jest || pw) && w > 0 && c > 0) {
    const flags = jest ? ['--maxWorkers', '-w'] : ['--workers', '-j'];
    const notes = [];
    const lower = (flag, raw) => {
      const m = /^(\d+)(%?)$/.exec(String(raw).trim());
      const n = m ? (m[2] ? Math.max(1, Math.floor(c * Number(m[1]) / 100)) : Number(m[1])) : null;
      if (n === null || n > w) { notes.push(`${flag} ${raw}->${w}`); return String(w); }
      return raw;
    };
    let seen = false;
    for (let i = 2; i < process.argv.length; i++) {
      const arg = process.argv[i];
      const eq = arg.indexOf('=');
      const name = eq > 0 ? arg.slice(0, eq) : arg;
      if (flags.includes(name) && eq > 0) { process.argv[i] = `${name}=${lower(name, arg.slice(eq + 1))}`; seen = true; }
      else if (flags.includes(arg) && i + 1 < process.argv.length) {
        process.argv[i + 1] = lower(arg, process.argv[i + 1]); seen = true; i++;
      } else if (flags.includes(arg.slice(0, 2)) && /^-[wjn]\d+%?$/.test(arg)) {
        process.argv[i] = arg.slice(0, 2) + lower(arg.slice(0, 2), arg.slice(2)); seen = true;
      } else if (jest && (arg === '--runInBand' || arg === '-i')) { seen = true; }
    }
    if (!seen) {
      const flag = jest ? '--maxWorkers' : '--workers';
      process.argv.push(`${flag}=${w}`); notes.push(`preload injected ${flag}=${w}`);
    }
    if (notes.length && process.env.SOLO_SLOT_DIR) {
      require('fs').appendFileSync(`${process.env.SOLO_SLOT_DIR}/wlog`, `${jest ? 'jest' : 'playwright'}: ${notes.join(', ')}\n`);
    }
  }
} catch (_) { /* Keep unrelated Node processes usable when diagnostic logging fails. */ }
