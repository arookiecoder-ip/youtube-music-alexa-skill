(function () {
  'use strict';
  const {request, Poller} = window.StatusDashboard;
  const el = id => document.getElementById(id);
  const node = (tag, text, className) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; };
  const stamp = at => new Date(at * 1000).toLocaleString();
  const dialog = el('status-dialog'), body = el('dialog-body'), controls = el('dialog-controls'), state = el('dialog-state'), more = el('dialog-more');
  let dialogController, dialogEpoch = 0, loadMore = null, lastInboxAt = -1, errorEntries = [];
  function inboxSummary(summary) {
    if ((summary.as_of || 0) < lastInboxAt) return;
    lastInboxAt = summary.as_of || lastInboxAt;
    el('error-count').textContent = summary.available && summary.unseen != null ? String(summary.unseen) : '—';
    el('error-inbox').classList.toggle('has-errors', summary.unseen > 0);
    el('error-inbox').title = summary.available ? `${summary.unseen} unseen errors; up to 24-hour retention` : 'Error journal unavailable';
  }
  window.addEventListener('status-snapshot', event => inboxSummary(event.detail));
  function open(title) {
    if (dialogController) dialogController.abort();
    dialogEpoch++; body.replaceChildren(); controls.replaceChildren(); more.hidden = true; loadMore = null;
    el('dialog-title').textContent = title; state.textContent = ''; state.className = '';
    if (!dialog.open) dialog.showModal();
  }
  function loading() { state.className = 'loading-state'; state.textContent = 'Loading…'; }
  function message(text) { state.className = ''; state.textContent = text; }
  async function fetchDialog(path, render, options) {
    if (dialogController) dialogController.abort();
    const controller = new AbortController(), epoch = ++dialogEpoch;
    dialogController = controller; loading();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const data = await request(path, controller.signal, options);
      if (!dialog.open || epoch !== dialogEpoch) return;
      message(''); render(data);
    } catch (error) {
      if (!dialog.open || epoch !== dialogEpoch) return;
      message(error.status === 401 || error.status === 403 ? 'Your owner session expired or access was denied. Sign in again, then reopen these details.' : error.status === 404 ? 'This entry expired or is no longer retained.' : 'Could not load details. Use refresh or reopen to retry.');
    } finally { clearTimeout(timeout); }
  }
  dialog.addEventListener('close', () => { dialogEpoch++; if (dialogController) dialogController.abort(); });
  el('dialog-close').addEventListener('click', () => dialog.close());
  more.addEventListener('click', () => { if (loadMore) loadMore(); });
  function button(text, action) { const b = node('button', text); b.type = 'button'; b.addEventListener('click', action); return b; }
  function logRow(row, focus = false) {
    const wrapper = node('article', null, `log-row ${row.is_error ? 'log-error' : ''}${focus ? ' focused-log' : ''}`);
    const header = node('div', null, 'log-row-head');
    header.append(node('time', stamp(row.at)), node('span', row.level, `log-level level-${row.level}`), node('span', row.source));
    if (row.is_error) header.append(node('span', row.seen ? 'Seen' : 'Unseen', 'badge neutral'));
    wrapper.append(header, node('pre', row.message));
    if (row.route) wrapper.append(node('small', row.route, 'muted'));
    if (row.request_id) wrapper.append(node('small', `Request ${row.request_id.slice(0, 12)}`, 'log-correlation'));
    if (row.is_error && !focus) wrapper.append(button('View error & context', () => showError(row.id)));
    if (focus && row.trace) wrapper.append(node('pre', row.trace, 'exception-trace'));
    return wrapper;
  }
  const labels = {audio_lookups:'Saved audio file checks', audio_responses:'Audio response cache', account_ytmusic_clients:'Account API clients', public_ytmusic_clients:'Public API clients', audio_search:'Audio resolver results', stream_search:'Playback stream lists', playlist_tracks:'Playlist track lists'};
  function showCache(name) {
    open(labels[name] || name);
    const filter = node('select'); filter.setAttribute('aria-label', 'Cache outcome');
    for (const [value, label] of [['','Hits and misses'],['hit','Hits only'],['miss','Misses only']]) { const option = node('option', label); option.value = value; filter.append(option); }
    let entries = [];
    function load(before) {
      const query = new URLSearchParams({outcome: filter.value, limit: '50'}); if (before) query.set('before', before);
      fetchDialog(`/api/status/cache/${encodeURIComponent(name)}?${query}`, data => {
        entries = before ? entries.concat(data.entries) : data.entries;
        body.replaceChildren(node('p', `Since restart: ${data.totals.hits || 0} hits · ${data.totals.misses || 0} misses. Showing retained events (maximum 200, up to 24 hours).`, 'help'));
        if (data.usage && data.usage.description) body.append(node('p', data.usage.description, 'help'));
        if (data.usage && data.usage.activity && data.usage.activity.length) {
          const usage = node('section', null, 'cache-route-activity');
          usage.append(node('h3', 'Related endpoint requests'));
          usage.append(node('p', 'Request counts include calls that bypass this cache. They are not cache hits or misses.', 'help'));
          for (const row of data.usage.activity) usage.append(node('p', `${row.route}: ${row.requests} requests · ${row.errors} errors`, 'log-correlation'));
          body.append(usage);
        }
        if (!entries.length) body.append(node('p', data.totals.hits || data.totals.misses ? 'No retained lookups match this filter.' : 'This cache has not been used in this server process. Its lookup history will appear here when the described path uses it.', 'empty'));
        for (const entry of entries) {
          const row = node('article', null, 'cache-event');
          row.append(node('span', entry.outcome, `log-level ${entry.outcome}`), node('time', stamp(entry.at)), node('strong', entry.video_id || (entry.key ? `Key ${entry.key}` : 'Identifier not retained')), node('small', entry.reason || 'lookup', 'muted'));
          body.append(row);
        }
        body.append(node('p', 'Identifiers are hashed except valid video IDs. A lookup is not proof that media is complete or valid. Totals can exceed the retained detail history.', 'help'));
        more.hidden = !data.next_before; loadMore = () => load(data.next_before);
      });
    }
    filter.addEventListener('change', () => load());
    controls.append(filter, button('Refresh details', () => load())); load();
  }
  el('audio-cache-details').addEventListener('click', () => showCache('audio_lookups'));
  el('cache-layer-cards').addEventListener('click', event => { const card = event.target.closest('[data-cache-name]'); if (card) showCache(card.dataset.cacheName); });
  async function markSeen(ids, done) {
    if (!ids.length) return;
    await fetchDialog('/api/status/errors/seen', summary => { inboxSummary(summary); done(); }, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ids})});
  }
  function showInbox() {
    open('Error alerts'); errorEntries = [];
    const seenButton = button('Mark displayed errors as seen', () => markSeen(errorEntries.filter(row => !row.seen).map(row => row.id).slice(0, 100), () => showInbox()));
    seenButton.id = 'mark-visible-seen';
    function load(before) {
      fetchDialog('/api/status/errors?limit=50'+(before ? '&before='+before : ''), data => {
        errorEntries = before ? errorEntries.concat(data.entries).slice(-100) : data.entries;
        inboxSummary(data.summary); body.replaceChildren();
        body.append(node('p', 'Opening an error does not mark it as seen. Entries expire after 24 hours; storage caps can remove older entries sooner.', 'help'));
        if (!errorEntries.length) body.append(node('p', 'No retained errors.', 'empty'));
        for (const row of errorEntries) body.append(logRow(row));
        more.hidden = !data.next_before; loadMore = () => load(data.next_before);
        seenButton.disabled = !errorEntries.some(row => !row.seen);
      });
    }
    controls.append(button('Refresh errors', () => load()), seenButton); load();
  }
  function showError(id) {
    open('Error details');
    controls.append(button('Back to alerts', showInbox), button('Refresh context', () => showError(id)));
    fetchDialog('/api/status/errors/'+encodeURIComponent(id), data => {
      body.replaceChildren(logRow(data.entry, true));
      const seen = button(data.entry.seen ? 'Already seen' : 'Mark as seen', () => markSeen([id], () => showError(id)));
      seen.disabled = !!data.entry.seen; controls.append(seen);
      for (const [title, entries] of [['Earlier retained logs', data.earlier], ['Following retained logs', data.later]]) {
        const details = node('details'), summary = node('summary', `${title} (${entries.length})`); details.append(summary);
        details.append(node('p', 'Nearby journal entries can include other requests. Compare request IDs and timestamps; context may be truncated by retention.', 'help'));
        for (const row of entries) details.append(logRow(row, row.id === id));
        body.append(details);
      }
    });
  }
  el('error-inbox').addEventListener('click', showInbox);

  // No log list requests until the user opens the section. Pause polling off-screen.
  let logsOpened = false, live = false, intersecting = false, filterEpoch = 0, wantsLatest = false, olderController, logEntries = [], nextBefore = null;
  const panel = el('live-log-section'), logState = el('log-state'), list = el('live-logs');
  function query(before) {
    const params = new URLSearchParams({level:el('log-level').value, source:el('log-source').value, q:el('log-search').value, limit:'50'});
    if (before) params.set('before', before); return params;
  }
  function showLogs(data, append) {
    logEntries = append ? logEntries.concat(data.entries).slice(0, 300) : data.entries;
    nextBefore = logEntries.length < 300 ? data.next_before : null;
    list.replaceChildren(); for (const row of logEntries) list.append(logRow(row));
    if (!logEntries.length) list.append(node('p', 'No retained logs match these filters.', 'empty'));
    logState.className = 'help'; logState.textContent = `${live ? 'Live' : 'Paused'} · ${logEntries.length} entries · retained up to 24 hours${data.summary.dropped ? ` · ${data.summary.dropped} entries dropped under load` : ''}`;
    const source = el('log-source'), selected = source.value;
    source.replaceChildren(node('option', 'All sources')); source.firstChild.value = '';
    for (const name of new Set([...(data.sources || []), ...(selected ? [selected] : [])])) { const option = node('option', name); option.value = name; source.append(option); } source.value = selected;
    el('log-older').hidden = !nextBefore; inboxSummary(data.summary);
  }
  const poller = new Poller({
    load: async signal => { const epoch = filterEpoch; return {epoch, data:await request('/api/status/logs?'+query(), signal)}; },
    onData: value => { if (value.epoch === filterEpoch) { wantsLatest=false; showLogs(value.data, false); } },
    onError: error => { wantsLatest=false; logState.className='help'; logState.textContent = error.status === 401 || error.status === 403 ? 'Owner session expired. Sign in again.' : 'Logs unavailable. Keeping the last loaded entries; use Load latest to retry.'; },
    visible: () => logsOpened && !document.hidden && intersecting, interval: () => 5000,
    schedule: (fn, ms) => setTimeout(fn, ms), cancel: id => clearTimeout(id)
  });
  poller.paused = true;
  function toggle(value) { live = value; el('log-live').textContent = live ? 'Pause live logs' : 'Start live logs'; el('log-live').setAttribute('aria-pressed', String(live)); poller.setPaused(!live); }
  function latest() { logsOpened = true; wantsLatest = true; if (olderController) olderController.abort(); filterEpoch++; logState.textContent = 'Loading logs…'; logState.className = 'help loading-state'; poller.refresh(true); }
  el('log-live').addEventListener('click', () => { logsOpened=true; if (!live) { filterEpoch++; logState.textContent='Loading logs…'; logState.className='help loading-state'; } toggle(!live); });
  el('log-refresh').addEventListener('click', latest);
  let debounce;
  el('log-search').addEventListener('input', () => { clearTimeout(debounce); debounce=setTimeout(latest,300); });
  for (const id of ['log-level','log-source']) el(id).addEventListener('change', latest);
  el('log-older').addEventListener('click', async () => {
    if (!nextBefore || olderController) return;
    toggle(false); filterEpoch++; if (poller.controller) poller.controller.abort();
    const epoch = filterEpoch, controller = new AbortController(); olderController=controller;
    const timeout=setTimeout(()=>controller.abort(),10000);
    logState.className='help loading-state'; logState.textContent='Loading older logs…';
    try { const data=await request('/api/status/logs?'+query(nextBefore), controller.signal); if (epoch===filterEpoch) showLogs(data,true); }
    catch (_) { if(epoch===filterEpoch) { logState.className='help'; logState.textContent='Could not load older logs. Retry.'; } }
    finally { clearTimeout(timeout); if (olderController===controller) olderController=null; }
  });
  function logVisibility() {
    if (document.hidden || !intersecting) {
      poller.epoch++; poller.clear(); if (poller.controller) poller.controller.abort();
      if (olderController) olderController.abort();
    } else if (logsOpened && (live || wantsLatest)) poller.refresh(true);
  }
  if ('IntersectionObserver' in window) new IntersectionObserver(entries => { intersecting=entries[0].isIntersecting; logVisibility(); }, {rootMargin:'100px'}).observe(panel);
  else intersecting=true;
  document.addEventListener('visibilitychange', logVisibility);
})();
