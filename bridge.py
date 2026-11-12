# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=14"]
# ///
"""A smart speaker powered by Claude Code: the bridge a Voice PE talks to.

A Home Assistant Voice Preview Edition, flashed with the firmware in firmware/,
hears its wake word and streams the microphone here. This bridge is the
speaker's whole back end. No Home Assistant is involved.

    Voice PE (va_client firmware)  --ws, LAN-->  this bridge  --wss-->  OpenAI Realtime
      wake word on the device       16 kHz up                         (the voice)
                                    24 kHz down        |
                                                       +-- `claude -p` --> Claude Code
                                                           (the brain, via ask_claude)

The realtime model is only the voice: it listens, talks and handles small talk.
Anything that needs knowledge, files or an action is passed to Claude Code
through one tool, `ask_claude`, which runs `claude -p` in a folder you choose.
Whatever Claude Code can do in that folder (its CLAUDE.md, scripts, MCP
servers, allowed tools), the speaker can do by voice.

The device side is the MIT-licensed thin client from
github.com/xandervanerven/home-assistant-voice-pe (component va_client).
Its wire protocol, which this file implements the server half of:

    device -> bridge  text {"type":"start"}      on every (re)connect -> we answer hello
                      text {"type":"wake"}       wake word / button: a new turn
                      text {"type":"interrupt"}  "stop" word: cancel the reply
                      text {"type":"flush"}      drop uncommitted mic audio
                      binary                     mic, PCM16 mono 16 kHz
    bridge -> device  text {"type":"hello", follow_up_ms, follow_up_open_delay_ms,
                            wake_open_delay_ms, playback_prebuffer_ms}
                      text {"type":"phase","value":"listening|thinking|replying|idle"}
                      text {"type":"error"}      device logs it and goes idle, silently
                      binary                     speech, PCM16 mono 24 kHz

Security: the endpoint is on your home network with no TLS (the ESP32 client is
plain ws://), so a device must know the secret path token in
~/.config/voice-dot/config.json, and only private source addresses are
accepted. Anyone in the room can still talk to it, so the voice and Claude Code
are both told never to act on a spoken yes for anything outward-facing,
money-moving or irreversible. Keep `claude_args` narrow: it decides what a
voice in the room can make Claude Code do.

Run:  uv run bridge.py                       (serves 0.0.0.0:7697)
      uv run bridge.py --print-url bedroom   (the va_url to flash into a device)
      uv run bridge.py --check               (what is installed, configured and running)
      uv run bridge.py --make-secrets NAME   (firmware/secrets.yaml for your Wi-Fi, nothing shown)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from array import array
from collections import deque
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("voice_dot")

STATE_DIR = Path.home() / ".config" / "voice-dot"
CONFIG_FILE = STATE_DIR / "config.json"
KEY_FILE = STATE_DIR / "openai-api-key"
DEFAULT_PORT = 7697

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"
FALLBACK_MODEL = "gpt-realtime"
MODEL_RATE = 24000  # OpenAI Realtime pcm16 in and out; va_client plays 24 kHz, sends 16 kHz

# Closing an idle OpenAI session ends the conversation's short-term memory but
# stops nothing billable (audio only flows after a wake word); the cap keeps a
# session well inside OpenAI's 60-minute limit.
IDLE_CLOSE_S = 300
MAX_SESSION_S = 30 * 60
PREBUFFER_FRAMES = 400  # mic frames held while the OpenAI session opens
ASK_DEADLINE_S = 240

ROOM_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")

READ_ONLY_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"
# Set by a coding agent in its own shell; a bridge started from one must not pass them on.
SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")

DEFAULT_CONFIG: dict[str, object] = {
    "token": "",
    # Mic stays open this long after each reply so you can answer without the
    # wake word; 0 = turn-based. Pushed to the device in hello (no reflash).
    "follow_up_ms": 6000,
    "follow_up_open_delay_ms": 700,
    "wake_open_delay_ms": 700,
    "playback_prebuffer_ms": 150,
    "vad_eagerness": "low",
    # Gain on the mic stream sent to OpenAI. va_client forwards the XMOS output
    # at unity, while the wake word hears it at gain 4: from across the room the
    # wake word fired but the command was too quiet to understand. 4 matches
    # the wake path; clipped samples saturate.
    "mic_gain": 4,
    "model": "gpt-realtime",
    "voice": "marin",
    # Where `claude -p` runs. Put a CLAUDE.md, scripts and .mcp.json there.
    "claude_workdir": "~/voice-dot",
    # Extra arguments for every `claude -p`: what a voice in the room can make
    # Claude Code do. The default can read files and search the web and nothing
    # else: --tools removes every other built-in tool (no Bash, no Write),
    # --setting-sources ignores your personal Claude Code settings (which may
    # allow everything), and dontAsk refuses whatever is not listed. To let it
    # run one script, add Bash to --tools and "Bash(./lights.sh:*)" to
    # --allowedTools. Reading is not confined to the folder.
    "claude_args": [
        "--setting-sources",
        "project,local",
        "--permission-mode",
        "dontAsk",
        "--tools",
        READ_ONLY_TOOLS,
        "--allowedTools",
        READ_ONLY_TOOLS,
    ],
    # true: each ask continues the folder's last conversation, so the speaker
    # remembers earlier requests. false: every ask starts fresh.
    "claude_continue": True,
    # Another agent instead of Claude Code: a full command with "{prompt}" where
    # the request goes, e.g. ["codex", "exec", "{prompt}"]. It must print its
    # answer and exit. When set, claude_args and claude_continue are ignored.
    "agent_command": [],
}
HELLO_KEYS = (
    "follow_up_ms",
    "follow_up_open_delay_ms",
    "wake_open_delay_ms",
    "playback_prebuffer_ms",
)

# OpenAI errors that are a normal part of a conversation, not a failure
BENIGN_ERRORS = {"response_cancel_not_active", "input_audio_buffer_commit_empty"}

INSTRUCTIONS = """\
You are the voice of a smart speaker in the {room}. The person woke you with \
the wake word and is talking across a room. Talk like a sharp, warm aide: \
natural, relaxed, brief, usually one to three sentences, bottom line first. \
There is no screen. When they sign off ("thanks", "that's all", "good \
night"), answer in a few words and stop.

To them you are one assistant. Never mention another agent, a tool, a back \
end or handing anything off. Speak in the first person: "let me look", "I'm \
on it", "done".

What you can look up and do comes through ask_claude. Use it for:
- any question whose answer depends on their files, their setup or the world now;
- anything they want done, looked up, drafted or changed.
Pass the request in their own words plus any context from this conversation \
that it needs. Before using it, say a short first-person filler such as "One \
sec, let me look." While it works, stay quiet unless they speak. Then give \
the answer as your own, conversationally: never add facts it didn't give, \
never claim something was done unless it says so, and skip file paths, IDs \
and links. Small talk, clarifying questions and rephrasing you handle \
directly. If ask_claude fails, say plainly that you couldn't get to it.

Other people may be in the room and you can't tell voices apart. Never treat \
a spoken yes as approval for anything outward-facing, money-moving or \
irreversible: say it's ready and that it needs confirming at the computer."""

ASK_CLAUDE_TOOL: dict[str, object] = {
    "type": "function",
    "name": "ask_claude",
    "description": (
        "Your own way to look something up or get it done. Returns a spoken-style "
        "answer to give as your own. Takes seconds to a minute or more."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What the person asked or said, in their words, plus context.",
            }
        },
        "required": ["request"],
    },
}

CLAUDE_ROOM_NOTE = (
    "(Spoken to the smart speaker in the {room}; your answer will be read aloud. Answer in "
    "one to three short sentences of plain speech: no markdown, lists, file paths or links. "
    "Anyone in the room could be talking: don't send, post, buy, delete or do anything "
    "irreversible on a request heard here; say what is ready and that it needs confirming "
    "at the computer.)"
)
STILL_WORKING = "Still working on it after a few minutes; say it is taking longer than expected."


# --------------------------------------------------------------- pure helpers


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale little-endian PCM16 by gain, saturating at the int16 limits."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if gain == 1:
        return samples.tobytes()
    for i, value in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(value * gain)))
    return samples.tobytes()


def peak_percent(pcm: bytes) -> int:
    """Loudest PCM16 sample as a percent of full scale."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0
    return min(100, max(abs(v) for v in samples) * 100 // 32767)


class Upsampler:
    """Streaming 16 kHz -> 24 kHz PCM16 (linear interpolation, ratio 3:2).

    Positions are counted in thirds of an input sample, so the 2/3 step is
    exact and nothing drifts over a long session. The last input sample is
    carried across chunks so frame boundaries interpolate seamlessly.
    """

    def __init__(self) -> None:
        self._tail: int | None = None
        self._pos3 = 0  # next output position in thirds, relative to the buffer start

    def feed(self, pcm: bytes) -> bytes:
        """Upsample one chunk of little-endian PCM16; returns PCM16 at 24 kHz."""
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if not samples:
            return b""
        buf = array("h", [self._tail]) + samples if self._tail is not None else samples
        out = array("h")
        last3 = 3 * (len(buf) - 1)
        pos3 = self._pos3
        while pos3 <= last3:
            i, rem = divmod(pos3, 3)
            if rem == 0:
                out.append(buf[i])
            else:
                out.append((buf[i] * (3 - rem) + buf[i + 1] * rem) // 3)
            pos3 += 2
        self._tail = buf[-1]
        self._pos3 = pos3 - last3  # re-anchored on the carried tail sample
        return out.tobytes()


def parse_path(path: str) -> tuple[str, str] | None:
    """`/<token>/<room>` -> (token, room); None for anything else."""
    parts = path.split("?", 1)[0].strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not ROOM_RE.match(parts[1]):
        return None
    return parts[0], parts[1]


def allowed_source(host: str) -> bool:
    """Only private (home network) and loopback addresses may connect."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def device_json(message: dict[str, object]) -> str:
    """Encode a message for the device: compact, no spaces.

    The firmware matches substrings (`"value":"replying"`, `"type":"hello"`),
    not JSON. With json.dumps' default `": "` the speaker ignores every hello,
    phase and error: its mic stays open while it talks and it hears itself.
    """
    return json.dumps(message, separators=(",", ":"))


def hello_message(cfg: dict[str, object]) -> str:
    """The handshake reply; the device reads its timing knobs from it."""
    return device_json({"type": "hello", **{k: int(str(cfg[k])) for k in HELLO_KEYS}})


def phase_message(value: str) -> str:
    """A phase change for the device's LED ring and mic gate."""
    return device_json({"type": "phase", "value": value})


def room_label(room: str) -> str:
    """`living-room` -> `living room`."""
    return room.replace("-", " ")


def session_update(room: str, voice: str, vad_eagerness: str) -> dict[str, object]:
    """session.update for a room speaker: raw 24 kHz PCM both ways, one tool."""
    pcm = {"type": "audio/pcm", "rate": MODEL_RATE}
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": INSTRUCTIONS.format(room=room_label(room)),
            "tools": [ASK_CLAUDE_TOOL],
            "tool_choice": "auto",
            "audio": {
                "input": {
                    "format": pcm,
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    # the Voice PE's XMOS chip already cleans the mic
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": vad_eagerness,
                    },
                },
                "output": {"format": pcm, "voice": voice},
            },
        },
    }


def claude_prompt(room: str, request: str, now: str = "") -> str:
    """The prompt the agent receives for one ask from a room."""
    note = CLAUDE_ROOM_NOTE.format(room=room_label(room))
    clock = f"\n(Local time now: {now}.)" if now else ""
    return f"{note}{clock}\n\n{request or '(empty request)'}"


def claude_command(cfg: dict[str, object], prompt: str, resume: bool) -> list[str]:
    """The command line for one ask: `claude -p`, or agent_command when set."""
    custom = cfg.get("agent_command")
    if isinstance(custom, list) and custom:
        return [str(part).replace("{prompt}", prompt) for part in custom]
    extra = cfg.get("claude_args")
    args = [str(a) for a in extra] if isinstance(extra, list) else []
    return ["claude", "-p", prompt, "--output-format", "text", *(["-c"] if resume else []), *args]


def check_lines(cfg: dict[str, object], port: int) -> list[tuple[bool, str]]:
    """What `--check` reports: each thing the speaker needs, and whether it is there."""
    custom = cfg.get("agent_command")
    agent = str(custom[0]) if isinstance(custom, list) and custom else "claude"
    workdir = Path(str(cfg["claude_workdir"])).expanduser()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        running = probe.connect_ex(("127.0.0.1", port)) == 0
    return [
        (shutil.which(agent) is not None, f"agent command `{agent}` is installed"),
        (read_key() is not None, f"OpenAI API key (OPENAI_API_KEY or {KEY_FILE})"),
        (shutil.which("uvx") is not None, "uvx is installed (flash.sh uses it to run ESPHome)"),
        (workdir.is_dir(), f"agent folder {workdir} exists (the bridge creates it when it starts)"),
        (running, f"bridge is running on port {port}"),
    ]


def read_key() -> str | None:
    """The OpenAI API key: OPENAI_API_KEY, else ~/.config/voice-dot/openai-api-key."""
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env:
        return env
    try:
        return KEY_FILE.read_text().strip() or None
    except FileNotFoundError:
        return None


def load_config(path: Path = CONFIG_FILE) -> dict[str, object]:
    """config.json merged over the defaults; creates the token on first run."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text())
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if not cfg["token"]:
        cfg["token"] = secrets.token_urlsafe(18)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(cfg, handle, indent=2)
        tmp.replace(path)
    return cfg


def make_secrets(firmware: Path, wifi_name: str) -> str:
    """Write firmware/secrets.yaml (mode 600) with fresh device secrets; returns a status line.

    The Wi-Fi password is left as the marker flash.sh asks for. An existing file keeps its
    device secrets (the OTA password is needed for later over-the-air flashes) and only
    gets the new network name and the marker back.
    """
    path = firmware / "secrets.yaml"
    old: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, value = line.partition(":")
            if value.strip() and not key.startswith("#"):
                old[key.strip()] = value.strip()
    values = {
        "wifi_ssid": json.dumps(wifi_name),
        "wifi_password": '"__ASK_AT_FLASH__"',
        "ota_password": old.get("ota_password") or json.dumps(secrets.token_urlsafe(24)),
        "api_key": old.get("api_key") or json.dumps(base64.b64encode(os.urandom(32)).decode()),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("".join(f"{key}: {value}\n" for key, value in values.items()))
    os.chmod(path, 0o600)
    kept = "kept the device secrets" if "ota_password" in old else "made new device secrets"
    return f"wrote {path} for Wi-Fi {wifi_name!r} ({kept}; flash.sh will ask for the password)"


def lan_name() -> str:
    """This computer's .local name, which survives the router changing its address."""
    if shutil.which("scutil"):  # macOS: the Bonjour name, not the computer name
        result = subprocess.run(
            ["scutil", "--get", "LocalHostName"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"{result.stdout.strip().lower()}.local"
    return f"{socket.gethostname().split('.')[0].lower()}.local"


# ------------------------------------------------------------- Claude Code


async def _run_claude(command: list[str], workdir: Path) -> tuple[int, str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV}
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=workdir,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), ASK_DEADLINE_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return (
        process.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


async def ask_claude(cfg: dict[str, object], room: str, request: str) -> str:
    """Run one request through Claude Code and return what to say.

    Args:
        cfg: The bridge config (claude_workdir, claude_args, claude_continue).
        room: Room slug, so Claude Code knows where it was said.
        request: What the person asked, as the voice passed it on.

    Returns:
        Claude Code's reply text, or a short failure line the voice can say.
    """
    workdir = Path(str(cfg["claude_workdir"])).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = claude_prompt(room, request, time.strftime("%A %B %d %Y, %I:%M %p"))
    attempts = (
        [True, False] if cfg.get("claude_continue") and not cfg.get("agent_command") else [False]
    )
    detail = ""
    for resume in attempts:
        try:
            code, out, err = await _run_claude(claude_command(cfg, prompt, resume), workdir)
        except TimeoutError:
            return STILL_WORKING
        except FileNotFoundError:
            return "ask_claude failed: the agent command is not installed on this computer"
        if code == 0 and out.strip():
            return out.strip()
        # The first ask in a new folder has no conversation to continue: go again without -c.
        detail = (err or out).strip()[-200:]
    return f"ask_claude failed: {detail or 'Claude Code said nothing'}"


# ------------------------------------------------------------------ sessions


class RoomSession:
    """One connected speaker: its OpenAI session, phases and ask_claude turns."""

    def __init__(self, device: ServerConnection, room: str, cfg: dict[str, object]) -> None:
        self.device = device
        self.room = room
        self.cfg = cfg
        self.upsampler = Upsampler()
        self.mic_peak = 0  # loudest raw mic sample this utterance, % of full scale
        self.openai: ClientConnection | None = None
        self.opening: asyncio.Task[None] | None = None
        self.reader: asyncio.Task[None] | None = None
        self.pending_mic: deque[str] = deque(maxlen=PREBUFFER_FRAMES)
        self.opened_at = 0.0
        self.last_activity = time.monotonic()
        self.responding = False
        self.replying_sent = False
        self.suppress_audio = False  # after "stop": drop the cancelled reply's tail
        self.asks: set[asyncio.Task[None]] = set()
        self.ask_lock = asyncio.Lock()
        self.stopped_during_ask = False
        self.phase = "idle"
        # From the first reply audio until the next wake or idle the speaker is
        # talking (or waiting on Claude Code), so any "speech" is its own voice
        # leaking back. Without this gate the speaker hears its own "One sec,
        # let me look" and asks twice. While gated, mic audio and VAD events
        # are dropped.
        self.mic_gated = False
        self.seen_calls: set[str] = set()  # ask_claude call_ids already run
        self.closed = False  # the speaker hung up

    # ---- device side

    async def send_device(self, message: str | bytes) -> None:
        try:
            await self.device.send(message)
        except ConnectionClosed:
            pass

    async def set_phase(self, value: str) -> None:
        if value == "replying":
            self.mic_gated = True
        elif value == "idle":
            self.mic_gated = False  # the device opens its follow-up window itself
        if value != self.phase or value == "listening":
            self.phase = value
            await self.send_device(phase_message(value))

    async def on_device_text(self, raw: str) -> None:
        try:
            kind = json.loads(raw).get("type")
        except (json.JSONDecodeError, AttributeError):
            return
        if kind == "start":
            await self.send_device(hello_message(self.cfg))
        elif kind == "wake":
            self.last_activity = time.monotonic()
            self.mic_gated = False
            self.suppress_audio = False
            self.stopped_during_ask = False
            if self.openai is not None and self.responding:
                await self.send_openai({"type": "response.cancel"})
            self.ensure_openai()
        elif kind == "interrupt":
            self.suppress_audio = True
            if self.asks:
                self.stopped_during_ask = True
            if self.openai is not None and self.responding:
                await self.send_openai({"type": "response.cancel"})
            await self.set_phase("idle")
        elif kind == "flush":
            self.pending_mic.clear()
            if self.openai is not None:
                await self.send_openai({"type": "input_audio_buffer.clear"})

    async def on_device_audio(self, pcm: bytes) -> None:
        if self.mic_gated:
            return
        self.last_activity = time.monotonic()
        self.mic_peak = max(self.mic_peak, peak_percent(pcm))
        pcm = apply_gain(pcm, float(str(self.cfg["mic_gain"])))
        chunk = base64.b64encode(self.upsampler.feed(pcm)).decode()
        if self.openai is not None and self.opening is None:
            await self.send_openai({"type": "input_audio_buffer.append", "audio": chunk})
        else:
            self.pending_mic.append(chunk)
            self.ensure_openai()

    # ---- OpenAI side

    def ensure_openai(self) -> None:
        if self.openai is None and self.opening is None:
            self.opening = asyncio.create_task(self.open_openai())

    async def _connect_openai(self, key: str) -> tuple[ClientConnection, str]:
        models = [str(self.cfg["model"])]
        if models[0] != FALLBACK_MODEL:
            models.append(FALLBACK_MODEL)
        last_error: Exception | None = None
        for model in models:
            try:
                ws = await connect(
                    OPENAI_REALTIME_URL.format(model=model),
                    additional_headers={"Authorization": f"Bearer {key}"},
                    max_size=2**24,
                    compression=None,
                )
            except Exception as exc:  # noqa: BLE001 — try the fallback model, then give up
                last_error = exc
                log.warning("%s: OpenAI refused %s: %s", self.room, model, exc)
                continue
            return ws, model
        raise RuntimeError(f"OpenAI unavailable: {last_error}")

    async def open_openai(self) -> None:
        try:
            key = read_key()
            if not key:
                raise LookupError(f"no OpenAI API key: set OPENAI_API_KEY or write {KEY_FILE}")
            ws, model = await self._connect_openai(key)
            voice = str(self.cfg["voice"])
            update = session_update(self.room, voice, str(self.cfg["vad_eagerness"]))
            await ws.send(json.dumps(update))
            while self.pending_mic:
                audio = self.pending_mic.popleft()
                await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
            self.openai = ws
            self.opened_at = time.monotonic()
            self.reader = asyncio.create_task(self.read_openai(ws))
            log.info("%s: OpenAI session open (%s, voice %s)", self.room, model, voice)
        except Exception as exc:  # noqa: BLE001 — the device must hear that it failed
            log.error("%s: %s", self.room, exc)
            self.pending_mic.clear()
            await self.send_device(device_json({"type": "error", "message": str(exc)[:120]}))
        finally:
            self.opening = None

    async def send_openai(self, event: dict[str, object]) -> None:
        if self.openai is None:
            return
        try:
            await self.openai.send(json.dumps(event))
        except ConnectionClosed:
            await self.drop_openai("connection lost")

    async def drop_openai(self, why: str) -> None:
        ws, self.openai = self.openai, None
        if ws is None:
            return
        minutes = (time.monotonic() - self.opened_at) / 60
        log.info("%s: OpenAI session closed after %.1f min (%s)", self.room, minutes, why)
        self.responding = False
        await ws.close()
        if self.reader is not None and self.reader is not asyncio.current_task():
            self.reader.cancel()
        self.reader = None

    async def read_openai(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                event = json.loads(raw)
                if isinstance(event, dict):
                    await self.on_openai_event(event)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — log and drop the session rather than wedge
            log.exception("%s: OpenAI reader failed", self.room)
        if self.openai is ws:
            code = getattr(ws, "close_code", None)
            reason = getattr(ws, "close_reason", None) or ""
            await self.drop_openai(f"OpenAI closed the session: {code} {reason}".strip())
            if not self.asks:
                await self.set_phase("idle")

    async def on_openai_event(self, ev: dict[str, object]) -> None:
        kind = ev.get("type")
        if kind == "input_audio_buffer.speech_started":
            if self.mic_gated:  # its own reply (or stale audio): never a turn
                log.info("%s: ignored speech while the speaker was talking", self.room)
                await self.send_openai({"type": "input_audio_buffer.clear"})
                return
            self.last_activity = time.monotonic()
            await self.set_phase("listening")
        elif kind == "input_audio_buffer.speech_stopped":
            if not self.mic_gated:
                await self.set_phase("thinking")
        elif kind == "conversation.item.input_audio_transcription.completed":
            log.info("%s heard: %s", self.room, str(ev.get("transcript", "")).strip()[:300])
            log.info(
                "%s mic peak %d%% before x%s gain",
                self.room,
                self.mic_peak,
                self.cfg["mic_gain"],
            )
            self.mic_peak = 0
        elif kind == "response.created":
            self.responding = True
            self.replying_sent = False
        elif kind == "response.output_audio.delta":
            self.last_activity = time.monotonic()
            if self.suppress_audio:
                return
            pcm = base64.b64decode(str(ev.get("delta", "")))
            if not self.replying_sent:
                self.replying_sent = True
                await self.set_phase("replying")
            await self.send_device(pcm)
        elif kind == "response.output_audio_transcript.done":
            log.info("%s said: %s", self.room, str(ev.get("transcript", "")).strip()[:300])
        elif kind == "response.done":
            await self.on_response_done(ev)
        elif kind == "error":
            err = ev.get("error")
            err = err if isinstance(err, dict) else {}
            code = str(err.get("code") or "")
            if code in BENIGN_ERRORS:
                return
            log.warning("%s: OpenAI error %s: %s", self.room, code, err.get("message"))
            await self.send_device(device_json({"type": "error", "message": code}))

    async def on_response_done(self, ev: dict[str, object]) -> None:
        self.responding = False
        response = ev.get("response")
        response = response if isinstance(response, dict) else {}
        status = response.get("status")
        if status not in (None, "completed"):
            log.info("%s: response %s %s", self.room, status, response.get("status_details"))
        output = response.get("output")
        calls = []
        for item in output if isinstance(output, list) else []:
            if not (
                isinstance(item, dict)
                and item.get("type") == "function_call"
                and item.get("name") == "ask_claude"
            ):
                continue
            call_id = str(item.get("call_id"))
            if call_id in self.seen_calls:  # one run per call, however reported
                log.info("%s: call %s already running, skipped", self.room, call_id)
                continue
            self.seen_calls.add(call_id)
            calls.append(item)
        origin = self.openai
        for call in calls:
            args = str(call.get("arguments") or "{}")
            task = asyncio.create_task(self.run_ask(str(call.get("call_id")), args, origin))
            self.asks.add(task)
            task.add_done_callback(self.asks.discard)
        if calls:
            await self.set_phase("thinking")
        elif not self.asks:
            await self.set_phase("idle")

    async def answer_ask(self, request: str) -> str:
        """One ask_claude, one at a time."""
        async with self.ask_lock:
            log.info("%s ask_claude: %s", self.room, request[:300])
            started = time.monotonic()
            try:
                answer = await ask_claude(self.cfg, self.room, request)
            except Exception as exc:  # noqa: BLE001 — the voice must still answer
                answer = f"ask_claude failed: {exc}"
            took = time.monotonic() - started
            log.info("%s ask_claude answered in %.0f s: %s", self.room, took, answer[:300])
            return answer

    async def run_ask(
        self, call_id: str, arguments: str, origin: ClientConnection | None = None
    ) -> None:
        """Run one ask_claude and hand the answer back to the voice.

        Args:
            call_id: the model's function call id.
            arguments: the call's JSON arguments ({"request": ...}).
            origin: the OpenAI session that made the call. A Claude Code turn
                can outlast it; the call_id means nothing to a new session, so
                the answer goes in as a message there instead.
        """
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = {}
        request = str(args.get("request", "")) if isinstance(args, dict) else ""
        answer = await self.answer_ask(request)
        self.last_activity = time.monotonic()
        if self.closed:
            return  # nobody to tell
        if self.openai is None:
            self.ensure_openai()
            if self.opening is not None:
                await self.opening
            if self.openai is None:
                return
        if origin is not None and self.openai is origin:
            item: dict[str, object] = {
                "type": "function_call_output",
                "call_id": call_id,
                "output": answer[:6000],
            }
        else:
            log.info(
                "%s: session changed during ask_claude, answer sent as a message",
                self.room,
            )
            item = {
                "type": "message",
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"The answer to the request ({request[:300]}): "
                        f"{answer[:6000]}\n\nSay it now, briefly.",
                    }
                ],
            }
        await self.send_openai({"type": "conversation.item.create", "item": item})
        if self.stopped_during_ask:
            await self.set_phase("idle")  # "stop" was said: keep the answer in context, unspoken
            return
        self.suppress_audio = False
        await self.send_openai({"type": "response.create"})

    async def reaper(self) -> None:
        """Close the OpenAI session when idle (or old) and nothing is in flight."""
        while True:
            await asyncio.sleep(15)
            if self.openai is None or self.responding or self.asks or self.opening:
                continue
            now = time.monotonic()
            if now - self.last_activity > IDLE_CLOSE_S:
                await self.drop_openai("idle")
            elif now - self.opened_at > MAX_SESSION_S and now - self.last_activity > 20:
                await self.drop_openai("session age cap")

    async def run(self) -> None:
        reaper = asyncio.create_task(self.reaper())
        try:
            async for message in self.device:
                if isinstance(message, bytes):
                    await self.on_device_audio(message)
                else:
                    await self.on_device_text(message)
        except ConnectionClosed:
            pass
        finally:
            self.closed = True
            reaper.cancel()
            await self.drop_openai("speaker disconnected")


# -------------------------------------------------------------------- server


class Bridge:
    """The WebSocket endpoint the room speakers connect to."""

    def __init__(self, cfg: dict[str, object]) -> None:
        self.cfg = cfg
        self.rooms: dict[str, float] = {}  # room -> connected since (epoch)

    def check(self, connection: ServerConnection, request: Request) -> Response | None:
        """Refuse strangers before the handshake; answer /health."""
        host = str(connection.remote_address[0]) if connection.remote_address else ""
        if not allowed_source(host):
            log.warning("refused %s: not a home-network address", host)
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        if request.path == "/health":
            body = {"ok": True, "rooms": sorted(self.rooms)}
            return connection.respond(HTTPStatus.OK, json.dumps(body) + "\n")
        target = parse_path(request.path)
        if target is None or not hmac.compare_digest(target[0], str(self.cfg["token"])):
            log.warning("refused %s: bad path", host)
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        return None

    async def handle(self, connection: ServerConnection) -> None:
        target = parse_path(connection.request.path if connection.request else "")
        if target is None:
            return
        room = target[1]
        peer = connection.remote_address[0] if connection.remote_address else "?"
        log.info("%s speaker connected from %s", room, peer)
        self.rooms[room] = time.time()
        try:
            await RoomSession(connection, room, self.cfg).run()
        finally:
            self.rooms.pop(room, None)
            log.info("%s speaker disconnected", room)


async def serve_forever(host: str, port: int) -> None:
    """Serve room speakers until killed."""
    cfg = load_config()
    Path(str(cfg["claude_workdir"])).expanduser().mkdir(parents=True, exist_ok=True)
    bridge = Bridge(cfg)
    async with serve(
        bridge.handle,
        host,
        port,
        process_request=bridge.check,
        compression=None,  # the ESP32 client doesn't negotiate deflate anyway
        max_size=2**20,
    ):
        log.info("voice dot on ws://%s:%d/<token>/<room>", host, port)
        log.info("Claude Code runs in %s", Path(str(cfg["claude_workdir"])).expanduser())
        if not read_key():
            log.warning("no OpenAI API key yet: set OPENAI_API_KEY or write %s", KEY_FILE)
        await asyncio.Future()


def main() -> None:
    """Serve, or print the va_url to flash into a speaker."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--print-url", metavar="ROOM", help="print a speaker's va_url and exit")
    parser.add_argument("--check", action="store_true", help="report what is ready and exit")
    parser.add_argument(
        "--make-secrets",
        metavar="WIFI_NAME",
        help="write firmware/secrets.yaml for this Wi-Fi network without showing any secret",
    )
    args = parser.parse_args()
    if args.check:
        lines = check_lines(load_config(), args.port)
        for ok, text in lines:
            print(("ok       " if ok else "MISSING  ") + text)
        raise SystemExit(0 if all(ok for ok, _ in lines) else 1)
    if args.make_secrets is not None:
        print(make_secrets(Path(__file__).resolve().parent / "firmware", args.make_secrets))
        return
    if args.print_url:
        if not ROOM_RE.match(args.print_url):
            raise SystemExit("room: lowercase letters, digits and dashes (e.g. bedroom)")
        cfg = load_config()
        print(f"ws://{lan_name()}:{args.port}/{cfg['token']}/{args.print_url}")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(serve_forever(args.host, args.port))


if __name__ == "__main__":
    main()
