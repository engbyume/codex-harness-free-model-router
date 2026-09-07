---
name: codex-harness-free-model-router
description: Help a user configure and use a local Codex model router with their own providers, models, keys, lanes, and fallback policy.
metadata:
  short-description: Configure a personal model fallback route
---

# Codex Harness Free Model Router

This skill is user-configured. It has no provider presets and no model presets.
Do not assume which provider, endpoint, key, model, tool set, or paid service
the user has.

Everything is customizable.

## First setup

Before a first run, ask the user for the values needed for their setup:

1. Provider name and provider ID.
2. Base URL for the provider's OpenAI-compatible endpoint.
3. API style: Responses or Chat Completions.
4. Model ID and friendly model name.
5. Lane: `free` or `paid`.
6. Key environment variable name, if the provider needs a key.
7. Local panel port, daemon port, profile name, and fallback thresholds.

If the user asks for a key scan, get current explicit approval immediately
before the scan. Scan only the configured environment variable names. Never
print, copy, save, commit, or send key values.

The local panel starts empty. It does not invent provider or model settings.

## Default Fallback Rules

The package has generic starting values. The user can change every value.

- Five-hour fallback is eligible when five-hour remaining usage is at or below
  5% and weekly remaining usage is below 25% and above 10%.
- Weekly remaining usage at or below 10% is a safety stop by default.
- A current explicit user request can override the weekly stop for that task.
- Missing or unclear usage data produces `usage_unknown` and stops the route.

Do not silently change the Default Fallback Rules. Explain the current values
and ask the user to change them when their policy is different.

Run the decision helper with sanitized usage values only:

```bash
python3 scripts/decide_default_fallback.py \
  --five-hour-remaining <remaining-percent> \
  --weekly-remaining <remaining-percent> \
  --config <private-config-file>
```

Use `--weekly-override` only when the current user message gives that
permission. An older override does not apply to a new task.

## Route the Codex harness

When a fallback is required:

1. Confirm that the user configured at least one enabled `free` model.
2. Check model availability (below). Do not route onto a rate-limited model.
3. Start `scripts/start_free_model_router_daemon.sh`.
4. Write a profile with `scripts/write_harness_profile.py` or use the panel.
5. Verify the local daemon health endpoint.
6. Start a new Codex process with the generated profile.

The free profile points Codex to the local daemon. The daemon forwards the
request to the user's configured provider and model. The normal Codex model
must not receive the task.

Report `uses_codex_quota=false` only after the route passes a health or request
check. A profile on disk is not proof that Codex loaded it.

## Check availability

A configured model can still be unusable. A `429 Too Many Requests` answer
means the provider's usage window for that model is spent. Do not report such
a model as available and do not route onto it.

Read the daemon's recorded outcomes before switching or routing:

```bash
curl http://127.0.0.1:4242/v1/status
```

For a live check, use the panel's **Check availability** button or send one
probe:

```bash
curl "http://127.0.0.1:4242/v1/status?probe=1"
```

The daemon sends one tiny request (32 output tokens) and records the result.
Probes are throttled to one per model every 2 minutes. Respect that throttle;
do not loop probes against the provider.

Status meanings:

- `ok`: the model answered.
- `rate_limited`: the provider answered 429. The model is not available now.
- `error`: the provider answered with another failure.
- `unknown`: a request-shape or credential problem, which does not prove the
  model is down.

When the active free model is rate limited, say so plainly and help the user
add or select a different configured free model on a separate quota. Never
present a fallback as working when its only model is rate limited.

## Use the route without Codex

When Codex cannot start or has no usable quota, run:

```bash
python3 scripts/run_external_model.py \
  --base-url <provider-base-url> \
  --model-id <model-id> \
  --api-key-env <key-environment-name> \
  "<task>"
```

This path does not call Codex. It may not support the full Codex tool or
document workflow. State the limitation before relying on the result.

## Switch between lanes

The local panel shows the user's configured free and paid models. A switch
requires confirmation and writes no key.

```bash
python3 scripts/select_router_model.sh <configured-model-id>
```

Use the free daemon for a `free` model. Use the provider route for a `paid`
model. Do not label a route as free because its provider name sounds free.
Use the user's configuration and current provider terms.

## Customization rules

Keep these values user-owned and editable:

- provider IDs, names, base URLs, and API styles;
- model IDs, friendly names, lane labels, and enabled state;
- provider and model order;
- Default Fallback Rules;
- local panel and daemon ports;
- Codex profile name and app settings; and
- credential environment variable names.

When a user changes the model catalog, restart the daemon and verify the model
list again. Do not copy another user's catalog into the user's setup.

## Privacy and safety

- Keep the public skill free of provider names, model names, keys, usernames,
  home paths, hostnames, account IDs, cookies, and private prompts.
- Keep keys in a provider credential store or environment variable.
- Bind the panel and daemon to loopback unless the user adds a separate access
  control design.
- Do not scan credentials without current approval.
- Do not reset usage or buy credits.
- Verify live local state before claiming a switch is active.
