let state = null;
let lane = null;

const $ = (id) => document.getElementById(id);

const AVAILABILITY_LABELS = {
  ok: "available",
  rate_limited: "rate limited (429)",
  error: "error",
  unknown: "unknown",
};

function availabilityFor(item) {
  if (item.lane !== "free") return null;
  return item.availability ? AVAILABILITY_LABELS[item.availability.status] || "unknown" : "not checked";
}

function availabilityKind(item) {
  const status = item.availability ? item.availability.status : null;
  if (status === "ok") return "avail-ok";
  if (status === "rate_limited" || status === "error") return "avail-bad";
  return "avail-unknown";
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function showBanner(message, kind = "") {
  const banner = $("banner");
  banner.textContent = message;
  banner.className = `banner show ${kind}`.trim();
}

function providerById(id) {
  return state.providers.find((item) => item.id === id) || { name: id, connected: false };
}

function modelById(id) {
  return state.models.find((item) => item.id === id);
}

function activeModel() {
  return state.active_model_id ? modelById(state.active_model_id) : null;
}

function pill(text, kind) {
  const element = document.createElement("span");
  element.className = `pill ${kind}`;
  element.textContent = text;
  return element;
}

function renderStatus() {
  const active = activeModel();
  const currentText = active ? active.name : "no model configured";
  const currentLane = active ? active.lane : lane;
  const pills = [
    pill("local panel: ready", "ok"),
    pill(`current: ${currentText} (${currentLane})`, "accent"),
    pill(state.fallback.free_only ? "free-only: on" : "free-only: off", state.fallback.free_only ? "ok" : "bad"),
  ];
  if (active && active.lane === "free" && active.availability) {
    pills.push(pill(`availability: ${availabilityFor(active)}`, availabilityKind(active) === "avail-ok" ? "ok" : availabilityKind(active) === "avail-bad" ? "bad" : "accent"));
  }
  pills.push(pill("all settings customizable", "accent"));
  $("statusbar").replaceChildren(...pills);
}

function renderLanes() {
  document.querySelectorAll(".lane").forEach((button) => button.classList.toggle("active", button.dataset.lane === lane));
  $("model-heading").textContent = lane === "free" ? "Free models" : "Paid models";
  $("model-caption").textContent = lane === "free"
    ? "Add the models that you want to use. The list starts empty."
    : "Add paid models only if you want a paid route. The list starts empty.";
}

function renderModels() {
  const rows = $("model-rows");
  rows.replaceChildren();
  const items = state.models.filter((item) => item.lane === lane);
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.textContent = "No models configured. Add a provider and model below.";
    row.append(cell);
    rows.append(row);
    return;
  }
  for (const item of items) {
    const row = document.createElement("tr");
    const modelCell = document.createElement("td");
    modelCell.className = "model";
    const strong = document.createElement("strong");
    strong.textContent = item.name;
    const id = document.createElement("span");
    id.textContent = item.id;
    modelCell.append(strong, id);
    const providerCell = document.createElement("td");
    providerCell.className = "provider";
    providerCell.textContent = providerById(item.provider_id).name;
    const routeCell = document.createElement("td");
    routeCell.className = "route";
    routeCell.textContent = item.lane === "free" ? "local daemon" : "provider route";
    const statusCell = document.createElement("td");
    const provider = providerById(item.provider_id);
    statusCell.className = provider.connected ? "status-ready" : "status-warn";
    statusCell.textContent = provider.connected ? "key ready" : "needs key";
    const availabilityCell = document.createElement("td");
    const availability = availabilityFor(item);
    if (availability === null) {
      availabilityCell.className = "status-off";
      availabilityCell.textContent = "provider route";
    } else {
      availabilityCell.className = availabilityKind(item);
      availabilityCell.textContent = availability;
      availabilityCell.title = item.availability && item.availability.source === "request" ? "From the latest request through the daemon" : "From the latest availability check";
    }
    const actionCell = document.createElement("td");
    const button = document.createElement("button");
    button.className = "button quiet";
    button.textContent = item.id === state.active_model_id ? "Active" : "Switch";
    const rateLimited = availability === "rate limited (429)";
    button.disabled = item.id === state.active_model_id || rateLimited;
    if (rateLimited) button.title = "The provider answered 429 Too Many Requests. This model is not available right now.";
    button.addEventListener("click", () => switchModel(item));
    actionCell.append(button);
    row.append(modelCell, providerCell, routeCell, availabilityCell, statusCell, actionCell);
    rows.append(row);
  }
}

function fillSelect(selectId, items, valueKey, labelKey, emptyText) {
  const select = $(selectId);
  select.replaceChildren();
  if (!items.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = emptyText;
    select.append(option);
    return;
  }
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.append(option);
  }
}

function renderControls() {
  fillSelect("key-provider", state.providers, "id", "name", "Add a provider first");
  fillSelect("model-provider", state.providers, "id", "name", "Add a provider first");
  const selectedProvider = providerById($("key-provider").value);
  $("provider-status").textContent = selectedProvider.connected
    ? `Connected through ${selectedProvider.key_source}.`
    : "Not connected. No approved key scan has found a key.";
  $("five-hour-rule").value = state.fallback.five_hour_remaining_max;
  $("weekly-rule").value = state.fallback.weekly_remaining_max;
  $("weekly-gate-rule").value = state.fallback.five_hour_weekly_gate_max;
  $("weekly-stop").checked = state.fallback.weekly_stop_enabled;
  $("free-only").checked = state.fallback.free_only;
  $("daemon-port").value = state.app.daemon_port;
  $("profile-name").value = state.app.profile_name;
}

function render() {
  renderLanes();
  renderStatus();
  renderModels();
  renderControls();
}

async function load() {
  state = await api("/api/state");
  // Keep the lane that the user is viewing. Only the first load follows the
  // configured active lane, so a refresh never jumps back to the other tab.
  lane = lane || (state.active_lane === "paid" ? "paid" : "free");
  render();
}

async function checkAvailability() {
  if (!window.confirm("Check availability now? The daemon sends one tiny request to your provider.")) return;
  showBanner("Checking model availability...");
  try {
    await api("/api/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    await load();
    showBanner("Availability updated. A 429 result means the model is rate limited right now.", "ok");
  } catch (error) { showBanner(error.message, "error"); }
}

async function saveConfig(config, message) {
  const result = await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved: true, config }),
  });
  state = result.state;
  render();
  showBanner(message, "ok");
}

async function addProvider() {
  const id = $("provider-id").value.trim();
  const name = $("provider-name").value.trim() || id;
  const baseUrl = $("provider-url").value.trim();
  const keyEnv = $("provider-key-env").value.trim();
  if (!id || !baseUrl) return showBanner("Provider ID and base URL are required.", "warn");
  if (state.providers.some((item) => item.id === id)) return showBanner("That provider ID already exists.", "warn");
  if (!window.confirm(`Add provider ${name}? Check the URL before continuing.`)) return;
  const config = JSON.parse(JSON.stringify({ providers: state.providers, models: state.models, fallback: state.fallback, app: state.app }));
  config.providers.push({ id, name, base_url: baseUrl, api_key_env: keyEnv, wire_api: $("provider-wire").value });
  try { await saveConfig(config, "Provider added. Add one of its models next."); } catch (error) { showBanner(error.message, "error"); }
}

async function addModel() {
  const id = $("model-id").value.trim();
  const name = $("model-name").value.trim() || id;
  const providerId = $("model-provider").value;
  if (!id || !providerId) return showBanner("Model ID and provider are required.", "warn");
  if (state.models.some((item) => item.id === id)) return showBanner("That model ID already exists.", "warn");
  if (!window.confirm(`Add model ${name}?`)) return;
  const config = JSON.parse(JSON.stringify({ providers: state.providers, models: state.models, fallback: state.fallback, app: state.app }));
  config.models.push({ id, name, provider_id: providerId, lane: $("model-lane").value, enabled: true, notes: $("model-notes").value.trim() });
  try { await saveConfig(config, "Model added."); } catch (error) { showBanner(error.message, "error"); }
}

async function connectKey() {
  const providerId = $("key-provider").value;
  const key = $("provider-key").value;
  if (!providerId || !key) return showBanner("Choose a provider and enter a key.", "warn");
  if (!window.confirm("Connect this key for the current run? It stays in memory and is not saved.")) return;
  try {
    await api("/api/providers/key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: true, provider_id: providerId, key }) });
    $("provider-key").value = "";
    showBanner("Key connected for this run.", "ok");
    await load();
  } catch (error) { showBanner(error.message, "error"); }
}

async function scanEnvironment() {
  if (!window.confirm("Scan only the key environment names that you configured? Key values will not be shown.")) return;
  try {
    const result = await api("/api/providers/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: true }) });
    showBanner(result.providers_with_keys.length ? `Found keys for: ${result.providers_with_keys.join(", ")}.` : "No configured keys found.", "ok");
    await load();
  } catch (error) { showBanner(error.message, "error"); }
}

async function saveRules() {
  if (!window.confirm("Save the Default Fallback Rules and local app settings?")) return;
  const config = JSON.parse(JSON.stringify({ providers: state.providers, models: state.models, fallback: state.fallback, app: state.app }));
  config.fallback.five_hour_remaining_max = Number($("five-hour-rule").value);
  config.fallback.weekly_remaining_max = Number($("weekly-rule").value);
  config.fallback.five_hour_weekly_gate_max = Number($("weekly-gate-rule").value);
  config.fallback.weekly_stop_enabled = $("weekly-stop").checked;
  config.fallback.free_only = $("free-only").checked;
  config.app.daemon_port = Number($("daemon-port").value);
  config.app.profile_name = $("profile-name").value.trim();
  try { await saveConfig(config, "Default Fallback Rules saved. Everything remains customizable."); } catch (error) { showBanner(error.message, "error"); }
}

async function preview() {
  try {
    const result = await api("/api/decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ five_hour_remaining: $("usage-five").value, weekly_remaining: $("usage-weekly").value, weekly_override: $("weekly-override").checked }) });
    $("decision").textContent = JSON.stringify(result, null, 2);
  } catch (error) { $("decision").textContent = error.message; }
}

async function switchModel(item) {
  if (!window.confirm(`Switch to ${item.name}? This writes a Codex profile.`)) return;
  try {
    const result = await api("/api/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: true, model_id: item.id }) });
    showBanner(`Switched to ${result.model_name}.\nProfile: ${result.profile_path}\nCommand: ${result.codex_command}\n${result.uses_codex_quota ? "This paid route uses provider quota." : "This free route does not use the normal Codex model quota."}`, "ok");
    await load();
  } catch (error) { showBanner(error.message, "error"); }
}

document.querySelectorAll(".lane").forEach((button) => button.addEventListener("click", () => { lane = button.dataset.lane; render(); }));
$("refresh").addEventListener("click", () => load().catch((error) => showBanner(error.message, "error")));
$("check-availability").addEventListener("click", () => checkAvailability());
$("add-provider").addEventListener("click", addProvider);
$("add-model").addEventListener("click", addModel);
$("connect-key").addEventListener("click", connectKey);
$("scan-env").addEventListener("click", scanEnvironment);
$("save-rules").addEventListener("click", saveRules);
$("preview").addEventListener("click", preview);
$("key-provider").addEventListener("change", () => {
  const provider = providerById($("key-provider").value);
  $("provider-status").textContent = provider.connected ? `Connected through ${provider.key_source}.` : "Not connected. No approved key scan has found a key.";
});
load().catch((error) => showBanner(error.message, "error"));

// Poll quietly so availability and the active model stay current after real
// runs. Hidden tabs skip the poll, and failures stay silent.
setInterval(() => {
  if (!document.hidden) load().catch(() => {});
}, 10000);
