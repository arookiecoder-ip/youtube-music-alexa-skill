/* In-page section navigation and the secondary detail dialogs.
 *
 * The shell owns the sidebar drawer, so this file is left with two jobs: scroll
 * a section-nav target into view without leaving the page, and borrow a panel's
 * detail list into the shared dialog and give it back on close.
 */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const nav = el('status-nav'), details = el('panel-dialog');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');

  nav.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    const target = document.querySelector(link.getAttribute('href'));
    if (!target) return;
    event.preventDefault();
    if (location.hash !== link.hash) history.pushState(null, '', link.hash);
    window.dispatchEvent(new Event('hashchange'));
    requestAnimationFrame(() => target.scrollIntoView({
      behavior: reduceMotion.matches ? 'instant' : 'smooth',
      block: 'start'
    }));
  });

  let home = null, content = null;
  for (const button of document.querySelectorAll('[data-detail-target]')) {
    button.addEventListener('click', () => {
      if (details.open) return;
      content = el(button.dataset.detailTarget);
      home = content.parentElement;
      el('panel-dialog-title').textContent = button.childNodes[0].textContent;
      el('panel-dialog-body').append(content);
      details.showModal();
    });
  }
  details.addEventListener('close', () => {
    if (home && content) home.append(content);
    home = content = null;
  });
  el('panel-dialog-close').addEventListener('click', () => details.close());
  details.addEventListener('click', event => {
    const rect = details.getBoundingClientRect();
    if (event.target === details && (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom)) details.close();
  });
})();
