/* Runs after the sheet's app.js: every few seconds, if the labels changed, send the state
 * and the TSV rows to the server (state URL = this page + "state"). Shows the save status
 * in the corner; a failed save is retried on the next tick. */
(function () {
  'use strict';
  var app = window.__app;
  if (!app) return;
  var url = window.location.pathname.replace(/\/?$/, '/') + 'state';
  var key = 'qa202610:' + window.CFG.task + ':' + window.CFG.labeller + ':' + window.DATA.version;
  var last = JSON.stringify(app.st()), busy = false;

  var badge = document.createElement('div');
  badge.style.cssText = 'position:fixed;right:12px;bottom:12px;padding:4px 10px;border-radius:6px;' +
    'font:13px system-ui,sans-serif;background:#1f7a4d;color:#fff;z-index:99';
  badge.textContent = 'Saved to server';
  document.body.appendChild(badge);
  function show(text, ok) { badge.textContent = text; badge.style.background = ok ? '#1f7a4d' : '#b3261e'; }

  function body() { return JSON.stringify({ state: app.st(), rows: app.exportRows() }); }

  function tick() {
    var now = JSON.stringify(app.st());
    if (busy || now === last) return;
    try { window.localStorage.setItem(key + ':t', String(Date.now())); } catch (e) { /* ignore */ }
    busy = true;
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body(),
                 credentials: 'same-origin' })
      .then(function (r) {
        // a signed-out session is redirected to the login page, which itself answers 200
        if (r.redirected) show('Signed out: reload the page and sign in; nothing is lost', false);
        else if (r.ok) { last = now; show('Saved to server ' + new Date().toLocaleTimeString(), true); }
        else show('Not saved (' + r.status + '): retrying', false);
      })
      .catch(function () { show('Offline: not saved, retrying', false); })
      .then(function () { busy = false; });
  }
  setInterval(tick, 3000);
  // last chance when the tab closes
  window.addEventListener('pagehide', function () {
    if (JSON.stringify(app.st()) !== last && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body()], { type: 'application/json' }));
    }
  });
})();
