# Codex Harness Free Model Router

This repository packages an existing local Codex model switcher and its
fallback skill for public use.

The panel keeps the same dark Codex-style interface. It shows a free fallback
lane, a paid GPT lane, model availability, current status, and a confirmation
before a switch. The included screenshots show the intended experience.

## What it does

- Switches the Codex default between the free daemon and paid GPT models.
- Routes the free daemon to the approved OpenCode Zen free model list.
- Keeps OpenRouter MiniMax M3 Free as a text-only last fallback.
- Keeps OmniRoute available as an optional local route.
- Supports direct free runs when Codex cannot start or has no remaining usage.
- Preserves existing chat model assignments through the included backup script.
- Lets users customize model names, thresholds, paths, ports, and provider
  helpers for their own setup.

Free daemon requests use an outside provider. The normal Codex model does not
receive the request. The skill reports `codex_provider_request=false` and
`codex_request=false` only after the selected route passes its live check.

## Included files

| Path | Purpose |
| --- | --- |
| `skill/SKILL.md` | The fallback skill. |
| `skill/scripts/` | Daemon, router, model switch, and app restart helpers. |
| `skill/web/model_switcher_server.py` | The local host website. |
| `docs/screenshots/` | UI showcase images. |

## Free model list

| Provider | Friendly name | Use |
| --- | --- | --- |
| OpenCode Zen | Muse Spark 1.3 | Default daemon model |
| OpenCode Zen | MiMo V2.5 | Free daemon choice |
| OpenCode Zen | DeepSeek V4 Flash | Free daemon choice |
| OpenCode Zen | Nemotron 3 Ultra | Free daemon choice |
| OpenCode Zen | Muse Spark 1.2 | Free daemon choice |
| OpenRouter | MiniMax M3 Free | Text-only last fallback |
| OmniRoute | Auto Best Coding | Optional; may select paid models |

The OpenCode list and model IDs are in the skill daemon and router files. Use
friendly names in instructions and user messages. Use IDs only in commands and
configuration.

## Fallback rules

- Five-hour fallback starts at 5% or less remaining when weekly remaining usage
  is below 25% and above 10%.
- Weekly remaining usage at 10% or less is a safety stop by default.
- An explicit user override can start the weekly fallback for that task.
- Missing or unclear usage data fails closed.

## Start the local website

Run these commands from the `skill` directory:

```bash
cd skill
./scripts/start_model_switcher.sh
```

Open `http://127.0.0.1:8791` in the Codex built-in browser or another local
browser. Set `MODEL_SWITCHER_PORT` to use another port.

The website reads the local Codex configuration and the daemon status. It does
not put keys in the repository. Set up provider keys through the provider's
supported environment variable or credential helper. If an agent scans for
keys, it must ask first and must not print or save the key.

## Start the free daemon

```bash
cd skill
./scripts/start_codex_muse_daemon.sh
```

The daemon binds to `127.0.0.1:4242` by default. Set
`CODEX_DAEMON_HOST` and `CODEX_DAEMON_PORT` to change the local address. The
daemon forwards the approved model request to OpenCode Zen and keeps the Codex
harness route active.

## Run without Codex

Use the included runner with an approved provider credential:

```bash
cd skill
./scripts/run_free_fallback.sh opencode muse-spark-1.3-contributor-free "your task"
```

The same runner accepts the OpenRouter MiniMax M3 Free model. It does not call
Codex. It cannot provide every Codex tool action, so use daemon mode when the
harness workflow is required.

## Public-release privacy

This repository contains no account IDs, personal paths, usernames, device
details, cookies, private prompts, or API keys. All path and app settings use
portable defaults or environment variables. Read [`SECURITY.md`](SECURITY.md)
before using real credentials.
