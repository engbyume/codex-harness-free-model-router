# Codex Harness Free Model Router

Use the Codex harness with free models when the normal Codex quota is low or
empty. Switch back to paid GPT models when you want them.

This repository packages the existing local Codex model switcher, its fallback
skill, its daemon helpers, and the local website. The website keeps the same
dark Codex-style layout shown in the screenshots.

## Why use it

The router gives you one simple control point for two lanes:

| Lane | What it does | Usage source |
| --- | --- | --- |
| Free fallback | Sends work through the local daemon to an approved free provider. | Outside provider quota |
| GPT (paid) | Restores the normal Codex model and provider. | Codex or provider quota |

The free lane is useful when you want to keep working after Codex usage is
exhausted. The Codex harness can still provide its normal local work surface,
skills, files, and tools when the selected free route supports them. A direct
standalone run is also available when Codex cannot start.

The router does not claim that a switch worked because a file changed. It
checks the daemon or a real request before it reports a free route as active.

## Screenshots

These images show the packaged website and its main controls.

### Free fallback lane

The free lane shows the daemon state, the active model, and the availability of
each approved free model.

![Free fallback lane](docs/screenshots/model-switcher-free.png)

### Paid GPT lane

The paid lane lists the GPT models that can be restored when Codex usage is
available.

![Paid GPT lane](docs/screenshots/model-switcher-paid.png)

### Model menu

The Codex model menu shows the friendly model names used by the daemon.

![Model menu](docs/screenshots/model-switcher-model-menu.png)

### Switch confirmation

The switch confirmation makes the restart and quota impact clear before the
change is applied.

![Switch confirmation](docs/screenshots/model-switcher-confirmation.png)

## Included files

| Path | Purpose |
| --- | --- |
| `skill/SKILL.md` | The reusable fallback skill. |
| `skill/openai.yaml` | Skill display information and automatic discovery settings. |
| `skill/scripts/codex_muse_daemon.py` | Local Codex Responses bridge for the approved OpenCode free models. |
| `skill/web/model_switcher_server.py` | The local host website. |
| `skill/scripts/decide_fallback.py` | The threshold decision helper. |
| `skill/scripts/start_codex_muse_daemon.sh` | Starts or restarts the free daemon. |
| `skill/scripts/start_model_switcher.sh` | Starts or stops the local website. |
| `skill/scripts/switch_model.sh` | Switches the default free model. |
| `skill/scripts/enable_fallback.sh` | Enables the free daemon route. |
| `skill/scripts/disable_fallback.sh` | Restores the paid route. |
| `docs/screenshots/` | Website showcase images. |

## Approved free models

Use the friendly name in conversation. Use the model ID in a command or
configuration file.

| Provider | Friendly name | Model ID | Role |
| --- | --- | --- | --- |
| OpenCode Zen | Muse Spark 1.3 | `muse-spark-1.3-contributor-free` | Default daemon model |
| OpenCode Zen | MiMo V2.5 | `mimo-v2.5-free` | Free daemon choice |
| OpenCode Zen | DeepSeek V4 Flash | `deepseek-v4-flash-free` | Free daemon choice |
| OpenCode Zen | Nemotron 3 Ultra | `nemotron-3-ultra-free` | Free daemon choice |
| OpenCode Zen | Muse Spark 1.2 | `muse-spark-1.2-contributor-free` | Free daemon choice |
| OpenRouter | MiniMax M3 Free | `minimax/minimax-m3:free` | Text-only last fallback |
| OmniRoute | Auto Best Coding | `auto-best-coding` | Optional local route |

Free model availability can change at the provider. The website records the
last real result and shows rate-limited models as unavailable.

OmniRoute is not used automatically in free-only mode. Its automatic route can
select a paid model. Enable it only when you accept that behavior or have
configured OmniRoute to use free models only.

## Fallback rules

The skill checks the read-only Codex usage data before it sends new work.

### Five-hour fallback

The five-hour fallback starts when both conditions are true:

1. Five-hour remaining usage is 5% or less.
2. Weekly remaining usage is below 25% and above 10%.

This rule does not need a manual override.

### Weekly fallback

Weekly remaining usage at 10% or less is a safety stop by default. The skill
does not call a free provider at that point.

An explicit request in the current task can override that stop. Examples are:

- `Run the fallback for real.`
- `Override the weekly stop for this task.`
- `Switch to Muse Spark 1.2 now.`

The override applies to the current task only. The skill does not reuse an old
override in a later task.

### Unknown usage

If a required usage value is missing or unclear, the skill stops with
`usage_unknown`. It does not guess.

## Route order

When a fallback is eligible, the skill uses this order:

1. Native Codex daemon profile using the selected OpenCode free model.
2. Direct OpenCode free runner if the daemon cannot start.
3. Another approved OpenCode free model if the first model is unavailable.
4. OpenRouter MiniMax M3 Free as a text-only last fallback.

OmniRoute is excluded from this automatic free-only order unless the user
enables it.

## Install and start

The repository is standard-library Python plus shell scripts. It does not need
a database or a JavaScript package install.

Run the website from the `skill` directory:

```bash
cd skill
./scripts/start_model_switcher.sh
```

Open `http://127.0.0.1:8791` in the Codex built-in browser or another browser.
Use `MODEL_SWITCHER_PORT` when port 8791 is already in use.

Start the free daemon in another terminal:

```bash
cd skill
./scripts/start_codex_muse_daemon.sh
```

The daemon uses `127.0.0.1:4242` by default. Use
`CODEX_DAEMON_PORT` when another local service uses that port.

## Switch models

You can switch from the website or the command line.

From the website:

1. Open the Free fallback lane.
2. Select a model with `Switch`.
3. Confirm the change.
4. Start a new Codex chat after the restart.

From the command line:

```bash
cd skill
./scripts/switch_model.sh "Muse Spark 1.2"
```

To enable the free lane:

```bash
cd skill
./scripts/enable_fallback.sh
```

To restore the paid lane:

```bash
cd skill
./scripts/disable_fallback.sh
```

Existing chats can keep a pinned model. The included thread-model helper takes
a backup before it folds GPT chats into the free lane, then restores them when
the paid lane returns.

## Provider credentials

This repository contains no keys. The website also does not display keys.
Configure a provider through its supported credential method before starting
the daemon or runner.

| Provider | Credential input used by the included scripts |
| --- | --- |
| OpenCode Zen | OpenCode's supported auth file, or `OPENCODE_AUTH_FILE`. |
| OpenRouter | `OPENROUTER_API_KEY`. |
| OmniRoute | `OMNIROUTE_ENV_FILE` with the local OmniRoute key entry. |
| Other OpenAI-compatible providers | Add a provider block and a token helper that returns one key only. |

If an agent offers to scan for keys, require a current user approval first. The
agent must not print, save, commit, or send a key. Keep provider credentials in
the provider's credential store or environment, not in this repository.

## Direct use without Codex

When Codex cannot start or has no usable quota, run the standalone fallback
runner:

```bash
cd skill
./scripts/run_free_fallback.sh opencode muse-spark-1.3-contributor-free "your task"
```

For OpenRouter:

```bash
cd skill
./scripts/run_free_fallback.sh openrouter minimax/minimax-m3:free "your task"
```

This path does not call Codex. It is text-oriented and does not provide the
full Codex tool surface. Use daemon mode when you need the Codex harness.

## Customization

The package is intended to be customized. Review the model list and provider
settings before relying on it.

| Setting | Default | How to customize |
| --- | --- | --- |
| Codex home | `~/.codex` | Set `CODEX_HOME`. |
| Website port | `8791` | Set `MODEL_SWITCHER_PORT`. |
| Daemon port | `4242` | Set `CODEX_DAEMON_PORT`. |
| Daemon host | `127.0.0.1` | Keep it on loopback unless you add a separate access-control design. |
| Website log | `/tmp/codex-model-switcher.log` | Set `MODEL_SWITCHER_LOG`. |
| OpenCode auth file | OpenCode default | Set `OPENCODE_AUTH_FILE`. |
| OmniRoute env file | `~/.omniroute/.env` | Set `OMNIROUTE_ENV_FILE`. |
| Codex app process | `ChatGPT.app/Contents/MacOS/ChatGPT` | Set `CODEX_APP_PROCESS`. |
| Codex app name | `ChatGPT` | Set `CODEX_APP_NAME`. |
| Codex app bundle | `com.openai.codex` | Set `CODEX_APP_BUNDLE`. |

When you change the model catalog, keep the model IDs in sync in:

- `skill/scripts/codex_muse_daemon.py`
- `skill/scripts/decide_fallback.py`
- `skill/scripts/run_free_fallback.sh`
- `skill/web/model_switcher_server.py`

Restart the daemon after a catalog or default-model change.

## Troubleshooting

### The website says the daemon is offline

Start the daemon first. Check:

```bash
curl http://127.0.0.1:4242/health
```

### A free model is rate limited

The provider rejected the request. Choose a model marked `available`, or wait
for the provider limit to reset.

### The app did not restart

Set `CODEX_APP_NAME`, `CODEX_APP_BUNDLE`, or `CODEX_APP_PROCESS` for the local
Codex installation. You can also quit and reopen the app, then start a new
chat.

### The new chat still uses the old model

Codex can keep a model on an existing chat. Start a new chat, or run the
included thread-model helper while the app is closed.

### OpenRouter cannot use tools

The OpenRouter fallback is text-only in this package. Use the OpenCode daemon
for the Codex tool workflow.

## Safety and privacy

- The local website and daemon bind to loopback by default.
- The public files contain no API keys, cookies, private prompts, account IDs,
  usernames, or machine-specific paths.
- Key scans require current approval.
- The router fails closed when usage data is unknown.
- The router does not reset usage or purchase credits.
- Check the real daemon or request result before treating a route as active.

Read [`SECURITY.md`](SECURITY.md) before using live credentials.
