"""Tests for the bridge: audio maths, the device protocol and the session flow.

No network and no real Claude Code: the OpenAI socket and the device are sinks,
and ask_claude is replaced where a test needs an answer.
"""

from __future__ import annotations

import asyncio
import base64
import json
import stat
from array import array
from pathlib import Path

import pytest

import bridge


def _pcm(values: list[int]) -> bytes:
    return array("h", values).tobytes()


def _samples(pcm: bytes) -> list[int]:
    out = array("h")
    out.frombytes(pcm)
    return list(out)


def test_upsampler_makes_three_samples_for_every_two() -> None:
    up = bridge.Upsampler()
    total = sum(len(_samples(up.feed(_pcm([0] * 320)))) for _ in range(2))
    assert total in (959, 960)  # the very last sample waits for the next chunk


def test_upsampler_interpolates_a_ramp_across_chunk_boundaries() -> None:
    up = bridge.Upsampler()
    out = _samples(up.feed(_pcm([0, 300, 600]))) + _samples(up.feed(_pcm([900, 1200])))
    assert out == [0, 200, 400, 600, 800, 1000, 1200]


def test_apply_gain_scales_and_saturates() -> None:
    assert _samples(bridge.apply_gain(_pcm([100, -100, 20000, -20000]), 4)) == [
        400,
        -400,
        32767,
        -32768,
    ]
    assert bridge.peak_percent(_pcm([0, -16384, 100])) == 50


def test_parse_path_takes_token_and_room_only() -> None:
    assert bridge.parse_path("/tok/bedroom") == ("tok", "bedroom")
    assert bridge.parse_path("/tok/Bad Room") is None
    assert bridge.parse_path("/tok") is None


@pytest.mark.parametrize(
    ("host", "ok"),
    [("127.0.0.1", True), ("192.168.1.20", True), ("10.0.0.7", True), ("8.8.8.8", False)],
)
def test_only_home_network_addresses_may_connect(host: str, ok: bool) -> None:
    assert bridge.allowed_source(host) is ok


def test_device_messages_match_the_firmwares_substring_checks() -> None:
    assert '"value":"replying"' in bridge.phase_message("replying")
    hello = bridge.hello_message(dict(bridge.DEFAULT_CONFIG))
    assert '"type":"hello"' in hello and '"follow_up_ms":6000' in hello


def test_session_is_24k_pcm_with_one_tool_and_the_room_rule() -> None:
    session = bridge.session_update("living-room", "marin", "low")["session"]
    assert session["tools"] == [bridge.ASK_CLAUDE_TOOL]
    assert "living room" in session["instructions"]
    assert "spoken yes" in session["instructions"]
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}


def test_config_creates_a_private_token_once_and_keeps_it(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    first = bridge.load_config(path)
    assert len(str(first["token"])) >= 20
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text(json.dumps({**first, "follow_up_ms": 0, "junk": 1}))
    second = bridge.load_config(path)
    assert second["token"] == first["token"]
    assert second["follow_up_ms"] == 0 and "junk" not in second


def test_claude_command_is_read_only_by_default_and_continues_when_asked() -> None:
    cfg = dict(bridge.DEFAULT_CONFIG)
    command = bridge.claude_command(cfg, "hi", resume=True)
    assert command[:5] == ["claude", "-p", "hi", "--output-format", "text"]
    assert "-c" in command and "Read,Glob,Grep,WebSearch,WebFetch" in command
    assert "-c" not in bridge.claude_command(cfg, "hi", resume=False)
    assert "Bash" not in " ".join(command)


def test_claude_prompt_names_the_room_and_forbids_acting_on_a_spoken_request() -> None:
    prompt = bridge.claude_prompt("bedroom", "turn off the light")
    assert "bedroom" in prompt and "irreversible" in prompt
    assert prompt.endswith("turn off the light")


def test_ask_claude_retries_without_continue_when_there_is_nothing_to_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[list[str]] = []

    async def fake_run(command: list[str], workdir: Path) -> tuple[int, str, str]:
        runs.append(command)
        return (
            (1, "", "No conversation found to continue")
            if "-c" in command
            else (0, "9:53 PM\n", "")
        )

    monkeypatch.setattr(bridge, "_run_claude", fake_run)
    cfg = {**bridge.DEFAULT_CONFIG, "claude_workdir": str(tmp_path / "work")}
    assert asyncio.run(bridge.ask_claude(cfg, "bedroom", "what time is it?")) == "9:53 PM"
    assert len(runs) == 2 and (tmp_path / "work").is_dir()


def test_ask_claude_says_plainly_when_claude_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(command: list[str], workdir: Path) -> tuple[int, str, str]:
        return 1, "", "not logged in"

    monkeypatch.setattr(bridge, "_run_claude", fake_run)
    cfg = {**bridge.DEFAULT_CONFIG, "claude_workdir": str(tmp_path), "claude_continue": False}
    answer = asyncio.run(bridge.ask_claude(cfg, "bedroom", "hello"))
    assert answer == "ask_claude failed: not logged in"


# ------------------------------------------------------------ session flow


class _Sink:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        pass

    def texts(self) -> list[dict]:
        return [json.loads(m) for m in self.sent if isinstance(m, str)]

    def phases(self) -> list[str]:
        return [m["value"] for m in self.texts() if m["type"] == "phase"]


def _session() -> tuple[bridge.RoomSession, _Sink, _Sink]:
    device, openai = _Sink(), _Sink()
    session = bridge.RoomSession(device, "bedroom", dict(bridge.DEFAULT_CONFIG))  # type: ignore[arg-type]
    session.openai = openai  # type: ignore[assignment]
    return session, device, openai


def _answers(monkeypatch: pytest.MonkeyPatch, text: str) -> list[str]:
    asked: list[str] = []

    async def fake(cfg: dict, room: str, request: str) -> str:
        asked.append(request)
        return text

    monkeypatch.setattr(bridge, "ask_claude", fake)
    return asked


CALL = {
    "type": "function_call",
    "name": "ask_claude",
    "call_id": "c1",
    "arguments": json.dumps({"request": "what time is it?"}),
}
DELTA = base64.b64encode(b"\x01\x00").decode()


def test_start_is_answered_with_hello() -> None:
    async def go() -> _Sink:
        session, device, _ = _session()
        await session.on_device_text(json.dumps({"type": "start"}))
        return device

    assert asyncio.run(go()).texts()[0]["type"] == "hello"


def test_openai_events_drive_the_device_phases_and_audio() -> None:
    async def go() -> _Sink:
        session, device, _ = _session()
        for ev in (
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "response.created"},
            {"type": "response.output_audio.delta", "delta": DELTA},
            {"type": "response.output_audio.delta", "delta": DELTA},
            {"type": "response.done", "response": {"output": []}},
        ):
            await session.on_openai_event(ev)
        return device

    device = asyncio.run(go())
    assert device.phases() == ["listening", "thinking", "replying", "idle"]
    assert [m for m in device.sent if isinstance(m, bytes)] == [b"\x01\x00"] * 2


def test_ask_claude_call_answers_the_voice_then_lets_it_speak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answers(monkeypatch, "It's 9:53 PM.")

    async def go() -> tuple[_Sink, _Sink]:
        session, device, openai = _session()
        await session.on_openai_event({"type": "response.done", "response": {"output": [CALL]}})
        await asyncio.gather(*session.asks)
        return device, openai

    device, openai = asyncio.run(go())
    assert asked == ["what time is it?"]
    sent = openai.texts()
    assert sent[0]["item"] == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": "It's 9:53 PM.",
    }
    assert sent[1] == {"type": "response.create"}
    assert device.phases() == ["thinking"]


def test_a_call_reported_twice_runs_claude_once(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _answers(monkeypatch, "9:53 PM")

    async def go() -> None:
        session, _, _ = _session()
        for _ in range(2):
            await session.on_openai_event({"type": "response.done", "response": {"output": [CALL]}})
        await asyncio.gather(*session.asks)

    asyncio.run(go())
    assert asked == ["what time is it?"]


def test_an_answer_that_outlives_its_session_is_told_as_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _answers(monkeypatch, "9:53 PM")

    async def go() -> _Sink:
        session, _, _ = _session()
        await session.on_openai_event({"type": "response.done", "response": {"output": [CALL]}})
        fresh = _Sink()
        session.openai = fresh  # type: ignore[assignment]  # OpenAI dropped the old one
        await asyncio.gather(*session.asks)
        return fresh

    sent = asyncio.run(go()).texts()
    assert sent[0]["item"]["role"] == "system"
    assert "9:53 PM" in sent[0]["item"]["content"][0]["text"]
    assert sent[1] == {"type": "response.create"}


def test_an_unknown_function_call_is_ignored() -> None:
    call = {"type": "function_call", "name": "rm_rf", "call_id": "x", "arguments": "{}"}

    async def go() -> bridge.RoomSession:
        session, _, _ = _session()
        await session.on_openai_event({"type": "response.done", "response": {"output": [call]}})
        return session

    assert not asyncio.run(go()).asks


def test_stop_cancels_the_reply_and_mutes_its_tail() -> None:
    async def go() -> tuple[_Sink, _Sink]:
        session, device, openai = _session()
        await session.on_openai_event({"type": "response.created"})
        await session.on_device_text(json.dumps({"type": "interrupt"}))
        await session.on_openai_event({"type": "response.output_audio.delta", "delta": DELTA})
        return device, openai

    device, openai = asyncio.run(go())
    assert {"type": "response.cancel"} in openai.texts()
    assert not [m for m in device.sent if isinstance(m, bytes)]


def test_speech_heard_while_the_speaker_talks_is_its_own_echo() -> None:
    async def go() -> tuple[_Sink, _Sink]:
        session, device, openai = _session()
        await session.on_openai_event({"type": "response.created"})
        await session.on_openai_event({"type": "response.output_audio.delta", "delta": DELTA})
        await session.on_openai_event({"type": "input_audio_buffer.speech_started"})
        await session.on_openai_event({"type": "input_audio_buffer.speech_stopped"})
        await session.on_device_audio(b"\x05\x00" * 8)
        return device, openai

    device, openai = asyncio.run(go())
    assert device.phases() == ["replying"]  # no `listening`: that would reopen the mic
    sent = openai.texts()
    assert {"type": "input_audio_buffer.clear"} in sent
    assert not [m for m in sent if m["type"] == "input_audio_buffer.append"]


def test_a_new_wake_reopens_the_mic_after_a_reply() -> None:
    async def go() -> _Sink:
        session, device, _ = _session()
        await session.on_openai_event({"type": "response.created"})
        await session.on_openai_event({"type": "response.output_audio.delta", "delta": DELTA})
        await session.on_device_text(json.dumps({"type": "wake"}))
        await session.on_openai_event({"type": "input_audio_buffer.speech_started"})
        return device

    assert asyncio.run(go()).phases()[-1] == "listening"


def test_firmware_is_pinned_silent_on_failure_and_free_of_private_details() -> None:
    firmware = (Path(__file__).parent.parent / "firmware" / "voice-pe-realtime.yaml").read_text()
    assert "error_cloud_expired" not in firmware
    assert "\n  on_repeated_failure:" not in firmware
    assert "ref: 763a87a85fe2f86b6aa819e6b4cbc6bd6943f796" in firmware
    assert "okay_nabu" in firmware and "/alexa.json" not in firmware
