// static/app.js
const state = {
  league: null,
  predictions: [],
  searchTerm: '',
  firstLoadDone: false,
};

const el = (id) => document.getElementById(id);

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
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
    league: state.league,
    home: p.home_team,
    away: p.away_team,
    date: p.utc_date,
  });
  return `/match?${params.toString()}`;
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
        <p class="card-title">${p.home_team} vs. ${p.away_team}</p>
        <p class="card-meta">${fmtDate(p.utc_date)}</p>
      </div>
      ${uncertain ? '<div class="warn-badge">LIMITED DATA</div>' : ''}
    </div>
    <div class="prob-bar">
      <div class="prob-seg home" style="width:${p.home_pct}%"></div>
      <div class="prob-seg draw" style="width:${p.draw_pct}%"></div>
      <div class="prob-seg away" style="width:${p.away_pct}%"></div>
    </div>
    <div class="outcome-legend">
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--home)"></span>
        <span class="legend-name">${p.home_team}</span>
        <span class="legend-value">${p.home_pct}%</span>
      </div>
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--draw)"></span>
        <span class="legend-name">Draw</span>
        <span class="legend-value">${p.draw_pct}%</span>
      </div>
      <div class="legend-item">
        <span class="legend-swatch" style="background:var(--away)"></span>
        <span class="legend-name">${p.away_team}</span>
        <span class="legend-value">${p.away_pct}%</span>
      </div>
    </div>
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

async function loadPredictions() {
  if (!state.league) { hideLoader(); return; }
  try {
    const data = await fetchJSON(`/api/predictions?league=${state.league}`);
    state.predictions = data.predictions || [];
    el('stat-tracked').textContent = state.predictions.length;
    render();
    el('last-refreshed').textContent =
      `Last refreshed: ${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC`;
  } catch (err) {
    console.error('Failed to load predictions:', err);
  } finally {
    hideLoader();
  }
}

function init() {
  el('league-select').addEventListener('change', (e) => {
    state.league = e.target.value;
    loadPredictions();
  });

  el('search-input').addEventListener('input', (e) => {
    state.searchTerm = e.target.value.trim();
    render();
  });

  loadLeagues().then(() => {
    loadPredictions();
  });
}

document.addEventListener('DOMContentLoaded', init);
