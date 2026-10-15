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
  function inventoryRows(entries, query = '', pool = 'all', sort = 'newest') {
    const q = query.trim().toLowerCase();
    return array(entries).filter(row => (!q || `${row.video_id} ${row.format}`.toLowerCase().includes(q)) &&
      (pool === 'all' || (pool === 'pinned' ? row.pinned : pool === 'partial' ? row.state === 'partial' : row.pool === pool)))
      .sort((a, b) => sort === 'size' ? b.bytes - a.bytes : sort === 'idle' ? b.idle_seconds - a.idle_seconds : b.modified_at - a.modified_at);
  }

  function cacheCapacity(audio = {}, limits = {}) {
    const valid = value => Number.isFinite(value) && value >= 0;
    const used = valid(audio.ready_bytes) && audio.available !== false ? audio.ready_bytes : null;
    const budget = valid(limits.audio_bytes) ? limits.audio_bytes : null;
    const warmup = used == null || !valid(audio.warmup_bytes) ? null : Math.min(used, audio.warmup_bytes);
    const requested = used == null || warmup == null ? null : used - warmup;
    return {used, budget, warmup, requested, complete: audio.complete !== false,
      percent: used != null && budget > 0 ? used / budget * 100 : null,
      remaining: used != null && budget > 0 ? Math.max(0, budget - used) : null};
  }

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
  const exports = {bytes, duration, inventoryRows, cacheCapacity, Poller};
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
  function inventory() {
    const cache = (snapshot && snapshot.storage && snapshot.storage.audio) || {};
    const filtered = inventoryRows(cache.entries, el('search').value, el('pool').value, el('sort').value);
    table('inventory', filtered.map(row => [row.video_id, row.format, bytes(row.bytes), badge(row.state, row.state === 'partial' ? 'warning' : 'neutral'), row.pinned ? `${row.pool} · pinned` : row.pool, duration(row.idle_seconds), time(row.modified_at)]), 7);
    text('inventory-note', `${number(filtered.length)} matching rows · newest ${number(array(cache.entries).length)} of ${number((cache.ready_files || 0) + (cache.partial_files || 0))} audio files retained.${cache.complete === false ? ' Scan is partial: totals are lower bounds.' : ''}${cache.available === false ? ' Audio cache is not available yet.' : ''}`);
  }
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
  function cacheOverview(cache, limits) {
    const model = cacheCapacity(cache, limits), prefix = model.complete ? '' : 'At least ';
    text('cache-used', prefix + bytes(model.used));
    text('cache-budget', model.budget === 0 ? 'used · no size limit' : `of ${bytes(model.budget)} used`);
    text('cache-percent', model.percent == null ? '—' : `${model.complete ? '' : '≥ '}${number(model.percent, 2)}%`);
    text('cache-requested', bytes(model.requested)); text('cache-warmup', bytes(model.warmup));
    text('cache-remaining', model.complete ? bytes(model.remaining) : 'Not yet known');
    el('cache-requested-bar').style.width = percentWidth(model.budget > 0 && model.requested != null ? model.requested / model.budget * 100 : null);
    el('cache-warmup-bar').style.width = percentWidth(model.budget > 0 && model.warmup != null ? Math.min(model.warmup / model.budget * 100, Math.max(0, 100 - model.requested / model.budget * 100)) : null);
    meter('cache-meter', model.percent, `${prefix}${bytes(model.used)} used; budget ${model.budget === 0 ? 'unlimited' : bytes(model.budget)}`);
    text('cache-file-count', `${number(cache.ready_files)} cached files`);
    text('cache-pinned', `${bytes(cache.pinned_bytes)} current-track protected`);
    text('cache-capacity-note', model.used == null ? 'Cache inventory unavailable. No usage estimate is shown.' :
      `${model.complete ? '' : 'Partial scan: usage is a lower bound. '}${model.percent > 100 ? 'Over budget; protected files may delay cleanup. ' : ''}Kept until space is needed · oldest unused songs go first. ${bytes(cache.partial_bytes)} downloading, outside the completed-audio budget.`);
  }
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
  function cacheLayers(data) {
    const target = el('cache-layer-cards'); target.replaceChildren();
    const names = new Set([...array(data.caches).map(row => row.name), ...array(data.cache_sizes).map(row => row.name)]);
    for (const name of names) {
      const value = array(data.caches).find(row => row.name === name) || {}, size = array(data.cache_sizes).find(row => row.name === name) || {};
      const card = document.createElement('div'), top = document.createElement('div'), title = document.createElement('strong'), rate = document.createElement('span'), track = document.createElement('div'), fill = document.createElement('div'), bottom = document.createElement('div'), counts = document.createElement('span'), entries = document.createElement('span');
      top.className = 'layer-top'; title.textContent = name.replaceAll('_', ' '); rate.textContent = Number.isFinite(value.hit_rate) ? `${number(value.hit_rate, 1)}% hits` : 'No lookups';
      top.append(title, rate); track.className = 'meter'; fill.style.width = percentWidth(value.hit_rate); track.append(fill);
      bottom.className = 'layer-bottom'; counts.textContent = `${number(value.hits)} hits · ${number(value.misses)} misses`;
      entries.textContent = Number.isFinite(size.entries) ? `${number(size.entries)}${size.capacity ? ` / ${number(size.capacity)}` : ''} entries` : 'Disk lookup';
      bottom.append(counts, entries); card.append(top, track, bottom); target.append(card);
    }
  }
  function render(data) {
    snapshot = data;
    const request = data.requests || {}, storage = data.storage || {}, cache = storage.audio || {}, system = storage.system || {}, fetching = data.fetching || {}, cookies = data.cookies || {}, download = cookies.downloads || {}, disk = system.disk || {}, process = system.process || {}, memory = system.memory || {};
    el('notice').hidden = true; el('export').disabled = false;
    const status = ['healthy', 'warning', 'critical'].includes(data.health) ? data.health : 'warning';
    el('health').className = `badge ${status === 'healthy' ? '' : status}`;
    text('health', {healthy: 'Operational', warning: 'Needs attention', critical: 'Critical'}[status]);
    text('updated', `Updated ${time(data.generated_at)}`);
    text('uptime', `Up ${duration(data.uptime_seconds)}`);
    text('rpm', number((array(data.history).at(-1) || {}).requests || 0));
    text('request-total', `${number(request.total || 0)} total · ${number(request.active || 0)} active handlers`);
    const hits = array(data.caches).find(row => row.name === 'audio_lookups');
    text('hit-rate', hits ? `${number(hits.hit_rate, 1)}%` : '—');
    el('hit-ring').setAttribute('stroke-dasharray', `${hits ? Math.max(0, Math.min(100, hits.hit_rate)) : 0} 100`);
    text('hit-detail', hits ? `${number(hits.hits)} hits · ${number(hits.misses)} misses · internal lookups` : 'No audio lookups recorded');
    text('latency', Number.isFinite(request.p95_ms) ? `${number(request.p95_ms)} ms` : '—');
    text('free-disk', bytes(disk.free_bytes)); text('disk-detail', `${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}`);
    el('disk-meter').style.width = percentWidth(disk.used_percent);
    text('disk-percent', Number.isFinite(disk.used_percent) ? `${number(disk.used_percent, 1)}%` : 'Unknown');
    meter('disk-usage', disk.used_percent, `${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}`);
    const memoryPercent = process.memory_limit_bytes > 0 && Number.isFinite(process.memory_current_bytes) ? process.memory_current_bytes / process.memory_limit_bytes * 100 : null;
    el('memory-meter').style.width = percentWidth(memoryPercent); text('memory-value', `${bytes(process.rss_bytes)} RSS`);
    meter('memory-usage', memoryPercent, memoryPercent == null ? 'Container memory limit unavailable' : `${number(memoryPercent, 1)}% of container memory limit`);
    connectionTiles(cookies);
    const slots = el('fetch-slots'); slots.replaceChildren();
    const active = array(data.active_fetches).length;
    const budget = Number.isFinite(fetching.download_slots) ? Math.max(0, Math.min(32, fetching.download_slots)) : 0;
    for (let i = 0; i < budget; i++) { const slot = document.createElement('span'); slot.className = i < active ? 'busy' : ''; slot.setAttribute('aria-hidden', 'true'); slots.append(slot); }
    slots.setAttribute('aria-label', `${number(active)} active downloader attempts; ${number(fetching.download_slots)} configured slots`);
    text('request-health', `${number((request['4xx'] || 0) + (request['5xx'] || 0))} HTTP errors · p50 ${number(request.p50_ms)} ms`);
    rows('system', [['Host load · 1 / 5 / 15 min', array(system.load_average).map(v => number(v, 2)).join(' / ') || 'Unavailable'], ['Host logical CPUs', number(system.cpu_count)], ['Process RSS / threads', `${bytes(process.rss_bytes)} / ${number(system.threads)}`], ['Host available / total RAM', `${bytes(memory.MemAvailable)} / ${bytes(memory.MemTotal)}`], ['Container memory / limit', `${bytes(process.memory_current_bytes)} / ${bytes(process.memory_limit_bytes)}`], ['Container CPU quota', process.cpu_quota == null ? 'Unlimited / unavailable' : `${number(process.cpu_quota, 2)} CPUs`], ['Database size', bytes((system.database || {}).bytes)], ['Storage snapshot age', duration(Math.max(0, data.generated_at - (storage.captured_at || data.generated_at)))]]);
    rows('cookies', [['Download export', download.present ? `Present · ${bytes(download.bytes)}` : 'Not configured'], ['Export modified', time(download.modified_at)], ['Cookie format', download.format_valid == null ? 'Not inspected' : download.format_valid ? 'Netscape export' : 'Invalid export'], ['Cookies · active YouTube / expired', `${number(download.youtube_active)} / ${number(download.expired_count)}`], ['Session cookies / next expiry', `${number(download.session_count)} / ${Number.isFinite(download.next_expiry) ? new Date(download.next_expiry * 1000).toLocaleString() : 'No known expiry'}`], ['Last audio validation', download.validation == null ? 'Not tested for this file' : download.validation ? 'Passed' : 'Failed'], ['Validation age', duration(download.validation_age_seconds)], ['Cookie probe', download.probe_busy ? 'Running' : 'Idle'], ['YouTube account headers', (cookies.youtube_headers || {}).present ? 'Configured · not probed' : 'Not configured'], ['Amazon session', cookies.amazon_session_in_memory ? 'Present in memory · not probed' : 'Not loaded']]);
    el('cooldown').className = `badge ${fetching.cooldown_seconds > 0 ? 'warning' : 'neutral'}`;
    text('cooldown', fetching.cooldown_seconds > 0 ? `Cooldown ${duration(fetching.cooldown_seconds)}` : 'No cooldown');
    rows('fetching', [['Queued + running calls', number(fetching.queued_and_running)], ['Download / prefetch / stream budgets', `${number(fetching.download_slots)} / ${number(fetching.prefetch_slots)} / ${number(fetching.stream_slots)}`], ['Active streams', number(fetching.active_streams)], ['yt-dlp attempts · completed / failed', `${number((data.fetches || {}).succeeded || 0)} / ${number((data.fetches || {}).failed || 0)}`], ['Known dead / temporarily flaky', `${number(fetching.known_dead)} / ${number(fetching.temporarily_flaky)}`], ['Playback confirmed / queue', `${(data.playback || {}).confirmed ? 'Yes' : 'No'} / ${number((data.playback || {}).queue_length)} tracks`]]);
    activities('active-fetches', [...array(data.active_fetches).map(row => ({title: `${row.video_id} · ${row.client}`, detail: `Fetching for ${duration(data.generated_at - row.started_at)}`})), ...array(fetching.streams).map(row => ({title: `${row.video_id} · live stream`, detail: `Active ${duration(row.age_seconds)}`}))]);
    cacheLayers(data);
    const limits = data.limits || {};
    cacheOverview(cache, limits);
    rows('retention', [['Ready / partial audio', `${bytes(cache.ready_bytes)} / ${bytes(cache.partial_bytes)}`], ['Warmup / pinned audio', `${bytes(cache.warmup_bytes)} / ${bytes(cache.pinned_bytes)}`], ['Total cache budget', limits.audio_bytes === 0 ? 'Unlimited' : bytes(limits.audio_bytes)], ['Prefetch budget', limits.retention_policy === 'capacity_lru' ? 'Shared with total music cache' : bytes(limits.warmup_bytes)], ['Minimum free reserve', bytes(limits.reserve_bytes)], ['Completed audio retention', limits.retention_policy === 'capacity_lru' ? 'Until space is needed · LRU' : `${duration(limits.audio_ttl_seconds)} idle`], ['Abandoned partial cleanup', duration(limits.partial_ttl_seconds)], ['Sweep interval', duration(limits.sweep_seconds)]]);
    text('inventory-summary', `${number(cache.ready_files)} ready · ${number(cache.partial_files)} partial · ${bytes(cache.bytes)} total`);
    table('routes', array(data.routes).map(row => [row.route, number(row.requests), number(row.errors), `${number(row.average_ms)} ms`, `${number(row.max_ms)} ms`]), 5);
    activities('events', array(data.events).map(row => ({title: row.kind === 'fetch' ? `${row.video_id} · ${row.client} · ${row.status}` : `${row.label} · HTTP ${row.status}`, detail: `${time(row.at)} · ${number(row.duration_ms)} ms`, error: row.status === 'failed' || row.status >= 400})));
    const alerts = el('alerts'); alerts.replaceChildren();
    for (const row of array(data.alerts)) { const div = document.createElement('div'), title = document.createElement('strong'), detail = document.createElement('small'); div.className = row.level === 'critical' ? 'critical' : ''; title.textContent = row.title; detail.textContent = row.detail; div.append(title, detail); alerts.append(div); }
    chart(data.history); inventory();
  }
  const url = new URL(root.location.href), apiKey = url.searchParams.get('key');
  if (apiKey) { url.searchParams.delete('key'); root.history.replaceState(null, '', url.pathname + url.search + url.hash); }
  const headers = apiKey ? {'X-Api-Key': apiKey} : {};
  async function get(path, signal) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', headers, signal});
    if (!response.ok) { const error = new Error(`Status request failed (HTTP ${response.status})`); error.status = response.status; throw error; }
    if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Invalid status response');
    return response.json();
  }
  const poller = new Poller({load: async signal => { const data = await get('/api/status', signal); if (!Number.isFinite(data.generated_at) || !data.requests) throw new Error('Incomplete status response'); return data; },
    onData: render, onError: error => { el('notice').hidden = false; text('notice', error.status === 401 || error.status === 403 ? 'Your owner session expired. Sign in again, then reload this page.' : `Status is temporarily unavailable. ${snapshot ? `Keeping the snapshot from ${time(snapshot.generated_at)}.` : 'Retrying automatically.'}`); text('health', 'Connection interrupted'); el('health').className = 'badge warning'; },
    visible: () => !document.hidden, interval: () => Number(el('interval').value) * 1000,
    schedule: (fn, ms) => setTimeout(fn, ms), cancel: id => clearTimeout(id)});
  el('refresh').addEventListener('click', () => poller.refresh(true));
  el('pause').addEventListener('click', () => { poller.setPaused(!poller.paused); text('pause', poller.paused ? 'Resume updates' : 'Pause updates'); el('pause').setAttribute('aria-pressed', String(poller.paused)); });
  el('interval').addEventListener('change', () => { poller.clear(); if (!poller.paused) poller.refresh(true); });
  document.addEventListener('visibilitychange', () => poller.visibilityChanged());
  for (const id of ['search', 'pool', 'sort']) el(id).addEventListener(id === 'search' ? 'input' : 'change', inventory);
  root.addEventListener('resize', () => { if (snapshot) chart(snapshot.history); });
  el('export').addEventListener('click', () => { if (!snapshot) return; const blob = new Blob([JSON.stringify(snapshot, null, 2)], {type: 'application/json'}), link = document.createElement('a'), href = URL.createObjectURL(blob); link.href = href; link.download = `musicbox-status-${new Date().toISOString().replaceAll(':', '-')}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(href), 1000); });
  el('cookie-check').addEventListener('click', async () => { const button = el('cookie-check'); if (button.disabled) return; button.disabled = true; text('cookie-result', 'Testing authenticated audio access…'); const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 60000); try { const result = await get('/api/youtube/download-cookies/status?refresh=1', controller.signal); text('cookie-result', result.valid ? 'Audio test passed.' : 'Audio test failed. Existing cookies were kept.'); poller.refresh(true); } catch (_) { text('cookie-result', 'Audio test could not complete. Existing cookies were kept.'); } finally { clearTimeout(timeout); button.disabled = false; } });
  poller.refresh();
})(typeof window !== 'undefined' ? window : globalThis);
