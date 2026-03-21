"""
Wickerman OS v5.1.0 — Code Forge plugin manifest.
"""

WM_FORGE = {
    "name": "Code Forge",
    "description": "AI-assisted code sandbox — write, run, and export apps",
    "icon": "construction",
    "build": True,
    "build_context": "data",
    "container_name": "wm-forge",
    "url": "http://forge.wickerman.local",
    "ports": [5000],
    "gpu": False,
    "network": "wm-forge-net",
    "env": [
        "LLAMA_API=http://wm-llama:8080",
        "WORKSPACE=/workspace"
    ],
    "volumes": ["{self}/data:/data", "{workspace}:/workspace", "{models}:/models"],
    "nginx_host": "forge.wickerman.local",
    "help": "## Code Forge\nAI-assisted code creation and execution sandbox.\n\n**Standalone:** Describe what you want, the AI writes code, you run it, iterate, export.\n\n**As a node:** POST `/node/execute` with `{\"instruction\": \"...\", \"language\": \"python\"}` to generate and run code.\n\n**Export:** Package projects as zip files or standalone installers.\n\n**Workspace:** Files saved to `~/wickerman/workspace/` persist across sessions."
}

WM_FORGE_FILES = {
    "data/Dockerfile": r"""FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git nodejs npm zip && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.* requests==2.32.* gunicorn==22.*
RUN useradd -m -s /bin/bash forgeuser
WORKDIR /app
ARG CACHEBUST=1
COPY . .
RUN chown -R forgeuser:forgeuser /app
USER forgeuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
""",

    "data/app.py": r"""
from flask import Flask, render_template, request, jsonify, send_file
import os, json, subprocess, tempfile, shutil, requests, time, uuid

app = Flask(__name__)
LLAMA_API = os.environ.get("LLAMA_API", "http://wm-llama:8080")
WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
os.makedirs(WORKSPACE, exist_ok=True)

def safe_path(base, user_input):
    joined = os.path.abspath(os.path.join(base, user_input))
    if not joined.startswith(os.path.abspath(base)):
        raise ValueError(f"Path traversal blocked: {user_input}")
    return joined

def ask_llm(prompt, system="You are a skilled programmer. Write clean, working code. Return ONLY code, no explanations unless asked."):
    try:
        r = requests.post(f"{LLAMA_API}/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role":"system","content":system}, {"role":"user","content":prompt}],
            "temperature": 0.3, "max_tokens": 2048, "stream": False
        }, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error calling LLM: {e}"

def run_code(code, language="python", timeout=30):
    # Execute code in a sandboxed subprocess.
    try:
        if language == "python":
            result = subprocess.run(["python3", "-c", code], capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        elif language == "javascript":
            result = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        elif language == "bash":
            result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        else:
            return {"stdout": "", "stderr": f"Unsupported language: {language}", "returncode": 1}
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Execution timed out", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}

@app.route("/")
def index(): return render_template("index.html")

@app.route("/health")
def health(): return jsonify({"status": "ok"})

@app.route("/api/generate", methods=["POST"])
def generate():
    d = request.json or {}
    instruction = d.get("instruction", "")
    language = d.get("language", "python")
    if not instruction: return jsonify({"error": "instruction required"}), 400
    prompt = f"Write a {language} program that does the following:\n{instruction}\n\nReturn ONLY the code."
    code = ask_llm(prompt)
    return jsonify({"code": code, "language": language})

@app.route("/api/run", methods=["POST"])
def api_run():
    d = request.json or {}
    code = d.get("code", "")
    language = d.get("language", "python")
    if not code: return jsonify({"error": "code required"}), 400
    result = run_code(code, language, timeout=d.get("timeout", 30))
    return jsonify(result)

@app.route("/api/save", methods=["POST"])
def save_file():
    d = request.json or {}
    filename = d.get("filename", "untitled.py")
    content = d.get("content", "")
    path = safe_path(WORKSPACE, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: f.write(content)
    return jsonify({"saved": filename})

@app.route("/api/files")
def list_files():
    files = []
    for root, dirs, fnames in os.walk(WORKSPACE):
        for fn in fnames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, WORKSPACE)
            files.append({"name": rel, "size": os.path.getsize(full)})
    files.sort(key=lambda x: x["name"])
    return jsonify(files)

@app.route("/api/export", methods=["POST"])
def export_project():
    d = request.json or {}
    project_name = d.get("name", "project")
    files = d.get("files", [])
    if not files: return jsonify({"error": "No files specified"}), 400
    zip_path = os.path.join("/tmp", f"{project_name}.zip")
    shutil.make_archive(zip_path.replace(".zip",""), "zip", WORKSPACE)
    return send_file(zip_path, as_attachment=True, download_name=f"{project_name}.zip")

# ── Node API ─────────────────────────────────────────────────
@app.route("/node/schema")
def node_schema():
    return jsonify({
        "name": "forge",
        "description": "Generate and execute code using a local LLM",
        "inputs": [
            {"name": "instruction", "type": "string", "required": False, "description": "Describe what code to generate"},
            {"name": "code", "type": "string", "required": False, "description": "Code to execute directly"},
            {"name": "language", "type": "string", "default": "python"},
            {"name": "run", "type": "boolean", "default": True, "description": "Whether to execute the code"},
        ],
        "outputs": [
            {"name": "code", "type": "string"},
            {"name": "stdout", "type": "string"},
            {"name": "stderr", "type": "string"},
            {"name": "returncode", "type": "number"},
        ]
    })

@app.route("/node/execute", methods=["POST"])
def node_execute():
    d = request.json or {}
    code = d.get("code", "")
    language = d.get("language", "python")
    if d.get("instruction") and not code:
        prompt = f"Write a {language} program: {d['instruction']}\nReturn ONLY code."
        code = ask_llm(prompt)
    if not code: return jsonify({"error": "No code to execute"}), 400
    result = {"code": code, "stdout": "", "stderr": "", "returncode": -1}
    if d.get("run", True):
        exec_result = run_code(code, language)
        result.update(exec_result)
    return jsonify(result)

if __name__ == "__main__": app.run(host="0.0.0.0", port=5000)
""",

    "data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wickerman Forge</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{background:#11111b;color:#cdd6f4;font-family:system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
.hdr{padding:12px 24px;border-bottom:1px solid #313244;display:flex;justify-content:space-between;align-items:center}.hdr h1{font-size:18px;color:#89b4fa}
.hdr .controls{display:flex;gap:8px;align-items:center;font-size:13px}
.hdr select{background:#1e1e2e;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px;outline:none}
.main{flex:1;display:flex;overflow:hidden}
.editor{flex:1;display:flex;flex-direction:column;border-right:1px solid #313244}
.prompt-area{padding:12px;border-bottom:1px solid #313244;display:flex;gap:8px}
.prompt-area input{flex:1;padding:10px;border-radius:6px;border:1px solid #313244;background:#1e1e2e;color:#cdd6f4;font-size:14px;outline:none}
.prompt-area input:focus{border-color:#89b4fa}
.btn{padding:10px 16px;border-radius:6px;border:none;font-weight:700;font-size:13px;cursor:pointer}
.btn.blue{background:#89b4fa;color:#1e1e2e}.btn.blue:hover{background:#74c7ec}
.btn.green{background:#a6e3a1;color:#1e1e2e}.btn.green:hover{background:#94e2d5}
.btn.gray{background:#313244;color:#cdd6f4}.btn.gray:hover{background:#45475a}
.btn:disabled{opacity:.5}
.code-area{flex:1;position:relative}
.code-area textarea{width:100%;height:100%;background:#11111b;color:#cdd6f4;border:none;padding:16px;font-family:'Cascadia Code','Fira Code',monospace;font-size:14px;line-height:1.5;resize:none;outline:none;tab-size:4}
.output{width:350px;display:flex;flex-direction:column}
.output h3{padding:12px;border-bottom:1px solid #313244;font-size:13px;color:#6c7086}
.out-content{flex:1;overflow-y:auto;padding:12px;font-family:monospace;font-size:13px;white-space:pre-wrap}
.out-content .stdout{color:#a6e3a1}.out-content .stderr{color:#f38ba8}
.files{border-top:1px solid #313244;max-height:200px;overflow-y:auto;padding:8px}
.files .f{padding:4px 8px;font-size:12px;color:#6c7086;cursor:pointer;border-radius:4px}.files .f:hover{background:#313244;color:#cdd6f4}
</style></head><body>
<div class="hdr">
  <h1>Code Forge</h1>
  <div class="controls">
    <select id="lang"><option value="python">Python</option><option value="javascript">JavaScript</option><option value="bash">Bash</option></select>
    <button class="btn green" onclick="runCode()">▶ Run</button>
    <button class="btn gray" onclick="saveFile()">💾 Save</button>
    <button class="btn gray" onclick="exportZip()">📦 Export</button>
  </div>
</div>
<div class="main">
  <div class="editor">
    <div class="prompt-area">
      <input id="prompt" placeholder="Describe what you want to build..." onkeydown="if(event.key==='Enter')generate()">
      <button class="btn blue" id="genBtn" onclick="generate()">Generate</button>
    </div>
    <div class="code-area"><textarea id="code" placeholder="# Write or generate code here..."></textarea></div>
  </div>
  <div class="output">
    <h3>Output</h3>
    <div class="out-content" id="out">Run code to see output.</div>
    <h3>Workspace Files</h3>
    <div class="files" id="files"></div>
  </div>
</div>
<script>
const code=document.getElementById('code'),out=document.getElementById('out'),lang=document.getElementById('lang');
async function generate(){const p=document.getElementById('prompt').value.trim();if(!p)return;const b=document.getElementById('genBtn');b.disabled=true;b.textContent='...';
try{const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction:p,language:lang.value})});
const d=await r.json();if(d.code)code.value=d.code}catch(e){out.innerHTML=`<span class="stderr">Error: ${e}</span>`}b.disabled=false;b.textContent='Generate'}
async function runCode(){const c=code.value;if(!c)return;out.innerHTML='Running...';
try{const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c,language:lang.value})});
const d=await r.json();let h='';if(d.stdout)h+=`<span class="stdout">${esc(d.stdout)}</span>`;if(d.stderr)h+=`<span class="stderr">${esc(d.stderr)}</span>`;
if(!d.stdout&&!d.stderr)h='<span style="color:#6c7086">(no output)</span>';out.innerHTML=h;loadFiles()}catch(e){out.innerHTML=`<span class="stderr">${e}</span>`}}
async function saveFile(){const fn=prompt('Filename:','script.py');if(!fn)return;
try{await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:fn,content:code.value})});loadFiles()}catch(e){}}
async function exportZip(){try{const r=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'project',files:['*']})});
const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='project.zip';a.click()}catch(e){alert('Export failed: '+e)}}
async function loadFiles(){try{const r=await(await fetch('/api/files')).json();document.getElementById('files').innerHTML=r.map(f=>`<div class="f" onclick="loadFile('${f.name}')">${f.name}</div>`).join('')||'<div class="f">Empty</div>'}catch(e){}}
async function loadFile(fn){try{/* future: load file content into editor */}catch(e){}}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
loadFiles();
</script></body></html>
"""
}

WM_FORGE["files"] = WM_FORGE_FILES

PLUGIN_HOST = ("127.0.0.1", "forge.wickerman.local")
