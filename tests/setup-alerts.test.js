const {test} = require('node:test');
const assert = require('node:assert/strict');
const rules = require('../frontend/setup-alerts.js');
const watch = () => ({trigger: 100, invalidation: 95, state: 'near', lastDate: '2026-10-26', confirmed: false});

test('confirmation uses saved trigger and volume; repeated checks are silent', () => {
  const saved = watch();
  assert.equal(rules.advance(saved, {as_of: '2026-10-27', price: 101, volume_ratio: 1.1}), null);
  const bar = {as_of: '2026-10-28', price: 102, volume_ratio: 1.6};
  assert.equal(rules.advance(saved, bar).state, 'confirmed');
  assert.equal(rules.advance(saved, bar), null);
  assert.equal(saved.trigger, 100);
});
test('missed failure survives a subsequent recovery until explicitly rearmed', () => {
  const saved = watch();
  assert.equal(rules.advance(saved, {as_of: '2026-10-27', price: 94, volume_ratio: 2}).state, 'failed');
  assert.equal(rules.advance(saved, {as_of: '2026-10-28', price: 102, volume_ratio: 2}), null);
  assert.equal(saved.state, 'failed');
});
test('confirmed breakouts fail below their trigger even above base support', () => {
  const saved = watch();
  rules.advance(saved, {as_of: '2026-10-27', price: 102, volume_ratio: 2});
  assert.equal(rules.advance(saved, {as_of: '2026-10-28', price: 99, volume_ratio: 1}).state, 'failed');
});
test('high volume gap above extension threshold retains confirmation on later failure', () => {
  const saved = watch();
  assert.equal(rules.advance(saved, {as_of: '2026-10-27', price: 108, volume_ratio: 2}).state, 'extended');
  assert.equal(saved.confirmed, true);
  assert.equal(rules.advance(saved, {as_of: '2026-10-28', price: 99, volume_ratio: 1}).state, 'failed');
});
