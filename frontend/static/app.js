/* Сравнение решений СПР — фронтенд (vanilla JS, без сборки, без внешних ресурсов).
   Маршруты: #/runs — прогоны, #/runs/<id> — ход/результаты, #/workspace — код. */
"use strict";

const $view = document.getElementById("view");
let pollTimer = null;
let LLM_CONFIGURED = false;
let DIMS_LIST = ["path", "key", "change_type", "prefix", "class", "suffix",
                 "array_key", "diff_flag", "source", "value", "bucket",
                 "value_before", "value_after", "adjusted_before", "adjusted_after"];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- базовые помощники ----------

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

function toast(msg, isErr = false) {
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast" + (isErr ? " err" : "");
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

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

function fmtDur(a, b) {
  if (!a || !b) return "";
  const s = b - a;
  return s < 1 ? `${Math.round(s * 1000)} мс` : `${s.toFixed(1)} с`;
}

// ---------- русские подписи (технический код — в title) ----------

const CHANGE_TYPE_RU = {
  SAME: "без изменений", VALUE_CHANGED: "значение изменено",
  ATTRIBUTE_NEW: "атрибут появился", ATTRIBUTE_LOST: "атрибут исчез",
  ADDED: "атрибут появился", REMOVED: "атрибут исчез",
};
const ctRu = v => CHANGE_TYPE_RU[v] || v;

const DIM_RU = {
  path: "путь атрибута", key: "имя атрибута", change_type: "тип изменения",
  prefix: "раздел", class: "блок", suffix: "подблок", array_key: "элемент массива",
  diff_flag: "есть различие", source: "срез", value: "значение на срезе",
  bucket: "бакет значения", value_adjusted: "бакет значения", value_before: "значение (прод)", value_after: "значение (бета)",
  adjusted_before: "бакет (прод)", adjusted_after: "бакет (бета)",
  decision_before: "решение (прод)", decision_after: "решение (бета)",
  decision_changed: "решение сменилось",
  product: "продукт", partner_id: "ID партнёра", partner_name: "партнёр",
  partner_type: "тип партнёра", routing_method: "маршрутизация",
  pd_model_before: "целевая модель (прод)", pd_model_after: "целевая модель (бета)",
  cutoff_before: "порог (прод)", cutoff_after: "порог (бета)",
  reject_reason_before: "причина отказа (прод)", reject_reason_after: "причина отказа (бета)",
  score_okb_before: "скор БКИ (прод)", score_okb_after: "скор БКИ (бета)",
};

const STAGE_TITLES = {
  normalize: "Нормализация ответов", compare: "Сопоставление срезов",
  facts: "Подготовка фактов counts", checks: "Автоматические проверки",
  llm: "Мнение языковой модели",
};
const STAGE_STATUS_RU = { pending: "ожидает", running: "в работе", done: "готов",
                          error: "ошибка", skipped: "пропущено" };
const PROC_STAGES = ["normalize", "compare", "facts"];  // проверки идут своим блоком

function statusBadge(s) {
  return `<span class="badge ${s}" title="${esc(s)}">${esc(STAGE_STATUS_RU[s] || s)}</span>`;
}

function decisionStamp(d) {
  const ic = {
    ok: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.2 8.6l3 3L12.8 5"/></svg>',
    bad: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>',
  };
  if (d === "APPROVE") return `<span class="verdict ok" title="APPROVE">${ic.ok}одобрено</span>`;
  if (d === "REJECT") return `<span class="verdict bad" title="REJECT">${ic.bad}отклонено</span>`;
  return `<span class="verdict neutral">${esc(d || "—")}</span>`;
}

function emptyState(title, sub) {
  return `<div class="empty">
    <svg viewBox="0 0 130 96" fill="none" aria-hidden="true">
      <ellipse cx="65" cy="82" rx="52" ry="9" fill="var(--pink-soft)"/>
      <rect x="30" y="18" width="70" height="52" rx="7" fill="var(--card)" stroke="var(--rose)" stroke-width="2"/>
      <path d="M40 32h34M40 42h50M40 52h28" stroke="var(--rose)" stroke-width="3" stroke-linecap="round"/>
      <circle cx="95" cy="24" r="13" fill="var(--pink-soft)" stroke="var(--pink)" stroke-width="2"/>
      <path d="M89.5 24l4 4 8-9" stroke="var(--pink)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <div class="t">${esc(title)}</div>
    <div>${esc(sub || "")}</div></div>`;
}

// ---------- декор: иконки ----------

const IC = (inner) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none"
  stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

const STAGE_ICONS = {
  normalize: IC('<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>'),
  compare: IC('<path d="M4 8h13l-3.5-3.5M20 16H7l3.5 3.5"/>'),
  facts: IC('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
  checks: IC('<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4.5"/>'),
  llm: IC('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/><path d="M19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9L19 16z"/>'),
};
const stageIcon = name => `<span class="ic" title="${esc(STAGE_TITLES[name] || name)}">${STAGE_ICONS[name] || ""}</span>`;

const CHECK_ICONS = {
  ar_drop_by_product: IC('<path d="M4 4v16h16"/><path d="M7 14l4-5 3 3 5-7"/><path d="M17 5h2v2"/>'),
  attribute_new_loss: IC('<circle cx="8" cy="12" r="5"/><path d="M8 9.5v5M5.5 12h5"/><path d="M14 7h7M17.5 3.5v7"/>'),
  external_score_drift: IC('<path d="M2 12c2.5-4 5-4 7.5 0s5 4 7.5 0 3.5-3 5-1.5"/><path d="M2 18c2.5-4 5-4 7.5 0s5 4 7.5 0"/>'),
  stop_factor_disappeared: IC('<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8l5-5z"/><path d="M9 9l6 6M15 9l-6 6"/>'),
  target_model_changed: IC('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor"/>'),
};
const checkIcon = file =>
  `<span class="ck-ic" title="проверка">${CHECK_ICONS[(file || "").replace(/^check_|\.py$/g, "")] || STAGE_ICONS.checks}</span>`;

// ---------- тема и горячие клавиши ----------

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("csd-theme", next);
}

let gPressed = false, gTimer = null;
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeModal(); return; }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (typing) return;
  if (e.key === "/") {
    const q = document.getElementById("quick-search");
    if (q) { e.preventDefault(); q.focus(); }
  } else if (e.key === "g") {
    gPressed = true; clearTimeout(gTimer); gTimer = setTimeout(() => gPressed = false, 800);
  } else if (e.key === "r" && gPressed) {
    location.hash = "#/runs";
  }
});

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

// ---------- подписи проверок и параметров ----------

const PARAM_LABELS = {
  min_share: ["минимальная доля заявок",
    "Доля, не проценты: 0.001 = 0.1%. Срабатывания реже этой доли игнорируются (считаются шумом)."],
  max_drop_rel: ["максимально допустимое падение",
    "Относительное падение, не процентные пункты: 0.1 = 10% от базового значения. Пример: AR 32% → 21% — падение ≈ 34% (0.34) — это проблема при пороге 0.1."],
  low_confidence_cnt: ["низкая уверенность, если срабатываний меньше",
    "Строки с меньшим числом срабатываний подсвечиваются бледно: сигнал есть, уверенности нет."],
  app_ids_limit: ["сколько заявок сохранять в строке",
    "По сохранённым заявкам можно провалиться к деталям из отчёта."],
};

const CHECK_DESCRIPTIONS = {
  ar_drop_by_product: "проверяет, что доля одобрений по продукту не просела после правки",
  attribute_new_loss: "находит атрибуты, которые появились или исчезли в новой версии",
  external_score_drift: "ловит сильный уход внешнего скора — это данные внешнего источника, а не наша логика",
  stop_factor_disappeared: "находит стоп-факторы, которые убрали из стратегии или которые перестали срабатывать",
  target_model_changed: "сигналит, если решение стало приниматься другой скоринговой моделью",
};

function paramsFields(file, params) {
  return Object.entries(params).map(([k, v]) => {
    const [label, hint] = PARAM_LABELS[k] || [k, ""];
    return `<div class="param-field">
      <label title="${esc(hint)}">${esc(label)}</label>
      <input class="chk-param" data-file="${esc(file)}" data-key="${esc(k)}" value="${esc(v)}" size="10">
    </div>`;
  }).join("");
}

// ---------- форма прогона ----------

// состояние формы (заполняется при загрузке конфигурации)
const formState = {
  stage_order: ["normalize", "compare", "facts", "checks"],
  procs: {  // источник процедуры по этапу
    normalize: { kind: "builtin", name: "default" },
    compare: { kind: "builtin", name: "default" },
    facts: { kind: "builtin", name: "default" },
  },
  normalization: {},
  checks: null, // null = дефолт (все встроенные)
};

function procPanelHtml() {
  const items = formState.stage_order.filter(s => PROC_STAGES.includes(s));
  return items.map(stage => {
    const p = formState.procs[stage] || { kind: "builtin", name: "" };
    return `<div class="proc-item" data-stage="${stage}">
      <span class="proc-move">
        <button class="ghost icon" title="раньше" onclick="moveProc('${stage}',-1)">↑</button>
        <button class="ghost icon" title="позже" onclick="moveProc('${stage}',1)">↓</button>
      </span>
      <b class="proc-title">${esc(STAGE_TITLES[stage])}</b>
      <select class="proc-src" data-stage="${stage}" onchange="procSrcChanged('${stage}')">
        <option value="builtin" ${p.kind === "builtin" ? "selected" : ""}>встроенная</option>
        <option value="workspace" ${p.kind === "workspace" ? "selected" : ""}>рабочая область</option>
        <option value="file" ${p.kind === "file" ? "selected" : ""}>файл .py</option>
      </select>
      <input class="proc-name" data-stage="${stage}" placeholder="${p.kind === "file" ? "путь к .py" : "имя файла без .py"}"
        value="${p.kind === "builtin" ? "" : esc(p.name)}" ${p.kind === "builtin" ? 'style="display:none"' : ""}>
    </div>`;
  }).join("");
}

function moveProc(stage, dir) {
  const order = formState.stage_order.filter(s => PROC_STAGES.includes(s));
  const i = order.indexOf(stage), j = i + dir;
  if (j < 0 || j >= order.length) return;
  order[i] = order[j]; order[j] = stage;
  formState.stage_order = [...order, "checks"];
  document.getElementById("proc-list").innerHTML = procPanelHtml();
}

function procSrcChanged(stage) {
  const sel = document.querySelector(`.proc-src[data-stage="${stage}"]`);
  const inp = document.querySelector(`.proc-name[data-stage="${stage}"]`);
  inp.style.display = sel.value === "builtin" ? "none" : "";
  formState.procs[stage] = { kind: sel.value, name: inp.value.trim() };
}

function normSettingsHtml() {
  const n = formState.normalization || {};
  const exclude = (n.exclude || ["appData.request_id", "appData.createDateTime"]).join("\n");
  const rules = n.array_rules || [];
  const bucket = n.bucket || { preset: "none", rules: [] };
  const rulesRows = rules.map((r, i) => arrayRuleRow(i, r)).join("");
  const bucketRows = (bucket.rules || []).map((r, i) => bucketRuleRow(i, r)).join("");
  return `<details class="norm-settings">
    <summary>Настройки нормализации — исключения, массивы, бакеты (контракт pipeline.Config)</summary>
    <label>Технические поля — не участвуют в сравнении (путь с каждой строки)</label>
    <textarea id="norm-exclude" rows="2" class="code" style="min-height:52px">${esc(exclude)}</textarea>
    <label>Режимы массивов: по какому пути как сопоставлять элементы</label>
    <div id="norm-rules">${rulesRows}</div>
    <button class="ghost" onclick="addArrayRule()">+ добавить правило массива</button>
    <label>Бакетизация значений для группировки в отчёте</label>
    <select id="norm-bucket-preset">
      <option value="none" ${bucket.preset === "none" ? "selected" : ""}>не бакетизировать</option>
      <option value="example" ${bucket.preset === "example" ? "selected" : ""}>пресет «как в примере задания»</option>
      <option value="custom" ${(bucket.rules || []).length ? "selected" : ""}>свои границы</option>
    </select>
    <div id="norm-bucket-rules">${bucketRows}</div>
    <button class="ghost" onclick="addBucketRule()">+ добавить границы бакетов</button>
    <p class="hint">Правила массива: режим «по полю» — элемент опознаётся значением ключей (через запятую);
    «по значению» — массив скаляров; «по индексу» — вектор модели, позиция значима.
    Границы бакетов: числа через запятую, интервалы вида (нижняя - верхняя] строятся автоматически.</p>
  </details>`;
}

function arrayRuleRow(i, r) {
  r = r || {};
  return `<div class="norm-rule" data-i="${i}">
    <input class="nr-path" placeholder="путь массива, напр. decisionResult.product_trials" value="${esc(r.path || "")}" size="42">
    <select class="nr-mode">
      <option value="field" ${r.mode === "field" ? "selected" : ""}>по полю</option>
      <option value="value" ${r.mode === "value" ? "selected" : ""}>по значению</option>
      <option value="index" ${r.mode === "index" ? "selected" : ""}>по индексу</option>
    </select>
    <input class="nr-keys" placeholder="ключи через запятую" value="${esc((r.keys || []).join(","))}" size="24">
    <button class="ghost icon" title="удалить" onclick="this.parentElement.remove()">✕</button>
  </div>`;
}

function addArrayRule() {
  const box = document.getElementById("norm-rules");
  box.insertAdjacentHTML("beforeend", arrayRuleRow(box.children.length, {}));
}

function bucketRuleRow(i, r) {
  r = r || {};
  return `<div class="norm-bucket" data-i="${i}">
    <input class="br-path" placeholder="префикс пути, напр. decisionResult.available_offers" value="${esc(r.path_prefix || "")}" size="36">
    <input class="br-edges" placeholder="границы через запятую: 50000,100000,250000" value="${esc((r.edges || []).join(","))}" size="30">
    <button class="ghost icon" title="удалить" onclick="this.parentElement.remove()">✕</button>
  </div>`;
}

function addBucketRule() {
  const box = document.getElementById("norm-bucket-rules");
  box.insertAdjacentHTML("beforeend", bucketRuleRow(box.children.length, {}));
}

function collectNormalization() {
  const exclude = document.getElementById("norm-exclude").value.split("\n")
    .map(s => s.trim()).filter(Boolean);
  const array_rules = [...document.querySelectorAll("#norm-rules .norm-rule")].map(row => ({
    path: row.querySelector(".nr-path").value.trim(),
    mode: row.querySelector(".nr-mode").value,
    keys: row.querySelector(".nr-keys").value.split(",").map(s => s.trim()).filter(Boolean),
  })).filter(r => r.path);
  const preset = document.getElementById("norm-bucket-preset").value;
  const brules = [...document.querySelectorAll("#norm-bucket-rules .norm-bucket")].map(row => ({
    path_prefix: row.querySelector(".br-path").value.trim(),
    edges: row.querySelector(".br-edges").value.split(",").map(s => Number(s.trim())).filter(x => !isNaN(x)),
  })).filter(r => r.path_prefix && r.edges.length);
  const norm = {};
  if (exclude.length) norm.exclude = exclude;
  if (array_rules.length) norm.array_rules = array_rules;
  if (preset !== "none" || brules.length) norm.bucket = { preset, rules: brules };
  return norm;
}

document.addEventListener("click", async event => {
  if (event.target.id !== "giga-save") return;
  const result = document.getElementById("giga-result");
  try {
    await api("/api/llm/gigachat", {method:"POST", body:JSON.stringify({key:document.getElementById("giga-key").value, scope:document.getElementById("giga-scope").value, model:document.getElementById("giga-model").value})});
    document.getElementById("giga-key").value = "";
    document.getElementById("run-llm").checked = true;
    result.textContent = "Настройки сохранены. Доступ проверится при запросе мнения.";
  } catch (e) { result.textContent = "Не удалось сохранить: " + e.message; }
});

async function renderRuns() {
  $view.innerHTML = "<p>Загрузка…</p>";
  let runs = [], datasets = [], builtins = [], configs = [];
  try {
    [ { runs }, { datasets }, { checks: builtins } ] = await Promise.all([
      api("/api/runs"), api("/api/datasets"), api("/api/builtin/checks"),
    ]);
    ({ configured: LLM_CONFIGURED } = await api("/api/llm/config"));
    ({ configs } = await api("/api/configs"));
  } catch (e) { $view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; return; }

  const dsOpts = datasets.map(d => {
    const label = d.id.startsWith("demo://")
      ? "Демо-данные — 300 заявок, работает без файлов (для разработки)"
      : `Выданный датасет — ${d.apps} заявок`;
    return `<option value="${esc(d.id)}">${esc(label)}</option>`;
  }).join("");

  const checkOpts = builtins.map(b => `
    <div class="check-item">
      ${checkIcon(b.file)}
      <div class="ck-body">
        <label class="check-title">
          <input type="checkbox" class="chk" value="${esc(b.file)}" checked ${b.error ? "disabled" : ""}>
          ${esc(b.label)}
        </label>
        <div class="check-desc">${esc(CHECK_DESCRIPTIONS[(b.file || "").replace(/^check_|\.py$/g, "")] || "")}</div>
        <div class="check-src">
          <select class="chk-src" data-file="${esc(b.file)}" onchange="chkSrcChanged('${esc(b.file)}')">
            <option value="builtin">встроенная</option>
            <option value="workspace">рабочая область</option>
            <option value="file">файл .py</option>
          </select>
          <input class="chk-name" data-file="${esc(b.file)}" placeholder="имя / путь" style="display:none" size="18">
        </div>
        <div class="check-params">${paramsFields(b.file, b.params)}</div>
      </div>
    </div>`).join("");

  const llmHint = LLM_CONFIGURED
    ? "модель настроена — после прогона появится независимый разбор изменений"
    : "модель не настроена (нет CSD_LLM_API_KEY) — шаг будет пропущен";

  $view.innerHTML = `
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Новый прогон</h3>
      <label>Название прогона — чтобы отличать в списке</label>
      <input id="run-name" value="прогон ${new Date().toLocaleString("ru")}" size="40">
      <label>Данные для сравнения</label>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select id="run-dataset" style="flex:1;min-width:220px">${dsOpts}</select>
        <button class="ghost" id="ds-upload-btn" title="Загрузить свой датасет zip-архивом: внутри папки BEFORE/AFTER или RESULTS/BEFORE_MASTER/AFTER_BETA с JSON-ответами">+ добавить файл</button>
        <input type="file" id="ds-file" accept=".zip" style="display:none">
      </div>
      <p class="hint" id="ds-upload-hint" style="display:none"></p>
      <label>Последовательность процедур — порядок этапов обработки</label>
      <div id="proc-list">${procPanelHtml()}</div>
      ${normSettingsHtml()}
      <label>Автоматические проверки — каждая ищет свой класс проблем</label>
      <div>${checkOpts}</div>
      <label style="display:flex;gap:6px;align-items:center">
        <input type="checkbox" id="run-llm" ${LLM_CONFIGURED ? "checked" : ""}>
        после прогона запросить «второе мнение» у языковой модели
      </label>
      <div class="hint" style="margin:2px 0 0 24px">${esc(llmHint)}</div>
      <details style="margin-top:10px"><summary>Подключить GigaChat</summary>
        <p class="hint">Ключ сохраняется локально на этом компьютере и не отображается повторно. В модель отправляется сводка результатов и проверки.</p>
        <label>Ключ авторизации</label><input id="giga-key" type="password" autocomplete="off" placeholder="Вставьте ключ авторизации">
        <label>Тип доступа</label><select id="giga-scope"><option value="GIGACHAT_API_PERS">Личный</option><option value="GIGACHAT_API_B2B">Бизнес</option><option value="GIGACHAT_API_CORP">Корпоративный</option></select>
        <label>Модель</label><input id="giga-model" value="GigaChat-2">
        <button type="button" id="giga-save">Сохранить настройки</button><span id="giga-result" class="hint"></span>
      </details>
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <button onclick="createRun(false)">Запустить прогон</button>
        <button class="ghost" onclick="createRun(true)" title="сохранить настройки, чтобы запускать потом в один клик">Сохранить конфигурацию</button>
      </div>
      <p class="hint">Прогон идёт в фоне и не блокирует страницу: ход виден на странице прогона, можно закрыть вкладку.</p>
    </div>
    <div>
      <div class="card">
        <h3 style="margin-top:0">Сохранённые конфигурации</h3>
        ${configs.length ? `<div class="twrap"><table><thead><tr><th>Название</th><th>действия</th></tr></thead><tbody>
          ${configs.map(c => `<tr>
            <td>${esc(c.name)}</td>
            <td style="white-space:nowrap">
              <a data-cb="loadConfigIntoForm:${cbArg(c)}" title="подставить настройки в форму слева">загрузить</a> ·
              <a data-cb="runConfig:${cbArg(c)}" title="запустить сразу">запустить</a> ·
              <a data-cb="deleteConfig:${cbArg(c.name)}" title="удалить">удалить</a>
            </td></tr>`).join("")}
        </tbody></table></div>` : emptyState("Нет сохранённых конфигураций", "Настройте прогон слева и нажмите «Сохранить конфигурацию».")}
      </div>
      <div class="card">
        <h3 style="margin-top:0">Ваши прогоны</h3>
        ${runs.length ? `<div class="twrap"><table><thead><tr><th>Название</th><th>Статус</th><th>Создан</th></tr></thead><tbody>
          ${runs.map(r => `<tr>
            <td><a href="#/runs/${r.id}">${esc(r.config.name)}</a></td>
            <td>${statusBadge(r.status)}</td>
            <td>${new Date(r.created * 1000).toLocaleString("ru")}</td></tr>`).join("")}
        </tbody></table></div>` : emptyState("Пока нет прогонов", "Создайте первый слева и нажмите «Запустить прогон».")}
        ${runs.length >= 2 ? `
        <div class="toolbar" style="margin-top:12px">
          <div><label>Сравнить прогоны</label>
            <select id="cmp-a">${runs.map(r => `<option value="${r.id}">${esc(r.config.name)}</option>`).join("")}</select>
            <select id="cmp-b">${runs.map((r, i) => `<option value="${r.id}" ${i === 1 ? "selected" : ""}>${esc(r.config.name)}</option>`).join("")}</select>
          </div>
          <button class="ghost" onclick="compareRuns()">сравнить</button>
        </div>` : ""}
      </div>
    </div>
  </div>`;
  // загрузка датасета файлом
  const fileInput = document.getElementById("ds-file");
  document.getElementById("ds-upload-btn").onclick = () => fileInput.click();
  fileInput.onchange = () => uploadDataset(fileInput);
}

async function uploadDataset(input) {
  const file = input.files[0];
  if (!file) return;
  const hint = document.getElementById("ds-upload-hint");
  hint.style.display = "";
  const setHint = t => { hint.textContent = t; };
  setHint(`Загружаю «${file.name}»…`);
  try {
    const form = new FormData();
    form.append("file", file);
    form.append("name", file.name.replace(/\.zip$/i, ""));
    const resp = await fetch("/api/datasets/upload", {
      method: "POST", headers: { "X-User-Id": user() }, body: form,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail?.message || data.detail || "ошибка сервера");
    setHint(`Датасет добавлен: ${data.apps} заявок (после — ${data.after_apps}). Выбран в списке.`);
    toast(`Датасет загружен: ${data.apps} заявок`);
    const sel = document.getElementById("run-dataset");
    const opt = document.createElement("option");
    opt.value = data.id;
    opt.textContent = `Загруженный файл — ${data.apps} заявок`;
    opt.selected = true;
    sel.appendChild(opt);
  } catch (e) {
    setHint("Не получилось: " + e.message);
    toast("Ошибка загрузки датасета", true);
  } finally {
    input.value = "";
  }
}

function chkSrcChanged(file) {
  const sel = document.querySelector(`.chk-src[data-file="${CSS.escape(file)}"]`);
  const inp = document.querySelector(`.chk-name[data-file="${CSS.escape(file)}"]`);
  inp.style.display = sel.value === "builtin" ? "none" : "";
}

function loadConfigIntoForm(cfg) {
  formState.stage_order = cfg.stage_order || formState.stage_order;
  formState.procs = {
    normalize: cfg.normalize || { kind: "builtin", name: "default" },
    compare: cfg.compare || { kind: "builtin", name: "default" },
    facts: cfg.facts || { kind: "builtin", name: "default" },
  };
  formState.normalization = cfg.normalization || {};
  document.getElementById("run-name").value = cfg.name || "";
  document.getElementById("run-dataset").value = cfg.dataset || "demo://synthetic";
  document.getElementById("proc-list").innerHTML = procPanelHtml();
  const det = document.querySelector(".norm-settings");
  if (det) { det.outerHTML = normSettingsHtml(); }
  if (Array.isArray(cfg.checks)) {
    const wanted = new Map(cfg.checks.map(c => [c.source?.kind === "builtin" ? c.source.name : "", c]));
    document.querySelectorAll(".chk").forEach(chk => {
      const spec = wanted.get(chk.value);
      chk.checked = spec ? spec.enabled !== false : false;
    });
    cfg.checks.forEach(c => {
      if (c.source?.kind === "builtin") {
        document.querySelectorAll(`.chk-param[data-file="${CSS.escape(c.source.name)}"]`)
          .forEach(inp => { if (c.params && inp.dataset.key in c.params) inp.value = c.params[inp.dataset.key]; });
      }
    });
  }
  toast("Конфигурация «" + cfg.name + "» загружена в форму");
}

async function deleteConfig(name) {
  await api(`/api/configs/${encodeURIComponent(name)}`, { method: "DELETE" });
  toast("Конфигурация «" + name + "» удалена");
  renderRuns();
}

async function runConfig(cfg) {
  try {
    const { run } = await api("/api/runs", { method: "POST", body: JSON.stringify({ ...cfg, name: cfg.name + " (запуск)" }) });
    location.hash = `#/runs/${run.id}`;
  } catch (e) { toast("Ошибка: " + e.message, true); }
}

async function compareRuns() {
  const a = document.getElementById("cmp-a").value;
  const b = document.getElementById("cmp-b").value;
  if (a === b) return toast("Выберите два разных прогона", true);
  const [ra, rb] = await Promise.all([api(`/api/runs/${a}`), api(`/api/runs/${b}`)]);
  const s1 = ra.run.summary || {}, s2 = rb.run.summary || {};
  const fails = r => (r.run.check_rows || []).filter(x => x.RESULT === "FAIL").length;
  const f1 = fails(ra), f2 = fails(rb);
  const rows = [
    ["заявок в прогоне", s1.total_apps, s2.total_apps],
    ["с изменениями", s1.apps_with_diffs, s2.apps_with_diffs, true],
    ["смена решения", s1.apps_decision_changed, s2.apps_decision_changed, true],
    ["проверок FAIL", f1, f2, false, true],
  ];
  const delta = (x, y, lessIsBetter) => {
    const d = (y ?? 0) - (x ?? 0);
    const good = lessIsBetter ? d < 0 : d > 0;
    const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "—";
    return `<span style="color:${d === 0 ? "var(--muted)" : good ? "var(--ok)" : "var(--bad)"}">${arrow} ${d > 0 ? "+" : ""}${d}</span>`;
  };
  openModal("Сравнение прогонов", `
    <div class="twrap"><table><thead><tr><th>метрика</th><th>${esc(ra.run.config.name)}</th><th>${esc(rb.run.config.name)}</th><th>изменение</th></tr></thead>
    <tbody>${rows.map(([m, v1, v2, moreBetter, lessBetter]) => `
      <tr><td>${esc(m)}</td><td>${esc(v1 ?? "—")}</td><td>${esc(v2 ?? "—")}</td>
      <td>${delta(v1, v2, lessBetter && !moreBetter)}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="hint">▲▼ — изменение второго прогона относительно первого; зелёным — в лучшую сторону, красным — в худшую.</p>`);
}

async function createRun(saveOnly, presetConfig) {
  let cfg;
  if (presetConfig) {
    cfg = { ...presetConfig, name: (presetConfig.name || "прогон") + " (повтор)" };
  } else {
    const checks = [...document.querySelectorAll(".chk")].filter(c => c.checked && !c.disabled)
      .map(c => {
        const params = {};
        document.querySelectorAll(`.chk-param[data-file="${CSS.escape(c.value)}"]`)
          .forEach(inp => {
            const raw = inp.value.trim();
            const num = Number(raw);
            params[inp.dataset.key] = raw !== "" && !isNaN(num) ? num : raw;
          });
        const srcSel = document.querySelector(`.chk-src[data-file="${CSS.escape(c.value)}"]`);
        const nameInp = document.querySelector(`.chk-name[data-file="${CSS.escape(c.value)}"]`);
        const kind = srcSel ? srcSel.value : "builtin";
        return { source: { kind, name: kind === "builtin" ? c.value : (nameInp?.value.trim() || "") },
                 params, enabled: true };
      });
    cfg = {
      name: document.getElementById("run-name").value,
      dataset: document.getElementById("run-dataset").value,
      stage_order: [...formState.stage_order.filter(s => PROC_STAGES.includes(s)),
                    ...(formState.stage_order.includes("checks") ? ["checks"] : []),
                    ...(document.getElementById("run-llm").checked ? ["llm"] : [])],
      normalize: { ...formState.procs.normalize },
      compare: { ...formState.procs.compare },
      facts: { ...formState.procs.facts },
      normalization: collectNormalization(),
      checks, llm_opinion: document.getElementById("run-llm").checked,
    };
    // синхронизируем formState с тем, что реально в инпутах
    document.querySelectorAll(".proc-src").forEach(sel => {
      const inp = document.querySelector(`.proc-name[data-stage="${sel.dataset.stage}"]`);
      cfg.procs && 0;
      formState.procs[sel.dataset.stage] =
        { kind: sel.value, name: sel.value === "builtin" ? "default" : (inp?.value.trim() || "") };
    });
    cfg.normalize = { ...formState.procs.normalize };
    cfg.compare = { ...formState.procs.compare };
    cfg.facts = { ...formState.procs.facts };
  }
  try {
    if (saveOnly) { await api("/api/configs", { method: "POST", body: JSON.stringify(cfg) });
                    toast("Конфигурация сохранена"); }
    else { const { run } = await api("/api/runs", { method: "POST", body: JSON.stringify(cfg) });
           location.hash = `#/runs/${run.id}`; }
  } catch (e) { toast("Ошибка: " + e.message, true); }
}

// ---------- анимация счётчиков ----------

function animateNum(el, target) {
  if (reducedMotion || !el) { if (el) el.textContent = target; return; }
  const t0 = performance.now(), dur = 600;
  const step = t => {
    const k = Math.min(1, (t - t0) / dur);
    el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- прогон: ход и результаты ----------

let visibleRunStages = [];
document.addEventListener("click", event => {
  const button = event.target.closest("[data-stage-code]");
  if (!button) return;
  const stage = visibleRunStages[Number(button.dataset.stageCode)];
  if (stage) showCode(stage.title, stage.code_snapshot || stage.code_ref || "");
});

// безопасная передача аргументов в клики: никаких строк кода в onclick —
// только индексы реестра (строки из данных с кавычками/переводами строк
// больше не ломают кнопки)
const cbReg = [];
const cbArg = v => { cbReg.push(v); return cbReg.length - 1; };
document.addEventListener("click", event => {
  const el = event.target.closest("[data-cb]");
  if (!el) return;
  const [fn, ...idxs] = el.dataset.cb.split(":");
  const args = idxs.map(i => cbReg[+i]);
  if (typeof window[fn] === "function") window[fn](...args);
});

async function renderRun(id, tab = "pivot") {
  let run;
  try { ({ run } = await api(`/api/runs/${id}`)); }
  catch (e) { $view.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; return; }

  visibleRunStages = run.stages || [];
  if (run.status === "running" || run.status === "pending") {
    // процент готовности: вес этапа = 1, внутри «compare» есть точный прогресс
    const stages = run.stages;
    let units = 0, curFrac = 0, curName = "";
    for (const s of stages) {
      if (s.status === "done" || s.status === "skipped") { units += 1; continue; }
      if (s.status === "running") {
        curName = s.title;
        if (s.total) curFrac = Math.min(1, (s.completed || 0) / s.total);
      }
    }
    const pct = Math.min(99, Math.round((units + curFrac) / stages.length * 100));
    $view.innerHTML = `
      <div class="card run-wrap">
        <div class="run-bg"><div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div></div>
        <div class="run-inner">
        <h3 style="margin-top:0">${esc(run.config.name)} ${statusBadge(run.status)}</h3>
        <div class="ouro-wrap">
          <svg class="ouro" viewBox="0 0 100 100" aria-hidden="true">
            <defs>
              <linearGradient id="ouro-grad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#2563eb"/><stop offset="100%" stop-color="#7c3aed"/>
              </linearGradient>
            </defs>
            <circle class="track" cx="50" cy="50" r="42" pathLength="100" fill="none" stroke-width="7"/>
            <circle class="snake" cx="50" cy="50" r="42" pathLength="100" fill="none" stroke-width="7" stroke-linecap="round"/>
          </svg>
          <div class="ouro-info">
            <div class="pct">${pct}%</div>
            <div class="what">вычисления идут — сейчас: ${esc(curName || "подготовка")}. Можно закрыть страницу: прогон продолжится, результат сохранится.</div>
          </div>
        </div>
        <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
        <p class="hint">Этапы обработки (${units} из ${stages.length}):</p>
        ${stages.map((s, index) => `
          <div class="stage">
            ${stageIcon(s.name)}
            <span class="st">${statusBadge(s.status)}</span>
            <b>${esc(s.title)}</b>
            <span class="msg">${esc(s.message || (s.status === "running" ? "выполняется…" : ""))}</span>
            <span class="hint">${esc(fmtDur(s.started, s.finished))}</span>
            ${s.code_ref ? `<button class="ghost" style="margin-left:auto" title="показать код, который исполняется на этом этапе" data-stage-code="${index}">показать код этапа</button>` : ""}
          </div>`).join("")}
        </div>
      </div>`;
    pollTimer = setTimeout(() => renderRun(id), 1500);
    return;
  }

  const s = run.summary || {};
  const share = s.total_apps ? (s.apps_with_diffs || 0) / s.total_apps : 0;
  const doneStages = run.stages.filter(x => x.status === "done" || x.status === "skipped").length;
  const cards = `
    <div class="cards">
      <div class="stat"><div class="num" data-n="${s.total_apps ?? 0}">0</div><div class="lbl">заявок в прогоне</div></div>
      <div class="stat"><div class="num" data-n="${s.apps_with_diffs ?? 0}">0</div><div class="lbl">заявок с изменениями</div></div>
      <div class="stat"><div class="num" data-n="${s.apps_decision_changed ?? 0}">0</div><div class="lbl">сменили итоговое решение</div></div>
    </div>
    ${share >= 0.95 ? `<div class="warn">Изменились почти все заявки (${(share * 100).toFixed(0)}%). Проверьте, что нормализация не помечает расхождения формата как изменения.</div>` : ""}
    <div class="hint">Загружено: ${run.quality?.total_apps ?? s.total_apps ?? 0} · допущено: ${run.quality?.eligible_apps ?? s.total_apps ?? 0} · исключено: ${run.quality?.excluded_apps ?? 0}</div>${(run.diagnostics || []).length ? `<details><summary>Причины исключения</summary>${run.diagnostics.map(d => `<div>${esc(d.APP_ID)} · ${esc(d.SOURCE)} · ${esc(d.MESSAGE)}</div>`).join("")}</details>` : ""}<div id="brief-slot"></div>
    <div id="matrix-slot"></div>
    <div id="split-slot"></div>`;

  $view.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0">${esc(run.config.name)} ${statusBadge(run.status)}
        <span class="hint">· ${esc(run.config.dataset)}</span></h3>
      ${run.error ? `<pre class="code">${esc(run.error)}</pre>` : cards}
      <div class="toolbar" id="seg-bar" style="margin-top:6px">
        <div><label>Фильтры разрезов заявки — применяются и к сводной, и к спискам</label>
          <div class="chips" id="seg-chips"></div></div>
      </div>
      <p class="hint">Этапы обработки (${doneStages} из ${run.stages.length}):</p>
      ${run.stages.map((sg, index) => `
        <div class="stage">${stageIcon(sg.name)}<span class="st">${statusBadge(sg.status)}</span>
          <b>${esc(sg.title)}</b><span class="msg">${esc(sg.message)}</span>
          <span class="hint">${esc(fmtDur(sg.started, sg.finished))}</span>
          ${sg.code_ref ? `<button class="ghost" style="margin-left:auto" title="показать код, который исполнялся на этом этапе" data-stage-code="${index}">показать код этапа</button>` : ""}
        </div>${sg.error ? `<div class="warn" style="margin:2px 0 6px 34px">${esc(sg.error)}</div>` : ""}`).join("")}
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:end">
        <a href="/api/runs/${id}/checks/export.html" target="_blank"><button class="ghost">открыть отчёт проверок в новой вкладке</button></a>
        <a href="/api/runs/${id}/checks/export.html" download="checks-${id}.html"><button class="ghost">скачать отчёт проверок (HTML)</button></a>
        <a href="/api/runs/${id}/mart.csv" download="mart-${id}.csv"><button class="ghost" title="витрина сравнения по контракту docs/СХЕМА_ВИТРИНЫ.md — одна строка = атрибут заявки, до/после, признак различия">скачать витрину сравнения (CSV)</button></a>
        <button class="ghost" onclick="navigator.clipboard.writeText(location.origin + '/#/runs/${id}').then(()=>toast('Ссылка на сводный отчёт скопирована'))">скопировать ссылку на сводный отчёт</button>
        <button class="ghost" data-cb="createRun:${cbArg(false)}:${cbArg(run.config)}" title="запустить ещё раз с тем же набором проверок и параметров">повторить прогон</button>
        <div style="margin-left:auto"><label style="margin:0">найти заявку по ID — Enter (<kbd>/</kbd>)</label>
          <input id="quick-search" placeholder="I32554354" onkeydown="if(event.key==='Enter')quickFind('${id}')"></div>
      </div>
    </div>
    <section class="tabs">
      <button class="${tab === "pivot" ? "active" : ""}" onclick="renderRun('${id}','pivot')">Сводная таблица</button>
      <button class="${tab === "checks" ? "active" : ""}" onclick="renderRun('${id}','checks')">Отчёт проверок</button>
      <button class="${tab === "llm" ? "active" : ""}" onclick="renderRun('${id}','llm')">Мнение ИИ</button>
    </section>
    <div id="tab-body">Загрузка…</div>`;

  document.querySelectorAll(".stat .num[data-n]").forEach(el => animateNum(el, +el.dataset.n));

  if (run.status === "done") { renderBrief(id, run); renderMatrix(id); renderSegFilters(id); renderSourceSplit(id); }
  if (tab === "pivot") renderPivot(id);
  else if (tab === "checks") renderChecks(id, run);
  else renderLlm(id, run);
}

function fmtObj(o) {
  if (!o) return "—";
  return Object.entries(o).map(([k, v]) => `${k}: ${v}`).join(", ");
}

function showCode(title, code) { openModal("Код этапа: " + title, `<pre class="code">${esc(code)}</pre>`); }

async function quickFind(runId) {
  const appId = document.getElementById("quick-search").value.trim();
  if (!appId) return;
  try { await showApp(runId, appId); }
  catch (e) { toast("Заявка не найдена: " + appId, true); }
}

// ---------- фильтры разрезов ----------

let segFilters = {};      // активные фильтры {product: ..., ...}
let segOptions = {};      // допустимые значения

async function renderSegFilters(id) {
  const box = document.getElementById("seg-chips");
  if (!box) return;
  try { segOptions = await api(`/api/runs/${id}/segment_values`); } catch (_) { segOptions = {}; }
  const defs = [["product", "продукт"], ["partner", "партнёр"], ["routing", "маршрутизация"],
                ["pd_model", "целевая модель"], ["cutoff", "порог"]];
  box.innerHTML = defs.map(([k, ru]) => {
    const opts = segOptions[k] || [];
    if (!opts.length) return "";
    return `<select class="seg-filter" data-k="${k}" title="фильтр: ${ru}">
      <option value="">${ru}: все</option>
      ${opts.map(v => `<option ${segFilters[k] === v ? "selected" : ""}>${esc(v)}</option>`).join("")}
    </select>`;
  }).join("") + (Object.keys(segFilters).length
    ? `<button class="ghost icon" title="сбросить фильтры" onclick="clearSegFilters('${id}')">сбросить</button>`
    : "");
  box.querySelectorAll(".seg-filter").forEach(sel => sel.onchange = () => {
    if (sel.value) segFilters[sel.dataset.k] = sel.value;
    else delete segFilters[sel.dataset.k];
    renderPivot(id); // пересчёт сводной с новыми фильтрами
  });
}

function clearSegFilters(id) {
  segFilters = {};
  renderSegFilters(id).then(() => renderPivot(id));
}

function segParams() {
  return new URLSearchParams(Object.entries(segFilters)).toString();
}

// ---------- «главное за 10 секунд» ----------

async function renderBrief(id, run) {
  const slot = document.getElementById("brief-slot");
  if (!slot) return;
  const s = run.summary || {};
  const total = s.total_apps || 0;
  if (!total) return;
  const lines = [];
  const ch = s.apps_decision_changed || 0;
  lines.push(`Решение изменилось у <b>${ch}</b> из ${total} заявок (${(ch / total * 100).toFixed(0)}%).`);
  const apprB = (s.decision_before || {}).APPROVE || 0;
  const apprA = (s.decision_after || {}).APPROVE || 0;
  const d = apprA - apprB;
  if (d !== 0) lines.push(`Одобрений стало ${d > 0 ? "больше" : "меньше"} на <b>${Math.abs(d)}</b> (${apprB} → ${apprA}).`);
  try {
    const qs = segParams();
    const top = await api(`/api/runs/${id}/facts?dims=path&diff_flag=1&limit=3${qs ? "&" + qs : ""}`);
    if (top.rows.length)
      lines.push(`Топ-${top.rows.length} изменившихся атрибутов: <b>${top.rows.map(r => esc(r.dims.path)).join("</b>, <b>")}</b>.`);
  } catch (_) {}
  const fails = (run.check_rows || []).filter(r => r.RESULT === "FAIL");
  if (fails.length) {
    const worst = fails.reduce((a, b) => (b.CNT || 0) > (a.CNT || 0) ? b : a);
    lines.push(`<span title="FAIL">Проблем</span> по ${fails.length} проверкам; серьёзнее всего: <b>${esc(worst.LABEL)}</b>${worst.ARGUMENT ? ` («${esc(worst.ARGUMENT)}»)` : ""} — ${worst.CNT} заявок.`);
  } else {
    lines.push(`Проверки не выявили проблем — все <span title="PASS">в норме</span>.`);
  }
  const text = lines.map(l => l.replace(/<[^>]+>/g, "")).join("\n");
  slot.innerHTML = `<div class="card brief">
    <h3 style="margin-top:0">Главное за 10 секунд</h3>
    <ul>${lines.map(l => `<li>${l}</li>`).join("")}</ul>
    <button class="ghost" id="brief-copy">скопировать текст</button>
  </div>`;
  document.getElementById("brief-copy").onclick = () =>
    navigator.clipboard.writeText(text).then(() => toast("Скопировано"));
}

// ---------- матрица переходов ----------

async function renderMatrix(id) {
  const slot = document.getElementById("matrix-slot");
  if (!slot) return;
  let data;
  try { data = await api(`/api/runs/${id}/decision_matrix`); }
  catch (_) { return; }
  const cell = (b, a) => {
    const c = data.cells[`${b}→${a}`];
    const cls = b === a ? "stable" : (b === "APPROVE" && a === "REJECT") ? "flip-bad" : "";
    const pct = data.total ? ((c ? c.cnt : 0) / data.total * 100).toFixed(1) : "0.0";
    return `<div class="mx-cell ${cls}" style="grid-column:${a === "APPROVE" ? 3 : 4};grid-row:${b === "APPROVE" ? 3 : 4}" ${c && c.cnt ? `data-cb="showAppIds:${cbArg(id)}:${cbArg(c.app_ids)}:${cbArg(`Решение: ${b} → ${a}`)}"` : ""} title="показать заявки">
      <div class="n">${c ? c.cnt : 0}</div><div class="p">${pct}%</div>
      <div>${decisionStamp(a)}</div></div>`;
  };
  slot.innerHTML = `<div class="card"><h3 style="margin-top:0">Куда переехали решения</h3>
    <div class="matrix">
      <div class="mx-axis-top">СТАЛО</div>
      <div class="mx-axis-side">БЫЛО</div>
      <div style="grid-column:2;grid-row:2"></div>
      <div class="mx-head" style="grid-column:3;grid-row:2">${decisionStamp("APPROVE")}</div>
      <div class="mx-head" style="grid-column:4;grid-row:2">${decisionStamp("REJECT")}</div>
      <div class="mx-rowhead" style="grid-column:2;grid-row:3">${decisionStamp("APPROVE")}</div>
      ${cell("APPROVE", "APPROVE")}${cell("APPROVE", "REJECT")}
      <div class="mx-rowhead" style="grid-column:2;grid-row:4">${decisionStamp("REJECT")}</div>
      ${cell("REJECT", "APPROVE")}${cell("REJECT", "REJECT")}
    </div>
    <p class="hint">Клик по ячейке — заявки этой группы. Диагональ — решения не изменились; красная ячейка — одобренные стали отклонёнными (самый опасный случай).</p>
  </div>`;
}

async function renderSourceSplit(id) {
  const slot = document.getElementById("split-slot");
  if (!slot) return;
  let data;
  try { data = await api(`/api/runs/${id}/source_split`); }
  catch (_) { return; }
  const total = data.logic.cnt + data.external_only.cnt + data.both.cnt;
  if (!total) { slot.innerHTML = ""; return; }
  const card = (key, title, hint, cls) => `
    <div class="split-cell ${cls}" ${data[key].cnt ? `data-cb="showAppIds:${cbArg(id)}:${cbArg(data[key].app_ids)}:${cbArg(title)}"` : ""} title="показать заявки">
      <div class="n">${data[key].cnt}</div>
      <div class="t">${title}</div>
      <div class="p">${(data[key].cnt / total * 100).toFixed(1)}% заявок · клик — список</div>
      <div class="p">изменено атрибутов: ${data[key].attrs}</div>
      <div class="hint">${hint}</div>
    </div>`;
  slot.innerHTML = `<div class="card"><h3 style="margin-top:0">Что изменилось: логика стратегии или входные данные</h3>
    <div class="split-grid">
      ${card("logic", "только поля результата", "изменились поля результата; причина требует проверки", "logic")}
      ${card("both", "и входные, и результат", "внешние данные уехали и за собой потянули результат: цепочка скор → модель → решение", "both")}
    </div>
    <p class="hint">Считается по заявкам с различиями (${total}). Отдельной категории «только входные поля» нет:
    изменение внешнего скора почти всегда тянет за собой цепочку до решения, такие заявки попадают в «и то, и другое».
    Категории показывают совпадение изменений, а не их причину: ext_data и characteristics — внешние источники, остальное — логика.</p>
  </div>`;
}

function showAppIds(id, ids, title) {
  openModal(title + ` (показано: ${ids.length}, не более 500)`,
    ids.slice(0, 500).map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join("") || "<p>пусто</p>");
}

// ---------- сводная таблица ----------

let pivotRowDims = ["path", "change_type"];

async function renderPivot(id) {
  const el = document.getElementById("tab-body");
  try { ({ dims: DIMS_LIST } = await api("/api/facts/dims")); } catch (_) {}
  el.innerHTML = `
    <div class="card">
      <p class="hint">В ячейках — число уникальных заявок. Нажмите на число, чтобы открыть список.</p>
      <div class="toolbar">
        <div><label>Группировать строки по (порядок чипов = порядок строк)</label>
          <div class="chips" id="pv-dim-chips"></div></div>
        <div><label>Колонки таблицы</label>
          <select id="pv-cols"><option value="">— нет —</option><option value="change_type">тип изменения</option><option value="diff_flag">есть различие?</option><option value="key">имя атрибута</option><option value="prefix">префикс пути</option><option value="class">класс пути</option><option value="suffix">хвост пути</option><option value="source">срез</option><option value="bucket">бакет</option><option value="value_after">значение после</option></select></div>
        <div><label>Только изменения типа</label>
          <select id="pv-ct"><option value="">все типы</option><option value="VALUE_CHANGED">значение изменилось</option><option value="ATTRIBUTE_NEW">атрибут появился</option><option value="ATTRIBUTE_LOST">атрибут исчез</option><option value="SAME">без изменений</option></select></div>
        <div><label>Показывать</label>
          <select id="pv-diff"><option value="-1">все строки</option><option value="1" selected>только с различиями</option><option value="0">только без изменений</option></select></div>
        <div><label>Только атрибуты с пути</label><input id="pv-prefix" size="24" placeholder="decisionResult.product_trials"></div>
        <button onclick="loadPivot('${id}')">Применить раскладку</button>
        <button class="ghost" id="pv-csv" style="display:none" onclick="exportPivotCsv()">скачать сводную (CSV)</button>
      </div>
      <div id="pv-table" class="hint"></div>
      <p class="hint">Клик по заголовку колонки — сортировка. Клик по ячейке — список заявок этой группы,
      оттуда клик по заявке — её атрибуты до и после.</p>
    </div>`;
  renderDimChips();
  await loadPivot(id);  // автоприменение раскладки по умолчанию
}

function renderDimChips() {
  const box = document.getElementById("pv-dim-chips");
  if (!box) return;
  box.innerHTML = DIMS_LIST.map(d =>
    `<span class="chip ${pivotRowDims.includes(d) ? "" : "off"}" data-dim="${esc(d)}"
       title="${esc(d)}">${pivotRowDims.includes(d) ? (pivotRowDims.indexOf(d) + 1) + ". " : ""}${esc(DIM_RU[d] || d)}</span>`).join("");
  box.querySelectorAll(".chip").forEach(ch => ch.onclick = () => {
    const d = ch.dataset.dim;
    if (pivotRowDims.includes(d)) {
      if (pivotRowDims.length > 1) pivotRowDims = pivotRowDims.filter(x => x !== d);
    } else pivotRowDims.push(d);
    renderDimChips();
  });
}

let lastPivot = null; // для CSV-экспорта

async function loadPivot(id) {
  const box = document.getElementById("pv-table");
  const colDim = document.getElementById("pv-cols").value;
  if (colDim && pivotRowDims.includes(colDim)) { box.textContent = "Выберите для колонок другое измерение: оно уже используется в строках."; document.getElementById("pv-csv").style.display = "none"; lastPivot = null; return; }
  const params = new URLSearchParams({ dims: pivotRowDims.join(",") + (colDim ? "," + colDim : "") });
  const ct = document.getElementById("pv-ct").value;
  if (ct) params.set("change_type", ct);
  const df = document.getElementById("pv-diff").value;
  if (df !== "-1") params.set("diff_flag", df);
  const pf = document.getElementById("pv-prefix").value.trim();
  if (pf) params.set("path_prefix", pf);
  for (const [k, v] of Object.entries(segFilters)) params.set(k, v);
  box.innerHTML = `<div class="skeleton" style="width:60%"></div><div class="skeleton" style="width:80%"></div>`;
  let data;
  try { data = await api(`/api/runs/${id}/pivot`, { method: "POST", body: JSON.stringify({dimensions: [...new Set([...pivotRowDims, ...(colDim ? [colDim] : [])])], column_dimension: colDim || null, filters: Object.fromEntries([...params.entries()].filter(([k]) => k !== "dims"))}) }); }
  catch (e) { box.textContent = "Ошибка: " + e.message; return; }
  if (!data.rows.length) { lastPivot = null; document.getElementById("pv-csv").style.display = "none";
    box.innerHTML = emptyState("Нет данных по такому фильтру", "Смягчите условия, смените раскладку или сбросьте фильтры разрезов."); return; }

  const rowDims = [...pivotRowDims];
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
  const maxVal = Math.max(...data.rows.map(r => r.cnt_apps), 1);
  const filterOf = cell => {
    const f = { ...segFilters };
    if (ct) f.change_type = ct;
    if (df !== "-1") f.diff_flag = df;
    if (pf) f.path_prefix = pf;
    Object.assign(f, cell.dims);
    return f;
  };
  const rowTotalMap = new Map((data.row_totals || []).map(r => [rowDims.map(d => r.dims[d] ?? "").join("‖"), r.cnt_apps]));
  const colTotals = Object.fromEntries((data.column_totals || []).map(r => [r.dims[colDim], r.cnt_apps]));
  if (!colDim) colTotals["заявки"] = data.totals.cnt_apps;
  const rowTotals = [...groups.keys()].map(k => rowTotalMap.get(k) ?? 0);
  const grand = data.totals.cnt_apps;
  const heat = v => {
    // идея из прототипа организаторов: малые значения — мягкая подсветка,
    // остальное — сдержанная шкала насыщенности
    if (v > 0 && v < 10) return "background: var(--soft-hl);";
    const a = 0.03 + 0.22 * (v / maxVal);
    return `background: rgba(37, 99, 235, ${a.toFixed(3)});`;
  };
  const colTitle = c => !colDim ? "Итого" : colDim === "change_type" ? ctRu(c) : c;
  lastPivot = { rowDims, colDim, cols, groups: [...groups.values()], colTotals, rowTotals, grand };

  let sortState = null;
  const renderTable = () => {
    const keys = [...groups.keys()];
    if (sortState) {
      const [colIdx, dir] = sortState;
      keys.sort((x, y) => {
        const gx = groups.get(x), gy = groups.get(y);
        const vx = colIdx < 0 ? rowTotalMap.get(x) ?? 0 : gx.cells[cols[colIdx]]?.cnt_apps || 0;
        const vy = colIdx < 0 ? rowTotalMap.get(y) ?? 0 : gy.cells[cols[colIdx]]?.cnt_apps || 0;
        return (vx - vy) * dir;
      });
    }
    let html = `<div class="twrap"><table class="pivot"><thead><tr><th>${rowDims.map(d => esc(DIM_RU[d] || d)).join("</th><th>")}</th>` +
      cols.map((c, i) => `<th class="sortable" data-ci="${i}" title="сортировать по колонке">${esc(colTitle(c))}</th>`).join("") + `</tr></thead><tbody>`;
    keys.forEach((rk, ri) => {
      const g = groups.get(rk);
      html += `<tr>${g.dims.map((d, di) => {
        const dimName = rowDims[di];
        const pretty = dimName === "path" ? prettyPath(d)
          : dimName === "key" ? prettyKey(d)
          : dimName === "prefix" || dimName === "class" ? (SEG_RU[d] || humanizeSeg(d))
          : d;
        return `<td title="${dimName}: ${esc(d)}">${esc(pretty)}</td>`;
      }).join("")}` +
        cols.map(c => {
          const cell = g.cells[c];
          if (!cell) return "<td>·</td>";
          return `<td class="clickable" title="показать заявки этой группы" style="${heat(cell.cnt_apps)}" data-f='${esc(JSON.stringify(filterOf(cell)))}'>${cell.cnt_apps}</td>`;
        }).join("") + `</tr>`;
    });
    html += `<tr class="totals"><td colspan="${rowDims.length}">итого уникальных заявок</td>${cols.map(c => `<td>${colTotals[c]}</td>`).join("")}</tr>`;
    box.innerHTML = (data.rows.length >= data.limit ? `<p class="warn">Показаны первые ${data.limit} групп. Уточните фильтры; CSV содержит показанные группы.</p>` : "") + html + "</tbody></table></div>";
    box.querySelectorAll("td.clickable").forEach(td => td.onclick = () => showApps(id, JSON.parse(td.dataset.f)));
    box.querySelectorAll("th.sortable").forEach(th => th.onclick = () => {
      const ci = +th.dataset.ci;
      sortState = sortState && sortState[0] === ci ? [ci, -sortState[1]] : [ci, -1];
      renderTable();
    });
  };
  renderTable();
  document.getElementById("pv-csv").style.display = "";
}

function exportPivotCsv() {
  if (!lastPivot) return;
  const { rowDims, colDim, cols, groups, colTotals, rowTotals, grand } = lastPivot;
  const head = [...rowDims.map(d => DIM_RU[d] || d),
                ...cols.map(c => !colDim ? "Итого" : colDim === "change_type" ? ctRu(c) : c)];
  const lines = [head.join(";")];
  groups.forEach((g, i) => {
    lines.push([...g.dims, ...cols.map(c => g.cells[c]?.cnt_apps || 0)].join(";"));
  });
  lines.push(["итого уникальных заявок", ...Array(Math.max(0, rowDims.length - 1)).fill(""), ...cols.map(c => colTotals[c])].join(";"));
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "pivot.csv";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Сводная таблица сохранена в CSV");
}

async function showApps(id, filters, offset = 0) {
  const params = new URLSearchParams({ offset });
  for (const [k, v] of Object.entries(filters)) if (v !== "") params.set(k, v);
  const data = await api(`/api/runs/${id}/apps?${params}`);
  const filterText = Object.entries(filters).filter(([, v]) => v !== "")
    .map(([k, v]) => `${DIM_RU[k] || k} = ${k === "change_type" ? ctRu(v) : v}`).join(" · ") || "без фильтра";
  openModal(`Заявки группы (всего ${data.total})`, `
    <p class="hint">Условие группы: ${esc(filterText)}</p>
    ${data.app_ids.map(a => `<a style="display:inline-block;margin:2px 8px" onclick="showApp('${id}','${esc(a)}')">${esc(a)}</a>`).join("") || "<p>пусто</p>"}
    <div style="margin-top:10px">
      ${offset > 0 ? `<button class="ghost" data-cb="showApps:${cbArg(id)}:${cbArg(filters)}:${cbArg(offset - 200)}">← предыдущие 200</button>` : ""}
      ${offset + 200 < data.total ? `<button class="ghost" data-cb="showApps:${cbArg(id)}:${cbArg(filters)}:${cbArg(offset + 200)}">следующие 200 →</button>` : ""}
    </div>`);
}

async function showApp(id, appId) {
  const data = await api(`/api/runs/${id}/apps/${appId}`);
  const seg = data.segment || {};
  const rows = data.attributes.map(r => `<tr class="${r.DIFF_FLAG ? "fail" : ""}">
      <td title="${esc(r.PATH)}"><b>${esc(prettyKey(r.KEY, r.PATH))}</b><br><span class="hint">${esc(prettyPath(r.PATH))}${r.ARRAY_KEY ? ` · [${esc(r.ARRAY_KEY)}]` : ""}</span></td>
      <td>${esc(r.VALUE_BEFORE || "—")}</td><td>${esc(r.VALUE_AFTER || "—")}</td>
      <td title="${esc(r.CHANGE_TYPE)}">${esc(ctRu(r.CHANGE_TYPE))}</td></tr>`).join("");
  openModal(`Заявка ${appId}: что изменилось`, `
    <p class="hint">Итоговое решение: ${decisionStamp(seg.DECISION_BEFORE)} → ${decisionStamp(seg.DECISION_AFTER)}
       · продукты: ${esc(seg.PRODUCTS || "—")} · модель: ${esc(seg.PD_MODEL_BEFORE || "—")} → ${esc(seg.PD_MODEL_AFTER || "—")}
       <br>Красным — изменившиеся атрибуты; «—» значит атрибута нет в этом срезе. Технический путь — при наведении на название атрибута.</p>
    <div class="twrap"><table><thead><tr><th>Атрибут</th><th>Было (прод)</th><th>Стало (бета)</th><th>Что случилось</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`);
}

// ---------- отчёт проверок ----------

const checksState = { filter: "all" };   // all | fail | pass | low

async function renderChecks(id, run) {
  const el = document.getElementById("tab-body");
  const rows = run.check_rows || [];
  const diags = run.errors || [];
  if (!rows.length && !diags.length) {
    el.innerHTML = `<div class="card">${emptyState("Проверки не выполнялись", "Выберите проверки при создании прогона.")}</div>`;
    return;
  }
  const nPass = rows.filter(r => r.RESULT === "PASS").length;
  const nFail = rows.filter(r => r.RESULT === "FAIL").length;
  const nLow = rows.filter(r => r.CONFIDENCE === "LOW").length;
  const sev = r => (r.RESULT === "FAIL" ? (r.CONFIDENCE === "LOW" ? 1 : 0) : 2);
  let view = rows.map((r, i) => ({ r, i }));
  if (checksState.filter === "fail") view = view.filter(x => x.r.RESULT === "FAIL");
  if (checksState.filter === "pass") view = view.filter(x => x.r.RESULT === "PASS");
  if (checksState.filter === "low") view = view.filter(x => x.r.CONFIDENCE === "LOW");
  view.sort((a, b) => sev(a.r) - sev(b.r) || (b.r.CNT || 0) - (a.r.CNT || 0));

  const cols = [["LABEL", "проверка"], ["SOURCE", "срез"], ["ARGUMENT", "что именно"],
                ["CNT", "сколько"], ["TOTAL", "из"], ["METRIC", "метрика"],
                ["RESULT", "итог"], ["CONFIDENCE", "уверенность"]];
  const cnt = (cls, f, label, title) =>
    `<span class="counter ${cls} ${checksState.filter === f ? "on" : ""}" data-cb="setChecksFilter:${cbArg(f)}" title="${title}">${label}</span>`;
  el.innerHTML = `<div class="card">
    <p class="hint">Все проверки одной таблицей. «Проблема» — проверка не пройдена; бледные строки — срабатываний мало,
    это сигнал для проверки, а не подтверждённая поломка. Клик по числу в последней колонке — заявки, на которых сработала проверка.</p>
    <div class="counters">
      ${cnt("pass", "all", `все: ${rows.length}`, "показать всё")}
      ${cnt("fail", "fail", `проблем: ${nFail}`, "только непройденные проверки")}
      ${cnt("pass", "pass", `в норме: ${nPass}`, "только пройденные проверки")}
      ${cnt("low", "low", `низкая уверенность: ${nLow}`, "только строки с низкой уверенностью — срабатываний мало, это шум, а не поломка")}
    </div>
    ${diags.length ? `<div class="warn">Проверки с ошибками исполнения: ${diags.length}.
      ${diags.map(d => `<br>• <b>${esc(d.check)}</b> (${esc(d.status || "ERROR")}): <code>${esc(JSON.stringify(d.error))}</code>`).join("")}
      <br>Проверьте контракт CHECK и код процедуры.</div>` : ""}
    ${view.length ? `<div class="twrap"><table><thead><tr>${cols.map(([k, ru]) => `<th title="${k}">${ru}</th>`).join("")}<th>заявки</th></tr></thead>
    <tbody>${view.map(({ r }) => `
      <tr class="${r.RESULT === "FAIL" ? "fail" + (r.CONFIDENCE === "LOW" ? " low" : "") : ""}">
        ${cols.map(([k]) => {
          if (k === "RESULT") return `<td title="${esc(r[k])}">${r[k] === "FAIL" ? "проблема" : "в норме"}</td>`;
          if (k === "CONFIDENCE") return `<td title="${esc(r[k])}">${r[k] === "LOW" ? "низкая" : "высокая"}</td>`;
          if (k === "SOURCE") return `<td title="${esc(r[k])}">${r[k] === "AFTER" ? "бета" : "прод"}</td>`;
          return `<td>${esc(fmtMetric(r[k]))}</td>`;
        }).join("")}
        <td class="clickable">${(r.APP_IDS || []).length ? esc(r.APP_IDS.length) + " шт." : "—"}</td>
      </tr>`).join("")}
    </tbody></table></div>` : emptyState("Нет строк по текущим фильтрам", "Снимите фильтры счётчиков выше.")}
  </div>`;
  el.querySelectorAll("td.clickable").forEach((td, j) => td.onclick = () => {
    const ids = view[j].r.APP_IDS || [];
    if (ids.length) showAppIds(id, ids, "Заявки, на которых сработала проверка");
  });
}

function setChecksFilter(f) { checksState.filter = f; refreshChecksTab(); }
async function refreshChecksTab() {
  const m = location.hash.match(/^#\/runs\/(\w+)/);
  if (m) { const { run } = await api(`/api/runs/${m[1]}`); renderChecks(m[1], run); }
}

// ---------- мнение ИИ ----------

async function renderLlm(id, run) {
  const el = document.getElementById("tab-body");
  const llm = run.llm;
  if (llm && llm.status === "ok") {
    el.innerHTML = `<div class="card">
      <p class="hint">«Второе мнение»: модель получила только сводку (сдвиги решений, топ изменившихся атрибутов, строки FAIL).</p>
      <b>Модель ${esc(llm.model)} отвечает:</b><p style="white-space:pre-wrap">${esc(llm.opinion)}</p></div>`;
    return;
  }
  el.innerHTML = `<div class="card">${emptyState("Мнение ИИ недоступно",
    "Чтобы включить, задайте переменные окружения CSD_LLM_BASE_URL, CSD_LLM_API_KEY, CSD_LLM_MODEL и перезапустите сервер (подробности — в README). Остальные функции работают без модели.")}
    <div style="text-align:center"><button onclick="askLlm('${id}')">повторить попытку</button></div></div>`;
}

async function askLlm(id) {
  const el = document.getElementById("tab-body");
  el.innerHTML = `<div class="card"><div class="skeleton" style="width:80%"></div><div class="skeleton" style="width:60%"></div></div>`;
  try {
    const { llm } = await api(`/api/runs/${id}/llm`, { method: "POST" });
    renderLlm(id, { llm });
  } catch (e) { el.innerHTML = `<div class="card">Ошибка: ${esc(e.message)}</div>`; }
}

// ---------- рабочая область ----------

async function renderWorkspace() {
  const el = $view;
  el.innerHTML = "<p>Загрузка…</p>";
  const { files } = await api("/api/workspace");
  el.innerHTML = `
    <div class="card">
      <h3 style="margin-top:0">Рабочая область — свои процедуры и проверки на Python</h3>
      <p class="hint">Проверка — файл по контракту: объект <code>CHECK = {"id", "label", "params", "entrypoint"}</code>,
      функция <code>run(mart, params)</code> возвращает строки отчёта. Через <code>mart</code> доступна витрина:
      <code>mart.rows(path=…, change_type=…)</code>, <code>mart.count(...)</code>, <code>mart.app_ids(...)</code>,
      <code>mart.segments()</code>, <code>mart.total_apps()</code>.<br>
      Процедура этапа — файл с функцией <code>normalize</code> / <code>compare</code> / <code>facts</code>
      (или общей <code>run</code>): её можно выбрать в форме прогона вместо встроенной.
      Сохранённые файлы можно выбрать в конфигурации прогона или применить к готовому прогону.</p>
      <div class="toolbar">
        <div><label>Имя файла (латиница, без пробелов)</label><input id="ws-name" list="ws-files" placeholder="my_check">
          <datalist id="ws-files">${files.map(f => `<option value="${esc(f.name)}">`).join("")}</datalist></div>
        <button onclick="loadWs()">Открыть</button>
        <button onclick="saveWs()">Сохранить</button>
        <button class="ghost" onclick="deleteWs()">Удалить</button>
      </div>
      <textarea id="ws-code" class="code" spellcheck="false" placeholder="# CHECK = {'id': ..., 'label': ..., 'params': ..., 'entrypoint': run}&#10;# def run(mart, params): -> список строк отчёта&#10;# контракт: task/checks/README.md"></textarea>
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
  if (!name) return toast("Введите имя файла", true);
  await api(`/api/workspace/${encodeURIComponent(name)}`,
    { method: "PUT", body: JSON.stringify({ code: document.getElementById("ws-code").value }) });
  toast("Файл «" + name + "» сохранён");
}

async function deleteWs() {
  const name = document.getElementById("ws-name").value.trim();
  if (name && confirm("Удалить файл «" + name + "»?")) {
    await api(`/api/workspace/${encodeURIComponent(name)}`, { method: "DELETE" });
    document.getElementById("ws-code").value = "";
    toast("Удалено");
  }
}

async function tryWs() {
  const runId = document.getElementById("ws-run").value.trim();
  const res = document.getElementById("ws-result");
  if (!runId) return toast("Укажите ID прогона", true);
  try {
    const { rows, check_id } = await api(`/api/runs/${runId}/checks/try`,
      { method: "POST", body: JSON.stringify({ kind: "code", code: document.getElementById("ws-code").value }) });
    await api(`/api/runs/${runId}/checks/add_result`,
      { method: "POST", body: JSON.stringify({ rows }) });
    res.innerHTML = `Проверка «${esc(check_id)}» отработала: ${rows.length} строк. ` +
      `Они уже <a href="#/runs/${runId}">в отчёте прогона</a>, вкладка «Отчёт проверок».`;
    toast("Проверка применена к прогону");
  } catch (e) { res.textContent = "Не получилось: " + e.message; toast("Ошибка проверки", true); }
}

// ---------- init ----------

const savedUser = localStorage.getItem("csd-user");
document.getElementById("username").value = savedUser || "user-" + Math.random().toString(36).slice(2, 6);
document.getElementById("username").addEventListener("change", e => {
  localStorage.setItem("csd-user", e.target.value.trim());
  route();
});
route();
