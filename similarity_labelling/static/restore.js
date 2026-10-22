/* Runs after data.js and before the sheet's app.js: if the server's copy of this sheet is
 * newer than the one in this browser, put it where app.js looks (its localStorage key).
 * sync.js records in <key>:t when this browser last changed the state. */
(function () {
  'use strict';
  var server = window.SERVER_STATE;
  if (!server) return;
  var key = 'qa202610:' + window.CFG.task + ':' + window.CFG.labeller + ':' + window.DATA.version;
  try {
    var localT = +(window.localStorage.getItem(key + ':t') || 0);
    if (!window.localStorage.getItem(key) || server.updated_ms >= localT) {
      window.localStorage.setItem(key, JSON.stringify(server.state));
      window.localStorage.setItem(key + ':t', String(server.updated_ms));
    }
  } catch (e) { /* no storage: app.js warns, and sync.js still saves to the server */ }
})();
