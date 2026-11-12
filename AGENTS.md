# AGENTS.md: set this speaker up for your human

You are an AI coding agent (Claude Code, Codex, Gemini CLI, Cursor, or any other) and your human
wants the smart speaker from the video. This file is the whole job. Work through it top to bottom,
run every check, and do everything yourself except the five things marked **HUMAN**.

**Done means:** the human says "Okay Nabu, what time is it?" to the speaker and hears an answer,
and `bridge.log` shows a `heard:` line and a `said:` line for it.

## What this is

A Home Assistant Voice Preview Edition (the speaker) connects over Wi-Fi to `bridge.py` on this
computer. The bridge uses OpenAI Realtime as the voice and passes real requests to a coding agent
on this computer (`claude -p` by default) running in `~/voice-dot`. No Home Assistant server.

## Rules

- Never print, log or paste the OpenAI key, the Wi-Fi password, the bridge token or
  `firmware/secrets.yaml`. Never ask the human to paste a secret into the chat.
- Do not widen what the speaker's agent may do (step 8) without showing the human the exact list
  and getting a yes. Anyone in the room can talk to the speaker.
- If a check fails, read the log and diagnose before changing anything. Do not edit
  `firmware/voice-pe-realtime.yaml` to get past an error.

## Steps

1. **Tools.** Run `command -v uv claude`. If `uv` is missing, install it
   (`curl -LsSf https://astral.sh/uv/install.sh | sh`). If the human uses a different agent than
   Claude Code for the speaker's brain, see "Another agent" below.
2. **Tests.** `uv run --with pytest pytest -q` must end with `passed`.
3. **HUMAN: OpenAI key.** Ask them to run this themselves, with their key, in their own terminal:
   `mkdir -p ~/.config/voice-dot && printf '%s\n' 'sk-...' > ~/.config/voice-dot/openai-api-key && chmod 600 ~/.config/voice-dot/openai-api-key`
   The key needs credit on it (platform.openai.com, Billing).
4. **Bridge.** Start it in the background and keep it running:
   `nohup uv run bridge.py > ~/.config/voice-dot/bridge.log 2>&1 &`
   Check: `curl -s http://127.0.0.1:7697/health` prints `{"ok": true, "rooms": []}`.
   Check: `uv run bridge.py --check` prints `ok` on every line.
5. **Firmware secrets.** `cp firmware/secrets.yaml.example firmware/secrets.yaml`. Ask the human
   for the name of their **2.4 GHz** Wi-Fi network and write it to `wifi_ssid`. Generate
   `ota_password` (`python3 -c 'import secrets;print(secrets.token_urlsafe(24))'`) and `api_key`
   (`python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())'`) and write
   them in without displaying them. Leave `wifi_password` as `"__ASK_AT_FLASH__"`.
6. **HUMAN: plug in and flash.** Ask them to connect the speaker to this computer with a USB-C
   *data* cable and run `./flash.sh` in their own terminal. It asks for the Wi-Fi password, then
   compiles (5 to 15 minutes the first time) and flashes. If it lists serial ports, they pick the
   USB one. Wait for them to say it finished.
7. **HUMAN: first words.** Run `tail -f ~/.config/voice-dot/bridge.log` (or re-read the file) and
   look for `speaker connected`. Then ask them to say **"Okay Nabu, what time is it?"**
   Expect, in order: `OpenAI session open`, `heard: ...`, `ask_claude: ...`, `said: ...`.
   That is "done". Tell them "stop" cuts a reply and "Okay Nabu" starts a new one.
8. **HUMAN: what should it do?** Ask what they want to control by voice. For each thing, put the
   means in `~/voice-dot` (a `CLAUDE.md` describing their home, a script, or an MCP server in
   `.mcp.json`), then add only those tools to `claude_args` in `~/.config/voice-dot/config.json`,
   for example `"Bash(./lights.sh:*)"` appended to the `--allowedTools` value. Show the final
   list, get a yes, save, restart the bridge, and have them try it by voice.
9. **HUMAN: keep it running?** Offer to start the bridge at login: a launchd agent on macOS
   (`~/Library/LaunchAgents`), a systemd user service on Linux. Command: `uv run bridge.py` with
   this folder as the working directory.

Later firmware updates go over Wi-Fi: `./flash.sh ota`.

## When it does not work

| What you see | Cause and fix |
|---|---|
| No `speaker connected` in the log | Speaker cannot reach the bridge: bridge not running, computer asleep or on another network, firewall blocking port 7697, or Wi-Fi details wrong (fix `firmware/secrets.yaml`, flash again over USB) |
| `insufficient_quota` | The OpenAI key has no credit. HUMAN adds credit |
| `no OpenAI API key` | Step 3 not done, or the file is empty |
| `refused ...: bad path` | The speaker was flashed with an old token. Flash again |
| `heard:` lines are garbled or empty | Change `mic_gain` in the config (default 4; try 2 or 6), restart the bridge |
| It answers its own voice or asks twice | Something changed the JSON sent to the device; restore `device_json` in `bridge.py` |
| `ask_claude failed: ...` | Run the printed error down: is the agent signed in? Try `cd ~/voice-dot && claude -p "hello"` |
| `flash.sh` cannot find the device | Charge-only cable, or another program holds the serial port |

## Another agent

The speaker's brain is `claude -p` by default. To use a different agent, set `agent_command` in
`~/.config/voice-dot/config.json` to a command that takes the request, prints the answer and
exits, with `{prompt}` where the request goes, for example `["codex", "exec", "{prompt}"]`.
Only Claude Code has been tested. `claude_args` and `claude_continue` are ignored when it is set.

## Map

- `bridge.py`: the whole server, one file. Its header documents the device protocol.
- `firmware/speaker.yaml`: the small per-device config. `firmware/voice-pe-realtime.yaml`:
  vendored upstream firmware, pinned and patched; leave it alone.
- `flash.sh`: asks the bridge for the speaker's address and token, then runs ESPHome.
- `README.md`: the human-facing overview, settings table and costs.
