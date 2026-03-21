"""
Wickerman OS v5.1.0 — Model Trainer plugin manifest.
"""

WM_TRAINER = {
    "name": "Model Trainer",
    "description": "LoRA fine-tuning with Unsloth — fast local training on your GPU",
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
        "OUTPUT_DIR=/data/outputs"
    ],
    "volumes": [
        "{models}:/models",
        "{datasets}:/datasets",
        "{loras}:/loras",
        "{self}/data:/data"
    ],
    "nginx_host": "trainer.wickerman.local",
    "help": "## Model Trainer (Unsloth)\nFast LoRA fine-tuning on your local GPU.\n\n**Workflow:** Select a base model from /models, point to a dataset, configure training params, hit Train.\n\n**Output:** LoRA adapters saved to ~/WickermanSupport/loras/\n\n**Node API:** POST `/node/execute` with base_model, dataset, epochs, learning_rate to trigger training from wm-flow.\n\n**Formats:** Supports JSONL, CSV, and Alpaca-format datasets."
}

WM_TRAINER_FILES = {
    "data/Dockerfile": r"""FROM nvidia/cuda:12.2.0-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev git curl build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.* gunicorn==22.* requests==2.32.*
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu121
RUN pip install --no-cache-dir "unsloth[cu121-torch240] @ git+https://github.com/unslothai/unsloth.git"
RUN pip install --no-cache-dir datasets transformers trl peft accelerate bitsandbytes
WORKDIR /app
ARG CACHEBUST=1
COPY . .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "0", "app:app"]
""",

    "data/app.py": r"""
from flask import Flask, render_template, request, jsonify
import os, json, threading, time, glob

app = Flask(__name__)
MODEL_DIR = os.environ.get("MODEL_DIR", "/models")
DATASET_DIR = os.environ.get("DATASET_DIR", "/datasets")
LORA_DIR = os.environ.get("LORA_DIR", "/loras")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/data/outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LORA_DIR, exist_ok=True)

def safe_path(base, user_input):
    joined = os.path.abspath(os.path.join(base, user_input))
    if not joined.startswith(os.path.abspath(base)):
        raise ValueError(f"Path traversal blocked: {user_input}")
    return joined

training_state = {"status": "idle", "progress": 0, "log": [], "current_job": None}
_lock = threading.Lock()

def log_training(msg):
    with _lock:
        training_state["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(training_state["log"]) > 500:
            training_state["log"] = training_state["log"][-500:]

def run_training(config):
    try:
        with _lock:
            training_state["status"] = "training"
            training_state["progress"] = 0
            training_state["log"] = []
            training_state["current_job"] = config
        
        log_training(f"Starting training: {config.get('base_model', 'unknown')}")
        log_training(f"Dataset: {config.get('dataset', 'unknown')}")
        
        from unsloth import FastLanguageModel
        from datasets import load_dataset
        from trl import SFTTrainer
        from transformers import TrainingArguments
        
        model_path = safe_path(MODEL_DIR, config["base_model"])
        log_training(f"Loading model: {model_path}")
        
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_path,
            max_seq_length=config.get("max_seq_length", 2048),
            load_in_4bit=config.get("load_in_4bit", True),
        )
        log_training("Model loaded")
        
        model = FastLanguageModel.get_peft_model(
            model, r=config.get("lora_r", 16),
            target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
            lora_alpha=config.get("lora_alpha", 16),
            lora_dropout=0, bias="none", use_gradient_checkpointing="unsloth",
        )
        log_training(f"LoRA applied: r={config.get('lora_r',16)}")
        
        dataset_path = safe_path(DATASET_DIR, config["dataset"])
        ext = dataset_path.rsplit(".",1)[-1].lower()
        if ext == "jsonl": ds = load_dataset("json", data_files=dataset_path, split="train")
        elif ext == "csv": ds = load_dataset("csv", data_files=dataset_path, split="train")
        else: ds = load_dataset("json", data_files=dataset_path, split="train")
        log_training(f"Dataset loaded: {len(ds)} examples")
        
        output_name = config.get("output_name", f"lora_{int(time.time())}")
        output_path = os.path.join(LORA_DIR, output_name)
        
        trainer = SFTTrainer(
            model=model, tokenizer=tokenizer, train_dataset=ds,
            dataset_text_field=config.get("text_field", "text"),
            max_seq_length=config.get("max_seq_length", 2048),
            args=TrainingArguments(
                output_dir=os.path.join(OUTPUT_DIR, output_name),
                per_device_train_batch_size=config.get("batch_size", 2),
                gradient_accumulation_steps=config.get("grad_accum", 4),
                num_train_epochs=config.get("epochs", 3),
                learning_rate=config.get("learning_rate", 2e-4),
                fp16=True, logging_steps=1, save_strategy="epoch",
                warmup_steps=config.get("warmup_steps", 5),
            ),
        )
        
        log_training("Training started...")
        trainer.train()
        log_training("Training complete! Saving LoRA...")
        
        model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)
        log_training(f"LoRA saved to {output_path}")
        
        with _lock:
            training_state["status"] = "complete"
            training_state["progress"] = 100
    except Exception as e:
        log_training(f"ERROR: {e}")
        with _lock:
            training_state["status"] = "error"

@app.route("/")
def index(): return render_template("index.html")

@app.route("/health")
def health(): return jsonify({"status": "ok"})

@app.route("/api/status")
def status():
    with _lock: return jsonify(dict(training_state))

@app.route("/api/models")
def list_models():
    models = []
    for f in os.listdir(MODEL_DIR):
        full = os.path.join(MODEL_DIR, f)
        if os.path.isfile(full):
            models.append({"name": f, "size_mb": round(os.path.getsize(full) / 1024 / 1024, 1)})
    return jsonify(models)

@app.route("/api/datasets")
def list_datasets():
    ds = []
    for f in os.listdir(DATASET_DIR):
        if f.endswith((".jsonl",".csv",".json")):
            full = os.path.join(DATASET_DIR, f)
            ds.append({"name": f, "size_mb": round(os.path.getsize(full) / 1024 / 1024, 1)})
    return jsonify(ds)

@app.route("/api/loras")
def list_loras():
    loras = []
    for f in os.listdir(LORA_DIR):
        full = os.path.join(LORA_DIR, f)
        if os.path.isdir(full):
            loras.append({"name": f})
    return jsonify(loras)

@app.route("/api/train", methods=["POST"])
def start_training():
    with _lock:
        if training_state["status"] == "training":
            return jsonify({"error": "Training already in progress"}), 409
    config = request.json or {}
    if not config.get("base_model"): return jsonify({"error": "base_model required"}), 400
    if not config.get("dataset"): return jsonify({"error": "dataset required"}), 400
    threading.Thread(target=run_training, args=(config,), daemon=True).start()
    return jsonify({"status": "started"})

# ── Node API ─────────────────────────────────────────────────
@app.route("/node/schema")
def node_schema():
    return jsonify({
        "name": "trainer",
        "description": "Fine-tune a model with LoRA using Unsloth",
        "inputs": [
            {"name": "base_model", "type": "string", "required": True},
            {"name": "dataset", "type": "string", "required": True},
            {"name": "epochs", "type": "number", "default": 3},
            {"name": "learning_rate", "type": "number", "default": 2e-4},
            {"name": "lora_r", "type": "number", "default": 16},
            {"name": "output_name", "type": "string", "required": False},
        ],
        "outputs": [
            {"name": "status", "type": "string"},
            {"name": "lora_path", "type": "string"},
        ]
    })

@app.route("/node/execute", methods=["POST"])
def node_execute():
    config = request.json or {}
    if not config.get("base_model") or not config.get("dataset"):
        return jsonify({"error": "base_model and dataset required"}), 400
    threading.Thread(target=run_training, args=(config,), daemon=True).start()
    return jsonify({"status": "started", "message": "Training kicked off. Poll /api/status for progress."})

if __name__ == "__main__": app.run(host="0.0.0.0", port=5000)
""",

    "data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wickerman Trainer</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{background:#11111b;color:#cdd6f4;font-family:system-ui,sans-serif;padding:24px}
.c{max-width:800px;margin:0 auto}h1{text-align:center;color:#89b4fa;margin-bottom:4px}.sub{text-align:center;color:#6c7086;margin-bottom:24px;font-size:14px}
.card{background:#1e1e2e;border-radius:10px;padding:20px;margin-bottom:16px}
.card h2{font-size:16px;color:#89b4fa;margin-bottom:12px}
label{display:block;color:#6c7086;font-size:13px;margin:8px 0 4px}
select,input{width:100%;background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:10px;border-radius:6px;font-size:14px;outline:none}
select:focus,input:focus{border-color:#89b4fa}
.row{display:flex;gap:12px}.row>*{flex:1}
.btn{display:block;width:100%;padding:14px;border:none;border-radius:8px;font-size:16px;font-weight:700;cursor:pointer;margin-top:16px}
.btn.primary{background:#89b4fa;color:#1e1e2e}.btn.primary:hover{background:#74c7ec}
.btn:disabled{opacity:.5;cursor:not-allowed}
.log{background:#000;color:#a6e3a1;font-family:monospace;font-size:12px;padding:12px;border-radius:6px;height:200px;overflow-y:auto;white-space:pre-wrap;margin-top:12px}
.status{display:inline-block;padding:4px 12px;border-radius:12px;font-size:13px;font-weight:600}
.status.idle{background:#313244;color:#6c7086}.status.training{background:#2e2a1e;color:#f9e2af}.status.complete{background:#1e3a2e;color:#a6e3a1}.status.error{background:#302030;color:#f38ba8}
</style></head><body>
<div class="c">
<h1>Model Trainer</h1><p class="sub">LoRA fine-tuning with Unsloth</p>
<div class="card">
<h2>Configuration</h2>
<label>Base Model</label><select id="model"><option value="">Loading models...</option></select>
<label>Dataset</label><select id="dataset"><option value="">Loading datasets...</option></select>
<div class="row"><div><label>Epochs</label><input id="epochs" type="number" value="3" min="1"></div>
<div><label>Learning Rate</label><input id="lr" type="number" value="0.0002" step="0.0001"></div>
<div><label>LoRA Rank</label><input id="rank" type="number" value="16" min="4" max="128"></div></div>
<div class="row"><div><label>Batch Size</label><input id="batch" type="number" value="2" min="1"></div>
<div><label>Max Seq Length</label><input id="seqlen" type="number" value="2048" step="256"></div>
<div><label>Output Name</label><input id="outname" placeholder="auto"></div></div>
<button class="btn primary" id="trainBtn" onclick="startTrain()">Start Training</button>
</div>
<div class="card">
<h2>Status <span class="status idle" id="st">idle</span></h2>
<div class="log" id="log">Ready.</div>
</div>
<div class="card"><h2>Saved LoRAs</h2><div id="loras">Loading...</div></div>
</div>
<script>
async function loadModels(){try{const r=await(await fetch('/api/models')).json();const s=document.getElementById('model');s.innerHTML=r.length?r.map(m=>`<option value="${m.name}">${m.name} (${m.size_mb}MB)</option>`).join(''):'<option>No models found</option>'}catch(e){}}
async function loadDatasets(){try{const r=await(await fetch('/api/datasets')).json();const s=document.getElementById('dataset');s.innerHTML=r.length?r.map(d=>`<option value="${d.name}">${d.name} (${d.size_mb}MB)</option>`).join(''):'<option>No datasets found</option>'}catch(e){}}
async function loadLoras(){try{const r=await(await fetch('/api/loras')).json();document.getElementById('loras').innerHTML=r.length?r.map(l=>`<div style="padding:8px;background:#11111b;border-radius:6px;margin:4px 0">${l.name}</div>`).join(''):'<div style="color:#6c7086">None yet</div>'}catch(e){}}
async function startTrain(){const cfg={base_model:document.getElementById('model').value,dataset:document.getElementById('dataset').value,
epochs:parseInt(document.getElementById('epochs').value),learning_rate:parseFloat(document.getElementById('lr').value),
lora_r:parseInt(document.getElementById('rank').value),batch_size:parseInt(document.getElementById('batch').value),
max_seq_length:parseInt(document.getElementById('seqlen').value),output_name:document.getElementById('outname').value||undefined};
if(!cfg.base_model||!cfg.dataset){alert('Select a model and dataset');return}
document.getElementById('trainBtn').disabled=true;
try{const r=await fetch('/api/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
const d=await r.json();if(d.error)alert(d.error)}catch(e){alert('Error: '+e)}}
async function poll(){try{const r=await(await fetch('/api/status')).json();const st=document.getElementById('st');
st.textContent=r.status;st.className='status '+r.status;
const lg=document.getElementById('log');const newLines=r.log.slice(window._lastLogLen||0);if(newLines.length){lg.textContent+=newLines.join('\n')+'\n';lg.scrollTop=99999}window._lastLogLen=r.log.length;if(r.status==='idle'&&!r.log.length){lg.textContent='Ready.';window._lastLogLen=0}
document.getElementById('log').scrollTop=99999;
document.getElementById('trainBtn').disabled=r.status==='training';
if(r.status==='complete'||r.status==='error')loadLoras()}catch(e){}}
loadModels();loadDatasets();loadLoras();setInterval(poll,2000);
</script></body></html>
"""
}

WM_TRAINER["files"] = WM_TRAINER_FILES

PLUGIN_HOST = ("127.0.0.1", "trainer.wickerman.local")
