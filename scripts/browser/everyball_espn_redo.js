// Everyball: download 20 ESPN matches again (the 12 the full run missed + the 8 played ODIs that came back empty).
// Paste into the Console on www.cricinfo.com and press Enter. A status box appears at the bottom right.
// It saves espn_full_commentary_redo.json.gz (same layout as the full download). About 1,000 requests in all,
// 80 doors at up to 5,000 a minute (ESPNcricinfo's limit for us): about a minute.
window.EVERYBALL_RUN = (async () => {
  const TARGETS = [ // [format, number, ESPN id] (ids checked against ESPN's own match numbers)
    ['Test', 1548, 63934], ['Test', 1549, 63935],
    ['ODI', 1720, 66275], ['ODI', 1721, 66276], ['ODI', 1722, 66277], ['ODI', 1723, 66278], ['ODI', 1724, 66279],
    ['ODI', 1725, 66280], ['ODI', 1726, 66281], ['ODI', 1727, 66312], ['ODI', 1728, 66282], ['ODI', 1729, 66313],
    ['ODI', 1364, 66169], ['ODI', 2400, 251481], ['ODI', 2505, 275754], ['ODI', 2511, 275755], ['ODI', 2516, 275756],
    ['ODI', 2722, 325568], ['ODI', 2727, 325554], ['ODI', 2784, 375461],
  ];
  const DOORS = 80, PER_MIN = 5000;     // requests at the same time, and the most per minute
  const TEST = window.EVERYBALL_TEST;   // ESPN id(s) = try just those, don't save
  const API = 'https://hs-consumer-api.cricinfo.com';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const list = TEST ? TARGETS.filter(t => [].concat(TEST).includes(t[2])) : TARGETS;
  const out = [], running = new Set(), S = {requests: 0, refused: 0, problems: 0, withCom: 0, empty: 0, items: 0, recent: [],
                       paused: false, started: Date.now(), saved: false};
  let inFlight = 0, nextAt = 0; const waiting = [];   // doors in use, next free moment, queue

  // ---------- status box ----------
  document.getElementById('everyball-box')?.remove();
  const box = document.createElement('div');
  box.id = 'everyball-box';
  box.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:440px;background:#1e2430;' +
    'color:#e8ecf3;font:13px/1.45 system-ui,Segoe UI,sans-serif;border-radius:14px;padding:16px 18px;' +
    'box-shadow:0 10px 30px rgba(0,0,0,.45)';
  box.innerHTML = `<div style="font-weight:700;font-size:15px">Everyball · ESPN redo</div>
    <div style="opacity:.7;margin-bottom:8px">${list.length} matches (12 missed + 8 empty)</div>
    <div style="height:10px;background:#2f3747;border-radius:6px;overflow:hidden;margin-bottom:10px">
      <div id="eb-bar" style="height:100%;width:0;background:#5ccf9a;transition:width .3s"></div></div>
    <div id="eb-text" style="white-space:pre-wrap"></div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button id="eb-save" style="flex:1;padding:8px;border:0;border-radius:8px;background:#5ccf9a;color:#10251b;font-weight:600;cursor:pointer">Save what's downloaded so far</button>
      <button id="eb-pause" style="padding:8px 14px;border:1px solid #5b6578;border-radius:8px;background:transparent;color:#e8ecf3;cursor:pointer">Pause</button>
    </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector('#' + id);
  function render(note) {
    const done = out.length + S.problems, mins = (Date.now() - S.started) / 60000;
    const left = done ? ((Date.now() - S.started) / done) * (list.length - done) / 60000 : null;
    $('eb-bar').style.width = (100 * done / list.length) + '%';
    $('eb-text').textContent =
      (S.recent.length ? S.recent.slice(-4).map(x => '▸ ' + x).join('\n') + '\n' : '') +
      [...running].slice(0, 3).map(x => '… ' + x).join('\n') + (running.size > 3 ? `\n… and ${running.size - 3} more` : '') + '\n\n' +
      `${done} / ${list.length} matches (${(100 * done / list.length).toFixed(1)}%) · ${DOORS} doors, ${inFlight} in use\n` +
      `with commentary ${S.withCom} · empty ${S.empty} · problems ${S.problems}\n` +
      `items ${S.items.toLocaleString()}\n` +
      `requests ${S.requests.toLocaleString()} · ${mins > 0.05 ? Math.round(S.requests / mins) : 0}/min (cap ${PER_MIN.toLocaleString()}) · refused ${S.refused}\n` +
      (note || (left != null && done < list.length ? `about ${Math.ceil(left)} min left` : (done >= list.length ? 'finished' : 'starting…')));
  }
  render();

  async function saveFile() {
    const gz = await new Response(new Blob([JSON.stringify(out)]).stream()
      .pipeThrough(new CompressionStream('gzip'))).blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(gz); a.download = 'espn_full_commentary_redo.json.gz';
    document.body.appendChild(a); a.click(); a.remove();
    console.log(`Everyball: saved ${out.length} matches to espn_full_commentary_redo.json.gz`);
  }
  $('eb-save').onclick = () => saveFile();
  $('eb-pause').onclick = () => { S.paused = !S.paused; $('eb-pause').textContent = S.paused ? 'Resume' : 'Pause'; render(S.paused ? 'paused' : ''); };

  // ---------- Cricinfo's own pass maker (the same one the full download used) ----------
  let req; self.webpackChunk_N_E.push([[Symbol('everyball-redo')], {}, r => { req = r; }]);
  let L = null;
  try { L = req(764).L; } catch (e) {}
  if (typeof L !== 'function') {
    for (const k of Object.keys(req.m)) {
      if (String(req.m[k]).includes('generateURLToken')) {
        try { L = Object.values(req(k)).find(f => typeof f === 'function'); } catch (e) {}
        if (L) break;
      }
    }
  }
  if (typeof L !== 'function') { render('pass maker not found: reload cricinfo.com and paste again'); return; }

  // doors (at most DOORS requests at once) + pace (at most PER_MIN a minute)
  const acquire = () => inFlight < DOORS ? (inFlight++, Promise.resolve()) : new Promise(r => waiting.push(r));
  const release = () => { const w = waiting.shift(); if (w) w(); else inFlight--; };
  async function slot() { const now = Date.now(), at = Math.max(now, nextAt); nextAt = at + 60000 / PER_MIN; if (at > now) await sleep(at - now); }
  async function get(url, opts) {
    for (let i = 0; i < 5; i++) {
      while (S.paused) await sleep(500);
      await acquire(); await slot();
      try { S.requests++; const r = await fetch(url, opts); if (r.ok) { release(); return r; } S.refused++; } catch (e) { S.refused++; }
      release(); render(); await sleep(2000 * (i + 1));
    }
    throw new Error('gave up on ' + url);
  }
  const api = async path => (await get(API + path, {headers: {'x-hsci-auth-token': L(path)}})).json(); // fresh pass each call

  async function matchPage(id) {
    const r = await get(`/ci/engine/match/${id}.html`);
    const m = (await r.text()).match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) throw new Error('no page data for ' + id);
    const d = JSON.parse(m[1]).props.appPageProps.data;
    return {url: r.url, match: d.match || {}, content: d.content || {}};
  }

  async function inningComments(series, id, inn, label) {
    const items = [], seen = new Set(); let from = null;
    for (let page = 0; page < 600; page++) {
      const path = `/v1/pages/match/comments?lang=en&seriesId=${series}&matchId=${id}&inningNumber=${inn}` +
                   `&commentType=ALL&sortDirection=DESC` + (from != null ? `&fromInningOver=${from}` : '');
      const j = await api(path);
      const cs = j.comments || []; let fresh = 0;
      for (const c of cs) {
        const k = c.id ?? c._uid ?? `${c.oversActual}|${c.ballNumber}|${c.title}`;
        if (!seen.has(k)) { seen.add(k); items.push(c); fresh++; }
      }
      S.items += fresh; render();
      const next = j.nextInningOver;
      if (!cs.length || !fresh || next == null || next === from) break;
      from = next; await sleep(60);
    }
    return items;
  }

  function collectPlayers(x, out, depth = 0) {
    if (!x || typeof x !== 'object' || depth > 14) return;
    if (Array.isArray(x)) { for (const v of x) collectPlayers(v, out, depth + 1); return; }
    if (x.id && x.longName && ('dateOfBirth' in x || 'battingStyles' in x)) out[x.id] = x;
    for (const v of Object.values(x)) collectPlayers(v, out, depth + 1);
  }

  async function one([fmt, num, id]) {
    const pg = await matchPage(id), mt = pg.match;
    const series = mt.series && mt.series.objectId;
    const sc = pg.content.innings || [];
    const teams = (mt.teams || []).map(t => t.team && (t.team.longName || t.team.name)).join(' v ');
    const date = (mt.startDate || '').slice(0, 10);
    const label = `${fmt} #${num} · ${teams} · ${date}`;
    const innNos = new Set(sc.map(i => i.inningNumber).filter(Boolean));
    for (let i = 1; i <= (fmt === 'Test' ? 4 : 2); i++) innNos.add(i);
    const innings = {}, nos = [...innNos].sort((a, b) => a - b);
    running.add(label); render();
    const got = await Promise.all(nos.map(inn => inningComments(series, id, inn, label)));   // all innings at once
    nos.forEach((inn, i) => { if (got[i].length) innings[inn] = got[i]; });
    running.delete(label);
    const players = {}; collectPlayers(pg.content, players);
    const n = Object.values(innings).reduce((a, x) => a + x.length, 0);
    return {format: fmt, number: num, espn_id: id, start_date: date, teams, page_url: pg.url, match: mt, players,
            scorecard_innings: sc,
            status: n ? 'ok' : (mt.hasCommentary ? 'commentary flag but nothing returned' : 'no commentary'),
            label, innings,
            feed_extras: {redo: '2026-11-12', number_check: +mt.internationalNumber === num ? 'ok' : `ESPN says #${+mt.internationalNumber}`}};
  }

  await Promise.all(list.map(async t => {   // all matches at once; the doors and the pace keep it polite
    try {
      const r = await one(t); out.push(r);
      const n = Object.values(r.innings).reduce((a, x) => a + x.length, 0);
      if (n) S.withCom++; else S.empty++;
      S.recent.push(`${r.label} — ${n ? n + ' items' : 'no commentary'}`);
      console.log(`Everyball ${out.length}/${list.length}: ${r.label} - ${r.status}, ${n} items`);
    } catch (e) {
      S.problems++; S.recent.push(`${t[0]} #${t[1]} — problem: ${e.message}`);
      console.error('Everyball problem with', t, e);
    }
    render();
  }));
  if (TEST) { render('test finished (not saved)'); return {out, S}; }
  await saveFile(); S.saved = true;
  render(`finished — saved espn_full_commentary_redo.json.gz (${out.length} matches)`);
})();
