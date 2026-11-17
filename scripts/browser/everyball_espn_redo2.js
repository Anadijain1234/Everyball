// Everyball redo 2: download again the 182 matches that came in "partial" (fewer balls than ESPN's scorecard).
// Paste into the Console on www.cricinfo.com and press Enter. A status box appears at the bottom right.
// It saves espn_full_commentary_redo2.json.gz (same layout as the full download).
// Stronger than the first run: it does not stop on a page of repeats, then asks again for every over still
// missing, one by one, and shows "got X of Y balls" for each match, so we learn if ESPN itself has the gap.
window.EVERYBALL_RUN = (async () => {
  const TARGETS = [ // [format, number, ESPN id]: the 182 matches added as 'partial' on 17 Nov 2026
    ['ODI',  729,  65133], ['ODI',  750,  65154], ['ODI',  1078,  65187], ['ODI',  1082,  65191], ['ODI',  1129,  66061], ['ODI',  1137,  66066],
    ['ODI',  1327,  65774], ['ODI',  1391,  65577], ['ODI',  1469,  65219], ['ODI',  1485,  66225], ['ODI',  1487,  66227], ['ODI',  1490,  66230],
    ['ODI',  1537,  65588], ['ODI',  1547,  65595], ['ODI',  1550,  65597], ['ODI',  1552,  65598], ['ODI',  1554,  65599], ['ODI',  1565,  64650],
    ['ODI',  1568,  64653], ['ODI',  1569,  64654], ['ODI',  1570,  64655], ['ODI',  1571,  64656], ['ODI',  1638,  66178], ['ODI',  1724,  66279],
    ['ODI',  1810,  64749], ['ODI',  2001,  66350], ['ODI',  2002,  66351], ['ODI',  2012,  66360], ['ODI',  2033,  66298], ['ODI',  2064,  66373],
    ['ODI',  2072,  64854], ['ODI',  2074,  64856], ['ODI',  2076,  64858], ['ODI',  2083,  64861], ['ODI',  2090,  64863], ['ODI',  2099,  64867],
    ['ODI',  2100,  64868], ['ODI',  2107,  64875], ['ODI',  2110,  64878], ['ODI',  2111,  64879], ['ODI',  2118,  64886], ['ODI',  2120,  64888],
    ['ODI',  2144,  65707], ['ODI',  2148,  65711], ['ODI',  2156,  64902], ['ODI',  2162,  66376], ['ODI',  2171,  66199], ['ODI',  2187,  66382],
    ['ODI',  2201,  64916], ['ODI',  2204,  65657], ['ODI',  2213,  65663], ['ODI',  2228,  64931], ['ODI',  2235,  64938], ['ODI',  2243,  209027],
    ['ODI',  2244,  209297], ['ODI',  2247,  209733], ['ODI',  2251,  211425], ['ODI',  2252,  211579], ['ODI',  2268,  215449], ['ODI',  2272,  216929],
    ['ODI',  2278,  217811], ['ODI',  2279,  217978], ['ODI',  2290,  223634], ['ODI',  2303,  226366], ['ODI',  2305,  226353], ['ODI',  2307,  226355],
    ['ODI',  2312,  226393], ['ODI',  2313,  226379], ['ODI',  2370,  239910], ['ODI',  2387,  240777], ['ODI',  2394,  249205], ['ODI',  2451,  259385],
    ['ODI',  2480,  267706], ['ODI',  2503,  267388], ['ODI',  2533,  247459], ['ODI',  2584,  293504], ['ODI',  2585,  293505], ['ODI',  2596,  296566],
    ['ODI',  2604,  295787], ['ODI',  2693,  335633], ['ODI',  2695,  335635], ['ODI',  2729,  325556], ['ODI',  2731,  325555], ['ODI',  2743,  314670],
    ['ODI',  2744,  349904], ['ODI',  2746,  349902], ['ODI',  2749,  349901], ['ODI',  2752,  349903], ['ODI',  2763,  361755], ['ODI',  2764,  361756],
    ['ODI',  2765,  361757], ['ODI',  2767,  325582], ['ODI',  2768,  325583], ['ODI',  2789,  366712], ['ODI',  2805,  385023], ['ODI',  2807,  385024],
    ['ODI',  2812,  385026], ['ODI',  2814,  385027], ['ODI',  2831,  390205], ['ODI',  2835,  390221], ['ODI',  2837,  390226], ['ODI',  2844,  390252],
    ['ODI',  2857,  408622], ['ODI',  2858,  408623], ['ODI',  2860,  408624], ['ODI',  2861,  410557], ['ODI',  2876,  408627], ['ODI',  2879,  350042],
    ['ODI',  2880,  415172], ['ODI',  2881,  415173], ['ODI',  2908,  424849], ['ODI',  2911,  426217], ['ODI',  2912,  426219], ['ODI',  2956,  441782],
    ['ODI',  2958,  441783], ['ODI',  2978,  448369], ['ODI',  2992,  442232], ['ODI',  2997,  426406], ['ODI',  3005,  450370], ['ODI',  3007,  450372],
    ['ODI',  3008,  450373], ['ODI',  3009,  450374], ['ODI',  3010,  450375], ['ODI',  3012,  450376], ['ODI',  3017,  450381], ['ODI',  3019,  450382],
    ['ODI',  3020,  450383], ['ODI',  3021,  450384], ['ODI',  3022,  450387], ['ODI',  3023,  450385], ['ODI',  3024,  450386], ['ODI',  3027,  450106],
    ['ODI',  3029,  450105], ['ODI',  3034,  464717], ['ODI',  3048,  474453], ['ODI',  3049,  474454], ['ODI',  3050,  474455], ['ODI',  3053,  470625],
    ['ODI',  3157,  507995], ['ODI',  3164,  504778], ['ODI',  3171,  506207], ['ODI',  3172,  506208], ['ODI',  3173,  518202], ['ODI',  3174,  518203],
    ['ODI',  3271,  543889], ['ODI',  3342,  592271], ['ODI',  3345,  592267], ['ODI',  3359,  602761], ['ODI',  3379,  631136], ['ODI',  3381,  631137],
    ['ODI',  3386,  631139], ['ODI',  3410,  566939], ['ODI',  3413,  660841], ['ODI',  3417,  661891], ['ODI',  3418,  661893], ['ODI',  3487,  736497],
    ['ODI',  3503,  756035], ['ODI',  3541,  791633], ['ODI',  3542,  791635], ['ODI',  3560,  803799], ['ODI',  3562,  803801], ['ODI',  3576,  812645],
    ['ODI',  3711,  911051], ['ODI',  3729,  952183], ['ODI',  3748,  913619], ['ODI',  3761,  997961], ['ODI',  3762,  997963], ['ODI',  3764,  997967],
    ['ODI',  3842,  1079250], ['ODI',  3934,  1126323], ['ODI',  3938,  1124090], ['ODI',  3975,  1134035], ['ODI',  3983,  1133001], ['ODI',  3988,  1133004],
    ['ODI',  3990,  1133009], ['ODI',  3997,  1133018], ['ODI',  4003,  1133027], ['ODI',  4032,  1150146], ['ODI',  4123,  1179274], ['ODI',  4131,  1169330],
    ['ODI',  4338,  1290882], ['ODI',  4350,  1295183],
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
  box.innerHTML = `<div style="font-weight:700;font-size:15px">Everyball · ESPN redo 2</div>
    <div style="opacity:.7;margin-bottom:8px">${list.length} partial matches, fetched again</div>
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
      `full now ${S.full || 0} · with commentary ${S.withCom} · empty ${S.empty} · problems ${S.problems}\n` +
      `items ${S.items.toLocaleString()}\n` +
      `requests ${S.requests.toLocaleString()} · ${mins > 0.05 ? Math.round(S.requests / mins) : 0}/min (cap ${PER_MIN.toLocaleString()}) · refused ${S.refused}\n` +
      (note || (left != null && done < list.length ? `about ${Math.ceil(left)} min left` : (done >= list.length ? 'finished' : 'starting…')));
  }
  render();

  async function saveFile() {
    const gz = await new Response(new Blob([JSON.stringify(out)]).stream()
      .pipeThrough(new CompressionStream('gzip'))).blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(gz); a.download = 'espn_full_commentary_redo2.json.gz';
    document.body.appendChild(a); a.click(); a.remove();
    console.log(`Everyball: saved ${out.length} matches to espn_full_commentary_redo2.json.gz`);
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

  const commentKey = c => c.id ?? c._uid ?? `${c.oversActual}|${c.ballNumber}|${c.title}`;
  async function pageFrom(series, id, inn, from) {
    const path = `/v1/pages/match/comments?lang=en&seriesId=${series}&matchId=${id}&inningNumber=${inn}` +
                 `&commentType=ALL&sortDirection=DESC` + (from != null ? `&fromInningOver=${from}` : '');
    return api(path);
  }
  async function inningComments(series, id, inn, label) {
    const items = [], seen = new Set();
    const add = cs => { let fresh = 0; for (const c of cs) { const k = commentKey(c); if (!seen.has(k)) { seen.add(k); items.push(c); fresh++; } }
                        S.items += fresh; return fresh; };
    // pass 1: walk back from the last over (don't stop on a page of repeats, only when ESPN says there is no more)
    let from = null, top = 0;
    for (let page = 0; page < 600; page++) {
      const j = await pageFrom(series, id, inn, from); const cs = j.comments || [];
      add(cs); for (const c of cs) top = Math.max(top, c.overNumber || 0); render();
      const next = j.nextInningOver;
      if (!cs.length || next == null || next === from) break;
      from = next; await sleep(60);
    }
    // pass 2: ask again for every over that is still missing
    const have = () => new Set(items.map(c => c.overNumber));
    let missing = [];
    for (let o = 1; o <= top; o++) if (!have().has(o)) missing.push(o);
    for (const o of missing) {
      if (have().has(o)) continue;
      for (const f of [o, o + 1]) {          // ESPN's "from" over: try the over itself and the one after
        const j = await pageFrom(series, id, inn, f); add(j.comments || []);
        if (have().has(o)) break;
      }
      render();
    }
    const still = []; for (let o = 1; o <= top; o++) if (!have().has(o)) still.push(o);
    return {items, missingBefore: missing.length, stillMissing: still};
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
    const check = {};
    nos.forEach((inn, i) => {
      const it = got[i].items; if (it.length) innings[inn] = it;
      const card = sc.find(x => x.inningNumber === inn) || {};
      const legal = it.filter(c => !c.wides && !c.noballs).length;
      check[inn] = {legal_got: legal, legal_scorecard: card.balls ?? null, overs_refetched: got[i].missingBefore,
                    overs_still_missing: got[i].stillMissing};
    });
    running.delete(label);
    const players = {}; collectPlayers(pg.content, players);
    const n = Object.values(innings).reduce((a, x) => a + x.length, 0);
    return {format: fmt, number: num, espn_id: id, start_date: date, teams, page_url: pg.url, match: mt, players,
            scorecard_innings: sc,
            status: n ? 'ok' : (mt.hasCommentary ? 'commentary flag but nothing returned' : 'no commentary'),
            label, innings,
            feed_extras: {redo2: '2026-11-17', check, number_check: +mt.internationalNumber === num ? 'ok' : `ESPN says #${+mt.internationalNumber}`}};
  }

  await Promise.all(list.map(async t => {   // all matches at once; the doors and the pace keep it polite
    try {
      const r = await one(t); out.push(r);
      const n = Object.values(r.innings).reduce((a, x) => a + x.length, 0);
      if (n) S.withCom++; else S.empty++;
      const ck = Object.values(r.feed_extras.check || {});
      const g = ck.reduce((a, x) => a + x.legal_got, 0), w = ck.reduce((a, x) => a + (x.legal_scorecard || 0), 0);
      if (w && g >= w) S.full = (S.full || 0) + 1;
      S.recent.push(`${r.label} — got ${g} of ${w} balls${w && g >= w ? ' ✓ full' : ''}`);
      console.log(`Everyball ${out.length}/${list.length}: ${r.label} - ${r.status}, ${n} items`);
    } catch (e) {
      S.problems++; S.recent.push(`${t[0]} #${t[1]} — problem: ${e.message}`);
      console.error('Everyball problem with', t, e);
    }
    render();
  }));
  if (TEST) { render('test finished (not saved)'); return {out, S}; }
  await saveFile(); S.saved = true;
  render(`finished — saved espn_full_commentary_redo2.json.gz (${out.length} matches)`);
})();
