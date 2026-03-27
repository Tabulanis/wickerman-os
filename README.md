# Wickerman OS

**Self-hosted AI operating system.** Run local language models, chain them into pipelines, and manage everything from a single dashboard. No cloud required.

![Version](https://img.shields.io/badge/version-5.6.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-orange)

## What Is This?

Wickerman OS is a Docker-based platform that turns your machine into a local AI command center. It bundles model inference, chat, visual pipelines, model training, code generation, and model evaluation behind a single installer and web dashboard.

**Key features:**
- **Model Router** — Load multiple local models simultaneously, configure them as agents with system prompts, RAG memory, and custom settings. Also supports remote APIs (OpenAI, Anthropic, Google Gemini) behind the same unified endpoint.
- **Chat** — Multi-conversation UI with per-chat agent selection. Conversations persist server-side.
- **Flow Editor** — Visual drag-and-drop pipeline builder (Flowise). Chain agents together.
- **Model Trainer** — Fine-tune models with LoRA using Unsloth. Full pipeline: download, train, export to GGUF.
- **Code Forge** — Real local IDE with Monaco editor (VS Code engine), integrated terminal, per-project isolation, AI assist, and human-in-loop agent mode.
- **Model Probe** — Systematic model evaluation. Run probe banks against any agent, score responses across six categories, drill into failures, and export correction datasets directly to the Trainer.
- **RAG Memory** — Each agent has its own FAISS-powered vector memory. Old conversation context is automatically archived and retrieved when relevant.

## Requirements

- **OS:** Linux (tested on Pop!_OS / Ubuntu 22.04+)
- **Docker:** Docker Engine 20.10+
- **GPU:** NVIDIA GPU recommended (CUDA support). CPU-only mode available.
- **RAM:** 16GB+ recommended
- **Disk:** 20GB+ for the platform, plus space for models

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Tabulanis/wickerman-os.git ~/aidojo
cd ~/aidojo

# (Optional) Add GGUF models to the models/ directory
# cp /path/to/your-model.gguf models/

# Install
sudo python3 wickermaninstall.py

# Start
cd ~/wickerman && sudo ./start.sh
```

Open `http://wickerman.local` in your browser.

## First Steps

1. **Install the Model Router** — Click INSTALL on the Model Router card. First build takes ~10 minutes (compiles llama.cpp with CUDA).
2. **Load a model** — Open the Model Router, configure a model with a system prompt and settings, click "Load agent".
3. **Install Chat** — Click INSTALL on the Chat card. Open it, pick your agent, start chatting.
4. **Browse the Codex** — The Codex tab has full documentation, searchable.

## Project Structure

```
~/aidojo/                          # Install source (this repo)
  wickermaninstall.py              # Single-file installer
  wickerman_support.py             # Dashboard + nginx generator
  wickerman_plugins/               # Plugin source code
    __init__.py                    # Auto-discovers all wm_*.py plugins
    wm_llama.py                   # Model Router (agents, RAG, providers)
    wm_chat.py                    # Chat UI
    wm_flow.py                    # Flow Editor (Flowise)
    wm_trainer.py                 # Model Trainer (Unsloth)
    wm_forge.py                   # Code Forge (Monaco IDE)
    wm_probe.py                   # Model Probe (evaluation engine)
  models/                         # Local GGUF models (copied on install)
  stop.sh                         # Stop all containers

~/wickerman/                       # Runtime (generated on install)
~/WickermanSupport/                # Persistent data (survives reinstall)
  models/                         # Your GGUF model files
  plugins/                        # Plugin manifests and data
  datasets/                       # Training datasets
    probes/                       # Probe banks (.jsonl files)
  loras/                          # Fine-tuned adapters
  workspace/                      # Code Forge projects
```

## Architecture

All inference flows through the Model Router's unified `/v1/chat/completions` endpoint, regardless of whether the model runs locally or via a remote API.

- **Model Router** manages agents (local llama.cpp + remote APIs), RAG memory, system prompts, and settings
- **Chat** is a thin conversation UI that picks an agent and manages history
- **Flow Editor** chains agents into visual pipelines
- **Code Forge** is a full local IDE — Monaco editor, terminal, per-project venv isolation, AI assist
- **Model Probe** evaluates any agent against structured probe banks and feeds failures back to the Trainer
- All plugins talk to the Router — they never know where inference happens

## Agents

An agent is a fully configured AI endpoint: a model (local or remote) with a system prompt, RAG memory, and sampling settings baked in. Create them in the Model Router dashboard.

## Adding Plugins

Drop any `wm_*.py` file into `~/aidojo/wickerman_plugins/` and it will be auto-discovered on the next install. No manual registration required.

## API

The Model Router exposes an OpenAI-compatible API:

```bash
# Chat with an agent
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "code-assistant", "messages": [{"role": "user", "content": "Hello"}]}'

# List loaded agents
curl http://localhost:8080/v1/models

# Get VRAM usage
curl http://localhost:8080/api/vram
```

## Models

Place `.gguf` files in the `models/` directory before installing, or use the built-in Downloader after installation.

Tested models:
- **Qwen2.5-Coder-14B** (Q5_K_M / Q8_0) — excellent for coding
- **TinyLlama 1.1B** (Q4_K_M) — fast test/fallback model
- **all-MiniLM-L6-v2** — embedding model for RAG memory

## Reinstalling / Updating

```bash
cd ~/aidojo
git pull
sudo python3 wickermaninstall.py
```

Your models, datasets, and plugin data in `~/WickermanSupport/` survive reinstalls. For a complete reset: `sudo python3 wickermaninstall.py --hard-reset`

## License

MIT License. See [LICENSE](LICENSE) for details.

## Credits

Built by Tabulanis.

Powered by [llama.cpp](https://github.com/ggerganov/llama.cpp), [NiceGUI](https://nicegui.io/), [Flowise](https://flowiseai.com/), [Unsloth](https://github.com/unslothai/unsloth), [FAISS](https://github.com/facebookresearch/faiss).

## Changelog

### v5.6.0
- **Model Probe** — Systematic model evaluation engine. Run structured probe banks against any loaded agent across six categories: factual accuracy, empirical clarity, reasoning, uncertainty handling, consistency, and custom domain. Scores responses using auto-scoring and LLM-as-judge. Drill into failures, approve correction pairs, and export directly to the Trainer. Ships with 115 built-in empirical probes including 40 adversarial baited questions.
- **Code Forge** — Full local IDE with Monaco editor (VS Code engine), integrated terminal (xterm.js + PTY), file tree, multi-file projects, per-project venv and node_modules isolation, AI assist (generate/explain/fix/refactor), and human-in-loop agent mode (plan → approve → code → approve → apply).
- **Auto-discovery** — `wickerman_plugins/__init__.py` now auto-discovers all `wm_*.py` files. Drop a new plugin in the directory and it registers automatically on the next install.
- **Fixed** — Plugin data directories now get correct permissions on install so container users can write to volumes.
- **Fixed** — Dashboard timer crashes (RuntimeError on deleted NiceGUI elements) fully resolved across all four timers.

### v5.4.0
- **Model Customization Studio** — Complete training pipeline: HuggingFace model download, SFT fine-tuning with presets, and GGUF export via Unsloth native conversion. No external build tools required.
- **Fixed** — NiceGUI timer crash on long plugin builds.

### v5.3.0
- **RAG Library** — Build named RAG indexes from datasets (TXT, JSONL, CSV). Agents can now use a dataset RAG instead of conversation memory. Create domain experts by pointing an agent at a knowledge base.
- **Fixed** — Dashboard no longer resets/blinks during plugin installs or Docker operations (`reload=False`).

### v5.2.0
- Model Router with full agent orchestration (local + remote APIs)
- Multi-conversation Chat UI with server-side history
- Flow Editor, Model Trainer, Code Forge plugins
- Dual-repo Git architecture with auto-commits
