(function (root) {
  'use strict';
  const number = (value, digits = 0) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: digits}) : '—';
  function bytes(value) {
    if (!Number.isFinite(value) || value < 0) return '—';
    if (!value) return '0 B';
    const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
    return `${number(value / 1024 ** unit, unit ? 1 : 0)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
  }
  function duration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '—';
    if (seconds >= 86400) return `${number(seconds / 86400, 1)} days`;
    if (seconds >= 3600) return `${number(seconds / 3600, 1)} hours`;
    if (seconds >= 60) return `${number(seconds / 60, 1)} min`;
    return `${number(seconds)} sec`;
  }
  const time = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleTimeString() : '—';
  const array = value => Array.isArray(value) ? value : [];




  class Poller {
    constructor(options) { Object.assign(this, options); this.busy = false; this.paused = false; this.stopped = false; this.failures = 0; this.timer = null; this.again = false; this.epoch = 0; }
    clear() { if (this.timer !== null) this.cancel(this.timer); this.timer = null; }
    visibilityChanged() {
      this.clear();
      if (!this.visible()) { this.epoch++; if (this.controller) this.controller.abort(); }
      else this.refresh(true);
    }
    setPaused(paused) { this.paused = paused; this.clear(); if (!paused) this.refresh(true); }
    async refresh(manual = false) {
      if (this.stopped || !this.visible() || (this.paused && !manual)) return;
      if (this.busy) { this.again = this.again || manual; return; }
      this.clear(); this.busy = true; const controller = new AbortController(), epoch = this.epoch; this.controller = controller;
      const timeout = this.schedule(() => controller.abort(), 10000);
      try {
        const data = await this.load(controller.signal);
        if (this.visible() && !this.stopped && epoch === this.epoch) { this.failures = 0; this.onData(data); }
      } catch (error) {
        if (epoch === this.epoch && (error.name !== 'AbortError' || this.visible())) {
          this.failures++;
          if (error.status === 401 || error.status === 403) this.stopped = true;
          this.onError(error);
        }
      } finally {
        this.cancel(timeout); this.busy = false;
        if (!this.stopped && this.visible() && (!this.paused || this.again)) {
          const again = this.again; this.again = false;
          this.timer = this.schedule(() => this.refresh(again), again ? 0 : Math.min(60000, this.interval() * 2 ** Math.min(this.failures, 3)));
        }
      }
    }
  }
  const exports = {bytes, duration, Poller};
  if (typeof module !== 'undefined' && module.exports) module.exports = exports;
  root.StatusDashboard = exports;
  if (typeof document === 'undefined') return;

  const el = id => document.getElementById(id);
  const text = (id, value) => { el(id).textContent = value; };
  const badge = (value, level = 'neutral') => { const span = document.createElement('span'); span.className = `badge ${['warning', 'critical', 'neutral'].includes(level) ? level : ''}`; span.textContent = value; return span; };
  function rows(id, values) {
    const target = el(id); target.replaceChildren();
    for (const [key, value] of values) { const div = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = key; dd.textContent = value; div.append(dt, dd); target.append(div); }
  }
  function table(id, values, columns) {
    const target = el(id); target.replaceChildren();
    if (!values.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = columns; td.className = 'empty'; td.textContent = 'No matching data yet'; tr.append(td); target.append(tr); return; }
    for (const valuesInRow of values) { const tr = document.createElement('tr'); for (const value of valuesInRow) { const td = document.createElement('td'); if (value instanceof Node) td.append(value); else td.textContent = value; tr.append(td); } target.append(tr); }
  }
  function activities(id, entries) {
    const target = el(id); target.replaceChildren();
    for (const row of entries) { const div = document.createElement('div'), span = document.createElement('span'), small = document.createElement('small'); div.className = `activity${row.error ? ' error' : ''}`; span.textContent = row.title; small.textContent = row.detail; div.append(span, small); target.append(div); }
    if (!entries.length) { const p = document.createElement('p'); p.className = 'help'; p.textContent = 'No activity recorded'; target.append(p); }
  }
  let snapshot = null;

  function chart(history) {
    const canvas = el('traffic'), ctx = canvas.getContext('2d'); if (!ctx) return;
    const width = canvas.clientWidth || 600, height = 180, scale = Math.min(root.devicePixelRatio || 1, 2);
    canvas.width = width * scale; canvas.height = height * scale; ctx.scale(scale, scale);
    const values = array(history), peak = Math.max(1, ...values.map(row => row.requests));
    ctx.strokeStyle = '#303030'; ctx.lineWidth = 1;
    for (let i = 0; i < 4; i++) { const y = 10 + i * 50; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
    for (const [key, color] of [['requests', '#e8590c'], ['errors', '#ff938d']]) {
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 2;
      values.forEach((row, i) => { const x = i / Math.max(1, values.length - 1) * width, y = height - 8 - (row[key] || 0) / peak * 150; if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke();
    }
    canvas.setAttribute('aria-label', `Requests over 60 minutes: ${values.reduce((sum, row) => sum + row.requests, 0)}; errors: ${values.reduce((sum, row) => sum + row.errors, 0)}`);
  }

  function meter(id, percent, label) {
    const target = el(id);
    target.setAttribute('aria-valuemin', '0'); target.setAttribute('aria-valuemax', '100');
    if (Number.isFinite(percent)) target.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, percent))));
    else target.removeAttribute('aria-valuenow');
    target.setAttribute('aria-valuetext', label);
  }
  const percentWidth = percent => `${Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0}%`;

  function connectionTiles(cookies) {
    const download = cookies.downloads || {}, target = el('connection-tiles'); target.replaceChildren();
    const entries = [
      ['Download cookies', !download.present ? 'Not configured' : download.format_valid === false ? 'Invalid export' : download.youtube_active === 0 ? 'No active YouTube cookies' : download.validation === true ? 'Last audio test passed' : download.validation === false ? 'Last audio test failed' : 'Saved · audio access not tested', download.validation === true ? 'ok' : 'warning'],
      ['YouTube Music', (cookies.youtube_headers || {}).present ? 'Account headers saved · not probed' : 'Account headers not configured', (cookies.youtube_headers || {}).present ? 'ok' : 'warning'],
      ['Amazon / Alexa', cookies.amazon_session_in_memory ? 'Session loaded · reachability not probed' : 'No session loaded', cookies.amazon_session_in_memory ? 'ok' : 'warning']
    ];
    for (const [name, detail, state] of entries) {
      const tile = document.createElement('div'), dot = document.createElement('i'), content = document.createElement('div'), title = document.createElement('strong'), small = document.createElement('small');
      tile.className = `connection-tile ${state}`; dot.className = 'status-dot'; title.textContent = name; small.textContent = detail;
      content.append(title, small); tile.append(dot, content); target.append(tile);
    }
  }

  function render(data) {
    snapshot = data;
    const request = data.requests || {}, storage = data.storage || {}, system = storage.system || {}, fetching = data.fetching || {}, cookies = data.cookies || {}, download = cookies.downloads || {}, disk = system.disk || {}, process = system.process || {}, memory = system.memory || {};
    el('notice').hidden = true; el('export').disabled = false;
    const status = ['healthy', 'warning', 'critical'].includes(data.health) ? data.health : 'warning';
    el('health').className = `badge ${status === 'healthy' ? '' : status}`;
    text('health', {healthy: 'Operational', warning: 'Needs attention', critical: 'Critical'}[status]);
    text('updated', `Updated ${time(data.generated_at)}`);
    root.dispatchEvent(new CustomEvent('status-snapshot', {detail: data.error_inbox || {}}));
    text('uptime', `Up ${duration(data.uptime_seconds)}`);
    text('rpm', number((array(data.history).at(-1) || {}).requests || 0));
    text('request-total', `${number(request.total || 0)} total · ${number(request.active || 0)} active handlers`);
    text('latency', Number.isFinite(request.p95_ms) ? `${number(request.p95_ms)} ms` : '—');
    text('free-disk', bytes(disk.free_bytes)); text('disk-detail', `${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}`);
    el('disk-meter').style.width = percentWidth(disk.used_percent);
    text('disk-percent', Number.isFinite(disk.used_percent) ? `${number(disk.used_percent, 1)}%` : 'Unknown');
    meter('disk-usage', disk.used_percent, `${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}`);
    const memoryPercent = process.memory_limit_bytes > 0 && Number.isFinite(process.memory_current_bytes) ? process.memory_current_bytes / process.memory_limit_bytes * 100 : null;
    el('memory-meter').style.width = percentWidth(memoryPercent); text('memory-value', `${bytes(process.memory_current_bytes)} / ${bytes(process.memory_limit_bytes)}`);
    meter('memory-usage', memoryPercent, memoryPercent == null ? 'Container memory limit unavailable' : `${number(memoryPercent, 1)}% of container memory limit`);
    connectionTiles(cookies);
    const slots = el('fetch-slots'); slots.replaceChildren();
    const active = fetching.active_streams || 0;
    const budget = Number.isFinite(fetching.stream_slots) ? Math.max(0, Math.min(32, fetching.stream_slots)) : 0;
    for (let i = 0; i < budget; i++) { const slot = document.createElement('span'); slot.className = i < active ? 'busy' : ''; slot.setAttribute('aria-hidden', 'true'); slots.append(slot); }
    slots.setAttribute('aria-label', `${number(active)} active streams; ${number(fetching.stream_slots)} configured slots`);
    text('request-health', `${number((request['4xx'] || 0) + (request['5xx'] || 0))} HTTP errors · p50 ${number(request.p50_ms)} ms`);
    rows('system', [['Host load · 1 / 5 / 15 min', array(system.load_average).map(v => number(v, 2)).join(' / ') || 'Unavailable'], ['Host logical CPUs', number(system.cpu_count)], ['Process RSS / threads', `${bytes(process.rss_bytes)} / ${number(system.threads)}`], ['Host available / total RAM', `${bytes(memory.MemAvailable)} / ${bytes(memory.MemTotal)}`], ['Container memory / limit', `${bytes(process.memory_current_bytes)} / ${bytes(process.memory_limit_bytes)}`], ['Container CPU quota', process.cpu_quota == null ? 'Unlimited / unavailable' : `${number(process.cpu_quota, 2)} CPUs`], ['Database size', bytes((system.database || {}).bytes)], ['Storage snapshot age', duration(Math.max(0, data.generated_at - (storage.captured_at || data.generated_at)))]]);
    rows('cookies', [['Download export', download.present ? `Present · ${bytes(download.bytes)}` : 'Not configured'], ['Export modified', time(download.modified_at)], ['Cookie format', download.format_valid == null ? 'Not inspected' : download.format_valid ? 'Netscape export' : 'Invalid export'], ['Cookies · active YouTube / expired', `${number(download.youtube_active)} / ${number(download.expired_count)}`], ['Session cookies / next expiry', `${number(download.session_count)} / ${Number.isFinite(download.next_expiry) ? new Date(download.next_expiry * 1000).toLocaleString() : 'No known expiry'}`], ['Last audio validation', download.validation == null ? 'Not tested for this file' : download.validation ? 'Passed' : 'Failed'], ['Validation age', duration(download.validation_age_seconds)], ['Cookie probe', download.probe_busy ? 'Running' : 'Idle'], ['YouTube account headers', (cookies.youtube_headers || {}).present ? 'Configured · not probed' : 'Not configured'], ['Amazon session', cookies.amazon_session_in_memory ? 'Present in memory · not probed' : 'Not loaded']]);
    el('cooldown').className = `badge ${fetching.cooldown_seconds > 0 ? 'warning' : 'neutral'}`;
    text('cooldown', fetching.cooldown_seconds > 0 ? `Cooldown ${duration(fetching.cooldown_seconds)}` : 'No cooldown');
    rows('fetching', [['Queued + running calls', number(fetching.queued_and_running)], ['Resolver / prefetch / stream budgets', `${number(fetching.resolver_slots)} / ${number(fetching.prefetch_slots)} / ${number(fetching.stream_slots)}`], ['Active streams', number(fetching.active_streams)], ['Preparation jobs · completed / failed', `${number((data.fetches || {}).succeeded || 0)} / ${number((data.fetches || {}).failed || 0)}`], ['Known dead / temporarily flaky', `${number(fetching.known_dead)} / ${number(fetching.temporarily_flaky)}`], ['Playback confirmed / queue', `${(data.playback || {}).confirmed ? 'Yes' : 'No'} / ${number((data.playback || {}).queue_length)} tracks`]]);
    const summary = el('fetch-summary'); summary.replaceChildren();
    for (const [label, value] of [['Streaming', active], ['Waiting / running', fetching.queued_and_running || 0], ['Prepared URLs', (fetching.preparation || {}).completed || 0], ['Failed preparations', (fetching.preparation || {}).failed || 0]]) {
      const tile = document.createElement('div'), count = document.createElement('strong'), caption = document.createElement('span');
      count.textContent = number(value); caption.textContent = label; tile.append(count, caption); summary.append(tile);
    }
    const current = [...array(data.active_fetches).map(row => ({title: `${row.video_id} · ${row.mode === 'prefetch' ? 'prefetch' : 'download'} · ${row.client}`, detail: `Active for ${duration(data.generated_at - row.started_at)}`})), ...array(fetching.streams).map(row => ({title: `${row.video_id} · live stream`, detail: `Active ${duration(row.age_seconds)}`}))];
    const recent = array(data.recent_fetches).slice(0, 12).map(row => ({title: `${row.video_id} · ${row.mode === 'prefetch' ? 'prefetch' : 'download'} · ${row.status}`, detail: `${time(row.at)} · ${number(row.duration_ms)} ms · ${row.client}`, error: row.status === 'failed'}));
    activities('active-fetches', [...current, ...recent]);
    if (!current.length && !recent.length) el('active-fetches').textContent = 'No active audio streams. Paused tracks retain only their temporary current-track bytes.';
    table('routes', array(data.routes).map(row => [row.route, number(row.requests), number(row.errors), `${number(row.average_ms)} ms`, `${number(row.max_ms)} ms`]), 5);
    activities('events', array(data.events).map(row => ({title: row.kind === 'fetch' ? `${row.video_id} · ${row.client} · ${row.status}` : `${row.label} · HTTP ${row.status}`, detail: `${time(row.at)} · ${number(row.duration_ms)} ms`, error: row.status === 'failed' || row.status >= 400})));
    const alerts = el('alerts'); alerts.replaceChildren();
    for (const row of array(data.alerts)) { const div = document.createElement('div'), title = document.createElement('strong'), detail = document.createElement('small'); div.className = row.level === 'critical' ? 'critical' : ''; title.textContent = row.title; detail.textContent = row.detail; div.append(title, detail); alerts.append(div); }
    chart(data.history);
  }
  const url = new URL(root.location.href), apiKey = url.searchParams.get('key');
  if (apiKey) { url.searchParams.delete('key'); root.history.replaceState(null, '', url.pathname + url.search + url.hash); }
  const headers = apiKey ? {'X-Api-Key': apiKey} : {};
  async function get(path, signal, options = {}) {
    const response = await fetch(path, {...options, credentials: 'same-origin', cache: 'no-store', headers: {...headers, ...(options.headers || {})}, signal});
    if (!response.ok) { const error = new Error(`Status request failed (HTTP ${response.status})`); error.status = response.status; throw error; }
    if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Invalid status response');
    return response.json();
  }
  root.StatusDashboard.request = get;
  const poller = new Poller({load: async signal => { const data = await get('/api/status', signal); if (!Number.isFinite(data.generated_at) || !data.requests) throw new Error('Incomplete status response'); return data; },
    onData: render, onError: error => { el('notice').hidden = false; text('notice', error.status === 401 || error.status === 403 ? 'Your owner session expired. Sign in again, then reload this page.' : `Status is temporarily unavailable. ${snapshot ? `Keeping the snapshot from ${time(snapshot.generated_at)}.` : 'Retrying automatically.'}`); text('health', 'Connection interrupted'); el('health').className = 'badge warning'; },
    visible: () => !document.hidden, interval: () => Number(el('interval').value) * 1000,
    schedule: (fn, ms) => setTimeout(fn, ms), cancel: id => clearTimeout(id)});
  el('refresh').addEventListener('click', () => poller.refresh(true));
  el('pause').addEventListener('click', () => { poller.setPaused(!poller.paused); text('pause', poller.paused ? 'Resume updates' : 'Pause updates'); el('pause').setAttribute('aria-pressed', String(poller.paused)); });
  el('interval').addEventListener('change', () => { poller.clear(); if (!poller.paused) poller.refresh(true); });
  document.addEventListener('visibilitychange', () => poller.visibilityChanged());
  function sectionSelection() {
    const hash = root.location.hash || '#overview';
    for (const link of document.querySelectorAll('.section-nav a')) {
      if (link.getAttribute('href') === hash) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }
  root.addEventListener('hashchange', sectionSelection); sectionSelection();
  root.addEventListener('resize', () => { if (snapshot) chart(snapshot.history); });
  el('export').addEventListener('click', () => { if (!snapshot) return; const blob = new Blob([JSON.stringify(snapshot, null, 2)], {type: 'application/json'}), link = document.createElement('a'), href = URL.createObjectURL(blob); link.href = href; link.download = `musicbox-status-${new Date().toISOString().replaceAll(':', '-')}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(href), 1000); });
  el('cookie-check').addEventListener('click', async () => { const button = el('cookie-check'); if (button.disabled) return; button.disabled = true; text('cookie-result', 'Testing authenticated audio access…'); const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 60000); try { const result = await get('/api/youtube/download-cookies/status?refresh=1', controller.signal); text('cookie-result', result.valid ? 'Audio test passed.' : 'Audio test failed. Existing cookies were kept.'); poller.refresh(true); } catch (_) { text('cookie-result', 'Audio test could not complete. Existing cookies were kept.'); } finally { clearTimeout(timeout); button.disabled = false; } });
  poller.refresh();
})(typeof window !== 'undefined' ? window : globalThis);
