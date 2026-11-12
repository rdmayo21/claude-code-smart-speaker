# AGENTS.md: set this speaker up for your human

You are an AI coding agent (Claude Code, Codex, Gemini CLI, Cursor, or any other) and your human
wants the smart speaker from the video. This file is the whole job. Work through it top to bottom
and run every check. Do everything yourself except the parts marked **HUMAN**; for those, ask,
wait for the answer, and go on.

**Done means:** the human says "Okay Nabu, what time is it?" to the speaker and hears the time,
and the bridge log has a `heard:` line and a `said:` line for it.

## What this is

A Home Assistant Voice Preview Edition (the speaker) connects over Wi-Fi to `bridge.py` on this
computer. The bridge uses OpenAI Realtime as the voice and passes real requests to a coding agent
on this computer (`claude -p` by default) running in `~/voice-dot`. No Home Assistant server.

## Rules

- Never print, log or paste the OpenAI key, the Wi-Fi password, the bridge token
  (`~/.config/voice-dot/config.json`) or `firmware/secrets.yaml`. Never ask the human to paste a
  secret into the chat. The commands below are written so you never need to see one.
- Do not widen what the speaker's agent may do (step 9) without showing the human the exact list
  and getting a yes. Anyone in the room can talk to the speaker.
- If a check fails, read the log and diagnose before changing anything. Do not edit
  `firmware/voice-pe-realtime.yaml` to get past an error.
- You may need to write outside this folder (`~/.config/voice-dot`, `~/voice-dot`), open a
  network port and download packages. If your sandbox blocks that, ask the human to allow it.

## Steps

1. **Tools.** Run:
   `for t in uv git claude; do command -v "$t" >/dev/null || echo "MISSING $t"; done`
   - `uv` missing: `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new shell.
   - `claude` missing: **HUMAN** installs and signs in to Claude Code (https://claude.com/claude-code),
     or chooses another agent (see "Another agent").
   - Signed in? `claude -p "Reply with the single word ready"` must print `ready`.
2. **Tests.** `uv run --with pytest pytest -q` must end with `passed`.
3. **HUMAN: OpenAI key.** First run `mkdir -p ~/.config/voice-dot`. Then ask them to run this
   themselves, with their own key, in their own terminal:
   `printf '%s\n' 'sk-...' > ~/.config/voice-dot/openai-api-key && chmod 600 ~/.config/voice-dot/openai-api-key`
   The key needs credit on it (platform.openai.com, Billing). Carry on with step 4 while you wait.
4. **Bridge.** Start it in the background, appending to the log:
   `mkdir -p ~/.config/voice-dot && (umask 077; nohup uv run bridge.py >> ~/.config/voice-dot/bridge.log 2>&1 &)`
   Wait for it (the first start downloads a package):
   `for i in $(seq 30); do curl -sf http://127.0.0.1:7697/health && break; sleep 1; done`
   must print `{"ok": true, "rooms": []}`.
   Then `uv run bridge.py --check`: every line must say `ok` (exit code 0). `MISSING OpenAI API key`
   means step 3 is not finished: wait for the human, then run it again.
   To restart the bridge later: `pkill -f "bridge.py" ; ` then the start command again.
5. **HUMAN: this computer.** Tell them the speaker only works while this computer is on, awake
   and on the same network. If the operating system asks whether Python may accept incoming
   connections, they must allow it. On Linux, open TCP port 7697 in `ufw`/`firewalld` if one is
   on, and check `avahi-daemon` is running (the speaker finds this computer by its `.local` name).
6. **HUMAN: Wi-Fi name and room.** Ask for the name of their **2.4 GHz** Wi-Fi network (the
   speaker cannot join 5 GHz-only networks) and which room the speaker will live in (one
   lowercase word such as `kitchen`; default `bedroom`). Then run:
   `uv run bridge.py --make-secrets "THEIR WIFI NAME"`
   It writes `firmware/secrets.yaml` (private, device secrets generated, nothing shown) and
   leaves the Wi-Fi password for `flash.sh` to ask for.
7. **HUMAN: plug in and flash.** Ask them to connect the speaker to this computer with a USB-C
   *data* cable and run `./flash.sh ROOM` in their own terminal (it needs them to type the Wi-Fi
   password). The first compile takes 5 to 15 minutes. If it lists serial ports, they pick the
   USB one. It is finished when the device's own log starts scrolling; they press Ctrl-C then.
   Your success sign is in the bridge log: `grep "speaker connected" ~/.config/voice-dot/bridge.log`.
   Nothing after two minutes: see the table below. Wrong Wi-Fi password: run step 6's command
   again and have them flash again.
8. **HUMAN: first words.** Ask them to say **"Okay Nabu, what time is it?"** Then read the end of
   the log (`tail -n 30 ~/.config/voice-dot/bridge.log`; do not use `tail -f`, it never returns).
   You should find `OpenAI session open`, a `heard:` line with their words and a `said:` line with
   the time; an `ask_claude:` line usually sits between them. That is "done". Tell them "stop"
   cuts a reply and "Okay Nabu" starts a new one.
9. **HUMAN: what should it do?** Out of the box the speaker's agent can only read files and
   search the web. Ask what they want to control by voice. For each thing, put the means in
   `~/voice-dot` (a `CLAUDE.md` describing their home, plus a script), then edit `claude_args` in
   `~/.config/voice-dot/config.json`: add `Bash` to the `--tools` value and the one script, for
   example `Bash(./lights.sh:*)`, to the `--allowedTools` value. Show the human the final list,
   get a yes, save, restart the bridge, and have them try it by voice.
10. **HUMAN: keep it running?** Offer to start the bridge at login. macOS: a launchd agent in
    `~/Library/LaunchAgents`. Linux: a systemd user service plus `loginctl enable-linger`. In
    both, use the absolute path of `uv` (`command -v uv`), set the working directory to this
    folder, set `PATH` to include the folders holding `uv` and `claude`, and send output to
    `~/.config/voice-dot/bridge.log`. Stop the background bridge from step 4 first, then check
    `/health` again.

Later firmware updates go over Wi-Fi: `./flash.sh ota ROOM`.

## When it does not work

| What you see | Cause and fix |
|---|---|
| No `speaker connected` in the log | The speaker cannot reach the bridge. Bridge not running; computer asleep or on another network; firewall blocking port 7697; `.local` names not resolving (Linux without Avahi); or Wi-Fi name or password wrong (step 6 again, flash again over USB) |
| `insufficient_quota` | The OpenAI key has no credit. HUMAN adds credit |
| `no OpenAI API key` | Step 3 not done, or the file is empty |
| `refused ...: bad path` | The speaker was flashed with an old token or room. Flash again |
| `heard:` lines are garbled or empty | Change `mic_gain` in the config (default 4; try 2 or 6), restart the bridge |
| It answers its own voice or asks twice | Something changed the JSON sent to the device; restore `device_json` in `bridge.py` |
| `ask_claude failed: ...` | The text after it is the agent's own error. Reproduce it: `cd ~/voice-dot && claude -p "hello"` plus the `claude_args` from the config |
| `flash.sh` cannot find the device | Charge-only cable, another program holding the serial port, or on Linux the user is not in the `dialout` group |
| Terminal shows no typing after `flash.sh` was interrupted | Run `stty echo` |

## Another agent

The speaker's brain is `claude -p` by default. To use a different agent, set `agent_command` in
`~/.config/voice-dot/config.json` to a command that takes the request, prints the answer and
exits, with `{prompt}` where the request goes, for example `["codex", "exec", "{prompt}"]`.
Only Claude Code has been tested. `claude_args` and `claude_continue` are ignored when it is set,
so **you** must put that agent's own read-only or sandbox flags in the command, and tell the
human what it is allowed to do.

## Map

- `bridge.py`: the whole server, one file. Its header documents the device protocol.
  `--check`, `--make-secrets` and `--print-url` are its helper commands.
- `firmware/speaker.yaml`: the small per-device config. `firmware/voice-pe-realtime.yaml`:
  vendored upstream firmware, pinned and patched; leave it alone.
- `flash.sh`: reads the speaker's address and token from the bridge's config, then runs ESPHome.
- `README.md`: the human-facing overview, settings table and costs.
