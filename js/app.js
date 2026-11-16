// Reads window.JOB_LISTINGS and window.JOB_PROFILES (from data/listings.js) and draws the page.
// To move to a real database later, replace loadData() with a fetch() to your API;
// nothing else in this file needs to change.

// Institute-type filter buttons: [id, button label, kinds it covers]. Add a row here for a new type.
const GROUPS = [
  ["iit", "IITs and IISc", ["IIT", "IISc"]],
  ["nit", "NITs", ["NIT"]],
  ["iiser", "IISERs", ["IISER"]],
  ["cu", "Central universities", ["CU"]]
];
const KIND_LABEL = { IIT: "IIT", IISc: "IISc", NIT: "NIT", IISER: "IISER", CU: "Central University" };
const STATUS_LABEL = { active: "Open", inactive: "Closed", unverified: "Not yet verified" };
const STATUS_RANK = { active: 0, unverified: 1, inactive: 2 };

const GROUP_OF = {};
GROUPS.forEach(([id, , kinds]) => kinds.forEach(k => (GROUP_OF[k] = id)));

async function loadData() {
  return { listings: window.JOB_LISTINGS || [], profiles: window.JOB_PROFILES || [] };
}

const state = { q: "", kinds: new Set(), status: "all", region: "all", sort: "status", profile: "" };
let LISTINGS = [], PROFILES = [];

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const safeUrl = u => (/^https?:\/\//i.test(String(u || "").trim()) ? String(u).trim() : "");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDate = d => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
  return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : "";
};

const TODAY = new Date().toISOString().slice(0, 10);
const daysLeft = d => Math.round((Date.parse(d) - Date.parse(TODAY)) / 86400000);

// The status to show: a listing whose last date has passed is shown as Closed even before the data is updated.
function shownStatus(d) {
  const s = STATUS_LABEL[d.status] ? d.status : "unverified";
  return s === "active" && d.deadline && !d.rolling && d.deadline < TODAY ? "inactive" : s;
}

function deadlineInfo(d) {
  const date = fmtDate(d.deadline);
  if (date && d.deadline >= TODAY) {
    const n = daysLeft(d.deadline);
    const left = n === 0 ? "today" : n === 1 ? "1 day left" : `${n} days left`;
    return { text: `${d.rolling ? "Next cut-off" : "Last date to apply"}: ${date} (${left})`, cls: n <= 7 ? "soon" : "set" };
  }
  if (d.rolling) return { text: "Rolling advertisement: apply any time", cls: "set" };
  if (date) return { text: `Last date was ${date}`, cls: "past" };
  return { text: "Last date not known", cls: "none" };
}

function tile(d) {
  const apply = safeUrl(d.applyUrl), site = safeUrl(d.siteUrl), ad = safeUrl(d.adUrl);
  const status = shownStatus(d);
  const primary = apply ? `<a class="btn primary" href="${esc(apply)}" target="_blank" rel="noopener">Apply / openings</a>`
    : site ? `<a class="btn" href="${esc(site)}" target="_blank" rel="noopener">Institute website</a>`
    : `<span class="btn none">No link yet</span>`;
  const advert = ad ? `<a class="btn" href="${esc(ad)}" target="_blank" rel="noopener">Advertisement</a>`
    : `<span class="btn none">No advertisement link yet</span>`;
  const checked = fmtDate(d.checked);
  return `<article class="tile" data-status="${status}">
    <div class="top"><span class="kind">${esc(KIND_LABEL[d.kind] || d.kind)}</span><span class="pill ${status}">${STATUS_LABEL[status]}</span></div>
    <h2>${esc(d.name)}</h2>
    <p class="where">${esc([d.city, d.state].filter(Boolean).join(", "))}</p>
    <p class="deadline ${deadlineInfo(d).cls}">${esc(deadlineInfo(d).text)}</p>
    <p class="note">${esc(d.note)}</p>
    <div class="actions">${primary}${advert}</div>
    <p class="checked">${checked ? "Checked " + checked : "Not checked yet"}</p>
  </article>`;
}

const segHtml = opts => opts.map(([v, label, n, on]) =>
  `<button type="button" data-v="${v}" aria-pressed="${on}">${label}<span>${n}</span></button>`).join("");

function render() {
  const q = state.q.trim().toLowerCase();
  const inProfile = LISTINGS.filter(d => d.profile === state.profile);
  const base = inProfile.filter(d =>
    (state.region === "all" || d.state === state.region) &&
    (!q || [d.name, d.city, d.state].join(" ").toLowerCase().includes(q)));
  const kindOk = d => !state.kinds.size || state.kinds.has(GROUP_OF[d.kind]);
  const statusOk = d => state.status === "all" || shownStatus(d) === state.status;
  const byStatus = base.filter(statusOk), byKind = base.filter(kindOk);
  const rows = byKind.filter(statusOk);
  const count = (a, f) => a.filter(f).length;

  $("kindSeg").innerHTML = segHtml([["all", "All", byStatus.length, !state.kinds.size],
    ...GROUPS.map(([id, label]) => [id, label, count(byStatus, d => GROUP_OF[d.kind] === id), state.kinds.has(id)])]);
  $("statusSeg").innerHTML = segHtml([["all", "Any", byKind.length, state.status === "all"],
    ...Object.keys(STATUS_LABEL).map(s => [s, STATUS_LABEL[s], count(byKind, d => shownStatus(d) === s), state.status === s])]);

  const closes = d => (shownStatus(d) === "active" && d.deadline >= TODAY ? d.deadline : "9999");
  rows.sort((a, b) => state.sort === "status"
    ? (STATUS_RANK[shownStatus(a)] - STATUS_RANK[shownStatus(b)]) || a.name.localeCompare(b.name)
    : state.sort === "deadline"
    ? closes(a).localeCompare(closes(b)) || a.name.localeCompare(b.name)
    : a.name.localeCompare(b.name));

  $("count").textContent = `Showing ${rows.length} of ${inProfile.length} institutes`;
  $("grid").innerHTML = rows.length ? rows.map(tile).join("")
    : `<div class="empty">${inProfile.length
        ? 'No institutes match these filters. Clear the search or choose "All" and "Any".'
        : "No listings for this job profile yet."}</div>`;
}

function fillRegions() {
  const regions = [...new Set(LISTINGS.filter(d => d.profile === state.profile).map(d => d.state).filter(Boolean))].sort();
  $("state").innerHTML = `<option value="all">All states</option>` + regions.map(s => `<option>${esc(s)}</option>`).join("");
  state.region = "all";
}

$("kindSeg").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  const v = b.dataset.v;
  if (v === "all") state.kinds.clear(); else state.kinds.has(v) ? state.kinds.delete(v) : state.kinds.add(v);
  render();
};
$("statusSeg").onclick = e => { const b = e.target.closest("button"); if (b) { state.status = b.dataset.v; render(); } };
$("q").addEventListener("input", e => { state.q = e.target.value; render(); });
$("state").addEventListener("change", e => { state.region = e.target.value; render(); });
$("sort").addEventListener("change", e => { state.sort = e.target.value; render(); });
$("profile").addEventListener("change", e => { state.profile = e.target.value; fillRegions(); render(); });

loadData().then(({ listings, profiles }) => {
  LISTINGS = listings;
  PROFILES = profiles.length ? profiles : [{ id: "professor", label: "Professor / faculty" }];
  $("profile").innerHTML = PROFILES.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
  state.profile = PROFILES[0].id;
  fillRegions();
  render();
}).catch(() => {
  $("grid").innerHTML = `<div class="empty">The listings could not be loaded. Check that data/listings.js is present and has no typing errors.</div>`;
});
