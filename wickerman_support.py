"""
Wickerman OS v5.2.0 — Embedded file contents.
Imported by wickermaninstall.py. Place this file next to the installer.
"""

# ══════════════════════════════════════════════════════════════════════════════
#  CORE DASHBOARD — runs inside wm-core container
# ══════════════════════════════════════════════════════════════════════════════

MAIN_PY = r'''#!/usr/bin/env python3
import sys
try:
    from nicegui import ui, app, run
    import docker, os, json, glob, datetime, psutil, requests, asyncio, subprocess, time
except ImportError as e:
    print(f"[FATAL] Missing dependency: {e.name}. Rebuild the container.", flush=True)
    sys.exit(1)

LOG_DIR     = "/app/data/logs"
PLUGIN_DIR  = "/app/plugins"
HOST_BASE   = os.environ.get("HOST_INSTALL_DIR", "/tmp/wickerman_missing_env")
SUPPORT_DIR = os.environ.get("HOST_SUPPORT_DIR", "/tmp/wickerman_support_missing")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PLUGIN_DIR, exist_ok=True)

try:
    import pynvml; pynvml.nvmlInit(); _NVML = True
except Exception:
    _NVML = False

THEMES = {
    "Terminal 80s": {"primary":"#ffb000","bg":"#1a1a1a","card":"#0d0d0d","text":"#ffb000","accent":"#ffb000","font":"'Courier New', monospace","crt":True},
    "Wickerman":    {"primary":"#00ff00","bg":"#0a0a0a","card":"#111","text":"#eee","accent":"#00ff00","font":"sans-serif","crt":False},
    "Matrix":       {"primary":"#0f0","bg":"#000","card":"#001100","text":"#0f0","accent":"#0f0","font":"'Courier New', monospace","crt":True},
    "Cyberpunk":    {"primary":"#f0f","bg":"#020024","card":"#090979","text":"#00d4ff","accent":"#f0f","font":"sans-serif","crt":False},
    "Corporate":    {"primary":"#3b82f6","bg":"#f3f4f6","card":"#ffffff","text":"#1f2937","accent":"#1d4ed8","font":"sans-serif","crt":False},
}

CRT_CSS = """
.crt-overlay { background: linear-gradient(rgba(18,16,16,0) 50%, rgba(0,0,0,0.25) 50%), linear-gradient(90deg,rgba(255,0,0,0.06),rgba(0,255,0,0.02),rgba(0,0,255,0.06)); background-size: 100% 2px, 3px 100%; pointer-events:none; position:fixed; top:0; left:0; width:100%; height:100%; z-index:9999; }
.retro-glow { text-shadow: 0 0 3px currentColor; }
"""

MANUAL = """
# WICKERMAN CODEX

Welcome to Wickerman OS v5.2.0 — your local AI command center. Everything runs on your machine, no cloud required.

## Getting Started

When you first load the dashboard, you'll see four tabs across the top: **Plugins**, **Source**, **Codex** (you're here), and **System**.

### The Plugins Tab

Your home screen. Every available AI tool shows as a card with a status badge:

- **not installed** — click INSTALL to set it up.
- **running** — click OPEN to use it, or KILL to stop it.
- **stopped / error** — check the console log at the bottom for details.

Click OPEN to load a plugin in a new tab inside the dashboard. Multiple plugins can be open at once.

### The Source Tab

Version control panel. Every change — installing a plugin, removing one, updating nginx — is automatically tracked by Git.

- **Working Changes** — files modified since last save (green = new, amber = modified, red = deleted)
- **Manual Commit** — save a snapshot with a description
- **Timeline** — full history with diffs

### The Codex Tab

You're reading it. The full documentation for Wickerman OS. Use the search bar above to find what you need.

### The System Tab

Maintenance: prune Docker containers/images to free disk space. Shows GPU status and session info.

## Core Architecture

Wickerman OS is built around a central **Model Router** that manages AI inference. All other plugins talk to it through a unified API.

```
Model Router (wm-llama)
  ├── Local Agents (llama.cpp on GPU/CPU)
  │     Each agent = model + system prompt + RAG + settings
  ├── Remote Providers (OpenAI, Anthropic, Google, custom)
  │     Same API, different backend
  └── /v1/chat/completions (OpenAI-compatible)
        ├── Chat plugin → conversation UI
        ├── Flow Editor → visual pipelines
        └── Code Forge → AI coding sandbox
```

## Your Plugins

Wickerman ships with five plugins:

| Plugin | What It Does |
|--------|-------------|
| **Model Router** | Agent orchestration hub. Manages local models + remote APIs. Handles system prompts, RAG memory, and inference settings. This is the brain — all other plugins talk to it. |
| **Chat** | Conversation UI. Pick an agent, chat with it. Conversations persist server-side. The agent's personality, RAG memory, and settings are configured in the Router. |
| **Flow Editor** | Visual drag-and-drop editor for building AI pipelines (powered by Flowise). Chain multiple agents together. |
| **Model Trainer** | Fine-tune models with LoRA using Unsloth. Upload a dataset, train, export. |
| **Code Forge** | AI-assisted coding sandbox. Describe what you want, the AI writes and runs it. |

**Install order matters.** Install the Model Router first — Chat, Flow Editor, and Code Forge all depend on it for inference.

## Agents

An **agent** is a fully configured AI endpoint: a model (local or remote) with a system prompt, RAG memory, and sampling settings baked in. You create agents in the Model Router dashboard.

Example agents:
- **code-assistant** — Qwen-14B with "You are a senior Python developer" prompt, RAG loaded with project docs, temperature 0.3
- **creative-writer** — TinyLlama with "You are a creative storyteller" prompt, temperature 0.9
- **gpt4-analyst** — GPT-4o via OpenAI API with "You are a data analyst" prompt

When you chat or build flows, you pick an agent by name. The Router handles everything behind the scenes.

## RAG (Retrieval Augmented Generation)

Each agent has its own RAG memory powered by FAISS vector search. When a conversation gets too long:

1. Oldest messages are **trimmed** from the context window
2. Trimmed messages are **archived** to the agent's FAISS index
3. Before each response, the Router **searches** the index for relevant context
4. Relevant chunks are **injected** into the prompt

This gives agents effectively infinite memory — old conversations are never truly lost.

RAG is enabled per-agent in the Model Router dashboard. Each agent's memory is isolated (no cross-contamination between agents).

## Remote Providers

The Model Router can proxy requests to external AI APIs:

| Provider | Type | Covers |
|----------|------|--------|
| **OpenAI-compatible** | `openai` | OpenAI, Groq, Together, OpenRouter, local Ollama/LM Studio |
| **Anthropic** | `anthropic` | Claude models (text-only, Messages API translation) |
| **Google** | `google` | Gemini models (text-only, generateContent translation) |
| **Custom** | `custom` | Any OpenAI-compatible endpoint |

Add providers in the Model Router dashboard under the "Remote providers" tab. API keys are stored locally with restricted permissions.

## Downloading Models

Two ways to get models:

1. **Local directory** — Place `.gguf` files in `~/aidojo/models/` before running the installer. They're copied to `~/WickermanSupport/models/` on install.
2. **Downloader plugin** — Browse HuggingFace, search for a model, pick a quantization, download directly to the models directory.

Recommended models:
- **Qwen2.5-Coder-14B Q5_K_M** — excellent for coding (12GB VRAM)
- **TinyLlama 1.1B Q4_K_M** — fast test/fallback model (600MB)
- **all-MiniLM-L6-v2** — embedding model for RAG (45MB, runs on CPU)

## Where Your Files Live

| Location | What's There | Survives Reinstall? |
|----------|-------------|-------------------|
| `~/aidojo/` | Installer, plugin source code, local models | Source of truth |
| `~/wickerman/` | Dashboard, nginx, docker-compose | No (recreated on install) |
| `~/WickermanSupport/` | Models, datasets, plugin data, configs | **Yes** |
| `~/WickermanSupport/models/` | Your GGUF model files | **Yes** |
| `~/WickermanSupport/plugins/` | Plugin manifests | **Yes** |

The golden rule: anything in `~/WickermanSupport/` survives a `--hard-reset`. Your models, datasets, and plugin customizations are safe.

## API Reference

All inference goes through the Model Router at `http://wm-llama:8080`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Chat with an agent (OpenAI-compatible) |
| `/v1/models` | GET | List loaded agents |
| `/v1/embeddings` | POST | Get text embeddings |
| `/api/status` | GET | Router status, all agents |
| `/api/models` | GET | Available model files |
| `/api/slots/load` | POST | Load a local agent |
| `/api/slots/unload` | POST | Unload an agent |
| `/api/slots/update` | POST | Update agent settings live |
| `/api/providers/add` | POST | Add a remote provider |
| `/api/vram` | GET | GPU VRAM usage |
| `/api/rag/<id>/status` | GET | RAG index status |
| `/api/rag/<id>/search` | POST | Search RAG index |
| `/api/rag/<id>/clear` | POST | Clear RAG index |
| `/api/rag/<id>/archive` | POST | Archive messages to RAG |

Chat plugin API at `http://wm-chat:5000`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents` | GET | List available agents (from router) |
| `/api/conversations` | GET/POST | List or create conversations |
| `/api/conversations/<id>` | GET/PUT/DELETE | Manage a conversation |
| `/api/conversations/<id>/chat` | POST | Send a message |
| `/api/conversations/<id>/context` | GET | Context usage info |
| `/node/execute` | POST | Flow Editor node endpoint |

## Volume Tokens (For Plugin Authors)

| Token | Resolves to |
|-------|-------------|
| `{self}` | `~/WickermanSupport/plugins/<plugin-id>` |
| `{models}` | `~/WickermanSupport/models/` |
| `{datasets}` | `~/WickermanSupport/datasets/` |
| `{loras}` | `~/WickermanSupport/loras/` |
| `{support}` | `~/WickermanSupport/` |
| `{workspace}` | `~/wickerman/workspace/` |

## Common Issues

**Plugin shows "502 Bad Gateway"** — The plugin container is still starting up. The Model Router needs ~10 minutes on first run to compile llama.cpp with CUDA. Wait and refresh.

**Chat says "Cannot reach Model Router"** — Install and start the Model Router plugin first.

**Model won't load (VRAM error)** — The model is too large for your GPU. Try loading with fewer GPU layers (set to 0 for CPU-only) or use a smaller quantization.

**RAG not working** — Ensure an embedding model (e.g., all-MiniLM-L6-v2) is loaded in the Router with alias "embedding". RAG needs embeddings to index and search.

**"llama.wickerman.local not found"** — The hostname isn't in /etc/hosts. Re-run the installer with `sudo`.

**Context overflow / 400 error** — The Router now handles this automatically via context trimming. If you still see it, the model may not have loaded correctly — check the Router dashboard.

## Security

The Docker socket (`/var/run/docker.sock`) is mounted into the dashboard container. This gives it full control over Docker. **Do not expose port 80 to the internet.** This is a local development tool.

API keys for remote providers are stored in plaintext at `/data/providers.json` with file permissions restricted to the container user. This is acceptable for a single-user local tool.
"""

AI_PLUGIN_GUIDE = """
# CREATING YOUR OWN PLUGINS

A plugin is a JSON manifest file that tells Wickerman how to run a containerized AI tool.

## Quick Start

1. Create a file called `wm-yourtool.json`
2. Put it in `~/wickerman/plugins/` (or `~/WickermanSupport/plugins/`)
3. Click Refresh on the dashboard — your plugin appears

## Manifest Template

```json
{
  "name": "Your Tool Name",
  "description": "One-line description shown on the plugin card",
  "icon": "smart_toy",
  "image": "registry/image:tag",
  "container_name": "wm-yourtool",
  "url": "http://yourtool.wickerman.local",
  "ports": [8080],
  "gpu": false,
  "env": ["KEY=value", "ANOTHER=value"],
  "volumes": ["{self}/data:/data"],
  "nginx_host": "yourtool.wickerman.local",
  "help": "Markdown documentation shown in the Codex tab."
}
```

## Two Ways to Ship a Plugin

**Registry pull** — set `"image": "some/image:tag"`. Wickerman pulls it from Docker Hub. Simplest approach.

**Local build** — set `"build": true` and `"build_context": "data"`. Put a `Dockerfile` and your app code in the `files` dict. Wickerman extracts them and runs `docker build`. Use this when you need custom code.

## Rules

- `container_name` **must** start with `wm-`
- `ports[0]` is the port your app listens on inside the container
- `gpu: true` passes through your NVIDIA GPU (requires CUDA)
- `nginx_host` must be added to `/etc/hosts` pointing to `127.0.0.1`
- `network` defaults to `wm-net` — all plugins can talk to each other

## Node API (For Flow Editor Integration)

If you want your plugin to work as a node in Flow Editor, expose two endpoints:

- `GET /node/schema` — returns JSON describing your inputs and outputs
- `POST /node/execute` — accepts input JSON, returns output JSON

Example schema response:
```json
{
  "name": "mytool",
  "inputs": [
    {"name": "prompt", "type": "string", "required": true}
  ],
  "outputs": [
    {"name": "result", "type": "string"}
  ]
}
```

Flow Editor can then call your plugin via an HTTP Request node pointed at `http://wm-yourtool:port/node/execute`.

## Tips

- Test your container standalone with `docker run` before making a manifest
- Check `docker logs wm-yourtool` if your plugin fails to start
- Use the diagnostic tool (`python3 wickerman_diag.py`) to verify routing
- Plugin data in `{self}/` survives reinstalls — use it for databases and configs
"""

SOURCE_HELP = """
## Version Control — What It Is and Why You Care

Imagine you're building a house. Every time you add a room, move a wall, or change the plumbing, someone takes a photograph of the entire house and writes a note: *"Tuesday 3pm — added kitchen window."* If you accidentally break something, you can flip back through the photos and see exactly when it changed.

That's what **Git** does for your Wickerman setup. Every time you install a plugin, change a config, or update nginx, the system takes a snapshot (called a **commit**) and writes a description of what happened.

### The Two Photo Albums

Wickerman keeps two separate histories:

**SYSTEM** (blue badges) tracks your core infrastructure — the dashboard, nginx routing, docker-compose config. These are the bones of the house.

**SUPPORT** (green badges) tracks your plugins, manifests, and customizations. These are the furniture and decorations.

They're separate because you might reinstall the system (new bones) while keeping all your plugins and models intact (same furniture).

### Reading the Timeline

Each entry in the Timeline looks like this:

> **SUPPORT** — plugin: installed wm-chat
> `a3f2c1b` — 2025-03-04 19:41:08

Here's what each piece means:

- **The badge** tells you which album this photo is in
- **The message** describes what changed in plain English
- **The short code** (like `a3f2c1b`) is a unique fingerprint for this exact snapshot — no two are alike
- **The timestamp** is when it happened

### What's a Diff?

Click the little icon next to any Timeline entry and you'll see a **diff** — a side-by-side comparison showing exactly what changed. Lines in **green** with a `+` were added. Lines in **red** with a `-` were removed.

For example, if you installed a new plugin, the diff would show the new manifest file appearing (all green). If you changed a setting, you'd see the old value in red and the new value in green.

This is incredibly useful when something breaks. You can look at the last few diffs and say *"Ah, that config change is what broke it"* instead of guessing.

### Working Changes

The "Working Changes" section shows you things that have changed since the last snapshot. Think of it like unsaved edits in a document. If this section is empty, everything is saved.

Most of the time this will be empty because Wickerman auto-saves after every significant action. But if you manually edit files on the host, those changes show up here until you commit them.

### Manual Commits

Sometimes you want to save a snapshot with your own description. Maybe you tweaked a model config, or you're about to try something experimental and want a save point first.

Type a message describing what you did (or what you're about to do) and click the commit button. Now if things go sideways, you have a named checkpoint you can refer back to.

**Pro tip:** Make a manual commit *before* you try something risky. That way you always know the last known-good state.

### Why Two Commit Buttons?

**Commit Support** saves changes in the plugins/config album — use this for anything in `~/WickermanSupport/`.

**Commit System** saves changes in the infrastructure album — use this for anything in `~/wickerman/` (dashboard, nginx, compose files).

Most of the time you'll use Commit Support, since that's where your day-to-day changes happen.
"""

_inst_state, _bg_tasks = {}, set()
_session_id = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
_log_path = f"{LOG_DIR}/session_{_session_id}.log"

from collections import deque
_log_buffer = []
_LOG_MAX = 2000

def write_log(msg, level="INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}][{level}] {msg}"
    _log_buffer.append(entry)
    if len(_log_buffer) > _LOG_MAX:
        del _log_buffer[:len(_log_buffer) - _LOG_MAX]
    try:
        with open(_log_path, "a") as f: f.write(entry + "\n")
    except Exception: pass

write_log("=== WICKERMAN SESSION STARTED ===")

_dc = None
def dc():
    global _dc
    if _dc is None: _dc = docker.from_env()
    return _dc

# ── Persistent container log watcher ──────────────────────────────────
# Runs in background threads, one per managed container.
# Continuously tails docker logs and pipes them to write_log().
# Survives across install/remove cycles — restarts watchers as needed.
import threading as _threading

_watched_containers = {}  # cname -> threading.Event (stop signal)

def _tail_container(cname, stop_event):
    # Tail a single container's logs forever until stopped
    while not stop_event.is_set():
        try:
            c = dc().containers.get(cname)
            # Get a friendly name from labels or container name
            label = cname.replace("wm-", "").title()
            for chunk in c.logs(stream=True, follow=True, timestamps=False, since=int(time.time())):
                if stop_event.is_set():
                    return
                for line in chunk.decode(errors="replace").splitlines():
                    line = line.strip()
                    if line:
                        write_log(f"[{label}] {line[:200]}")
        except Exception:
            # Container might not exist yet or was removed — wait and retry
            if stop_event.is_set():
                return
            stop_event.wait(5)

def _update_watchers():
    # Discover managed containers and start/stop watchers as needed
    try:
        running = {c.name for c in dc().containers.list(filters={"label": "wickerman.managed=true"})}
    except Exception:
        return
    # Start watchers for new containers
    for cname in running:
        if cname not in _watched_containers:
            stop = _threading.Event()
            _watched_containers[cname] = stop
            t = _threading.Thread(target=_tail_container, args=(cname, stop), daemon=True)
            t.start()
            write_log(f"[System] Started log watcher for {cname}")
    # Stop watchers for removed containers
    for cname in list(_watched_containers):
        if cname not in running:
            _watched_containers[cname].set()
            del _watched_containers[cname]

def _watcher_loop():
    # Periodically check for new/removed containers
    while True:
        _update_watchers()
        time.sleep(5)

_threading.Thread(target=_watcher_loop, daemon=True).start()
write_log("Container log watcher started")

# ── Git helpers ──────────────────────────────────────────────────────
INSTALL_GIT = "/app"          # mounted from ~/wickerman
SUPPORT_GIT = "/support"      # mounted from ~/WickermanSupport

def _git(repo, *args):
    """Run a git command in repo, return (success, stdout)"""
    cmd = ["git", "-C", repo] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0, r.stdout.strip()
    except Exception as e:
        return False, str(e)

def git_commit(repo, message, metadata=None):
    """Stage all and commit. Returns (success, commit_hash)"""
    _git(repo, "add", "-A")
    # Check if there's anything to commit
    ok, status = _git(repo, "status", "--porcelain")
    if not status.strip():
        return True, "no-changes"
    # Write metadata file if provided
    if metadata:
        meta_dir = os.path.join(repo, ".wickerman", "commits")
        os.makedirs(meta_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(meta_dir, f"{ts}.json"), "w") as f:
            json.dump(metadata, f, indent=2)
        _git(repo, "add", "-A")
    ok, out = _git(repo, "commit", "-m", message)
    if ok:
        _, hash_out = _git(repo, "rev-parse", "--short", "HEAD")
        write_log(f"Git [{os.path.basename(repo)}]: {message} ({hash_out})")
        return True, hash_out
    return False, out

def git_log(repo, limit=50):
    """Return list of recent commits as dicts"""
    fmt = "%H|%h|%ai|%s"
    ok, out = _git(repo, "log", f"--max-count={limit}", f"--pretty=format:{fmt}")
    if not ok or not out:
        return []
    commits = []
    for line in out.split("\n"):
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0], "short": parts[1],
                "date": parts[2], "message": parts[3]
            })
    return commits

def git_diff(repo, commit_hash=None, file_path=None):
    """Return diff text. No args = working changes. With hash = that commit's diff."""
    if commit_hash:
        args = ["show", "--pretty=format:", commit_hash]
    else:
        args = ["diff"]
    if file_path:
        args += ["--", file_path]
    ok, out = _git(repo, *args)
    return out.strip() if ok else ""

def git_status(repo):
    """Return list of {status, file} dicts for uncommitted changes"""
    ok, out = _git(repo, "status", "--porcelain")
    if not ok or not out:
        return []
    files = []
    for line in out.split("\n"):
        if len(line) >= 4:
            st = line[:2].strip()
            fp = line[3:]
            files.append({"status": st, "file": fp})
    return files

def git_file_diff(repo, file_path):
    """Return diff for a specific uncommitted file"""
    ok, out = _git(repo, "diff", "--", file_path)
    if not out:
        ok, out = _git(repo, "diff", "--cached", "--", file_path)
    return out if ok else ""

def git_restore(repo, commit_hash):
    """Restore repo to a specific commit (creates a revert commit)"""
    ok, out = _git(repo, "checkout", commit_hash, "--", ".")
    if ok:
        return git_commit(repo, f"Restored to {commit_hash[:8]}", {
            "action": "restore", "target_commit": commit_hash
        })
    return False, out

def git_auto_commit(repo, action, details=None):
    """Auto-commit with structured metadata. Fire-and-forget."""
    meta = {"action": action, "timestamp": datetime.datetime.now().isoformat()}
    if details:
        meta.update(details)
    # Build a descriptive message
    messages = {
        "plugin_install": f"plugin: installed {details.get('plugin', '?')}",
        "plugin_remove": f"plugin: removed {details.get('plugin', '?')}",
        "nginx_regen": f"nginx: regenerated config ({details.get('vhosts', '?')} vhosts)",
        "model_download": f"model: downloaded {details.get('filename', '?')}",
        "system_start": "system: started Wickerman OS",
        "manual": details.get("message", "manual commit"),
    }
    msg = messages.get(action, f"auto: {action}")
    try:
        git_commit(repo, msg, meta)
    except Exception as e:
        write_log(f"Git auto-commit failed: {e}", "WARN")

def get_theme(): return app.storage.general.get("theme", "Terminal 80s")
def set_theme(name): app.storage.general["theme"] = name

def get_stats():
    cpu, ram = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    gpu_pct = vram_p = 0; vram_txt = "N/A"
    if _NVML:
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem, util = pynvml.nvmlDeviceGetMemoryInfo(h), pynvml.nvmlDeviceGetUtilizationRates(h)
            gpu_pct, vram_p = util.gpu, mem.used / mem.total
            vram_txt = f"{mem.used//1024**2} / {mem.total//1024**2} MB"
        except Exception: pass
    return {"cpu":cpu,"ram":ram,"gpu":gpu_pct,"vram_p":vram_p,"vram_txt":vram_txt}

REQUIRED_FIELDS = {"name","description","container_name","ports"}

def load_plugins():
    out = {}
    scan_dirs = [PLUGIN_DIR, "/support/plugins"]
    for d in scan_dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.json"))):
            fname = os.path.basename(path)
            # Skip override files — they get merged into their base manifest
            if fname.endswith(".override.json"):
                continue
            if fname in out: continue  # first found wins (local overrides support)
            try:
                with open(path) as f: m = json.load(f)
                if REQUIRED_FIELDS - set(m): continue
                # Check for a matching .override.json and merge it in
                base_name = fname.replace(".json", "")
                for od in scan_dirs:
                    override_path = os.path.join(od, f"{base_name}.override.json")
                    if os.path.isfile(override_path):
                        try:
                            with open(override_path) as of: overrides = json.load(of)
                            _deep_merge(m, overrides)
                            write_log(f"Applied overrides from {base_name}.override.json")
                        except Exception as oe:
                            write_log(f"Failed to parse override {override_path}: {oe}", "WARN")
                        break  # first override found wins
                out[fname] = m
            except Exception as e:
                write_log(f"Failed to parse plugin {fname}: {e}", "ERROR")
    return out

def _deep_merge(base, overrides):
    # Recursively merge overrides into base dict
    for k, v in overrides.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v

def container_status(name):
    try: return dc().containers.get(name).status
    except Exception: return "missing"

def resolve_volume(spec, plugin_id):
    parts = spec.split(":")
    raw_host, cpath = parts[0], parts[1]
    mode = parts[2] if len(parts) >= 3 else "rw"
    host = (raw_host
        .replace("{self}", f"{SUPPORT_DIR}/plugins/{plugin_id}")
        .replace("{models}", f"{SUPPORT_DIR}/models")
        .replace("{datasets}", f"{SUPPORT_DIR}/datasets")
        .replace("{loras}", f"{SUPPORT_DIR}/loras")
        .replace("{support}", SUPPORT_DIR)
        .replace("{workspace}", f"{HOST_BASE}/workspace"))
    return host, cpath, mode

@ui.page("/")
def index():
    cs = {"log_cursor":0,"hud":{},"card_cursors":{},"open_tabs":{},"active_tab":"Plugins"}
    term_ref = {"w":None}
    _active_card_logs = {}

    def set_state(cn, st, sl=0):
        _inst_state[cn] = {"state":st,"start_line":sl}
        if st == "busy": cs["card_cursors"].pop(cn, None)
    def get_state(cn): return _inst_state.get(cn,{}).get("state","")
    def get_start_line(cn): return _inst_state.get(cn,{}).get("start_line",0)
    def pop_state(cn): _inst_state.pop(cn, None)
    def t(): return THEMES[get_theme()]
    def card_style(): return f"background:{t()['card']};border:1px solid {t()['accent']};color:{t()['text']}"

    def apply_css(name):
        th = THEMES[name]
        ui.colors(primary=th["primary"],secondary=th["accent"],accent=th["accent"])
        ui.query("body").style(f"background-color:{th['bg']};color:{th['text']};font-family:{th['font']};")
        if th.get("crt"): ui.query("body").classes("retro-glow")
        else: ui.query("body").classes(remove="retro-glow")

    def update_hud():
        h = cs["hud"]
        if not h: return
        s, color = get_stats(), t()["primary"]
        h["cpu_l"].text, h["cpu_b"].value = f"{s['cpu']}%", s["cpu"]/100
        h["cpu_b"].props(f"color={'#ef4444' if s['cpu']>90 else color}")
        h["ram_l"].text, h["ram_b"].value = f"{s['ram']}%", s["ram"]/100
        h["ram_b"].props(f"color={'#ef4444' if s['ram']>90 else color}")
        h["gpu_l"].text, h["gpu_b"].value = f"{s['gpu']}%", s["gpu"]/100
        h["vram_l"].text, h["vram_b"].value = s["vram_txt"], s["vram_p"]
        h["vram_b"].props(f"color={'#ef4444' if s['vram_p']>0.9 else color}")

    def pump_logs():
        if w := term_ref["w"]:
            cur = min(cs["log_cursor"], len(_log_buffer))
            new = _log_buffer[cur:]
            for line in new: w.push(line)
            cs["log_cursor"] = len(_log_buffer)

    def pump_card_log(cn, lw):
        if cn not in cs["card_cursors"]: cs["card_cursors"][cn] = get_start_line(cn)
        cur = min(cs["card_cursors"][cn], len(_log_buffer))
        new = _log_buffer[cur:]
        for line in new: lw.push(line)
        cs["card_cursors"][cn] = len(_log_buffer)

    async def _reload_nginx(clog):
        try:
            clog("Regenerating nginx config...")
            result = await run.io_bound(subprocess.run, ["python3","/app/nginx/generate_nginx.py"], capture_output=True, text=True)
            if result.returncode != 0: clog(f"Nginx gen stderr: {result.stderr.strip()}", "WARN")
            else: clog("Nginx config written ✓")
        except Exception as e: clog(f"Nginx config failed: {e}", "WARN"); return
        try:
            clog("Reloading nginx gateway...")
            gw = await run.io_bound(dc().containers.get, "wm-gateway")
            r = await run.io_bound(gw.exec_run, "nginx -s reload")
            if r.exit_code == 0:
                clog("Nginx reloaded ✓ — plugin URL is now live")
                await run.io_bound(git_auto_commit, INSTALL_GIT, "nginx_regen", {"vhosts": "updated"})
            else: clog(f"Nginx reload exit {r.exit_code}: {r.output.decode()}", "WARN")
        except Exception as e: clog(f"Nginx reload failed: {e}", "WARN")

    async def _install_task(manifest, fname):
        cname, pid = manifest["container_name"], fname.replace(".json","")
        def clog(msg, level="INFO"): write_log(f"[{manifest['name']}] {msg}", level)
        try:
            if "files" in manifest:
                data_dir = f"/support/plugins/{pid}"
                os.makedirs(data_dir, exist_ok=True)
                for fn, fc in manifest["files"].items():
                    fp = os.path.join(data_dir, fn)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "w") as f: f.write(fc)
                clog(f"Extracted {len(manifest['files'])} file(s) to {data_dir}")

            # Determine image name — build locally or pull from registry
            is_build = manifest.get("build", False)
            if is_build:
                image_tag = f"wickerman/{pid}:latest"
                build_sub = manifest.get("build_context", "data")
                # Docker SDK validates path locally, but we're inside a container.
                # Use the container-internal path (SDK checks os.path.isdir) — the Docker
                # daemon accesses the same files via the volume mount.
                build_path_container = f"/support/plugins/{pid}/{build_sub}"
                build_path_host = f"{SUPPORT_DIR}/plugins/{pid}/{build_sub}"
                clog(f"Building image {image_tag} from {build_path_host} (this may take a while)...")
                # Compute a hash of the manifest's embedded files to use as a cache buster.
                # Docker will reuse cached layers unless this hash changes.
                import hashlib
                files_str = json.dumps(manifest.get("files", {}), sort_keys=True)
                files_hash = hashlib.sha256(files_str.encode()).hexdigest()[:12]
                clog(f"Files hash: {files_hash}")
                build_done = asyncio.Event()
                async def heartbeat():
                    secs = 0
                    while not build_done.is_set():
                        await asyncio.sleep(15); secs += 15
                        if not build_done.is_set(): clog(f"Still building... ({secs}s)")
                hb = asyncio.ensure_future(heartbeat())
                try:
                    image, logs = await run.io_bound(
                        dc().images.build, path=build_path_container, tag=image_tag,
                        rm=True, forcerm=True, nocache=False,
                        buildargs={"CACHEBUST": files_hash}
                    )
                    for chunk in logs:
                        if "stream" in chunk:
                            line = chunk["stream"].strip()
                            if line: clog(f"  {line}")
                except Exception as build_err:
                    # images.build returns (image, generator) — generator may already be consumed
                    # If we get here with a TypeError, the build likely succeeded
                    if "generator" not in str(build_err).lower():
                        raise
                finally:
                    build_done.set(); hb.cancel()
                clog("Image built ✓")
            else:
                image_tag = manifest["image"]
                clog("Pulling image (this may take a while)...")
                pull_done = asyncio.Event()
                async def heartbeat():
                    secs = 0
                    while not pull_done.is_set():
                        await asyncio.sleep(15); secs += 15
                        if not pull_done.is_set(): clog(f"Still pulling… ({secs}s)")
                hb = asyncio.ensure_future(heartbeat())
                try: await run.io_bound(dc().images.pull, image_tag)
                finally: pull_done.set(); hb.cancel()
                clog("Image ready ✓")

            try:
                stale = await run.io_bound(dc().containers.get, cname)
                await run.io_bound(stale.remove, force=True); clog("Removed stale container")
            except docker.errors.NotFound: pass
            except Exception as e: clog(f"Stale container cleanup error: {e}", "WARN")
            vols = {}
            for v in manifest.get("volumes",[]):
                try:
                    hp,cp,mode = resolve_volume(v, pid)
                    os.makedirs(hp, exist_ok=True)
                    try: os.chmod(hp, 0o777)
                    except Exception: pass
                    vols[hp] = {"bind":cp,"mode":mode}
                    clog(f"Volume: {os.path.basename(hp)} → {cp} ({mode})")
                except Exception as ve: clog(f"Bad volume '{v}': {ve}", "WARN")
            dev = [docker.types.DeviceRequest(count=-1,capabilities=[["gpu"]])] if manifest.get("gpu") else []
            if dev: clog("GPU passthrough enabled")
            plugin_network = manifest.get("network", "wm-net")
            # Create custom network if needed
            if plugin_network != "wm-net":
                try:
                    await run.io_bound(dc().networks.create, plugin_network, driver="bridge", check_duplicate=True)
                    clog(f"Created isolated network: {plugin_network}")
                except Exception: pass  # already exists
            clog("Starting container...")
            await run.io_bound(dc().containers.run, image=image_tag, name=cname, detach=True,
                restart_policy={"Name":"unless-stopped"}, network=plugin_network,
                environment=manifest.get("env",[]), command=manifest.get("command"),
                volumes=vols, device_requests=dev, labels={"wickerman.managed":"true"})
            # Connect gateway to custom network so nginx can route to this plugin
            if plugin_network != "wm-net":
                try:
                    net = await run.io_bound(dc().networks.get, plugin_network)
                    gw = await run.io_bound(dc().containers.get, "wm-gateway")
                    await run.io_bound(net.connect, gw)
                    clog(f"Connected gateway to {plugin_network}")
                except Exception as e: clog(f"Gateway connect to {plugin_network}: {e}", "WARN")
                # Connect containers referenced in env vars (e.g. LLAMA_API=http://wm-llama:8080)
                for env_val in manifest.get("env", []):
                    for known in ["wm-llama","wm-chat","wm-trainer"]:
                        if known in env_val:
                            try:
                                dep = await run.io_bound(dc().containers.get, known)
                                await run.io_bound(net.connect, dep)
                                clog(f"Connected {known} to {plugin_network}")
                            except Exception: pass  # not running yet, that's fine
            await asyncio.sleep(2)
            # Container logs are streamed by the persistent watcher.
            # Just poll for healthy/ready status here.
            clog("Waiting for container to become ready...")
            max_wait = 600
            poll_interval = 3
            elapsed = 0
            while elapsed < max_wait:
                stat = await run.io_bound(container_status, cname)
                if stat == "running":
                    try:
                        c = await run.io_bound(dc().containers.get, cname)
                        health = c.attrs.get("State", {}).get("Health", {}).get("Status")
                        if health == "healthy":
                            break
                        elif health == "starting":
                            pass  # keep waiting
                        elif health is None:
                            port = manifest.get("ports", [None])[0]
                            if port:
                                try:
                                    probe = await run.io_bound(
                                        c.exec_run, f"curl -sf http://localhost:{port}/health"
                                    )
                                    if probe.exit_code == 0:
                                        break
                                except Exception:
                                    pass
                            else:
                                break
                    except Exception:
                        break
                elif stat in ("exited", "dead"):
                    set_state(cname, "error")
                    clog(f"Container exited (status: {stat})", "ERROR")
                    return
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            stat = await run.io_bound(container_status, cname)
            if stat == "running":
                set_state(cname,"running"); clog("READY","OK"); await _reload_nginx(clog)
                # Auto-commit plugin install
                await run.io_bound(git_auto_commit, SUPPORT_GIT, "plugin_install", {
                    "plugin": manifest.get("name", pid), "container": cname,
                    "nginx_host": manifest.get("nginx_host", ""),
                    "build": manifest.get("build", False)
                })
            else:
                set_state(cname,"error"); clog(f"Status: {stat} (expected running)","WARN")
        except Exception as e:
            set_state(cname,"error"); clog(f"FAILED: {e}","ERROR")

    async def do_remove(cname):
        try:
            c = await run.io_bound(dc().containers.get, cname)
            await run.io_bound(c.stop); await run.io_bound(c.remove)
            ui.notify(f"Removed {cname}", type="positive")
            await run.io_bound(git_auto_commit, SUPPORT_GIT, "plugin_remove", {"plugin": cname})
        except Exception as e: ui.notify(f"Error: {e}", type="negative")
        pop_state(cname); rebuild()

    main_col = ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-6")

    def rebuild():
        th = t(); main_col.clear(); _active_card_logs.clear()
        cs["hud"], term_ref["w"] = {}, None
        with main_col:
            if th.get("crt"): ui.element("div").classes("crt-overlay")
            with ui.row().classes("w-full justify-between items-center mb-2"):
                ui.label("WICKERMAN OS").classes("text-4xl font-mono font-bold").style(f"color:{th['primary']}")
                with ui.row().classes("gap-2 items-center"):
                    ui.button("⟳ Refresh", on_click=rebuild).props("flat size=sm")
                    ui.select(list(THEMES), value=get_theme(), on_change=lambda e: (set_theme(e.value),apply_css(e.value),rebuild())).classes("w-44")
            with ui.grid(columns=4).classes("w-full gap-4 mb-4"):
                for key,label in [("cpu","CPU"),("ram","RAM"),("gpu","GPU"),("vram","VRAM")]:
                    with ui.card().classes("p-3").style(card_style()):
                        ui.label(label).classes("text-xs font-bold opacity-60 tracking-widest")
                        lbl = ui.label("—").classes("text-xl font-bold font-mono")
                        bar = ui.linear_progress(value=0,show_value=False).classes("h-2 mt-1")
                        cs["hud"][f"{key}_l"], cs["hud"][f"{key}_b"] = lbl, bar

            with ui.tabs().classes("w-full") as tabs:
                ui.tab("Plugins",icon="extension")
                ui.tab("Source",icon="history")
                ui.tab("Codex",icon="menu_book")
                ui.tab("System",icon="settings")
                for cn, info in cs["open_tabs"].items():
                    with ui.tab(info["name"], icon=info.get("icon","extension")):
                        def close_tab(c=cn):
                            cs["open_tabs"].pop(c,None); cs["active_tab"]="Plugins"; rebuild()
                        ui.button(icon="close",on_click=close_tab).props("flat round dense size=xs").classes("ml-2")

            with ui.tab_panels(tabs, value=cs["active_tab"],
                               on_change=lambda e: cs.update({"active_tab":e.value})).classes("w-full bg-transparent"):
                with ui.tab_panel("Plugins"):
                    plugins = load_plugins()
                    if not plugins:
                        with ui.card().classes("w-full p-12 text-center").style(card_style()):
                            ui.label("Drop a .json manifest into ~/wickerman/plugins/ and Refresh").classes("text-xl font-mono opacity-50")
                    else:
                        with ui.column().classes("w-full gap-4"):
                            for fname, m in plugins.items():
                                cname = m["container_name"]; state = get_state(cname)
                                if state != "busy":
                                    raw = container_status(cname)
                                    state = "running" if raw in ("running","created","restarting") else ("error" if state=="error" else "idle")
                                    set_state(cname, state)
                                b_col,b_txt = {"idle":("#6b7280","not installed"),"busy":("#f59e0b","installing…"),"running":("#22c55e","running"),"error":("#ef4444","error")}[state]
                                with ui.card().classes("w-full p-4").style(card_style()):
                                    with ui.row().classes("w-full justify-between items-center"):
                                        with ui.row().classes("gap-3 items-center"):
                                            ui.icon(m.get("icon","extension"),color=(th["accent"] if state=="running" else "grey"),size="md")
                                            with ui.column().classes("gap-0"):
                                                with ui.row().classes("gap-2 items-center"):
                                                    ui.label(m["name"]).classes("font-bold text-lg")
                                                    ui.badge(b_txt).style(f"background:{b_col};font-size:0.65rem")
                                                ui.label(m.get("description","")).classes("text-sm opacity-60")
                                        with ui.row().classes("gap-2 items-center"):
                                            if state == "busy":
                                                ui.spinner(type="dots",size="lg").style(f"color:{th['primary']}")
                                            elif state == "running":
                                                if "url" in m:
                                                    def open_plugin(name=m["name"],url=m["url"],icon=m.get("icon","extension"),c=cname):
                                                        cs["open_tabs"][c]={"name":name,"url":url,"icon":icon}
                                                        cs["active_tab"]=name; rebuild()
                                                    ui.button("OPEN",on_click=open_plugin).props("flat")
                                                ui.button("KILL",on_click=lambda c=cname: do_remove(c)).props("outline color=red size=sm")
                                            else:
                                                def start_install(m=m,f=fname,c=cname):
                                                    set_state(c,"busy",len(_log_buffer))
                                                    tk=asyncio.ensure_future(_install_task(m,f))
                                                    _bg_tasks.add(tk); tk.add_done_callback(_bg_tasks.discard)
                                                    ui.timer(0.05,rebuild,once=True)
                                                ui.button("RETRY" if state=="error" else "INSTALL",on_click=start_install).props("flat")
                                    if state == "busy":
                                        lw = ui.log(max_lines=80).classes("w-full h-32 mt-3 rounded p-2 font-mono text-xs").style(f"background:#000;color:{th['primary']};border:1px solid {th['accent']}50")
                                        pump_card_log(cname,lw); _active_card_logs[cname]=lw

                with ui.tab_panel("Source"):
                    with ui.column().classes("w-full gap-4"):
                        # ── Help Guide ────────────────────────────
                        with ui.expansion("What is this? (click to learn)", icon="help_outline").classes("w-full").style(card_style()):
                            ui.markdown(SOURCE_HELP).classes("p-3 text-sm")

                        # ── Working Changes ──────────────────────
                        with ui.card().classes("w-full p-4").style(card_style()):
                            ui.label("WORKING CHANGES").classes("text-xs font-bold opacity-40 tracking-widest mb-2")
                            for repo_label, repo_path in [("SYSTEM", INSTALL_GIT), ("SUPPORT", SUPPORT_GIT)]:
                                changes = git_status(repo_path)
                                if changes:
                                    ui.label(repo_label).classes("text-xs font-bold mt-2").style(f"color:{th['accent']}")
                                    for ch in changes:
                                        st_color = "#a6e3a1" if ch["status"] in ("A","??") else "#f9e2af" if ch["status"] == "M" else "#f38ba8"
                                        ui.label(f"  {ch['status']}  {ch['file']}").classes("font-mono text-xs").style(f"color:{st_color}")
                            if not git_status(INSTALL_GIT) and not git_status(SUPPORT_GIT):
                                ui.label("No uncommitted changes").classes("text-sm opacity-40")

                        # ── Manual Commit ─────────────────────────
                        with ui.card().classes("w-full p-4").style(card_style()):
                            ui.label("MANUAL COMMIT").classes("text-xs font-bold opacity-40 tracking-widest mb-2")
                            commit_msg = ui.input("Commit message").classes("w-full")
                            with ui.row().classes("gap-2 mt-2"):
                                async def do_commit_support():
                                    msg = commit_msg.value.strip()
                                    if not msg: ui.notify("Enter a message", type="warning"); return
                                    ok, h = await run.io_bound(git_commit, SUPPORT_GIT, msg, {"action": "manual", "message": msg})
                                    ui.notify(f"Committed: {h}" if ok else f"Failed: {h}", type="positive" if ok else "negative")
                                    commit_msg.value = ""; rebuild()
                                async def do_commit_system():
                                    msg = commit_msg.value.strip()
                                    if not msg: ui.notify("Enter a message", type="warning"); return
                                    ok, h = await run.io_bound(git_commit, INSTALL_GIT, msg, {"action": "manual", "message": msg})
                                    ui.notify(f"Committed: {h}" if ok else f"Failed: {h}", type="positive" if ok else "negative")
                                    commit_msg.value = ""; rebuild()
                                ui.button("Commit Support", on_click=lambda: asyncio.ensure_future(do_commit_support())).props("outline dense")
                                ui.button("Commit System", on_click=lambda: asyncio.ensure_future(do_commit_system())).props("outline dense")

                        # ── Timeline ─────────────────────────────
                        with ui.card().classes("w-full p-4").style(card_style()):
                            ui.label("TIMELINE").classes("text-xs font-bold opacity-40 tracking-widest mb-2")
                            # Interleave commits from both repos
                            all_commits = []
                            for repo_label, repo_path in [("SYSTEM", INSTALL_GIT), ("SUPPORT", SUPPORT_GIT)]:
                                for c in git_log(repo_path, limit=30):
                                    c["repo"] = repo_label
                                    c["repo_path"] = repo_path
                                    all_commits.append(c)
                            all_commits.sort(key=lambda c: c.get("date",""), reverse=True)

                            if not all_commits:
                                ui.label("No commits yet. Install a plugin or make a change.").classes("text-sm opacity-40")
                            else:
                                for c in all_commits[:40]:
                                    badge_color = "#89b4fa" if c["repo"] == "SYSTEM" else "#a6e3a1"
                                    with ui.row().classes("w-full items-start gap-2 py-1").style("border-bottom:1px solid #31324420"):
                                        ui.label(c["repo"]).classes("text-xs font-bold px-2 py-0.5 rounded").style(f"background:{badge_color}20;color:{badge_color};min-width:70px;text-align:center")
                                        with ui.column().classes("gap-0 flex-1"):
                                            ui.label(c["message"]).classes("text-sm font-mono")
                                            ui.label(f"{c['short']} — {c['date'][:19]}").classes("text-xs opacity-40")
                                        # Diff button
                                        def show_diff(commit=c):
                                            diff_text = git_diff(commit["repo_path"], commit["hash"])
                                            if diff_text:
                                                with ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl p-4").style("max-height:80vh;overflow:auto"):
                                                    ui.label(f"Diff: {commit['short']} — {commit['message']}").classes("text-sm font-bold mb-2")
                                                    ui.code(diff_text, language="diff").classes("w-full text-xs")
                                                    ui.button("Close", on_click=dlg.close).props("flat")
                                                dlg.open()
                                            else:
                                                ui.notify("No diff available", type="info")
                                        ui.button(icon="difference", on_click=show_diff).props("flat round dense size=xs")

                with ui.tab_panel("Codex"):
                    codex_search = ui.input("Search the codex...", placeholder="Type to filter docs...").classes("w-full mb-4").props("outlined dense clearable")
                    codex_content = ui.column().classes("w-full")
                    with codex_content:
                        with ui.row().classes("w-full gap-4"):
                            with ui.column().classes("w-1/5 gap-1"):
                                ui.label("SECTIONS").classes("text-xs font-bold opacity-40 tracking-widest mb-1")
                                for lbl in ["Getting Started","Core Architecture","Your Plugins","Agents","RAG Memory","Remote Providers","Downloading Models","File Locations","API Reference","Common Issues","Security","Plugin Authoring"]:
                                    ui.label(lbl).classes("text-sm pl-2 border-l-2").style(f"border-color:{th['accent']};opacity:0.8")
                            with ui.scroll_area().classes("w-4/5"):
                                with ui.column().classes("p-4 gap-4 rounded").style(f"border:1px dashed {th['accent']}"):
                                    codex_md = ui.markdown(MANUAL).classes("w-full")
                                    ui.separator()
                                    with ui.expansion("AI Plugin Authoring Guide",icon="smart_toy").classes("w-full").style(card_style()):
                                        codex_guide = ui.markdown(AI_PLUGIN_GUIDE).classes("p-2 text-sm")
                                    pc = load_plugins()
                                    if pc:
                                        ui.label("PLUGIN DOCS").classes("text-xs font-bold opacity-40 tracking-widest mt-4")
                                        for _,pm in pc.items():
                                            with ui.expansion(pm["name"],icon=pm.get("icon","extension")).classes("w-full").style(card_style()):
                                                ui.markdown(pm.get("help","_No docs._")).classes("p-3 text-sm")

                    def _filter_codex(e):
                        q = (codex_search.value or "").strip().lower()
                        if not q:
                            codex_md.set_content(MANUAL)
                            return
                        # Highlight matching sections
                        lines = MANUAL.split("\n")
                        filtered = []
                        include = False
                        for line in lines:
                            if line.startswith("#"):
                                include = q in line.lower()
                                if include: filtered.append(line)
                            elif include or q in line.lower():
                                filtered.append(line)
                                include = True
                        codex_md.set_content("\n".join(filtered) if filtered else f"_No results for '{codex_search.value}'_")
                    codex_search.on("update:model-value", _filter_codex)

                with ui.tab_panel("System"):
                    with ui.column().classes("w-full gap-4"):
                        with ui.card().classes("p-6").style(card_style()):
                            ui.label("MAINTENANCE").classes("text-xl font-bold mb-4")
                            async def do_prune_c():
                                await run.io_bound(dc().containers.prune); write_log("Pruned containers","OK"); ui.notify("Done",type="positive")
                            async def do_prune_i():
                                await run.io_bound(dc().images.prune); write_log("Pruned images","OK"); ui.notify("Done",type="positive")
                            ui.button("Prune Stopped Containers",on_click=lambda: asyncio.ensure_future(do_prune_c())).props("outline color=orange w-full")
                            ui.button("Prune Unused Images",on_click=lambda: asyncio.ensure_future(do_prune_i())).props("outline color=red w-full").classes("mt-2")
                        with ui.card().classes("p-6").style(card_style()):
                            ui.label("SESSION INFO").classes("text-xl font-bold mb-2")
                            for line in [f"Session: {_session_id}",f"Log: {_log_path}",f"GPU: {'Yes' if _NVML else 'No'}",f"Plugins: {len(load_plugins())}",f"Support: {SUPPORT_DIR}"]:
                                ui.label(line).classes("font-mono text-sm opacity-60")
                        with ui.card().classes("p-6").style(card_style()):
                            ui.label("⚠ SECURITY").classes("text-xl font-bold mb-2").style("color:#f59e0b")
                            ui.markdown("Docker socket = root-equivalent. Do **not** expose port 80.").classes("text-sm opacity-80")

                for cn, info in cs["open_tabs"].items():
                    with ui.tab_panel(info["name"]).classes("w-full p-0"):
                        el = ui.element("iframe").classes("w-full").style("height:80vh;border:none;border-radius:8px;")
                        el._props["src"] = info["url"]
                        el._props["frameborder"] = "0"
                        el._props["allowfullscreen"] = True
                        el.update()

            term = ui.log(max_lines=300).classes("w-full h-40 mt-4 rounded p-2 font-mono text-xs").style(f"background:#000;color:{th['primary']}")
            term_ref["w"] = term
            for line in _log_buffer: term.push(line)
            cs["log_cursor"] = len(_log_buffer)

    ui.add_head_html(f"<style>{CRT_CSS}</style>"); apply_css(get_theme()); rebuild()
    ui.timer(2.0, update_hud); ui.timer(0.5, pump_logs)
    def pump_all():
        for cn,lw in list(_active_card_logs.items()):
            try: pump_card_log(cn,lw)
            except Exception: pass
    ui.timer(0.5, pump_all)
    _prev = {}
    def watch():
        changed = {c for c,info in _inst_state.items() if _prev.get(c) != info.get("state")}
        if not changed: return
        _prev.update({c:_inst_state[c].get("state") for c in changed})
        if any(_inst_state.get(c,{}).get("state") in ("running","error") for c in changed): rebuild()
    ui.timer(1.0, watch)

ui.run(host="0.0.0.0", port=8000, title="Wickerman OS", dark=True, show=False, storage_secret="wm-secret")
'''

# ══════════════════════════════════════════════════════════════════════════════
#  CORE DOCKERFILE
# ══════════════════════════════════════════════════════════════════════════════

CORE_DOCKERFILE = """\
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir nicegui docker psutil nvidia-ml-py requests
RUN git config --global --add safe.directory /app \
    && git config --global --add safe.directory /support \
    && git config --global user.name "Wickerman OS" \
    && git config --global user.email "wickerman@local"
WORKDIR /app
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -sf http://localhost:8000 || exit 1
CMD ["python3", "core_app/main.py"]
"""

# ══════════════════════════════════════════════════════════════════════════════
#  HUGGINGFACE DOWNLOADER — Flask app (replaces old raw-URL downloader)
# ══════════════════════════════════════════════════════════════════════════════

DOWNLOADER_APP_PY = r'''
from flask import Flask, render_template, request, jsonify
import os, json, requests, threading, time, re
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)
MODEL_DIR  = "/data/models"
STATE_FILE = "/data/downloads_state.json"
downloads  = {}
_lock      = threading.Lock()
_dl_pool   = ThreadPoolExecutor(max_workers=3)
os.makedirs(MODEL_DIR, exist_ok=True)

def save_state():
    try:
        with _lock:
            snapshot = json.dumps(downloads)
        with open(STATE_FILE, "w") as f: f.write(snapshot)
    except Exception as e:
        print(f"[WARN] save_state failed: {e}", flush=True)

try:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f: downloads = json.load(f)
        for tid, info in downloads.items():
            if info.get("status") == "downloading": downloads[tid]["status"] = "interrupted"
        save_state()
except Exception: downloads = {}

def human_size(b):
    for u in ["B","KB","MB","GB","TB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def parse_hf_input(text):
    text = text.strip().rstrip("/")
    m = re.match(r'https?://huggingface\.co/([^/]+/[^/]+)', text)
    if m: return m.group(1)
    if "/" in text and not text.startswith("http"): return text
    return None

def download_file(url, dest, task_id, filename):
    try:
        headers = {}; part_file = dest + ".part"; downloaded = 0
        if os.path.exists(part_file):
            downloaded = os.path.getsize(part_file)
            headers["Range"] = f"bytes={downloaded}-"
        r = requests.get(url, stream=True, timeout=60, headers=headers)
        if r.status_code == 416:
            if os.path.exists(part_file): os.rename(part_file, dest)
            with _lock: downloads[task_id]["status"] = "complete"
            save_state(); return
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        if r.status_code == 206: total += downloaded
        else: downloaded = 0
        last_save = time.time()
        mode = "ab" if r.status_code == 206 else "wb"
        with open(part_file, mode) as f:
            for chunk in r.iter_content(chunk_size=1024*256):
                f.write(chunk); downloaded += len(chunk)
                with _lock:
                    downloads[task_id] = {"status":"downloading","total":total,"downloaded":downloaded,"filename":filename,"url":url,"dest":dest}
                now = time.time()
                if now - last_save >= 3: save_state(); last_save = now
        os.rename(part_file, dest)
        with _lock: downloads[task_id]["status"] = "complete"
        save_state()
    except Exception as e:
        with _lock: downloads[task_id] = {"status":"error","error":str(e),"filename":filename,"url":url,"dest":dest}
        save_state()

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/hf/lookup", methods=["POST"])
def hf_lookup():
    text = (request.json or {}).get("query","").strip()
    repo_id = parse_hf_input(text)
    if not repo_id: return jsonify({"error":"Use format: owner/model or a HuggingFace URL"}), 400
    try:
        info = requests.get(f"https://huggingface.co/api/models/{repo_id}", timeout=15).json()
        if "error" in info: return jsonify({"error":f"Model not found: {repo_id}"}), 404
        tree = requests.get(f"https://huggingface.co/api/models/{repo_id}/tree/main", timeout=15).json()
        files = []
        for item in tree:
            if item.get("type") != "file": continue
            name, size = item.get("path",""), item.get("size",0)
            ext = name.rsplit(".",1)[-1].lower() if "." in name else ""
            cat = "other"
            if ext == "gguf": cat = "gguf"
            elif ext in ("safetensors","bin","pt","pth"): cat = "weights"
            elif ext in ("json","txt","md","yaml","yml"): cat = "config"
            elif "tokenizer" in name.lower(): cat = "tokenizer"
            files.append({"name":name,"size":size,"size_h":human_size(size),"ext":ext,"category":cat,
                "url":f"https://huggingface.co/{repo_id}/resolve/main/{name}"})
        order = {"gguf":0,"quantized":1,"weights":2,"tokenizer":3,"config":4,"other":5}
        files.sort(key=lambda f:(order.get(f["category"],9),f["name"]))
        return jsonify({"repo_id":repo_id,"model_name":info.get("modelId",repo_id),
            "tags":info.get("tags",[])[:20],"files":files,"total_size":human_size(sum(f["size"] for f in files))})
    except requests.exceptions.ConnectionError:
        return jsonify({"error":"Cannot reach HuggingFace."}), 502
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.json or {}; url = data.get("url","").strip(); sub = data.get("subfolder","").strip()
    if not url: return jsonify({"error":"No URL"}), 400
    fn = url.rstrip("/").split("/")[-1].split("?")[0]
    if sub: os.makedirs(os.path.join(MODEL_DIR,sub), exist_ok=True); dest = os.path.join(MODEL_DIR,sub,fn)
    else: dest = os.path.join(MODEL_DIR,fn)
    tid = f"{int(time.time())}_{fn}"
    with _lock: downloads[tid] = {"status":"starting","filename":fn,"url":url,"dest":dest}
    threading.Thread(target=download_file,args=(url,dest,tid,fn),daemon=True).start()
    return jsonify({"task_id":tid})

@app.route("/api/download/batch", methods=["POST"])
def batch_download():
    data = request.json or {}; files = data.get("files",[]); sub = data.get("subfolder","").strip()
    tids = []
    for f in files:
        url = f.get("url","").strip()
        if not url: continue
        fn = url.rstrip("/").split("/")[-1].split("?")[0]
        if sub: os.makedirs(os.path.join(MODEL_DIR,sub),exist_ok=True); dest=os.path.join(MODEL_DIR,sub,fn)
        else: dest=os.path.join(MODEL_DIR,fn)
        tid = f"{int(time.time())}_{fn}"
        with _lock: downloads[tid]={"status":"starting","filename":fn,"url":url,"dest":dest}
        _dl_pool.submit(download_file, url, dest, tid, fn)
        tids.append(tid); time.sleep(0.05)
    return jsonify({"task_ids":tids})

@app.route("/api/models")
def list_models():
    result = []
    for root,dirs,files in os.walk(MODEL_DIR):
        for f in files:
            if f.endswith(".part"): continue
            full = os.path.join(root,f); rel = os.path.relpath(full,MODEL_DIR)
            result.append({"name":rel,"size":os.path.getsize(full),"size_h":human_size(os.path.getsize(full))})
    result.sort(key=lambda x:x["name"]); return jsonify(result)

@app.route("/api/status")
def status():
    with _lock: return jsonify(dict(downloads))

@app.route("/api/clear_completed", methods=["POST"])
def clear_completed():
    with _lock:
        rm=[t for t,i in downloads.items() if i.get("status") in ("complete","error","interrupted")]
        for t in rm: del downloads[t]
    save_state(); return jsonify({"cleared":len(rm)})

if __name__ == "__main__": app.run(host="0.0.0.0", port=5000)
'''

# ══════════════════════════════════════════════════════════════════════════════
#  DOWNLOADER HTML — HuggingFace browser + direct URL + downloads + library
# ══════════════════════════════════════════════════════════════════════════════

DOWNLOADER_INDEX_HTML = r'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wickerman — Model Downloader</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{background:#11111b;color:#cdd6f4;font-family:system-ui,sans-serif;padding:24px;min-height:100vh}
.c{max-width:900px;margin:0 auto}h1{text-align:center;margin-bottom:4px}.sub{text-align:center;color:#6c7086;margin-bottom:24px;font-size:14px}
.nav{text-align:center;margin-bottom:20px}.nav a{color:#89b4fa;text-decoration:none;font-size:15px}
.tabs{display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid #313244}
.tab{padding:10px 20px;cursor:pointer;color:#6c7086;font-size:14px;font-weight:600;border-bottom:2px solid transparent}.tab:hover{color:#cdd6f4}.tab.active{color:#89b4fa;border-color:#89b4fa}
.tc{display:none}.tc.active{display:block}
.sf{display:flex;gap:8px;margin-bottom:16px}.sf input{flex:1;padding:12px 16px;border-radius:8px;border:1px solid #313244;background:#1e1e2e;color:#cdd6f4;font-size:15px;outline:none}
.sf input:focus{border-color:#89b4fa}.sf input::placeholder{color:#6c7086}
.sf button,.btn{padding:12px 20px;border-radius:8px;border:none;background:#89b4fa;color:#1e1e2e;font-weight:700;font-size:14px;cursor:pointer;white-space:nowrap}
.sf button:hover,.btn:hover{background:#74c7ec}.sf button:disabled{opacity:.5;cursor:not-allowed}
.btn2{background:#313244;color:#cdd6f4}.btn2:hover{background:#45475a}.btns{padding:6px 14px;font-size:12px;border-radius:6px}
.mi{background:#1e1e2e;border-radius:10px;padding:16px;margin-bottom:16px}.mi h2{font-size:18px;margin-bottom:8px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.tg{background:#313244;color:#a6adc8;padding:2px 10px;border-radius:12px;font-size:12px}
.fcat{color:#89b4fa;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid #313244}
.fi{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#1e1e2e;border-radius:6px;margin-bottom:4px;font-size:14px}
.fi input{accent-color:#89b4fa}.fn{flex:1;word-break:break-all}.fs{color:#6c7086;white-space:nowrap;font-size:13px}
.fe{background:#313244;color:#a6adc8;padding:1px 8px;border-radius:4px;font-size:11px;font-family:monospace}
.da{display:flex;gap:8px;align-items:center;margin-bottom:16px}.di{color:#6c7086;font-size:13px}
.task{background:#1e1e2e;border-radius:10px;padding:16px;margin-bottom:10px}
.th{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.tn{font-size:14px;font-weight:600}
.bb{height:8px;background:#313244;border-radius:4px;overflow:hidden}.bf{height:100%;background:linear-gradient(90deg,#89b4fa,#74c7ec);border-radius:4px;transition:width .3s}
.ts{font-size:12px;color:#a6adc8;margin-top:6px}.ts.complete{color:#a6e3a1}.ts.error{color:#f38ba8}.ts.interrupted{color:#f9e2af}
.st{font-size:16px;color:#a6adc8;margin-bottom:12px;border-bottom:1px solid #313244;padding-bottom:8px;display:flex;justify-content:space-between}
.ml{list-style:none}.mli{display:flex;justify-content:space-between;padding:12px 16px;background:#1e1e2e;border-radius:8px;margin-bottom:6px;font-size:14px}
.ms{color:#6c7086;white-space:nowrap;margin-left:12px}
.empty{color:#6c7086;text-align:center;padding:32px;font-size:14px}
.spin{display:inline-block;width:16px;height:16px;border:2px solid #313244;border-top-color:#89b4fa;border-radius:50%;animation:sp .8s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}
</style></head><body>
<div class="c">
<h1>Model Downloader</h1><p class="sub">HuggingFace model browser &amp; direct URL downloads</p>
<div class="nav"><a href="http://wickerman.local">&larr; Dashboard</a></div>
<div class="tabs">
<div class="tab active" data-tab="hf" onclick="sw('hf')">HuggingFace</div>
<div class="tab" data-tab="url" onclick="sw('url')">Direct URL</div>
<div class="tab" data-tab="dl" onclick="sw('dl')">Downloads</div>
<div class="tab" data-tab="lib" onclick="sw('lib')">Library</div>
</div>
<div id="t-hf" class="tc active">
<div class="sf"><input type="text" id="hfQ" placeholder="TheBloke/Mistral-7B-v0.2-GGUF or paste HF URL"><button id="hfB" onclick="hfLookup()">Browse Files</button></div>
<div id="hfR"></div>
</div>
<div id="t-url" class="tc">
<div class="sf"><input type="text" id="urlI" placeholder="https://huggingface.co/.../resolve/main/model.gguf"><button onclick="directDL()">Download</button></div>
</div>
<div id="t-dl" class="tc">
<div class="st"><span>Active Downloads</span><button class="btn2 btns" onclick="clearDone()">Clear Completed</button></div>
<div id="tasks"></div>
</div>
<div id="t-lib" class="tc">
<div class="st">Downloaded Models</div>
<ul class="ml" id="models"></ul>
</div>
</div>
<script>
function sw(n){document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===n));document.querySelectorAll('.tc').forEach(t=>t.classList.toggle('active',t.id==='t-'+n))}
function hs(b){if(!b)return"?";const u=["B","KB","MB","GB","TB"];let i=0;while(b>=1024&&i<u.length-1){b/=1024;i++}return b.toFixed(i>0?1:0)+" "+u[i]}
let hfF=[],hfR="";
async function hfLookup(){const i=document.getElementById("hfQ"),b=document.getElementById("hfB"),q=i.value.trim();if(!q)return;b.disabled=true;b.innerHTML='<span class="spin"></span> Looking up...';document.getElementById("hfR").innerHTML="";try{const r=await fetch("/api/hf/lookup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query:q})});const d=await r.json();if(d.error){document.getElementById("hfR").innerHTML=`<div class="empty" style="color:#f38ba8">${d.error}</div>`;return}hfF=d.files;hfR=d.repo_id;renderHF(d)}catch(e){document.getElementById("hfR").innerHTML=`<div class="empty" style="color:#f38ba8">Failed: ${e}</div>`}finally{b.disabled=false;b.textContent="Browse Files"}}
function renderHF(d){const cats={gguf:"GGUF Quantizations",quantized:"Quantized",weights:"Model Weights",tokenizer:"Tokenizer",config:"Config",other:"Other"};let h=`<div class="mi"><h2>${d.model_name}</h2>`;if(d.tags.length)h+=`<div class="tags">${d.tags.map(t=>`<span class="tg">${t}</span>`).join("")}</div>`;h+=`<div style="color:#6c7086;font-size:13px">Total: ${d.total_size} across ${d.files.length} files</div></div>`;h+=`<div class="da"><button class="btn" onclick="dlSel()">Download Selected</button><button class="btn btn2" onclick="selGGUF()">Select GGUF</button><button class="btn btn2" onclick="selNone()">Select None</button><span class="di" id="si">0 selected</span></div>`;h+='<div>';let lc="";d.files.forEach((f,i)=>{if(f.category!==lc){lc=f.category;h+=`<div class="fcat">${cats[f.category]||f.category}</div>`}h+=`<div class="fi"><input type="checkbox" data-i="${i}" onchange="upd()"><span class="fe">${f.ext||"?"}</span><span class="fn">${f.name}</span><span class="fs">${f.size_h}</span></div>`});h+='</div>';document.getElementById("hfR").innerHTML=h}
function gc(){return[...document.querySelectorAll('#hfR input[type=checkbox]:checked')].map(c=>hfF[c.dataset.i])}
function upd(){const s=gc(),t=s.reduce((a,f)=>a+f.size,0);document.getElementById("si").textContent=`${s.length} file${s.length!==1?"s":""} (${hs(t)})`}
function selGGUF(){document.querySelectorAll('#hfR input[type=checkbox]').forEach(c=>{c.checked=hfF[c.dataset.i].category==="gguf"});upd()}
function selNone(){document.querySelectorAll('#hfR input[type=checkbox]').forEach(c=>c.checked=false);upd()}
async function dlSel(){const s=gc();if(!s.length){alert("Select files first");return}try{await fetch("/api/download/batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({files:s,subfolder:hfR.replace("/","_")})});sw("dl")}catch(e){alert("Failed: "+e)}}
async function directDL(){const i=document.getElementById("urlI"),u=i.value.trim();if(!u)return;try{const r=await fetch("/api/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url:u})});const d=await r.json();if(d.error){alert(d.error);return}i.value="";sw("dl")}catch(e){alert("Failed: "+e)}}
async function poll(){try{const a=await(await fetch("/api/status")).json();const el=document.getElementById("tasks");const e=Object.entries(a);if(!e.length){el.innerHTML='<div class="empty">No downloads</div>';return}const newIds=new Set(e.map(([t])=>t));el.querySelectorAll('[data-tid]').forEach(n=>{if(!newIds.has(n.dataset.tid))n.remove()});for(const[t,i]of e){const p=i.total?Math.round(i.downloaded/i.total*100):0;const sc=["complete","error","interrupted"].includes(i.status)?i.status:"";const st=i.status==="downloading"?hs(i.downloaded)+" / "+hs(i.total):i.status==="error"?"Error: "+(i.error||"?"):i.status;let node=el.querySelector(`[data-tid="${t}"]`);if(!node){node=document.createElement('div');node.className='task';node.dataset.tid=t;el.appendChild(node)}node.innerHTML=`<div class="th"><span class="tn">${i.filename||t}</span><span>${i.status==="downloading"?p+"%":""}</span></div>${i.status==="downloading"?`<div class="bb"><div class="bf" style="width:${p}%"></div></div>`:""}<div class="ts ${sc}">${st}</div>`}}catch(e){}}
async function pollM(){try{const f=await(await fetch("/api/models")).json();document.getElementById("models").innerHTML=f.length?f.map(f=>`<li class="mli"><span>${f.name}</span><span class="ms">${f.size_h}</span></li>`).join(""):"<li class='empty'>No models yet.</li>"}catch(e){}}
async function clearDone(){try{await fetch("/api/clear_completed",{method:"POST"});poll()}catch(e){}}
poll();pollM();setInterval(poll,2000);setInterval(pollM,10000);
document.getElementById("hfQ").addEventListener("keydown",e=>{if(e.key==="Enter")hfLookup()});
document.getElementById("urlI").addEventListener("keydown",e=>{if(e.key==="Enter")directDL()});
</script></body></html>
'''

# ══════════════════════════════════════════════════════════════════════════════
#  DOWNLOADER DOCKERFILE & REQUIREMENTS
# ══════════════════════════════════════════════════════════════════════════════

DOWNLOADER_REQUIREMENTS = "flask==3.0.*\nrequests==2.32.*\ngunicorn==22.*\n"

DOWNLOADER_DOCKERFILE = """\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
"""

# ══════════════════════════════════════════════════════════════════════════════
#  NGINX CONFIG GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

GENERATE_NGINX_PY = '''\
#!/usr/bin/env python3
import json, glob, os, sys
INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
vhosts = []
seen = set()
# Build list of directories to scan for plugin manifests
# Must work on host (~/wickerman, ~/WickermanSupport) AND inside container (/app, /support)
scan_dirs = [
    os.path.join(INSTALL_DIR, "plugins"),
]
# Inside container: /support/plugins is the mounted WickermanSupport
if os.path.isdir("/support/plugins"):
    scan_dirs.append("/support/plugins")
# On host: derive from INSTALL_DIR parent (e.g. ~/WickermanSupport)
host_support = os.path.join(os.path.dirname(INSTALL_DIR), "WickermanSupport", "plugins")
if os.path.isdir(host_support):
    scan_dirs.append(host_support)
# Also check HOST_SUPPORT_DIR env var
env_support = os.environ.get("HOST_SUPPORT_DIR", "")
if env_support and os.path.isdir(os.path.join(env_support, "plugins")):
    scan_dirs.append(os.path.join(env_support, "plugins"))
print(f"Nginx generator: scanning {scan_dirs}", file=sys.stderr)
for scan_dir in scan_dirs:
    for path in glob.glob(os.path.join(scan_dir, "*.json")):
        fname = os.path.basename(path)
        if fname in seen: continue
        seen.add(fname)
        try:
            with open(path) as f: m = json.load(f)
            if m.get("nginx_host") and m.get("container_name"):
                port = m.get("ports", [80])[0]
                varname = m["container_name"].replace("-", "_")
                host = m["nginx_host"]
                cname = m["container_name"]
                block = (
                    "\\n    server {"
                    "\\n        listen 80;"
                    "\\n        server_name " + host + ";"
                    "\\n        location / {"
                    "\\n            set $upstream_" + varname + " http://" + cname + ":" + str(port) + ";"
                    "\\n            proxy_pass $upstream_" + varname + ";"
                    "\\n            proxy_http_version 1.1;"
                    "\\n            proxy_set_header Upgrade $http_upgrade;"
                    '\\n            proxy_set_header Connection "upgrade";'
                    "\\n            proxy_set_header Host $host;"
                    "\\n            proxy_hide_header X-Frame-Options;"
                    "\\n            proxy_hide_header Content-Security-Policy;"
                    "\\n        }"
                    "\\n        error_page 502 503 504 @starting;"
                    "\\n        location @starting {"
                    "\\n            default_type text/html;"
                    "\\n            add_header Cache-Control 'no-cache, no-store, must-revalidate';"
                    "\\n            return 200 '<html><body style=background:#11111b;color:#cdd6f4;font-family:sans-serif;text-align:center;padding:60px><h2>Plugin is starting up...</h2><p>Wait a moment and refresh.</p></body></html>';"
                    "\\n        }"
                    "\\n    }"
                )
                vhosts.append(block)
                print(f"  vhost: {host} -> {cname}:{port}", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR processing {fname}: {e}", file=sys.stderr)
print(f"  Total vhosts: {len(vhosts)}", file=sys.stderr)
conf = """
events { worker_connections 1024; }
http {
    resolver 127.0.0.11 valid=10s;
    server {
        listen 80;
        server_name wickerman.local;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
        location / {
            proxy_pass http://wm-core:8000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
        }
        error_page 502 503 504 @starting;
        location @starting {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            return 200 '<html><body style="background:#11111b;color:#cdd6f4;font-family:sans-serif;text-align:center;padding:60px"><h2>Wickerman OS is starting up...</h2><p>Wait a moment and refresh.</p></body></html>';
            add_header Content-Type text/html;
        }
    }
    server {
        listen 80;
        server_name downloader.wickerman.local;
        client_max_body_size 0;
        location / {
            proxy_pass http://wm-downloader:5000;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_read_timeout 3600;
            proxy_hide_header X-Frame-Options;
            proxy_hide_header Content-Security-Policy;
        }
    }
""" + "".join(vhosts) + """
}
"""
with open(os.path.join(INSTALL_DIR, "nginx", "nginx.conf"), "w") as f: f.write(conf)
print("Nginx config written.", file=sys.stderr)
'''
