"""
Wickerman OS v5.6.0 — Model Customization Studio plugin manifest.

Stage 1: HuggingFace download -> SFT training -> Merge -> GGUF -> Quantize -> Model Router
Stage 2 (planned): DPO training, AI-assisted pair generation, correction/shape/focus modes
Stage 3 (planned): Compare mode, full preset system, dataset manager
"""

WM_TRAINER = {
    "name": "Model Studio",
    "description": "Customize local models — teach, correct, shape, and export to GGUF",
    "icon": "model_training",
    "build": True,
    "build_context": "data",
    "container_name": "wm-trainer",
    "url": "http://trainer.wickerman.local",
    "ports": [5000],
    "gpu": True,
    "env": [
        "MODEL_DIR=/models",
        "DATASET_DIR=/datasets",
        "LORA_DIR=/loras",
        "HF_HOME=/hf_cache",
        "OUTPUT_DIR=/data/outputs",
        "LLAMA_API=http://wm-llama:8080",
    ],
    "volumes": [
        "{models}:/models",
        "{datasets}:/datasets",
        "{loras}:/loras",
        "{support}/hf_cache:/hf_cache",
        "{self}/data:/data",
    ],
    "nginx_host": "trainer.wickerman.local",
    "help": (
        "## Model Customization Studio\n"
        "Full pipeline for customizing local AI models.\n\n"
        "**Stage 1 — Get a Model:** Download any HuggingFace model. "
        "Downloads are cached so you only wait once.\n\n"
        "**Stage 2 — Train:** Fine-tune on your own datasets with simple presets. "
        "No need to know what LoRA rank means.\n\n"
        "**Stage 3 — Export:** Merge, convert to GGUF, quantize, and send directly "
        "to the Model Router. Your customized model appears as a new agent.\n\n"
        "**Correct / Shape / Focus (coming soon):** Remove bias, fix wrong facts, "
        "change behavior using AI-assisted DPO training.\n\n"
        "**Models directory:** ~/WickermanSupport/models/\n"
        "**HF cache:** ~/WickermanSupport/hf_cache/ (survives reinstalls)"
    ),
}

WM_TRAINER_FILES = {

"data/Dockerfile": r"""FROM nvidia/cuda:12.1.1-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/hf_cache
ENV TRANSFORMERS_CACHE=/hf_cache
ENV HF_DATASETS_CACHE=/hf_cache/datasets

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev git curl build-essential \
    wget && rm -rf /var/lib/apt/lists/*

# Core Python deps
RUN pip3 install --no-cache-dir \
    flask==3.0.* gunicorn==22.* requests==2.32.* \
    huggingface_hub==0.23.*

# PyTorch for CUDA 12.1
RUN pip3 install --no-cache-dir \
    torch==2.4.0 torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu121

# Unsloth + training stack
# Unsloth - install from PyPI after torch is present (auto-detects CUDA)
RUN pip3 install --no-cache-dir unsloth
RUN pip3 install --no-cache-dir \
    datasets transformers trl peft accelerate bitsandbytes sentencepiece protobuf

# gguf package needed for Unsloth's built-in GGUF export
# Unsloth handles conversion + quantization via save_pretrained_gguf() — no external tools needed
RUN pip3 install --no-cache-dir gguf transformers accelerate

WORKDIR /app
ARG CACHEBUST=1
COPY . .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "0", "app:app"]
""",

"data/app.py": r"""#!/usr/bin/env python3
# Wickerman Model Customization Studio - Backend
# Stage 1: Download -> SFT Train -> Merge -> GGUF -> Quantize -> Export
# Stage 2 (stub): DPO training, AI pair generation
# Stage 3 (stub): Compare mode
import os, sys, json, threading, time, subprocess, glob, re, shutil, requests
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)

# ── Paths ──────────────────────────────────────────────────
MODEL_DIR    = os.environ.get("MODEL_DIR",    "/models")       # WickermanSupport/models (GGUF output)
DATASET_DIR  = os.environ.get("DATASET_DIR",  "/datasets")     # WickermanSupport/datasets
LORA_DIR     = os.environ.get("LORA_DIR",     "/loras")        # WickermanSupport/loras
HF_CACHE_DIR = os.environ.get("HF_HOME", "/hf_cache")          # WickermanSupport/hf_cache
OUTPUT_DIR   = os.environ.get("OUTPUT_DIR",   "/data/outputs") # Temp outputs (merge/convert workspace)
LLAMA_API    = os.environ.get("LLAMA_API",    "http://wm-llama:8080")

HF_MODELS_DIR = os.path.join(HF_CACHE_DIR, "hub")  # Where HF stores downloaded models
MERGED_DIR    = os.path.join(OUTPUT_DIR, "merged")  # Merged HF model before GGUF conversion

for d in [MODEL_DIR, DATASET_DIR, LORA_DIR, HF_CACHE_DIR, OUTPUT_DIR, MERGED_DIR]:
    os.makedirs(d, exist_ok=True)

CONVERT_SCRIPT = "/opt/convert_hf_to_gguf.py"  # fallback, not used with Unsloth export
QUANTIZE_BIN = None  # not needed — Unsloth handles quantization natively
# llama-quantize not needed — Unsloth exports GGUF natively

# ── Job State ──────────────────────────────────────────────
_job = {
    "status": "idle",       # idle | running | complete | error
    "stage": "",            # download | train | merge | convert | quantize | done
    "log": [],
    "progress": 0,          # 0-100
    "current": None,        # current job config dict
    "result": None,         # path to final GGUF on success
}
_lock = threading.Lock()

def _log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    icons = {"INFO": "", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "STEP": "▶"}
    icon = icons.get(level, "")
    line = f"[{ts}] {icon} {msg}".strip()
    with _lock:
        _job["log"].append(line)
        if len(_job["log"]) > 1000:
            _job["log"] = _job["log"][-1000:]
    print(line, flush=True)

def _set_stage(stage, progress=None):
    with _lock:
        _job["stage"] = stage
        if progress is not None:
            _job["progress"] = progress

def _finish(result_path=None):
    with _lock:
        _job["status"] = "complete" if result_path else "error"
        _job["stage"] = "done"
        _job["progress"] = 100 if result_path else _job["progress"]
        _job["result"] = result_path

def _safe_path(base, user_input):
    joined = os.path.abspath(os.path.join(base, user_input))
    if not joined.startswith(os.path.abspath(base)):
        raise ValueError(f"Path traversal blocked: {user_input}")
    return joined

# ── HuggingFace Helpers ────────────────────────────────────
def _hf_model_local_path(repo_id):
    # Return the local cache path for a HF model repo, or None if not cached.
    # HF stores as models--owner--repo
    safe = repo_id.replace("/", "--")
    candidate = os.path.join(HF_MODELS_DIR, f"models--{safe}")
    if os.path.isdir(candidate):
        return candidate
    return None

def _hf_model_snapshot(repo_id):
    # Return the snapshot path (actual model files) for a cached HF model.
    base = _hf_model_local_path(repo_id)
    if not base:
        return None
    snapshots_dir = os.path.join(base, "snapshots")
    if not os.path.isdir(snapshots_dir):
        return None
    snaps = sorted(os.listdir(snapshots_dir))
    if not snaps:
        return None
    return os.path.join(snapshots_dir, snaps[-1])

def list_cached_hf_models():
    # List all HF models in the cache with basic info.
    models = []
    if not os.path.isdir(HF_MODELS_DIR):
        return models
    for entry in os.listdir(HF_MODELS_DIR):
        if not entry.startswith("models--"):
            continue
        repo_id = entry[len("models--"):].replace("--", "/", 1)
        snap = _hf_model_snapshot(repo_id)
        size_gb = 0
        if snap:
            for root, dirs, files in os.walk(snap):
                for f in files:
                    try:
                        size_gb += os.path.getsize(os.path.join(root, f))
                    except: pass
            size_gb = round(size_gb / 1024**3, 1)
        models.append({
            "repo_id": repo_id,
            "name": repo_id.split("/")[-1],
            "cached": snap is not None,
            "size_gb": size_gb,
            "snapshot_path": snap,
        })
    return models

def list_loras():
    # List all saved LoRA adapters.
    loras = []
    for entry in os.listdir(LORA_DIR):
        full = os.path.join(LORA_DIR, entry)
        if os.path.isdir(full):
            meta_path = os.path.join(full, "wm_meta.json")
            meta = {}
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                except: pass
            loras.append({
                "name": entry,
                "base_model": meta.get("base_model", "unknown"),
                "trained_at": meta.get("trained_at", ""),
                "mode": meta.get("mode", "sft"),
                "path": full,
            })
    return loras

def list_gguf_models():
    # List GGUF files in the models dir.
    models = []
    for f in sorted(glob.glob(os.path.join(MODEL_DIR, "*.gguf"))):
        name = os.path.basename(f)
        size_gb = round(os.path.getsize(f) / 1024**3, 1)
        models.append({"name": name, "size_gb": size_gb, "path": f})
    return models

# ── Stage: Download ─────────────────────────────────────────
def run_download(repo_id, token=None):
    _set_stage("download", 5)
    _log(f"Starting download: {repo_id}", "STEP")
    _log("Models are cached — you only wait once per model.")

    try:
        from huggingface_hub import snapshot_download, hf_hub_download
        import huggingface_hub

        _log(f"Connecting to HuggingFace Hub...")
        kwargs = {
            "repo_id": repo_id,
            "cache_dir": HF_CACHE_DIR,
            "ignore_patterns": ["*.msgpack", "*.h5", "flax_*", "tf_*", "rust_*"],
        }
        if token:
            kwargs["token"] = token

        _set_stage("download", 10)
        _log("Downloading model files — this may take several minutes for large models.")
        _log("Progress is shown as files complete.")

        path = snapshot_download(**kwargs)
        _set_stage("download", 90)
        _log(f"Download complete! Cached at: {path}", "OK")

        # Verify it looks like a model
        has_config = os.path.isfile(os.path.join(path, "config.json"))
        has_weights = any(
            glob.glob(os.path.join(path, p))
            for p in ["*.safetensors", "*.bin", "*.pt"]
        )
        if not has_config:
            _log("Warning: no config.json found. This may not be a valid model.", "WARN")
        if not has_weights:
            _log("Warning: no weight files found. Check the repo ID.", "WARN")

        _set_stage("download", 100)
        _log(f"Model ready for training: {repo_id}", "OK")
        return True, path

    except Exception as e:
        _log(f"Download failed: {e}", "ERROR")
        if "401" in str(e) or "403" in str(e):
            _log("This model requires a HuggingFace token. Add your token in the download form.", "WARN")
        elif "404" in str(e):
            _log("Model not found. Check the repo ID (e.g. 'Qwen/Qwen2.5-7B').", "WARN")
        return False, str(e)

# ── Stage: SFT Training ─────────────────────────────────────
def run_sft_training(config):
    repo_id    = config["repo_id"]
    dataset    = config["dataset"]
    output_name = config.get("output_name") or f"lora_{int(time.time())}"
    epochs     = int(config.get("epochs", 3))
    lr         = float(config.get("learning_rate", 2e-4))
    lora_r     = int(config.get("lora_r", 16))
    lora_alpha = int(config.get("lora_alpha", lora_r))
    batch_size = int(config.get("batch_size", 2))
    grad_accum = int(config.get("grad_accum", 4))
    max_seq    = int(config.get("max_seq_length", 2048))
    text_field = config.get("text_field", "text")
    load_4bit  = config.get("load_in_4bit", True)

    _set_stage("train", 5)
    _log("Starting fine-tuning...", "STEP")

    snap = _hf_model_snapshot(repo_id)
    if not snap:
        _log(f"Model not found in cache. Please download it first.", "ERROR")
        return False, "Model not cached"

    dataset_path = _safe_path(DATASET_DIR, dataset)
    if not os.path.isfile(dataset_path):
        _log(f"Dataset not found: {dataset}", "ERROR")
        return False, "Dataset not found"

    try:
        from unsloth import FastLanguageModel
        from datasets import load_dataset
        from trl import SFTTrainer, SFTConfig
        from transformers import TrainingArguments

        _log(f"Loading model: {repo_id}")
        _log("This takes 1-2 minutes — loading weights into GPU memory...")
        _set_stage("train", 15)

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=snap,
            max_seq_length=max_seq,
            load_in_4bit=load_4bit,
        )
        _log("Model loaded into GPU memory.", "OK")
        _set_stage("train", 25)

        _log(f"Applying LoRA adapters (rank={lora_r})...")
        model = FastLanguageModel.get_peft_model(
            model,
            r=lora_r,
            target_modules=["q_proj","k_proj","v_proj","o_proj",
                            "gate_proj","up_proj","down_proj"],
            lora_alpha=lora_alpha,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing="unsloth",
        )
        _log("LoRA adapters applied.", "OK")
        _set_stage("train", 35)

        _log(f"Loading dataset: {dataset}")
        ext = dataset_path.rsplit(".", 1)[-1].lower()
        if ext == "jsonl" or ext == "json":
            ds = load_dataset("json", data_files=dataset_path, split="train")
        elif ext == "csv":
            ds = load_dataset("csv", data_files=dataset_path, split="train")
        else:
            ds = load_dataset("json", data_files=dataset_path, split="train")
        _log(f"Dataset loaded: {len(ds):,} examples.", "OK")

        # Validate text field exists
        if text_field not in ds.column_names:
            available = ", ".join(ds.column_names)
            _log(f"Text field '{text_field}' not found. Available columns: {available}", "WARN")
            text_field = ds.column_names[0]
            _log(f"Using '{text_field}' instead.")

        _set_stage("train", 40)

        output_path = os.path.join(LORA_DIR, output_name)
        os.makedirs(output_path, exist_ok=True)

        total_steps = max(1, (len(ds) * epochs) // (batch_size * grad_accum))
        _log(f"Training: {epochs} epoch(s), ~{total_steps} steps. This may take a while.")

        class ProgressCallback:
            def __init__(self):
                self.last_step = 0
            def on_log(self, args, state, control, logs=None, **kwargs):
                if logs and state.global_step > self.last_step:
                    self.last_step = state.global_step
                    pct = min(99, 40 + int(state.global_step / max(total_steps, 1) * 55))
                    loss = logs.get("loss", 0)
                    _set_stage("train", pct)
                    step_msg = f"Step {state.global_step}/{total_steps}"
                    loss_msg = f"Loss: {loss:.3f}"
                    hint = ""
                    if loss > 2.0:
                        hint = " — still warming up"
                    elif loss > 1.0:
                        hint = " — model is learning"
                    elif loss > 0.5:
                        hint = " — good progress"
                    else:
                        hint = " — excellent convergence"
                    _log(f"{step_msg} ({pct-40}%) — {loss_msg}{hint}")

        from transformers import TrainerCallback
        class WMCallback(TrainerCallback):
            def __init__(self):
                self.cb = ProgressCallback()
            def on_log(self, args, state, control, **kwargs):
                self.cb.on_log(args, state, control, **kwargs)

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=ds,
            dataset_text_field=text_field,
            max_seq_length=max_seq,
            callbacks=[WMCallback()],
            args=TrainingArguments(
                output_dir=os.path.join(OUTPUT_DIR, output_name + "_checkpoints"),
                per_device_train_batch_size=batch_size,
                gradient_accumulation_steps=grad_accum,
                num_train_epochs=epochs,
                learning_rate=lr,
                fp16=True,
                logging_steps=1,
                save_strategy="no",
                warmup_steps=min(10, total_steps // 10),
                report_to="none",
            ),
        )

        _log("Training started — watch the loss go down!", "STEP")
        trainer.train()
        _log("Training complete!", "OK")
        _set_stage("train", 95)

        _log("Saving LoRA adapters...")
        model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)

        # Save Wickerman metadata
        meta = {
            "base_model": repo_id,
            "base_model_path": snap,
            "dataset": dataset,
            "mode": "sft",
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "config": config,
        }
        with open(os.path.join(output_path, "wm_meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

        _log(f"LoRA saved: {output_name}", "OK")
        _set_stage("train", 100)
        return True, output_path

    except Exception as e:
        import traceback
        _log(f"Training failed: {e}", "ERROR")
        _log(traceback.format_exc(), "ERROR")
        if "out of memory" in str(e).lower() or "cuda out" in str(e).lower():
            _log("GPU ran out of memory. Try: smaller batch size, shorter sequence length, or a smaller model.", "WARN")
        return False, str(e)

# ── Stage: Export to GGUF (Unsloth native) ───────────────────
# Unsloth handles merge + convert + quantize in one call via save_pretrained_gguf()
# No external binaries needed — this is the cleanest approach.

QUANT_MAP = {
    "Q4_K_M": "q4_k_m",
    "Q5_K_M": "q5_k_m",
    "Q8_0":   "q8_0",
    "f16":    "f16",
}

def run_export_gguf(lora_name, output_name, quant_type="Q5_K_M"):
    _set_stage("merge", 5)
    _log("Starting GGUF export...", "STEP")
    _log("Unsloth will merge, convert, and quantize in one step. No external tools needed.")

    lora_path = os.path.join(LORA_DIR, lora_name)
    if not os.path.isdir(lora_path):
        _log(f"LoRA not found: {lora_name}", "ERROR")
        return False, None

    meta_path = os.path.join(lora_path, "wm_meta.json")
    if not os.path.isfile(meta_path):
        _log("No metadata found for this LoRA.", "ERROR")
        return False, None

    with open(meta_path) as f:
        meta = json.load(f)

    repo_id = meta.get("base_model", "unknown")
    max_seq = meta.get("config", {}).get("max_seq_length", 2048)

    _log(f"Base model: {repo_id}")
    _log(f"LoRA: {lora_name}")
    _log(f"Quantization: {quant_type}")

    quant_descriptions = {
        "Q4_K_M": "Smallest file, slightly lower quality.",
        "Q5_K_M": "Best balance of size and quality. Recommended.",
        "Q8_0":   "Largest, highest quality.",
        "f16":    "Full precision, largest file.",
    }
    _log(quant_descriptions.get(quant_type, quant_type))

    try:
        from unsloth import FastLanguageModel
        _set_stage("merge", 15)
        _log("Loading model + LoRA adapters...")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=lora_path,
            max_seq_length=max_seq,
            load_in_4bit=True,
        )
        _set_stage("merge", 40)
        _log("Model loaded. Starting merge + GGUF conversion...", "OK")
        _log("This step uses significant RAM and takes several minutes.")

        safe_out = re.sub(r'[^a-zA-Z0-9_-]', '_', output_name)
        final_name = f"{safe_out}_{quant_type}.gguf"
        final_path = os.path.join(MODEL_DIR, final_name)
        temp_out = os.path.join(OUTPUT_DIR, safe_out)

        _set_stage("convert", 50)
        unsloth_quant = QUANT_MAP.get(quant_type, "q5_k_m")
        _log(f"Converting and quantizing to {quant_type}...")

        model.save_pretrained_gguf(temp_out, tokenizer, quantization_method=unsloth_quant)

        _set_stage("quantize", 80)

        # Find the output file — Unsloth names it differently
        gguf_files = glob.glob(os.path.join(temp_out + "*", "*.gguf")) +                      glob.glob(temp_out + "*.gguf") +                      glob.glob(os.path.join(OUTPUT_DIR, "*.gguf"))
        if not gguf_files:
            # Try broader search
            gguf_files = glob.glob(os.path.join(OUTPUT_DIR, "**", "*.gguf"), recursive=True)

        if not gguf_files:
            _log("GGUF file not found after export.", "ERROR")
            return False, None

        gguf_src = gguf_files[0]
        shutil.copy2(gguf_src, final_path)
        size_gb = round(os.path.getsize(final_path) / 1024**3, 1)

        # Cleanup temp
        try:
            if os.path.isdir(temp_out): shutil.rmtree(temp_out)
            if os.path.isfile(gguf_src) and gguf_src != final_path: os.remove(gguf_src)
        except: pass

        _set_stage("quantize", 100)
        _log(f"Export complete! {final_name} ({size_gb} GB)", "OK")
        _log("Your model is ready in the Model Router.", "OK")
        return True, final_path

    except Exception as e:
        import traceback
        _log(f"Export failed: {e}", "ERROR")
        _log(traceback.format_exc(), "ERROR")
        if "out of memory" in str(e).lower():
            _log("GPU ran out of memory during export. Try a smaller model or close other applications.", "WARN")
        return False, None

# Keep these as thin wrappers so the pipeline orchestrator still works
def run_merge(lora_name):
    return True, "skipped — using unified export", None

def run_convert(merged_path, output_name):
    return True, "skipped — using unified export", None

def run_quantize(f16_path, output_name, quant_type="Q5_K_M"):
    return True, f16_path

# ── Full Pipeline Orchestrator ───────────────────────────────
def run_pipeline(config):
    # Full pipeline: see config keys below
    with _lock:
        _job["status"] = "running"
        _job["log"] = []
        _job["progress"] = 0
        _job["result"] = None
        _job["current"] = config

    mode = config.get("mode", "full")
    output_name = config.get("output_name", f"custom_model_{int(time.time())}")
    quant_type = config.get("quant_type", "Q5_K_M")

    try:
        # ── Step 1: Download (if needed) ──
        if mode in ("full",) and config.get("download_first", False):
            _log("=" * 50)
            _log("STEP 1 OF 4: Downloading model", "STEP")
            _log("=" * 50)
            ok, result = run_download(config["repo_id"], config.get("hf_token"))
            if not ok:
                _log("Pipeline stopped: download failed.", "ERROR")
                _finish(None)
                return

        # ── Step 2: Train ──
        if mode in ("full", "train_only"):
            _log("=" * 50)
            step = "2" if mode == "full" else "1"
            _log(f"STEP {step} OF 4: Fine-tuning", "STEP")
            _log("=" * 50)
            ok, lora_path = run_sft_training(config)
            if not ok:
                _log("Pipeline stopped: training failed.", "ERROR")
                _finish(None)
                return
            lora_name = os.path.basename(lora_path)
            if mode == "train_only":
                _log("Training complete. LoRA saved — use Export tab to convert to GGUF.", "OK")
                _finish(lora_path)
                return

        elif mode == "export_only":
            lora_name = config.get("lora_name")
            if not lora_name:
                _log("No LoRA specified for export.", "ERROR")
                _finish(None)
                return

        # ── Step 3+4: Export to GGUF (merge + convert + quantize in one step) ──
        _log("=" * 50)
        _log("STEP 3 OF 3: Exporting to GGUF", "STEP")
        _log("=" * 50)
        ok, final_path = run_export_gguf(lora_name, output_name, quant_type)
        if not ok or not final_path:
            _log("Pipeline stopped: export failed.", "ERROR")
            _finish(None)
            return

        # ── Done ──
        _log("=" * 50)
        _log("ALL DONE!", "OK")
        _log(f"Your model is ready: {os.path.basename(final_path)}", "OK")
        _log("Go to the Model Router and load it as a new agent.", "OK")
        _log("=" * 50)

        # Clean up merged dir
        try:
            shutil.rmtree(merged_path, ignore_errors=True)
        except: pass

        _finish(final_path)

    except Exception as e:
        import traceback
        _log(f"Unexpected pipeline error: {e}", "ERROR")
        _log(traceback.format_exc(), "ERROR")
        _finish(None)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ── Status & Logs ──────────────────────────────────────────
@app.route("/api/status")
def api_status():
    with _lock:
        return jsonify({
            "status": _job["status"],
            "stage": _job["stage"],
            "progress": _job["progress"],
            "log_count": len(_job["log"]),
            "result": _job["result"],
        })

@app.route("/api/logs")
def api_logs():
    since = int(request.args.get("since", 0))
    with _lock:
        return jsonify({"logs": _job["log"][since:], "total": len(_job["log"])})

# ── Data Listings ──────────────────────────────────────────
@app.route("/api/hf_models")
def api_hf_models():
    return jsonify({"models": list_cached_hf_models()})

@app.route("/api/datasets")
def api_datasets():
    ds = []
    for f in os.listdir(DATASET_DIR):
        if f.endswith((".jsonl", ".csv", ".json", ".txt")):
            full = os.path.join(DATASET_DIR, f)
            size_mb = round(os.path.getsize(full) / 1024**2, 1)
            # Try to count examples
            count = 0
            try:
                if f.endswith((".jsonl", ".json")):
                    with open(full) as fh:
                        count = sum(1 for line in fh if line.strip())
                elif f.endswith(".csv"):
                    with open(full) as fh:
                        count = sum(1 for line in fh) - 1  # subtract header
                elif f.endswith(".txt"):
                    with open(full) as fh:
                        count = sum(1 for line in fh if line.strip())
            except: pass
            ds.append({"name": f, "size_mb": size_mb, "examples": count})
    return jsonify({"datasets": ds})

@app.route("/api/loras")
def api_loras():
    return jsonify({"loras": list_loras()})

@app.route("/api/gguf_models")
def api_gguf_models():
    return jsonify({"models": list_gguf_models()})

# ── Actions ────────────────────────────────────────────────
@app.route("/api/download", methods=["POST"])
def api_download():
    with _lock:
        if _job["status"] == "running":
            return jsonify({"error": "A job is already running"}), 409
    d = request.json or {}
    repo_id = d.get("repo_id", "").strip()
    if not repo_id:
        return jsonify({"error": "repo_id required"}), 400
    token = d.get("token", "").strip() or None
    threading.Thread(
        target=lambda: _run_download_only(repo_id, token),
        daemon=True
    ).start()
    return jsonify({"ok": True, "message": f"Downloading {repo_id}..."})

def _run_download_only(repo_id, token):
    with _lock:
        _job["status"] = "running"
        _job["log"] = []
        _job["progress"] = 0
        _job["result"] = None
        _job["current"] = {"mode": "download", "repo_id": repo_id}
    ok, result = run_download(repo_id, token)
    _finish(result if ok else None)

@app.route("/api/train", methods=["POST"])
def api_train():
    with _lock:
        if _job["status"] == "running":
            return jsonify({"error": "A job is already running"}), 409
    config = request.json or {}
    if not config.get("repo_id"):
        return jsonify({"error": "repo_id required"}), 400
    if not config.get("dataset"):
        return jsonify({"error": "dataset required"}), 400
    config.setdefault("mode", "train_only")
    threading.Thread(target=run_pipeline, args=(config,), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/export", methods=["POST"])
def api_export():
    with _lock:
        if _job["status"] == "running":
            return jsonify({"error": "A job is already running"}), 409
    d = request.json or {}
    if not d.get("lora_name"):
        return jsonify({"error": "lora_name required"}), 400
    config = {
        "mode": "export_only",
        "lora_name": d["lora_name"],
        "output_name": d.get("output_name") or d["lora_name"],
        "quant_type": d.get("quant_type", "Q5_K_M"),
    }
    threading.Thread(target=run_pipeline, args=(config,), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    with _lock:
        if _job["status"] == "running":
            _job["status"] = "idle"
            _job["stage"] = ""
            _job["log"].append("[--:--] Job cancelled by user.")
    return jsonify({"ok": True})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    with _lock:
        if _job["status"] != "running":
            _job.update({"status": "idle", "stage": "", "log": [], "progress": 0,
                         "current": None, "result": None})
    return jsonify({"ok": True})

# ── Stage 2 Stubs (DPO — coming soon) ─────────────────────
@app.route("/api/dpo/generate_pairs", methods=["POST"])
def api_dpo_generate():
    # Stage 2: AI-assisted DPO pair generation via Model Router.
    # Check if router is up
    try:
        r = requests.get(f"{LLAMA_API}/health", timeout=3)
        if r.status_code != 200:
            return jsonify({"error": "Model Router not ready. Please load a model first."}), 503
    except:
        return jsonify({"error": "Model Router unreachable. Make sure wm-llama is running."}), 503

    d = request.json or {}
    mode = d.get("mode", "correct")       # correct | shape | focus
    description = d.get("description", "").strip()
    topic = d.get("topic", "").strip()
    count = min(int(d.get("count", 10)), 20)

    if not description:
        return jsonify({"error": "description required"}), 400

    # Build the generation prompt based on mode
    mode_instructions = {
        "correct": (
            "You are generating DPO (Direct Preference Optimization) training pairs to correct "
            "factual errors or bias in a language model. Each pair has: a prompt the user might ask, "
            "a CHOSEN response (empirically correct, no hedging or false equivalence), and a "
            "REJECTED response (the incorrect/biased version the model currently gives). "
            "Focus on giving clean, direct factual answers in the chosen response. "
            "Never present religious or ideological claims as equivalent to empirical evidence."
        ),
        "shape": (
            "You are generating DPO training pairs to reshape a language model's communication style. "
            "Each pair has: a prompt, a CHOSEN response (the desired style/tone/behavior), "
            "and a REJECTED response (the current unwanted style). "
            "Be specific about what makes the chosen response better."
        ),
        "focus": (
            "You are generating DPO training pairs to restrict a language model to a specific domain. "
            "Each pair has: a prompt, a CHOSEN response (stays on topic, declines gracefully if out of scope), "
            "and a REJECTED response (wanders off topic or handles out-of-scope poorly)."
        ),
    }

    system_prompt = mode_instructions.get(mode, mode_instructions["correct"])
    user_prompt = (
        f"Generate exactly {count} DPO training pairs for the following correction:\n\n"
        f"DESCRIPTION: {description}\n"
        + (f"TOPIC AREA: {topic}\n" if topic else "") +
        f"\nReturn ONLY a valid JSON array with no explanation, no markdown, no backticks. "
        f"Each object must have exactly these keys: \"prompt\", \"chosen\", \"rejected\".\n"
        f"Example format:\n"
        f'[{{"prompt": "Question?", "chosen": "Good answer.", "rejected": "Bad answer."}}]'
    )

    try:
        resp = requests.post(
            f"{LLAMA_API}/v1/chat/completions",
            json={
                "model": "default",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 4096,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        reply = data["choices"][0]["message"]["content"].strip()

        # Strip markdown formatting if the LLM wrapped it in backticks
        if reply.startswith("```"):
            reply = reply.strip("`").removeprefix("json").strip()
        # Also strip any trailing explanation after the JSON array
        bracket_end = reply.rfind("]")
        if bracket_end != -1:
            reply = reply[:bracket_end + 1]

        pairs = json.loads(reply)

        # Validate structure
        valid = []
        for p in pairs:
            if isinstance(p, dict) and "prompt" in p and "chosen" in p and "rejected" in p:
                valid.append({
                    "prompt": str(p["prompt"]).strip(),
                    "chosen": str(p["chosen"]).strip(),
                    "rejected": str(p["rejected"]).strip(),
                })

        if not valid:
            return jsonify({"error": "Model returned no valid pairs. Try a more specific description."}), 500

        return jsonify({"pairs": valid, "count": len(valid)})

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Model returned malformed JSON: {e}. Try again or use a stronger model."}), 500
    except Exception as e:
        return jsonify({"error": f"Generation failed: {e}"}), 500

@app.route("/api/dpo/train", methods=["POST"])
def api_dpo_train():
    # Stage 2: DPO training.
    return jsonify({"error": "DPO training coming in Stage 2."}), 501

# ── Node API (Flow Editor integration) ───────────────────
@app.route("/node/schema")
def node_schema():
    return jsonify({
        "name": "trainer",
        "description": "Fine-tune and customize local language models",
        "inputs": [
            {"name": "repo_id", "type": "string", "required": True,
             "description": "HuggingFace model ID (e.g. Qwen/Qwen2.5-7B)"},
            {"name": "dataset", "type": "string", "required": True},
            {"name": "output_name", "type": "string", "required": False},
            {"name": "epochs", "type": "number", "default": 3},
            {"name": "quant_type", "type": "string", "default": "Q5_K_M"},
        ],
        "outputs": [
            {"name": "status", "type": "string"},
            {"name": "model_path", "type": "string"},
        ]
    })

@app.route("/node/execute", methods=["POST"])
def node_execute():
    d = request.json or {}
    if not d.get("repo_id") or not d.get("dataset"):
        return jsonify({"error": "repo_id and dataset required"}), 400
    d["mode"] = "full"
    d["download_first"] = True
    threading.Thread(target=run_pipeline, args=(d,), daemon=True).start()
    return jsonify({"status": "started",
                    "message": "Pipeline started. Poll /api/status for progress."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
""",

"data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Customization Studio</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;
  --text:#cdd6f4;--sub:#6c7086;--blue:#89b4fa;--green:#a6e3a1;
  --red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;--peach:#fab387;
  --mono:'Courier New',monospace;
}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column}

/* ── Top bar ── */
.topbar{padding:14px 24px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.topbar .logo{display:flex;align-items:center;gap:10px}
.topbar .logo .icon{width:30px;height:30px;background:var(--mauve);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px;color:var(--bg);font-weight:700}
.topbar .logo span{font-size:16px;font-weight:700;color:var(--mauve);letter-spacing:.5px}
.topbar .status-pill{font-size:12px;padding:4px 12px;border-radius:12px;font-weight:600}
.pill-idle{background:#313244;color:var(--sub)}
.pill-running{background:#2e2a1e;color:var(--yellow)}
.pill-complete{background:#1e3a2e;color:var(--green)}
.pill-error{background:#302030;color:var(--red)}

/* ── Tabs ── */
.tab-row{display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--surface);padding:0 24px}
.tab{padding:12px 20px;font-size:13px;cursor:pointer;color:var(--sub);border-bottom:2px solid transparent;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab.soon{color:#45475a;cursor:default}
.tab.soon:hover{color:#45475a}
.tab-content{display:none;padding:24px;max-width:960px;margin:0 auto;width:100%}
.tab-content.active{display:block}

/* ── Cards ── */
.card{background:var(--overlay);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:16px}
.card-title{font-size:15px;font-weight:600;color:var(--blue);margin-bottom:4px}
.card-sub{font-size:12px;color:var(--sub);margin-bottom:16px}

/* ── Form elements ── */
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--sub);margin-bottom:6px;font-weight:500}
.field input,.field select,.field textarea{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px 12px;border-radius:6px;font-size:13px;outline:none;font-family:inherit}
.field input:focus,.field select:focus,.field textarea:focus{border-color:var(--blue)}
.field .hint{font-size:11px;color:var(--sub);margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}

/* ── Buttons ── */
.btn{padding:10px 20px;border-radius:6px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn-primary{background:var(--blue);color:var(--bg)}
.btn-primary:hover{opacity:.9}
.btn-success{background:var(--green);color:var(--bg)}
.btn-success:hover{opacity:.9}
.btn-danger{background:transparent;border:1px solid rgba(243,139,168,.4);color:var(--red)}
.btn-danger:hover{background:rgba(243,139,168,.1)}
.btn-muted{background:var(--border);color:var(--text)}
.btn-muted:hover{background:#45475a}
.btn:disabled{opacity:.4;cursor:not-allowed}
.btn-row{display:flex;gap:10px;margin-top:16px;align-items:center}

/* ── Preset pills ── */
.preset-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.preset{padding:7px 14px;border-radius:20px;border:1px solid var(--border);font-size:12px;cursor:pointer;color:var(--sub);background:var(--bg)}
.preset:hover{border-color:var(--blue);color:var(--text)}
.preset.active{background:var(--blue);color:var(--bg);border-color:var(--blue);font-weight:600}
.adv-toggle{font-size:12px;color:var(--blue);cursor:pointer;margin-bottom:12px;display:inline-block}
.adv{display:none}
.adv.open{display:block}

/* ── Log console ── */
.console{background:#000;color:#a6e3a1;font-family:var(--mono);font-size:12px;padding:14px;border-radius:6px;height:280px;overflow-y:auto;white-space:pre-wrap;line-height:1.5}
.console .log-error{color:var(--red)}
.console .log-warn{color:var(--yellow)}
.console .log-ok{color:var(--green)}
.console .log-step{color:var(--blue);font-weight:bold}

/* ── Progress bar ── */
.progress-wrap{margin:12px 0}
.progress-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-fill{height:100%;background:var(--blue);border-radius:3px;transition:width .5s ease}
.progress-label{font-size:11px;color:var(--sub);margin-top:4px;display:flex;justify-content:space-between}

/* ── Model/LoRA cards ── */
.item-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.item-card{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px}
.item-card .iname{font-size:13px;font-weight:600;margin-bottom:4px;word-break:break-word}
.item-card .imeta{font-size:11px;color:var(--sub)}
.item-card .iactions{margin-top:10px;display:flex;gap:8px}

/* ── Stage indicator ── */
.stages{display:flex;gap:0;margin-bottom:20px}
.stage-step{flex:1;text-align:center;padding:8px 4px;font-size:11px;color:var(--sub);border-bottom:2px solid var(--border);position:relative}
.stage-step.done{color:var(--green);border-bottom-color:var(--green)}
.stage-step.active{color:var(--yellow);border-bottom-color:var(--yellow)}

/* ── Coming soon ── */
.coming-soon{text-align:center;padding:60px 40px;color:var(--sub)}
.coming-soon .cs-icon{font-size:48px;margin-bottom:16px}
.coming-soon h3{font-size:18px;color:var(--text);margin-bottom:8px}
.coming-soon p{font-size:13px;line-height:1.6}

/* ── Badges ── */
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.badge-sft{background:rgba(137,180,250,.15);color:var(--blue)}
.badge-dpo{background:rgba(203,166,247,.15);color:var(--mauve)}
.badge-gguf{background:rgba(166,227,161,.15);color:var(--green)}

/* ── Info box ── */
.info-box{background:rgba(137,180,250,.07);border:1px solid rgba(137,180,250,.2);border-radius:6px;padding:12px 14px;font-size:12px;color:var(--sub);margin-bottom:14px;line-height:1.6}
.warn-box{background:rgba(249,226,175,.07);border:1px solid rgba(249,226,175,.2);border-radius:6px;padding:12px 14px;font-size:12px;color:var(--yellow);margin-bottom:14px;line-height:1.6}
</style>
</head><body>

<div class="topbar">
  <div class="logo">
    <div class="icon">S</div>
    <span>MODEL CUSTOMIZATION STUDIO</span>
  </div>
  <span class="status-pill pill-idle" id="statusPill">Idle</span>
</div>

<div class="tab-row">
  <div class="tab active" onclick="showTab('get')">Get a Model</div>
  <div class="tab" onclick="showTab('train')">Train</div>
  <div class="tab" onclick="showTab('export')">Export</div>
  <div class="tab soon" title="Coming in Stage 2">Correct ✦</div>
  <div class="tab soon" title="Coming in Stage 2">Shape ✦</div>
  <div class="tab soon" title="Coming in Stage 2">Focus ✦</div>
  <div class="tab soon" title="Coming in Stage 3">Compare ✦</div>
</div>

<!-- ══ GET A MODEL TAB ══════════════════════════════════════ -->
<div class="tab-content active" id="tab-get">
  <div class="card">
    <div class="card-title">Download a model from HuggingFace</div>
    <div class="card-sub">Models are cached locally — you only wait once. Large models can take several minutes on first download.</div>

    <div class="info-box">
      💡 <strong>Recommended models for your GPU (24GB VRAM):</strong><br>
      <strong>Qwen/Qwen2.5-7B</strong> — Excellent all-rounder, great reasoning. Comfortable fit.<br>
      <strong>meta-llama/Llama-3.2-3B</strong> — Fast and lightweight, good for testing.<br>
      <strong>mistralai/Mistral-7B-v0.3</strong> — Strong instruction following.<br>
      <strong>Qwen/Qwen2.5-14B</strong> — More capable, uses most of your VRAM. Worth it for important models.
    </div>

    <div class="field">
      <label>HuggingFace Model ID</label>
      <input id="dl_repo" placeholder="e.g. Qwen/Qwen2.5-7B" oninput="clearDlStatus()">
      <div class="hint">Format: owner/model-name — copy from the HuggingFace model page URL</div>
    </div>
    <div class="field">
      <label>HuggingFace Token <span style="color:var(--sub)">(only needed for gated/private models)</span></label>
      <input id="dl_token" type="password" placeholder="hf_...">
      <div class="hint">Get your token at huggingface.co/settings/tokens — not required for most public models</div>
    </div>
    <div class="btn-row">
      <button class="btn btn-primary" id="dlBtn" onclick="startDownload()">Download Model</button>
      <span id="dlStatus" style="font-size:12px;color:var(--sub)"></span>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Downloaded models</div>
    <div class="card-sub">These are ready to train. Models stay here even after reinstalling Wickerman.</div>
    <div id="hfModelList"><div style="color:var(--sub);font-size:13px">Loading...</div></div>
  </div>
</div>

<!-- ══ TRAIN TAB ══════════════════════════════════════════ -->
<div class="tab-content" id="tab-train">
  <div class="card">
    <div class="card-title">Fine-tune a model on your dataset</div>
    <div class="card-sub">Teaching the model new knowledge or skills. Pick a preset — advanced settings are optional.</div>

    <div class="field">
      <label>Base model (must be downloaded first)</label>
      <select id="tr_model">
        <option value="">Loading models...</option>
      </select>
    </div>

    <div class="field">
      <label>Dataset</label>
      <select id="tr_dataset">
        <option value="">Loading datasets...</option>
      </select>
      <div class="hint">Datasets live in ~/WickermanSupport/datasets/ — add files there to see them here</div>
    </div>

    <div class="field">
      <label>Text field name</label>
      <input id="tr_textfield" value="text" placeholder="text">
      <div class="hint">The column in your dataset that contains the training text. Usually "text", "content", or "instruction".</div>
    </div>

    <div class="field">
      <label>Training preset</label>
      <div class="preset-row">
        <div class="preset" id="pr_quick" onclick="setPreset('quick')">
          ⚡ Quick test<br><span style="font-size:10px;color:var(--sub)">~5 min — just checking</span>
        </div>
        <div class="preset active" id="pr_light" onclick="setPreset('light')">
          🎯 Light touch<br><span style="font-size:10px;color:var(--sub)">~15 min — small adjustments</span>
        </div>
        <div class="preset" id="pr_solid" onclick="setPreset('solid')">
          💪 Solid training<br><span style="font-size:10px;color:var(--sub)">~30 min — real changes</span>
        </div>
        <div class="preset" id="pr_deep" onclick="setPreset('deep')">
          🔥 Deep rework<br><span style="font-size:10px;color:var(--sub)">~60 min+ — major shift</span>
        </div>
      </div>
    </div>

    <div class="field">
      <label>Output name</label>
      <input id="tr_outname" placeholder="e.g. science-expert (auto-generated if blank)">
    </div>

    <span class="adv-toggle" onclick="toggleAdv('tr')">⚙ Advanced settings ▾</span>
    <div class="adv" id="adv_tr">
      <div class="grid3">
        <div class="field"><label>Epochs</label><input id="tr_epochs" type="number" value="3" min="1" max="10"></div>
        <div class="field"><label>Learning rate</label><input id="tr_lr" type="number" value="0.0002" step="0.00001"></div>
        <div class="field"><label>LoRA rank</label><input id="tr_lora_r" type="number" value="16" min="4" max="128"></div>
      </div>
      <div class="grid3">
        <div class="field"><label>Batch size</label><input id="tr_batch" type="number" value="2" min="1" max="16"></div>
        <div class="field"><label>Gradient accum</label><input id="tr_grad" type="number" value="4" min="1"></div>
        <div class="field"><label>Max sequence length</label><input id="tr_seqlen" type="number" value="2048" step="256"></div>
      </div>
    </div>

    <div class="btn-row">
      <button class="btn btn-primary" id="trainBtn" onclick="startTraining()">Start Training</button>
      <button class="btn btn-danger" id="cancelBtn" onclick="cancelJob()" style="display:none">Cancel</button>
    </div>
  </div>

  <div id="trainProgress" style="display:none">
    <div class="card">
      <div class="card-title">Training progress</div>
      <div class="stages" id="stageBar">
        <div class="stage-step" id="st_train">Training</div>
        <div class="stage-step" id="st_merge">Merging</div>
        <div class="stage-step" id="st_convert">Converting</div>
        <div class="stage-step" id="st_quantize">Quantizing</div>
      </div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        <div class="progress-label"><span id="progressStage">Starting...</span><span id="progressPct">0%</span></div>
      </div>
      <div class="console" id="trainLog">Waiting to start...</div>
      <div class="btn-row" id="afterTrain" style="display:none">
        <button class="btn btn-success" onclick="showTab('export');loadExportData()">→ Go to Export</button>
        <button class="btn btn-muted" onclick="resetJob()">Train another</button>
      </div>
    </div>
  </div>
</div>

<!-- ══ EXPORT TAB ══════════════════════════════════════════ -->
<div class="tab-content" id="tab-export">
  <div class="card">
    <div class="card-title">Export a trained LoRA to GGUF</div>
    <div class="card-sub">This merges your training into the model, converts it to GGUF format, and sends it to the Model Router. Your customized model will appear as a new agent.</div>

    <div class="field">
      <label>Select LoRA to export</label>
      <select id="ex_lora">
        <option value="">Loading...</option>
      </select>
    </div>

    <div class="field">
      <label>Output name for your model</label>
      <input id="ex_outname" placeholder="e.g. science-7b">
      <div class="hint">This is the name that will appear in the Model Router</div>
    </div>

    <div class="field">
      <label>Quantization</label>
      <select id="ex_quant">
        <option value="Q5_K_M" selected>Q5_K_M — Recommended (best balance of size and quality)</option>
        <option value="Q4_K_M">Q4_K_M — Smaller file, slightly lower quality</option>
        <option value="Q8_0">Q8_0 — Largest, highest quality (Advanced)</option>
      </select>
      <div class="hint">Q5_K_M is the sweet spot — roughly half the original size with barely noticeable quality loss</div>
    </div>

    <div class="btn-row">
      <button class="btn btn-success" id="exportBtn" onclick="startExport()">Export to Model Router</button>
    </div>
  </div>

  <div id="exportProgress" style="display:none">
    <div class="card">
      <div class="card-title">Export progress</div>
      <div class="stages" id="expStageBar">
        <div class="stage-step" id="es_merge">Merging</div>
        <div class="stage-step" id="es_convert">Converting</div>
        <div class="stage-step" id="es_quantize">Quantizing</div>
      </div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" id="expProgressFill" style="width:0%"></div></div>
        <div class="progress-label"><span id="expStage">Starting...</span><span id="expPct">0%</span></div>
      </div>
      <div class="console" id="exportLog">Waiting to start...</div>
      <div id="exportDone" style="display:none;margin-top:16px">
        <div class="info-box" style="color:var(--green)">
          ✓ Your model is ready! Go to the <strong>Model Router</strong> and click on your model to load it as an agent.
        </div>
        <button class="btn btn-muted" onclick="resetJob();loadExportData()">Export another</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Available LoRAs</div>
    <div id="loraList"><div style="color:var(--sub);font-size:13px">Loading...</div></div>
  </div>

  <div class="card">
    <div class="card-title">Models in the Router</div>
    <div class="card-sub">These are your exported GGUF models, available in the Model Router.</div>
    <div id="ggufList"><div style="color:var(--sub);font-size:13px">Loading...</div></div>
  </div>
</div>

<!-- ══ COMING SOON TABS ════════════════════════════════════ -->
<div class="tab-content" id="tab-correct">
  <div class="coming-soon">
    <div class="cs-icon">🎯</div>
    <h3>Correct — Fix wrong facts or bias</h3>
    <p>Describe a problem in plain English. The AI will generate correction pairs, you review and approve them, then we train the model to move away from bad responses.<br><br>
    <strong>Coming in Stage 2.</strong><br>
    This is the highest priority upcoming feature — designed specifically for removing religious/philosophical conflation from science and history models.</p>
  </div>
</div>

<div class="tab-content" id="tab-shape">
  <div class="coming-soon">
    <div class="cs-icon">✏️</div>
    <h3>Shape — Change personality or behavior</h3>
    <p>Change how the model communicates — tone, verbosity, how it handles uncertainty, what it refuses. Uses AI-assisted DPO training with a review step so you see exactly what's being taught.<br><br>
    <strong>Coming in Stage 2.</strong></p>
  </div>
</div>

<div class="tab-content" id="tab-focus">
  <div class="coming-soon">
    <div class="cs-icon">🔭</div>
    <h3>Focus — Make a domain specialist</h3>
    <p>Define what the model should and shouldn't discuss. Create a medical assistant that stays on medicine, a legal assistant that stays on law, a children's tutor that stays age-appropriate.<br><br>
    <strong>Coming in Stage 2.</strong></p>
  </div>
</div>

<div class="tab-content" id="tab-compare">
  <div class="coming-soon">
    <div class="cs-icon">⚖️</div>
    <h3>Compare — Before and after</h3>
    <p>Run the same questions against your original model and your customized version side by side. Confirm the changes worked before you ship.<br><br>
    <strong>Coming in Stage 3.</strong></p>
  </div>
</div>

<script>
const API = window.location.origin;
let logOffset = 0;
let pollTimer = null;
let currentPreset = 'light';

const PRESETS = {
  quick: {epochs:1, lora_r:8,  batch_size:2, grad_accum:2, learning_rate:0.0002, max_seq_length:1024},
  light: {epochs:2, lora_r:16, batch_size:2, grad_accum:4, learning_rate:0.0002, max_seq_length:2048},
  solid: {epochs:3, lora_r:32, batch_size:2, grad_accum:4, learning_rate:0.0001, max_seq_length:2048},
  deep:  {epochs:5, lora_r:64, batch_size:1, grad_accum:8, learning_rate:0.00005, max_seq_length:2048},
};

// ── Tab switching ──
const TAB_IDS = {
  'get':'tab-get','train':'tab-train','export':'tab-export',
  'correct':'tab-correct','shape':'tab-shape','focus':'tab-focus','compare':'tab-compare'
};
function showTab(t) {
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
  const tc = document.getElementById(TAB_IDS[t] || 'tab-'+t);
  if(tc) tc.classList.add('active');
  const tabs = document.querySelectorAll('.tab');
  const names = ['get','train','export','correct','shape','focus','compare'];
  const i = names.indexOf(t);
  if(i >= 0 && !tabs[i].classList.contains('soon')) tabs[i].classList.add('active');
  if(t === 'train') loadTrainData();
  if(t === 'export') loadExportData();
  if(t === 'get') loadHFModels();
}

function toggleAdv(prefix) {
  const el = document.getElementById('adv_'+prefix);
  if(el) el.classList.toggle('open');
}

// ── Presets ──
function setPreset(name) {
  currentPreset = name;
  document.querySelectorAll('.preset').forEach(p=>p.classList.remove('active'));
  document.getElementById('pr_'+name)?.classList.add('active');
  const p = PRESETS[name];
  if(!p) return;
  document.getElementById('tr_epochs').value = p.epochs;
  document.getElementById('tr_lora_r').value = p.lora_r;
  document.getElementById('tr_batch').value = p.batch_size;
  document.getElementById('tr_grad').value = p.grad_accum;
  document.getElementById('tr_lr').value = p.learning_rate;
  document.getElementById('tr_seqlen').value = p.max_seq_length;
}

// ── Data loading ──
async function loadHFModels() {
  try {
    const r = await(await fetch(API+'/api/hf_models')).json();
    const el = document.getElementById('hfModelList');
    if(!r.models?.length) {
      el.innerHTML = '<div style="color:var(--sub);font-size:13px">No models downloaded yet. Use the form above to download one.</div>';
      return;
    }
    el.innerHTML = '<div class="item-grid">'+r.models.map(m=>
      `<div class="item-card">
        <div class="iname">${m.repo_id}</div>
        <div class="imeta">${m.cached ? '✓ Ready' : 'Incomplete'} &mdash; ${m.size_gb} GB</div>
      </div>`
    ).join('')+'</div>';
  } catch(e) {}
}

async function loadTrainData() {
  try {
    const [mr, dr] = await Promise.all([
      fetch(API+'/api/hf_models').then(r=>r.json()),
      fetch(API+'/api/datasets').then(r=>r.json()),
    ]);
    const ms = document.getElementById('tr_model');
    const models = mr.models?.filter(m=>m.cached) || [];
    ms.innerHTML = models.length
      ? models.map(m=>`<option value="${m.repo_id}">${m.repo_id} (${m.size_gb} GB)</option>`).join('')
      : '<option value="">No models downloaded — go to Get a Model tab first</option>';
    const ds = document.getElementById('tr_dataset');
    const datasets = dr.datasets || [];
    ds.innerHTML = datasets.length
      ? datasets.map(d=>`<option value="${d.name}">${d.name} (${d.examples} examples, ${d.size_mb} MB)</option>`).join('')
      : '<option value="">No datasets found — add .jsonl or .csv files to ~/WickermanSupport/datasets/</option>';
  } catch(e) {}
}

async function loadExportData() {
  try {
    const [lr, gr] = await Promise.all([
      fetch(API+'/api/loras').then(r=>r.json()),
      fetch(API+'/api/gguf_models').then(r=>r.json()),
    ]);
    const ls = document.getElementById('ex_lora');
    const loras = lr.loras || [];
    ls.innerHTML = loras.length
      ? loras.map(l=>`<option value="${l.name}">${l.name} (${l.base_model})</option>`).join('')
      : '<option value="">No LoRAs yet — train a model first</option>';
    // Update LoRA list display
    const loraEl = document.getElementById('loraList');
    loraEl.innerHTML = loras.length
      ? '<div class="item-grid">'+loras.map(l=>
          `<div class="item-card">
            <div class="iname">${l.name}</div>
            <div class="imeta">${l.base_model} &mdash; <span class="badge badge-sft">${l.mode.toUpperCase()}</span></div>
            <div class="imeta">${l.trained_at}</div>
            <div class="iactions">
              <button class="btn btn-muted" style="font-size:11px;padding:5px 10px" onclick="document.getElementById('ex_lora').value='${l.name}';showTab('export')">Select for export</button>
            </div>
          </div>`
        ).join('')+'</div>'
      : '<div style="color:var(--sub);font-size:13px">No LoRAs yet. Train a model first.</div>';
    // GGUF models
    const ggufEl = document.getElementById('ggufList');
    const ggufs = gr.models || [];
    ggufEl.innerHTML = ggufs.length
      ? '<div class="item-grid">'+ggufs.map(g=>
          `<div class="item-card">
            <div class="iname">${g.name}</div>
            <div class="imeta"><span class="badge badge-gguf">GGUF</span> ${g.size_gb} GB</div>
          </div>`
        ).join('')+'</div>'
      : '<div style="color:var(--sub);font-size:13px">No exported models yet.</div>';
  } catch(e) {}
}

// ── Download ──
function clearDlStatus() { document.getElementById('dlStatus').textContent = ''; }
async function startDownload() {
  const repo = document.getElementById('dl_repo').value.trim();
  const token = document.getElementById('dl_token').value.trim();
  if(!repo) { document.getElementById('dlStatus').textContent = 'Enter a model ID first.'; return; }
  const btn = document.getElementById('dlBtn');
  btn.disabled = true; btn.textContent = 'Downloading...';
  document.getElementById('dlStatus').textContent = 'Starting download...';
  try {
    const r = await fetch(API+'/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo_id:repo,token:token||undefined})});
    const d = await r.json();
    if(d.error) { document.getElementById('dlStatus').textContent = 'Error: '+d.error; btn.disabled=false; btn.textContent='Download Model'; return; }
    document.getElementById('dlStatus').textContent = 'Downloading — see progress in Train tab log...';
    showTab('train');
    startLogPolling('trainLog', ()=>{ btn.disabled=false; btn.textContent='Download Model'; loadHFModels(); loadTrainData(); });
  } catch(e) {
    document.getElementById('dlStatus').textContent = 'Error: '+e;
    btn.disabled=false; btn.textContent='Download Model';
  }
}

// ── Training ──
async function startTraining() {
  const repo = document.getElementById('tr_model').value;
  const dataset = document.getElementById('tr_dataset').value;
  if(!repo || repo.includes('No models')) { alert('Please download a model first in the Get a Model tab.'); return; }
  if(!dataset || dataset.includes('No datasets')) { alert('No datasets found. Add a .jsonl or .csv file to ~/WickermanSupport/datasets/'); return; }
  const config = {
    mode: 'train_only',
    repo_id: repo,
    dataset: dataset,
    text_field: document.getElementById('tr_textfield').value || 'text',
    output_name: document.getElementById('tr_outname').value.trim() || undefined,
    epochs: parseInt(document.getElementById('tr_epochs').value),
    learning_rate: parseFloat(document.getElementById('tr_lr').value),
    lora_r: parseInt(document.getElementById('tr_lora_r').value),
    batch_size: parseInt(document.getElementById('tr_batch').value),
    grad_accum: parseInt(document.getElementById('tr_grad').value),
    max_seq_length: parseInt(document.getElementById('tr_seqlen').value),
    quant_type: 'Q5_K_M',
  };
  document.getElementById('trainBtn').disabled = true;
  document.getElementById('cancelBtn').style.display = 'inline-block';
  document.getElementById('trainProgress').style.display = 'block';
  document.getElementById('afterTrain').style.display = 'none';
  logOffset = 0;
  document.getElementById('trainLog').textContent = '';
  try {
    const r = await fetch(API+'/api/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
    const d = await r.json();
    if(d.error) { alert('Error: '+d.error); resetUI(); return; }
    startLogPolling('trainLog', ()=>{
      document.getElementById('trainBtn').disabled = false;
      document.getElementById('cancelBtn').style.display = 'none';
      document.getElementById('afterTrain').style.display = 'flex';
    });
  } catch(e) { alert('Error: '+e); resetUI(); }
}

// ── Export ──
async function startExport() {
  const lora = document.getElementById('ex_lora').value;
  if(!lora) { alert('Select a LoRA to export.'); return; }
  const outname = document.getElementById('ex_outname').value.trim() || lora;
  const quant = document.getElementById('ex_quant').value;
  document.getElementById('exportBtn').disabled = true;
  document.getElementById('exportProgress').style.display = 'block';
  document.getElementById('exportDone').style.display = 'none';
  logOffset = 0;
  document.getElementById('exportLog').textContent = '';
  try {
    const r = await fetch(API+'/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lora_name:lora,output_name:outname,quant_type:quant})});
    const d = await r.json();
    if(d.error) { alert('Error: '+d.error); document.getElementById('exportBtn').disabled=false; return; }
    startLogPolling('exportLog', ()=>{
      document.getElementById('exportBtn').disabled = false;
      document.getElementById('exportDone').style.display = 'block';
      loadExportData();
    });
  } catch(e) { alert('Error: '+e); document.getElementById('exportBtn').disabled=false; }
}

// ── Job control ──
async function cancelJob() {
  await fetch(API+'/api/cancel',{method:'POST'});
}
async function resetJob() {
  await fetch(API+'/api/reset',{method:'POST'});
  resetUI();
}
function resetUI() {
  document.getElementById('trainBtn').disabled = false;
  document.getElementById('cancelBtn').style.display = 'none';
  document.getElementById('trainProgress').style.display = 'none';
  document.getElementById('exportProgress').style.display = 'none';
  if(pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ── Log polling ──
function colorLine(line) {
  if(line.includes('✗') || line.includes('ERROR')) return `<span class="log-error">${esc(line)}</span>`;
  if(line.includes('⚠') || line.includes('WARN')) return `<span class="log-warn">${esc(line)}</span>`;
  if(line.includes('✓') || line.includes('OK')) return `<span class="log-ok">${esc(line)}</span>`;
  if(line.includes('▶') || line.includes('STEP') || line.includes('===')) return `<span class="log-step">${esc(line)}</span>`;
  return esc(line);
}
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function startLogPolling(logId, onDone) {
  if(pollTimer) clearInterval(pollTimer);
  logOffset = 0;
  pollTimer = setInterval(async ()=>{
    try {
      const [sr, lr] = await Promise.all([
        fetch(API+'/api/status').then(r=>r.json()),
        fetch(API+'/api/logs?since='+logOffset).then(r=>r.json()),
      ]);
      // Update status pill
      const pill = document.getElementById('statusPill');
      pill.textContent = sr.status.charAt(0).toUpperCase()+sr.status.slice(1)+(sr.stage?' — '+sr.stage:'');
      pill.className = 'status-pill pill-'+sr.status;
      // Update progress
      const fill = document.getElementById('progressFill') || document.getElementById('expProgressFill');
      const stageLbl = document.getElementById('progressStage') || document.getElementById('expStage');
      const pctLbl = document.getElementById('progressPct') || document.getElementById('expPct');
      if(fill) fill.style.width = sr.progress+'%';
      if(stageLbl) stageLbl.textContent = sr.stage || 'Running...';
      if(pctLbl) pctLbl.textContent = sr.progress+'%';
      // Update stage indicators
      updateStages(sr.stage);
      // Append new log lines
      if(lr.logs?.length) {
        const logEl = document.getElementById(logId);
        const html = lr.logs.map(colorLine).join('\n');
        logEl.innerHTML += (logEl.innerHTML?'\n':'')+html;
        logEl.scrollTop = 99999;
        logOffset = lr.total;
      }
      // Check done
      if(sr.status === 'complete' || sr.status === 'error') {
        clearInterval(pollTimer);
        pollTimer = null;
        pill.textContent = sr.status === 'complete' ? 'Done ✓' : 'Error ✗';
        if(onDone) onDone();
      }
    } catch(e) {}
  }, 1500);
}

function updateStages(stage) {
  const stageOrder = ['download','train','merge','convert','quantize','done'];
  const idx = stageOrder.indexOf(stage);
  ['train','merge','convert','quantize'].forEach((s,i) => {
    const el = document.getElementById('st_'+s) || document.getElementById('es_'+s);
    if(!el) return;
    const stageIdx = stageOrder.indexOf(s);
    if(idx > stageIdx) el.className = 'stage-step done';
    else if(idx === stageIdx) el.className = 'stage-step active';
    else el.className = 'stage-step';
  });
}

// ── Status polling (global, always on) ──
async function pollStatus() {
  try {
    const r = await fetch(API+'/api/status').then(r=>r.json());
    const pill = document.getElementById('statusPill');
    if(r.status === 'idle') {
      pill.textContent = 'Idle';
      pill.className = 'status-pill pill-idle';
    } else if(r.status === 'running') {
      pill.textContent = 'Running — '+r.stage;
      pill.className = 'status-pill pill-running';
    } else if(r.status === 'complete') {
      pill.textContent = 'Done ✓';
      pill.className = 'status-pill pill-complete';
    } else if(r.status === 'error') {
      pill.textContent = 'Error';
      pill.className = 'status-pill pill-error';
    }
  } catch(e) {}
}

// ── Init ──
setPreset('light');
loadHFModels();
setInterval(pollStatus, 5000);
</script>
</body></html>
""",

}  # end WM_TRAINER_FILES

WM_TRAINER["files"] = WM_TRAINER_FILES

PLUGIN_HOST = ("127.0.0.1", "trainer.wickerman.local")
