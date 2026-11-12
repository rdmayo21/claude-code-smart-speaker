# Setup prompt

Open Claude Code in this folder (`claude`) and paste this:

```
Set up this smart speaker for me. Read README.md and bridge.py's header first.

1. Check that uv and claude are installed; if uv is missing, tell me the one command to install it.
2. Ask me to put my OpenAI API key in ~/.config/voice-dot/openai-api-key myself (mode 600). Never ask me to paste the key into this chat and never print it.
3. Run the tests: uv run --with pytest pytest -q
4. Start the bridge in the background (uv run bridge.py), logging to ~/.config/voice-dot/bridge.log, and confirm http://127.0.0.1:7697/health answers.
5. Copy firmware/secrets.yaml.example to firmware/secrets.yaml. Ask me for my 2.4 GHz Wi-Fi name, generate the OTA password and API key yourself, and leave the Wi-Fi password marker so flash.sh asks me for it. Never print the bridge token or the secrets.
6. Tell me to plug the Voice PE into this computer with USB-C, then have me run ./flash.sh in my own terminal (it needs me to type the Wi-Fi password). The first compile takes several minutes.
7. When it has flashed, watch the bridge log, ask me to say "Okay Nabu, what time is it?", and tell me what the log shows. If it fails, diagnose from the log before changing anything.
8. Ask me what I want the speaker to be able to do, then set up ~/voice-dot (a CLAUDE.md, and scripts or MCP servers) and add only the tools those need to claude_args in ~/.config/voice-dot/config.json. Show me the allowed list and get my yes before saving it.
9. Offer to keep the bridge running after a reboot (launchd on macOS, a systemd user service on Linux).
```
