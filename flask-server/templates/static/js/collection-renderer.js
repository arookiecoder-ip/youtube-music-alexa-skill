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
        (options.description ? '<div class="playlist-detail-hero-desc">' + esc(options.description) + '</div>' : '') +
        '<div class="playlist-detail-hero-meta">' + esc(options.meta || '') + '</div>' +
        renderActions(options) +
      '</div>' +
    '</div>';
  }

  function renderLoadingState(message) {
    return '<div class="playlist-loading-indicator visible" role="status" aria-live="polite">' +
      '<span class="playlist-loading-spinner" aria-hidden="true"></span>' +
      '<span>' + esc(message || 'Loading songs…') + '</span>' +
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

  window.CollectionRenderer = {
    esc: esc,
    renderDetailHero: renderDetailHero,
    renderLoadingState: renderLoadingState,
    renderCard: renderCard
  };
})();
