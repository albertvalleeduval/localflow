# localflow

*[Version française](README.fr.md)*

Free, local system-wide dictation for Windows: hold a key, speak, release,
and the recognized text is typed into the active field of any application.
Everything runs locally on the CPU — no account, no quota, no audio ever
leaving your machine.

A personal alternative to Wispr Flow, with the same core requirement:
perceived latency must stay under one second no matter how long you dictate.

## How it works

Audio is not transcribed in one block at the end: it is split on silences
*while you speak*, and each chunk is transcribed in the background as you keep
talking. When you release the key, only the tail end of the last sentence
remains to be processed — hence a near-constant latency.

Engine: [`parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
(NVIDIA, 25 languages, native punctuation and capitalization) via `onnx-asr`
in fp32 on CPU. A faster-whisper backend remains available as an option.

## Measured latency

`poc_latency.py` is the benchmark: it replays an audio file into the engine at
real-time pace (100 ms blocks, like a microphone callback) and measures the
time between end of speech and the complete text.

Benchmark machine: 16-thread CPU, no CUDA GPU. Test audio: 68 s of dense
speech without pauses — the worst case for silence-based chunking.

| | Whisper `large-v3-turbo` int8 | Parakeet fp32 |
|---|---|---|
| Cost of one 8 s chunk | ~10 s | 0.65–0.9 s |
| CPU load while dictating | 122% (can't keep up with real time) | ~8% |
| **Latency after key release** | **23.1 s** | **0.11 s** |

Two decisive lessons:

- **Whisper is unsuitable for streaming on CPU.** It encodes a padded 30 s
  window on every call, and the `turbo` encoder is the large model's encoder:
  each chunk costs ~10 s regardless of its size. With dense speech,
  transcription falls behind instead of catching up.
- **Parakeet must run in fp32.** The int8 quantized variant loses all
  punctuation and capitalization. fp32 costs ~2.5 GB of RAM; quality is on par
  with `large-v3-turbo` (96.5% similarity on real human speech).

Streaming chunking does not degrade the text: 98.3% similarity with a
single-block transcription of the same audio.

Upper bound: if the key release lands just before a cut, the last chunk is
12 s long, i.e. ~0.9 s of compute. Perceived latency therefore stays between
0.1 and 1 s regardless of dictation length.

## Installation

Prerequisites: Windows, Python 3.11+.

```
git clone https://github.com/albertvalleeduval/localflow.git
cd localflow
pip install -r requirements.txt
copy config.example.json config.json
```

`pywebview` is only needed for the settings window: the daemon runs without it.

The model downloads itself on first launch (~2.5 GB, cached by HuggingFace).

Set `language` in `config.json` (or later in the Settings tab) to the language
you dictate in — `"en"`, `"fr"`, or any of the 25 languages Parakeet supports.

## Usage

```
python app.py
```

The model loads in a few seconds, then the daemon waits for the hotkey.
Hold `Ctrl+Alt+D`, speak, release: the text is typed into the active field.

Only one instance can run at a time (Windows lock): a second launch exits
immediately and says so in the log. Everything the daemon does is logged to
`localflow.log`, next to the script — useful when it is launched at startup,
without a console.

An icon appears in the notification area. Click: the window — or bring it back
to the foreground if it is already open, never a duplicate. Right-click: open
the window, reload the configuration, open the log, quit. On Windows 11, new
icons land in the overflow area — click the `^` chevron in the taskbar and
drag the localflow icon next to the clock to keep it visible.

### The window

```
python ui.py
```

Five tabs. **History**: every dictation, search, one-click copy. Text is
written there *before* being injected, so a dictation that landed in the wrong
window is always recoverable. **Settings**: hotkey captured by pressing the
keys, mode, language, microphone, corrections, launch at startup.
**Statistics**: throughput, volume, day-by-day regularity, applications and
hours. **My voice**: style portrait, recurring turn of phrase, favorite words.
**Status**: health check, log, daemon restart.

The Status tab answers a single question — "is it working?". One line per
thing to check: service running and for how long, model loaded or not, memory
used, microphone actually in use, hotkey active, last dictation with its
latency. Everything is inferred from the outside (process table, log,
configuration, history), so nothing is asked of the daemon and the answer
stays correct even if it stops responding.

### Two languages, not to be confused

- **`ui_language`** — the language of the window and the tray icon menu.
  English by default, French available, `auto` to follow Windows. Changed in
  Settings, no restart needed.
- **`language`** — the language you **dictate** in. It drives the model and
  the text analysis of My voice: stop words, filler words, elisions.

The two are independent. Someone dictating in French in an English interface
keeps the French stop-word list, otherwise their most-used word would be "de".
Linguistic data exists for French and English; for another dictated language
the degradation is clean — no stop-word filtering or filler counting, all the
rest of the statistics keeps working.

Window translations live in `web/locales/*.json`, tray menu and voice portrait
translations in `i18n.py`. **The log and configuration error messages are not
translated**: they are technical artifacts, and keeping them in English makes
a log pasted into a bug report readable by everyone.

### Statistics and voice portrait, without a language model

`stats.py` does nothing but arithmetic and word counting: no LLM, no network.
That is a project constraint, but also a choice — a count is verifiable and
reproducible, whereas a portrait written by a model is mostly flattering.

The portrait is therefore made of measured observations (sentence length,
vocabulary richness, share of questions, hesitation rate), each compared to a
threshold declared in plain sight at the top of the module. The recurring turn
of phrase is the most frequent n-gram, searched from longest to shortest.
French elisions are detached before counting, otherwise "c'est" and
"j'aimerais" would dominate the word ranking.

Two fields feed these pages: the target application, captured at the start of
the dictation (focus can change while you speak), and the number of applied
corrections. Like everything else, they never leave your machine.

Settings apply without restarting: the daemon watches the modification date of
`config.json` and reloads itself, whether it was edited by the window or by
hand in an editor. Only a change of engine or language requires reloading the
model, which takes a few seconds.

Display goes through `pywebview`, i.e. the WebView2 engine already shipped
with Windows. Same rendering as an Electron app, but without embedding a
second runtime: ~1 MB of dependency instead of ~150 MB, and a single Python
process instead of two plus a bridge. The core of the tool (microphone
capture, keyboard hook, ONNX model, keystroke injection) has to stay in Python
anyway — Electron would have been added, not substituted.

The window runs in its own process: it can crash or be closed without ever
interrupting dictation. It does not talk to the daemon; the two share three
files (`config.json`, `history.jsonl`, `localflow.log`).

### Launch at Windows startup

```
python install_startup.py            installs the shortcut
python install_startup.py --status   checks
python install_startup.py --remove   uninstalls
```

The shortcut points to `pythonw app.py`: no console window, the tool lives in
its tray icon. Allow a dozen seconds after logging in for the model to load
(~2.5 GB of RAM permanently used — the price of fp32).

### Testing each part

Every component can be tested separately:

| Command | What it checks |
|---|---|
| `python config.py` | The effective configuration, setting by setting. |
| `python injector.py` | Both injection modes and clipboard restoration. |
| `python recorder.py 10` | The microphone: VU meter, captured duration, recognized text. |
| `python overlay.py` | The status pill: cycling through the four states. |
| `python tray.py` | The notification icon and its menu. |
| `python stats.py` | Both reports, usage and voice, in the console. |
| `python i18n.py` | The detected language and catalog symmetry. |
| `python poc_latency.py <file.wav>` | Engine latency on a recording. |
| `python make_icon.py` | Regenerates `web/icon.ico`. |

## Configuration

`config.json` is git-ignored: it holds your personal settings. It only needs
to contain the keys that differ from the defaults. `python config.py` prints
the effective configuration.

| Key | Default | Role |
|---|---|---|
| `hotkey` | `ctrl+alt+d` | Dictation hotkey: modifiers (`ctrl`, `alt`, `shift`, `win`) then one key, separated by `+`. The final key is consumed by localflow: it never reaches the active application, so `ctrl+win` does not open the Start menu. |
| `mode` | `hold` | `hold`: keep the key pressed. `toggle`: one press starts, one press stops. |
| `backend` | `parakeet` | `parakeet` (CPU, recommended) or `whisper` (faster-whisper). |
| `model` | `null` | Model name; `null` = backend default. |
| `language` | `fr` | Forced language (set it to yours — see Installation). |
| `replacements` | `{}` | Corrections applied to the final text, `{"pattern": "replacement"}`. |
| `vocabulary` | `""` | Proper nouns passed as `initial_prompt` — `whisper` backend only. |
| `paste_mode` | `clipboard` | `clipboard`: Ctrl+V (fast). `type`: character-by-character typing, for fields that block pasting. |
| `type_delay_ms` | `10` | Pace of the fallback typing. Classic Win32 windows keep up at `0`; some modern apps need much more (see known limits). |
| `min_chunk_s` | `3.0` | Minimum chunk duration before considering a cut on silence. |
| `max_chunk_s` | `12.0` | Forced cut, even without silence. |
| `silence_ms` | `400` | Silence required to cut. |
| `max_dictation_s` | `300` | Hard stop — the forgotten-open-mic safeguard. |
| `input_device` | `null` | Input device; `null` = system default microphone. |
| `overlay` | `true` | Status pill at the bottom of the screen: loading, listening with VU meter, transcribing. |

## Known limits

- Windows only (global hotkey, clipboard and injection are platform-specific).
- If the target application runs as administrator, injection fails silently:
  that is a Windows protection (UIPI), not a bug.
- The `type` mode (character-by-character typing) is a fallback, not a daily
  driver: Windows 11 Notepad drops or repeats characters below ~50 ms per
  character, i.e. 20 characters per second. Pasting does not have this problem
  and stays instant; `type` is only for fields that refuse pasting.

## License

MIT.
