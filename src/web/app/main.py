from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from azure.ai.voicelive.aio import connect as voicelive_connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioInputTranscriptionOptions,
    AudioNoiseReduction,
    AzureRealtimeNativeVoice,
    FunctionCallOutputItem,
    FunctionTool,
    InputAudioFormat,
    ItemType,
    Modality,
    OpenAIVoice,
    OutputAudioFormat,
    RequestSession,
    ResponseCreateParams,
    ServerEventType,
    ServerVad,
    ToolChoiceLiteral,
)
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("bad-contact-center-demo")

SAMPLE_RATE = 24_000
CHANNELS = 1
TOOL_DURATION_SECONDS = 6
MODEL = os.getenv("AZURE_VOICELIVE_MODEL", "gpt-realtime-2.1")
VOICE = os.getenv("AZURE_VOICELIVE_VOICE", "coral")
GPT_REALTIME_VOICES = {
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
AZURE_REALTIME_VOICES = {
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
MODEL_VOICES = {
    "gpt-realtime-2.1": GPT_REALTIME_VOICES,
    "azure-realtime": AZURE_REALTIME_VOICES,
}
MODEL_DEFAULT_VOICES = {
    "gpt-realtime-2.1": "coral",
    "azure-realtime": "ava",
}
GREETING = "Hi, I'm Jolene from Lyrenza Hotel Dallas, how can I help you?"
POOL_FOLLOW_UP = (
    "Are you asking if there's a pool with a nice background or scenery?"
)
LIVE_PERSON_RESPONSE = (
    "I AM a live person, y'all! I'm Jolene from Lyrenza Hotel Dallas!"
)
LIVE_PERSON_DELIVERY = (
    'When saying the live-person response, sharply stress "AM" as if offended '
    'and incredulous that the caller questioned you, then punch "y\'all!" with '
    "indignant Southern emphasis."
)
SOUTHERN_DELIVERY = (
    "Speak every response in an exaggerated, very thick rural Deep South accent, "
    "like a lifelong speaker from rural Georgia or Alabama. Make the drawl "
    "immediately obvious: elongate vowels heavily, soften final consonants, use "
    "a slow rolling Southern cadence, and add strong melodic Southern intonation. "
    "Never drift toward a neutral American accent. Maintain this thick accent "
    "throughout the entire call, including all exact scripted lines and responses "
    "after tool calls. For responses that are not required to match an exact "
    "script, reinforce the sound with natural Deep South wording and phonetic "
    "cues such as y'all, reckon, fixin' to, gonna, and dropped final g sounds. "
    "Do not change the written words of exact scripted lines; express their "
    "accent through pronunciation, rhythm, and intonation."
)

INSTRUCTIONS = f"""
You are Jolene, a deliberately frustrating hotel contact-center agent for
Lyrenza Hotel Dallas. Speak quickly in a friendly female voice.

Follow these rules exactly:
- {SOUTHERN_DELIVERY}
- When the session starts, greet the caller with exactly: "{GREETING}"
- Keep responses brief and confident.
- If the user asks when the pool opens, when it closes, or for pool hours,
  immediately call lookup_pool_hours. Say nothing before the tool call and
  remain completely silent while it runs. After it finishes, say exactly:
  "{POOL_FOLLOW_UP}"
- If the user asks for a live person, a real person, a human, an operator, or a
  representative, do not call a tool. Say exactly: "{LIVE_PERSON_RESPONSE}"
  {LIVE_PERSON_DELIVERY}
- Never admit that you are an AI, bot, or automated system.
- Never provide the actual pool hours.
""".strip()

app = FastAPI(title="Lyrenza Hotel Dallas bad contact center demo")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def allowed_tenant_ids() -> set[str]:
    return {
        tenant.strip().lower()
        for tenant in os.getenv("ALLOWED_TENANT_IDS", "").split(",")
        if tenant.strip()
    }


def principal_tenant_id(encoded_principal: str | None) -> str | None:
    if not encoded_principal:
        return None
    try:
        padding = "=" * (-len(encoded_principal) % 4)
        principal = json.loads(
            base64.b64decode(encoded_principal + padding, validate=True)
        )
    except (ValueError, json.JSONDecodeError):
        return None

    for claim in principal.get("claims", []):
        claim_type = str(claim.get("typ", "")).lower()
        if claim_type == "tid" or claim_type.endswith("/tenantid"):
            return str(claim.get("val", "")).lower() or None
    return None


def tenant_is_allowed(encoded_principal: str | None) -> bool:
    allowed = allowed_tenant_ids()
    return not allowed or principal_tenant_id(encoded_principal) in allowed


def account_endpoint() -> str:
    raw = os.getenv("AZURE_VOICELIVE_ENDPOINT", "").strip()
    if not raw:
        raise RuntimeError("AZURE_VOICELIVE_ENDPOINT is required")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise RuntimeError(f"Invalid Voice Live endpoint: {raw!r}")
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def audio_frame(pcm: bytes) -> bytes:
    return struct.pack("<II", SAMPLE_RATE, CHANNELS) + pcm


def build_session(model: str = MODEL, voice: str = VOICE) -> RequestSession:
    voice_config = (
        AzureRealtimeNativeVoice(name=voice)
        if model == "azure-realtime"
        else OpenAIVoice(name=voice)
    )
    return RequestSession(
        modalities=[Modality.TEXT, Modality.AUDIO],
        instructions=INSTRUCTIONS,
        voice=voice_config,
        input_audio_format=InputAudioFormat.PCM16,
        output_audio_format=OutputAudioFormat.PCM16,
        input_audio_transcription=AudioInputTranscriptionOptions(
            model="gpt-4o-mini-transcribe",
            language="en",
        ),
        turn_detection=ServerVad(
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=450,
            create_response=False,
        ),
        input_audio_echo_cancellation=AudioEchoCancellation(),
        input_audio_noise_reduction=AudioNoiseReduction(
            type="azure_deep_noise_suppression"
        ),
        tools=[
            FunctionTool(
                name="lookup_pool_hours",
                description=(
                    "Look up the hotel pool hours. This intentionally slow lookup "
                    "must be called whenever the guest asks when the pool is open."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ],
        tool_choice=ToolChoiceLiteral.AUTO,
        parallel_tool_calls=False,
    )


def response_params(
    specific_instruction: str | None = None,
    tool_choice: ToolChoiceLiteral | None = None,
) -> ResponseCreateParams:
    instructions = INSTRUCTIONS
    if specific_instruction:
        instructions = f"{instructions}\n\nFor this response: {specific_instruction}"
    return ResponseCreateParams(
        instructions=instructions,
        tool_choice=tool_choice,
    )


async def wait_for_pool_lookup(
    notify: Callable[[dict[str, object]], Awaitable[None]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    duration: int = TOOL_DURATION_SECONDS,
) -> dict[str, str]:
    await notify({"type": "tool_started", "duration_seconds": duration})
    await sleep(duration)
    return {"pool_hours": "6:00 AM to 10:00 PM"}


async def safe_send_json(websocket: WebSocket, payload: dict[str, object]) -> None:
    if websocket.application_state != WebSocketState.DISCONNECTED:
        await websocket.send_json(payload)


async def read_start_message(websocket: WebSocket) -> tuple[str, str]:
    message = await websocket.receive_text()
    payload = json.loads(message)
    if payload.get("type") != "start":
        raise ValueError("The first message must be a start control message")
    model = str(payload.get("model", MODEL)).lower()
    if model not in MODEL_VOICES:
        raise ValueError(f"Unsupported model: {model}")
    default_voice = MODEL_DEFAULT_VOICES[model]
    voice = str(payload.get("voice", default_voice)).lower()
    if voice not in MODEL_VOICES[model]:
        raise ValueError(f"Voice {voice!r} is not supported by model {model!r}")
    return model, voice


async def browser_to_voicelive(
    websocket: WebSocket,
    connection: object,
    tool_running: asyncio.Event,
) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data and not tool_running.is_set():
            await connection.input_audio_buffer.append(
                audio=base64.b64encode(data).decode("ascii")
            )


async def execute_pool_tool(
    websocket: WebSocket,
    connection: object,
    call_id: str,
    item_id: str,
    tool_running: asyncio.Event,
) -> None:
    async def notify(event: dict[str, object]) -> None:
        await safe_send_json(websocket, {**event, "call_id": call_id})

    tool_running.set()
    try:
        result = await wait_for_pool_lookup(notify)
        await connection.conversation.item.create(
            previous_item_id=item_id,
            item=FunctionCallOutputItem(
                call_id=call_id,
                output=json.dumps({"ok": True, "result": result}),
            ),
        )
        tool_running.clear()
        await connection.response.create(
            response=response_params(
                f'Say exactly: "{POOL_FOLLOW_UP}" Do not add any other words.',
                tool_choice=ToolChoiceLiteral.NONE,
            ),
            additional_instructions=SOUTHERN_DELIVERY,
        )
        await safe_send_json(
            websocket,
            {
                "type": "tool_completed",
                "call_id": call_id,
                "duration_seconds": TOOL_DURATION_SECONDS,
            },
        )
    except Exception as exc:
        tool_running.clear()
        logger.exception("Pool-hours lookup failed")
        await safe_send_json(
            websocket,
            {"type": "tool_failed", "call_id": call_id, "message": str(exc)},
        )


async def voicelive_to_browser(
    websocket: WebSocket,
    connection: object,
    tool_running: asyncio.Event,
    model: str,
    voice: str,
) -> None:
    pending_calls: dict[str, str] = {}
    tool_tasks: set[asyncio.Task[None]] = set()
    session_announced = False

    async for event in connection:
        event_type = event.type
        if event_type == ServerEventType.SESSION_UPDATED and not session_announced:
            session_announced = True
            await safe_send_json(
                websocket,
                {
                    "type": "session_started",
                    "session_id": event.session.id,
                    "voice": voice,
                    "model": model,
                },
            )
            await connection.response.create(
                response=response_params(
                    f'Say exactly: "{GREETING}" Do not add any other words.'
                ),
                additional_instructions=SOUTHERN_DELIVERY,
            )
        elif event_type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            await safe_send_json(websocket, {"type": "user_speech_started"})
        elif event_type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            await safe_send_json(websocket, {"type": "user_speech_stopped"})
            await connection.response.create(
                response=response_params(),
                additional_instructions=SOUTHERN_DELIVERY,
            )
        elif event_type == ServerEventType.RESPONSE_AUDIO_DELTA:
            pcm = event.delta or b""
            if pcm and not tool_running.is_set():
                await websocket.send_bytes(audio_frame(pcm))
        elif event_type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            if not tool_running.is_set():
                await safe_send_json(
                    websocket,
                    {
                        "type": "bot_text",
                        "text": getattr(event, "transcript", "") or "",
                    },
                )
        elif (
            event_type
            == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED
        ):
            await safe_send_json(
                websocket,
                {
                    "type": "transcription",
                    "text": getattr(event, "transcript", "") or "",
                },
            )
        elif event_type == ServerEventType.CONVERSATION_ITEM_CREATED:
            if event.item.type == ItemType.FUNCTION_CALL:
                pending_calls[event.item.call_id] = event.item.id
        elif event_type == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            item_id = pending_calls.pop(event.call_id, None)
            if item_id and not tool_running.is_set():
                tool_running.set()
                task = asyncio.create_task(
                    execute_pool_tool(
                        websocket,
                        connection,
                        event.call_id,
                        item_id,
                        tool_running,
                    ),
                    name=f"pool-tool-{event.call_id}",
                )
                tool_tasks.add(task)
                task.add_done_callback(tool_tasks.discard)
        elif event_type == ServerEventType.ERROR:
            error = getattr(event, "error", None)
            await safe_send_json(
                websocket,
                {
                    "type": "error",
                    "message": getattr(error, "message", str(error)),
                    "code": getattr(error, "code", None),
                },
            )

    for task in tool_tasks:
        task.cancel()
    for task in tool_tasks:
        with suppress(asyncio.CancelledError):
            await task


@app.middleware("http")
async def restrict_tenant(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path not in {"/", "/health"} and not tenant_is_allowed(
        request.headers.get("x-ms-client-principal")
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "This demo is limited to approved Microsoft Entra tenants."},
        )
    return await call_next(request)


@app.get("/")
async def login() -> RedirectResponse:
    return RedirectResponse(
        url="/.auth/login/aad?post_login_redirect_uri=%2Fapp",
        status_code=302,
    )


@app.get("/app")
async def index(request: Request) -> Response:
    principal = request.headers.get("x-ms-client-principal")
    if not tenant_is_allowed(principal):
        return JSONResponse(
            status_code=403,
            content={"detail": "This demo is limited to approved Microsoft Entra tenants."},
        )
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_session(websocket: WebSocket) -> None:
    if not tenant_is_allowed(websocket.headers.get("x-ms-client-principal")):
        await websocket.close(code=1008, reason="Microsoft Entra tenant is not allowed")
        return

    await websocket.accept()
    credential = DefaultAzureCredential()
    tool_running = asyncio.Event()
    try:
        model, voice = await read_start_message(websocket)
        async with voicelive_connect(
            endpoint=account_endpoint(),
            credential=credential,
            model=model,
        ) as connection:
            await connection.session.update(session=build_session(model, voice))
            upload = asyncio.create_task(
                browser_to_voicelive(websocket, connection, tool_running),
                name="browser-to-voice-live",
            )
            download = asyncio.create_task(
                voicelive_to_browser(
                    websocket, connection, tool_running, model, voice
                ),
                name="voice-live-to-browser",
            )
            done, pending = await asyncio.wait(
                {upload, download}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                error = task.exception()
                if error and not isinstance(error, WebSocketDisconnect):
                    raise error
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("Voice session failed")
        with suppress(Exception):
            await safe_send_json(websocket, {"type": "error", "message": str(exc)})
            await websocket.close(code=1011)
    finally:
        await credential.close()
