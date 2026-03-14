import pytest
from unittest.mock import patch, AsyncMock


def test_index_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Podcast Studio" in resp.text


def test_create_project_redirects(client):
    with patch("app.routers.projects.run_ingest_only", new_callable=AsyncMock):
        resp = client.post(
            "/projects",
            data={"url": "https://example.com", "num_speakers": "2", "tone": "neutral", "length": "medium"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/projects/" in resp.headers["location"]


def test_project_page_not_found(client):
    resp = client.get("/projects/nonexistent-id-xyz")
    assert resp.status_code == 404


def test_project_list_empty(client):
    resp = client.get("/projects")
    assert resp.status_code == 200


def test_artifact_not_found(client):
    resp = client.get("/projects/fake-id/artifacts/research_brief")
    assert resp.status_code == 404


def test_invalid_artifact_type(client):
    resp = client.get("/projects/fake-id/artifacts/unknown_type")
    assert resp.status_code == 400


def test_project_status_not_found(client):
    resp = client.get("/projects/nonexistent/status")
    assert resp.status_code == 404


def test_create_and_fetch_project(client):
    with patch("app.routers.projects.run_ingest_only", new_callable=AsyncMock):
        resp = client.post(
            "/projects",
            data={"url": "https://cbc.ca/news/test", "num_speakers": "2", "tone": "neutral", "length": "short"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    location = resp.headers["location"]
    project_id = location.split("/")[-1]

    # Fetch the project page
    resp2 = client.get(f"/projects/{project_id}")
    assert resp2.status_code == 200
    assert "cbc.ca" in resp2.text
