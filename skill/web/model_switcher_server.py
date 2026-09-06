#!/usr/bin/env python3
"""Local web control panel for switching the Codex model lane.

Serves a tiny single-page app on http://127.0.0.1:8791 with two modes:

* Free fallback - the models served by the Codex Muse daemon
  (127.0.0.1:4242). Picking one updates the Codex default, force-restarts the
  Muse daemon, and restarts the Codex desktop app. Open GPT chats are
  temporarily repointed at the free default (with a backup so they can be
  restored) - see scripts/apply_thread_models.py.
* GPT (paid) - the normal OpenAI models. Picking one restores the paid
  default, removes the custom provider, and restarts the desktop app, which
  also restores any GPT chats the fallback had taken over.

The daemon records the real upstream outcome of every request (ok, 429 rate
limited, or error) in ~/.codex/.model_status.json. The panel shows that
availability per model, can trigger an on-demand probe of every model, and
auto-refreshes, so after a chat run on a model the status updates within
seconds.

Standard library only. Listens on 127.0.0.1 and rejects cross-origin POSTs.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("MODEL_SWITCHER_PORT", "8791"))
CODEX_HOME = pathlib.Path(
    os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
).expanduser()
CONFIG = CODEX_HOME / "config.toml"
# The native daemon profile also pins a model; keep it in sync so a switch
# applies to `codex exec --profile codex-fallback-muse-spark-1-3` too.
PROFILE = CODEX_HOME / "codex-fallback-muse-spark-1-3.config.toml"
SKILL_DIR = pathlib.Path(
    os.environ.get(
        "CODEX_FALLBACK_SKILL_DIR",
        str(pathlib.Path(__file__).resolve().parents[1]),
    )
).expanduser()
DAEMON_HOST = os.environ.get("CODEX_DAEMON_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("CODEX_DAEMON_PORT", "4242"))
DAEMON_URL = f"http://{DAEMON_HOST}:{DAEMON_PORT}"
DAEMON_START = os.path.join(str(SKILL_DIR), "scripts", "start_codex_muse_daemon.sh")
APP_RESTART = os.path.join(str(SKILL_DIR), "scripts", "restart_codex_app.sh")
DRY_RUN = os.environ.get("MODEL_SWITCHER_DRYRUN") == "1"

FALLBACK_PROVIDER = "codex_muse_daemon"

# Fallback catalog used only when the daemon is not running, so the page can
# still render. Mirrors MODELS in codex_muse_daemon.py.
FALLBACK_MODELS = [
    {"id": "muse-spark-1.3-contributor-free", "display_name": "Muse Spark 1.3"},
    {"id": "mimo-v2.5-free", "display_name": "MiMo V2.5"},
    {"id": "deepseek-v4-flash-free", "display_name": "DeepSeek V4 Flash"},
    {"id": "nemotron-3-ultra-free", "display_name": "Nemotron 3 Ultra"},
    {"id": "muse-spark-1.2-contributor-free", "display_name": "Muse Spark 1.2"},
]

# Paid OpenAI models available to this account (the ones Codex recommends).
GPT_MODELS = [
    {"id": "gpt-6-astra", "display_name": "GPT-6 Astra"},
    {"id": "gpt-5.6-sol", "display_name": "GPT-5.6 Sol"},
    {"id": "gpt-5.6-terra", "display_name": "GPT-5.6 Terra"},
    {"id": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna"},
]
GPT_IDS = {entry["id"] for entry in GPT_MODELS}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codex Model Switcher</title>
<style>
  :root {
    --bg: #141313;
    --surface: #1e1b1b;
    --border: #332d2d;
    --text: #e8e0da;
    --muted: #a89f97;
    --accent: #ee7c37;
    --ok: #a6e3a1;
    --bad: #f38ba8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 40px 20px;
  }
  main { max-width: 780px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 16px; }
  .modebar { display: flex; gap: 8px; margin-bottom: 14px; }
  .modebar button {
    flex: 1;
    padding: 9px 14px;
    font-size: 14px;
    cursor: pointer;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 10px;
    text-align: left;
  }
  .modebar button.active { border-color: var(--accent); }
  .modebar button small { display: block; color: var(--muted); font-size: 12px; }
  .modebar button.active small { color: var(--accent); }
  .pillbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 10px; }
  .pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    border: 1px solid var(--border);
  }
  .pill.ok { color: var(--ok); border-color: var(--ok); }
  .pill.bad { color: var(--bad); border-color: var(--bad); }
  .pill.accent { color: var(--accent); border-color: var(--accent); }
  .pill.muted { color: var(--muted); }
  button.mini {
    margin-left: auto;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 12px;
    cursor: pointer;
  }
  button.mini:hover { border-color: var(--accent); color: var(--accent); }
  button.mini:disabled { opacity: .5; cursor: default; }
  table { width: 100%; border-collapse: collapse; margin-top: 4px; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  td.model strong { display: block; }
  td.model span { color: var(--muted); font-size: 12px; font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  td .tag { font-size: 12px; }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
  .muted { color: var(--muted); }
  .current { color: var(--ok); }
  button.switch {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 5px 14px;
    font-size: 13px;
    cursor: pointer;
  }
  button.switch:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button.switch:disabled { opacity: .4; cursor: default; }
  .row-current button.switch { border-color: var(--ok); color: var(--ok); }
  #banner {
    display: none;
    margin-top: 14px;
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface);
    white-space: pre-wrap;
  }
  #banner.show { display: block; }
  #banner.err { border-color: var(--bad); color: var(--bad); }
  #banner.warn { border-color: var(--accent); color: var(--accent); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 16px; }
</style>
</head>
<body>
<main>
  <h1>Codex Model Switcher</h1>
  <p class="sub">Pick a lane and a model. Existing chats follow the switch; GPT chats return to GPT when you switch back.</p>
  <div class="modebar" id="modebar"></div>
  <div class="pillbar" id="pillbar"></div>
  <table>
    <thead>
      <tr><th>Model</th><th>Availability</th><th>Status</th><th></th></tr>
    </thead>
    <tbody id="rows"><tr><td colspan="4">Loading models...</td></tr></tbody>
  </table>
  <div id="banner"></div>
  <p class="hint" id="hint"></p>
</main>
<script>
const modebar = document.getElementById("modebar");
const pillbar = document.getElementById("pillbar");
const rows = document.getElementById("rows");
const banner = document.getElementById("banner");
const hint = document.getElementById("hint");

let state = null;
let viewMode = null;

const AVAIL = {
  ok: { text: "available", cls: "ok" },
  rate_limited: { text: "rate limited (429)", cls: "bad" },
  error: { text: "error", cls: "bad" },
  unknown: { text: "not checked", cls: "muted" }
};

function setBanner(text, kind) {
  banner.textContent = text;
  banner.className = "show" + (kind ? " " + kind : "");
}
function clearBanner() { banner.className = ""; }

async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    throw new Error((data && data.error) || ("HTTP " + res.status));
  }
  return data;
}

function pill(kind, text) {
  const el = document.createElement("span");
  el.className = "pill " + kind;
  el.textContent = text;
  return el;
}

function availabilityOf(id) {
  const st = state.statuses && state.statuses[id];
  return st ? (st.status || "unknown") : "unknown";
}

function applyState() {
  pillbar.innerHTML = "";
  pillbar.append(pill(state.daemon_healthy ? "ok" : "bad", state.daemon_healthy ? "daemon: healthy" : "daemon: offline"));
  pillbar.append(pill("accent", "current: " + (state.current_display || state.current || "?") + " (" + (state.mode === "fallback" ? "free fallback" : "GPT paid") + ")"));
  const limited = (state.models || []).filter((m) => availabilityOf(m.id) === "rate_limited").length;
  if (limited > 0) {
    pillbar.append(pill("bad", limited + " model" + (limited > 1 ? "s" : "") + " rate limited right now"));
  } else {
    pillbar.append(pill("ok", "no model currently rate limited"));
  }
  const checkBtn = document.createElement("button");
  checkBtn.className = "mini";
  checkBtn.textContent = "Check availability now";
  checkBtn.addEventListener("click", checkAvailability);
  pillbar.append(checkBtn);

  modebar.innerHTML = "";
  for (const mode of ["fallback", "gpt"]) {
    const btn = document.createElement("button");
    if (mode === state.mode) btn.className = "active";
    const label = mode === "fallback" ? "Free fallback" : "GPT (paid)";
    const note = mode === "fallback"
      ? "Muse / MiMo / DeepSeek / Nemotron via OpenCode Zen"
      : "OpenAI models (needs Codex quota)";
    btn.innerHTML = "<strong>" + label + "</strong><small>" + note + "</small>";
    btn.addEventListener("click", () => renderTable(mode));
    modebar.append(btn);
  }
  renderTable(state.mode);
}

function renderTable(mode) {
  viewMode = mode;
  const isFallback = mode === "fallback";
  const list = isFallback ? (state.models || []) : (state.gpt_models || []);
  const current = state.current;
  document.querySelectorAll(".modebar button").forEach((b, i) => {
    b.className = (i === (isFallback ? 0 : 1)) ? "active" : "";
  });
  rows.innerHTML = "";
  for (const m of list) {
    const isCurrent = m.id === current;
    const avail = availabilityOf(m.id);
    const tr = document.createElement("tr");
    if (isCurrent) tr.className = "row-current";

    const tdModel = document.createElement("td");
    tdModel.className = "model";
    const strong = document.createElement("strong");
    strong.textContent = m.display_name || m.id;
    const span = document.createElement("span");
    span.textContent = m.id;
    tdModel.append(strong, span);

    const tdAvail = document.createElement("td");
    const av = AVAIL[avail] || AVAIL.unknown;
    const atag = document.createElement("span");
    atag.className = "tag " + av.cls;
    atag.textContent = av.text;
    if (avail === "unknown" && isFallback) {
      atag.textContent = "checking...";
    }
    tdAvail.append(atag);

    const tdStatus = document.createElement("td");
    tdStatus.className = isCurrent ? "current" : "muted";
    tdStatus.textContent = isCurrent ? "active default" : (isFallback ? "standby" : "");

    const tdAction = document.createElement("td");
    const btn = document.createElement("button");
    btn.className = "switch";
    btn.textContent = isCurrent ? "Active" : "Switch";
    const blocked = isFallback && !isCurrent && (avail === "rate_limited" || avail === "error");
    btn.disabled = isCurrent || blocked;
    btn.title = blocked ? "This model is not available right now (429 / upstream error)." : "";
    btn.addEventListener("click", async () => {
      const name = m.display_name || m.id;
      const msg = isFallback
        ? "Switch to " + name + " (free fallback)?\\nOpen GPT chats move to this model too; they restore when you switch back to GPT. Codex restarts."
        : "Switch to " + name + " (paid GPT)?\\nGPT chats return to their original models. Codex restarts. Needs Codex quota.";
      if (!window.confirm(msg)) return;
      const all = document.querySelectorAll("button.switch");
      all.forEach((b) => { b.disabled = true; });
      setBanner("Switching to " + name + "...\\nThis restarts what is needed and takes a few seconds.", "warn");
      try {
        const out = await api("/api/switch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: m.id }),
        });
        let outMsg = "Switched to " + name + ".";
        if (out.config_written === false) outMsg += "\\nConfig was not changed.";
        if (out.daemon) outMsg += out.daemon.ok
          ? "\\nDaemon: " + (out.daemon.output || "ok")
          : "\\nDaemon restart failed: " + ((out.daemon.error || out.daemon.output) || "unknown");
        if (out.app) outMsg += out.app.ok
          ? "\\nCodex app: " + (out.app.output || "restarted")
          : "\\nCodex app restart failed: " + ((out.app.error || out.app.output) || "unknown");
        if (out.threads) outMsg += "\\nChats: " + JSON.stringify(out.threads);
        if (out.dry_run) outMsg += "\\n(dry run: nothing restarted)";
        setBanner(outMsg, out.ok ? "" : "err");
        await loadState(false);
      } catch (e) {
        setBanner("Failed: " + e.message, "err");
        await loadState(false);
      }
    });
    tdAction.append(btn);
    tr.append(tdModel, tdAvail, tdStatus, tdAction);
    rows.append(tr);
  }
  hint.textContent = state.hint || "";
}

async function loadState(keepBanner) {
  state = await api("/api/state");
  if (!keepBanner) clearBanner();
  applyState();
}

async function checkAvailability() {
  setBanner("Checking each model against OpenCode Zen...\\nModels that answer 429 are shown as unavailable.", "warn");
  try {
    await api("/api/check");
    await loadState(true);
    clearBanner();
  } catch (e) {
    setBanner("Availability check failed: " + e.message, "err");
  }
}

async function boot() {
  try {
    await loadState(false);
    await checkAvailability();
  } catch (e) {
    setBanner("Could not load state: " + e.message, "err");
  }
}

boot();
// Auto-refresh: real chat runs update the daemon's status file, so poll and
// repaint without clearing the banner or re-probing every model.
setInterval(async () => {
  try { await loadState(true); } catch (e) {}
}, 10000);
</script>
</body>
</html>
"""


def fetch_daemon_models():
    try:
        with urllib.request.urlopen(DAEMON_URL + "/v1/models", timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
        entries = data.get("models") or data.get("data") or []
        models = [
            {
                "id": entry.get("id") or entry.get("slug"),
                "display_name": entry.get("display_name") or entry.get("id"),
            }
            for entry in entries
            if entry.get("id") or entry.get("slug")
        ]
        if models:
            return models
    except Exception:
        pass
    return list(FALLBACK_MODELS)


def fetch_model_statuses(probe=False):
    """Read per-model availability recorded by the daemon. With probe=True the
    daemon also sends one tiny request per stale model to test it live."""
    url = DAEMON_URL + "/v1/models/status" + ("?probe=1" if probe else "")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data.get("models") or {}
    except Exception:
        return {}


def config_values():
    try:
        text = open(CONFIG, encoding="utf-8").read()
    except OSError:
        return None, None
    model = re.search(r'(?m)^model\s*=\s*"([^"]*)"', text)
    provider = re.search(r'(?m)^model_provider\s*=\s*"([^"]*)"', text)
    return (
        model.group(1) if model else None,
        provider.group(1) if provider else None,
    )


def set_model_line(text, model_id):
    return re.sub(r'(?m)^model\s*=\s*"[^"]*"$', f'model = "{model_id}"', text, count=1)


def ensure_provider_line(text, provider):
    if re.search(r'(?m)^model_provider\s*=\s*"[^"]*"$', text):
        return re.sub(
            r'(?m)^model_provider\s*=\s*"[^"]*"$',
            f'model_provider = "{provider}"',
            text,
            count=1,
        )
    return re.sub(
        r'(?m)(^model\s*=\s*"[^"]*"$)',
        rf'\1\nmodel_provider = "{provider}"',
        text,
        count=1,
    )


def drop_provider_line(text):
    # Remove the custom provider line so Codex uses the default (OpenAI)
    # provider for GPT models.
    return re.sub(r'(?m)^model_provider\s*=\s*"[^"]*"\s*\n?', "", text, count=1)


def write_config(text):
    open(CONFIG, "w", encoding="utf-8").write(text)


def sync_profile(model_id):
    if not os.path.exists(PROFILE):
        return
    profile_text = open(PROFILE, encoding="utf-8").read()
    profile_text = set_model_line(profile_text, model_id)
    open(PROFILE, "w", encoding="utf-8").write(profile_text)


def apply_fallback_model(model_id):
    text = open(CONFIG, encoding="utf-8").read()
    text = ensure_provider_line(set_model_line(text, model_id), FALLBACK_PROVIDER)
    write_config(text)
    sync_profile(model_id)


def apply_gpt_model(model_id):
    text = open(CONFIG, encoding="utf-8").read()
    text = drop_provider_line(set_model_line(text, model_id))
    write_config(text)


def daemon_healthy():
    try:
        with urllib.request.urlopen(DAEMON_URL + "/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8")).get("healthy", False)
    except Exception:
        return False


def run_script(args):
    """Run a bash script from the skill with the given extra arguments."""
    try:
        result = subprocess.run(
            ["bash", *args], capture_output=True, text=True, timeout=150
        )
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return {
            "ok": result.returncode == 0,
            "exit": result.returncode,
            "output": (detail[-1] if detail else "")[:300],
        }
    except Exception as error:
        return {"ok": False, "error": str(error)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def send_json(self, status, data):
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def send_page(self):
        encoded = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def check_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
        return origin in allowed

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.send_page()
            return
        if path == "/api/state":
            current, provider = config_values()
            models = fetch_daemon_models()
            gpt_models = list(GPT_MODELS)
            statuses = fetch_model_statuses(probe=False)
            if provider == FALLBACK_PROVIDER:
                mode = "fallback"
            else:
                mode = "gpt"
            if current in GPT_IDS:
                mode = "gpt"
            current_display = next(
                (m["display_name"] for m in models + gpt_models if m["id"] == current),
                current,
            )
            self.send_json(
                200,
                {
                    "mode": mode,
                    "models": models,
                    "gpt_models": gpt_models,
                    "statuses": statuses,
                    "current": current,
                    "current_display": current_display,
                    "provider": provider,
                    "daemon_healthy": daemon_healthy(),
                    "dry_run": DRY_RUN,
                    "hint": (
                        "Availability = what the model actually did upstream on "
                        "its last request or probe. 'rate limited (429)' means "
                        "OpenCode Zen is rejecting it right now - it is not "
                        "available. The panel auto-refreshes every 10 seconds, "
                        "so after a chat run on a model its status updates "
                        "within seconds. GPT (paid) needs Codex quota."
                    ),
                },
            )
            return
        if path == "/api/check":
            statuses = fetch_model_statuses(probe=True)
            self.send_json(200, {"ok": True, "statuses": statuses})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/switch":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.check_origin():
            self.send_json(403, {"error": "cross-origin request blocked"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self.send_json(400, {"error": "invalid JSON body"})
            return
        model = body.get("model")
        models = fetch_daemon_models()
        known_free = next((m for m in models if m["id"] == model), None)
        known_gpt = next((m for m in GPT_MODELS if m["id"] == model), None)
        if not known_free and not known_gpt:
            self.send_json(400, {"error": f"unknown model: {model!r}"})
            return

        try:
            if known_free:
                apply_fallback_model(model)
            else:
                apply_gpt_model(model)
        except Exception as error:
            self.send_json(500, {"error": f"could not write config: {error}"})
            return

        if DRY_RUN:
            self.send_json(
                200,
                {
                    "ok": True,
                    "dry_run": True,
                    "model": model,
                    "mode": "fallback" if known_free else "gpt",
                    "daemon": {"ok": True, "output": "skipped (dry run)"},
                    "app": {"ok": True, "output": "skipped (dry run)"},
                },
            )
            return

        # The app restart runs apply_thread_models.py between quit and launch,
        # which moves GPT chats to the free model (fallback) or restores them
        # (GPT mode).
        app = run_script([APP_RESTART])
        if known_free:
            daemon = run_script([DAEMON_START, "--restart"])
        else:
            daemon = {"ok": True, "output": "not needed in GPT mode"}
        print(
            f"[switch] model={model} mode={'fallback' if known_free else 'gpt'} "
            f"daemon_ok={daemon['ok']} app_ok={app['ok']}",
            flush=True,
        )
        self.send_json(
            200,
            {
                "ok": app["ok"] and daemon["ok"],
                "dry_run": False,
                "model": model,
                "mode": "fallback" if known_free else "gpt",
                "daemon": daemon,
                "app": app,
            },
        )


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Codex model switcher listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
