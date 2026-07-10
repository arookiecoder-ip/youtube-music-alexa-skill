(function() {
  'use strict';
  if ('serviceWorker' in navigator && window.JAM_GUEST) {
    navigator.serviceWorker.getRegistrations()
      .then(function(regs) { regs.forEach(function(r) { r.unregister(); }); })
      .catch(function() {});
  }
  if ('serviceWorker' in navigator && !window.JAM_GUEST) {
    window.addEventListener('load', function() {
      navigator.serviceWorker.register('/service-worker.js').then(function(reg) {
        // A worker already waiting means an update is pending from a previous
        // visit (the SW skipWaiting()s, so this is rare but possible).
        if (reg.waiting && window.showUpdateNotification) {
          window.showUpdateNotification();
        }

        // Listen for new updates
        reg.addEventListener('updatefound', function() {
          var newWorker = reg.installing;
          if (!newWorker) return;

          newWorker.addEventListener('statechange', function() {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              if (window.showUpdateNotification) {
                window.showUpdateNotification();
              }
            }
          });
        });
      }).catch(function() {});

      // Listen for and broadcast state changes across tabs. The SW also posts
      // 'sw-update' when it activates on a first install — only treat it as an
      // update if this page was already controlled by a previous SW.
      var hadController = !!navigator.serviceWorker.controller;
      navigator.serviceWorker.addEventListener('message', function(event) {
        if (event.data && event.data.type === 'sw-update') {
          if (hadController && window.showUpdateNotification) {
            window.showUpdateNotification();
          }
        }
      });
    });
  }
})();
