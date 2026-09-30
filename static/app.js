// static/app.js
const state = {
  league: null,
  predictions: [],
  searchTerm: '',
  firstLoadDone: false,
  requestId: 0,
  controller: null,
  loading: false,
};

const el = (id) => document.getElementById(id);

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]));

async function fetchJSON(url, controller = new AbortController()) {
  const timer = setTimeout(() => controller.abort(), 60000);
  try {
    const res = await fetch(url, { signal: controller.signal });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function loadLeagues() {
  const leagues = await fetchJSON('/api/leagues');
  const select = el('league-select');
  select.innerHTML = '';
  leagues.forEach((lg) => {
    const opt = document.createElement('option');
    opt.value = lg.key;
    opt.textContent = lg.display_name;
    select.appendChild(opt);
  });
  state.league = leagues[0]?.key || null;
}

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit'
  });
}

function matchUrl(p) {
  const params = new URLSearchParams({
    league: p.league,
    home: p.home_team,
    away: p.away_team,
    date: p.utc_date,
  });
  return `/match?${params.toString()}`;
}

function marketComparisonHTML(p) {
  if (!p.market) {
    return `<div class="market-row market-missing">No live market data yet for this fixture.</div>`;
  }
  const m = p.market;
  const gap = (m.home_pct != null) ? (p.home_pct - m.home_pct) : null;
  const gapClass = gap > 0 ? 'pos' : gap < 0 ? 'neg' : '';
  const gapLabel = gap != null
    ? `Model ${gap > 0 ? 'favors' : 'is softer on'} ${escapeHtml(p.home_team)} by ${Math.abs(gap).toFixed(1)}pt vs. the market`
    : '';

  return `
    <div class="market-row">
      <div class="market-label">Market odds (Polymarket)</div>
      <div class="prob-bar compare-bar">
        <div class="prob-seg home" style="width:${m.home_pct}%"></div>
        ${m.draw_pct != null ? `<div class="prob-seg draw" style="width:${m.draw_pct}%"></div>` : ''}
        <div class="prob-seg away" style="width:${m.away_pct}%"></div>
      </div>
      <div class="compare-legend">
        <span>${m.home_pct}%</span>
        ${m.draw_pct != null ? `<span>${m.draw_pct}%</span>` : '<span>—</span>'}
        <span>${m.away_pct}%</span>
      </div>
      ${gapLabel ? `<div class="gap-note ${gapClass}">${gapLabel}</div>` : ''}
    </div>
  `;
}

function buildCard(p, index) {
  const card = document.createElement('div');
  card.className = 'match-card card-enter';
  card.tabIndex = 0;
  card.setAttribute('role', 'link');
  card.setAttribute('aria-label', `Open prediction details for ${p.home_team} versus ${p.away_team}`);
  card.style.animationDelay = `${Math.min(index * 0.05, 0.4)}s`;
  card.addEventListener('animationend', (e) => {
    if (e.animationName === 'card-in') {
      card.classList.remove('card-enter');
      card.style.animationDelay = '';
    }
  });

  const uncertain = !p.home_rating_known || !p.away_rating_known;
  const openMatch = () => { window.location.href = matchUrl(p); };
  card.addEventListener('click', openMatch);
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openMatch();
    }
  });

  card.innerHTML = `
    <div class="card-top">
      <div>
        <p class="card-title">${escapeHtml(p.home_team)} vs. ${escapeHtml(p.away_team)}</p>
        <p class="card-meta">${fmtDate(p.utc_date)}</p>
      </div>
      ${uncertain ? '<div class="warn-badge">LIMITED DATA</div>' : ''}
    </div>

    <div class="model-label">Ballr's prediction</div>
    <div class="prob-bar">
      <div class="prob-seg home" style="width:${p.home_pct}%"></div>
      <div class="prob-seg draw" style="width:${p.draw_pct}%"></div>
      <div class="prob-seg away" style="width:${p.away_pct}%"></div>
    </div>
    <div class="outcome-legend">
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--home)"></span>
        <span class="legend-name">${escapeHtml(p.home_team)}</span>
        <span class="legend-value">${p.home_pct}%</span>
      </div>
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--draw)"></span>
        <span class="legend-name">Draw</span>
        <span class="legend-value">${p.draw_pct}%</span>
      </div>
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--away)"></span>
        <span class="legend-name">${escapeHtml(p.away_team)}</span>
        <span class="legend-value">${p.away_pct}%</span>
      </div>
    </div>

    ${marketComparisonHTML(p)}

    <div class="metrics-row">
      <div><span>Model xG</span><b>${p.home_xg} – ${p.away_xg}</b></div>
      <div><span>Elo</span><b>${p.home_rating} – ${p.away_rating}</b></div>
    </div>
    <div class="card-link">Open full match prediction <span aria-hidden="true">→</span></div>
    <div class="date-row">${uncertain ? 'One or both teams have limited match history this season — prediction is less reliable.' : ''}</div>
  `;

  return card;
}

function filterBySearch(items) {
  if (!state.searchTerm) return items;
  const term = state.searchTerm.toLowerCase();
  return items.filter((p) =>
    p.home_team.toLowerCase().includes(term) || p.away_team.toLowerCase().includes(term)
  );
}

function render() {
  const container = el('list-predictions');
  const emptyNote = el('empty-predictions');
  const filtered = filterBySearch(state.predictions);

  container.innerHTML = '';

  if (!filtered.length) {
    if (state.loading || !el('request-error').hidden) { emptyNote.hidden = true; return; }
    emptyNote.hidden = false;
    return;
  }
  emptyNote.hidden = true;

  filtered.forEach((p, i) => container.appendChild(buildCard(p, i)));
}

function hideLoader() {
  if (state.firstLoadDone) return;
  state.firstLoadDone = true;
  el('loader').classList.add('hidden');
}

function showError(message) {
  el('request-error').hidden = false;
  el('error-message').textContent = message;
  el('empty-predictions').hidden = true;
}

async function loadPredictions() {
  if (!state.league) { hideLoader(); return; }
  const requestId = ++state.requestId;
  const league = state.league;
  if (state.controller) state.controller.abort();
  const controller = new AbortController();
  state.controller = controller;
  state.loading = true;
  state.predictions = [];
  el('list-predictions').innerHTML = '';
  el('stat-tracked').textContent = '—';
  el('last-refreshed').textContent = 'Updating predictions…';
  el('request-error').hidden = true;
  el('data-freshness').hidden = true;
  el('empty-predictions').hidden = true;
  try {
    const data = await fetchJSON(`/api/predictions?league=${encodeURIComponent(league)}`, controller);
    if (requestId !== state.requestId) return;
    state.predictions = data.predictions || [];
    const freshness = data.data_status;
    el('data-freshness').hidden = !freshness?.stale;
    el('data-freshness').textContent = freshness?.stale
      ? `Using saved match data. Last results sync: ${freshness.last_results_sync ? new Date(freshness.last_results_sync).toLocaleString() : 'unavailable'}. Updates are temporarily delayed.` : '';
    state.loading = false;
    el('stat-tracked').textContent = state.predictions.length;
    render();
    el('last-refreshed').textContent =
      `Last refreshed: ${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC`;
  } catch (err) {
    if (requestId !== state.requestId) return;
    showError(err.name === 'AbortError' ? 'The request timed out. Please retry.' : err.message);
    el('last-refreshed').textContent = 'Update failed';
  } finally {
    if (requestId === state.requestId) {
      state.loading = false;
      hideLoader();
    }
  }
}

async function loadInitial() {
  el('request-error').hidden = true;
  try {
    await loadLeagues();
    await loadPredictions();
  } catch (error) {
    showError('Could not load leagues. Please retry.');
    hideLoader();
  }
}

function init() {
  el('league-select').addEventListener('change', (event) => {
    state.league = event.target.value;
    loadPredictions();
  });
  el('search-input').addEventListener('input', (event) => {
    state.searchTerm = event.target.value.trim();
    render();
  });
  el('retry-load').addEventListener('click', () => state.league ? loadPredictions() : loadInitial());
  loadInitial();
  setInterval(() => {
    if (!document.hidden && state.league && !state.loading) loadPredictions();
  }, 120000);
}

document.addEventListener('DOMContentLoaded', init);
