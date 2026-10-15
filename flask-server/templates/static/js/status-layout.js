/* Navigation and secondary details stay outside the dashboard's grid flow. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const drawer = el('nav-drawer'), nav = el('status-nav'), toggle = el('nav-toggle');
  const mobile = matchMedia('(max-width: 900px)');
  function syncNavigation() {
    if (drawer.open) drawer.close();
    (mobile.matches ? el('drawer-links') : el('desktop-navigation')).append(nav);
  }
  toggle.addEventListener('click', () => {
    if (!mobile.matches || drawer.open) return;
    drawer.showModal();
    toggle.setAttribute('aria-expanded', 'true');
  });
  el('nav-close').addEventListener('click', () => drawer.close());
  drawer.addEventListener('close', () => toggle.setAttribute('aria-expanded', 'false'));
  nav.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    const target = document.querySelector(link.getAttribute('href'));
    if (!target) return;
    event.preventDefault();
    if (drawer.open) drawer.close();
    if (location.hash !== link.hash) history.pushState(null, '', link.hash);
    window.dispatchEvent(new Event('hashchange'));
    requestAnimationFrame(() => target.scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
      block: 'start'
    }));
  });
  mobile.addEventListener('change', syncNavigation);
  syncNavigation();

  const details = el('panel-dialog');
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
  for (const dialog of [drawer, details]) {
    dialog.addEventListener('click', event => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
  }
})();
