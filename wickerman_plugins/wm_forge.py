"""
Wickerman OS v5.7.0 - Code Forge plugin manifest.

Stage 1: Monaco editor, file tree, terminal, per-project isolation,
         AI assist, human-in-loop agent mode, web/PWA export.
Stage 2 (planned): Full auto agent loop, Flow deep integration.
Stage 3 (planned): wm-builder handoff for native compilation.
"""

WM_FORGE = {
    "name": "Code Forge",
    "description": "Local IDE with Monaco editor, terminal, AI assist, and multi-agent development",
    "icon": "construction",
    "build": True,
    "build_context": "data",
    "container_name": "wm-forge",
    "url": "http://forge.wickerman.local",
    "ports": [5000],
    "gpu": False,
    "env": [
        "LLAMA_API=http://wm-llama:8080",
        "WORKSPACE=/workspace",
        "DATA_DIR=/data",
    ],
    "volumes": [
        "{support}/workspace:/workspace",
        "{self}/data:/data",
    ],
    "nginx_host": "forge.wickerman.local",
    "help": (
        "## Code Forge\n"
        "A real local IDE with AI superpowers.\n\n"
        "**Editor:** Monaco (VS Code engine) with syntax highlighting, multi-file projects, file tree.\n\n"
        "**Terminal:** Full integrated terminal inside each project.\n\n"
        "**AI Assist:** Select text and ask an agent to explain, fix, or generate code. "
        "Uses any loaded agent from the Model Router.\n\n"
        "**Agent Mode:** Describe a goal. The AI writes a plan, you approve it, "
        "then it writes the code. You approve before anything runs.\n\n"
        "**Export:** Download any project as a zip. Web projects get a production build.\n\n"
        "**Workspace:** All projects saved to ~/WickermanSupport/workspace/ - "
        "survives reinstalls."
    ),
}

WM_FORGE_FILES = {

"data/Dockerfile": r"""FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# System deps: git, Node.js, build tools, zip
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget zip unzip \
    build-essential \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir \
    flask==3.0.* \
    gunicorn==22.* \
    requests==2.32.* \
    flask-sock==0.7.*

# Create non-root user
RUN useradd -m -s /bin/bash -u 1001 forgeuser

WORKDIR /app
ARG CACHEBUST=1
COPY . .
RUN chown -R forgeuser:forgeuser /app

# Workspace and data dirs
RUN mkdir -p /workspace /data && \
    chown -R forgeuser:forgeuser /workspace /data

USER forgeuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "120", "--worker-class", "gthread", "app:app"]
""",

"data/app.py": r"""#!/usr/bin/env python3
# Wickerman Code Forge - Backend
# Stage 1: Projects, files, execution, AI assist, human-in-loop agent mode
import os, sys, json, re, subprocess, threading, time, shutil, glob, signal
import tempfile, zipfile, hashlib, pty, select, fcntl, termios, struct
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

# ── Paths ──────────────────────────────────────────────────
WORKSPACE   = os.environ.get("WORKSPACE", "/workspace")
DATA_DIR    = os.environ.get("DATA_DIR", "/data")
LLAMA_API   = os.environ.get("LLAMA_API", "http://wm-llama:8080")

os.makedirs(WORKSPACE, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ── Security ───────────────────────────────────────────────
def safe_path(base, user_input):
    base = os.path.abspath(base)
    joined = os.path.abspath(os.path.join(base, user_input.lstrip("/")))
    if not joined.startswith(base):
        raise ValueError(f"Path traversal blocked: {user_input}")
    return joined

def project_path(project_name):
    safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name)
    return os.path.join(WORKSPACE, safe)

# ── Project Management ─────────────────────────────────────
def list_projects():
    projects = []
    if not os.path.isdir(WORKSPACE):
        return projects
    for entry in sorted(os.listdir(WORKSPACE)):
        full = os.path.join(WORKSPACE, entry)
        if not os.path.isdir(full):
            continue
        meta_path = os.path.join(full, ".forge_meta.json")
        meta = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except: pass
        # Count files
        file_count = sum(1 for _ in Path(full).rglob("*") if _.is_file() and not _.name.startswith("."))
        projects.append({
            "name": entry,
            "type": meta.get("type", "python"),
            "description": meta.get("description", ""),
            "created_at": meta.get("created_at", ""),
            "file_count": file_count,
        })
    return projects

def create_project(name, project_type="python", description=""):
    path = project_path(name)
    if os.path.exists(path):
        return False, f"Project '{name}' already exists"
    os.makedirs(path, exist_ok=True)
    meta = {
        "name": name,
        "type": project_type,
        "description": description,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(path, ".forge_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    # Create initial files based on type
    _scaffold_project(path, project_type, name)
    return True, path

def _scaffold_project(path, project_type, name):
    templates = {
        "python": {
            "main.py": f'# {name}\n\ndef main():\n    print("Hello from {name}!")\n\nif __name__ == "__main__":\n    main()\n',
            "requirements.txt": "# Add your dependencies here\n",
        },
        "flask": {
            "app.py": f'from flask import Flask, jsonify\n\napp = Flask(__name__)\n\n@app.route("/")\ndef index():\n    return jsonify({{"message": "Hello from {name}!"}})\n\nif __name__ == "__main__":\n    app.run(debug=True)\n',
            "requirements.txt": "flask\n",
        },
        "web": {
            "index.html": f'<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <title>{name}</title>\n  <link rel="stylesheet" href="style.css">\n</head>\n<body>\n  <h1>{name}</h1>\n  <script src="script.js"></script>\n</body>\n</html>\n',
            "style.css": f'/* {name} styles */\nbody {{\n  font-family: system-ui, sans-serif;\n  margin: 2rem;\n}}\n',
            "script.js": f'// {name}\nconsole.log("{name} loaded");\n',
        },
        "node": {
            "index.js": f'// {name}\nconsole.log("Hello from {name}!");\n',
            "package.json": json.dumps({"name": name.lower().replace(" ", "-"), "version": "1.0.0", "main": "index.js"}, indent=2) + "\n",
        },
        "wm-plugin": {
            "manifest.json": json.dumps({
                "name": name,
                "description": "A Wickerman plugin",
                "icon": "extension",
                "container_name": f"wm-{name.lower().replace(' ', '-')}",
                "url": f"http://{name.lower().replace(' ', '-')}.wickerman.local",
                "ports": [5000],
                "gpu": False,
                "env": [],
                "volumes": ["{self}/data:/data"],
                "nginx_host": f"{name.lower().replace(' ', '-')}.wickerman.local",
            }, indent=2) + "\n",
            "plugin.py": f'# {name} Wickerman Plugin\n# See docs at http://wickerman.local for plugin authoring guide\n\nWM_PLUGIN = {{}}\nWM_PLUGIN_FILES = {{}}\nWM_PLUGIN["files"] = WM_PLUGIN_FILES\n',
        },
        "blank": {},
    }
    files = templates.get(project_type, templates["blank"])
    for filename, content in files.items():
        filepath = os.path.join(path, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(content)

# ── File Operations ────────────────────────────────────────
def list_files(project_name, subdir=""):
    base = project_path(project_name)
    target = safe_path(base, subdir) if subdir else base
    if not os.path.isdir(target):
        return []
    items = []
    for entry in sorted(os.listdir(target)):
        if entry.startswith("."):
            continue
        full = os.path.join(target, entry)
        rel = os.path.relpath(full, base)
        if os.path.isdir(full):
            items.append({"name": entry, "path": rel, "type": "dir", "children": []})
        else:
            items.append({
                "name": entry,
                "path": rel,
                "type": "file",
                "size": os.path.getsize(full),
                "ext": os.path.splitext(entry)[1].lower(),
            })
    return items

def read_file(project_name, filepath):
    base = project_path(project_name)
    full = safe_path(base, filepath)
    if not os.path.isfile(full):
        return None, "File not found"
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)

def write_file(project_name, filepath, content):
    base = project_path(project_name)
    full = safe_path(base, filepath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return True

def delete_file(project_name, filepath):
    base = project_path(project_name)
    full = safe_path(base, filepath)
    if os.path.isfile(full):
        os.remove(full)
    elif os.path.isdir(full):
        shutil.rmtree(full)
    return True

def rename_file(project_name, old_path, new_path):
    base = project_path(project_name)
    old_full = safe_path(base, old_path)
    new_full = safe_path(base, new_path)
    os.makedirs(os.path.dirname(new_full), exist_ok=True)
    os.rename(old_full, new_full)
    return True

# ── Code Execution ─────────────────────────────────────────
def _get_venv(project_name):
    return os.path.join(project_path(project_name), ".venv")

def _ensure_venv(project_name):
    venv_path = _get_venv(project_name)
    if not os.path.isdir(venv_path):
        subprocess.run(
            [sys.executable, "-m", "venv", venv_path],
            capture_output=True, timeout=30
        )
    return venv_path

def _python_bin(project_name):
    venv = _get_venv(project_name)
    bin_path = os.path.join(venv, "bin", "python")
    return bin_path if os.path.isfile(bin_path) else sys.executable

def run_code(project_name, code, language="python", timeout=30, entry_file=None):
    cwd = project_path(project_name)
    env = os.environ.copy()
    env["HOME"] = cwd  # Isolate home dir

    try:
        if language == "python":
            python_bin = _python_bin(project_name)
            if entry_file:
                full = safe_path(cwd, entry_file)
                cmd = [python_bin, full]
            else:
                cmd = [python_bin, "-c", code]
        elif language == "javascript" or language == "node":
            node_bin = shutil.which("node") or "node"
            if entry_file:
                full = safe_path(cwd, entry_file)
                cmd = [node_bin, full]
            else:
                cmd = [node_bin, "-e", code]
        elif language == "bash":
            cmd = ["bash", "-c", code]
        else:
            return {"stdout": "", "stderr": f"Unsupported language: {language}", "returncode": 1}

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=env
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Execution timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}

def install_package(project_name, package, manager="pip"):
    cwd = project_path(project_name)
    if manager == "pip":
        venv = _ensure_venv(project_name)
        pip_bin = os.path.join(venv, "bin", "pip")
        if not os.path.isfile(pip_bin):
            pip_bin = "pip3"
        cmd = [pip_bin, "install", package]
    elif manager == "npm":
        cmd = ["npm", "install", package, "--prefix", cwd]
    else:
        return {"ok": False, "error": f"Unknown manager: {manager}"}

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, cwd=cwd
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Install timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── AI Assist ─────────────────────────────────────────────
def call_agent(prompt, system_prompt=None, agent="default", max_tokens=2048):
    import urllib.request
    if not system_prompt:
        system_prompt = "You are an expert programmer. Write clean, correct code."
    payload = {
        "model": agent,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        req = urllib.request.Request(
            f"{LLAMA_API}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"], None
    except Exception as e:
        # Check if router is up
        try:
            urllib.request.urlopen(f"{LLAMA_API}/health", timeout=3)
            return None, f"Agent call failed: {e}"
        except:
            return None, "Model Router is not running. Please load a model in the Router first."

def strip_code_fences(text):
    text = text.strip()
    # Remove ```lang\n...\n``` or ```\n...\n```
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    return text.strip()

# ── Agent Mode (Human-in-Loop) ────────────────────────────
_agent_sessions = {}
_sessions_lock = threading.Lock()

def create_agent_session(project_name, goal, agent="default"):
    session_id = hashlib.md5(f"{project_name}{goal}{time.time()}".encode()).hexdigest()[:12]
    session = {
        "id": session_id,
        "project": project_name,
        "goal": goal,
        "agent": agent,
        "status": "planning",  # planning | awaiting_plan_approval | coding | awaiting_code_approval | running | done | error
        "plan": None,
        "steps": [],
        "current_step": 0,
        "log": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _sessions_lock:
        _agent_sessions[session_id] = session

    # Start planning in background
    threading.Thread(target=_run_planning, args=(session_id,), daemon=True).start()
    return session_id

def _log_session(session_id, msg):
    ts = time.strftime("%H:%M:%S")
    with _sessions_lock:
        if session_id in _agent_sessions:
            _agent_sessions[session_id]["log"].append(f"[{ts}] {msg}")

def _run_planning(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s: return
        goal = s["goal"]
        agent = s["agent"]
        project = s["project"]

    _log_session(session_id, f"Analyzing goal: {goal}")

    system = (
        "You are a software project planner. Given a goal, break it into clear, concrete steps. "
        "Return ONLY a JSON object with keys: "
        '"summary" (one sentence), '
        '"steps" (array of objects with "title" and "description"), '
        '"files" (array of filenames that will be created or modified). '
        "No markdown, no backticks, no explanation outside the JSON."
    )
    prompt = f"Project: {project}\nGoal: {goal}\n\nCreate a development plan."

    reply, err = call_agent(prompt, system, agent, max_tokens=1024)
    if err:
        _log_session(session_id, f"Planning failed: {err}")
        with _sessions_lock:
            _agent_sessions[session_id]["status"] = "error"
            _agent_sessions[session_id]["error"] = err
        return

    # Strip and parse
    reply = reply.strip()
    if reply.startswith("```"):
        reply = re.sub(r'^```[a-zA-Z]*\n', '', reply)
        reply = re.sub(r'\n```$', '', reply)

    try:
        plan = json.loads(reply)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        match = re.search(r'\{.*\}', reply, re.DOTALL)
        if match:
            try:
                plan = json.loads(match.group())
            except:
                plan = {"summary": goal, "steps": [{"title": "Write code", "description": goal}], "files": []}
        else:
            plan = {"summary": goal, "steps": [{"title": "Write code", "description": goal}], "files": []}

    _log_session(session_id, f"Plan ready: {len(plan.get('steps', []))} steps")
    with _sessions_lock:
        _agent_sessions[session_id]["plan"] = plan
        _agent_sessions[session_id]["status"] = "awaiting_plan_approval"

def _run_coding(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s: return
        plan = s["plan"]
        goal = s["goal"]
        agent = s["agent"]
        project = s["project"]

    steps = plan.get("steps", [])
    _log_session(session_id, f"Starting coding: {len(steps)} steps")

    with _sessions_lock:
        _agent_sessions[session_id]["status"] = "coding"

    all_code = {}  # filename -> code

    for i, step in enumerate(steps):
        with _sessions_lock:
            _agent_sessions[session_id]["current_step"] = i

        _log_session(session_id, f"Step {i+1}/{len(steps)}: {step['title']}")

        system = (
            "You are an expert programmer. Write clean, working code for the given task. "
            "Return ONLY the code — no explanations, no markdown fences. "
            "If multiple files are needed, separate them with a comment like: # FILE: filename.py"
        )
        context = f"Project: {project}\nOverall goal: {goal}\n"
        if all_code:
            context += f"Files written so far: {list(all_code.keys())}\n"
        prompt = f"{context}\nCurrent step: {step['title']}\n{step['description']}\n\nWrite the code:"

        reply, err = call_agent(prompt, system, agent, max_tokens=2048)
        if err:
            _log_session(session_id, f"Coding failed at step {i+1}: {err}")
            with _sessions_lock:
                _agent_sessions[session_id]["status"] = "error"
                _agent_sessions[session_id]["error"] = err
            return

        # Parse multi-file responses
        if "# FILE:" in reply:
            parts = re.split(r'# FILE:\s*(\S+)', reply)
            for j in range(1, len(parts), 2):
                fname = parts[j].strip()
                code = parts[j+1].strip() if j+1 < len(parts) else ""
                all_code[fname] = code
        else:
            # Single file — use first suggested filename from plan or step
            fname = plan.get("files", ["output.py"])[0] if plan.get("files") else "main.py"
            all_code[fname] = strip_code_fences(reply)

        _log_session(session_id, f"Step {i+1} complete")

    with _sessions_lock:
        _agent_sessions[session_id]["status"] = "awaiting_code_approval"
        _agent_sessions[session_id]["generated_files"] = all_code
        _log_session(session_id, f"Code ready: {list(all_code.keys())} — awaiting your approval")

def _apply_generated_files(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s: return False
        files = s.get("generated_files", {})
        project = s["project"]

    for fname, code in files.items():
        try:
            write_file(project, fname, code)
        except Exception as e:
            _log_session(session_id, f"Failed to write {fname}: {e}")
            return False

    _log_session(session_id, f"Files written: {list(files.keys())}")
    with _sessions_lock:
        _agent_sessions[session_id]["status"] = "done"
    return True

# ── Export ─────────────────────────────────────────────────
def export_project_zip(project_name):
    base = project_path(project_name)
    if not os.path.isdir(base):
        return None, "Project not found"
    zip_path = os.path.join(DATA_DIR, f"{project_name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(base):
            # Skip hidden dirs and venv
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for file in files:
                if file.startswith("."): continue
                full = os.path.join(root, file)
                arcname = os.path.join(project_name, os.path.relpath(full, base))
                zf.write(full, arcname)
    return zip_path, None

def build_web_project(project_name):
    base = project_path(project_name)
    # Check if it has package.json with a build script
    pkg_path = os.path.join(base, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path) as f:
                pkg = json.load(f)
            if "scripts" in pkg and "build" in pkg["scripts"]:
                result = subprocess.run(
                    ["npm", "run", "build"],
                    capture_output=True, text=True,
                    timeout=120, cwd=base
                )
                return result.returncode == 0, result.stdout + result.stderr
        except: pass
    # Static web — just zip the html/css/js files
    return True, "Static web project — use Export to download."

# ══════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ── Projects ──────────────────────────────────────────────
@app.route("/api/projects")
def api_list_projects():
    return jsonify({"projects": list_projects()})

@app.route("/api/projects/create", methods=["POST"])
def api_create_project():
    d = request.json or {}
    name = d.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    ok, result = create_project(name, d.get("type", "python"), d.get("description", ""))
    return jsonify({"ok": ok, "path": result if ok else None, "error": result if not ok else None})

@app.route("/api/projects/<name>", methods=["DELETE"])
def api_delete_project(name):
    path = project_path(name)
    if os.path.isdir(path):
        shutil.rmtree(path)
    return jsonify({"ok": True})

# ── Files ─────────────────────────────────────────────────
@app.route("/api/projects/<project>/files")
def api_list_files(project):
    return jsonify({"files": list_files(project)})

@app.route("/api/projects/<project>/file", methods=["GET"])
def api_read_file(project):
    filepath = request.args.get("path", "")
    content, err = read_file(project, filepath)
    if err:
        return jsonify({"error": err}), 404
    return jsonify({"content": content, "path": filepath})

@app.route("/api/projects/<project>/file", methods=["POST"])
def api_write_file(project):
    d = request.json or {}
    filepath = d.get("path", "")
    content = d.get("content", "")
    if not filepath:
        return jsonify({"error": "path required"}), 400
    try:
        write_file(project, filepath, content)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/projects/<project>/file", methods=["DELETE"])
def api_delete_file(project):
    filepath = request.args.get("path", "")
    try:
        delete_file(project, filepath)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/projects/<project>/file/rename", methods=["POST"])
def api_rename_file(project):
    d = request.json or {}
    try:
        rename_file(project, d.get("old_path", ""), d.get("new_path", ""))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Execution ─────────────────────────────────────────────
@app.route("/api/projects/<project>/run", methods=["POST"])
def api_run(project):
    d = request.json or {}
    result = run_code(
        project,
        d.get("code", ""),
        d.get("language", "python"),
        int(d.get("timeout", 30)),
        d.get("entry_file"),
    )
    return jsonify(result)

@app.route("/api/projects/<project>/install", methods=["POST"])
def api_install(project):
    d = request.json or {}
    package = d.get("package", "").strip()
    manager = d.get("manager", "pip")
    if not package:
        return jsonify({"error": "package required"}), 400
    result = install_package(project, package, manager)
    return jsonify(result)

@app.route("/api/projects/<project>/setup_venv", methods=["POST"])
def api_setup_venv(project):
    try:
        _ensure_venv(project)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── AI Assist ─────────────────────────────────────────────
@app.route("/api/ai/generate", methods=["POST"])
def api_ai_generate():
    d = request.json or {}
    instruction = d.get("instruction", "").strip()
    language = d.get("language", "python")
    agent = d.get("agent", "default")
    context = d.get("context", "")  # surrounding code for context
    if not instruction:
        return jsonify({"error": "instruction required"}), 400

    system = (
        f"You are an expert {language} programmer. "
        "Write clean, working code. Return ONLY the code — no markdown fences, no explanation."
    )
    prompt = instruction
    if context:
        prompt = f"Context (existing code):\n{context}\n\nTask: {instruction}"

    reply, err = call_agent(prompt, system, agent)
    if err:
        return jsonify({"error": err}), 503
    return jsonify({"code": strip_code_fences(reply), "language": language})

@app.route("/api/ai/explain", methods=["POST"])
def api_ai_explain():
    d = request.json or {}
    code = d.get("code", "").strip()
    agent = d.get("agent", "default")
    if not code:
        return jsonify({"error": "code required"}), 400
    system = "You are a helpful programming tutor. Explain code clearly in plain English."
    prompt = f"Explain this code:\n\n{code}"
    reply, err = call_agent(prompt, system, agent, max_tokens=1024)
    if err:
        return jsonify({"error": err}), 503
    return jsonify({"explanation": reply})

@app.route("/api/ai/fix", methods=["POST"])
def api_ai_fix():
    d = request.json or {}
    code = d.get("code", "").strip()
    error = d.get("error", "").strip()
    agent = d.get("agent", "default")
    if not code:
        return jsonify({"error": "code required"}), 400
    system = "You are an expert debugger. Fix the code. Return ONLY the fixed code — no explanation, no markdown fences."
    prompt = f"Code:\n{code}\n\nError:\n{error}\n\nFixed code:"
    reply, err = call_agent(prompt, system, agent)
    if err:
        return jsonify({"error": err}), 503
    return jsonify({"code": strip_code_fences(reply)})

@app.route("/api/ai/refactor", methods=["POST"])
def api_ai_refactor():
    d = request.json or {}
    code = d.get("code", "").strip()
    instruction = d.get("instruction", "").strip()
    agent = d.get("agent", "default")
    if not code or not instruction:
        return jsonify({"error": "code and instruction required"}), 400
    system = "You are an expert programmer. Refactor the code as instructed. Return ONLY the refactored code."
    prompt = f"Code:\n{code}\n\nInstruction: {instruction}\n\nRefactored code:"
    reply, err = call_agent(prompt, system, agent)
    if err:
        return jsonify({"error": err}), 503
    return jsonify({"code": strip_code_fences(reply)})

# ── Agent Mode (Human-in-Loop) ────────────────────────────
@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    d = request.json or {}
    project = d.get("project", "").strip()
    goal = d.get("goal", "").strip()
    agent = d.get("agent", "default")
    if not project or not goal:
        return jsonify({"error": "project and goal required"}), 400
    session_id = create_agent_session(project, goal, agent)
    return jsonify({"session_id": session_id})

@app.route("/api/agent/<session_id>/status")
def api_agent_status(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({
        "id": s["id"],
        "status": s["status"],
        "plan": s.get("plan"),
        "current_step": s.get("current_step", 0),
        "generated_files": list(s.get("generated_files", {}).keys()),
        "log": s["log"],
        "error": s.get("error"),
    })

@app.route("/api/agent/<session_id>/approve_plan", methods=["POST"])
def api_agent_approve_plan(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s:
            return jsonify({"error": "Session not found"}), 404
        if s["status"] != "awaiting_plan_approval":
            return jsonify({"error": f"Cannot approve plan in status: {s['status']}"}), 400
    threading.Thread(target=_run_coding, args=(session_id,), daemon=True).start()
    return jsonify({"ok": True, "message": "Plan approved. Writing code..."})

@app.route("/api/agent/<session_id>/reject_plan", methods=["POST"])
def api_agent_reject_plan(session_id):
    d = request.json or {}
    feedback = d.get("feedback", "")
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s:
            return jsonify({"error": "Session not found"}), 404
        s["status"] = "planning"
        if feedback:
            s["goal"] = s["goal"] + f"\n\nRevision requested: {feedback}"
    threading.Thread(target=_run_planning, args=(session_id,), daemon=True).start()
    return jsonify({"ok": True, "message": "Replanning..."})

@app.route("/api/agent/<session_id>/approve_code", methods=["POST"])
def api_agent_approve_code(session_id):
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s:
            return jsonify({"error": "Session not found"}), 404
        if s["status"] != "awaiting_code_approval":
            return jsonify({"error": f"Cannot approve code in status: {s['status']}"}), 400
    ok = _apply_generated_files(session_id)
    return jsonify({"ok": ok})

@app.route("/api/agent/<session_id>/reject_code", methods=["POST"])
def api_agent_reject_code(session_id):
    d = request.json or {}
    feedback = d.get("feedback", "")
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
        if not s:
            return jsonify({"error": "Session not found"}), 404
        s["status"] = "planning"
        if feedback:
            s["goal"] = s["goal"] + f"\n\nCode revision: {feedback}"
    threading.Thread(target=_run_planning, args=(session_id,), daemon=True).start()
    return jsonify({"ok": True, "message": "Revising..."})

@app.route("/api/agent/<session_id>/preview_file")
def api_agent_preview(session_id):
    filepath = request.args.get("file", "")
    with _sessions_lock:
        s = _agent_sessions.get(session_id)
    if not s:
        return jsonify({"error": "Session not found"}), 404
    files = s.get("generated_files", {})
    if filepath not in files:
        return jsonify({"error": "File not in generated set"}), 404
    return jsonify({"content": files[filepath], "path": filepath})

# ── Export ────────────────────────────────────────────────
@app.route("/api/projects/<project>/export")
def api_export(project):
    zip_path, err = export_project_zip(project)
    if err:
        return jsonify({"error": err}), 404
    return send_file(zip_path, as_attachment=True, download_name=f"{project}.zip")

@app.route("/api/projects/<project>/build", methods=["POST"])
def api_build(project):
    d = request.json or {}
    target = d.get("target", "web")
    if target == "web":
        ok, msg = build_web_project(project)
        return jsonify({"ok": ok, "message": msg})
    return jsonify({"error": f"Build target '{target}' not supported in Stage 1. Use Export for now."}), 400

# ── Loaded agents list ────────────────────────────────────
@app.route("/api/agents")
def api_agents():
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{LLAMA_API}/api/status", timeout=3)
        data = json.loads(resp.read())
        agents = [a for a, s in data.get("slots", {}).items() if s.get("status") == "ready"]
        return jsonify({"agents": agents})
    except:
        return jsonify({"agents": []})

# ── Node API (Flow Editor) ────────────────────────────────
# ── Terminal WebSocket ────────────────────────────────────────────
@sock.route("/api/projects/<project>/terminal")
def terminal_ws(ws, project):
    cwd = project_path(project)
    if not os.path.isdir(cwd):
        ws.send("Project not found\r\n")
        return
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["HOME"] = cwd
        env["PS1"] = "[" + project + "]$ "
        os.execvpe("bash", ["bash"], env)
    else:
        try:
            while True:
                r, _, _ = select.select([fd], [], [], 0.02)
                if r:
                    try:
                        data = os.read(fd, 1024)
                        if data:
                            ws.send(data.decode("utf-8", errors="replace"))
                    except OSError:
                        break
                try:
                    msg = ws.receive(timeout=0)
                    if msg is not None:
                        if isinstance(msg, str) and msg.startswith("{"):
                            try:
                                d = json.loads(msg)
                                if d.get("type") == "resize":
                                    cols, rows = d.get("cols", 80), d.get("rows", 24)
                                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                            except: pass
                        elif isinstance(msg, str):
                            os.write(fd, msg.encode())
                        elif isinstance(msg, bytes):
                            os.write(fd, msg)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try: os.kill(pid, 9)
            except: pass
            try: os.waitpid(pid, 0)
            except: pass
            try: os.close(fd)
            except: pass

@app.route("/node/schema")
def node_schema():
    return jsonify({
        "name": "forge",
        "description": "Execute code or generate-and-run via AI agent",
        "inputs": [
            {"name": "instruction", "type": "string", "required": False},
            {"name": "code", "type": "string", "required": False},
            {"name": "language", "type": "string", "default": "python"},
            {"name": "project", "type": "string", "default": "scratch"},
            {"name": "agent", "type": "string", "default": "default"},
            {"name": "timeout", "type": "number", "default": 30},
        ],
        "outputs": [
            {"name": "stdout", "type": "string"},
            {"name": "stderr", "type": "string"},
            {"name": "returncode", "type": "number"},
            {"name": "code", "type": "string"},
        ]
    })

@app.route("/node/execute", methods=["POST"])
def node_execute():
    d = request.json or {}
    code = d.get("code", "")
    language = d.get("language", "python")
    project = d.get("project", "scratch")
    agent = d.get("agent", "default")
    timeout = int(d.get("timeout", 30))

    # Ensure scratch project exists
    if not os.path.isdir(project_path(project)):
        create_project(project, "python", "Auto-created scratch project")

    if d.get("instruction") and not code:
        reply, err = call_agent(
            f"Write {language} code: {d['instruction']}\nReturn ONLY the code.",
            agent=agent
        )
        if err:
            return jsonify({"error": err, "stdout": "", "stderr": err, "returncode": -1})
        code = strip_code_fences(reply)

    if not code:
        return jsonify({"error": "No code to execute"}), 400

    result = run_code(project, code, language, timeout)
    result["code"] = code
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
""",

"data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Forge</title>
<!-- Monaco Editor -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/editor/editor.main.min.css">
<!-- xterm.js -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.css">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;
  --text:#cdd6f4;--sub:#6c7086;--blue:#89b4fa;--green:#a6e3a1;
  --red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;
  --mono:'Cascadia Code','Fira Code','Courier New',monospace;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif}

/* ── Layout ── */
.app{display:flex;flex-direction:column;height:100vh}
.topbar{height:42px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 12px;gap:8px;flex-shrink:0}
.topbar .logo{font-size:14px;font-weight:700;color:var(--mauve);letter-spacing:.5px;margin-right:8px}
.topbar .project-name{font-size:13px;color:var(--text);font-weight:600}
.topbar .project-name span{color:var(--sub);font-weight:400}
.main{display:flex;flex:1;overflow:hidden}

/* ── Sidebar ── */
.sidebar{width:220px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.sidebar-header{padding:10px 12px;font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.sidebar-header .icon-btn{cursor:pointer;color:var(--blue);font-size:16px;line-height:1}
.file-tree{flex:1;overflow-y:auto;padding:4px 0}
.tree-item{padding:5px 12px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px;color:var(--text)}
.tree-item:hover{background:rgba(137,180,250,.08)}
.tree-item.active{background:rgba(137,180,250,.15);color:var(--blue)}
.tree-item .icon{font-size:13px;width:16px;text-align:center;color:var(--sub)}
.tree-item.dir .icon{color:var(--yellow)}
.tree-indent{padding-left:20px}

/* ── Editor area ── */
.editor-area{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.editor-tabs{height:36px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;overflow-x:auto;flex-shrink:0}
.tab{padding:0 14px;height:100%;display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub);cursor:pointer;border-right:1px solid var(--border);white-space:nowrap;min-width:0}
.tab:hover{color:var(--text);background:rgba(49,50,68,.4)}
.tab.active{color:var(--text);background:var(--overlay);border-bottom:2px solid var(--blue)}
.tab .close{opacity:0;font-size:11px;margin-left:2px}
.tab:hover .close{opacity:1}
#monaco-container{flex:1;overflow:hidden}
.editor-bottom{height:200px;background:var(--bg);border-top:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.bottom-tabs{height:30px;background:var(--surface);display:flex;align-items:center;gap:0;border-bottom:1px solid var(--border);flex-shrink:0}
.bottom-tab{padding:0 14px;height:100%;display:flex;align-items:center;font-size:11px;color:var(--sub);cursor:pointer}
.bottom-tab:hover{color:var(--text)}
.bottom-tab.active{color:var(--blue);border-bottom:2px solid var(--blue)}
.bottom-panel{flex:1;overflow:hidden;display:none}
.bottom-panel.active{display:flex;flex-direction:column}
.output-console{flex:1;padding:8px 12px;font-family:var(--mono);font-size:12px;overflow-y:auto;white-space:pre-wrap;line-height:1.5}
.output-console .stdout{color:var(--green)}
.output-console .stderr{color:var(--red)}
.output-console .info{color:var(--sub)}
#terminal-container{flex:1;padding:4px}

/* ── Right panel (AI) ── */
.ai-panel{width:300px;background:var(--surface);border-left:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;transition:width .2s}
.ai-panel.collapsed{width:36px}
.ai-panel-header{padding:10px 12px;font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;cursor:pointer;flex-shrink:0}
.ai-panel-header .icon-btn{font-size:16px;color:var(--blue)}
.ai-panel-body{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
.ai-panel.collapsed .ai-panel-body{display:none}
.ai-section-label{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.ai-btn{width:100%;padding:8px 10px;background:var(--overlay);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px;cursor:pointer;text-align:left;font-family:inherit}
.ai-btn:hover{border-color:var(--blue);color:var(--blue)}
.ai-input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px;font-size:12px;outline:none;font-family:inherit;resize:vertical;min-height:60px}
.ai-input:focus{border-color:var(--blue)}
.ai-submit{width:100%;padding:8px;background:var(--blue);border:none;border-radius:6px;color:var(--bg);font-weight:700;font-size:12px;cursor:pointer;margin-top:4px}
.ai-submit:hover{opacity:.9}
.ai-submit:disabled{opacity:.4}
.agent-select{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px;border-radius:6px;font-size:12px;outline:none}
.ai-result{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:11px;line-height:1.5;max-height:200px;overflow-y:auto;white-space:pre-wrap;font-family:var(--mono)}
.ai-apply-btn{padding:5px 10px;background:var(--green);border:none;border-radius:4px;color:var(--bg);font-size:11px;font-weight:700;cursor:pointer;margin-top:4px}

/* ── Agent mode ── */
.agent-mode-section{background:var(--overlay);border:1px solid var(--border);border-radius:8px;padding:10px}
.agent-status{font-size:11px;padding:4px 8px;border-radius:10px;display:inline-block;font-weight:600;margin-bottom:8px}
.status-planning{background:rgba(249,226,175,.15);color:var(--yellow)}
.status-awaiting{background:rgba(137,180,250,.15);color:var(--blue)}
.status-coding{background:rgba(166,227,161,.15);color:var(--green)}
.status-done{background:rgba(166,227,161,.2);color:var(--green)}
.status-error{background:rgba(243,139,168,.15);color:var(--red)}
.plan-step{padding:6px 8px;background:var(--bg);border-radius:4px;font-size:11px;margin-bottom:4px}
.plan-step .step-title{font-weight:600;color:var(--text)}
.plan-step .step-desc{color:var(--sub);margin-top:2px}
.approve-btn{padding:6px 12px;background:var(--green);border:none;border-radius:4px;color:var(--bg);font-weight:700;font-size:11px;cursor:pointer;margin-right:6px}
.reject-btn{padding:6px 12px;background:transparent;border:1px solid var(--red);border-radius:4px;color:var(--red);font-size:11px;cursor:pointer}
.agent-log{background:var(--bg);border-radius:4px;padding:6px;font-family:var(--mono);font-size:10px;color:var(--sub);max-height:100px;overflow-y:auto;margin-top:6px}

/* ── Modals ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:1000;display:none}
.modal-overlay.open{display:flex}
.modal{background:var(--overlay);border:1px solid var(--border);border-radius:10px;padding:24px;width:420px;max-width:90vw}
.modal h3{font-size:16px;font-weight:600;margin-bottom:16px;color:var(--blue)}
.modal-field{margin-bottom:12px}
.modal-field label{display:block;font-size:12px;color:var(--sub);margin-bottom:4px}
.modal-field input,.modal-field select,.modal-field textarea{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 10px;border-radius:6px;font-size:13px;outline:none;font-family:inherit}
.modal-field input:focus,.modal-field select:focus{border-color:var(--blue)}
.modal-actions{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
.btn{padding:8px 16px;border-radius:6px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn-primary{background:var(--blue);color:var(--bg)}
.btn-primary:hover{opacity:.9}
.btn-muted{background:var(--border);color:var(--text)}
.btn-muted:hover{background:#45475a}

/* ── Project list ── */
.project-list{padding:12px;overflow-y:auto;flex:1}
.proj-item{background:var(--overlay);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px;cursor:pointer}
.proj-item:hover{border-color:var(--blue)}
.proj-item .pname{font-size:13px;font-weight:600;color:var(--blue)}
.proj-item .pmeta{font-size:11px;color:var(--sub);margin-top:3px}
.proj-actions{display:flex;gap:6px;margin-top:8px}
.proj-btn{padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);font-size:11px;cursor:pointer}
.proj-btn:hover{border-color:var(--blue);color:var(--blue)}
.proj-btn.danger:hover{border-color:var(--red);color:var(--red)}

/* ── Topbar buttons ── */
.tb-btn{padding:5px 10px;border-radius:5px;border:1px solid var(--border);background:transparent;color:var(--sub);font-size:11px;cursor:pointer;font-family:inherit}
.tb-btn:hover{color:var(--text);border-color:var(--text)}
.tb-btn.primary{background:var(--blue);color:var(--bg);border-color:var(--blue);font-weight:600}
.tb-btn.primary:hover{opacity:.9}
.tb-btn.success{background:var(--green);color:var(--bg);border-color:var(--green);font-weight:600}
.tb-run{background:var(--green);color:var(--bg);border:none;padding:5px 14px;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer}
.tb-run:hover{opacity:.9}
.tb-run:disabled{opacity:.4}
.mode-toggle{display:flex;border:1px solid var(--border);border-radius:5px;overflow:hidden}
.mode-opt{padding:4px 10px;font-size:11px;cursor:pointer;color:var(--sub);background:transparent;border:none;font-family:inherit}
.mode-opt:hover{color:var(--text)}
.mode-opt.active{background:var(--mauve);color:var(--bg);font-weight:600}
.spacer{flex:1}
.lang-select{background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:5px;font-size:11px;outline:none}
</style>
</head>
<body>
<div class="app" id="app">

  <!-- Top bar -->
  <div class="topbar">
    <span class="logo">⚒ CODE FORGE</span>
    <span class="project-name" id="currentProjectLabel"><span>No project open</span></span>
    <div class="mode-toggle">
      <button class="mode-opt active" id="modeGuided" onclick="setMode('guided')">Guided</button>
      <button class="mode-opt" id="modePro" onclick="setMode('pro')">Pro</button>
    </div>
    <div class="spacer"></div>
    <select class="lang-select" id="langSelect" onchange="onLangChange()">
      <option value="python">Python</option>
      <option value="javascript">JavaScript</option>
      <option value="bash">Bash</option>
    </select>
    <button class="tb-run" id="runBtn" onclick="runCurrentFile()" disabled>▶ Run</button>
    <button class="tb-btn" onclick="openInstallModal()">+ Package</button>
    <button class="tb-btn" onclick="exportProject()">⬇ Export</button>
    <button class="tb-btn primary" onclick="showProjectModal()">Projects</button>
  </div>

  <!-- Main area -->
  <div class="main">

    <!-- File tree sidebar -->
    <div class="sidebar" id="sidebar">
      <div class="sidebar-header">
        Files
        <span class="icon-btn" onclick="showNewFileModal()" title="New file">+</span>
      </div>
      <div class="file-tree" id="fileTree">
        <div style="padding:16px;color:var(--sub);font-size:12px">Open a project to see files.</div>
      </div>
    </div>

    <!-- Editor -->
    <div class="editor-area">
      <div class="editor-tabs" id="editorTabs"></div>
      <div id="monaco-container"></div>
      <div class="editor-bottom">
        <div class="bottom-tabs">
          <div class="bottom-tab active" onclick="showBottomPanel('output')">Output</div>
          <div class="bottom-tab" onclick="showBottomPanel('terminal')">Terminal</div>
        </div>
        <div class="bottom-panel active" id="panel-output">
          <div class="output-console" id="outputConsole"><span class="info">Run your code to see output here.</span></div>
        </div>
        <div class="bottom-panel" id="panel-terminal">
          <div id="terminal-container" style="height:100%;width:100%"></div>
        </div>
      </div>
    </div>

    <!-- AI Panel -->
    <div class="ai-panel" id="aiPanel">
      <div class="ai-panel-header" onclick="toggleAiPanel()">
        <span>AI Assist</span>
        <span class="icon-btn" id="aiPanelToggleIcon">◀</span>
      </div>
      <div class="ai-panel-body" id="aiPanelBody">

        <!-- Agent selector -->
        <div>
          <div class="ai-section-label">Agent</div>
          <select class="agent-select" id="agentSelect">
            <option value="default">default</option>
          </select>
        </div>

        <!-- Quick actions -->
        <div>
          <div class="ai-section-label">Quick actions</div>
          <button class="ai-btn" onclick="aiAction('explain')">💬 Explain selected code</button>
          <button class="ai-btn" onclick="aiAction('fix')" style="margin-top:4px">🔧 Fix last error</button>
          <button class="ai-btn" onclick="aiAction('refactor')" style="margin-top:4px">✨ Refactor selected</button>
        </div>

        <!-- Generate -->
        <div>
          <div class="ai-section-label">Generate code</div>
          <textarea class="ai-input" id="generateInput" placeholder="Describe what you want to build..."></textarea>
          <button class="ai-submit" id="generateBtn" onclick="aiGenerate()">Generate</button>
        </div>

        <!-- AI result -->
        <div id="aiResultSection" style="display:none">
          <div class="ai-section-label">Result</div>
          <div class="ai-result" id="aiResult"></div>
          <button class="ai-apply-btn" id="aiApplyBtn" onclick="applyAiResult()">Apply to editor</button>
        </div>

        <!-- Agent mode -->
        <div>
          <div class="ai-section-label" style="color:var(--mauve)">Agent mode</div>
          <div class="agent-mode-section">
            <div id="agentModeIdle">
              <textarea class="ai-input" id="agentGoalInput" placeholder="Describe what you want to build in plain English..."></textarea>
              <button class="ai-submit" onclick="startAgentMode()" style="background:var(--mauve)">Plan it</button>
            </div>
            <div id="agentModeActive" style="display:none">
              <span class="agent-status" id="agentStatus">planning</span>
              <div id="agentPlanSection" style="display:none">
                <div style="font-size:12px;margin-bottom:8px;color:var(--text)" id="agentPlanSummary"></div>
                <div id="agentStepsList"></div>
                <div style="margin-top:8px">
                  <button class="approve-btn" onclick="approveAgentPlan()">Approve Plan</button>
                  <button class="reject-btn" onclick="rejectAgentPlan()">Revise</button>
                </div>
              </div>
              <div id="agentCodeSection" style="display:none">
                <div style="font-size:12px;color:var(--text);margin-bottom:8px">Code is ready. Review the files then approve to write them to your project.</div>
                <div id="agentFilesList"></div>
                <div style="margin-top:8px">
                  <button class="approve-btn" onclick="approveAgentCode()">Apply to Project</button>
                  <button class="reject-btn" onclick="rejectAgentCode()">Revise</button>
                </div>
              </div>
              <div class="agent-log" id="agentLog"></div>
              <button class="tb-btn" onclick="resetAgentMode()" style="margin-top:8px;width:100%">Reset</button>
            </div>
          </div>
        </div>

      </div>
    </div>

  </div>
</div>

<!-- Project modal -->
<div class="modal-overlay" id="projectModal">
  <div class="modal" style="width:520px">
    <h3>Projects</h3>
    <button class="btn btn-primary" onclick="showNewProjectModal()" style="margin-bottom:12px">+ New Project</button>
    <div class="project-list" id="projectListEl" style="max-height:400px;overflow-y:auto;padding:0"></div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal('projectModal')">Close</button>
    </div>
  </div>
</div>

<!-- New project modal -->
<div class="modal-overlay" id="newProjectModal">
  <div class="modal">
    <h3>New Project</h3>
    <div class="modal-field">
      <label>Project name</label>
      <input id="newProjectName" placeholder="my-project" onkeydown="if(event.key==='Enter')createNewProject()">
    </div>
    <div class="modal-field">
      <label>Type</label>
      <select id="newProjectType">
        <option value="python">Python script</option>
        <option value="flask">Flask API</option>
        <option value="web">Web app (HTML/CSS/JS)</option>
        <option value="node">Node.js</option>
        <option value="wm-plugin">Wickerman Plugin</option>
        <option value="blank">Blank</option>
      </select>
    </div>
    <div class="modal-field">
      <label>Description (optional)</label>
      <input id="newProjectDesc" placeholder="What does this project do?">
    </div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal('newProjectModal')">Cancel</button>
      <button class="btn btn-primary" onclick="createNewProject()">Create</button>
    </div>
  </div>
</div>

<!-- New file modal -->
<div class="modal-overlay" id="newFileModal">
  <div class="modal">
    <h3>New File</h3>
    <div class="modal-field">
      <label>File name</label>
      <input id="newFileName" placeholder="e.g. utils.py" onkeydown="if(event.key==='Enter')createNewFile()">
    </div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal('newFileModal')">Cancel</button>
      <button class="btn btn-primary" onclick="createNewFile()">Create</button>
    </div>
  </div>
</div>

<!-- Install package modal -->
<div class="modal-overlay" id="installModal">
  <div class="modal">
    <h3>Install Package</h3>
    <div class="modal-field">
      <label>Package name</label>
      <input id="installPkgName" placeholder="e.g. requests" onkeydown="if(event.key==='Enter')doInstall()">
    </div>
    <div class="modal-field">
      <label>Package manager</label>
      <select id="installManager">
        <option value="pip">pip (Python)</option>
        <option value="npm">npm (Node.js)</option>
      </select>
    </div>
    <div id="installStatus" style="font-size:12px;color:var(--sub);margin-top:8px"></div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal('installModal')">Close</button>
      <button class="btn btn-primary" id="installBtn" onclick="doInstall()">Install</button>
    </div>
  </div>
</div>

<!-- Refactor instruction modal -->
<div class="modal-overlay" id="refactorModal">
  <div class="modal">
    <h3>Refactor Code</h3>
    <div class="modal-field">
      <label>What should be changed?</label>
      <textarea id="refactorInstruction" class="ai-input" placeholder="e.g. Add type hints, extract helper functions, improve error handling..."></textarea>
    </div>
    <div class="modal-actions">
      <button class="btn btn-muted" onclick="closeModal('refactorModal')">Cancel</button>
      <button class="btn btn-primary" onclick="doRefactor()">Refactor</button>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.js"></script>
<script>
const API = window.location.origin;
let editor = null;
let currentProject = null;
let openTabs = [];  // [{path, language, dirty}]
let activeTab = null;
let aiPanelOpen = true;
let currentMode = 'guided';
let agentSessionId = null;
let agentPollTimer = null;
let lastError = '';
let agentLastLogLen = 0;

// ── Monaco init ──────────────────────────────────────────
require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' } });
require(['vs/editor/editor.main'], function() {
  monaco.editor.defineTheme('wickerman', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#11111b',
      'editor.foreground': '#cdd6f4',
      'editorLineNumber.foreground': '#45475a',
      'editor.selectionBackground': '#313244',
      'editor.lineHighlightBackground': '#181825',
      'editorCursor.foreground': '#f5c2e7',
    }
  });
  editor = monaco.editor.create(document.getElementById('monaco-container'), {
    theme: 'wickerman',
    language: 'python',
    automaticLayout: true,
    minimap: { enabled: true },
    fontSize: 13,
    fontFamily: "'Cascadia Code', 'Fira Code', 'Courier New', monospace",
    wordWrap: 'on',
    scrollBeyondLastLine: false,
    value: '# Open a project to start coding\n',
  });
  editor.onDidChangeModelContent(() => {
    if (activeTab) {
      const t = openTabs.find(t => t.path === activeTab);
      if (t) { t.dirty = true; renderTabs(); }
    }
  });
});

// ── Mode ─────────────────────────────────────────────────
function setMode(mode) {
  currentMode = mode;
  document.getElementById('modeGuided').classList.toggle('active', mode==='guided');
  document.getElementById('modePro').classList.toggle('active', mode==='pro');
}

// ── Projects ─────────────────────────────────────────────
async function loadProjects() {
  const r = await fetch(API+'/api/projects').then(r=>r.json());
  const el = document.getElementById('projectListEl');
  if (!r.projects?.length) {
    el.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:8px">No projects yet. Create one above.</div>';
    return;
  }
  el.innerHTML = r.projects.map(p => `
    <div class="proj-item">
      <div class="pname">${esc(p.name)}</div>
      <div class="pmeta">${esc(p.type)} &mdash; ${p.file_count} files ${p.description ? '&mdash; '+esc(p.description) : ''}</div>
      <div class="proj-actions">
        <button class="proj-btn" onclick="openProject('${esc(p.name)}');closeModal('projectModal')">Open</button>
        <button class="proj-btn danger" onclick="deleteProject('${esc(p.name)}')">Delete</button>
      </div>
    </div>`).join('');
}

async function openProject(name) {
  currentProject = name;
  document.getElementById('currentProjectLabel').innerHTML = `<span style="color:var(--sub)">Project: </span>${esc(name)}`;
  document.getElementById('runBtn').disabled = false;
  openTabs = [];
  activeTab = null;
  renderTabs();
  await loadFileTree();
  loadAgents();
  await _terminalOnProjectOpen(name);
}

async function loadFileTree() {
  if (!currentProject) return;
  const r = await fetch(`${API}/api/projects/${currentProject}/files`).then(r=>r.json());
  const el = document.getElementById('fileTree');
  if (!r.files?.length) {
    el.innerHTML = '<div style="padding:12px;color:var(--sub);font-size:12px">No files yet.</div>';
    return;
  }
  el.innerHTML = renderFileTree(r.files, '');
}

function renderFileTree(items, indent) {
  return items.map(item => {
    const icon = item.type === 'dir' ? '📁' : getFileIcon(item.ext);
    const activeClass = activeTab === item.path ? ' active' : '';
    if (item.type === 'dir') {
      return `<div class="tree-item dir${activeClass}" style="padding-left:${12+indent*12}px" onclick="toggleDir(this,'${esc(item.path)}')">
        <span class="icon">${icon}</span>${esc(item.name)}</div>`;
    }
    return `<div class="tree-item${activeClass}" style="padding-left:${12+indent*12}px" onclick="openFile('${esc(item.path)}')">
      <span class="icon">${icon}</span>${esc(item.name)}</div>`;
  }).join('');
}

function getFileIcon(ext) {
  const icons = {'.py':'🐍','.js':'🟨','.ts':'🔷','.html':'🌐','.css':'🎨','.json':'📋','.md':'📝','.sh':'⚙','.txt':'📄'};
  return icons[ext] || '📄';
}

async function openFile(filepath) {
  if (!currentProject) return;
  // Check if already open
  const existing = openTabs.find(t => t.path === filepath);
  if (existing) { setActiveTab(filepath); return; }
  const r = await fetch(`${API}/api/projects/${currentProject}/file?path=${encodeURIComponent(filepath)}`).then(r=>r.json());
  if (r.error) return;
  const lang = detectLanguage(filepath);
  openTabs.push({ path: filepath, language: lang, dirty: false, content: r.content });
  setActiveTab(filepath);
}

function setActiveTab(path) {
  const tab = openTabs.find(t => t.path === path);
  if (!tab) return;
  activeTab = path;
  if (editor) {
    const model = monaco.editor.createModel(tab.content || '', tab.language);
    editor.setModel(model);
    document.getElementById('langSelect').value = tab.language;
  }
  renderTabs();
  // Update file tree highlight
  document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tree-item').forEach(el => {
    if (el.getAttribute('onclick')?.includes(path)) el.classList.add('active');
  });
}

function renderTabs() {
  const el = document.getElementById('editorTabs');
  el.innerHTML = openTabs.map(t => {
    const name = t.path.split('/').pop();
    const active = t.path === activeTab ? ' active' : '';
    return `<div class="tab${active}" onclick="setActiveTab('${esc(t.path)}')">
      ${esc(name)}${t.dirty?'●':''}
      <span class="close" onclick="event.stopPropagation();closeTab('${esc(t.path)}')">✕</span>
    </div>`;
  }).join('');
}

async function closeTab(path) {
  const tab = openTabs.find(t => t.path === path);
  if (tab?.dirty) {
    if (!confirm(`Save ${path.split('/').pop()} before closing?`)) {
      openTabs = openTabs.filter(t => t.path !== path);
    } else {
      await saveCurrentFile();
      openTabs = openTabs.filter(t => t.path !== path);
    }
  } else {
    openTabs = openTabs.filter(t => t.path !== path);
  }
  if (activeTab === path) {
    activeTab = openTabs.length ? openTabs[openTabs.length-1].path : null;
    if (activeTab) setActiveTab(activeTab);
    else { if(editor) editor.setValue(''); renderTabs(); }
  }
  renderTabs();
}

async function saveCurrentFile() {
  if (!activeTab || !currentProject || !editor) return;
  const content = editor.getValue();
  await fetch(`${API}/api/projects/${currentProject}/file`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({path: activeTab, content})
  });
  const tab = openTabs.find(t => t.path === activeTab);
  if (tab) { tab.dirty = false; tab.content = content; }
  renderTabs();
}

function detectLanguage(filepath) {
  const ext = filepath.split('.').pop().toLowerCase();
  const map = {py:'python',js:'javascript',ts:'typescript',html:'html',css:'css',json:'json',md:'markdown',sh:'shell',bash:'shell',txt:'plaintext'};
  return map[ext] || 'plaintext';
}

function onLangChange() {
  const lang = document.getElementById('langSelect').value;
  if (editor && activeTab) {
    const tab = openTabs.find(t => t.path === activeTab);
    if (tab) tab.language = lang;
    monaco.editor.setModelLanguage(editor.getModel(), lang);
  }
}

// ── Execution ─────────────────────────────────────────────
async function runCurrentFile() {
  if (!currentProject) return;
  await saveCurrentFile();
  const lang = document.getElementById('langSelect').value;
  const console_el = document.getElementById('outputConsole');
  console_el.innerHTML = '<span class="info">Running...</span>';
  showBottomPanel('output');
  const btn = document.getElementById('runBtn');
  btn.disabled = true; btn.textContent = '⏳ Running...';
  try {
    const r = await fetch(`${API}/api/projects/${currentProject}/run`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        language: lang,
        entry_file: activeTab,
        code: editor?.getValue() || '',
        timeout: 30,
      })
    });
    const d = await r.json();
    lastError = d.stderr || '';
    let html = '';
    if (d.stdout) html += `<span class="stdout">${esc(d.stdout)}</span>`;
    if (d.stderr) html += `<span class="stderr">${esc(d.stderr)}</span>`;
    if (!d.stdout && !d.stderr) html = '<span class="info">(no output)</span>';
    if (d.returncode !== 0) html += `\n<span class="info">Exit code: ${d.returncode}</span>`;
    console_el.innerHTML = html;
    console_el.scrollTop = 99999;
  } catch(e) {
    console_el.innerHTML = `<span class="stderr">Error: ${esc(String(e))}</span>`;
  }
  btn.disabled = false; btn.textContent = '▶ Run';
}

// ── File ops ──────────────────────────────────────────────
async function createNewFile() {
  const name = document.getElementById('newFileName').value.trim();
  if (!name || !currentProject) return;
  await fetch(`${API}/api/projects/${currentProject}/file`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({path: name, content: ''})
  });
  closeModal('newFileModal');
  document.getElementById('newFileName').value = '';
  await loadFileTree();
  openFile(name);
}

async function deleteProject(name) {
  if (!confirm(`Delete project "${name}"? This cannot be undone.`)) return;
  await fetch(`${API}/api/projects/${name}`, {method:'DELETE'});
  if (currentProject === name) {
    currentProject = null;
    openTabs = []; activeTab = null; renderTabs();
    document.getElementById('fileTree').innerHTML = '<div style="padding:16px;color:var(--sub);font-size:12px">Open a project to see files.</div>';
    document.getElementById('currentProjectLabel').innerHTML = '<span>No project open</span>';
    document.getElementById('runBtn').disabled = true;
  }
  loadProjects();
}

// ── Packages ──────────────────────────────────────────────
async function doInstall() {
  const pkg = document.getElementById('installPkgName').value.trim();
  const mgr = document.getElementById('installManager').value;
  if (!pkg || !currentProject) return;
  const status = document.getElementById('installStatus');
  const btn = document.getElementById('installBtn');
  btn.disabled = true; status.textContent = `Installing ${pkg}...`; status.style.color='var(--yellow)';
  const r = await fetch(`${API}/api/projects/${currentProject}/install`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({package: pkg, manager: mgr})
  }).then(r=>r.json());
  if (r.ok) {
    status.textContent = `${pkg} installed successfully!`; status.style.color='var(--green)';
    document.getElementById('installPkgName').value = '';
  } else {
    status.textContent = `Failed: ${r.error || r.stderr}`; status.style.color='var(--red)';
  }
  btn.disabled = false;
}

// ── Export ─────────────────────────────────────────────────
async function exportProject() {
  if (!currentProject) return;
  await saveCurrentFile();
  window.location.href = `${API}/api/projects/${currentProject}/export`;
}

// ── AI Assist ─────────────────────────────────────────────
async function loadAgents() {
  try {
    const r = await fetch(API+'/api/agents').then(r=>r.json());
    const sel = document.getElementById('agentSelect');
    const cur = sel.value;
    sel.innerHTML = '<option value="default">default</option>' +
      (r.agents || []).filter(a=>a!=='default').map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join('');
    if (cur && [...sel.options].some(o=>o.value===cur)) sel.value = cur;
  } catch(e) {}
}

async function aiAction(action) {
  const agent = document.getElementById('agentSelect').value;
  const selection = editor?.getModel()?.getValueInRange(editor.getSelection()) || '';
  const code = selection || editor?.getValue() || '';
  if (!code.trim()) return;

  if (action === 'refactor') {
    document.getElementById('refactorModal').classList.add('open');
    return;
  }

  showAiResult('Thinking...');
  let result, err;
  if (action === 'explain') {
    [result, err] = await callAI('/api/ai/explain', {code, agent});
    if (!err) showAiResult(result.explanation, false);
  } else if (action === 'fix') {
    [result, err] = await callAI('/api/ai/fix', {code, error: lastError, agent});
    if (!err) showAiResult(result.code, true);
  }
  if (err) showAiResult(`Error: ${err}`, false);
}

async function aiGenerate() {
  const instruction = document.getElementById('generateInput').value.trim();
  const lang = document.getElementById('langSelect').value;
  const agent = document.getElementById('agentSelect').value;
  const context = editor?.getValue() || '';
  if (!instruction) return;
  const btn = document.getElementById('generateBtn');
  btn.disabled = true; btn.textContent = 'Generating...';
  showAiResult('Generating...');
  const [result, err] = await callAI('/api/ai/generate', {instruction, language: lang, agent, context});
  if (err) showAiResult(`Error: ${err}`, false);
  else showAiResult(result.code, true);
  btn.disabled = false; btn.textContent = 'Generate';
}

async function doRefactor() {
  const instruction = document.getElementById('refactorInstruction').value.trim();
  const agent = document.getElementById('agentSelect').value;
  const selection = editor?.getModel()?.getValueInRange(editor.getSelection()) || '';
  const code = selection || editor?.getValue() || '';
  closeModal('refactorModal');
  document.getElementById('refactorInstruction').value = '';
  showAiResult('Refactoring...');
  const [result, err] = await callAI('/api/ai/refactor', {code, instruction, agent});
  if (err) showAiResult(`Error: ${err}`, false);
  else showAiResult(result.code, true);
}

async function callAI(endpoint, body) {
  try {
    const r = await fetch(API+endpoint, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) return [null, d.error];
    return [d, null];
  } catch(e) {
    return [null, String(e)];
  }
}

function showAiResult(text, isCode = false) {
  const section = document.getElementById('aiResultSection');
  const result = document.getElementById('aiResult');
  const applyBtn = document.getElementById('aiApplyBtn');
  section.style.display = 'block';
  result.textContent = text;
  applyBtn.style.display = isCode ? 'block' : 'none';
  applyBtn.dataset.code = isCode ? text : '';
}

function applyAiResult() {
  const code = document.getElementById('aiApplyBtn').dataset.code;
  if (!code || !editor) return;
  const selection = editor.getSelection();
  if (selection && !selection.isEmpty()) {
    editor.executeEdits('ai', [{range: selection, text: code}]);
  } else {
    editor.setValue(code);
  }
  if (activeTab) {
    const tab = openTabs.find(t=>t.path===activeTab);
    if (tab) tab.dirty = true;
  }
  renderTabs();
}

// ── Agent mode ────────────────────────────────────────────
async function startAgentMode() {
  const goal = document.getElementById('agentGoalInput').value.trim();
  const agent = document.getElementById('agentSelect').value;
  if (!goal || !currentProject) {
    alert('Open a project and describe your goal first.'); return;
  }
  document.getElementById('agentModeIdle').style.display = 'none';
  document.getElementById('agentModeActive').style.display = 'block';
  document.getElementById('agentPlanSection').style.display = 'none';
  document.getElementById('agentCodeSection').style.display = 'none';
  agentLastLogLen = 0;
  const r = await fetch(API+'/api/agent/start', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({project: currentProject, goal, agent})
  }).then(r=>r.json());
  if (r.error) { alert(r.error); return; }
  agentSessionId = r.session_id;
  if (agentPollTimer) clearInterval(agentPollTimer);
  agentPollTimer = setInterval(pollAgentStatus, 1500);
}

async function pollAgentStatus() {
  if (!agentSessionId) return;
  try {
    const s = await fetch(`${API}/api/agent/${agentSessionId}/status`).then(r=>r.json());
    if (s.error) return;
    // Update status badge
    const statusEl = document.getElementById('agentStatus');
    statusEl.textContent = s.status.replace(/_/g, ' ');
    statusEl.className = 'agent-status status-' + (
      s.status.includes('awaiting') ? 'awaiting' :
      s.status === 'coding' ? 'coding' :
      s.status === 'done' ? 'done' :
      s.status === 'error' ? 'error' : 'planning'
    );
    // Update log
    const logEl = document.getElementById('agentLog');
    if (s.log?.length > agentLastLogLen) {
      const newLines = s.log.slice(agentLastLogLen);
      logEl.textContent += (logEl.textContent ? '\n' : '') + newLines.join('\n');
      logEl.scrollTop = 99999;
      agentLastLogLen = s.log.length;
    }
    // Show plan approval UI
    if (s.status === 'awaiting_plan_approval' && s.plan) {
      document.getElementById('agentPlanSection').style.display = 'block';
      document.getElementById('agentCodeSection').style.display = 'none';
      document.getElementById('agentPlanSummary').textContent = s.plan.summary || '';
      document.getElementById('agentStepsList').innerHTML = (s.plan.steps || []).map((step, i) =>
        `<div class="plan-step">
          <div class="step-title">${i+1}. ${esc(step.title)}</div>
          <div class="step-desc">${esc(step.description)}</div>
        </div>`
      ).join('');
    }
    // Show code approval UI
    if (s.status === 'awaiting_code_approval' && s.generated_files?.length) {
      document.getElementById('agentPlanSection').style.display = 'none';
      document.getElementById('agentCodeSection').style.display = 'block';
      document.getElementById('agentFilesList').innerHTML = s.generated_files.map(f =>
        `<div class="plan-step">
          <div class="step-title">📄 ${esc(f)}</div>
          <div class="step-desc"><a href="#" onclick="previewAgentFile('${esc(f)}');return false" style="color:var(--blue)">Preview</a></div>
        </div>`
      ).join('');
    }
    // Done
    if (s.status === 'done' || s.status === 'error') {
      clearInterval(agentPollTimer);
      agentPollTimer = null;
      if (s.status === 'done') {
        await loadFileTree();
        document.getElementById('agentPlanSection').style.display = 'none';
        document.getElementById('agentCodeSection').style.display = 'none';
      }
    }
  } catch(e) {}
}

async function previewAgentFile(filepath) {
  if (!agentSessionId) return;
  const r = await fetch(`${API}/api/agent/${agentSessionId}/preview_file?file=${encodeURIComponent(filepath)}`).then(r=>r.json());
  if (r.content !== undefined) {
    showAiResult(r.content, false);
  }
}

async function approveAgentPlan() {
  if (!agentSessionId) return;
  await fetch(`${API}/api/agent/${agentSessionId}/approve_plan`, {method:'POST'});
  document.getElementById('agentPlanSection').style.display = 'none';
}

async function rejectAgentPlan() {
  const feedback = prompt('What should be changed about the plan?');
  if (feedback === null) return;
  await fetch(`${API}/api/agent/${agentSessionId}/reject_plan`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({feedback})
  });
  document.getElementById('agentPlanSection').style.display = 'none';
  agentLastLogLen = 0;
}

async function approveAgentCode() {
  if (!agentSessionId) return;
  await fetch(`${API}/api/agent/${agentSessionId}/approve_code`, {method:'POST'});
  document.getElementById('agentCodeSection').style.display = 'none';
}

async function rejectAgentCode() {
  const feedback = prompt('What should be changed about the code?');
  if (feedback === null) return;
  await fetch(`${API}/api/agent/${agentSessionId}/reject_code`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({feedback})
  });
  document.getElementById('agentCodeSection').style.display = 'none';
  agentLastLogLen = 0;
}

function resetAgentMode() {
  if (agentPollTimer) clearInterval(agentPollTimer);
  agentSessionId = null; agentLastLogLen = 0;
  document.getElementById('agentModeIdle').style.display = 'block';
  document.getElementById('agentModeActive').style.display = 'none';
  document.getElementById('agentGoalInput').value = '';
  document.getElementById('agentLog').textContent = '';
}

// ── UI helpers ─────────────────────────────────────────────
function showBottomPanel(name) {
  document.querySelectorAll('.bottom-panel').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('.bottom-tab').forEach(el=>el.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  document.querySelectorAll('.bottom-tab').forEach(el=>{
    if(el.textContent.toLowerCase()===name) el.classList.add('active');
  });
}

function toggleAiPanel() {
  aiPanelOpen = !aiPanelOpen;
  document.getElementById('aiPanel').classList.toggle('collapsed', !aiPanelOpen);
  document.getElementById('aiPanelToggleIcon').textContent = aiPanelOpen ? '▶' : '◀';
}

function showProjectModal() { loadProjects(); document.getElementById('projectModal').classList.add('open'); }
function showNewProjectModal() { document.getElementById('newProjectModal').classList.add('open'); }
function showNewFileModal() { if(!currentProject){alert('Open a project first.');return;} document.getElementById('newFileModal').classList.add('open'); }
function openInstallModal() { if(!currentProject){alert('Open a project first.');return;} document.getElementById('installStatus').textContent=''; document.getElementById('installModal').classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

async function createNewProject() {
  const name = document.getElementById('newProjectName').value.trim();
  const type = document.getElementById('newProjectType').value;
  const desc = document.getElementById('newProjectDesc').value.trim();
  if (!name) return;
  const r = await fetch(API+'/api/projects/create', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name, type, description: desc})
  }).then(r=>r.json());
  if (r.ok) {
    closeModal('newProjectModal');
    closeModal('projectModal');
    document.getElementById('newProjectName').value='';
    document.getElementById('newProjectDesc').value='';
    await openProject(name);
  } else {
    alert(r.error || 'Failed to create project');
  }
}

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  if ((e.ctrlKey||e.metaKey) && e.key==='s') { e.preventDefault(); saveCurrentFile(); }
  if ((e.ctrlKey||e.metaKey) && e.key==='Enter') { e.preventDefault(); runCurrentFile(); }
  if (e.key==='Escape') { document.querySelectorAll('.modal-overlay.open').forEach(el=>el.classList.remove('open')); }
});

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

// Init
loadAgents();
setInterval(loadAgents, 30000);

// ── Terminal ──────────────────────────────────────────────
let term = null;
let termWs = null;
let termProject = null;

function initTerminal(project) {
  if (!project) return;
  if (termProject === project && term && termWs && termWs.readyState === WebSocket.OPEN) return;

  // Clean up existing
  if (termWs) { try { termWs.close(); } catch(e){} }
  if (term) { term.dispose(); term = null; }

  termProject = project;
  const container = document.getElementById('terminal-container');
  container.innerHTML = '';

  term = new Terminal({
    theme: {
      background: '#11111b',
      foreground: '#cdd6f4',
      cursor: '#f5c2e7',
      selectionBackground: '#313244',
      black: '#45475a', red: '#f38ba8', green: '#a6e3a1',
      yellow: '#f9e2af', blue: '#89b4fa', magenta: '#cba6f7',
      cyan: '#94e2d5', white: '#bac2de',
    },
    fontFamily: "'Cascadia Code', 'Fira Code', 'Courier New', monospace",
    fontSize: 13,
    cursorBlink: true,
    convertEol: true,
  });
  term.open(container);
  term.resize(80, 24);

  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = proto + '//' + window.location.host + '/api/projects/' + encodeURIComponent(project) + '/terminal';
  termWs = new WebSocket(wsUrl);
  termWs.binaryType = 'arraybuffer';

  termWs.onopen = () => {
    term.writeln('\x1b[32mConnected to terminal\x1b[0m');
    // Send initial resize
    termWs.send(JSON.stringify({type:'resize', cols: term.cols, rows: term.rows}));
  };
  termWs.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      term.write(new Uint8Array(e.data));
    } else {
      term.write(e.data);
    }
  };
  termWs.onclose = () => {
    if (term) term.writeln('\r\n\x1b[33mTerminal disconnected.\x1b[0m');
  };
  termWs.onerror = () => {
    if (term) term.writeln('\r\n\x1b[31mTerminal connection error.\x1b[0m');
  };

  term.onData(data => {
    if (termWs && termWs.readyState === WebSocket.OPEN) {
      termWs.send(data);
    }
  });

  // Handle resize
  const resizeObs = new ResizeObserver(() => {
    if (term && termWs && termWs.readyState === WebSocket.OPEN) {
      const cols = Math.floor(container.clientWidth / 8);
      const rows = Math.floor(container.clientHeight / 17);
      if (cols > 10 && rows > 2) {
        term.resize(cols, rows);
        termWs.send(JSON.stringify({type:'resize', cols, rows}));
      }
    }
  });
  resizeObs.observe(container);
}

// Override showBottomPanel to init terminal on first open
const _origShowBottomPanel = showBottomPanel;
function showBottomPanel(name) {
  _origShowBottomPanel(name);
  if (name === 'terminal' && currentProject) {
    initTerminal(currentProject);
  }
}

// Re-init terminal when project changes
// Terminal cleanup on project switch — patched into the existing openProject flow
// (function declaration override removed to avoid JS hoisting conflict)
async function _terminalOnProjectOpen(name) {
  if (termWs) { try { termWs.close(); } catch(e){} termWs = null; }
  if (term) { term.dispose(); term = null; }
  termProject = null;
}
</script>
</body>
</html>
""",

}  # end WM_FORGE_FILES

WM_FORGE["files"] = WM_FORGE_FILES

PLUGIN_HOST = ("127.0.0.1", "forge.wickerman.local")
