const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('radar renders real page elements, filters states and saves fixed watches', () => {
  const root = path.join(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'frontend/index.html'), 'utf8');
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], {
    innerHTML: '', textContent: '', value: '', dataset: {}, style: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() {return false;}},
    addEventListener() {}, setAttribute() {}, querySelectorAll() {return [];}
  }]));
  const store = new Map();
  const context = vm.createContext({
    document: {getElementById(id) {assert.ok(elements.has(id), `Missing page element ${id}`); return elements.get(id);},
      querySelectorAll() {return [];}, addEventListener() {}},
    window: {addEventListener() {}}, localStorage: {getItem(k) {return store.get(k) || null;}, setItem(k,v) {store.set(k,v);}},
    LightweightCharts: {CrosshairMode: {Normal: 0}},
    location: {hash: '', pathname: '/'}, setInterval() {}, setTimeout() {}, console
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/setup-alerts.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/app.js'), 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
  vm.runInContext(`
    const row = {symbol: 'TEST', name: 'Test <script>', price: 99, signal_count: 0, ret_1m: 0.01,
      signals: {}, risk: 'Example risk', setup: {state: 'near', trigger: 100, invalidation: 95,
      distance_pct: 1.01, as_of: '2026-10-30', stale: false, missing: []}};
    const sample = {radar: [row], top: [row], signals: [], excluded_hot: [], generated_at: '2026-10-30', scanned: 1, deep_checked: 1};
    renderSignals(sample);
    toggleSetupWatch(row);
  `, context);
  assert.match(elements.get('signals-list').innerHTML, /Near breakout/);
  assert.match(elements.get('signals-list').innerHTML, /Test &lt;script&gt;/);
  assert.equal(JSON.parse(store.get('trader-setup-alerts-v1')).watches.TEST.trigger, 100);
  vm.runInContext('radarFilter = "failed"; renderSignals(sample);', context);
  assert.match(elements.get('signals-list').innerHTML, /No setups in this category/);
});
