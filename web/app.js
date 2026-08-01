// localflow — logique de l'interface.
// `window.pywebview.api` expose les méthodes de la classe Api (ui.py).

let state = null;
let draft = {};          // config en cours d'édition
let recording = false;   // capture d'un nouveau raccourci

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- traduction
//
// Le catalogue arrive par le pont Python plutôt que par un `fetch` : la page
// vient d'un fichier local, et le moteur de rendu refuse un `fetch` depuis
// l'origine `file://`.

let STRINGS = {};
let LANG = "en";
let LOCALE = "en-US";

function t(key, params) {
  let text = STRINGS[key];
  if (text === undefined) return key;    // clé absente : visible, jamais muette
  if (params) {
    Object.entries(params).forEach(([name, value]) => {
      text = text.split("{" + name + "}").join(value);
    });
  }
  return text;
}

function plural(n, oneKey, manyKey, params) {
  return t(n > 1 ? manyKey : oneKey, Object.assign({ n: n }, params));
}

function applyLocale() {
  document.documentElement.lang = LANG;
  LOCALE = LANG === "fr" ? "fr-FR" : "en-GB";
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  // Réservé aux textes du catalogue, qui portent quelques balises de mise en
  // forme. Jamais utilisé pour du contenu venu d'une dictée.
  document.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}

function localeNumber(value) {
  return value.toLocaleString(LOCALE);
}

function localeDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(LOCALE, {
    weekday: "long", day: "numeric", month: "long",
  });
}

function localeHour(hour) {
  const padded = String(hour).padStart(2, "0");
  return LANG === "fr" ? `${padded} h` : `${padded}:00`;
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 2200);
}

// ----------------------------------------------------------------- navigation

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("page-" + btn.dataset.page).classList.add("active");
    if (btn.dataset.page === "etat") { refreshState(); loadStatus(); }
    if (btn.dataset.page === "historique") loadHistory();
    if (btn.dataset.page === "usage") loadUsage();
    if (btn.dataset.page === "voix") loadVoice();
  });
});

// ----------------------------------------------------------------- historique

let historyRequest = 0;

async function loadHistory() {
  // Chaque frappe dans la recherche relance un appel : seule la réponse du
  // dernier compte, une plus lente arrivée après ne doit pas l'écraser.
  const request = ++historyRequest;
  const query = $("search").value.trim();
  const entries = await window.pywebview.api.get_history(query);
  if (request !== historyRequest) return;
  const list = $("history-list");
  list.innerHTML = "";

  $("history-empty").hidden = entries.length > 0 || query !== "";
  $("history-count").textContent = entries.length
    ? plural(entries.length, "history.count_one", "history.count_many")
    : (query ? t("history.no_result") : "");

  entries.forEach((entry) => {
    list.appendChild(historyRow(entry));
  });
}

function historyRow(entry) {
  const div = document.createElement("div");
  div.className = "entry";

  const head = document.createElement("div");
  head.className = "entry-head";
  const when = document.createElement("span");
  when.textContent = entry.time;
  const words = document.createElement("span");
  words.textContent = "· " + entry.words + " "
    + t(entry.words > 1 ? "history.word_many" : "history.word_one");
  const seconds = document.createElement("span");
  seconds.textContent = "· " + entry.seconds + "s";

  const actions = document.createElement("span");
  actions.className = "entry-actions";
  const copy = document.createElement("button");
  copy.className = "icon-btn";
  copy.textContent = t("history.copy");
  copy.addEventListener("click", async () => {
    const res = await window.pywebview.api.copy_text(entry.text);
    toast(res.ok ? t("history.copied")
                 : t("history.copy_failed", { error: res.error }));
  });
  const remove = document.createElement("button");
  remove.className = "icon-btn danger";
  remove.textContent = t("history.delete");
  remove.addEventListener("click", async () => {
    await window.pywebview.api.delete_entry(entry.index);
    loadHistory();
  });
  actions.append(copy, remove);
  head.append(when, words, seconds, actions);

  // Texte posé en propriété, jamais interpolé dans du HTML : une dictée peut
  // contenir n'importe quels caractères.
  const body = document.createElement("div");
  body.className = "entry-text";
  body.textContent = entry.text;

  div.append(head, body);
  return div;
}

$("search").addEventListener("input", () => loadHistory());

$("clear-history").addEventListener("click", async () => {
  if (!confirm(t("history.confirm_clear"))) return;
  await window.pywebview.api.clear_history();
  loadHistory();
  toast(t("history.cleared"));
});

// ------------------------------------------------------------------ réglages

function fillSettings(cfg) {
  draft = Object.assign({}, cfg);

  $("hotkey-box").textContent = cfg.hotkey;
  document.querySelectorAll("#mode-choices .choice").forEach((c) => {
    c.classList.toggle("selected", c.dataset.value === cfg.mode);
  });

  // Le backend whisper n'est pas embarqué dans l'exécutable : l'option est
  // retirée plutôt que de laisser choisir un moteur qui ne chargera jamais.
  // Elle reste visible si la config l'utilise déjà (éditée à la main).
  const whisperOption = document.querySelector('#backend option[value="whisper"]');
  if (whisperOption) {
    const unavailable = state.frozen && cfg.backend !== "whisper";
    whisperOption.hidden = unavailable;
    whisperOption.disabled = unavailable;
  }

  $("backend").value = cfg.backend;
  $("language").value = cfg.language;
  $("ui_language").value = cfg.ui_language;
  $("paste_mode").value = cfg.paste_mode;
  $("overlay").checked = cfg.overlay;
  $("history_size").value = cfg.history_size;
  $("max_dictation_s").value = cfg.max_dictation_s;
  $("startup").checked = state.startup;

  const select = $("input_device");
  select.innerHTML = "";
  state.devices.forEach((dev) => {
    const opt = document.createElement("option");
    opt.value = dev.index === null ? "" : String(dev.index);
    opt.textContent = dev.name || t("settings.default_device");
    select.appendChild(opt);
  });
  select.value = cfg.input_device === null ? "" : String(cfg.input_device);

  renderReplacements(cfg.replacements || {});

  const hint = $("hotkey-hint");
  hint.innerHTML = "";
  const key = document.createElement("kbd");
  key.textContent = cfg.hotkey;
  hint.append(document.createTextNode(t("state.dictation") + " "), key,
              document.createElement("br"),
              document.createTextNode(
                t(cfg.mode === "hold" ? "state.hold" : "state.toggle")));
}

function renderReplacements(map) {
  const host = $("replacements");
  host.innerHTML = "";
  Object.entries(map).forEach(([from, to]) => addReplacementRow(from, to));
  if (!Object.keys(map).length) addReplacementRow("", "");
}

function addReplacementRow(from, to) {
  // Champs construits en DOM et remplis par propriété : interpolées dans du
  // HTML, les valeurs contenant un guillemet sortiraient de l'attribut et
  // injecteraient du balisage.
  const row = document.createElement("div");
  row.className = "replacement";
  const source = document.createElement("input");
  source.type = "text";
  source.placeholder = t("settings.heard");
  source.value = from;
  const target = document.createElement("input");
  target.type = "text";
  target.placeholder = t("settings.corrected_to");
  target.value = to;
  const remove = document.createElement("button");
  remove.className = "icon-btn danger";
  remove.textContent = "✕";
  remove.addEventListener("click", () => row.remove());
  row.append(source, target, remove);
  $("replacements").appendChild(row);
}

$("add-replacement").addEventListener("click", () => addReplacementRow("", ""));

document.querySelectorAll("#mode-choices .choice").forEach((choice) => {
  choice.addEventListener("click", () => {
    document.querySelectorAll("#mode-choices .choice")
      .forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    draft.mode = choice.dataset.value;
  });
});

// Capture du raccourci : on écoute les vraies touches plutôt que de demander
// à l'utilisateur d'écrire « ctrl+win » à la main.
const KEY_NAMES = { Control: "ctrl", Alt: "alt", Shift: "shift", Meta: "win" };

$("hotkey-record").addEventListener("click", startRecording);
$("hotkey-box").addEventListener("click", startRecording);

function startRecording() {
  recording = true;
  $("hotkey-box").classList.add("recording");
  $("hotkey-box").textContent = t("settings.recording");
}

document.addEventListener("keydown", (event) => {
  if (!recording) return;
  event.preventDefault();

  const parts = [];
  if (event.ctrlKey) parts.push("ctrl");
  if (event.altKey) parts.push("alt");
  if (event.shiftKey) parts.push("shift");
  if (event.metaKey) parts.push("win");

  let key = KEY_NAMES[event.key] || event.key.toLowerCase();
  if (key === " ") key = "space";
  if (key === "escape") {           // échappement : on annule
    recording = false;
    $("hotkey-box").classList.remove("recording");
    $("hotkey-box").textContent = draft.hotkey;
    return;
  }

  // Tant que seuls des modificateurs sont enfoncés, on attend la vraie touche —
  // sauf si la combinaison EST un modificateur (ctrl+win), auquel cas le
  // dernier modificateur pressé fait office de déclencheur.
  const isModifier = Object.values(KEY_NAMES).includes(key);
  if (isModifier) {
    const withoutSelf = parts.filter((p) => p !== key);
    if (!withoutSelf.length) return;      // un seul modificateur : on attend
    finishRecording(withoutSelf.concat(key).join("+"));
    return;
  }
  if (!parts.length) return;              // une touche seule ne fait pas un raccourci
  finishRecording(parts.concat(key).join("+"));
}, true);

function finishRecording(hotkey) {
  recording = false;
  draft.hotkey = hotkey;
  $("hotkey-box").classList.remove("recording");
  $("hotkey-box").textContent = hotkey;
}

$("save-config").addEventListener("click", async () => {
  const map = {};
  document.querySelectorAll("#replacements .replacement").forEach((row) => {
    const [from, to] = row.querySelectorAll("input");
    if (from.value.trim()) map[from.value.trim()] = to.value;
  });

  const device = $("input_device").value;
  const cfg = Object.assign({}, draft, {
    backend: $("backend").value,
    language: $("language").value,
    ui_language: $("ui_language").value,
    paste_mode: $("paste_mode").value,
    overlay: $("overlay").checked,
    history_size: parseInt($("history_size").value, 10),
    max_dictation_s: parseFloat($("max_dictation_s").value),
    input_device: device === "" ? null : parseInt(device, 10),
    replacements: map,
  });

  const res = await window.pywebview.api.save_config(cfg);
  $("config-error").hidden = res.ok;
  $("config-saved").hidden = !res.ok;
  if (!res.ok) {
    $("config-error").textContent = res.error;
    return;
  }
  setTimeout(() => { $("config-saved").hidden = true; }, 2500);

  const startup = await window.pywebview.api.set_startup($("startup").checked);
  if (!startup.ok) toast(t("settings.startup_failed", { error: startup.error }));

  // La langue a pu changer : on relit tout et on repeint, plutôt que de
  // laisser la fenêtre à moitié dans l'ancienne langue.
  await refreshState();
  fillSettings(state.config);
  toast(t("settings.applied"));
});

// --------------------------------------------------------------- statistiques

async function loadUsage() {
  const u = await window.pywebview.api.get_usage();
  const empty = u.dictations === 0;
  $("usage-empty").hidden = !empty;
  $("usage-body").hidden = empty;
  if (empty) return;

  // La jauge sature à 200 mots/min : au-delà, c'est du texte lu, pas dicté.
  $("wpm-arc").style.setProperty("--fill", Math.min(u.wpm / 200, 1) * 0.5 + "turn");
  $("wpm-value").textContent = u.wpm;

  $("u-words").textContent = localeNumber(u.words);
  $("u-words-note").textContent = t(
    u.fixes ? "usage.summary_fixes" : "usage.summary",
    { dictations: localeNumber(u.dictations), minutes: u.minutes,
      fixes: u.fixes });

  $("u-streak").textContent = u.streak;
  $("u-streak-note").textContent = u.best_streak
    ? t("usage.record", { n: u.best_streak })
    : "";

  renderCalendar(u.calendar);
  renderApps(u.apps);
  renderHours(u.hours);
}

function renderCalendar(days) {
  const host = $("calendar");
  host.innerHTML = "";
  const peak = Math.max(...days.map((d) => d.words), 1);

  let week = document.createElement("div");
  week.className = "cal-week";
  days.forEach((day) => {
    const cell = document.createElement("div");
    const ratio = day.words / peak;
    const level = day.words === 0 ? 0
      : ratio > 0.6 ? 4 : ratio > 0.35 ? 3 : ratio > 0.15 ? 2 : 1;
    cell.className = "cal-day" + (level ? " l" + level : "");
    // La donnée d'abord, la date ensuite : c'est le chiffre qu'on cherche en
    // survolant une case, pas le jour qu'on désigne déjà du curseur.
    cell.title = day.words + " "
      + t(day.words > 1 ? "history.word_many" : "history.word_one")
      + " — " + localeDate(day.date);
    week.appendChild(cell);
    // Sept cases par colonne : une colonne = une semaine, comme un calendrier.
    if (week.children.length === 7) {
      host.appendChild(week);
      week = document.createElement("div");
      week.className = "cal-week";
    }
  });
  if (week.children.length) host.appendChild(week);
  host.scrollLeft = host.scrollWidth;
}

function renderApps(apps) {
  const host = $("apps");
  host.innerHTML = "";
  $("apps-help").hidden = apps.length > 0;
  if (!apps.length) {
    const note = document.createElement("p");
    note.className = "help";
    note.textContent = t("usage.where_empty");
    host.appendChild(note);
    return;
  }
  const peak = Math.max(...apps.map((a) => a.words), 1);
  apps.forEach((app) => {
    const row = document.createElement("div");
    row.className = "bar-row";

    const name = document.createElement("span");
    name.className = "bar-name";
    name.textContent = app.app;

    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = Math.max(6, (app.words / peak) * 100) + "%";
    fill.textContent = app.share + " %";
    track.appendChild(fill);

    const count = document.createElement("span");
    count.className = "bar-count";
    count.textContent = localeNumber(app.words) + " " + t("usage.words_short");

    row.append(name, track, count);
    host.appendChild(row);
  });
}

function renderHours(hours) {
  const host = $("hours");
  host.innerHTML = "";
  const peak = Math.max(...hours, 1);
  hours.forEach((words, hour) => {
    const col = document.createElement("div");
    col.className = "hour";
    const bar = document.createElement("div");
    bar.className = "hour-bar";
    bar.style.height = Math.round((words / peak) * 100) + "%";
    bar.style.opacity = words ? 0.85 : 0.18;
    col.title = localeNumber(words) + " " + t("usage.words_short") + " — "
      + t("usage.around", { hour: localeHour(hour) });
    col.appendChild(bar);
    host.appendChild(col);
  });
}

// ----------------------------------------------------------------------- voix

async function loadVoice() {
  const v = await window.pywebview.api.get_voice();
  $("voice-empty").hidden = !v.empty;
  $("voice-body").hidden = v.empty;
  if (v.empty) return;

  $("portrait").textContent = v.profile;

  const traits = $("traits");
  traits.innerHTML = "";
  v.traits.forEach((trait) => {
    const div = document.createElement("div");
    div.className = "trait";
    const value = document.createElement("span");
    value.className = "trait-value";
    value.textContent = trait.value;
    const label = document.createElement("span");
    label.className = "trait-label";
    label.textContent = trait.label;
    div.append(value, label);
    traits.appendChild(div);
  });

  const phrase = $("catchphrase");
  phrase.innerHTML = "";
  if (v.catchphrase) {
    const em = document.createElement("em");
    em.textContent = LANG === "fr"
      ? "« " + v.catchphrase.text + " »"
      : "“" + v.catchphrase.text + "”";
    phrase.append(em, document.createTextNode(
      t("voice.catchphrase_count", { n: v.catchphrase.count })));
  } else {
    phrase.textContent = t("voice.catchphrase_none");
  }

  const peak = $("peak");
  if (v.peak_hour === null || v.peak_hour === undefined) {
    peak.textContent = t("voice.peak_none");
  } else {
    const hour = localeHour(v.peak_hour);
    peak.textContent = v.peak_app
      ? t("voice.peak_at_in", { hour: hour, app: v.peak_app })
      : t("voice.peak_at", { hour: hour });
  }

  renderChips($("top-words"), v.top_words);
  $("fillers-card").hidden = !v.fillers.length;
  renderChips($("fillers"), v.fillers);
}

function renderChips(host, items) {
  host.innerHTML = "";
  items.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    const word = document.createElement("b");
    word.textContent = item.word;
    const count = document.createElement("span");
    count.textContent = item.count + " " + t("voice.times");
    chip.append(word, count);
    host.appendChild(chip);
  });
}

// ----------------------------------------------------------------------- état

async function refreshState() {
  state = await window.pywebview.api.get_state();

  if (state.lang !== LANG || !Object.keys(STRINGS).length) {
    LANG = state.lang;
    STRINGS = state.strings || {};
    applyLocale();
  }

  $("brand-dot").className = "dot " + (state.running ? "on" : "off");
  $("brand-state").textContent = t(state.running ? "state.running"
                                                 : "state.stopped");

  if (state.configError) {
    $("config-error").hidden = false;
    $("config-error").textContent = state.configError;
  }
  return state;
}

// Relevé de santé : chaque ligne répond à une question qu'on se pose quand la
// dictée ne marche pas. Les compteurs d'usage vivent dans Statistiques, ils
// n'ont rien à faire ici.
async function loadStatus() {
  const s = await window.pywebview.api.get_status();
  const cards = [];

  cards.push({
    state: s.running ? "ok" : "off",
    label: t("status.daemon"),
    value: s.running ? t("status.running_for", { uptime: s.uptime })
                     : t("status.stopped"),
    meta: s.running
      ? [t("status.since", { time: s.startedAt }), "PID " + s.pid]
      : [],
  });

  const model = {
    ready: ["ok", s.model.name,
            [t("status.model_loaded_in", { n: s.model.seconds })]],
    loading: ["warn", t("status.model_loading"), []],
    failed: ["off", t("status.model_failed"), []],
    off: ["off", t("status.model_off"), []],
  }[s.model.state];
  cards.push({ state: model[0], label: t("status.model"),
               value: model[1], meta: model[2] });

  if (s.running) {
    cards.push({
      state: "", label: t("status.memory"),
      value: t("status.memory_value", { n: localeNumber(s.memoryMb) }),
      meta: [t("status.memory_note")],
    });
  }

  cards.push({
    state: s.microphone ? "ok" : "warn",
    label: t("status.mic"),
    value: s.microphone || t("status.mic_unknown"),
    meta: [],
  });

  cards.push({
    state: "", label: t("status.shortcut"), value: s.hotkey,
    meta: [t(s.mode === "hold" ? "state.hold" : "state.toggle")],
  });

  cards.push({
    state: s.last ? "ok" : "warn",
    label: t("status.last"),
    value: s.last ? t("status.last_value", { words: s.last.words,
                                             seconds: s.last.seconds })
                  : t("status.no_dictation"),
    meta: s.last
      ? [s.last.time].concat(s.last.latency !== null
          ? [t("status.latency", { n: s.last.latency })] : [])
      : [],
  });

  const host = $("health");
  host.innerHTML = "";
  cards.forEach((card) => {
    const box = document.createElement("div");
    box.className = "health-card";

    const head = document.createElement("div");
    head.className = "health-head";
    const dot = document.createElement("span");
    dot.className = "health-dot" + (card.state ? " " + card.state : "");
    const label = document.createElement("span");
    label.className = "health-label";
    label.textContent = card.label;
    head.append(dot, label);

    const value = document.createElement("div");
    // La valeur prend la couleur de l'alerte : un service arrêté doit se voir
    // sans avoir à interpréter la pastille.
    value.className = "health-value"
      + (card.state === "off" || card.state === "warn" ? " " + card.state : "");
    value.textContent = card.value;

    box.append(head, value);
    if (card.meta.length) {
      const meta = document.createElement("div");
      meta.className = "health-meta";
      meta.textContent = card.meta.join(" · ");
      box.appendChild(meta);
    }
    host.appendChild(box);
  });

  const warning = $("status-warning");
  warning.hidden = !s.duplicates;
  if (s.duplicates) {
    warning.textContent = t("status.duplicates", { n: s.duplicates });
  }

  $("log").textContent = s.log;
}

$("restart-daemon").addEventListener("click", async () => {
  const res = await window.pywebview.api.restart_daemon();
  toast(res.ok ? t("status.restarted")
               : t("status.restart_failed", { error: res.error }));
  setTimeout(() => { refreshState(); loadStatus(); }, 3000);
});

$("open-log").addEventListener("click", () => window.pywebview.api.open_log());

$("open-folder").addEventListener("click", () => window.pywebview.api.open_folder());

// -------------------------------------------------------------- démarrage

window.addEventListener("pywebviewready", async () => {
  await refreshState();
  fillSettings(state.config);
  loadHistory();
  // Pas de loadStatus() ici : la table des processus se paie en centaines de
  // millisecondes, et l'onglet État se recharge de toute façon à son ouverture.
});
