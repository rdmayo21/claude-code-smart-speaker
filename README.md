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

## Set it up: hand it to your agent

Paste this into your AI coding agent (Claude Code, Codex, Gemini CLI, Cursor, any of them):

```
Clone https://github.com/rdmayo21/claude-code-smart-speaker and follow its AGENTS.md to set up the smart speaker for me.
```

It does everything it can and stops only for what needs you: your OpenAI key, your Wi-Fi name and
password, plugging the speaker in, and saying the first words. [AGENTS.md](AGENTS.md) is the full
runbook, with a check after every step.

<details><summary>By hand instead</summary>

1. **Key.** `mkdir -p ~/.config/voice-dot && printf '%s\n' 'sk-...' > ~/.config/voice-dot/openai-api-key && chmod 600 ~/.config/voice-dot/openai-api-key`
2. **Bridge.** `uv run bridge.py` and leave it running. `uv run bridge.py --check` shows what is ready.
3. **Secrets.** `uv run bridge.py --make-secrets "your Wi-Fi name"`
4. **Flash.** Plug the speaker in with USB-C and run `./flash.sh bedroom`. Later updates: `./flash.sh ota bedroom`.
5. **Talk.** Say **"Okay Nabu, what time is it?"** Say **"stop"** to cut a reply.

</details>

## Teach it things

Out of the box the speaker's Claude Code can read files and search the web, and nothing else:
it has no shell and cannot write, and it ignores your personal Claude Code settings, so a
permissive setup on your computer does not leak into the speaker. (Reading is not confined to
`~/voice-dot`: it can read what your user can read, and say it aloud.)

To let the speaker *do* things, give Claude Code the means in `~/voice-dot` and allow them on purpose:

- Put a `CLAUDE.md` in `~/voice-dot` describing your home and what scripts exist.
- Add a script (say `lights.sh`) and, in `claude_args` in `~/.config/voice-dot/config.json`, add
  `Bash` to the `--tools` value and `Bash(./lights.sh:*)` to the `--allowedTools` value.

In the video the light and the email go through tools the author had already given his own
Claude Code setup. This repo gives you the speaker and the bridge; what it can control is yours to add.

**Keep the list narrow.** Anyone who can speak in the room can ask. The voice and Claude Code are
both told not to send, buy, delete or do anything irreversible on a spoken request, but the tool
list is the real limit.

## Settings

`~/.config/voice-dot/config.json` (restart the bridge after editing):

| Key | Default | What it does |
|---|---|---|
| `claude_workdir` | `~/voice-dot` | Folder Claude Code runs in |
| `claude_args` | read and web search only | What the speaker's Claude Code may do (see above) |
| `claude_continue` | `true` | Remember earlier requests (continues the folder's last conversation) |
| `agent_command` | empty | Use another agent instead of Claude Code, e.g. `["codex", "exec", "{prompt}"]` (untested) |
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
- Tested in this form: the unit tests; real asks through `claude -p` (follow-ups, and that the
  default refuses to run commands or write files even on a computer whose own Claude Code settings
  allow everything); a fresh agent following AGENTS.md as far as the steps that need a person;
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
