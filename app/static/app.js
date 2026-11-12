/* Everyball front end: plain JavaScript, no build step. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const n = (v, d = 0) => (v === null || v === undefined || v === "" ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const year = (d) => (d || "").slice(0, 4);
  const niceDate = (d) => {
    if (!d) return "";
    const t = new Date(d + "T00:00:00");
    return isNaN(t) ? d : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  };
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
  };
  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    let data = {};
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(data.error || `Something went wrong (${r.status})`);
    return data;
  }
  const spin = (t = "Loading…") => `<div class="empty"><span class="spin"></span>${esc(t)}</div>`;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  /* theme + menu */
  $("#theme")?.addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    store.set("eb-theme", root.dataset.theme);
    document.dispatchEvent(new Event("themechange"));
  });
  $("#menubtn")?.addEventListener("click", () => document.body.classList.toggle("menu-on"));

  /* autocomplete */
  function autocomplete(input, onPick) {
    const box = input.closest(".ac");
    const list = document.createElement("div");
    list.className = "ac-list";
    box.appendChild(list);
    let items = [], active = -1, timer = null, last = "";
    const close = () => { list.classList.remove("open"); active = -1; };
    const draw = () => {
      if (!items.length) { list.innerHTML = `<div class="ac-empty">No player found</div>`; return; }
      list.innerHTML = items.map((p, i) => `<div class="ac-item${i === active ? " on" : ""}" data-i="${i}">
        <span><b>${esc(p.name)}</b> <span class="muted small">${esc(p.team || "")}</span></span>
        <span class="meta">${p.first_year ? `(${esc(p.first_year)}–${esc(p.last_year)})` : ""} ${esc(p.formats || "")}</span></div>`).join("");
    };
    const pick = (i) => {
      const p = items[i];
      if (!p) return;
      input.value = p.name;
      input.dataset.pid = p.player_id;
      close();
      onPick(p);
    };
    input.addEventListener("input", () => {
      delete input.dataset.pid;
      input.dispatchEvent(new Event("cleared"));
      const q = input.value.trim();
      clearTimeout(timer);
      if (q.length < 2) { close(); return; }
      timer = setTimeout(async () => {
        last = q;
        try {
          const res = await getJSON(`/api/search?q=${encodeURIComponent(q)}`);
          if (last !== q) return;
          items = res; active = res.length ? 0 : -1; draw(); list.classList.add("open");
        } catch (e) { close(); }
      }, 160);
    });
    input.addEventListener("keydown", (e) => {
      if (!list.classList.contains("open")) return;
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); draw(); e.preventDefault(); }
      else if (e.key === "Enter") { if (active >= 0) { pick(active); e.preventDefault(); } }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", (e) => {
      const it = e.target.closest(".ac-item");
      if (it) { e.preventDefault(); pick(+it.dataset.i); }
    });
    input.addEventListener("blur", () => setTimeout(close, 120));
  }

  const top = $("#topsearch");
  if (top) autocomplete(top, (p) => { location.href = `/player/${p.player_id}`; });

  function seg(el, onChange) {
    if (!el) return () => "";
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      $$("button", el).forEach((x) => x.classList.toggle("on", x === b));
      onChange?.(b.dataset.f);
    });
    return (set) => {
      if (set !== undefined) $$("button", el).forEach((x) => x.classList.toggle("on", x.dataset.f === set));
      return $("button.on", el)?.dataset.f || "";
    };
  }

  async function firstPlayer(name) {
    const res = await getJSON(`/api/search?q=${encodeURIComponent(name)}`);
    return res[0];
  }

  /* duel finder (home + duel page) */
  function duelFinder(onGo) {
    const bat = $("#d-bat"), bowl = $("#d-bowl"), go = $("#d-go");
    if (!bat) return null;
    const fmt = seg($("#d-fmt"), () => { if (bat.dataset.pid && bowl.dataset.pid) go.click(); });
    const check = () => { go.disabled = !(bat.dataset.pid && bowl.dataset.pid); };
    autocomplete(bat, () => { check(); if (!bowl.dataset.pid) bowl.focus(); });
    autocomplete(bowl, check);
    bat.addEventListener("cleared", check);
    bowl.addEventListener("cleared", check);
    go.addEventListener("click", () => onGo(bat.dataset.pid, bowl.dataset.pid, fmt()));
    $$("[data-duel]").forEach((c) => c.addEventListener("click", async () => {
      const [a, b] = c.dataset.duel.split("|");
      const [pa, pb] = await Promise.all([firstPlayer(a), firstPlayer(b)]);
      if (pa && pb) onGo(pa.player_id, pb.player_id, fmt());
    }));
    return { bat, bowl, fmt, check };
  }
  const toDuel = (b, w, f) => { location.href = `/duel?batter=${b}&bowler=${w}${f ? `&format=${f}` : ""}`; };

  /* ---------------------------------------------------------- home */
  const page = document.body.dataset.page;
  if (page === "home" || page === "ask") {
    duelFinder(toDuel);
    if (page === "ask") $("#q")?.focus();
    loadCoverage();
  }
  setupAsk();

  function setupAsk() {
    const q = $("#q"), go = $("#ask-go"), out = $("#answer");
    if (!q) return;
    $$("[data-q]").forEach((c) => c.addEventListener("click", () => { q.value = c.dataset.q; go.click(); }));
    q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go.click(); } });
    go.addEventListener("click", async () => {
      const question = q.value.trim();
      if (question.length < 3) { q.focus(); return; }
      go.disabled = true;
      out.innerHTML = spin("Writing the query and counting…");
      try {
        const r = await getJSON("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
        out.innerHTML = renderAnswer(r);
      } catch (e) {
        out.innerHTML = `<p class="err">${esc(e.message)}</p>`;
      } finally { go.disabled = false; }
    });
  }

  function table(cols, rows, max = 25) {
    if (!rows.length) return `<p class="muted small">No rows.</p>`;
    const isNum = cols.map((_, i) => rows.every((r) => r[i] === null || typeof r[i] === "number"));
    return `<div class="scroll"><table class="tbl"><thead><tr>${cols.map((c, i) => `<th class="${isNum[i] ? "n" : ""}">${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead>
      <tbody>${rows.slice(0, max).map((r) => `<tr>${r.map((v, i) => `<td class="${isNum[i] ? "n" : ""}">${isNum[i] && v !== null ? n(v, Number.isInteger(v) ? 0 : 2) : esc(v ?? "–")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      ${rows.length > max ? `<p class="muted small">Showing ${max} of ${rows.length} rows.</p>` : ""}`;
  }

  function renderAnswer(r) {
    return `<div class="line">${esc(r.answer)}</div>
      ${r.answer_hi ? `<div class="hi">${esc(r.answer_hi)}</div>` : ""}
      <div style="margin-top:12px">${table(r.columns, r.rows, 10)}</div>
      <details class="how"><summary>How it was counted</summary><div class="body">
        <p class="small" style="margin:0">${esc(r.how)}</p>
        ${r.question_en ? `<p class="small muted">Question in English: ${esc(r.question_en)}</p>` : ""}
        <pre class="sql">${esc(r.sql)}</pre>
        <p class="small muted" style="margin:.6em 0 0">The AI only wrote this query. The numbers come straight from the database.</p>
      </div></details>`;
  }

  async function loadCoverage() {
    const el = $("#cov");
    if (!el) return;
    try {
      const c = await getJSON("/api/coverage");
      el.innerHTML = `<div class="grid2">${c.formats.map((f) => {
        const pct = f.matches ? (100 * f.with_balls / f.matches) : 0;
        const decs = c.decades.filter((d) => d.format === f.format);
        const max = Math.max(1, ...decs.map((d) => d.matches));
        return `<div>
          <h3>${f.format === "Test" ? "Tests" : "ODIs"} <span class="muted small">${year(f.first)}–${year(f.last)}</span></h3>
          <div class="tiles" style="margin:10px 0">
            <div class="tile"><div class="k">Matches</div><div class="v">${n(f.matches)}</div>
              ${f.espn_count ? `<div class="s">ESPNcricinfo lists ${n(f.espn_count)}</div>` : ""}</div>
            <div class="tile"><div class="k">Ball by ball</div><div class="v">${n(f.with_balls)}</div><div class="s">${n(pct, 1)}% of matches</div></div>
            <div class="tile"><div class="k">Deliveries</div><div class="v">${n(f.balls)}</div></div>
          </div>
          <div class="meter" title="${n(pct, 1)}% ball by ball"><i style="width:${pct}%"></i></div>
          <div class="decades">${decs.map((d) => `<div class="decade" title="${d.decade}: ${d.with_balls} of ${d.matches} ball by ball">
            <div class="bar" style="height:${16 + 48 * d.matches / max}px"><i style="height:${d.matches ? 100 * d.with_balls / d.matches : 0}%"></i></div>
            ${esc(d.decade)}<br><span class="num">${n(d.with_balls)}/${n(d.matches)}</span></div>`).join("")}</div>
        </div>`;
      }).join("")}</div>
      <p class="small muted" style="margin-top:14px">Green = matches with ball-by-ball. ${n(c.players)} player records.</p>`;
    } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  }

  /* ---------------------------------------------------------- duel page */
  if (page === "duel") {
    const out = $("#duel");
    const f = duelFinder((b, w, fm) => {
      history.replaceState(null, "", `/duel?batter=${b}&bowler=${w}${fm ? `&format=${fm}` : ""}`);
      loadDuel(b, w, fm);
    });
    const p = new URLSearchParams(location.search);
    f.fmt(p.get("format") || "");
    (async () => {
      let b = p.get("batter"), w = p.get("bowler");
      if (!b && p.get("bn")) b = (await firstPlayer(p.get("bn")))?.player_id;
      if (!w && p.get("wn")) w = (await firstPlayer(p.get("wn")))?.player_id;
      if (b && w) loadDuel(b, w, p.get("format") || "");
      else out.innerHTML = `<div class="empty">Pick a batter and a bowler to see every ball between them.</div>`;
    })();

    async function loadDuel(b, w, fm) {
      out.innerHTML = spin("Finding every ball…");
      try {
        const d = await getJSON(`/api/duel?batter=${b}&bowler=${w}${fm ? `&format=${fm}` : ""}`);
        f.bat.value = d.batter; f.bat.dataset.pid = d.batter_id;
        f.bowl.value = d.bowler; f.bowl.dataset.pid = d.bowler_id;
        f.check();
        document.title = `${d.batter} v ${d.bowler} · Everyball`;
        out.innerHTML = renderDuel(d);
      } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
    }
  }

  const OUT_COLORS = () => ({ "0": css("--dot"), "1": `color-mix(in srgb, ${css("--accent")} 45%, ${css("--dot")})`, "2": css("--accent"),
    "3": css("--accent"), "4": css("--four"), "5+": css("--accent"), "6": css("--six"), "W": css("--ball"), "extras": css("--gold") });
  const ballClass = (c) => (c === "W" ? "W" : /^\d$/.test(c) ? (c === "0" ? "" : `r${c >= 6 ? 6 : c}`) : "x");
  const ballTitle = (c) => (c === "W" ? "Out" : c === "wd" ? "Wide" : c.startsWith("nb") ? `No-ball${c.slice(2) !== "0" ? `, ${c.slice(2)} off the bat` : ""}`
    : c.startsWith("lb") ? `Leg-bye${c.slice(2) !== "1" ? "s" : ""}` : c.startsWith("b") ? "Bye" : c === "0" ? "Dot ball" : `${c} run${c === "1" ? "" : "s"}`);

  function renderDuel(d) {
    const who = `<h2 style="margin:0">${esc(d.batter)} <span class="muted" style="font-style:italic;font-weight:500">v</span> ${esc(d.bowler)}</h2>
      <div class="facts"><a class="tag" href="/player/${d.batter_id}">${esc(d.batter)} · ${esc(d.batter_team || "")}</a>
      <a class="tag" href="/player/${d.bowler_id}">${esc(d.bowler)} · ${esc(d.bowler_team || "")}</a>
      <span class="tag">${d.format ? (d.format === "Test" ? "Tests" : "ODIs") : "Tests and ODIs"}</span></div>`;
    const scorecard = d.scorecard_dismissals || [];
    if (!d.balls) {
      return `<section class="card">${who}
        <div class="notice section" style="margin-top:16px">No ball-by-ball record of this duel${d.matches_both_played ? "" : ", and no match where he bowled while the other batted"}.
        ${d.matches_both_played ? `They met in <b>${n(d.matches_both_played)}</b> match${d.matches_both_played === 1 ? "" : "es"} (from the scorecards), and ${esc(d.bowler)} got him out <b>${n(d.dismissed_in_scorecards)}</b> time${d.dismissed_in_scorecards === 1 ? "" : "s"}.` : ""}</div>
        ${scorecard.length ? `<h3 class="section">Dismissals in the scorecards</h3>${dismissalTable(scorecard)}` : ""}</section>`;
    }
    const met = Math.max(d.matches_both_played || 0, d.matches_with_balls);
    const cov = met ? 100 * d.matches_with_balls / met : 100;
    const oc = d.outcomes, total = Object.values(oc).reduce((a, b) => a + b, 0) || 1, cols = OUT_COLORS();
    const labels = { "0": "dots", "1": "1s", "2": "2s", "3": "3s", "4": "4s", "5+": "5s", "6": "6s", W: "out", extras: "extras" };
    const srcNames = { davis: "Charles Davis", cricsheet: "Cricsheet", espn: "ESPNcricinfo" };
    return `<section class="card">${who}
      <div class="tiles section" style="margin-top:18px">
        <div class="tile"><div class="k">Balls</div><div class="v">${n(d.balls)}</div></div>
        <div class="tile"><div class="k">Runs</div><div class="v">${n(d.runs)}</div></div>
        <div class="tile hot"><div class="k">Dismissals</div><div class="v">${n(d.outs)}</div></div>
        <div class="tile"><div class="k">Average</div><div class="v">${d.average === null ? "∞" : n(d.average, 1)}</div><div class="s">runs per dismissal</div></div>
        <div class="tile"><div class="k">Strike rate</div><div class="v">${n(d.strike_rate, 1)}</div></div>
        <div class="tile"><div class="k">Dot balls</div><div class="v">${n(d.dot_pct, 1)}%</div><div class="s">${n(d.dots)} dots</div></div>
        <div class="tile"><div class="k">4s / 6s</div><div class="v">${n(d.fours)} / ${n(d.sixes)}</div></div>
        <div class="tile"><div class="k">Longest dot run</div><div class="v">${n(d.longest_dot_streak)}</div><div class="s">balls in a row</div></div>
      </div>
      <div class="section" style="margin-top:20px">
        <div class="row" style="justify-content:space-between"><b class="small">Ball-by-ball for ${n(d.matches_with_balls)} of ${n(met)} matches they met in</b>
          <span class="small muted">${n(cov, 0)}%</span></div>
        <div class="meter" style="margin-top:6px"><i style="width:${cov}%"></i></div>
      </div>
      <div class="section" style="margin-top:20px">
        <div class="dist">${Object.entries(oc).filter(([, v]) => v).map(([k, v]) => `<i title="${labels[k]}: ${v}" style="width:${100 * v / total}%;background:${cols[k]}"></i>`).join("")}</div>
        <div class="legend">${Object.entries(oc).filter(([, v]) => v).map(([k, v]) => `<span><i class="b ${k === "W" ? "W" : k === "extras" ? "x" : k === "0" ? "" : `r${k === "5+" ? 2 : k}`}"></i>${labels[k]} ${n(v)}</span>`).join("")}</div>
      </div>
    </section>
    <section class="card section">
      <h3>Every ball</h3>
      <div class="legend"><span><i class="b"></i>dot</span><span><i class="b r1"></i>1</span><span><i class="b r2"></i>2–3</span>
        <span><i class="b r4"></i>4</span><span><i class="b r6"></i>6</span><span><i class="b x"></i>extra</span><span><i class="b W"></i>out</span></div>
      <div class="strip">${d.matches.map((m) => `<div class="strip-row">
        <div class="when"><b>${esc(niceDate(m.date))}</b><br>${esc(m.teams)}<br>${esc(m.ground || "")}<br><span class="num">${m.runs} off ${m.balls}${m.out ? " · out" : ""}</span></div>
        <div class="balls">${m.codes.map((c) => `<i class="b ${ballClass(c)}" title="${ballTitle(c)}"></i>`).join("")}</div></div>`).join("")}</div>
    </section>
    ${scorecard.length ? `<section class="card section"><h3>Every time ${esc(d.bowler)} got him out (scorecards)</h3>${dismissalTable(scorecard)}</section>` : ""}
    <section class="section"><details class="how" open><summary>How it was counted</summary><div class="body small">
      <p>Every delivery ${esc(d.bowler)} bowled to ${esc(d.batter)} in matches with a ball-by-ball record:
      ${Object.entries(d.ball_sources).map(([s, v]) => `${n(v)} from ${srcNames[s] || s}`).join(", ")}.
      Wides don't count as balls faced. Run outs aren't credited to the bowler.</p>
      ${d.matches_both_played > d.matches_with_balls ? `<p>${n(d.matches_both_played - d.matches_with_balls)} of the matches they met in have no ball-by-ball record, so the numbers above leave them out. The scorecard list above includes them.</p>` : ""}
      ${d.sure_pct !== null && d.ball_sources.davis ? `<p>Charles Davis's records were rebuilt over by over; for ${n(d.sure_pct, 1)}% of these balls the batter on strike is proven by the over-by-over totals.</p>` : ""}
      ${d.espn_player_v_player ? `<p>ESPNcricinfo's own batter-against-bowler tables give ${n(d.espn_player_v_player.runs)} runs off ${n(d.espn_player_v_player.balls)} balls in ${n(d.espn_player_v_player.matches)} match(es), a separate check.</p>` : ""}
    </div></details></section>`;
  }

  function dismissalTable(rows) {
    return `<div class="scroll"><table class="tbl"><thead><tr><th>Date</th><th>Ground</th><th>How</th><th class="n">Runs</th><th>Ball by ball</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><td>${esc(niceDate(r.date))}</td><td>${esc(r.ground || "")}</td><td>${esc(r.kind || "")}</td>
      <td class="n">${n(r.runs)}</td><td>${r.has_balls ? "yes" : "<span class='muted'>no</span>"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  /* ---------------------------------------------------------- player page */
  if (page === "player") {
    const pid = $("#phead").dataset.pid, out = $("#player");
    let chart = null, last = null;
    const fmt = seg($("#p-fmt"), (f) => load(f));
    const load = async (f) => {
      out.innerHTML = spin();
      try { last = await getJSON(`/api/player/${pid}${f ? `?format=${f}` : ""}`); draw(last); } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
    };
    document.addEventListener("themechange", () => last && draw(last));
    load(fmt());

    function draw(p) {
      const fs = p.format ? p.formats.filter((x) => x.format === p.format) : p.formats;
      const pf = p.format ? `&format=${p.format}` : "";
      const rival = (rows, asBatter, label) => rows.length ? `<div class="card"><h3>${label}</h3><div class="scroll"><table class="tbl">
        <thead><tr><th>Player</th><th class="n">Balls</th><th class="n">Runs</th><th class="n">Outs</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><a href="/duel?batter=${asBatter ? pid : r.player_id}&bowler=${asBatter ? r.player_id : pid}${pf}">${esc(r.name)}</a> <span class="muted small">${esc(r.team)}</span></td>
          <td class="n">${n(r.balls)}</td><td class="n">${n(r.runs)}</td><td class="n">${n(r.outs)}</td></tr>`).join("")}</tbody></table></div>
        <p class="small muted" style="margin:.6em 0 0">Matches with ball-by-ball only. Click a name for every ball.</p></div>` : "";
      const counted = (rows, label, asBatter) => rows.length ? `<div class="card"><h3>${label}</h3><table class="tbl"><tbody>
        ${rows.map((r) => `<tr><td><a href="/duel?batter=${asBatter ? pid : r.player_id}&bowler=${asBatter ? r.player_id : pid}${pf}">${esc(r.name)}</a> <span class="muted small">${esc(r.team)}</span></td><td class="n">${n(r.n)}</td></tr>`).join("")}
        </tbody></table></div>` : "";
      const kinds = p.dismissals, kt = kinds.reduce((a, k) => a + k.n, 0) || 1;
      out.innerHTML = `${fs.map((f) => `<section class="card section" style="margin-top:0;margin-bottom:20px">
          <h2>${f.format === "Test" ? "Tests" : "ODIs"}</h2>
          ${f.batting.innings ? `<div class="small muted" style="margin:6px 0">Batting</div><div class="tiles">
            <div class="tile"><div class="k">Matches</div><div class="v">${n(f.batting.matches)}</div></div>
            <div class="tile"><div class="k">Runs</div><div class="v">${n(f.batting.runs)}</div></div>
            <div class="tile"><div class="k">Average</div><div class="v">${n(f.batting.average, 2)}</div></div>
            <div class="tile"><div class="k">Highest</div><div class="v">${n(f.batting.highest)}</div></div>
            <div class="tile"><div class="k">100s / 50s</div><div class="v">${n(f.batting.hundreds)} / ${n(f.batting.fifties)}</div></div>
            <div class="tile"><div class="k">Strike rate</div><div class="v">${n(f.batting.strike_rate, 1)}</div><div class="s">where balls are known</div></div></div>` : ""}
          ${f.bowling.wickets ? `<div class="small muted" style="margin:14px 0 6px">Bowling</div><div class="tiles">
            <div class="tile"><div class="k">Wickets</div><div class="v">${n(f.bowling.wickets)}</div></div>
            <div class="tile"><div class="k">Average</div><div class="v">${n(f.bowling.average, 2)}</div></div>
            <div class="tile"><div class="k">Economy</div><div class="v">${n(f.bowling.economy, 2)}</div></div>
            <div class="tile"><div class="k">Best</div><div class="v">${esc(f.bowling.best || "–")}</div></div>
            <div class="tile"><div class="k">5 in an inns</div><div class="v">${n(f.bowling.five_fors)}</div></div></div>` : ""}
        </section>`).join("") || `<div class="empty">No ${p.format === "Test" ? "Tests" : "ODIs"} for this player.</div>`}
        ${p.years.length > 1 ? `<section class="card" style="margin-bottom:20px"><h3>Year by year</h3><div class="chart-box"><canvas id="yc"></canvas></div></section>` : ""}
        <div class="grid2" style="margin-bottom:20px">
          ${kinds.length ? `<div class="card"><h3>How he got out</h3>${kinds.map((k) => `<div style="margin:8px 0">
            <div class="row" style="justify-content:space-between"><span class="small">${esc(k.kind)}</span><span class="small num">${n(k.n)} · ${n(100 * k.n / kt, 0)}%</span></div>
            <div class="meter"><i style="width:${100 * k.n / kt}%;background:var(--ball)"></i></div></div>`).join("")}</div>` : ""}
          ${counted(p.dismissed_by, "Bowlers who got him out most", true)}
          ${counted(p.dismissed, "Batters he got out most", false)}
          ${rival(p.faced_most, true, "Bowlers he faced most")}
          ${rival(p.bowled_to_most, false, "Batters he bowled to most")}
        </div>`;
      drawYears(p);
    }

    function drawYears(p, tries = 0) {
      const cv = $("#yc");
      if (!cv) return;
      if (!window.Chart) {
        if (tries < 20) setTimeout(() => drawYears(p, tries + 1), 300);
        else cv.closest(".card").remove();
        return;
      }
      if (chart) chart.destroy();
      const ink = css("--muted"), grid = css("--line");
      const hasW = p.years.some((y) => y.wickets);
      const hasR = p.years.some((y) => y.runs);
      chart = new Chart(cv, {
        data: {
          labels: p.years.map((y) => y.year),
          datasets: [
            ...(hasR ? [{ type: "bar", label: "Runs", data: p.years.map((y) => y.runs), backgroundColor: css("--accent"), borderRadius: 3, yAxisID: "y" }] : []),
            ...(hasW ? [{ type: "line", label: "Wickets", data: p.years.map((y) => y.wickets), borderColor: css("--ball"), backgroundColor: css("--ball"),
              pointRadius: 2.5, tension: .25, yAxisID: hasR ? "y2" : "y" }] : []),
          ],
        },
        options: {
          maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          plugins: { legend: { labels: { color: ink, boxWidth: 12 } } },
          scales: {
            x: { ticks: { color: ink, maxRotation: 0, autoSkip: true }, grid: { display: false } },
            y: { ticks: { color: ink }, grid: { color: grid }, beginAtZero: true },
            ...(hasR && hasW ? { y2: { position: "right", ticks: { color: ink }, grid: { display: false }, beginAtZero: true } } : {}),
          },
        },
      });
    }
  }
})();
