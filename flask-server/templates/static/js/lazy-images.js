(function () {
  'use strict';

  // Native loading="lazy" is deliberately supplemented here because long
  // scroll containers can still start hundreds of requests at once. Keep the
  // URL out of src until the image is close to the viewport.
  const pending = new WeakSet();
  const reveal = function (img) {
    if (!img || img.dataset.imageReady === 'true') return;
    const expectedSrc = img.currentSrc || img.src;
    const finish = function () {
      if ((img.currentSrc || img.src) === expectedSrc) img.dataset.imageReady = 'true';
    };
    // `load` means the file is fully downloaded; `decode` prevents browsers
    // from painting progressive JPEG passes before that finished bitmap is
    // ready for display.
    if (typeof img.decode === 'function') {
      img.decode().catch(function () {}).then(finish);
    } else {
      finish();
    }
  };
  const prepareReveal = function (img) {
    if (!img || img.dataset.imageRevealTracked !== undefined) return;
    img.dataset.imageRevealTracked = '';
    img.addEventListener('load', function () { reveal(img); });
    // Do not leave a failed image permanently transparent: its fallback/alt
    // state still needs to be visible.
    img.addEventListener('error', function () { img.dataset.imageReady = 'true'; });
    if (img.complete && (img.currentSrc || img.getAttribute('src'))) reveal(img);
  };
  const load = function (img) {
    const src = img.getAttribute('data-lazy-src');
    if (!src) return;
    delete img.dataset.imageReady;
    img.src = src;
    img.removeAttribute('data-lazy-src');
  };
  const observe = function (img) {
    if (!img || img.dataset.lazyObserved !== undefined) return;
    if (img.hasAttribute('data-src')) {
      img.setAttribute('data-lazy-src', img.getAttribute('data-src'));
      img.removeAttribute('data-src');
    }
    if (img.getAttribute('loading') !== 'lazy' || !img.getAttribute('src')) {
      if (!img.hasAttribute('data-lazy-src')) return;
    } else {
      img.setAttribute('data-lazy-src', img.getAttribute('src'));
      img.removeAttribute('src');
    }
    img.dataset.lazyObserved = '';
    pending.add(img);
    if (!window.__lazyImageObserver) return;
    let root = null;
    let parent = img.parentElement;
    while (parent && parent !== document.body) {
      const style = window.getComputedStyle(parent);
      if (/(auto|scroll|overlay)/.test(style.overflowY) || /(auto|scroll|overlay)/.test(style.overflow)) {
        root = parent;
        break;
      }
      parent = parent.parentElement;
    }
    // One observer can use only one root, so use a per-container observer for
    // nested scrolling surfaces such as playlist/history bodies.
    if (root) {
      const key = '__lazyObserver';
      if (!root[key]) root[key] = new IntersectionObserver(function (entries, observer) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          load(entry.target);
          observer.unobserve(entry.target);
        });
      }, { root: root, rootMargin: '120px 0px' });
      root[key].observe(img);
    } else {
      window.__lazyImageObserver.observe(img);
    }
  };
  window.__lazyImageObserver = new IntersectionObserver(function (entries, observer) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      load(entry.target);
      observer.unobserve(entry.target);
    });
  }, { rootMargin: '120px 0px' });

  const scan = function (root) {
    if (!root || !root.querySelectorAll) return;
    if (root.tagName === 'IMG') {
      prepareReveal(root);
      observe(root);
    }
    root.querySelectorAll('img').forEach(prepareReveal);
    root.querySelectorAll('img[loading="lazy"], img[data-src]').forEach(observe);
  };
  scan(document);
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      if (record.type === 'attributes' && record.target.tagName === 'IMG') {
        delete record.target.dataset.imageReady;
        prepareReveal(record.target);
        if (record.target.complete && (record.target.currentSrc || record.target.getAttribute('src'))) reveal(record.target);
        return;
      }
      record.addedNodes.forEach(function (node) {
        if (node.nodeType === 1) scan(node);
      });
    });
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
})();
