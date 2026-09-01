// static/match.js
const content = document.getElementById('match-content');

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[character]));

function fmtDate(iso) {
  if (!iso) return 'Date to be confirmed';
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function formBadges(form) {
  if (!form || form === '—') return '<span class="form-empty">No recent results</span>';
  return [...form].map((result) => `<span class="form-badge ${result.toLowerCase()}">${result}</span>`).join('');
}

function marketPanel(p) {
  if (!p.market) {
    return `
      <section class="detail-panel market-panel">
        <div class="section-heading"><div><p class="eyebrow">Live market</p><h2>Ballr vs. the market</h2></div><p>via Oddscope</p></div>
        <p class="market-empty">No live market data tracked for this fixture yet — check back closer to kickoff.</p>
      </section>`;
  }

  const m = p.market;
  const rows = [
    { label: escapeHtml(p.home_team), model: p.home_pct, market: m.home_pct },
    { label: 'Draw', model: p.draw_pct, market: m.draw_pct },
    { label: escapeHtml(p.away_team), model: p.away_pct, market: m.away_pct },
  ];

  const rowsHTML = rows.map((r) => {
    if (r.market == null) return '';
    const gap = r.model - r.market;
    const gapClass = gap > 0 ? 'pos' : gap < 0 ? 'neg' : '';
    return `
      <div class="compare-row">
        <span class="compare-name">${r.label}</span>
        <span class="compare-model">${r.model}%</span>
        <span class="compare-market">${r.market}%</span>
        <span class="compare-gap ${gapClass}">${gap > 0 ? '+' : ''}${gap.toFixed(1)}pt</span>
      </div>`;
  }).join('');

  return `
    <section class="detail-panel market-panel">
      <div class="section-heading"><div><p class="eyebrow">Live market</p><h2>Ballr vs. the market</h2></div><p>via Oddscope</p></div>
      <div class="compare-table">
        <div class="compare-row compare-head">
          <span class="compare-name"></span>
          <span class="compare-model">Ballr</span>
          <span class="compare-market">Market</span>
          <span class="compare-gap">Gap</span>
        </div>
        ${rowsHTML}
      </div>
      <p class="score-note">A positive gap means Ballr rates that outcome higher than the live market does.</p>
    </section>`;
}

function scoreHeatmap(p) {
  const maxValue = Math.max(...p.score_matrix.flat());
  const headings = Array.from({ length: p.max_display_goals + 1 }, (_, goal) => `<th>${goal}</th>`).join('');
  const rows = p.score_matrix.map((row, homeGoals) => {
    const cells = row.map((probability, awayGoals) => {
      const intensity = Math.max(0.08, probability / maxValue);
      return `<td style="--heat:${intensity}" title="${homeGoals}–${awayGoals}: ${probability}%">${probability.toFixed(1)}%</td>`;
    }).join('');
    return `<tr><th>${homeGoals}</th>${cells}</tr>`;
  }).join('');

  return `
    <section class="detail-panel heatmap-panel">
      <div class="section-heading"><div><p class="eyebrow">Scoreline distribution</p><h2>Exact-score probabilities</h2></div><p>0–${p.max_display_goals} grid covers ${p.shown_probability}% of the model</p></div>
      <div class="score-axis"><span>${escapeHtml(p.home_team)} goals ↓</span><span>${escapeHtml(p.away_team)} goals →</span></div>
      <div class="score-grid-wrap"><table class="score-grid"><thead><tr><th></th>${headings}</tr></thead><tbody>${rows}</tbody></table></div>
    </section>`;
}

function render(p) {
  const scores = p.top_scorelines.map((score) => `
    <div class="score-pick"><b>${score.home_goals}–${score.away_goals}</b><span>${score.probability.toFixed(1)}%</span></div>`).join('');
  const uncertainty = !p.home_rating_known || !p.away_rating_known
    ? '<p class="detail-warning">Limited historical data for one or both teams; treat this prediction with extra caution.</p>' : '';

  content.innerHTML = `
    <header class="match-hero">
      <p class="eyebrow">Match prediction</p>
      <h1><span>${escapeHtml(p.home_team)}</span><em>vs</em><span>${escapeHtml(p.away_team)}</span></h1>
      <p class="match-date">${fmtDate(p.utc_date)}</p>
      ${uncertainty}
    </header>

    <section class="detail-panel outcome-panel">
      <div class="section-heading"><div><p class="eyebrow">Predicted outcome</p><h2>Win / Draw / Win</h2></div><p>Calibrated Monte Carlo model</p></div>
      <div class="large-prob-bar"><div class="prob-seg home" style="width:${p.home_pct}%"></div><div class="prob-seg draw" style="width:${p.draw_pct}%"></div><div class="prob-seg away" style="width:${p.away_pct}%"></div></div>
      <div class="outcome-values">
        <div><span>${escapeHtml(p.home_team)} win</span><b>${p.home_pct}%</b></div>
        <div><span>Draw</span><b>${p.draw_pct}%</b></div>
        <div><span>${escapeHtml(p.away_team)} win</span><b>${p.away_pct}%</b></div>
      </div>
    </section>

    ${marketPanel(p)}

    <section class="detail-grid">
      <div class="detail-panel form-panel"><p class="eyebrow">Current form</p><h2>${escapeHtml(p.home_team)}</h2><div class="form-badges">${formBadges(p.home_form)}</div><p>Most recent results first</p></div>
      <div class="detail-panel form-panel"><p class="eyebrow">Current form</p><h2>${escapeHtml(p.away_team)}</h2><div class="form-badges">${formBadges(p.away_form)}</div><p>Most recent results first</p></div>
    </section>

    <section class="detail-panel model-metrics">
      <div><span>Model xG</span><b>${p.home_xg} <i>–</i> ${p.away_xg}</b></div>
      <div><span>Elo rating</span><b>${p.home_rating} <i>–</i> ${p.away_rating}</b></div>
      <div class="likely-scores"><span>Likeliest scores</span><div>${scores}</div></div>
    </section>
    ${scoreHeatmap(p)}`;
}

async function init() {
  const params = new URLSearchParams(window.location.search);
  if (!params.get('league') || !params.get('home') || !params.get('away')) {
    content.innerHTML = '<p class="error-copy">This match link is incomplete. <a href="/">Return to all fixtures.</a></p>';
    return;
  }
  try {
    const response = await fetch(`/api/match?${params.toString()}`);
    const prediction = await response.json();
    if (!response.ok) throw new Error(prediction.error || 'Prediction could not be loaded');
    render(prediction);
  } catch (error) {
    console.error(error);
    content.innerHTML = `<p class="error-copy">${escapeHtml(error.message)} <a href="/">Return to all fixtures.</a></p>`;
  }
}

init();