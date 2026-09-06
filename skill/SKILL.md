---
name: codex-usage-fallback-router
description: Check Codex usage before substantive work and after task changes, then route new work through the approved OpenRouter, OpenCode, or OmniRoute fallback when the safe thresholds are met.
metadata:
  short-description: Guard Codex usage fallbacks
---

# Codex usage fallback router

Use this skill before every substantive task and at every mid-task re-audit.
Use it again when a task changes files, tools, models, risk, or side effects.

This skill routes new work only. It cannot change a request that Codex already
sent. When a fallback is selected, do not send the task to the normal Codex
model. Use the native free-provider profile when Codex can start, or use the
standalone runner when Codex cannot start.

The external runner can also run without Codex. Use it when Codex is fully
exhausted or cannot start.

The router also has daemon mode. In daemon mode the Codex harness stays active
and model requests go through the local Codex Muse daemon to OpenCode Zen.
When Codex usage hits the five-hour limit, or the user overrides the weekly
stop, the router switches the daemon automatically. No separate override
phrase is required for the automatic five-hour rule.

## Check usage

1. Call the read-only `mcp__codex_app__get_usage_limits` tool.
2. Convert the five-hour and weekly buckets to remaining percentages.
3. Check whether the current user message explicitly overrides the weekly stop.
4. If a required value is missing or unclear, stop and report `usage_unknown`.
5. Do not read, print, copy, or store API keys or other credentials.

Use `scripts/decide_fallback.py` for the threshold decision when possible.
Pass only sanitized percentages to the script.

## Required rules

- If weekly remaining usage is 10% or less, stop all work by default. Do not
  call an external fallback. Report `weekly_safety_stop`.
- If the user explicitly overrides the weekly stop in the current user message,
  start the fallback automatically unless that message says not to fall back
  or names a different action. Use the native Codex Muse daemon profile first.
- Treat a current request that says `run it for real`, `run for real`, `use the
  fallback now`, or `switch to <approved model>` as an explicit override.
- If the current request names an approved model, use that model first. Do not
  require a second override message.
- The five-hour fallback is eligible only when five-hour remaining usage is 5%
  or less and weekly remaining usage is below 25% but above 10%.
- When the five-hour rule is eligible, switch to the native Codex Muse daemon
  automatically: Muse Spark 1.3 through OpenCode Zen (`codex exec --profile
  codex-fallback-muse-spark-1-3`). Do not wait for an explicit override phrase.
- State the active model at the start of the first response in each task, for
  example `Model: Muse Spark 1.3 - free fallback`. The Codex UI labels the
  daemon provider `Free Fallback` and renders external provider models as
  `Custom`, so text identity is the only reliable model signal. Repeat it
  briefly whenever the active model changes mid-task.
- If weekly remaining usage is 25% or more, do not use the five-hour fallback.
- If five-hour remaining usage is above 5%, do not use the five-hour fallback.
- If usage data is unknown, stop and report `usage_unknown`.

The weekly 10%-remaining stop remains the default. A current, explicit user
override permits the automatic fallback for that task. Do not reuse an older
override in a later task.

When a fallback is selected, use the native Codex Muse daemon profile first
when a new Codex process can start. That process uses an outside free model,
so it does not use the normal Codex model quota. If the new Codex process
cannot start, use the standalone runner.

Start the local Codex Muse daemon with `scripts/start_codex_muse_daemon.sh`
(use `--restart` to force a fresh daemon) and use the profile:

`codex exec --profile codex-fallback-muse-spark-1-3`

The daemon listens on 127.0.0.1:4242 and serves every approved OpenCode Zen
free model from one endpoint: Muse Spark 1.3 (default), MiMo V2.5, DeepSeek
V4 Flash, Nemotron 3 Ultra, and Muse Spark 1.2. Its `/v1/models` response
includes `display_name` for each model, and it forwards the Codex Responses
request to OpenCode Zen (`https://opencode.ai/zen/v1/responses`) using the
model named in the request. It removes only recursive schema references that
the upstream rejects.

In `~/.codex/config.toml` the provider label is `Free Fallback` (neutral, so
it covers the whole skill and not one model). The Codex UI cannot render the
per-model display names for external providers - it shows `Custom` for them -
so the daemon appends a model-identity instruction to each model's
instructions and the skill rules require every agent to state its model at the
start of a response.

The approved model catalog lives in `MODELS` in `scripts/codex_muse_daemon.py`
and must stay in sync with `decide_fallback.py` and `run_free_fallback.sh`.
After changing the catalog or default model, restart the daemon: stop the
process on port 4242, then run the start script again. The start script does
not restart a healthy daemon.

### Switching the active free model

The Codex desktop app renders every external-provider model as one `Custom`
entry, so the picker cannot switch between the free models visually. Change
the default instead, then start a new chat:

- Desktop app default: run `scripts/switch_model.sh <model-id or display
  name>` (for example `nemotron-3-ultra-free` or `MiMo V2.5`). It rewrites
the `model` line in `~/.codex/config.toml`, keeps the daemon provider, and
restarts the Codex desktop app so new chats use the chosen model.
- CLI for one task: `codex exec -m <model-id> "task"`. The metadata warning
  for free models is cosmetic; the request still routes through the daemon.
- Web panel: the local model switcher at `http://127.0.0.1:8791` has two
  lanes. Free fallback lists every served free model with its display name;
  GPT (paid) lists the OpenAI models (GPT-6 Astra, GPT-5.6 Sol/Terra/Luna).
  One click writes the config default and restarts what is needed (Muse
  daemon plus the Codex desktop app in fallback mode; the desktop app only in
  GPT mode). Start it with `scripts/start_model_switcher.sh` (or `stop` to
  shut it down); the server is `web/model_switcher_server.py` and binds to
  127.0.0.1 only.

Existing chats follow the lane switch. Codex stores each conversation's model
in `~/.codex/state_5.sqlite` (`threads.model`), so `scripts/apply_thread_models.py`
reconciles old chats with the config default between app quit and relaunch:

- Fallback default: every open chat on the free daemon, and every open GPT
  chat, is repointed at the chosen free model. GPT chats are snapshotted first
  into `~/.codex/.thread_model_backup.json`.
- GPT default: chats in the backup are restored to their original GPT model
  and provider, and the backup is removed.

The restart script (`scripts/restart_codex_app.sh`) runs this automatically on
every switch, enable, and disable, so the web panel, `switch_model.sh`,
`enable_fallback.sh`, and `disable_fallback.sh` all keep old chats in step
without extra plumbing.

## Approved fallback choices

Use only these choices. Do not substitute another model.

### OpenCode free models

Use the external runner:

`scripts/run_free_fallback.sh opencode <model-id>`

Pass the original task through standard input. Use only these model IDs:

- `muse-spark-1.3-contributor-free` (also the native daemon model)
- `mimo-v2.5-free`
- `deepseek-v4-flash-free`
- `nemotron-3-ultra-free`
- `muse-spark-1.2-contributor-free`

### Standalone use without Codex

Run this command from a normal terminal:

`scripts/run_free_fallback.sh opencode muse-spark-1.3-contributor-free "your task"`

The standalone launcher does not call Codex and does not need Codex usage data.
It can run when Codex is fully exhausted or cannot start. It accepts the same
approved OpenCode models and OpenRouter MiniMax M3 Free. Use standard input for
long prompts.

### OpenRouter free model

- Model: `MiniMax M3 Free`
- Model ID: `minimax/minimax-m3:free`
- Use only as the last fallback when OpenCode is unavailable.
- It is text-only in this runner. It cannot continue tool actions.
- Read the key from `OPENROUTER_API_KEY`.
- Never place the key in this skill, a config file, a log, or a prompt.

### Native Codex daemon fallback

Use this profile when Codex can start:

`codex exec --profile codex-fallback-muse-spark-1-3`

It keeps the Codex harness active and sends the model request through the
local Codex Muse daemon to OpenCode Zen Muse Spark 1.3 free. It sets
`codex_harness=true` and `codex_provider_request=false`, so the request does
not use the Codex model quota. The daemon also serves the other approved
OpenCode free models, so the model picker can switch to any of them in a new
session without changing the profile.

If this profile cannot start, do not retry it. Use the standalone launcher.

### OmniRoute

- Route: `Auto Best Coding`
- Local address: `http://127.0.0.1:20128/v1`
- Do not use automatically in free-only fallback mode. It can select paid
  models, so it does not meet the free-model requirement.

Do not use a browser to start OmniRoute. A browser cannot start a local
server. The start script uses OmniRoute's supported background mode with no
browser window. A headless browser may check a local page only if a separate
browser check is needed.

## Route order

When either fallback rule is eligible:

1. If Codex can start, switch the harness to the native Codex Muse daemon and
   restart it. Run `scripts/enable_fallback.sh`, which replaces the
   `~/.codex/config.toml` default with the daemon route (Muse Spark 1.3
   through OpenCode Zen), restarts the daemon on port 4242, and restarts the
   Codex desktop app so new sessions default to the free route. This switch is
   automatic when the limits are hit; no separate override phrase is required.
2. If the daemon profile cannot start, use OpenCode `MiMo V2.5 Free` through
   the standalone launcher.
3. If OpenCode fails, try the remaining approved OpenCode free models.
4. Use OpenRouter `MiniMax M3 Free` through the standalone runner only as a
   text-only last fallback.

Never use OmniRoute automatically in free-only mode.

When usage recovers and Codex can continue on the paid lane again, run
`scripts/disable_fallback.sh` to restore the paid default model and restart
the desktop app. The Muse daemon stays running; it only serves approved free
models.

### Explicit Codex daemon mode

When the user asks to switch the daemon, run `scripts/enable_fallback.sh` and
use this Codex profile:

`codex exec --profile codex-fallback-muse-spark-1-3`

The profile routes through the local Codex Muse daemon, which forwards the
model request to OpenCode Zen Muse Spark 1.3 free. This keeps the Codex
harness active without using the Codex model quota. A request that names a
Muse model (`switch to muse spark 1.3`) selects the same daemon route.
OpenRouter MiniMax M3 Free remains available only as a text-only last
fallback through the standalone runner when OpenCode is unavailable.

Do not retry a failed route more than once. Do not continue when all approved
routes fail.

## Mid-task checks

Repeat the usage check before a new task phase when the phase is long, when
Codex reports a limit, or when the task changes materially.

At every repeat check:

- apply the weekly 10%-remaining stop first unless the current user message
  explicitly overrides it;
- apply the five-hour rule second;
- in daemon mode (automatic or explicit), use the Codex Muse daemon profile
  and set `codex_harness=true`, `codex_provider_request=false`;
- in standalone mode, set `codex_request=false` and use the external runner;
- keep the same route until it fails or the task phase ends;
- record only the decision, model name, and failure reason.

## Direct and OmniRoute OpenCode use

- Direct OpenCode: use `run_free_fallback.sh` with an approved model ID.
- Use the Codex daemon profile for the automatic and explicit daemon routes
  above. Do not route a Muse model through the daemon without checking that
  the daemon serves the requested Muse version first.

## Completion

Report one of these states:

- `codex_continue`
- `external_free_fallback`
- `native_codex_daemon_fallback`
- `native_codex_daemon_failed`
- `weekly_safety_stop`
- `usage_unknown`
- `fallback_unavailable`

Never claim a fallback is active unless the selected service passes its live
health or request check.
