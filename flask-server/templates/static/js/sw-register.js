(function() {
  'use strict';
  if ('serviceWorker' in navigator && window.JAM_GUEST) {
    navigator.serviceWorker.getRegistrations()
      .then(function(regs) { regs.forEach(function(r) { r.unregister(); }); })
      .catch(function() {});
  }
  if ('serviceWorker' in navigator && !window.JAM_GUEST) {
    window.addEventListener('load', function() {
      navigator.serviceWorker.register('/service-worker.js').catch(function() {});
    });
  }
})();
