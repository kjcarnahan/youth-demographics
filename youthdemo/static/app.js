const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = (n, d = 0) => n == null ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const signed = (n, d = 0) => n == null ? "–" : (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(Math.abs(n), d);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const cls = (n) => n == null || Math.abs(n) < 0.5 ? "" : n > 0 ? "pos" : "neg";

let meta, charts = {};
const saved = (() => { try { return JSON.parse(localStorage.getItem("yd") || "{}"); } catch { return {}; } })();
const persist = () => { try { localStorage.setItem("yd", JSON.stringify(saved)); } catch {} };

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function selectedGeos() {
  const a = $("area").value;
  if (a !== "zips") return [a];
  return [...document.querySelectorAll("#zips input:checked")].map((i) => i.value);
}

function lineChart(id, labels, sets, opts = {}) {
  charts[id]?.destroy();
  const colors = ["--series-1", "--series-2", "--series-3", "--series-4"].map(css);
  const text = css("--text-secondary"), grid = css("--grid"), surface = css("--surface-1");
  charts[id] = new Chart($(id), {
    type: "line",
    data: {
      labels,
      datasets: sets.map((s, i) => ({
        label: s.label, data: s.data, borderColor: colors[i], backgroundColor: colors[i],
        borderWidth: 2, pointRadius: labels.length > 1 ? 3 : 5, pointHoverRadius: 6,
        pointBorderColor: surface, pointBorderWidth: 2, tension: 0, spanGaps: true,
        borderDash: s.dash ? [6, 4] : [],
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: sets.length > 1, position: "bottom", labels: { color: text, usePointStyle: true, boxHeight: 7 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y == null ? "–" : fmt(c.parsed.y, opts.digits || 0)}${opts.unit || ""}` } },
      },
      scales: {
        x: { ticks: { color: text, maxRotation: 0, autoSkipPadding: 12 }, grid: { display: false }, border: { color: grid } },
        y: { beginAtZero: true, ticks: { color: text, maxTicksLimit: 6 }, grid: { color: grid }, border: { display: false } },
      },
    },
  });
}

function tile(k, v, d) {
  return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d || "&nbsp;"}</div></div>`;
}

function selectedSchools() {
  return [...document.querySelectorAll("#schools input:checked")].map((i) => i.value);
}

async function refresh() {
  const geos = selectedGeos(), schools = selectedSchools(), basis = $("basis").value;
  const bySchool = basis === "schools";
  $("areaWrap").hidden = !meta.has_census;
  $("zips").hidden = !meta.has_census || $("area").value !== "zips";
  $("schCount").textContent = `${schools.length} of ${meta.schools.length} selected`;
  const d = await api(`/api/dashboard?basis=${basis}&geos=${encodeURIComponent(geos.join(","))}&schools=${schools.join(",")}`);
  const s = d.series, yrs = s.years, m = (k) => s.metrics[k].values;
  const est = (k) => m(k).map((v) => (v ? v.est : null));
  const sch = d.schools, schTot = sch.years.map((_, i) => sch.ms[i] + sch.hs[i]);
  const teenWord = bySchool ? "students" : "teens";
  $("basisName").textContent = bySchool ? "students in grades 6–12 at the selected public schools" : "residents age 12–17 in the selected Census area";

  // Tiles
  const rows = d.compare.rows, lastY = rows.filter((r) => r.total != null).at(-1);
  let h = "";
  if (bySchool) {
    const n = sch.years.length;
    if (n) {
      h += tile(`Students in grades 6–12 (fall ${sch.years[n - 1]})`, fmt(schTot[n - 1]), `${schools.length} public schools`);
      if (n > 1) h += tile(`Enrollment change since ${sch.years[0]}`, signed(100 * (schTot[n - 1] / schTot[0] - 1)) + "%", `${fmt(schTot[0])} → ${fmt(schTot[n - 1])}`);
    } else h += tile("Students in grades 6–12", "–", "Pick at least one school");
  } else {
    const t = m("age_12_17");
    const idx = t.map((v, i) => (v ? i : -1)).filter((i) => i >= 0);
    const last = idx.at(-1), first = idx[0];
    if (last != null) {
      const v = t[last];
      h += tile(`Residents age 12–17 (${yrs[last] - 4}–${yrs[last]})`, fmt(v.est), v.moe != null ? `± ${fmt(v.moe)} margin of error` : "");
      if (first !== last) h += tile(`Teen population change since ${yrs[first]}`, signed(100 * (v.est / t[first].est - 1)) + "%", `${fmt(t[first].est)} → ${fmt(v.est)}`);
    } else h += tile("Residents age 12–17", "–", geos.length ? "No data for this area" : "Pick at least one ZIP");
  }
  if (lastY) {
    h += tile(`Youth group (${lastY.year})`, fmt(lastY.total), lastY.rate != null ? `${fmt(lastY.rate, 1)} per 1,000 local ${teenWord}` : "");
    if (lastY.expected != null && rows.filter((r) => r.expected != null).length > 1) {
      const base = rows.find((r) => r.expected != null);
      h += tile(`Real growth since ${base.year}`, `<span class="${cls(lastY.real_change)}">${signed(lastY.real_change)}</span>`,
        `${signed(lastY.demographic_change)} came from population change`);
    }
  } else h += tile("Youth group", "–", "Add your counts below");
  $("tiles").innerHTML = h;

  // School enrollment
  lineChart("cSch", sch.years, [{ label: "Grades 6–8", data: sch.ms }, { label: "Grades 9–12", data: sch.hs }]);
  let st = "<tr><th>Fall</th><th>Grades 6–8</th><th>Grades 9–12</th><th>Total</th></tr>";
  for (let i = sch.years.length - 1; i >= 0; i--) st += `<tr><td>${sch.years[i]}</td><td>${fmt(sch.ms[i])}</td><td>${fmt(sch.hs[i])}</td><td>${fmt(schTot[i])}</td></tr>`;
  $("tSch").innerHTML = st;

  // Youth vs kept pace
  const cy = rows.filter((r) => r.total != null);
  lineChart("cPace", cy.map((r) => r.year), [
    { label: "Youth group", data: cy.map((r) => r.total) },
    { label: "Kept pace with teen population", data: cy.map((r) => r.expected), dash: true },
  ], { digits: 0 });
  lineChart("cRate", cy.map((r) => r.year), [
    { label: `Youth group per 1,000 local ${teenWord}`, data: cy.map((r) => r.rate) },
  ], { digits: 1 });

  let yt = `<tr><th>Year</th><th>Middle</th><th>High</th><th>Total</th><th>${bySchool ? "Students 6–12" : "Teens 12–17"}</th><th>Per 1,000</th>
    <th>Kept pace</th><th>From population</th><th>Real change</th><th>Note</th><th></th></tr>`;
  for (const r of rows) {
    const carried = r.pop_year != null && r.pop_year !== r.year;
    yt += `<tr><td>${r.year}</td><td>${fmt(r.ms)}</td><td>${fmt(r.hs)}</td><td>${fmt(r.total)}</td>
      <td title="${carried ? "Figure from " + r.pop_year : ""}">${fmt(r.pop_total)}${carried ? " *" : ""}</td>
      <td>${fmt(r.rate, 1)}</td><td>${fmt(r.expected)}</td><td>${signed(r.demographic_change)}</td>
      <td class="${cls(r.real_change)}">${signed(r.real_change)}</td><td style="text-align:left">${esc(r.note)}</td>
      <td><button data-del="${r.year}" title="Delete ${r.year}">✕</button></td></tr>`;
  }
  if (!rows.length) yt += `<tr><td colspan="11" class="empty">No youth-group counts yet. Add a year above.</td></tr>`;
  else if (rows.some((r) => r.pop_year != null && r.pop_year !== r.year)) yt += `<tr><td colspan="11" style="text-align:left;border:0" class="note">* No data for that year yet; nearest available year used.</td></tr>`;
  $("tYouth").innerHTML = yt;

  if (!meta.has_census) return;
  // Population
  lineChart("cPop", yrs, [{ label: "Ages 12–14", data: est("age_12_14") }, { label: "Ages 15–17", data: est("age_15_17") }]);
  const cols = ["age_9_11", "age_12_14", "age_15_17", "age_12_17", "age_18_19", "under_18", "enrolled_5_8", "enrolled_9_12", "total_pop"];
  let pt = "<tr><th>5 years ending</th>" + cols.map((c) => `<th>${s.metrics[c].label}</th>`).join("") + "</tr>";
  yrs.forEach((y, i) => {
    pt += `<tr><td>${y}</td>` + cols.map((c) => { const v = m(c)[i]; return `<td>${v ? fmt(v.est) + (v.moe != null ? ` <span class="note">±${fmt(v.moe)}</span>` : "") : "–"}</td>`; }).join("") + "</tr>";
  });
  $("tPop").innerHTML = pt;

  lineChart("cRace", yrs, ["white_nh_10_17", "asian_10_17", "hispanic_10_17", "black_10_17"].map((k) => ({ label: s.metrics[k].label, data: est(k) })));
  lineChart("cSex", yrs, [{ label: "Boys", data: est("male_10_17") }, { label: "Girls", data: est("female_10_17") }]);

  const p = d.pipeline;
  if (p) {
    lineChart("cPipe", p.map((r) => r.year), [{ label: "Ages 12–14", data: p.map((r) => r.age_12_14) }, { label: "Ages 15–17", data: p.map((r) => r.age_15_17) }]);
    $("pipeNote").textContent = "Source: 2020 Census, population by single year of age.";
  } else {
    charts.cPipe?.destroy();
    $("pipeNote").textContent = "2020 Census single-year-of-age counts are not available for this selection.";
  }
}

async function loadProfile() {
  const p = await api("/api/profile");
  $("profYear").textContent = p.year ? `ACS ${p.year - 4}–${p.year}` : "";
  const pc = (n) => (n == null ? "–" : fmt(n, 0) + "%");
  let h = `<tr><th>Area</th><th>Population</th><th>Teens 12–17</th><th>Teen share</th><th>Median household income</th>
    <th>Households with children</th><th>White</th><th>Asian</th><th>Hispanic</th><th>Black</th>
    <th>Age 5–17, other language at home</th><th>Foreign born</th><th>Homeowners</th></tr>`;
  for (const r of p.rows) {
    h += `<tr><td>${esc(r.name)}</td><td>${fmt(r.population)}</td><td>${fmt(r.teens_12_17)}</td><td>${r.teen_share == null ? "–" : fmt(r.teen_share, 1) + "%"}</td>
      <td>${r.median_income == null ? "–" : "$" + fmt(r.median_income)}</td><td>${pc(r.hh_with_children)}</td><td>${pc(r.white_nh)}</td>
      <td>${pc(r.asian_nh)}</td><td>${pc(r.hispanic)}</td><td>${pc(r.black_nh)}</td><td>${pc(r.other_language_5_17)}</td>
      <td>${pc(r.foreign_born)}</td><td>${pc(r.owner_occupied)}</td></tr>`;
  }
  $("tProf").innerHTML = h;
}

async function init() {
  meta = await api("/api/meta");
  const any = meta.has_census || meta.schools.length > 0;
  $("nodata").hidden = any;
  $("dash").hidden = !any;
  if (!any) return;
  $("needKey").hidden = meta.has_census;
  $("census").hidden = !meta.has_census;
  $("fetched").textContent = [meta.schools_fetched_at && `School data downloaded ${meta.schools_fetched_at.replace("T", " ")}.`,
    meta.fetched_at && `Census data downloaded ${meta.fetched_at.replace("T", " ")}.`].filter(Boolean).join(" ");
  $("basis").innerHTML = (meta.schools.length ? `<option value="schools">Public school enrollment, grades 6–12</option>` : "") +
    (meta.has_census ? `<option value="census">Census residents age 12–17</option>` : "");
  if (saved.basis && [...$("basis").options].some((o) => o.value === saved.basis)) $("basis").value = saved.basis;
  const zips = meta.geos.filter((g) => g.kind === "zcta"), others = meta.geos.filter((g) => g.kind !== "zcta");
  $("area").innerHTML = `<option value="zips">Study area (ZIP codes)</option>` + others.map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("");
  const on = saved.zips || zips.map((g) => g.id);
  $("zips").innerHTML = zips.map((g) => `<label title="${esc(g.name)}"><input type="checkbox" value="${g.id}" ${on.includes(g.id) ? "checked" : ""}> ${g.id.split(":")[1]}</label>`).join("");
  if (saved.area && [...$("area").options].some((o) => o.value === saved.area)) $("area").value = saved.area;
  let sh = "", dist = null;
  for (const x of meta.schools) {
    if (x.district !== dist) { dist = x.district; sh += `<h3>${esc(dist)}</h3>`; }
    const chk = saved.schools ? saved.schools.includes(x.id) : x.default;
    sh += `<label><input type="checkbox" value="${x.id}" ${chk ? "checked" : ""}> ${esc(x.name)} <span class="note">${esc(x.zip)}</span></label>`;
  }
  $("schools").innerHTML = sh;
  const changed = () => {
    saved.basis = $("basis").value; saved.area = $("area").value; saved.schools = selectedSchools();
    saved.zips = [...document.querySelectorAll("#zips input:checked")].map((i) => i.value); persist(); refresh();
  };
  $("area").onchange = $("zips").onchange = $("basis").onchange = $("schools").onchange = changed;
  $("yform").onsubmit = async (e) => {
    e.preventDefault();
    $("err").textContent = "";
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api("/api/youth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
      e.target.reset();
      refresh();
    } catch (x) { $("err").textContent = x.message; }
  };
  $("tYouth").onclick = async (e) => {
    const y = e.target.dataset?.del;
    if (y && confirm(`Delete the ${y} youth-group count?`)) { await api("/api/youth?year=" + y, { method: "DELETE" }); refresh(); }
  };
  if (meta.warnings.length) $("warn").innerHTML = `<details><summary>${meta.warnings.length} download warning(s)</summary><ul class="notes">${meta.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>`;
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", refresh);
  await Promise.all([refresh(), loadProfile()]);
}
init().catch((e) => { document.querySelector("main").insertAdjacentHTML("beforeend", `<p class="card neg">${esc(e.message)}</p>`); });
