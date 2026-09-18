const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(fetch) {
  const elements = new Map();
  const element = () => ({ hidden: true, innerHTML: '', textContent: '', value: '', style: {},
    classList: { add() {}, remove() {} }, addEventListener() {}, appendChild() {}, setAttribute() {} });
  const document = {
    hidden: false,
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element, addEventListener() {},
  };
  const context = vm.createContext({ document, fetch, console, URLSearchParams, AbortController,
    setTimeout: () => 1, clearTimeout() {}, setInterval() {}, window: { location: {} } });
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  return { context, elements, run: (code) => vm.runInContext(code, context) };
}
const response = (data, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => data });

test('late league response cannot overwrite latest selection', async () => {
  const pending = [];
  const h = harness(() => new Promise(resolve => pending.push(resolve)));
  h.run("state.league = 'premier_league'; render = () => {};");
  const first = h.run('loadPredictions()');
  h.run("state.league = 'la_liga'");
  const second = h.run('loadPredictions()');
  pending[1](response({ predictions: [{ league: 'la_liga' }] }));
  await second;
  pending[0](response({ predictions: [{ league: 'premier_league' }] }));
  await first;
  assert.equal(h.run('state.predictions[0].league'), 'la_liga');
});

test('prediction failure clears old cards and shows retry error', async () => {
  const h = harness(async () => response({ error: 'Data unavailable' }, false));
  h.run("state.league = 'premier_league'; state.predictions = [{ old: true }]");
  await h.run('loadPredictions()');
  assert.equal(h.run('state.predictions.length'), 0);
  assert.equal(h.elements.get('request-error').hidden, false);
  assert.equal(h.elements.get('error-message').textContent, 'Data unavailable');
  assert.equal(h.run('state.firstLoadDone'), true);
});

test('league-list failure dismisses loader and exposes retry', async () => {
  const h = harness(async () => { throw new Error('offline'); });
  await h.run('loadInitial()');
  assert.equal(h.run('state.firstLoadDone'), true);
  assert.equal(h.elements.get('request-error').hidden, false);
});

test('match URLs use the prediction league', () => {
  const h = harness(async () => response({}));
  h.run("state.league = 'la_liga'");
  assert.match(h.run("matchUrl({league:'premier_league', home_team:'A', away_team:'B', utc_date:'date'})"), /league=premier_league/);
});

test('provider names are escaped in card HTML', () => {
  const h = harness(async () => response({}));
  const html = h.run(`buildCard({home_team:'<img src=x onerror=alert(1)>', away_team:'B',
    league:'premier_league', utc_date:'2099-01-01', home_pct:40, draw_pct:30, away_pct:30,
    home_rating:1500, away_rating:1500, home_xg:1, away_xg:1, market:null}, 0).innerHTML`);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});
