/* Сравнение решений СПР — фронтенд (vanilla JS, без сборки).
   Маршруты: #/runs — прогоны, #/runs/<id> — ход/результаты, #/workspace — код. */
"use strict";

const $view = document.getElementById("view");
let pollTimer = null;

// ---------- helpers ----------

function user() { return document.getElementById("username").value.trim() || "anon"; }

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "X-User-Id": user(), "Content-Type": "application/json",
               ...(opts.headers || {}) },
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function openModal(title, html) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-content").innerHTML = html;
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }

function fmtMetric(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return Math.abs(v) < 1 ? (v * 100).toFixed(1) + "%" : String(v);
  return String(v);
}

// ---------- routing ----------

function route() {
  clearTimeout(pollTimer);
  const h = location.hash || "#/runs";
  document.querySelectorAll("nav#tabs a").forEach(a =>
    a.classList.toggle("active", h.startsWith(a.getAttribute("href"))));
  const m = h.match(/^#\/runs\/(\w+)/);
  if (m) renderRun(m[1]);
  else if (h.startsWith("#/workspace")) renderWorkspace();
  else renderRuns();
}
window.addEventListener("hashchange", route);

// ---------- прогоны: список + новый ----------

async function renderRuns() {
  $view.innerHTML = "<p>Загрузка…</p>";
  let runs = [], datasets = [], builtins = [];
  try {
    [ { runs }, { datasets }, { checks: builtins } ] = await Promise.all([
      api("/api/runs"), api("/api/datasets"), api("/api/builtin/checks"),
    ]);
  } catch (e) { $view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; return; }

  const dsOpts = datasets.map(d =>
    `<option value="${esc(d.id)}">${esc(d.id)} (${d.apps} заявок${d.requires_pipeline ? ", нужен pipeline" : ""})</option>`).join("");
  const checkOpts = builtins.map(b =>
    `<label style="display:flex;gap:6px;align-items:center;margin:3px 0">
       <input type="checkbox" class="chk" value="${esc(b.file)}" checked ${b.error ? "disabled" : ""}>
       <span>${esc(b.label)}</span>
       <input class="chk-params" data-file="${esc(b.file)}" value="${esc(JSON.stringify(b.params))}" size="28" title="параметры (JSON)">
     </label>`).join("");

  $view.innerHTML = `
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Новый прогон</h3>
      <label>имя конфигурации</label><input id="run-name" value="прогон ${new Date().toLocaleString("ru")}">
      <label>датасет</label><select id="run-dataset">${dsOpts}</select>
      <label>процедуры приведения / сравнения / фактов</label>
      <div style="display:flex;gap:6px">
        <select class="proc"><option value="builtin:default">встроенные (default)</option></select>
        <select class="proc"><option value="builtin:default">встроенные (default)</option></select>
        <select class="proc"><option value="builtin:default">встроенные (default)</option></select>
      </div>
      <label>проверки (выданные — подключены по контракту как есть)</label>
      <div>${checkOpts}</div>
      <label style="display:flex;gap:6px;align-items:center">
        <input type="checkbox" id="run-llm" checked> запросить мнение ИИ по завершении
      </label>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button onclick="createRun(false)">Запустить</button>
        <button class="ghost" onclick="createRun(true)">Сохранить конфигурацию</button>
      </div>
      <p class="hint">Долгий этап не блокирует интерфейс: прогон идёт в фоне, ход виден на странице прогона.</p>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Прогоны (${esc(user())})</h3>
      ${runs.length ? `<table><thead><tr><th>имя</th><th>статус</th><th>создан</th></tr></thead><tbody>
        ${runs.map(r => `<tr>
          <td><a href="#/runs/${r.id}">${esc(r.config.name)}</a></td>
          <td>${statusBadge(r.status)}</td>
          <td>${new Date(r.created * 1000).toLocaleString("ru")}</td></tr>`).join("")}
      </tbody></table>` : `<p class="hint">Прогонов пока нет — создайте первый слева.</p>`}
    </div>
  </div>`;
}

function statusBadge(s) {
  const titles = { pending: "ожидает", running: "в работе", done: "готов", error: "ошибка" };
  return `<span class="badge ${s}">${titles[s] || s}</span>`;
}

async function createRun(saveOnly) {
  const checks = [...document.querySelectorAll(".chk")].filter(c => c.checked && !c.disabled)
    .map(c => ({
      source: { kind: "builtin", name: c.value },
      params: JSON.parse(document.querySelector(`.chk-params[data-file="${CSS.escape(c.value)}"]`).value || "{}"),
      enabled: true,
    }));
  const cfg = {
    name: document.getElementById("run-name").value,
    dataset: document.getElementById("run-dataset").value,
    normalize: { kind: "builtin", name: "default" },
    compare: { kind: "builtin", name: "default" },
    facts: { kind: "builtin", name: "default" },
    checks, llm_opinion: document.getElementById("run-llm").checked,
  };
  try {
    if (saveOnly) { await api("/api/configs", { method: "POST", body: JSON.stringify(cfg) }); alert("Конфигурация сохранена"); }
    else { const { run } = await api("/api/runs", { method: "POST", body: JSON.stringify(cfg) });
           location.hash = `#/runs/${run.id}`; }
  } catch (e) { alert("Ошибка: " + e.message); }
}

// ---------- прогон: ход и результаты ----------

async function renderRun(id, tab = "pivot") {
  let run;
  try { ({ run } = await api(`/api/runs/${id}`)); }
  catch (e) { $view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; return; }

  if (run.status === "running" || run.status === "pending") {
    $view.innerHTML = `
      <div class="card">
        <h3 style="margin-top:0">${esc(run.config.name)} ${statusBadge(run.status)}</h3>
        ${run.stages.map(s => `
          <div class="stage">
            <span class="st">${statusBadge(s.status)}</span>
            <b>${esc(s.title)}</b>
            <span class="msg">${esc(s.message || (s.status === "running" ? "выполняется…" : ""))}</span>
            ${s.code_ref ? `<button class="ghost" style="margin-left:auto" onclick='showCode(${JSON.stringify(s.title)}, ${JSON.stringify(s.code_snapshot || s.code_ref)})'>код этапа</button>` : ""}
          </div>`).join("")}
        <p class="hint">Можно закрыть страницу — прогон продолжится, результат сохранится.</p>
      </div>`;
    pollTimer = setTimeout(() => renderRun(id), 1500);
    return;
  }

  const s = run.summary || {};
  const cards = `
    <div class="cards">
      <div class="stat"><div class="num">${s.total_apps ?? 0}</div><div class="lbl">заявок в прогоне</div></div>
      <div class="stat"><div class="num">${s.apps_with_diffs ?? 0}</div><div class="lbl">с различиями</div></div>
      <div class="stat"><div class="num">${s.apps_decision_changed ?? 0}</div><div class="lbl">смена решения</div></div>
      <div class="stat"><div class="num">${esc(fmtObj(s.decision_before))} → ${esc(fmtObj(s.decision_after))}</div><div class="lbl">решение до → после</div></div>
    </div>`;

  $view.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0">${esc(run.config.name)} ${statusBadge(run.status)}
        <span class="hint">· ${esc(run.config.dataset)}</span></h3>
      ${run.error ? `<pre class="code">${esc(run.error)}</pre>` : cards}
      ${run.stages.map(sg => `
        <div class="stage"><span class="st">${statusBadge(sg.status)}</span>
          <b>${esc(sg.title)}</b><span class="msg">${esc(sg.message)}</span>
          <button class="ghost" style="margin-left:auto" onclick='showCode(${JSON.stringify(sg.title)}, ${JSON.stringify(sg.code_snapshot || sg.code_ref || "")})'>код этапа</button>
        </div>`).join("")}
      <div style="margin-top:10px;display:flex;gap:8px">
        <a href="/api/runs/${id}/checks/export.html" target="_blank"><button class="ghost">отчёт проверок по ссылке</button></a>
        <a href="/api/runs/${id}/checks/export.html" download="checks-${id}.html"><button class="ghost">скачать отчёт (HTML)</button></a>
        <button class="ghost" onclick="navigator.clipboard.writeText(location.origin + '/#/runs/${id}');alert('ссылка на сводный отчёт скопирована')">скопировать ссылку на сводный</button>
      </div>
    </div>
    <section class="tabs">
      <button class="${tab === "pivot" ? "active" : ""}" onclick="renderRun('${id}','pivot')">Сводная таблица</button>
      <button class="${tab === "checks" ? "active" : ""}" onclick="renderRun('${id}','checks')">Отчёт проверок</button>
      <button class="${tab === "llm" ? "active" : ""}" onclick="renderRun('${id}','llm')">Мнение ИИ</button>
    </section>
    <div id="tab-body">Загрузка…</div>`;

  if (tab === "pivot") renderPivot(id);
  else if (tab === "checks") renderChecks(id);
  else renderLlm(id, run);
}

function fmtObj(o) {
  if (!o) return "—";
  return Object.entries(o).map(([k, v]) => `${k}: ${v}`).join(", ");
}

function showCode(title, code) { openModal(title, `<pre class="code">${esc(code)}</pre>`); }

// ---------- сводная таблица ----------

async function renderPivot(id) {
  const el = document.getElementById("tab-body");
  el.innerHTML = `
    <div class="card">
      <div class="toolbar">
        <div><label>строки (измерения через запятую)</label>
          <input id="pv-rows" value="path,change_type" size="30"></div>
        <div><label>колонки</label>
          <select id="pv-cols"><option value="">—</option><option value="change_type">change_type</option><option value="diff_flag">diff_flag</option><option>key</option><option>prefix</option><option>class</option><option>value_after</option></select></div>
        <div><label>тип изменения</label>
          <select id="pv-ct"><option value="">все</option><option>VALUE_CHANGED</option><option>ATTRIBUTE_NEW</option><option>ATTRIBUTE_LOST</option><option>SAME</option></select></div>
        <div><label>только различия</label>
          <select id="pv-diff"><option value="-1">все строки</option><option value="1">только diff</option><option value="0">без изменений</option></select></div>
        <div><label>путь начинается с</label><input id="pv-prefix" size="24" placeholder="decisionResult.product_trials"></div>
        <button onclick="loadPivot('${id}')">Применить раскладку</button>
      </div>
      <div id="pv-table" class="hint">Нажмите «Применить раскладку».</div>
      <p class="hint">Клик по ячейке — список заявок группы, оттуда — к конкретной заявке и атрибуту.</p>
    </div>`;
}

async function loadPivot(id) {
  const box = document.getElementById("pv-table");
  const rowsDim = document.getElementById("pv-rows").value || "path";
  const colDim = document.getElementById("pv-cols").value;
  const params = new URLSearchParams({ dims: rowsDim + (colDim ? "," + colDim : "") });
  const ct = document.getElementById("pv-ct").value;
  if (ct) params.set("change_type", ct);
  const df = document.getElementById("pv-diff").value;
  if (df !== "-1") params.set("diff_flag", df);
  const pf = document.getElementById("pv-prefix").value.trim();
  if (pf) params.set("path_prefix", pf);
  box.textContent = "Считаю факты…";
  let data;
  try { data = await api(`/api/runs/${id}/facts?${params}`); }
  catch (e) { box.textContent = "Ошибка: " + e.message; return; }
  if (!data.rows.length) { box.textContent = "Нет данных по такому фильтру."; return; }

  const rowDims = rowsDim.split(",").map(s => s.trim());
  const groups = new Map();
  const colKeys = new Set();
  for (const r of data.rows) {
    const rk = rowDims.map(d => r.dims[d] ?? "").join("‖");
    const ck = colDim ? (r.dims[colDim] ?? "") : "заявки";
    colKeys.add(ck);
    if (!groups.has(rk)) groups.set(rk, { dims: rowDims.map(d => r.dims[d] ?? ""), cells: {} });
    groups.get(rk).cells[ck] = r;
  }
  const cols = [...colKeys];
  const filterOf = cell => {
    const f = {};
    if (ct) f.change_type = ct;
    if (df !== "-1") f.diff_flag = df;
    if (pf) f.path_prefix = pf;
    Object.assign(f, cell.dims);
    return f;
  };
  let html = `<table class="pivot"><thead><tr><th>${rowDims.join("</th><th>")}</th>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>`;
  for (const g of groups.values()) {
    html += `<tr>${g.dims.map(d => `<td>${esc(d)}</td>`).join("")}` +
      cols.map(c => {
        const cell = g.cells[c];
        return cell ? `<td class="clickable" data-f='${esc(JSON.stringify(filterOf(cell)))}'>${cell.cnt_apps}</td>` : "<td>·</td>";
      }).join("") + "</tr>";
  }
  box.innerHTML = html + "</tbody></table>";
  box.querySelectorAll("td.clickable").forEach(td =>
    td.onclick = () => showApps(id, JSON.parse(td.dataset.f)));
}

async function showApps(id, filters, offset = 0) {
  const params = new URLSearchParams({ offset });
  for (const [k, v] of Object.entries(filters)) if (v !== "") params.set(k, v);
  const data = await api(`/api/runs/${id}/apps?${params}`);
  openModal(`Заявки (${data.total})`, `
    <p class="hint">${Object.entries(filters).filter(([,v]) => v !== "").map(([k,v]) => `${k}=${v}`).join(" · ") || "без фильтра"}</p>
    ${data.app_ids.map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join("") || "<p>пусто</p>"}
    <div style="margin-top:10px">
      ${offset > 0 ? `<button class="ghost" onclick='showApps("${id}", ${JSON.stringify(filters)}, ${offset - 200})'>← назад</button>` : ""}
      ${offset + 200 < data.total ? `<button class="ghost" onclick='showApps("${id}", ${JSON.stringify(filters)}, ${offset + 200})'>вперёд →</button>` : ""}
    </div>`);
}

async function showApp(id, appId) {
  const data = await api(`/api/runs/${id}/apps/${appId}`);
  const seg = data.segment || {};
  const rows = data.attributes.map(r => `<tr class="${r.DIFF_FLAG ? "fail" : ""}">
      <td>${esc(r.PATH)}${r.ARRAY_KEY ? `<br><span class="hint">[${esc(r.ARRAY_KEY)}]</span>` : ""}</td>
      <td>${esc(r.VALUE_BEFORE || "—")}</td><td>${esc(r.VALUE_AFTER || "—")}</td>
      <td>${esc(r.CHANGE_TYPE)}</td></tr>`).join("");
  openModal(`Заявка ${appId}`, `
    <p class="hint">решение: ${esc(seg.DECISION_BEFORE)} → ${esc(seg.DECISION_AFTER)} ·
       продукты: ${esc(seg.PRODUCTS)} · модель: ${esc(seg.PD_MODEL_BEFORE)} → ${esc(seg.PD_MODEL_AFTER)}</p>
    <table><thead><tr><th>атрибут</th><th>до</th><th>после</th><th>тип</th></tr></thead>
    <tbody>${rows}</tbody></table>`);
}

// ---------- отчёт проверок ----------

async function renderChecks(id) {
  const el = document.getElementById("tab-body");
  const { rows } = await api(`/api/runs/${id}/checks`);
  if (!rows.length) { el.innerHTML = `<div class="card hint">Проверки не выполнялись.</div>`; return; }
  const cols = ["LABEL", "SOURCE", "ARGUMENT", "CNT", "TOTAL", "METRIC", "RESULT", "CONFIDENCE"];
  el.innerHTML = `<div class="card"><table><thead><tr>${cols.map(c => `<th>${c.toLowerCase()}</th>`).join("")}<th>заявки</th></tr></thead>
    <tbody>${rows.map(r => `
      <tr class="${r.RESULT === "FAIL" ? "fail" + (r.CONFIDENCE === "LOW" ? " low" : "") : ""}">
        ${cols.map(c => `<td>${esc(fmtMetric(r[c]))}</td>`).join("")}
        <td class="clickable">${(r.APP_IDS || []).length ? esc(r.APP_IDS.length) + " шт." : "—"}</td>
      </tr>`).join("")}
    </tbody></table>
    <p class="hint">Строки CONFIDENCE=LOW подсвечены бледно: срабатываний мало, это сигнал для проверки, а не подтверждённая поломка.</p>
  </div>`;
  el.querySelectorAll("td.clickable").forEach((td, i) => td.onclick = () => {
    const ids = rows[i].APP_IDS || [];
    if (ids.length) openModal(`Заявки проверки (${ids.length})`,
      ids.map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join(""));
  });
}

// ---------- мнение ИИ ----------

async function renderLlm(id, run) {
  const el = document.getElementById("tab-body");
  const llm = run.llm;
  el.innerHTML = `<div class="card" id="llm-box">
    ${llm && llm.status === "ok"
      ? `<b>Модель ${esc(llm.model)}</b><p style="white-space:pre-wrap">${esc(llm.opinion)}</p>`
      : `<p class="hint">${esc(llm?.reason || "Мнение ещё не запрашивалось.")}</p>
         <button onclick="askLlm('${id}')">Запросить мнение ИИ</button>`}
  </div>`;
}

async function askLlm(id) {
  const box = document.getElementById("llm-box");
  box.innerHTML = "<p class='hint'>Модель смотрит сводку…</p>";
  try {
    const { llm } = await api(`/api/runs/${id}/llm`, { method: "POST" });
    renderLlm(id, { llm });
  } catch (e) { box.innerHTML = "<p>Ошибка: " + esc(e.message) + "</p>"; }
}

// ---------- рабочая область ----------

async function renderWorkspace() {
  const el = $view;
  el.innerHTML = "<p>Загрузка…</p>";
  const { files } = await api("/api/workspace");
  el.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0">Рабочая область (${esc(user())})</h3>
      <div class="toolbar">
        <div><label>файл</label><input id="ws-name" list="ws-files" placeholder="my_check">
          <datalist id="ws-files">${files.map(f => `<option value="${esc(f.name)}">`).join("")}</datalist></div>
        <button onclick="loadWs()">Открыть</button>
        <button onclick="saveWs()">Сохранить</button>
        <button class="ghost" onclick="deleteWs()">Удалить</button>
      </div>
      <textarea id="ws-code" class="code" placeholder="# CHECK = {'id': ..., 'label': ..., 'params': ..., 'entrypoint': run}
# def run(mart, params): -> list[dict]  (контракт: task/checks/README.md)"></textarea>
      <div class="toolbar" style="margin-top:10px">
        <div><label>применить к прогону</label><input id="ws-run" placeholder="id прогона" size="12"></div>
        <button onclick="tryWs()">Проверить и добавить в отчёт</button>
      </div>
      <div id="ws-result" class="hint"></div>
    </div>`;
}

async function loadWs() {
  const name = document.getElementById("ws-name").value.trim();
  if (!name) return;
  const { code } = await api(`/api/workspace/${encodeURIComponent(name)}`);
  document.getElementById("ws-code").value = code;
}

async function saveWs() {
  const name = document.getElementById("ws-name").value.trim();
  if (!name) return alert("введите имя файла");
  await api(`/api/workspace/${encodeURIComponent(name)}`,
    { method: "PUT", body: JSON.stringify({ code: document.getElementById("ws-code").value }) });
  alert("сохранено");
}

async function deleteWs() {
  const name = document.getElementById("ws-name").value.trim();
  if (name && confirm("Удалить " + name + "?")) {
    await api(`/api/workspace/${encodeURIComponent(name)}`, { method: "DELETE" });
    document.getElementById("ws-code").value = "";
  }
}

async function tryWs() {
  const runId = document.getElementById("ws-run").value.trim();
  const res = document.getElementById("ws-result");
  if (!runId) return alert("укажите id прогона");
  try {
    const { rows, check_id } = await api(`/api/runs/${runId}/checks/try`,
      { method: "POST", body: JSON.stringify({ kind: "code", code: document.getElementById("ws-code").value }) });
    await api(`/api/runs/${runId}/checks/add_result`,
      { method: "POST", body: JSON.stringify({ rows }) });
    res.innerHTML = `Проверка <b>${esc(check_id)}</b>: ${rows.length} строк — добавлены в <a href="#/runs/${runId}" onclick="closeModal()">отчёт прогона</a>.`;
  } catch (e) { res.textContent = "Ошибка: " + e.message; }
}

// ---------- init ----------

const savedUser = localStorage.getItem("csd-user");
document.getElementById("username").value = savedUser || "user-" + Math.random().toString(36).slice(2, 6);
document.getElementById("username").addEventListener("change", e => {
  localStorage.setItem("csd-user", e.target.value.trim());
  route();
});
route();
