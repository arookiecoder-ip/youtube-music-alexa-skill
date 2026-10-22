(function () {
  'use strict';

  var esc = function (value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  var icons = {
    shuffle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
    queue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="14" y2="6"/><line x1="3" y1="12" x2="14" y2="12"/><line x1="3" y1="18" x2="11" y2="18"/><line x1="18" y1="13" x2="18" y2="21"/><line x1="14" y1="17" x2="22" y2="17"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    share: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>'
  };

  function actionButton(className, label, icon, disabled) {
    return '<button class="playlist-hero-btn ' + className + (disabled ? ' is-muted' : '') + '" type="button" title="' + esc(label) + '" aria-label="' + esc(label) + '"' + (disabled ? ' aria-disabled="true"' : '') + '>' + icons[icon] + '</button>';
  }

  function renderActions(options) {
    if (!options.showActions) return '';
    return '<div class="playlist-detail-hero-actions">' +
      '<div class="playlist-hero-actions-left">' +
        actionButton('playlist-hero-shuffle', 'Shuffle', 'shuffle', false) +
        actionButton('playlist-hero-add-queue', 'Add to queue', 'queue', false) +
      '</div>' +
      '<button class="playlist-hero-play' + (options.playClass ? ' ' + options.playClass : '') + '" type="button" aria-label="Play ' + esc(options.title) + '">' + icons.play + '</button>' +
      '<div class="playlist-hero-actions-right">' +
        (options.showShare === false ? '' : actionButton('playlist-hero-share', options.shareDisabled ? 'Sharing unavailable' : 'Share', 'share', !!options.shareDisabled)) +
        (options.showMore === false ? '' : actionButton('playlist-hero-more', options.moreDisabled ? 'Options unavailable' : 'More options', 'more', !!options.moreDisabled)) +
      '</div>' +
    '</div>';
  }

  function renderDetailHero(options) {
    return '<div' + (options.id ? ' id="' + esc(options.id) + '"' : '') + ' class="collection-detail-hero ' + esc(options.className || 'playlist-detail-hero') + '">' +
      (options.coverHtml || '') +
      '<div class="playlist-detail-hero-info">' +
        '<' + (options.titleTag || 'h2') + ' class="playlist-detail-page-title playlist-detail-hero-name">' + esc(options.title) + '</' + (options.titleTag || 'h2') + '>' +
        (options.artistHtml ? '<div class="album-artist-link">' + options.artistHtml + '</div>' : '') +
        (options.description ? '<details class="collection-description"><summary aria-label="Read collection description">Read more</summary><div class="playlist-detail-hero-desc">' + esc(options.description) + '</div></details>' : '') +
        '<div class="playlist-detail-hero-meta">' + esc(options.meta || '') + '</div>' +
        renderActions(options) +
      '</div>' +
    '</div>';
  }

  function renderLoadingState(message) {
    var label = message || 'Loading songs…';
    var rows = '';
    for (var i = 0; i < 7; i++) {
      rows += '<div class="collection-skeleton-row" aria-hidden="true">' +
        '<span class="collection-skeleton-number"></span>' +
        '<span class="collection-skeleton-thumb"></span>' +
        '<span class="collection-skeleton-copy"><span class="collection-skeleton-line collection-skeleton-line-title"></span><span class="collection-skeleton-line collection-skeleton-line-subtitle"></span></span>' +
        '<span class="collection-skeleton-action"></span>' +
      '</div>';
    }
    return '<div class="collection-detail-skeleton" role="status" aria-live="polite" aria-label="' + esc(label) + '">' +
      '<div class="collection-skeleton-hero" aria-hidden="true">' +
        '<span class="collection-skeleton-cover"></span>' +
        '<span class="collection-skeleton-hero-copy"><span class="collection-skeleton-line collection-skeleton-line-heading"></span><span class="collection-skeleton-line collection-skeleton-line-meta"></span><span class="collection-skeleton-actions"></span></span>' +
      '</div>' +
      '<div class="collection-skeleton-list history-list">' + rows + '</div>' +
    '</div>';
  }

  function renderCard(options) {
    var round = options.round ? ' round' : '';
    var play = options.showPlay ? '<button type="button" class="hscroll-play-btn" title="Play">' + icons.play + '</button>' : '';
    return '<div class="hscroll-card' + (options.cardClass ? ' ' + options.cardClass : '') + '">' +
      '<div class="hscroll-card-art' + round + '">' + (options.imageHtml || '') + play + '</div>' +
      '<div class="hscroll-card-title">' + esc(options.title) + '</div>' +
      (options.subtitle ? '<div class="hscroll-card-sub">' + esc(options.subtitle) + '</div>' : '') +
    '</div>';
  }

  function attachFloatingPlay(hero) {
    const play = hero && hero.querySelector('.playlist-hero-play');
    if (!play || !window.IntersectionObserver) return;
    const parent = hero.parentElement;
    if (parent.__floatingPlayCleanup) parent.__floatingPlayCleanup();
    const floating = document.createElement('button');
    floating.type = 'button';
    floating.className = 'collection-floating-play';
    floating.setAttribute('aria-label', play.getAttribute('aria-label') || 'Play collection');
    floating.innerHTML = icons.play;
    floating.setAttribute('aria-hidden', 'true');
    floating.tabIndex = -1;
    floating.addEventListener('click', () => play.click());
    // Keep viewport-fixed controls outside transformed/scrolling collection panes.
    document.body.appendChild(floating);
    const scrollRoot = hero.closest('.collection-detail-body');
    const root = scrollRoot && /auto|scroll/.test(getComputedStyle(scrollRoot).overflowY) ? scrollRoot : null;
    const shell = hero.closest('#collection-detail-shell');
    const title = hero.querySelector('.playlist-detail-hero-name');
    const back = shell && shell.querySelector('#playlist-detail-back');
    const header = shell && title ? document.createElement('div') : null;
    if (header) {
      header.className = 'collection-scroll-header';
      const backButton = document.createElement('button');
      backButton.type = 'button'; backButton.setAttribute('aria-label', 'Back');
      backButton.innerHTML = back ? back.innerHTML : '‹';
      backButton.addEventListener('click', () => { if (back) back.click(); });
      const heading = document.createElement('h3'); heading.textContent = title.textContent;
      header.append(backButton, heading); document.body.appendChild(header);
    }
    const description = hero.querySelector('.collection-description');
    const updateDescription = () => {
      const summary = description && description.querySelector('summary');
      if (summary) summary.textContent = description.open ? 'Show less' : 'Read more';
    };
    if (description) description.addEventListener('toggle', updateDescription);
    let visibilityObserver = null;
    let frame = 0;
    const scrollTarget = root || window;
    const update = () => {
      frame = 0;
      const top = root ? root.getBoundingClientRect().top : 0;
      const visible = play.getBoundingClientRect().bottom < top;
      floating.classList.toggle('is-visible', visible);
      floating.setAttribute('aria-hidden', String(!visible));
      floating.tabIndex = visible ? 0 : -1;
      if (header && title) {
        const appHeader = document.querySelector('main > header');
        const headerTop = appHeader ? Math.max(0, appHeader.getBoundingClientRect().bottom) : top;
        const bounds = shell.getBoundingClientRect();
        header.style.top = headerTop + 'px'; header.style.left = Math.max(0, bounds.left) + 'px';
        header.style.width = Math.min(bounds.width, window.innerWidth) + 'px';
        const scrolled = title.getBoundingClientRect().bottom < headerTop + header.offsetHeight;
        header.classList.toggle('is-visible', scrolled);
        header.inert = !scrolled;
      }
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    // A description collapse or fast scroll can skip directly from below to
    // above the viewport without crossing an observed intersection threshold.
    const observer = new IntersectionObserver(schedule, { root });
    observer.observe(play);
    scrollTarget.addEventListener('scroll', schedule, { passive: true, capture: true });
    window.addEventListener('resize', schedule);
    schedule();
    const cleanup = () => {
      observer.disconnect(); cancelAnimationFrame(frame); floating.remove();
      if (header) header.remove();
      if (visibilityObserver) visibilityObserver.disconnect();
      if (description) description.removeEventListener('toggle', updateDescription);
      scrollTarget.removeEventListener('scroll', schedule, true); window.removeEventListener('resize', schedule);
      window.removeEventListener('hashchange', cleanup);
    };
    if (window.MutationObserver) {
      const page = hero.closest('#collection-detail-page') || shell || parent;
      visibilityObserver = new MutationObserver(() => { if (page.hidden || !hero.isConnected) cleanup(); });
      visibilityObserver.observe(page, { attributes: true, attributeFilter: ['hidden'], childList: true, subtree: true });
    }
    parent.__floatingPlayCleanup = cleanup;
    window.addEventListener('hashchange', cleanup);
  }

  window.CollectionRenderer = {
    esc: esc,
    renderDetailHero: renderDetailHero,
    renderLoadingState: renderLoadingState,
    renderCard: renderCard,
    attachFloatingPlay: attachFloatingPlay
  };
})();
