import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "web"))

from app.main import (
    GREETING,
    INSTRUCTIONS,
    LIVE_PERSON_RESPONSE,
    LIVE_PERSON_DELIVERY,
    MODEL,
    POOL_FOLLOW_UP,
    SOUTHERN_DELIVERY,
    AZURE_REALTIME_VOICES,
    GPT_REALTIME_VOICES,
    MODEL_VOICES,
    TOOL_DURATION_SECONDS,
    app,
    build_session,
    health,
    principal_tenant_id,
    response_params,
    tenant_is_allowed,
    wait_for_pool_lookup,
)


def test_health() -> None:
    assert asyncio.run(health()) == {"status": "ok"}


def test_demo_defaults() -> None:
    session = build_session().as_dict()

    assert MODEL == "gpt-realtime-2.1"
    assert TOOL_DURATION_SECONDS == 6
    assert session["voice"]["name"] == "coral"
    assert session["tools"][0]["name"] == "lookup_pool_hours"
    assert session["turn_detection"]["create_response"] is False
    assert GREETING in session["instructions"]
    assert POOL_FOLLOW_UP in session["instructions"]
    assert LIVE_PERSON_RESPONSE in session["instructions"]
    assert LIVE_PERSON_DELIVERY in session["instructions"]
    assert LIVE_PERSON_RESPONSE.startswith("I AM a live person, y'all!")
    assert SOUTHERN_DELIVERY in session["instructions"]
    assert "Maintain this thick accent throughout the entire call" in session[
        "instructions"
    ]
    assert "interim_response" not in session
    assert build_session("gpt-realtime-2.1", "ballad").as_dict()["voice"] == {
        "type": "openai",
        "name": "ballad",
    }
    assert build_session("azure-realtime", "ava").as_dict()["voice"] == {
        "type": "azure-realtime-native",
        "name": "ava",
    }
    assert GPT_REALTIME_VOICES == {
        "alloy",
        "ash",
        "ballad",
        "cedar",
        "coral",
        "echo",
        "marin",
        "sage",
        "shimmer",
        "verse",
    }
    assert AZURE_REALTIME_VOICES == {
        "aarti",
        "andrew",
        "ava",
        "denise",
        "diya",
        "elsa",
        "florian",
        "francisca",
        "meera",
        "xiaoxiao",
        "ximena",
        "yunxi",
    }
    assert set(MODEL_VOICES) == {"gpt-realtime-2.1", "azure-realtime"}


def test_every_response_repeats_full_persona() -> None:
    regular = response_params().as_dict()
    exact = response_params(
        'Say exactly: "Hello." Do not add any other words.',
        tool_choice="none",
    ).as_dict()

    assert regular["instructions"] == INSTRUCTIONS
    assert exact["instructions"].startswith(INSTRUCTIONS)
    assert "For this response: Say exactly" in exact["instructions"]
    assert SOUTHERN_DELIVERY in exact["instructions"]
    assert exact["tool_choice"] == "none"


def test_pool_lookup_waits_once_for_six_seconds() -> None:
    events: list[dict[str, object]] = []
    sleeps: list[float] = []

    async def notify(event: dict[str, object]) -> None:
        events.append(event)

    async def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    result = asyncio.run(wait_for_pool_lookup(notify, sleep=no_wait))

    assert sleeps == [6]
    assert events == [{"type": "tool_started", "duration_seconds": 6}]
    assert result["pool_hours"] == "6:00 AM to 10:00 PM"


def principal_header(tenant_id: str) -> str:
    principal = {
        "claims": [
            {
                "typ": "http://schemas.microsoft.com/identity/claims/tenantid",
                "val": tenant_id,
            }
        ]
    }
    return base64.b64encode(json.dumps(principal).encode()).decode()


def test_principal_tenant_id() -> None:
    assert principal_tenant_id(principal_header("tenant-a")) == "tenant-a"
    assert principal_tenant_id("not-base64") is None


def test_tenant_allowlist(monkeypatch: object) -> None:
    monkeypatch.setenv("ALLOWED_TENANT_IDS", "tenant-a,tenant-b")
    assert tenant_is_allowed(principal_header("tenant-a"))
    assert not tenant_is_allowed(principal_header("tenant-c"))
    assert not tenant_is_allowed(None)


def test_root_redirects_to_sign_in(monkeypatch: object) -> None:
    monkeypatch.setenv("ALLOWED_TENANT_IDS", "tenant-a")
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/.auth/login/aad?")


def test_app_includes_three_recording_downloads(monkeypatch: object) -> None:
    monkeypatch.setenv("ALLOWED_TENANT_IDS", "tenant-a")
    headers = {"x-ms-client-principal": principal_header("tenant-a")}
    page = TestClient(app).get("/app", headers=headers)

    assert page.status_code == 200
    assert 'id="user-download"' in page.text
    assert 'id="system-download"' in page.text
    assert 'id="mixed-download"' in page.text
    assert '<select id="voice">' in page.text
    assert '<select id="model">' in page.text
    assert '<option value="coral" selected>Coral</option>' in page.text
    assert page.text.index('id="stop"') < page.text.index('id="recording-downloads"')
    assert page.text.index('id="recording-downloads"') < page.text.index(
        'id="connection"'
    )


def test_voice_live_endpoint_is_not_committed() -> None:
    assert "AZURE_VOICELIVE_ENDPOINT" not in os.environ or os.environ[
        "AZURE_VOICELIVE_ENDPOINT"
    ].startswith("https://")
