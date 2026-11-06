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

// Понятные названия параметров проверок (ключи из task/checks/*.py)
const PARAM_LABELS = {
  min_share:         ["минимальная доля", "0.001 = 0.1% — ниже этого срабатывания игнорируются"],
  max_drop_rel:      ["максимально допустимое падение", "0.10 = 10% — на сколько может просесть показатель"],
  low_confidence_cnt:["низкая уверенность, если срабатываний меньше", "такие строки в отчёте подсвечиваются бледно"],
  app_ids_limit:     ["сколько заявок сохранять в строке", "по этим заявкам можно провалиться к деталям"],
};

// Что делает каждая выданная проверка — одной фразой
const CHECK_DESCRIPTIONS = {
  check_ar_drop_by_product: "проверяет, что одобрение по продукту не просело после правки",
  check_attribute_new_loss: "находит атрибуты, которые появились или исчезли в новой версии",
  check_external_score_drift: "ловит сильный уход внешнего скора — это данные внешнего источника, а не наша логика",
  check_stop_factor_disappeared: "находит стоп-факторы, которые убрали из стратегии или которые перестали срабатывать",
  check_target_model_changed: "сигналит, если решение стало приниматься другой скоринговой моделью",
};

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

function paramsFields(file, params) {
  return Object.entries(params).map(([k, v]) => {
    const [label, hint] = PARAM_LABELS[k] || [k, ""];
    return `<div class="param-field">
      <label title="${esc(hint)}">${esc(label)}</label>
      <input class="chk-param" data-file="${esc(file)}" data-key="${esc(k)}" value="${esc(v)}" size="10">
    </div>`;
  }).join("");
}

async function renderRuns() {
  $view.innerHTML = "<p>Загрузка…</p>";
  let runs = [], datasets = [], builtins = [];
  try {
    [ { runs }, { datasets }, { checks: builtins } ] = await Promise.all([
      api("/api/runs"), api("/api/datasets"), api("/api/builtin/checks"),
    ]);
  } catch (e) { $view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; return; }

  const dsOpts = datasets.map(d => {
    const label = d.id.startsWith("demo://")
      ? "Демо-данные — 300 заявок, работает без файлов (для разработки)"
      : `Выданный датасет — ${d.apps} заявок (требуется модуль pipeline)`;
    return `<option value="${esc(d.id)}">${esc(label)}</option>`;
  }).join("");

  const checkOpts = builtins.map(b => `
    <div class="check-item">
      <label class="check-title">
        <input type="checkbox" class="chk" value="${esc(b.file)}" checked ${b.error ? "disabled" : ""}>
        ${esc(b.label)}
      </label>
      <div class="check-desc">${esc(CHECK_DESCRIPTIONS[b.file.replace(/^check_|\.py$/g, "")] || "")}</div>
      <div class="check-params">${paramsFields(b.file, b.params)}</div>
    </div>`).join("");

  $view.innerHTML = `
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Новый прогон</h3>
      <label>Название прогона — чтобы отличать в списке</label>
      <input id="run-name" value="прогон ${new Date().toLocaleString("ru")}" size="40">
      <label>Данные для сравнения</label>
      <select id="run-dataset">${dsOpts}</select>
      <label>Процедуры обработки — сейчас встроенные; свои можно писать во вкладке «Рабочая область»</label>
      <div style="display:flex;gap:6px">
        <select class="proc" title="нормализация ответов в базовый формат"><option>нормализация: встроенная</option></select>
        <select class="proc" title="сопоставление срезов"><option>сравнение: встроенное</option></select>
        <select class="proc" title="подготовка фактов для отчёта"><option>факты: встроенные</option></select>
      </div>
      <label>Автоматические проверки — каждая ищет свой класс проблем</label>
      <div>${checkOpts}</div>
      <label style="display:flex;gap:6px;align-items:center">
        <input type="checkbox" id="run-llm" checked>
        после прогона запросить «второе мнение» у языковой модели
      </label>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button onclick="createRun(false)">Запустить прогон</button>
        <button class="ghost" onclick="createRun(true)" title="сохранить настройки, чтобы запускать потом в один клик">Сохранить конфигурацию</button>
      </div>
      <p class="hint">Прогон идёт в фоне и не блокирует страницу: ход виден на странице прогона, можно закрыть вкладку.</p>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Ваши прогоны</h3>
      ${runs.length ? `<table><thead><tr><th>Название</th><th>Статус</th><th>Создан</th></tr></thead><tbody>
        ${runs.map(r => `<tr>
          <td><a href="#/runs/${r.id}">${esc(r.config.name)}</a></td>
          <td>${statusBadge(r.status)}</td>
          <td>${new Date(r.created * 1000).toLocaleString("ru")}</td></tr>`).join("")}
      </tbody></table>` : `<p class="hint">Прогонов пока нет — создайте первый слева и нажмите «Запустить прогон».</p>`}
    </div>
  </div>`;
}

function statusBadge(s) {
  const titles = { pending: "ожидает", running: "в работе", done: "готов", error: "ошибка" };
  return `<span class="badge ${s}">${titles[s] || s}</span>`;
}

async function createRun(saveOnly) {
  const checks = [...document.querySelectorAll(".chk")].filter(c => c.checked && !c.disabled)
    .map(c => {
      const params = {};
      document.querySelectorAll(`.chk-param[data-file="${CSS.escape(c.value)}"]`)
        .forEach(inp => {
          const raw = inp.value.trim();
          const num = Number(raw);
          params[inp.dataset.key] = raw !== "" && !isNaN(num) ? num : raw;
        });
      return { source: { kind: "builtin", name: c.value },
               params, enabled: true };
    });
  const cfg = {
    name: document.getElementById("run-name").value,
    dataset: document.getElementById("run-dataset").value,
    normalize: { kind: "builtin", name: "default" },
    compare: { kind: "builtin", name: "default" },
    facts: { kind: "builtin", name: "default" },
    checks, llm_opinion: document.getElementById("run-llm").checked,
  };
  try {
    if (saveOnly) { await api("/api/configs", { method: "POST", body: JSON.stringify(cfg) });
                    alert("Конфигурация сохранена — можно запускать повторно тем же составом."); }
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
        <p class="hint">Этапы обработки — по завершении появится ссылка на отчёт:</p>
        ${run.stages.map(s => `
          <div class="stage">
            <span class="st">${statusBadge(s.status)}</span>
            <b>${esc(s.title)}</b>
            <span class="msg">${esc(s.message || (s.status === "running" ? "выполняется…" : ""))}</span>
            ${s.code_ref ? `<button class="ghost" style="margin-left:auto" title="показать код, который исполняется на этом этапе" onclick='showCode(${JSON.stringify(s.title)}, ${JSON.stringify(s.code_snapshot || s.code_ref)})'>показать код этапа</button>` : ""}
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
      <div class="stat"><div class="num">${s.apps_with_diffs ?? 0}</div><div class="lbl">заявок с изменениями</div></div>
      <div class="stat"><div class="num">${s.apps_decision_changed ?? 0}</div><div class="lbl">сменили итоговое решение</div></div>
      <div class="stat"><div class="num" style="font-size:16px">${esc(fmtObj(s.decision_before))} → ${esc(fmtObj(s.decision_after))}</div><div class="lbl">решения: было → стало</div></div>
    </div>`;

  $view.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0">${esc(run.config.name)} ${statusBadge(run.status)}
        <span class="hint">· ${esc(run.config.dataset)}</span></h3>
      ${run.error ? `<pre class="code">${esc(run.error)}</pre>` : cards}
      <p class="hint">Этапы обработки:</p>
      ${run.stages.map(sg => `
        <div class="stage"><span class="st">${statusBadge(sg.status)}</span>
          <b>${esc(sg.title)}</b><span class="msg">${esc(sg.message)}</span>
          <button class="ghost" style="margin-left:auto" title="показать код, который исполнялся на этом этапе" onclick='showCode(${JSON.stringify(sg.title)}, ${JSON.stringify(sg.code_snapshot || sg.code_ref || "")})'>показать код этапа</button>
        </div>`).join("")}
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <a href="/api/runs/${id}/checks/export.html" target="_blank"><button class="ghost">открыть отчёт проверок в новой вкладке</button></a>
        <a href="/api/runs/${id}/checks/export.html" download="checks-${id}.html"><button class="ghost">скачать отчёт проверок (HTML)</button></a>
        <button class="ghost" onclick="navigator.clipboard.writeText(location.origin + '/#/runs/${id}');alert('Ссылка на сводный отчёт скопирована — можно отправить коллеге')">скопировать ссылку на сводный отчёт</button>
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

function showCode(title, code) { openModal("Код этапа: " + title, `<pre class="code">${esc(code)}</pre>`); }

// ---------- сводная таблица ----------

async function renderPivot(id) {
  const el = document.getElementById("tab-body");
  el.innerHTML = `
    <div class="card">
      <p class="hint">Сводная таблица отвечает на вопрос «где именно изменилось». Раскладку меняете вы:
      задайте, по чему группировать строки и колонки, — каждая ячейка покажет число заявок.</p>
      <div class="toolbar">
        <div><label>Группировать строки по<br>(измерения через запятую)</label>
          <input id="pv-rows" value="path,change_type" size="30" title="доступные: path, key, change_type, prefix, class, array_key, value_after, adjusted_after, diff_flag"></div>
        <div><label>Колонки таблицы</label>
          <select id="pv-cols"><option value="">— нет —</option><option value="change_type">тип изменения</option><option value="diff_flag">есть различие?</option><option value="key">имя атрибута</option><option value="prefix">префикс пути</option><option value="class">класс пути</option><option value="value_after">значение после</option></select></div>
        <div><label>Только изменения типа</label>
          <select id="pv-ct"><option value="">все типы</option><option value="VALUE_CHANGED">значение изменилось</option><option value="ATTRIBUTE_NEW">атрибут появился</option><option value="ATTRIBUTE_LOST">атрибут исчез</option><option value="SAME">без изменений</option></select></div>
        <div><label>Показывать</label>
          <select id="pv-diff"><option value="-1">все строки</option><option value="1">только с различиями</option><option value="0">только без изменений</option></select></div>
        <div><label>Только атрибуты с пути<br>(часть пути, например decisionResult.product_trials)</label><input id="pv-prefix" size="26"></div>
        <button onclick="loadPivot('${id}')">Применить раскладку</button>
      </div>
      <div id="pv-table" class="hint">Задайте раскладку и нажмите «Применить раскладку».</div>
      <p class="hint">Клик по ячейке — список заявок этой группы, оттуда клик по заявке — её атрибуты до и после.</p>
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
  if (!data.rows.length) { box.textContent = "Нет данных по такому фильтру — попробуйте смягчить условия."; return; }

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
        return cell ? `<td class="clickable" title="показать заявки этой группы" data-f='${esc(JSON.stringify(filterOf(cell)))}'>${cell.cnt_apps}</td>` : "<td>·</td>";
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
  const filterText = Object.entries(filters).filter(([, v]) => v !== "")
    .map(([k, v]) => `${k} = ${v}`).join(" · ") || "без фильтра";
  openModal(`Заявки группы (всего ${data.total})`, `
    <p class="hint">Условие группы: ${esc(filterText)}</p>
    ${data.app_ids.map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join("") || "<p>пусто</p>"}
    <div style="margin-top:10px">
      ${offset > 0 ? `<button class="ghost" onclick='showApps("${id}", ${JSON.stringify(filters)}, ${offset - 200})'>← предыдущие 200</button>` : ""}
      ${offset + 200 < data.total ? `<button class="ghost" onclick='showApps("${id}", ${JSON.stringify(filters)}, ${offset + 200})'>следующие 200 →</button>` : ""}
    </div>`);
}

async function showApp(id, appId) {
  const data = await api(`/api/runs/${id}/apps/${appId}`);
  const seg = data.segment || {};
  const rows = data.attributes.map(r => `<tr class="${r.DIFF_FLAG ? "fail" : ""}">
      <td>${esc(r.PATH)}${r.ARRAY_KEY ? `<br><span class="hint">[${esc(r.ARRAY_KEY)}]</span>` : ""}</td>
      <td>${esc(r.VALUE_BEFORE || "—")}</td><td>${esc(r.VALUE_AFTER || "—")}</td>
      <td>${esc(r.CHANGE_TYPE)}</td></tr>`).join("");
  openModal(`Заявка ${appId}: что изменилось`, `
    <p class="hint">Итоговое решение: ${esc(seg.DECISION_BEFORE || "?")} → ${esc(seg.DECISION_AFTER || "?")}
       · продукты: ${esc(seg.PRODUCTS || "—")} · модель: ${esc(seg.PD_MODEL_BEFORE || "—")} → ${esc(seg.PD_MODEL_AFTER || "—")}
       <br>Красным — изменившиеся атрибуты; «—» значит атрибута нет в этом срезе.</p>
    <table><thead><tr><th>Атрибут (путь)</th><th>Было (прод)</th><th>Стало (бета)</th><th>Что случилось</th></tr></thead>
    <tbody>${rows}</tbody></table>`);
}

// ---------- отчёт проверок ----------

async function renderChecks(id) {
  const el = document.getElementById("tab-body");
  const { rows } = await api(`/api/runs/${id}/checks`);
  if (!rows.length) { el.innerHTML = `<div class="card hint">Проверки не выполнялись — выберите их при создании прогона.</div>`; return; }
  // русские заголовки; английские ключи контракта — во всплывающей подсказке
  const cols = [["LABEL", "проверка"], ["SOURCE", "срез"], ["ARGUMENT", "что именно"],
                ["CNT", "сколько"], ["TOTAL", "из"], ["METRIC", "метрика"],
                ["RESULT", "итог"], ["CONFIDENCE", "уверенность"]];
  el.innerHTML = `<div class="card">
    <p class="hint">Все проверки одной таблицей. <b>FAIL</b> — проблема, требующая внимания;
    бледные строки — срабатываний мало, уверенности нет, это шум, а не поломка.
    Клик по числу в последней колонке — заявки, на которых сработала проверка.</p>
    <table><thead><tr>${cols.map(([k, ru]) => `<th title="${k}">${ru}</th>`).join("")}<th>заявки</th></tr></thead>
    <tbody>${rows.map(r => `
      <tr class="${r.RESULT === "FAIL" ? "fail" + (r.CONFIDENCE === "LOW" ? " low" : "") : ""}">
        ${cols.map(([k]) => `<td>${esc(fmtMetric(r[k]))}</td>`).join("")}
        <td class="clickable">${(r.APP_IDS || []).length ? esc(r.APP_IDS.length) + " шт." : "—"}</td>
      </tr>`).join("")}
    </tbody></table>
  </div>`;
  el.querySelectorAll("td.clickable").forEach((td, i) => td.onclick = () => {
    const ids = rows[i].APP_IDS || [];
    if (ids.length) openModal(`Заявки, на которых сработала проверка (${ids.length})`,
      ids.map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join(""));
  });
}

// ---------- мнение ИИ ----------

async function renderLlm(id, run) {
  const el = document.getElementById("tab-body");
  const llm = run.llm;
  el.innerHTML = `<div class="card" id="llm-box">
    <p class="hint">«Второе мнение»: языковая модель получает только сводку
    (сдвиги решений, топ изменившихся атрибутов, строки FAIL) и отвечает, что выглядит подозрительно.</p>
    ${llm && llm.status === "ok"
      ? `<b>Модель ${esc(llm.model)} отвечает:</b><p style="white-space:pre-wrap">${esc(llm.opinion)}</p>`
      : `<p class="hint">${esc(llm?.reason || "Мнение ещё не запрашивалось.")}</p>
         <button onclick="askLlm('${id}')">Запросить мнение ИИ</button>`}
  </div>`;
}

async function askLlm(id) {
  const box = document.getElementById("llm-box");
  box.innerHTML = "<p class='hint'>Модель изучает сводку…</p>";
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
      <h3 style="margin-top:0">Рабочая область — свои процедуры и проверки на Python</h3>
      <p class="hint">Код — обычный Python-файл по контракту: объект <code>CHECK = {"id", "label", "params", "entrypoint"}</code>,
      функция <code>run(mart, params)</code> возвращает строки отчёта. Через <code>mart</code> доступна витрина:
      <code>mart.rows(path=…, change_type=…)</code>, <code>mart.count(...)</code>, <code>mart.app_ids(...)</code>,
      <code>mart.segments()</code>, <code>mart.total_apps()</code>. Проверку можно сохранить, выбрать в конфигурации прогона
      или сразу применить к готовому прогону — строки попадут в его отчёт.</p>
      <div class="toolbar">
        <div><label>Имя файла (латиница, без пробелов)</label><input id="ws-name" list="ws-files" placeholder="my_check">
          <datalist id="ws-files">${files.map(f => `<option value="${esc(f.name)}">`).join("")}</datalist></div>
        <button onclick="loadWs()">Открыть</button>
        <button onclick="saveWs()">Сохранить</button>
        <button class="ghost" onclick="deleteWs()">Удалить</button>
      </div>
      <textarea id="ws-code" class="code" spellcheck="false" placeholder="# CHECK = {"id": ..., "label": ..., "params": ..., "entrypoint": run}&#10;# def run(mart, params): -> список строк отчёта&#10;# контракт: task/checks/README.md"></textarea>
      <div class="toolbar" style="margin-top:10px">
        <div><label>Применить к прогону — его ID<br>(из адресной строки: #/runs/&lt;id&gt;)</label>
          <input id="ws-run" placeholder="например 501d3f7970ab" size="16"></div>
        <button onclick="tryWs()">Запустить и добавить в отчёт прогона</button>
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
  alert("Файл «" + name + "» сохранён. Теперь его можно выбрать в конфигурации прогона.");
}

async function deleteWs() {
  const name = document.getElementById("ws-name").value.trim();
  if (name && confirm("Удалить файл «" + name + "»?")) {
    await api(`/api/workspace/${encodeURIComponent(name)}`, { method: "DELETE" });
    document.getElementById("ws-code").value = "";
  }
}

async function tryWs() {
  const runId = document.getElementById("ws-run").value.trim();
  const res = document.getElementById("ws-result");
  if (!runId) return alert("укажите ID прогона, на котором проверить");
  try {
    const { rows, check_id } = await api(`/api/runs/${runId}/checks/try`,
      { method: "POST", body: JSON.stringify({ kind: "code", code: document.getElementById("ws-code").value }) });
    await api(`/api/runs/${runId}/checks/add_result`,
      { method: "POST", body: JSON.stringify({ rows }) });
    res.innerHTML = `Проверка «${esc(check_id)}» отработала: ${rows.length} строк. ` +
      `Они уже <a href="#/runs/${runId}">в отчёте прогона</a>, вкладка «Отчёт проверок».`;
  } catch (e) { res.textContent = "Не получилось: " + e.message; }
}

// ---------- init ----------

const savedUser = localStorage.getItem("csd-user");
document.getElementById("username").value = savedUser || "user-" + Math.random().toString(36).slice(2, 6);
document.getElementById("username").addEventListener("change", e => {
  localStorage.setItem("csd-user", e.target.value.trim());
  route();
});
route();
