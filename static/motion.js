// Decorative motion never affects fixtures or requests. Remember an explicit pause.
(() => {
  const button = document.getElementById('motion-toggle');
  if (!button) return;
  let paused = false;
  try { paused = localStorage.getItem('ballr-motion-paused') === 'true'; } catch (_) {}
  function update() {
    document.body.classList.toggle('motion-paused', paused);
    button.setAttribute('aria-pressed', String(paused));
    button.textContent = paused ? 'Resume background' : 'Pause background';
  }
  button.addEventListener('click', () => {
    paused = !paused;
    try { localStorage.setItem('ballr-motion-paused', String(paused)); } catch (_) {}
    update();
  });
  document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('motion-paused', paused || document.hidden);
  });
  update();
})();
