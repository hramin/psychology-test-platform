"""End-to-end API tests via FastAPI's TestClient (no server needed)."""

from __future__ import annotations

QUESTION_IDS = list(range(1, 131))


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["instruments"] >= 2


def test_instruments_lists_both(client):
    r = client.get("/instruments")
    assert r.status_code == 200
    by_slug = {row["slug"]: row for row in r.json()}
    assert by_slug["mmpi-teen-13"]["kind"] == "profile"
    assert by_slug["wellbeing-8"]["kind"] == "index"


def test_mmpi_schema(client):
    r = client.get("/instruments/mmpi-teen-13/schema")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["page_size"] == 10
    assert len(body["schema"]["items"]) == 130


def test_schema_unknown_slug_404(client):
    assert client.get("/instruments/nope/schema").status_code == 404


def test_score_mmpi_profile(client):
    payload = {
        "slug": "mmpi-teen-13",
        "responses": {str(i): "yes" for i in QUESTION_IDS},
        "demographics": {"gender": "girl", "age": 15, "class": "نهم"},
    }
    r = client.post("/score", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "mmpi-teen-13" and body["version"] == 1
    assert len(body["score_result"]["raw"]) == 13
    assert body["result_view"]["kind"] == "profile"
    assert body["result_view"]["chart"]["type"] == "line"


def test_score_wellbeing_index(client):
    payload = {
        "slug": "wellbeing-8",
        "responses": {str(i): "0" for i in range(1, 9)},
        "demographics": {},
    }
    r = client.post("/score", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["result_view"]["kind"] == "index"
    assert body["result_view"]["chart"]["type"] == "gauge"
    assert body["score_result"]["derived"]["index"] == 6


def test_score_unknown_slug_404(client):
    r = client.post("/score", json={"slug": "nope", "responses": {}, "demographics": {}})
    assert r.status_code == 404


def test_score_mmpi_missing_gender_422(client):
    payload = {
        "slug": "mmpi-teen-13",
        "responses": {str(i): "yes" for i in QUESTION_IDS},
        "demographics": {},
    }
    assert client.post("/score", json=payload).status_code == 422
