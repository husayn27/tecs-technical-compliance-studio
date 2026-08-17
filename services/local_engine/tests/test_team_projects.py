from __future__ import annotations

import httpx
import pytest

from tecs_engine.team_projects import TeamProjectError, TeamProjectService


def test_team_projects_require_workspace_key(monkeypatch) -> None:
    monkeypatch.setattr("tecs_engine.team_projects.keyring.get_password", lambda *_: None)
    service = TeamProjectService()

    with pytest.raises(TeamProjectError) as caught:
        service.list_projects()

    assert caught.value.status_code == 428


def test_team_projects_use_jwt_and_workspace_key(monkeypatch) -> None:
    monkeypatch.setattr("tecs_engine.team_projects.keyring.get_password", lambda *_: "a" * 64)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/functions/v1/tecs-team-projects"
        assert request.headers["authorization"].startswith("Bearer eyJ")
        assert request.headers["x-tecs-team-key"] == "a" * 64
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": "project-one", "revision": 1})

    service = TeamProjectService(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    assert service.list_projects() == []
    saved = service.save_project({"project_name": "Project", "draft": {}})
    assert saved["revision"] == 1


def test_team_project_conflicts_are_preserved(monkeypatch) -> None:
    monkeypatch.setattr("tecs_engine.team_projects.keyring.get_password", lambda *_: "a" * 64)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"detail": "This project was updated by another team member."},
        )

    service = TeamProjectService(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    with pytest.raises(TeamProjectError) as caught:
        service.save_project({"project_name": "Project", "draft": {}})

    assert caught.value.status_code == 409
    assert "another team member" in str(caught.value)
