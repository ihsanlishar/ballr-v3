'use strict';
const byId = id => document.getElementById(id);
let controller;
let generation = 0;
const pct = value => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
async function loadPerformance() {
  const current = ++generation;
  if (controller) controller.abort();
  controller = new AbortController();
  const activeController = controller;
  const timer = setTimeout(() => activeController.abort(), 30000);
  byId('performance-content').hidden = true;
  byId('performance-retry').hidden = true;
  byId('performance-message').textContent = 'Loading performance…';
  try {
    const response = await fetch(`/api/performance?league=${encodeURIComponent(byId('performance-league').value)}`, {signal: activeController.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Performance is temporarily unavailable.');
    if (current !== generation) return;
    byId('performance-message').textContent = data.evaluated
      ? `${data.pending} forecasts awaiting results; ${data.excluded} excluded. ${data.evaluated < 100 ? 'Early sample — too small to draw strong conclusions.' : ''}`
      : `No completed predictions to evaluate yet. ${data.pending} forecasts awaiting results. This page will fill in as matches finish.`;
    byId('evaluated').textContent = data.evaluated;
    byId('accuracy').textContent = pct(data.accuracy);
    byId('brier').textContent = data.brier == null ? '—' : data.brier.toFixed(3);
    byId('baseline').textContent = data.baseline_brier == null ? '—' : data.baseline_brier.toFixed(3);
    byId('model-version').textContent = `Model version: ${data.model_version}. Scores on this page apply only to this version.`;
    byId('performance-range').textContent = data.first_recorded ? `Forecasts recorded ${new Date(data.first_recorded).toLocaleDateString()} – ${new Date(data.last_recorded).toLocaleDateString()}.` : '';
    const table = byId('calibration');
    table.replaceChildren();
    for (const bucket of data.calibration) {
      const row = document.createElement('tr');
      for (const value of [bucket.range, bucket.count, pct(bucket.predicted), pct(bucket.observed)]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      }
      table.appendChild(row);
    }
    byId('performance-content').hidden = false;
  } catch (error) {
    if (current !== generation) return;
    byId('performance-message').textContent = error.name === 'AbortError' ? 'Request timed out. Please retry.' : error.message;
    byId('performance-retry').hidden = false;
  } finally { clearTimeout(timer); }
}
byId('performance-league').addEventListener('change', loadPerformance);
byId('performance-retry').addEventListener('click', loadPerformance);
loadPerformance();
