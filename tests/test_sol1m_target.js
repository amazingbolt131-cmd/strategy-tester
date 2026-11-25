// Optional +$1000 per-trade target on SOL 1m Morning HAMA. Official default stays no-target.
const assert = require("assert");
const path = require("path");
const SOL1M = require(path.join(__dirname, "../js/v31/sol1m.js"));

function bars(rows) {
  // rows: [t, o, h, l, c]
  return rows;
}

function infoFor(all, dir) {
  return { ready: true, dir: dir, kind: dir > 0 ? "LONG" : "SHORT", i0: 0, iT: 0, i1: all.length, dayStart: 0, move: 2 };
}

// Entry at close 100, qty = round2(40000/100) = 400, entry px = 100.06 after $0.06 long spread.
// -$100 stop price = 100.06 - 100/400 = 99.81
// +$1000 TP price = 100.06 + 1000/400 = 102.56
function longSetup(second) {
  const all = [
    [0, 100, 100.2, 99.9, 100],           // flip / entry bar (fd=+1)
    [60000, 100, second.h, second.l, second.c],
    [120000, 101, 101.2, 100.8, 101],
  ];
  const fd = [1, 0, 0];
  return { all, fd };
}

function run(second, target) {
  const { all, fd } = longSetup(second);
  return SOL1M.daySim(all, fd, infoFor(all, 1), target ? { target: target } : undefined);
}

// Official default: a +$1000-capable bar does not take profit; trade is still open (or day_end later).
{
  const res = run({ h: 103, l: 99.9, c: 102.8 }, 0);
  // dayOver is false here because last bar time+60s < dayStart+86400000 and i1 == N, wait:
  // dayOver = info.i1 < N || (N && bars[N-1][0] + 60000 >= dayStart + 86400000)
  // i1 == N, last time 120000+60000 << 86400000, so dayOver is false, pos stays open, 0 closed trades? 
  // After entry on bar 0, bar 1 goes to 103 but no target, pos remains. No day_end.
  assert.strictEqual(res.trades.length, 0, "no-target should not close on a +$1000 favorable bar");
  assert.ok(res.pos, "position should still be open without a target");
}

// With $1000 target, favorable high 103 hits TP at 102.56, pnl = 1000.
{
  const res = run({ h: 103, l: 99.9, c: 102.8 }, 1000);
  assert.strictEqual(res.trades.length, 1, "target should close the trade");
  const t = res.trades[0];
  assert.strictEqual(t.reason, "TP");
  assert.ok(Math.abs(t.exit - 102.56) < 1e-9, "exit at exact +$1000 price, got " + t.exit);
  assert.ok(Math.abs(t.pnl - 1000) < 1e-6, "pnl should be $1000, got " + t.pnl);
  assert.ok(!res.pos, "position should be flat after TP");
}

// Same bar hits both the -$100 stop (low 99.5 < 99.81) and the +$1000 target (high 103): stop first.
{
  const res = run({ h: 103, l: 99.5, c: 101 }, 1000);
  assert.strictEqual(res.trades.length, 1);
  assert.strictEqual(res.trades[0].reason, "stop");
  assert.ok(Math.abs(res.trades[0].pnl + 100) < 1e-6, "stop pnl should be -$100, got " + res.trades[0].pnl);
}

// Default daySim signature (no opts) stays no-target.
{
  const { all, fd } = longSetup({ h: 103, l: 99.9, c: 102.8 });
  const res = SOL1M.daySim(all, fd, infoFor(all, 1));
  assert.strictEqual(res.trades.length, 0);
  assert.ok(res.pos);
}

console.log("ok");
