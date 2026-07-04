from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.state import get_session, create_session


@pytest.fixture()
def client():
    """TestClient with database I/O mocked out."""
    with patch("app.api.routes.scheduling.save_session"), \
         patch("app.core.database.load_all_sessions", return_value={}):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


class TestIndexRoute:
    def test_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Group Scheduler" in response.text

    def test_contains_create_form(self, client):
        response = client.get("/")
        assert 'action="/session/init"' in response.text


class TestInitSession:
    def test_redirects_to_submit_page(self, client):
        response = client.post(
            "/session/init",
            data={"title": "Test Group", "names": "Alice\nBob\nCarol",
                  "min_group_size": "3", "session_duration": "120"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/submit/")

    def test_session_has_correct_min_group_size(self, client):
        response = client.post(
            "/session/init",
            data={"title": "Test", "names": "Alice\nBob\nCarol\nDave",
                  "min_group_size": "4", "session_duration": "120"},
            follow_redirects=False,
        )
        token = response.headers["location"].split("/submit/")[1]
        state = get_session(token)
        assert state is not None
        assert state.class_config.weekly_config.minimum_group_size == 4

    def test_session_has_correct_session_duration(self, client):
        response = client.post(
            "/session/init",
            data={"title": "Test", "names": "Alice\nBob\nCarol",
                  "min_group_size": "3", "session_duration": "90"},
            follow_redirects=False,
        )
        token = response.headers["location"].split("/submit/")[1]
        state = get_session(token)
        assert state is not None
        assert state.class_config.weekly_config.session_duration_minutes == 90

    def test_uses_defaults_when_fields_empty(self, client):
        response = client.post(
            "/session/init",
            data={"title": "Test", "names": "Alice\nBob\nCarol",
                  "min_group_size": "", "session_duration": ""},
            follow_redirects=False,
        )
        assert response.status_code == 303
        token = response.headers["location"].split("/submit/")[1]
        state = get_session(token)
        assert state.class_config.weekly_config.minimum_group_size == 3
        assert state.class_config.weekly_config.session_duration_minutes == 120

    def test_creates_roster_from_names(self, client):
        response = client.post(
            "/session/init",
            data={"title": "Test", "names": "Alice\nBob\nCarol",
                  "min_group_size": "3", "session_duration": "120"},
            follow_redirects=False,
        )
        token = response.headers["location"].split("/submit/")[1]
        state = get_session(token)
        names = {s.name for s in state.students.values()}
        assert names == {"Alice", "Bob", "Carol"}


class TestSubmitRoute:
    def test_unknown_token_returns_404(self, client):
        response = client.get("/submit/doesnotexist")
        assert response.status_code == 404

    def test_valid_token_returns_200(self, client):
        token, _ = create_session("Test", ["Alice", "Bob", "Carol"], 3, 120)
        response = client.get(f"/submit/{token}")
        assert response.status_code == 200

    def test_shows_participant_names(self, client):
        token, _ = create_session("Group Title", ["Alice", "Bob", "Carol"], 3, 120)
        response = client.get(f"/submit/{token}")
        assert "Alice" in response.text
        assert "Bob" in response.text

    def test_shows_share_url(self, client):
        token, _ = create_session("Test", ["Alice", "Bob"], 3, 120)
        response = client.get(f"/submit/{token}")
        assert token in response.text

    def test_submit_availability_redirects(self, client):
        token, state = create_session("Test", ["Alice", "Bob", "Carol"], 3, 120)
        sid = state.expected_student_ids[0]
        response = client.post(
            f"/submit/{token}",
            data={
                "student_id": sid,
                "viewer_timezone": "America/Los_Angeles",
                "block_start": ["2026-06-30T18:00:00"],
                "block_end": ["2026-06-30T20:00:00"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert f"/submit/{token}" in response.headers["location"]

    def test_submit_availability_updates_state(self, client):
        token, state = create_session("Test", ["Alice", "Bob", "Carol"], 3, 120)
        sid = state.expected_student_ids[0]
        client.post(
            f"/submit/{token}",
            data={
                "student_id": sid,
                "viewer_timezone": "America/Los_Angeles",
                "block_start": ["2026-06-30T18:00:00"],
                "block_end": ["2026-06-30T20:00:00"],
            },
        )
        updated = get_session(token)
        assert sid in updated.availability
        assert len(updated.availability[sid].blocks) == 1

    def test_clear_deletes_session(self, client):
        token, _ = create_session("Test", ["Alice"], 3, 120)
        with patch("app.api.routes.scheduling.clear_session"):
            client.post(f"/submit/{token}/clear", follow_redirects=False)
        assert get_session(token) is None
