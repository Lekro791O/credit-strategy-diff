"""Тесты API: сквозной прогон на демо-данных, проверки, факты, экспорт.

Запуск из корня репозитория:
    python -m pytest tests/ -q
"""
import json
import os
import tempfile

os.environ["CSD_DATA_DIR"] = tempfile.mkdtemp(prefix="csd-test-")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"X-User-Id": "tester"}


def _wait_done(run_id: str, limit: int = 100) -> dict:
    for _ in range(limit):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] in ("done", "error"):
            return run
        import time
        time.sleep(0.1)
    raise AssertionError("прогон не завершился")


def _make_run() -> dict:
    r = client.post("/api/runs",
                    json={"name": "t", "dataset": "demo://synthetic",
                          "llm_opinion": True},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["run"]


def test_builtin_checks_listed():
    data = client.get("/api/builtin/checks").json()
    assert len(data["checks"]) == 5
    assert all("entrypoint" not in c or not c.get("error") for c in data["checks"])


def test_full_run_demo():
    run = _wait_done(_make_run()["id"])
    assert run["status"] == "done", run.get("error")
    s = run["summary"]
    assert s["total_apps"] == 300
    assert s["apps_with_diffs"] > 0
    assert run["check_rows"], "проверки должны вернуть строки"
    row = run["check_rows"][0]
    for col in ("LABEL", "SOURCE", "CNT", "TOTAL", "RESULT", "CONFIDENCE"):
        assert col in row
    fails = [r for r in run["check_rows"] if r["RESULT"] == "FAIL"]
    assert fails, "на демо-данных есть срабатывания FAIL"


def test_facts_and_drilldown():
    run = _wait_done(_make_run()["id"])
    rid = run["id"]
    facts = client.get(f"/api/runs/{rid}/facts?dims=path,change_type").json()
    assert facts["rows"]
    group = facts["rows"][0]
    f = {k: v for k, v in group["dims"].items()}
    apps = client.get(f"/api/runs/{rid}/apps",
                      params={k: v for k, v in f.items() if k in
                              ("path", "change_type")}).json()
    assert apps["app_ids"]
    app_id = apps["app_ids"][0]
    detail = client.get(f"/api/runs/{rid}/apps/{app_id}").json()
    assert detail["attributes"]
    assert any(r["DIFF_FLAG"] for r in detail["attributes"])


def test_adhoc_check_adds_to_report(monkeypatch):
    monkeypatch.setenv("CSD_TRUST_LOCAL_CODE", "1")
    rid = _wait_done(_make_run()["id"])["id"]
    code = '''
def run(mart, params):
    n = mart.total_apps()
    return [{"LABEL": "Проверка на лету", "SOURCE": "AFTER", "ARGUMENT": "",
             "CNT": n, "TOTAL": n, "METRIC": 1.0, "APP_IDS": mart.app_ids()[:5],
             "RESULT": "PASS", "CONFIDENCE": "HIGH"}]

CHECK = {"id": "adhoc", "label": "Проверка на лету", "params": {},
         "entrypoint": run}
'''
    r = client.post(f"/api/runs/{rid}/checks/try", json={"code": code})
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows[0]["CNT"] == 300
    client.post(f"/api/runs/{rid}/checks/add_result", json={"rows": rows})
    report = client.get(f"/api/runs/{rid}/checks").json()["rows"]
    assert any(r.get("_check_id") == "adhoc" for r in report)


def test_checks_export_html():
    rid = _wait_done(_make_run()["id"])["id"]
    r = client.get(f"/api/runs/{rid}/checks/export.html")
    assert r.status_code == 200
    assert "Отчёт автоматических проверок" in r.text
    assert "Сводный" in r.text  # ссылка на сводный отчёт приложения


def test_llm_graceful_without_config():
    rid = _wait_done(_make_run()["id"])["id"]
    data = client.post(f"/api/runs/{rid}/llm").json()["llm"]
    assert data["status"] == "unavailable"
    assert "CSD_LLM" in data["reason"]


def test_multiuser_isolation():
    first, second = TestClient(app), TestClient(app)
    r1 = first.post("/api/configs", json={"name": "cfg-a", "dataset": "demo://synthetic"}, headers={"X-User-Id": "u1"})
    assert r1.status_code == 200
    cfgs_u2 = second.get("/api/configs", headers={"X-User-Id": "u2"}).json()["configs"]
    assert all(c["name"] != "cfg-a" for c in cfgs_u2)
    mine = first.get("/api/configs", headers={"X-User-Id": "u1"}).json()["configs"]
    assert any(c["name"] == "cfg-a" for c in mine)


def test_datasets_endpoint():
    data = client.get("/api/datasets").json()
    assert any(d["id"] == "demo://synthetic" for d in data["datasets"])


def test_vertical_dims_and_exact_drill():
    """SOURCE/бакет в сводной и drill-down, точно соответствующий ячейке."""
    rid = _wait_done(_make_run()["id"])["id"]
    facts = client.get(f"/api/runs/{rid}/facts?dims=path,source&diff_flag=1&limit=20").json()
    assert facts["rows"]
    g = facts["rows"][0]["dims"]
    apps = client.get(f"/api/runs/{rid}/apps",
                      params={"path": g["path"], "source": g["source"],
                              "diff_flag": 1}).json()
    assert apps["total"] > 0
    fb = client.get(f"/api/runs/{rid}/facts?dims=bucket&diff_flag=1&limit=10").json()
    assert fb["rows"]


def test_segment_filters_apply_to_pivot_and_drill():
    rid = _wait_done(_make_run()["id"])["id"]
    vals = client.get(f"/api/runs/{rid}/segment_values").json()
    assert vals["partner"]
    p = vals["partner"][0]
    facts = client.get(f"/api/runs/{rid}/facts",
                       params={"dims": "path", "diff_flag": 1, "partner": p}).json()
    assert facts["rows"]
    apps = client.get(f"/api/runs/{rid}/apps",
                      params={"diff_flag": 1, "partner": p}).json()
    assert apps["total"] > 0


def test_export_link_not_hardcoded():
    rid = _wait_done(_make_run()["id"])["id"]
    r = client.get(f"/api/runs/{rid}/checks/export.html")
    assert "localhost:8000" not in r.text
    assert "http://testserver/#/runs/" in r.text


def test_dataset_upload_zip():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for side in ("BEFORE", "AFTER"):
            for i in (1, 2, 3):
                z.writestr(f"{side}/A{i}.json",
                           json.dumps({"appData": {"application_id": f"A{i}"},
                                       "decisionResult": {"decision": "APPROVE" if side == "BEFORE" else "REJECT"}}))
    r = client.post("/api/datasets/upload", files={"file": ("my-ds.zip", buf.getvalue())})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["apps"] == 3 and data["requires_pipeline"]
    listed = client.get("/api/datasets").json()["datasets"]
    assert any(d["id"] == data["id"] and d["apps"] == 3 for d in listed)


def test_dataset_upload_rejects_garbage():
    r = client.post("/api/datasets/upload", files={"file": ("bad.zip", b"not a zip")})
    assert r.status_code == 422


def test_source_split_logic_vs_external():
    run = _wait_done(_make_run()["id"])
    rid = run["id"]
    data = client.get(f"/api/runs/{rid}/source_split").json()
    for key in ("logic", "external_only", "both"):
        assert key in data and data[key]["cnt"] == len(data[key]["app_ids"])
        assert data[key]["attrs"] >= data[key]["cnt"]  # атрибутов не меньше, чем заявок
    assert data["logic"]["cnt"] + data["external_only"]["cnt"] + data["both"]["cnt"] \
        == run["summary"]["apps_with_diffs"]
    assert data["logic"]["attrs"] + data["external_only"]["attrs"] + data["both"]["attrs"] > 0


def test_mart_csv_export():
    run = _wait_done(_make_run()["id"])
    rid = run["id"]
    r = client.get(f"/api/runs/{rid}/mart.csv")
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    header = text.splitlines()[0].split(";")
    assert header == ["APP_ID", "PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "PATH",
                      "VALUE_BEFORE", "VALUE_AFTER", "ADJUSTED_BEFORE", "ADJUSTED_AFTER",
                      "DIFF_FLAG", "CHANGE_TYPE"]
    assert len(text.splitlines()) > 100  # витрина демо-прогона
    busy = client.get(f"/api/runs/{rid}/mart.csv")  # готовый прогон — всё ещё 200
    assert busy.status_code == 200
