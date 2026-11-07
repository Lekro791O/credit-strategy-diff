/* Человекочитаемые подписи для путей атрибутов ответа СПР.
   Структура ответа фиксирована (см. docs/ФОРМАТ_ОТВЕТА.md, ГЛОССАРИЙ) —
   это курируемый словарь, без ИИ. Неизвестные сегменты читаемо
   разбиваются по заглавным буквам. Оригинальный путь остаётся в title. */

const SEG_RU = {
  appData: "данные заявки",
  application_id: "ID заявки",
  request_id: "ID запроса",
  createDateTime: "время расчёта",
  decisionResult: "решение по заявке",
  decision: "итоговое решение",
  available_offers: "предложенные офферы",
  rank: "позиция в выдаче",
  product_name: "название продукта",
  product_id: "ID продукта",
  product_trials: "прогон по продукту",
  partner_id: "ID партнёра",
  partner_name: "партнёр",
  partner_type: "тип партнёра",
  tariff: "тариф",
  routing_method: "маршрутизация",
  rules: "стоп-факторы",
  name: "название",
  description: "описание",
  hit: "сработало",
  rejectReason: "класс причины отказа",
  rejectReasonCode: "причина отказа",
  scoring: "расчёт моделей",
  scoreModels: "значения моделей",
  modelName: "модель",
  type: "тип",
  value: "значение",
  pd: "целевой скор",
  cutOff: "порог",
  epr: "ставка (ЭПР)",
  calculationType: "тип расчёта",
  features: "предикторы модели",
  age: "возраст",
  income: "доход",
  sumActiveDeals: "сумма активных сделок",
  cntApprovedContracts3y: "одобрений за 3 года",
  cntDelinq90d12m: "просрочки 90+ за 12 мес",
  monthsSinceLastLoan: "мес. с последнего займа",
  score_okb: "скор БКИ",
  score_equ: "скор Эквифакс",
  characteristics: "характеристики заявки",
  ext_data: "внешние источники",
  body: "ответ источника",
  action: "что запрашивали",
  params: "параметры",
  data: "полученное значение",
  ok: "успех",
  error: "ошибка",
  status: "HTTP-статус",
  id: "ID",
  product_catalog: "справочник продуктов",
  mfo_id: "ID МФО",
  is_new_mfo: "новый МФО",
  tariff_matrix: "матрица тарифов",
};

// точные пути, где общий перевод сегментов читается хуже
const PATH_RU = {
  "decisionResult.decision": "итоговое решение по заявке",
  "decisionResult.product_trials": "прогон по продукту",
  "decisionResult.product_trials.rules": "стоп-фактор продукта",
  "decisionResult.product_trials.rules.hit": "стоп-фактор сработал",
  "decisionResult.product_trials.scoring.pd": "целевой скор продукта",
  "decisionResult.product_trials.scoring.pd.value": "значение целевого скора",
  "decisionResult.product_trials.scoring.pd.modelName": "целевая модель",
  "decisionResult.product_trials.cutOff.value": "значение порога",
  "decisionResult.product_trials.cutOff.name": "порог (название)",
  "decisionResult.product_trials.decision": "решение по продукту",
  "decisionResult.product_trials.epr": "ставка продукта (ЭПР)",
  "decisionResult.scoring": "расчёт моделей",
  "decisionResult.scoring.features": "предикторы модели",
  "decisionResult.scoring.value": "результат модели (вектор)",
  "decisionResult.available_offers": "предложенные офферы",
  "ext_data.body.params.data": "значение от внешнего источника",
  "characteristics.score_okb": "скор БКИ по заявке",
  "characteristics.score_equ": "скор Эквифакс по заявке",
};

// неизвестный сегмент: decisionResult -> "decision result"
function humanizeSeg(seg) {
  return seg
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase();
}

function prettyPath(path) {
  if (!path) return "—";
  if (PATH_RU[path]) return PATH_RU[path];
  const parts = String(path).split(".");
  const last = parts[parts.length - 1];
  const head = parts.slice(0, -1).map(p => SEG_RU[p] || humanizeSeg(p));
  const tail = SEG_RU[last] || humanizeSeg(last);
  return head.length ? head.join(" → ") + " → " + tail : tail;
}

function prettyKey(key, path = "") {
  if (!key) return "—";
  if (SEG_RU[key]) return SEG_RU[key];
  if (String(path).includes("rules") && key === "name") return "стоп-фактор";
  return humanizeSeg(key);
}

/* значения ячеек: решения, булевы, срезы, уверенность, итоги проверок */
const VALUE_RU = {
  APPROVE: "одобрено", REJECT: "отклонено", VERIFICATION: "на проверке",
  TRUE: "да", FALSE: "нет", NULL: "пусто", NONE: "—",
  "BEFORE (MASTER)": "прод", "AFTER (BETA)": "бета", BEFORE: "прод", AFTER: "бета",
  HIGH: "высокая", LOW: "низкая", PASS: "в норме", FAIL: "проблема",
  HARDCHECK: "стоп-фактор", CUTOFF: "порог",
};
function valRu(v) {
  if (v === null || v === undefined || v === "") return "—";
  return VALUE_RU[v] ?? v;
}

/* названия выданных проверок — подчистка англицизмов */
const LABEL_RU = {
  "Approval Rate по продукту упал": "Одобрение по продукту упало",
};
function labelRu(l) { return LABEL_RU[l] ?? (l || "—"); }

/* аргументы проверок: стоп-факторы, модели, продукты */
const RULE_RU = {
  "AGELOW@21": "младше 21 года",
  "AGELOW@18": "младше 18 лет",
  "RESTRICTED_REGIONS": "запрещённый регион",
  "NOINCOME": "нет подтверждения дохода",
  "DOCS_EXPIRED": "просрочены документы",
};
const MODEL_RU = {
  ML_MICRO_LOAN_V1: "модель «микрозайм V1»",
  ML_MICRO_LOAN_V2: "модель «микрозайм V2»",
  ML_MICRO_LOAN_V3: "модель «микрозайм V3»",
};
function prettyArgument(v) {
  if (v === null || v === undefined || v === "") return "—";
  v = String(v);
  if (v.includes(" -> ")) return v.split(" -> ").map(prettyArgument).join(" → ");
  if (/\w+\.\w+/.test(v)) return prettyPath(v);          // путь атрибута
  if (RULE_RU[v]) return RULE_RU[v];                     // стоп-фактор
  if (MODEL_RU[v]) return MODEL_RU[v];                   // модель
  if (/^\d+$/.test(v)) return `продукт №${v}`;           // ID продукта
  return v;
}
