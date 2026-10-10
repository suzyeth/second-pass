import json

from fastapi.testclient import TestClient

import server


class FakeMCP:
    def __init__(self):
        self.fail = False

    async def prewarm(self):
        return 0.0

    async def aclose(self):
        return None

    async def run_query(self, sql):
        if self.fail:
            raise RuntimeError("dependency down")
        if "SELECT 1 AS ok" in sql:
            return json.dumps({"columns": ["ok"], "rows": [[1]]})
        if "FROM films" in sql:
            return json.dumps({"columns": ["film"], "rows": [["tos"]]})
        return json.dumps({"columns": [], "rows": []})


def make_client(monkeypatch):
    fake = FakeMCP()
    monkeypatch.setattr(server, "MCP", fake)
    return TestClient(server.app, raise_server_exceptions=False), fake


def test_health_is_live_and_has_request_id(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.json()["mcp"] == "mcp-clickhouse"


def test_ready_checks_dependency(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependency": "clickhouse-via-mcp",
    }


def test_ready_returns_503_when_dependency_fails(monkeypatch):
    client, fake = make_client(monkeypatch)
    with client:
        fake.fail = True
        response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "data dependency unavailable"


def test_unknown_film_is_rejected_before_query(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        response = client.get("/api/film/not-a-film")
    assert response.status_code == 404


def test_ask_rejects_unknown_film(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        response = client.post(
            "/api/ask", json={"question": "What changed?", "film": "unknown"}
        )
    assert response.status_code == 404


def test_ask_rejects_oversized_question(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        response = client.post("/api/ask", json={"question": "x" * 2001})
    assert response.status_code == 422


def test_bins_are_driven_by_tool_result():
    response = {
        "result": json.dumps({
            "columns": ["bin", "score"],
            "rows": [[4, 0.1], [2, 0.2], [4, 0.3]],
        })
    }
    assert server.bins_in(response) == [2, 4]
