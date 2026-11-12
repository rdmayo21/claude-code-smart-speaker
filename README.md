# A smart speaker powered by Claude Code

Companion to the video **[Home Assistant Voice PE + Claude Code: I Built My Own Smart Speaker](https://youtu.be/uiEMxEWnbeg)**.

A [Home Assistant Voice Preview Edition](https://www.home-assistant.io/voice-pe/) (about $59) becomes a
speaker you wake by voice that can do whatever Claude Code can do on your computer. No Home
Assistant server, no Alexa or Google account.

```
Voice PE  ── Wi-Fi ──>  bridge.py on your computer  ──>  OpenAI Realtime   (the voice)
 wake word, mic,                    │
 speaker, LED ring                  └──>  claude -p in a folder you choose   (the brain)
```

The realtime model listens and talks. Anything that needs knowledge or an action goes to Claude
Code through one tool, `ask_claude`. Whatever Claude Code can do in that folder, the speaker can
do by voice.

## What you need

- A Home Assistant Voice Preview Edition and a USB-C data cable.
- A computer that stays on, on the same Wi-Fi (the video uses a Mac; Linux should work, untested).
- [Claude Code](https://claude.com/claude-code) installed and signed in (`claude` works in a terminal).
- An OpenAI API key **with credit on it** (the voice costs money per minute of talking; see Costs).
- [`uv`](https://docs.astral.sh/uv/). It runs the bridge and ESPHome with nothing else to install.
- A 2.4 GHz Wi-Fi network (the speaker cannot join 5 GHz-only networks).

## Set it up

The quick way: open Claude Code in this folder and paste the prompt in [SETUP.md](SETUP.md).

By hand:

1. **Key.** `mkdir -p ~/.config/voice-dot && printf '%s\n' 'sk-...' > ~/.config/voice-dot/openai-api-key && chmod 600 ~/.config/voice-dot/openai-api-key`
2. **Bridge.** `uv run bridge.py` and leave it running. First run creates `~/.config/voice-dot/config.json` with a secret token.
3. **Secrets.** `cp firmware/secrets.yaml.example firmware/secrets.yaml`, then set your Wi-Fi name, an OTA password and an API key (the file says how).
4. **Flash.** Plug the speaker into the computer with USB-C and run `./flash.sh`. It asks for the Wi-Fi password, compiles (several minutes the first time) and flashes. Later updates go over Wi-Fi: `./flash.sh ota`.
5. **Talk.** When the ring settles, say **"Okay Nabu, what time is it?"** The bridge log shows `heard:` and `said:` lines. Say **"stop"** to cut a reply.

## Teach it things

By default Claude Code can only read files in `~/voice-dot` and search the web. To let the
speaker *do* things, give Claude Code the means in that folder and allow them on purpose:

- Put a `CLAUDE.md` in `~/voice-dot` describing your home and what scripts exist.
- Add scripts (say `lights.sh off`) and allow exactly those in `~/.config/voice-dot/config.json`:
  `"claude_args": ["--allowedTools", "Read,Glob,Grep,WebSearch,WebFetch,Bash(./lights.sh:*)"]`
- Or connect MCP servers with a `.mcp.json` in that folder and allow their tools the same way.

In the video the light and the email go through tools the author had already given his own
Claude Code setup. This repo gives you the speaker and the bridge; what it can control is yours to add.

**Keep the allowed list narrow.** Anyone who can speak in the room can ask. The voice and Claude
Code are both told not to send, buy, delete or do anything irreversible on a spoken request, but
the allowed-tools list is the real limit.

## Settings

`~/.config/voice-dot/config.json` (restart the bridge after editing):

| Key | Default | What it does |
|---|---|---|
| `claude_workdir` | `~/voice-dot` | Folder Claude Code runs in |
| `claude_args` | read-only tools | Extra arguments for every `claude -p` |
| `claude_continue` | `true` | Remember earlier requests (continues the folder's last conversation) |
| `voice` / `model` | `marin` / `gpt-realtime` | The OpenAI voice and realtime model |
| `mic_gain` | `4` | Boost on the mic stream; lower it if speech sounds clipped |
| `follow_up_ms` | `6000` | How long the mic stays open after a reply; `0` for one turn per wake |
| `vad_eagerness` | `low` | How quickly it decides you have finished talking |

To keep the bridge running after a reboot, run `uv run bridge.py` from a launchd agent (macOS) or a systemd user service (Linux).

## Costs

OpenAI bills the realtime voice by audio time; audio only flows after a wake word and the session
closes after five idle minutes. Claude Code asks use your Claude plan or API key. If the speaker
wakes but never answers, check the bridge log first: `insufficient_quota` means the OpenAI key is out of credit.

## What was tested, and what was not

- The bridge is a trimmed copy of the one that has run the author's bedroom speaker since October 2026.
  Removed: his Cast-speaker playback, premade home commands and private agent hub. Changed: the
  one tool now runs `claude -p`.
- Tested in this form: the 25 unit tests; real asks through `claude -p` (including follow-ups);
  the server with a stand-in device (handshake, token check, error path); the firmware config
  validates with ESPHome 2026.10.1.
- **Not** tested in this form: a full spoken round trip on a real speaker, and flashing from
  `flash.sh` as written here. If something is off, open an issue.

```bash
uv run --with pytest pytest -q
```

## Troubleshooting

- **Ring never settles / no wake:** the speaker cannot reach the bridge. Is `bridge.py` running? Is the computer on the same network, awake, with its firewall allowing port 7697?
- **It hears itself and asks twice:** something changed the JSON the bridge sends; the firmware matches exact substrings (see `device_json`).
- **Wake word works, commands are not understood:** raise or lower `mic_gain`.
- **Wi-Fi fails after flashing:** fix `firmware/secrets.yaml` and flash again over USB. The fallback open access point is removed on purpose.

## Credits and license

- Firmware: `firmware/voice-pe-realtime.yaml` is a pinned, patched copy of
  [xandervanerven/home-assistant-voice-pe](https://github.com/xandervanerven/home-assistant-voice-pe)
  (its `va_client` thin client), itself built on ESPHome's
  [home-assistant-voice-pe](https://github.com/esphome/home-assistant-voice-pe). Their license is in
  `firmware/LICENSE-home-assistant-voice-pe`; every change is marked `PATCH (voice dot)`.
- Everything else: MIT, see [LICENSE](LICENSE).
- Not affiliated with Anthropic, OpenAI, Nabu Casa or Home Assistant.
