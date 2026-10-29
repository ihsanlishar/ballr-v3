// Run automatically; only suspend rendering while the page is not visible.
// Reduced-motion preferences are handled by CSS. Old stored pauses are ignored.
(() => {
  const update = () => document.body.classList.toggle('motion-paused', document.hidden);
  document.addEventListener('visibilitychange', update);
  update();
})();
