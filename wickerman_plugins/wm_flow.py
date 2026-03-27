"""
Wickerman OS v5.7.0 - wm-flow Native Flow Editor
Replaces Flowise with a Litegraph.js-based visual pipeline builder
tightly integrated with the Wickerman ecosystem.
"""

WM_FLOW = {
    "name": "Flow Editor",
    "description": "Native visual pipeline builder — chain agents, tools, and logic into reusable flows",
    "icon": "account_tree",
    "build": True,
    "build_context": "data",
    "container_name": "wm-flow",
    "url": "http://flow.wickerman.local",
    "ports": [5000],
    "gpu": False,
    "env": [
        "LLAMA_API=http://wm-llama:8080",
        "FLOWS_DIR=/flows",
        "WORKSPACE_DIR=/workspace",
        "DATA_DIR=/data",
    ],
    "volumes": [
        "{support}/flows:/flows",
        "{support}/workspace:/workspace",
        "{support}/plugins:/manifests",
        "{self}/data:/data",
    ],
    "nginx_host": "flow.wickerman.local",
    "help": (
        "## Flow Editor\n"
        "Visual pipeline builder for local AI.\n\n"
        "**Canvas:** Drag nodes onto the canvas, connect them with edges, hit Execute.\n\n"
        "**Node types:** Input, Agent, Prompt, Branch, Compare, Think, Combine, "
        "Retry, Workspace, Plugin (auto-discovered), Subflow, Output.\n\n"
        "**Subflows:** Select nodes, right-click, Create Subflow. "
        "Double-click any subflow to edit its internals with breadcrumb navigation.\n\n"
        "**Chat integration:** Add a Chat Input node with a JSON schema to make any "
        "flow triggerable from the Chat UI via tool calling.\n\n"
        "**Flows:** Saved as JSON in ~/WickermanSupport/flows/. "
        "Drop any .json flow file in there and it appears in the library."
    ),
}

WM_FLOW_FILES = {

"data/Dockerfile": r"""FROM python:3.11-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.* gunicorn==22.* requests==2.32.*
RUN useradd -m -s /bin/bash -u 1001 flowuser
WORKDIR /app
ARG CACHEBUST=1
COPY . .
RUN mkdir -p /flows /flows/subflows /flows/runs /workspace /data && \
    chown -R flowuser:flowuser /app /flows /workspace /data && \
    chmod -R 777 /flows /workspace /data
USER flowuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", \
     "--threads", "8", "--timeout", "300", "app:app"]
""",

"data/app.py": r"""#!/usr/bin/env python3
# Wickerman Flow Editor - Backend
import os, json, re, time, threading, hashlib, glob, uuid
from collections import defaultdict, deque
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import requests as req_lib

app = Flask(__name__)

LLAMA_API    = os.environ.get("LLAMA_API",    "http://wm-llama:8080")
FLOWS_DIR    = os.environ.get("FLOWS_DIR",    "/flows")
WORKSPACE    = os.environ.get("WORKSPACE_DIR", "/workspace")
DATA_DIR     = os.environ.get("DATA_DIR",     "/data")
SUBFLOWS_DIR = os.path.join(FLOWS_DIR, "subflows")
RUNS_DIR     = os.path.join(FLOWS_DIR, "runs")

def _safe_makedirs(path):
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o777)
    except: pass

for d in [FLOWS_DIR, SUBFLOWS_DIR, RUNS_DIR, WORKSPACE, DATA_DIR]:
    _safe_makedirs(d)

# ── Wickerman Docker network plugin discovery ──────────────
# Default known containers — extended by manifest scan and manual registration
_DEFAULT_CONTAINERS = [
    ("wm-forge",   5000),
    ("wm-probe",   5000),
    ("wm-trainer", 5000),
]
_discovered_plugins = {}
_discovery_lock     = threading.Lock()
_MANIFESTS_DIR      = os.environ.get("MANIFESTS_DIR", "/manifests")

def _scan_manifest_hosts():
    # Scan WickermanSupport/plugins/ manifests for container_name entries.
    hosts = list(_DEFAULT_CONTAINERS)
    try:
        for path in glob.glob(os.path.join(_MANIFESTS_DIR, "*.json")):
            try:
                with open(path) as f:
                    manifest = json.load(f)
                cname = manifest.get("container_name", "")
                port  = manifest.get("ports", [5000])[0]
                if cname and cname != "wm-flow" and (cname, port) not in hosts:
                    hosts.append((cname, port))
            except: pass
    except: pass
    return hosts

def discover_plugins(extra_hosts=None):
    # Scan all known + manifest-discovered + manually added plugin hosts.
    candidates = _scan_manifest_hosts()
    if extra_hosts:
        for h in extra_hosts:
            if h not in candidates:
                candidates.append(h)
    found = {}
    for host, port in candidates:
        try:
            r = req_lib.get(f"http://{host}:{port}/node/schema", timeout=3)
            if r.status_code == 200:
                schema = r.json()
                schema["_host"] = host
                schema["_port"] = port
                schema["_url"]  = f"http://{host}:{port}"
                found[host] = schema
        except: pass
    with _discovery_lock:
        _discovered_plugins.clear()
        _discovered_plugins.update(found)
    return found

threading.Thread(target=discover_plugins, daemon=True).start()

# ── Agent / Router helpers ─────────────────────────────────
def get_agents():
    try:
        r = req_lib.get(f"{LLAMA_API}/api/status", timeout=5)
        data = r.json()
        return [a for a, s in data.get("slots", {}).items() if s.get("status") == "ready"]
    except:
        return []

def call_agent(agent, messages, max_tokens=1024, temperature=0.3, stream=False):
    payload = {
        "model": agent,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    try:
        r = req_lib.post(
            f"{LLAMA_API}/v1/chat/completions",
            json=payload, timeout=120, stream=stream
        )
        r.raise_for_status()
        if stream:
            return r
        return r.json()["choices"][0]["message"]["content"], None
    except Exception as e:
        return None, str(e)

# ── Flow storage ───────────────────────────────────────────
def list_flows():
    flows = []
    for path in sorted(glob.glob(os.path.join(FLOWS_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            flows.append({
                "id": data.get("id", os.path.splitext(os.path.basename(path))[0]),
                "name": data.get("name", "Unnamed"),
                "description": data.get("description", ""),
                "chat_enabled": data.get("chat_enabled", False),
                "node_count": len(data.get("nodes", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except: pass
    return flows

def load_flow(flow_id):
    path = os.path.join(FLOWS_DIR, f"{flow_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)

def save_flow(flow_data):
    flow_id = flow_data.get("id") or re.sub(r'[^a-z0-9_]', '_', flow_data.get("name", "flow").lower())
    flow_data["id"] = flow_id
    flow_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = os.path.join(FLOWS_DIR, f"{flow_id}.json")
    with open(path, "w") as f:
        json.dump(flow_data, f, indent=2)
    return flow_id

def list_subflows():
    subflows = []
    for path in sorted(glob.glob(os.path.join(SUBFLOWS_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            subflows.append({
                "id": data.get("id"),
                "name": data.get("name", "Unnamed"),
                "description": data.get("description", ""),
                "inputs": data.get("inputs", []),
                "outputs": data.get("outputs", []),
            })
        except: pass
    return subflows

def load_subflow(subflow_id):
    path = os.path.join(SUBFLOWS_DIR, f"{subflow_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)

def save_subflow(subflow_data):
    sf_id = subflow_data.get("id") or re.sub(r'[^a-z0-9_]', '_', subflow_data.get("name", "subflow").lower())
    subflow_data["id"] = sf_id
    subflow_data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = os.path.join(SUBFLOWS_DIR, f"{sf_id}.json")
    with open(path, "w") as f:
        json.dump(subflow_data, f, indent=2)
    return sf_id

# ── Execution engine ───────────────────────────────────────
_active_runs = {}
_runs_lock   = threading.Lock()

def save_run(run_id, data):
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def topological_sort(nodes, edges):
    graph  = {n["id"]: [] for n in nodes}
    in_deg = {n["id"]: 0  for n in nodes}
    for e in edges:
        src, tgt = e["source"], e["target"]
        if src in graph and tgt in graph:
            graph[src].append(tgt)
            in_deg[tgt] += 1
    queue  = deque([n["id"] for n in nodes if in_deg[n["id"]] == 0])
    result = []
    while queue:
        nid = queue.popleft()
        result.append(nid)
        for child in graph[nid]:
            in_deg[child] -= 1
            if in_deg[child] == 0:
                queue.append(child)
    if len(result) != len(nodes):
        raise ValueError("Flow contains a cycle — only DAGs are supported.")
    return result

def get_node_inputs(node_id, edges, node_outputs):
    inputs = {}
    for e in edges:
        if e["target"] == node_id:
            src_val = node_outputs.get(e["source"], {})
            port    = e.get("targetPort", "text")
            src_port = e.get("sourcePort", "text")
            inputs[port] = src_val.get(src_port, src_val.get("text", ""))
    return inputs

def execute_node(node, inputs, run_id, emit):
    ntype  = node.get("type", "")
    config = node.get("config", {})
    nid    = node["id"]

    emit(run_id, "node_started", {"node_id": nid, "type": ntype})

    try:
        if ntype == "input":
            text = config.get("value", inputs.get("text", ""))
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": text}})
            return {"text": text}

        elif ntype == "chat_input":
            text = inputs.get("text", config.get("value", ""))
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": text}})
            return {"text": text}

        elif ntype == "subflow_input":
            text = inputs.get("text", config.get("value", ""))
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": text}})
            return {"text": text}

        elif ntype == "subflow_output":
            text = inputs.get("text", "")
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": text}})
            return {"text": text}

        elif ntype == "agent":
            agent  = config.get("agent", "default")
            system = config.get("system_prompt", "You are a helpful assistant.")
            text   = inputs.get("text", "")
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": text},
            ]
            reply, err = call_agent(agent, messages,
                max_tokens=int(config.get("max_tokens", 1024)),
                temperature=float(config.get("temperature", 0.3)))
            if err:
                raise RuntimeError(f"Agent call failed: {err}")
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": reply}})
            return {"text": reply}

        elif ntype == "prompt":
            template = config.get("template", "{{text}}")
            rendered = template
            for k, v in inputs.items():
                rendered = rendered.replace("{{" + k + "}}", str(v))
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": rendered}})
            return {"text": rendered}

        elif ntype == "think":
            agent   = config.get("agent", "default")
            text    = inputs.get("text", "")
            steps   = int(config.get("steps", 3))
            system  = (
                f"Think through this step by step. Use exactly {steps} numbered reasoning steps "
                "before giving your final answer. Format: Step 1: ... Step 2: ... Final Answer: ..."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": text},
            ]
            reply, err = call_agent(agent, messages, max_tokens=2048)
            if err:
                raise RuntimeError(f"Think node failed: {err}")
            # Extract final answer
            final = reply
            if "Final Answer:" in reply:
                final = reply.split("Final Answer:")[-1].strip()
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {
                "full_response": reply, "answer_only": final
            }})
            return {"full_response": reply, "answer_only": final}

        elif ntype == "compare":
            agent_a = config.get("agent_a", "default")
            agent_b = config.get("agent_b", "default")
            text    = inputs.get("text", "")
            system  = "You are a helpful assistant."
            msgs    = [{"role": "system", "content": system}, {"role": "user", "content": text}]
            results = [None, None]
            errors  = [None, None]
            def _call(idx, agent):
                results[idx], errors[idx] = call_agent(agent, msgs, max_tokens=1024)
            t_a = threading.Thread(target=_call, args=(0, agent_a))
            t_b = threading.Thread(target=_call, args=(1, agent_b))
            t_a.start(); t_b.start()
            t_a.join();  t_b.join()
            if errors[0]: raise RuntimeError(f"Agent A failed: {errors[0]}")
            if errors[1]: raise RuntimeError(f"Agent B failed: {errors[1]}")
            combined = f"[Agent A: {agent_a}]\n{results[0]}\n\n[Agent B: {agent_b}]\n{results[1]}"
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {
                "text_a": results[0], "text_b": results[1], "combined": combined
            }})
            return {"text_a": results[0], "text_b": results[1], "combined": combined}

        elif ntype == "branch":
            text      = inputs.get("text", "")
            bool_in   = inputs.get("bool", None)
            cond_type = config.get("condition_type", "keyword")
            cond_val  = config.get("condition_value", "")
            passed    = False
            if bool_in is not None:
                passed = bool(bool_in)
            elif cond_type == "keyword":
                passed = cond_val.lower() in text.lower()
            elif cond_type == "regex":
                passed = bool(re.search(cond_val, text))
            elif cond_type == "length_gt":
                passed = len(text) > int(cond_val or 0)
            true_out  = text if passed else ""
            false_out = "" if passed else text
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {
                "true": true_out, "false": false_out, "passed": passed
            }})
            return {"true": true_out, "false": false_out, "passed": passed}

        elif ntype == "combine":
            mode   = config.get("mode", "join")
            sep    = config.get("separator", "\n\n")
            parts  = [v for k, v in sorted(inputs.items()) if v]
            if mode == "join":
                combined = sep.join(str(p) for p in parts)
            elif mode == "template":
                tmpl = config.get("template", "")
                combined = tmpl
                for i, p in enumerate(parts):
                    combined = combined.replace(f"{{{{input_{i}}}}}", str(p))
            else:
                combined = sep.join(str(p) for p in parts)
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": combined}})
            return {"text": combined}

        elif ntype == "retry":
            agent      = config.get("agent", "default")
            max_tries  = int(config.get("max_retries", 3))
            cond_type  = config.get("condition_type", "keyword")
            cond_val   = config.get("condition_value", "")
            prompt     = inputs.get("text", "")
            system     = config.get("system_prompt", "You are a helpful assistant.")
            last_reply = ""
            passed     = False
            for attempt in range(1, max_tries + 1):
                emit(run_id, "node_output", {"node_id": nid, "text": f"Attempt {attempt}/{max_tries}..."})
                msgs   = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
                reply, err = call_agent(agent, msgs, max_tokens=2048)
                if err:
                    last_reply = f"Error: {err}"
                    continue
                last_reply = reply
                if cond_type == "keyword":
                    passed = cond_val.lower() in reply.lower()
                elif cond_type == "regex":
                    passed = bool(re.search(cond_val, reply))
                elif cond_type == "no_error":
                    passed = "error" not in reply.lower() and "exception" not in reply.lower()
                if passed:
                    break
                # Build error feedback for next attempt
                prompt = (f"Your previous response had an issue. Try again.\n\n"
                         f"Original request: {inputs.get('text', '')}\n\n"
                         f"Previous response: {reply}\n\n"
                         f"Please fix and try again.")
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {
                "text": last_reply, "passed": passed
            }})
            return {"text": last_reply, "passed": passed}

        elif ntype == "workspace":
            mode     = config.get("mode", "read")
            filename = config.get("filename", "data.json")
            filepath = os.path.join(WORKSPACE, filename)
            if mode == "read":
                if os.path.isfile(filepath):
                    with open(filepath) as f:
                        content = f.read()
                else:
                    content = "{}"
                emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": content}})
                return {"text": content}
            elif mode in ("write", "append"):
                new_data = inputs.get("text", "")
                if mode == "append" and os.path.isfile(filepath):
                    try:
                        with open(filepath) as f:
                            existing = json.load(f)
                        try:
                            new_parsed = json.loads(new_data)
                            if isinstance(existing, list):
                                existing.append(new_parsed)
                            elif isinstance(existing, dict):
                                existing.update(new_parsed)
                        except:
                            existing = [existing, new_data]
                        with open(filepath, "w") as f:
                            json.dump(existing, f, indent=2)
                    except:
                        with open(filepath, "w") as f:
                            f.write(new_data)
                else:
                    with open(filepath, "w") as f:
                        f.write(new_data)
                emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": f"Saved to {filename}"}})
                return {"text": f"Saved to {filename}"}

        elif ntype == "plugin":
            plugin_host = config.get("plugin_host", "")
            with _discovery_lock:
                plugin = _discovered_plugins.get(plugin_host, {})
            if not plugin:
                raise RuntimeError(f"Plugin {plugin_host} not discovered")
            url     = plugin.get("_url", "")
            payload = dict(inputs)
            payload.update(config.get("params", {}))
            r = req_lib.post(f"{url}/node/execute", json=payload, timeout=120)
            r.raise_for_status()
            result = r.json()
            emit(run_id, "node_complete", {"node_id": nid, "outputs": result})
            return result

        elif ntype == "subflow":
            sf_id = config.get("subflow_id", "")
            sf    = load_subflow(sf_id)
            if not sf:
                raise RuntimeError(f"Subflow not found: {sf_id}")
            emit(run_id, "subflow_entered", {"node_id": nid, "subflow_id": sf_id})
            # Map outer inputs to inner subflow_input nodes
            inner_inputs = {}
            for inner_node in sf.get("nodes", []):
                if inner_node.get("type") == "subflow_input":
                    port_name = inner_node.get("config", {}).get("port_name", "text")
                    inner_node["config"]["value"] = inputs.get(port_name, "")
            # Execute inner graph
            inner_outputs = _execute_graph(sf.get("nodes", []), sf.get("edges", []), run_id, emit)
            # Collect outputs from subflow_output nodes
            result = {}
            for inner_node in sf.get("nodes", []):
                if inner_node.get("type") == "subflow_output":
                    port_name = inner_node.get("config", {}).get("port_name", "text")
                    nout = inner_outputs.get(inner_node["id"], {})
                    result[port_name] = nout.get("text", "")
            emit(run_id, "subflow_exited", {"node_id": nid, "subflow_id": sf_id})
            emit(run_id, "node_complete", {"node_id": nid, "outputs": result})
            return result

        elif ntype == "output":
            text    = inputs.get("text", inputs.get("file_path", str(inputs)))
            mode    = config.get("mode", "display")
            if mode == "file_save":
                filename = config.get("filename", f"output_{run_id[:8]}.txt")
                filepath = os.path.join(WORKSPACE, filename)
                with open(filepath, "w") as f:
                    f.write(str(text))
                emit(run_id, "node_complete", {"node_id": nid, "outputs": {
                    "text": text, "saved_to": filename
                }})
                return {"text": text, "saved_to": filename}
            emit(run_id, "node_complete", {"node_id": nid, "outputs": {"text": text}})
            return {"text": text}

        else:
            raise RuntimeError(f"Unknown node type: {ntype}")

    except Exception as e:
        import traceback
        emit(run_id, "node_error", {"node_id": nid, "error": str(e), "traceback": traceback.format_exc()})
        raise

def _execute_graph(nodes, edges, run_id, emit):
    try:
        order = topological_sort(nodes, edges)
    except ValueError as e:
        emit(run_id, "flow_error", {"error": str(e)})
        return {}

    node_map     = {n["id"]: n for n in nodes}
    node_outputs = {}
    cancelled    = lambda: _active_runs.get(run_id, {}).get("cancelled", False)

    for nid in order:
        if cancelled():
            emit(run_id, "flow_cancelled", {})
            break
        node   = node_map[nid]
        inputs = get_node_inputs(nid, edges, node_outputs)
        # Skip output nodes that have no inputs (inactive branch)
        if not inputs and node.get("type") not in ("input", "chat_input", "subflow_input"):
            has_incoming = any(e["target"] == nid for e in edges)
            if has_incoming:
                node_outputs[nid] = {}
                continue
        try:
            result = execute_node(node, inputs, run_id, emit)
            node_outputs[nid] = result or {}
        except Exception:
            node_outputs[nid] = {"error": True}
            break

    return node_outputs

def _run_flow(run_id, flow):
    run = _active_runs[run_id]
    run["status"]   = "running"
    run["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    events = run["events"]
    def emit(rid, event_type, data):
        events.append({"type": event_type, "data": data, "ts": time.time()})

    emit(run_id, "flow_started", {"flow_id": flow.get("id"), "name": flow.get("name")})
    try:
        outputs = _execute_graph(flow.get("nodes", []), flow.get("edges", []), run_id, emit)
        # Collect final output
        final_text = ""
        for node in flow.get("nodes", []):
            if node.get("type") == "output":
                out = outputs.get(node["id"], {})
                final_text = out.get("text", "")
                break
        run["final_output"] = final_text
        run["status"]       = "complete"
        emit(run_id, "flow_complete", {"final_output": final_text})
    except Exception as e:
        run["status"] = "error"
        run["error"]  = str(e)
        emit(run_id, "flow_error", {"error": str(e)})

    save_run(run_id, {
        "id": run_id,
        "flow_id": flow.get("id"),
        "status": run["status"],
        "started_at": run.get("started_at"),
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "final_output": run.get("final_output", ""),
        "error": run.get("error"),
    })

# ══════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# ── Agents ─────────────────────────────────────────────────
@app.route("/api/agents")
def api_agents():
    return jsonify({"agents": get_agents()})

# ── Plugins ────────────────────────────────────────────────
@app.route("/api/plugins")
def api_plugins():
    threading.Thread(target=discover_plugins, daemon=True).start()
    with _discovery_lock:
        return jsonify({"plugins": list(_discovered_plugins.values())})

@app.route("/api/plugins/register", methods=["POST"])
def api_register_plugin():
    # Manually register a plugin by hostname. User can add custom plugins.
    d = request.json or {}
    host = d.get("host", "").strip()
    port = int(d.get("port", 5000))
    if not host:
        return jsonify({"error": "host required"}), 400
    # Try to reach it
    try:
        r = req_lib.get(f"http://{host}:{port}/node/schema", timeout=5)
        if r.status_code != 200:
            return jsonify({"error": f"Could not reach /node/schema at {host}:{port}"}), 400
        schema = r.json()
        schema["_host"] = host
        schema["_port"] = port
        schema["_url"]  = f"http://{host}:{port}"
        with _discovery_lock:
            _discovered_plugins[host] = schema
        return jsonify({"ok": True, "plugin": schema})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/plugins/<host>", methods=["DELETE"])
def api_remove_plugin(host):
    with _discovery_lock:
        _discovered_plugins.pop(host, None)
    return jsonify({"ok": True})

# ── Flows ──────────────────────────────────────────────────
@app.route("/api/flows")
def api_list_flows():
    return jsonify({"flows": list_flows()})

@app.route("/api/flows/<flow_id>")
def api_get_flow(flow_id):
    flow = load_flow(flow_id)
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify(flow)

@app.route("/api/flows", methods=["POST"])
def api_save_flow():
    data = request.json or {}
    if not data.get("name"):
        return jsonify({"error": "name required"}), 400
    flow_id = save_flow(data)
    return jsonify({"ok": True, "flow_id": flow_id})

@app.route("/api/flows/<flow_id>", methods=["DELETE"])
def api_delete_flow(flow_id):
    path = os.path.join(FLOWS_DIR, f"{flow_id}.json")
    if os.path.isfile(path):
        os.remove(path)
    return jsonify({"ok": True})

# ── Subflows ───────────────────────────────────────────────
@app.route("/api/subflows")
def api_list_subflows():
    return jsonify({"subflows": list_subflows()})

@app.route("/api/subflows/<sf_id>")
def api_get_subflow(sf_id):
    sf = load_subflow(sf_id)
    if not sf:
        return jsonify({"error": "Subflow not found"}), 404
    return jsonify(sf)

@app.route("/api/subflows", methods=["POST"])
def api_save_subflow():
    data = request.json or {}
    if not data.get("name"):
        return jsonify({"error": "name required"}), 400
    sf_id = save_subflow(data)
    return jsonify({"ok": True, "subflow_id": sf_id})

@app.route("/api/subflows/<sf_id>", methods=["DELETE"])
def api_delete_subflow(sf_id):
    path = os.path.join(SUBFLOWS_DIR, f"{sf_id}.json")
    if os.path.isfile(path):
        os.remove(path)
    return jsonify({"ok": True})

# ── Execution ──────────────────────────────────────────────
@app.route("/api/flows/<flow_id>/execute", methods=["POST"])
def api_execute_flow(flow_id):
    flow = load_flow(flow_id)
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    d = request.json or {}
    # Inject chat input if provided
    if d.get("chat_input"):
        for node in flow.get("nodes", []):
            if node.get("type") in ("chat_input", "input"):
                node.setdefault("config", {})["value"] = d["chat_input"]
                break
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    run = {
        "id": run_id, "flow_id": flow_id, "status": "starting",
        "events": [], "cancelled": False,
        "final_output": "", "error": None,
    }
    with _runs_lock:
        _active_runs[run_id] = run
    threading.Thread(target=_run_flow, args=(run_id, flow), daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id})

@app.route("/api/runs/<run_id>/stream")
def api_stream_run(run_id):
    def generate():
        sent = 0
        while True:
            run = _active_runs.get(run_id)
            if not run:
                run = {}
                try:
                    path = os.path.join(RUNS_DIR, f"{run_id}.json")
                    if os.path.isfile(path):
                        with open(path) as f:
                            run = json.load(f)
                except: pass
                if not run:
                    yield "data: {\"type\": \"error\", \"data\": {\"error\": \"Run not found\"}}\n\n"
                    return
            events = run.get("events", [])
            while sent < len(events):
                evt = events[sent]
                yield f"data: {json.dumps(evt)}\n\n"
                sent += 1
            status = run.get("status", "")
            if status in ("complete", "error", "cancelled"):
                yield f"data: {{\"type\": \"done\", \"data\": {{\"status\": \"{status}\"}}}}\n\n"
                return
            time.sleep(0.1)
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/runs/<run_id>/status")
def api_run_status(run_id):
    run = _active_runs.get(run_id)
    if not run:
        path = os.path.join(RUNS_DIR, f"{run_id}.json")
        if os.path.isfile(path):
            with open(path) as f:
                run = json.load(f)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({
        "id": run_id, "status": run.get("status"),
        "final_output": run.get("final_output", ""),
        "error": run.get("error"),
    })

@app.route("/api/runs/<run_id>/cancel", methods=["POST"])
def api_cancel_run(run_id):
    with _runs_lock:
        if run_id in _active_runs:
            _active_runs[run_id]["cancelled"] = True
    return jsonify({"ok": True})

# ── Workspace ──────────────────────────────────────────────
@app.route("/api/workspace")
def api_workspace():
    files = []
    for f in sorted(os.listdir(WORKSPACE)):
        full = os.path.join(WORKSPACE, f)
        if os.path.isfile(full):
            files.append({"name": f, "size": os.path.getsize(full)})
    return jsonify({"files": files})

# ── Chat tool-call endpoint ────────────────────────────────
@app.route("/api/chat/flows")
def api_chat_flows():
    # Returns Chat-enabled flows formatted as tool definitions
    flows = list_flows()
    tools = []
    for f in flows:
        if not f.get("chat_enabled"):
            continue
        flow = load_flow(f["id"])
        if not flow:
            continue
        # Find the Chat Input node schema
        schema = {"type": "object", "properties": {}, "required": []}
        for node in flow.get("nodes", []):
            if node.get("type") == "chat_input":
                schema = node.get("config", {}).get("schema", schema)
                break
        tools.append({
            "type": "function",
            "function": {
                "name": f"run_flow_{f['id']}",
                "description": f.get("description") or f"Run the '{f['name']}' flow",
                "parameters": schema,
            },
            "_flow_id": f["id"],
        })
    return jsonify({"tools": tools})

# ── Node schema (for wm-flow itself to be a plugin) ────────
@app.route("/node/schema")
def node_schema():
    return jsonify({
        "name": "flow",
        "description": "Execute a named Wickerman flow",
        "inputs": [
            {"name": "flow_id", "type": "string", "required": True},
            {"name": "chat_input", "type": "string", "required": False},
        ],
        "outputs": [
            {"name": "text", "type": "string"},
            {"name": "status", "type": "string"},
        ]
    })

@app.route("/node/execute", methods=["POST"])
def node_execute():
    d = request.json or {}
    flow_id = d.get("flow_id", "")
    flow    = load_flow(flow_id)
    if not flow:
        return jsonify({"error": f"Flow not found: {flow_id}"}), 404
    if d.get("chat_input"):
        for node in flow.get("nodes", []):
            if node.get("type") in ("chat_input", "input"):
                node.setdefault("config", {})["value"] = d["chat_input"]
                break
    run_id  = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    run     = {"id": run_id, "flow_id": flow_id, "status": "starting",
               "events": [], "cancelled": False, "final_output": "", "error": None}
    with _runs_lock:
        _active_runs[run_id] = run
    _run_flow(run_id, flow)  # Synchronous for node execution
    return jsonify({"text": run.get("final_output", ""), "status": run.get("status")})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
""",

"data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flow Editor</title>
<script src="https://unpkg.com/litegraph.js@0.7.18/build/litegraph.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/litegraph.js@0.7.18/css/litegraph.css">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;
  --text:#cdd6f4;--sub:#6c7086;--blue:#89b4fa;--green:#a6e3a1;
  --red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;
}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column}
.topbar{height:42px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 12px;gap:8px;flex-shrink:0}
.logo{font-size:14px;font-weight:700;color:var(--mauve);letter-spacing:.5px}
.breadcrumb{font-size:12px;color:var(--sub);display:flex;align-items:center;gap:6px}
.breadcrumb span{cursor:pointer;color:var(--blue)}.breadcrumb span:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--border)}
.spacer{flex:1}
.btn{padding:5px 12px;border-radius:5px;border:1px solid var(--border);background:transparent;color:var(--sub);font-size:11px;cursor:pointer;font-family:inherit}
.btn:hover{color:var(--text);border-color:var(--text)}
.btn.primary{background:var(--mauve);color:var(--bg);border-color:var(--mauve);font-weight:600}
.btn.primary:hover{opacity:.9}
.btn.success{background:var(--green);color:var(--bg);border-color:var(--green);font-weight:600}
.btn.danger{background:transparent;border-color:rgba(243,139,168,.4);color:var(--red)}
.main{display:flex;flex:1;overflow:hidden}
.sidebar{width:200px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sidebar-section{padding:8px 10px;font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border)}
.node-list{overflow-y:auto;flex:1;padding:6px}
.node-item{padding:7px 10px;border-radius:6px;font-size:12px;cursor:grab;border:1px solid var(--border);margin-bottom:4px;background:var(--overlay);color:var(--text)}
.node-item:hover{border-color:var(--mauve);color:var(--mauve)}
.node-item .ni-icon{margin-right:6px}
.canvas-wrap{flex:1;position:relative;overflow:hidden}
#canvas{position:absolute;inset:0;width:100%;height:100%}
.run-panel{width:280px;background:var(--surface);border-left:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.run-panel-header{padding:10px 12px;font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.run-log{flex:1;overflow-y:auto;padding:8px;font-family:'Courier New',monospace;font-size:11px;line-height:1.6}
.log-started{color:var(--blue)}
.log-complete{color:var(--green)}
.log-error{color:var(--red)}
.log-subflow{color:var(--mauve)}
.log-output{color:var(--teal);font-style:italic}
.log-info{color:var(--sub)}
.final-output{padding:10px 12px;border-top:1px solid var(--border);font-size:12px}
.final-output-label{color:var(--sub);font-size:10px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.final-output-text{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:12px;max-height:150px;overflow-y:auto;white-space:pre-wrap;line-height:1.5;color:var(--teal)}
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:1000}
.modal-overlay.open{display:flex}
.modal{background:var(--overlay);border:1px solid var(--border);border-radius:10px;padding:24px;width:480px;max-width:90vw;max-height:80vh;overflow-y:auto}
.modal h3{font-size:15px;font-weight:600;color:var(--blue);margin-bottom:14px}
.field{margin-bottom:12px}
.field label{display:block;font-size:11px;color:var(--sub);margin-bottom:4px}
.field input,.field select,.field textarea{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:6px;font-size:13px;outline:none;font-family:inherit}
.field input:focus,.field select:focus,.field textarea:focus{border-color:var(--mauve)}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
.flow-item{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:8px;cursor:pointer}
.flow-item:hover{border-color:var(--blue)}
.flow-item .fname{font-size:13px;font-weight:600;color:var(--blue)}
.flow-item .fmeta{font-size:11px;color:var(--sub);margin-top:2px}
.flow-actions{display:flex;gap:6px;margin-top:8px}
.receipt-badge{display:inline-flex;align-items:center;gap:4px;background:rgba(203,166,247,.1);border:1px solid rgba(203,166,247,.3);border-radius:10px;padding:2px 8px;font-size:11px;color:var(--mauve);cursor:pointer;margin-top:4px}
.receipt-badge:hover{background:rgba(203,166,247,.2)}
</style>
</head><body>

<div class="topbar">
  <span class="logo">⬡ FLOW EDITOR</span>
  <div class="breadcrumb" id="breadcrumb">
    <span onclick="navToRoot()">Main</span>
  </div>
  <div class="spacer"></div>
  <button class="btn" onclick="showLibrary()">📂 Flows</button>
  <button class="btn" onclick="newFlow()">+ New</button>
  <button class="btn" onclick="saveFlow()">💾 Save</button>
  <button class="btn success" id="runBtn" onclick="executeFlow()">▶ Execute</button>
  <button class="btn danger" id="cancelBtn" onclick="cancelRun()" style="display:none">■ Cancel</button>
</div>

<div class="main">
  <!-- Node palette -->
  <div class="sidebar">
    <div class="sidebar-section">Nodes</div>
    <div class="node-list" id="nodePalette"></div>
  </div>

  <!-- Canvas -->
  <div class="canvas-wrap">
    <canvas id="canvas"></canvas>
  </div>

  <!-- Run panel -->
  <div class="run-panel">
    <div class="run-panel-header">
      <span>Run Log</span>
      <button class="btn" onclick="clearLog()" style="padding:2px 8px;font-size:10px">Clear</button>
    </div>
    <div class="run-log" id="runLog"><span class="log-info">Ready. Hit Execute to run the flow.</span></div>
    <div class="final-output" id="finalOutputPanel" style="display:none">
      <div class="final-output-label">Final Output</div>
      <div class="final-output-text" id="finalOutputText"></div>
    </div>
  </div>
</div>

<!-- Library modal -->
<div class="modal-overlay" id="libraryModal">
  <div class="modal" style="width:560px">
    <h3>Flow Library</h3>
    <div id="flowListEl"></div>
    <div class="modal-actions">
      <button class="btn primary" onclick="closeModal('libraryModal')">Close</button>
    </div>
  </div>
</div>

<!-- New flow modal -->
<div class="modal-overlay" id="newFlowModal">
  <div class="modal">
    <h3>New Flow</h3>
    <div class="field"><label>Name</label><input id="nfName" placeholder="My Research Pipeline"></div>
    <div class="field"><label>Description</label><input id="nfDesc" placeholder="What does this flow do?"></div>
    <div class="field"><label><input type="checkbox" id="nfChat" style="width:auto;margin-right:6px">Chat-enabled (triggerable from Chat UI)</label></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal('newFlowModal')">Cancel</button>
      <button class="btn primary" onclick="createNewFlow()">Create</button>
    </div>
  </div>
</div>

<!-- Node config modal -->
<div class="modal-overlay" id="nodeConfigModal">
  <div class="modal">
    <h3 id="nodeConfigTitle">Configure Node</h3>
    <div id="nodeConfigFields"></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal('nodeConfigModal')">Cancel</button>
      <button class="btn primary" onclick="applyNodeConfig()">Apply</button>
    </div>
  </div>
</div>

<!-- Subflow name modal -->
<div class="modal-overlay" id="subflowModal">
  <div class="modal">
    <h3>Create Subflow</h3>
    <div class="field"><label>Name</label><input id="sfName" placeholder="Empirical Check"></div>
    <div class="field"><label>Description</label><input id="sfDesc" placeholder="What does this subflow do?"></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal('subflowModal')">Cancel</button>
      <button class="btn primary" onclick="doCreateSubflow()">Create Subflow</button>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;

// ── State ─────────────────────────────────────────────────
let graph = null;
let graphCanvas = null;
let currentFlowId = null;
let currentFlowData = { name: "Untitled", description: "", chat_enabled: false, nodes: [], edges: [] };
let currentRunId = null;
let runEventSource = null;
let agents = [];
let plugins = [];
let subflows = [];
let breadcrumbStack = []; // [{name, graph_data}]
let selectedNodes = []; // for subflow creation

// ── Litegraph setup ───────────────────────────────────────
function initLitegraph() {
  // Theme Wickerman Catppuccin colors
  LiteGraph.WIDGET_BGCOLOR     = "#181825";
  LiteGraph.WIDGET_OUTLINE_COLOR = "#313244";
  LiteGraph.DEFAULT_GROUP_FONT  = "bold 12px Arial";
  LGraphCanvas.DEFAULT_BACKGROUND_COLOR = "#11111b";
  LGraphCanvas.DEFAULT_CONNECTION_COLOR = "#89b4fa";

  graph = new LGraph();
  graphCanvas = new LGraphCanvas("#canvas", graph);
  graphCanvas.background_color = "#11111b";
  graphCanvas.clear_background = true;
  graphCanvas.node_title_color = "#cdd6f4";
  graphCanvas.render_connections_border = false;
  graphCanvas.render_curved_connections = true;
  graphCanvas.always_render_background = true;

  // Double-click node to configure
  graphCanvas.onNodeDblClicked = function(node) {
    if (node.type === "wm/subflow") {
      enterSubflow(node);
    } else {
      openNodeConfig(node);
    }
  };

  // Right-click canvas menu
  graphCanvas.getExtraMenuOptions = function(node, options) {
    if (!node) {
      const selected = graphCanvas.selected_nodes ? Object.values(graphCanvas.selected_nodes) : [];
      if (selected.length >= 2) {
        options.push({
          content: "Create Subflow from selection",
          callback: () => promptCreateSubflow(selected)
        });
      }
    }
  };

  registerAllNodes();
  graphCanvas.resize();
  graph.start();
}

// ── Register node types ───────────────────────────────────
const NODE_DEFS = [
  { type: "wm/chat_input",     title: "Chat Input",     color: "#1e3a2e", icon: "💬", outputs: [["text","string"]] },
  { type: "wm/input",          title: "Input",           color: "#1e3a2e", icon: "▶",  outputs: [["text","string"]] },
  { type: "wm/subflow_input",  title: "Subflow Input",   color: "#2e2a4e", icon: "→",  outputs: [["text","string"]] },
  { type: "wm/agent",          title: "Agent",           color: "#1e2a3e", icon: "🤖", inputs:  [["text","string"]], outputs: [["text","string"]] },
  { type: "wm/prompt",         title: "Prompt",          color: "#3e3a1e", icon: "✏",  inputs:  [["text","string"]], outputs: [["text","string"]] },
  { type: "wm/think",          title: "Think",           color: "#2e1e4e", icon: "🧠", inputs:  [["text","string"]], outputs: [["full_response","string"],["answer_only","string"]] },
  { type: "wm/compare",        title: "Compare",         color: "#1e3e3e", icon: "⇄",  inputs:  [["text","string"]], outputs: [["text_a","string"],["text_b","string"],["combined","string"]] },
  { type: "wm/branch",         title: "Branch",          color: "#3e2e1e", icon: "⇌",  inputs:  [["text","string"],["bool","boolean"]], outputs: [["true","string"],["false","string"],["passed","boolean"]] },
  { type: "wm/combine",        title: "Combine",         color: "#1e3a2e", icon: "⋃",  inputs:  [["input_0","string"],["input_1","string"]], outputs: [["text","string"]] },
  { type: "wm/retry",          title: "Retry",           color: "#3e1e1e", icon: "🔄", inputs:  [["text","string"]], outputs: [["text","string"],["passed","boolean"]] },
  { type: "wm/workspace",      title: "Workspace",       color: "#2e2e2e", icon: "📂", inputs:  [["text","string"]], outputs: [["text","string"]] },
  { type: "wm/subflow",        title: "Subflow",         color: "#2e1e3e", icon: "📦", inputs:  [["text","string"]], outputs: [["text","string"]] },
  { type: "wm/subflow_output", title: "Subflow Output",  color: "#2e2a4e", icon: "←",  inputs:  [["text","string"]] },
  { type: "wm/output",         title: "Output",          color: "#1e3a2e", icon: "■",  inputs:  [["text","string"]] },
];

function registerAllNodes() {
  NODE_DEFS.forEach(def => {
    function NodeClass() {
      this.title = def.title;
      this.color = def.color;
      this.shape = LiteGraph.ROUND_SHAPE;
      this._wm_config = {};
      this._wm_type = def.type.replace("wm/","");
      (def.inputs  || []).forEach(([n,t]) => this.addInput(n, t));
      (def.outputs || []).forEach(([n,t]) => this.addOutput(n, t));
      this.size = [180, 60];
    }
    NodeClass.title = def.title;
    LiteGraph.registerNodeType(def.type, NodeClass);
  });
}

// ── Fix: Subflow port sync ────────────────────────────────
// When a subflow_id is assigned to a wm/subflow node, dynamically
// sync its ports to match the subflow's named Subflow Input/Output nodes.
async function syncSubflowPorts(node, sfId) {
  if (!sfId) return;
  try {
    const sf = await fetch(`${API}/api/subflows/${sfId}`).then(r=>r.json());
    if (sf.error) return;
    // Clear all existing ports
    node.inputs  = [];
    node.outputs = [];
    // Recreate from the subflow's named I/O nodes
    (sf.inputs  || []).forEach(inp => node.addInput(inp.name  || "input",  "string"));
    (sf.outputs || []).forEach(out => node.addOutput(out.name || "output", "string"));
    // If no I/O defined yet, give one default port each
    if (!node.inputs.length)  node.addInput("text",  "string");
    if (!node.outputs.length) node.addOutput("text", "string");
    // Resize node to fit new ports
    node.size = node.computeSize ? node.computeSize() : [Math.max(180, 20 + (node.title.length * 8)), 60 + Math.max(node.inputs.length, node.outputs.length) * 20];
    // Update title to show which subflow
    node.title = sf.name || "Subflow";
    graphCanvas?.setDirty(true, true);
  } catch(e) {
    console.warn("Subflow port sync failed:", e);
  }
}

// ── Palette ───────────────────────────────────────────────
function buildPalette() {
  const el = document.getElementById('nodePalette');
  const groups = [
    { label: "Entry", types: ["wm/input","wm/chat_input","wm/subflow_input"] },
    { label: "Logic", types: ["wm/agent","wm/prompt","wm/think","wm/compare","wm/branch","wm/combine","wm/retry"] },
    { label: "Data",  types: ["wm/workspace"] },
    { label: "Flow",  types: ["wm/subflow"] },
    { label: "Exit",  types: ["wm/output","wm/subflow_output"] },
  ];
  el.innerHTML = '';
  groups.forEach(g => {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:.5px;padding:6px 4px 2px;';
    label.textContent = g.label;
    el.appendChild(label);
    g.types.forEach(type => {
      const def = NODE_DEFS.find(d => d.type === type);
      if (!def) return;
      const item = document.createElement('div');
      item.className = 'node-item';
      item.innerHTML = `<span class="ni-icon">${def.icon}</span>${def.title}`;
      item.draggable = true;
      item.addEventListener('click', () => addNode(type));
      el.appendChild(item);
    });
  });

  // Subflows section
  if (subflows.length) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:10px;color:var(--mauve);text-transform:uppercase;letter-spacing:.5px;padding:6px 4px 2px;';
    label.textContent = 'Subflows';
    el.appendChild(label);
    subflows.forEach(sf => {
      const item = document.createElement('div');
      item.className = 'node-item';
      item.style.borderColor = 'rgba(203,166,247,.3)';
      item.innerHTML = `<span class="ni-icon">📦</span>${esc(sf.name)}`;
      item.addEventListener('click', () => addSubflowNode(sf));
      el.appendChild(item);
    });
  }

  // Plugin nodes
  const pluginLabel = document.createElement('div');
  pluginLabel.style.cssText = 'font-size:10px;color:var(--teal);text-transform:uppercase;letter-spacing:.5px;padding:6px 4px 2px;display:flex;justify-content:space-between;align-items:center;';
  pluginLabel.innerHTML = 'Plugins <span onclick="showRegisterPlugin()" style="cursor:pointer;color:var(--teal);font-size:14px;line-height:1" title="Register a plugin">+</span>';
  el.appendChild(pluginLabel);
  if (plugins.length) {
    plugins.forEach(p => {
      const item = document.createElement('div');
      item.className = 'node-item';
      item.style.borderColor = 'rgba(148,226,213,.3)';
      item.innerHTML = `<span class="ni-icon">⚙</span>${esc(p.name)}`;
      item.addEventListener('click', () => addPluginNode(p));
      el.appendChild(item);
    });
  } else {
    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:11px;color:var(--sub);padding:4px 4px 8px;';
    hint.textContent = 'No plugins found. Click + to register.';
    el.appendChild(hint);
  }
}

function addNode(type) {
  if (!graph || !LiteGraph) { alert('Canvas not ready yet. Wait a moment and try again.'); return; }
  const node = LiteGraph.createNode(type);
  if (!node) { console.warn('Could not create node type:', type); return; }
  node.pos = [200 + Math.random()*200, 200 + Math.random()*100];
  graph.add(node);
}

function addSubflowNode(sf) {
  const node = LiteGraph.createNode("wm/subflow");
  node.title = sf.name;
  node._wm_config = { subflow_id: sf.id };
  node.pos = [200 + Math.random()*200, 200 + Math.random()*100];
  graph.add(node);
  // Sync ports immediately using the canonical sync function
  syncSubflowPorts(node, sf.id);
}

function addPluginNode(plugin) {
  // Dynamically register plugin node type if not registered
  const typeKey = `wm/plugin_${plugin._host}`;
  if (!LiteGraph.registered_node_types[typeKey]) {
    function PluginNode() {
      this.title = plugin.name;
      this.color = "#1e2e3e";
      this._wm_type = "plugin";
      this._wm_config = { plugin_host: plugin._host };
      (plugin.inputs  || []).forEach(i => this.addInput(i.name, i.type || "string"));
      (plugin.outputs || []).forEach(o => this.addOutput(o.name, o.type || "string"));
      this.size = [180, 60];
    }
    PluginNode.title = plugin.name;
    LiteGraph.registerNodeType(typeKey, PluginNode);
  }
  const node = LiteGraph.createNode(typeKey);
  node.pos = [200 + Math.random()*200, 200 + Math.random()*100];
  graph.add(node);
}

// ── Node config ───────────────────────────────────────────
let _configNode = null;
function openNodeConfig(node) {
  _configNode = node;
  const ntype = node._wm_type || node.type?.replace("wm/","") || "";
  document.getElementById('nodeConfigTitle').textContent = `Configure: ${node.title}`;
  const fields = document.getElementById('nodeConfigFields');

  const configs = {
    input:          [["mode","select","text|workspace_file","Mode"],["value","textarea","","Value / instruction"]],
    chat_input:     [["schema","textarea",'{"question":"string"}','Input JSON schema'],["value","textarea","","Default value"]],
    subflow_input:  [["port_name","text","text","Port name (appears on parent node)"]],
    subflow_output: [["port_name","text","text","Port name (appears on parent node)"]],
    agent:          [["agent","agent_select","default","Agent"],["system_prompt","textarea","","System prompt override"],["temperature","text","0.3","Temperature"],["max_tokens","text","1024","Max tokens"]],
    prompt:         [["template","textarea","{{text}}","Template — use {{variable}} placeholders"]],
    think:          [["agent","agent_select","default","Agent"],["steps","text","3","Reasoning steps"]],
    compare:        [["agent_a","agent_select","default","Agent A"],["agent_b","agent_select","default","Agent B"]],
    branch:         [["condition_type","select","keyword|regex|length_gt","Condition type"],["condition_value","text","","Value to check"]],
    combine:        [["mode","select","join|template","Mode"],["separator","text","\\n\\n","Separator (join mode)"],["template","textarea","","Template (template mode)"]],
    retry:          [["agent","agent_select","default","Agent"],["system_prompt","textarea","","System prompt"],["condition_type","select","keyword|regex|no_error","Exit condition"],["condition_value","text","","Condition value"],["max_retries","text","3","Max retries"]],
    workspace:      [["mode","select","read|write|append","Mode"],["filename","text","data.json","Filename in workspace"]],
    subflow:        [["subflow_id","subflow_select","","Subflow"]],
    output:         [["mode","select","display|chat_reply|file_save","Mode"],["filename","text","","Filename (file_save mode)"]],
    plugin:         [["plugin_host","text","","Plugin host"]],
  };

  const defs = configs[ntype] || [];
  fields.innerHTML = defs.map(([key, type, defaultVal, label]) => {
    const cur = (node._wm_config || {})[key] ?? defaultVal;
    if (type === "agent_select") {
      const opts = agents.map(a => `<option value="${esc(a)}" ${cur===a?'selected':''}>${esc(a)}</option>`).join('');
      return `<div class="field"><label>${label}</label><select id="cfg_${key}"><option value="default" ${cur==='default'?'selected':''}>default</option>${opts}</select></div>`;
    }
    if (type === "subflow_select") {
      const opts = subflows.map(s => `<option value="${esc(s.id)}" ${cur===s.id?'selected':''}>${esc(s.name)}</option>`).join('');
      return `<div class="field"><label>${label}</label><select id="cfg_${key}"><option value="">-- select --</option>${opts}</select></div>`;
    }
    if (type === "select") {
      const vals = defaultVal.split('|');
      const opts = vals.map(v => `<option value="${v}" ${cur===v?'selected':''}>${v}</option>`).join('');
      return `<div class="field"><label>${label}</label><select id="cfg_${key}">${opts}</select></div>`;
    }
    if (type === "textarea") {
      return `<div class="field"><label>${label}</label><textarea id="cfg_${key}" rows="3">${esc(cur)}</textarea></div>`;
    }
    return `<div class="field"><label>${label}</label><input id="cfg_${key}" value="${esc(cur)}"></div>`;
  }).join('');

  if (!defs.length) {
    fields.innerHTML = '<div style="color:var(--sub);font-size:12px;padding:8px">No configuration needed for this node.</div>';
  }

  document.getElementById('nodeConfigModal').classList.add('open');
}

function applyNodeConfig() {
  if (!_configNode) return;
  const ntype = _configNode._wm_type || _configNode.type?.replace("wm/","") || "";
  const allInputs = document.querySelectorAll('#nodeConfigFields input, #nodeConfigFields select, #nodeConfigFields textarea');
  const config = {};
  allInputs.forEach(el => {
    const key = el.id.replace('cfg_', '');
    config[key] = el.value;
  });
  _configNode._wm_config = config;
  // Sync subflow ports when subflow_id is set
  if (ntype === "subflow" && config.subflow_id) {
    syncSubflowPorts(_configNode, config.subflow_id);
  }
  closeModal('nodeConfigModal');
}

// ── Subflow navigation ────────────────────────────────────
function enterSubflow(node) {
  const sfId = (node._wm_config || {}).subflow_id;
  if (!sfId) { alert('No subflow configured. Double-click and set a subflow first.'); return; }
  fetch(`${API}/api/subflows/${sfId}`).then(r=>r.json()).then(sf => {
    if (sf.error) { alert(sf.error); return; }
    // Save current graph state to breadcrumb stack
    const serialized = { extra: {} };
    graph.serialize(serialized);
    breadcrumbStack.push({ name: currentFlowData.name || 'Main', graphData: serialized });
    // Load subflow graph
    const sfGraph = { extra: {} };
    if (sf.graph) {
      Object.assign(sfGraph, sf.graph);
    }
    graph.clear();
    if (sf.graph) graph.configure(sf.graph);
    updateBreadcrumb(sf.name);
  });
}

function navToRoot() {
  if (!breadcrumbStack.length) return;
  const root = breadcrumbStack[0];
  breadcrumbStack = [];
  graph.clear();
  if (root.graphData) graph.configure(root.graphData);
  updateBreadcrumb(null);
}

function navUp() {
  if (!breadcrumbStack.length) return;
  const prev = breadcrumbStack.pop();
  graph.clear();
  if (prev.graphData) graph.configure(prev.graphData);
  updateBreadcrumb(breadcrumbStack.length ? breadcrumbStack[breadcrumbStack.length-1].name : null);
}

function updateBreadcrumb(subflowName) {
  const el = document.getElementById('breadcrumb');
  if (!subflowName) {
    el.innerHTML = '<span onclick="navToRoot()">Main</span>';
  } else {
    const parts = ['<span onclick="navToRoot()">Main</span>'];
    breadcrumbStack.forEach((b, i) => {
      parts.push('<span class="sep">›</span>');
      parts.push(`<span onclick="navToBreadcrumb(${i})">${esc(b.name)}</span>`);
    });
    parts.push('<span class="sep">›</span>');
    parts.push(`<span style="color:var(--text)">${esc(subflowName)}</span>`);
    el.innerHTML = parts.join('');
  }
}

// ── Subflow creation ──────────────────────────────────────
let _pendingSubflowNodes = [];
function promptCreateSubflow(nodes) {
  _pendingSubflowNodes = nodes;
  document.getElementById('sfName').value = '';
  document.getElementById('sfDesc').value = '';
  document.getElementById('subflowModal').classList.add('open');
}

async function doCreateSubflow() {
  const name = document.getElementById('sfName').value.trim();
  const desc = document.getElementById('sfDesc').value.trim();
  if (!name) return;
  closeModal('subflowModal');

  // Serialize selected nodes as a mini-graph
  const nodeIds = new Set(_pendingSubflowNodes.map(n => n.id));
  const allLinks = graph.links;
  const innerEdges = [];
  if (allLinks) {
    Object.values(allLinks).forEach(link => {
      if (nodeIds.has(link.origin_id) && nodeIds.has(link.target_id)) {
        innerEdges.push({
          source: String(link.origin_id),
          target: String(link.target_id),
          sourcePort: graph.getNodeById(link.origin_id)?.outputs?.[link.origin_slot]?.name || "text",
          targetPort: graph.getNodeById(link.target_id)?.inputs?.[link.target_slot]?.name || "text",
        });
      }
    });
  }

  const innerNodes = _pendingSubflowNodes.map(n => ({
    id: String(n.id),
    type: n.type?.replace("wm/","") || "agent",
    config: n._wm_config || {},
    position: { x: n.pos[0], y: n.pos[1] },
  }));

  // Detect I/O from subflow_input and subflow_output nodes
  const inputs  = innerNodes.filter(n=>n.type==="subflow_input").map(n=>({name: n.config.port_name||"text", type:"string"}));
  const outputs = innerNodes.filter(n=>n.type==="subflow_output").map(n=>({name: n.config.port_name||"text", type:"string"}));

  const sf = { name, description: desc, inputs, outputs, graph: { nodes: innerNodes, edges: innerEdges } };
  const r  = await fetch(`${API}/api/subflows`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(sf) }).then(r=>r.json());
  if (r.ok) {
    await loadSubflows();
    buildPalette();
    alert(`Subflow "${name}" created and added to the palette.`);
  }
}

// ── Flow save / load ──────────────────────────────────────
function graphToFlowData() {
  const nodes = [];
  const edges = [];
  graph._nodes?.forEach(n => {
    nodes.push({
      id: String(n.id),
      type: n._wm_type || n.type?.replace("wm/","") || "agent",
      config: n._wm_config || {},
      position: { x: n.pos[0], y: n.pos[1] },
    });
  });
  if (graph.links) {
    Object.values(graph.links).forEach(link => {
      const srcNode = graph.getNodeById(link.origin_id);
      const tgtNode = graph.getNodeById(link.target_id);
      if (!srcNode || !tgtNode) return;
      edges.push({
        source: String(link.origin_id),
        target: String(link.target_id),
        sourcePort: srcNode.outputs?.[link.origin_slot]?.name || "text",
        targetPort: tgtNode.inputs?.[link.target_slot]?.name || "text",
      });
    });
  }
  return { ...currentFlowData, nodes, edges };
}

async function saveFlow() {
  const data = graphToFlowData();
  const r = await fetch(`${API}/api/flows`, {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)
  }).then(r=>r.json());
  if (r.ok) {
    currentFlowId = r.flow_id;
    currentFlowData.id = r.flow_id;
    logMessage(`Flow saved: ${currentFlowData.name}`, 'info');
  }
}

async function loadFlowById(flowId) {
  const flow = await fetch(`${API}/api/flows/${flowId}`).then(r=>r.json());
  if (flow.error) { alert(flow.error); return; }
  currentFlowId   = flow.id;
  currentFlowData = flow;
  graph.clear();
  // Recreate nodes
  (flow.nodes || []).forEach(n => {
    const typeKey = `wm/${n.type}`;
    let node;
    try { node = LiteGraph.createNode(typeKey); }
    catch(e) { node = LiteGraph.createNode("wm/agent"); }
    node._wm_config = n.config || {};
    node._wm_type   = n.type;
    if (n.position) node.pos = [n.position.x, n.position.y];
    graph.add(node);
    n._lg_node = node; // temp ref
  });
  // Recreate edges
  const nodeMap = {};
  flow.nodes?.forEach(n => { nodeMap[n.id] = n._lg_node; });
  (flow.edges || []).forEach(e => {
    const src = nodeMap[e.source];
    const tgt = nodeMap[e.target];
    if (!src || !tgt) return;
    const srcSlot = src.outputs?.findIndex(o=>o.name===e.sourcePort) ?? 0;
    const tgtSlot = tgt.inputs?.findIndex(i=>i.name===e.targetPort) ?? 0;
    if (srcSlot >= 0 && tgtSlot >= 0) src.connect(srcSlot, tgt, tgtSlot);
  });
  closeModal('libraryModal');
  logMessage(`Loaded: ${flow.name}`, 'info');
}

// ── Execution ─────────────────────────────────────────────
async function executeFlow() {
  if (!currentFlowId) {
    await saveFlow();
    if (!currentFlowId) { alert('Save the flow first.'); return; }
  }
  await saveFlow();
  clearLog();
  document.getElementById('finalOutputPanel').style.display = 'none';
  document.getElementById('runBtn').disabled = true;
  document.getElementById('cancelBtn').style.display = 'inline-block';

  const r = await fetch(`${API}/api/flows/${currentFlowId}/execute`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
  }).then(r=>r.json());

  if (r.error) { logMessage(`Error: ${r.error}`, 'error'); resetRunUI(); return; }
  currentRunId = r.run_id;

  if (runEventSource) runEventSource.close();
  runEventSource = new EventSource(`${API}/api/runs/${currentRunId}/stream`);
  runEventSource.onmessage = (e) => {
    const evt = JSON.parse(e.data);
    handleRunEvent(evt);
    if (evt.type === 'done') {
      runEventSource.close();
      resetRunUI();
    }
  };
  runEventSource.onerror = () => { runEventSource.close(); resetRunUI(); };
}

function handleRunEvent(evt) {
  const type = evt.type;
  const data = evt.data || {};
  if (type === 'flow_started') {
    logMessage(`▶ Starting: ${data.name || data.flow_id}`, 'started');
  } else if (type === 'node_started') {
    logMessage(`  → ${data.type || data.node_id}`, 'started');
    highlightNode(data.node_id, '#89b4fa');
  } else if (type === 'node_output') {
    logMessage(`    ${data.text || ''}`, 'info');
  } else if (type === 'node_complete') {
    highlightNode(data.node_id, '#a6e3a1');
    const out = data.outputs || {};
    const preview = Object.values(out).find(v => typeof v === 'string')?.slice(0,80) || '';
    if (preview) logMessage(`    ✓ ${preview}${preview.length >= 80 ? '...' : ''}`, 'complete');
  } else if (type === 'node_error') {
    highlightNode(data.node_id, '#f38ba8');
    logMessage(`  ✗ Error: ${data.error}`, 'error');
  } else if (type === 'subflow_entered') {
    logMessage(`  ↳ Entering subflow: ${data.subflow_id}`, 'subflow');
  } else if (type === 'subflow_exited') {
    logMessage(`  ↲ Exited subflow`, 'subflow');
  } else if (type === 'flow_complete') {
    logMessage(`■ Complete`, 'complete');
    if (data.final_output) {
      document.getElementById('finalOutputPanel').style.display = 'block';
      document.getElementById('finalOutputText').textContent = data.final_output;
    }
  } else if (type === 'flow_error') {
    logMessage(`✗ Flow error: ${data.error}`, 'error');
  } else if (type === 'flow_cancelled') {
    logMessage('Cancelled.', 'info');
  }
}

function highlightNode(nodeId, color) {
  graph._nodes?.forEach(n => {
    if (String(n.id) === String(nodeId)) {
      n.color = color;
      graphCanvas.setDirty(true);
    }
  });
}

async function cancelRun() {
  if (!currentRunId) return;
  await fetch(`${API}/api/runs/${currentRunId}/cancel`, {method:'POST'});
  if (runEventSource) runEventSource.close();
  resetRunUI();
  logMessage('Cancelled.', 'info');
}

function resetRunUI() {
  document.getElementById('runBtn').disabled = false;
  document.getElementById('cancelBtn').style.display = 'none';
}

// ── Library ───────────────────────────────────────────────
async function showLibrary() {
  const r = await fetch(`${API}/api/flows`).then(r=>r.json());
  const el = document.getElementById('flowListEl');
  const flows = r.flows || [];
  if (!flows.length) {
    el.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:8px">No flows saved yet.</div>';
  } else {
    el.innerHTML = flows.map(f => `
      <div class="flow-item">
        <div class="fname">${esc(f.name)}${f.chat_enabled?' <span style="color:var(--mauve);font-size:10px">⚡ Chat</span>':''}</div>
        <div class="fmeta">${f.node_count} nodes${f.description?' — '+esc(f.description):''}</div>
        <div class="flow-actions">
          <button class="btn" onclick="loadFlowById('${esc(f.id)}')">Open</button>
          <button class="btn danger" onclick="deleteFlow('${esc(f.id)}',this)">Delete</button>
        </div>
      </div>`).join('');
  }
  document.getElementById('libraryModal').classList.add('open');
}

async function deleteFlow(flowId, btn) {
  if (!confirm('Delete this flow?')) return;
  await fetch(`${API}/api/flows/${flowId}`, {method:'DELETE'});
  btn.closest('.flow-item').remove();
}

function newFlow() {
  document.getElementById('nfName').value = '';
  document.getElementById('nfDesc').value = '';
  document.getElementById('nfChat').checked = false;
  document.getElementById('newFlowModal').classList.add('open');
}

function createNewFlow() {
  const name = document.getElementById('nfName').value.trim();
  if (!name) return;
  if (!graph) { alert('Canvas not ready — Litegraph may still be loading. Try again in a moment.'); return; }
  graph.clear();
  currentFlowId = null;
  currentFlowData = {
    name,
    description: document.getElementById('nfDesc').value.trim(),
    chat_enabled: document.getElementById('nfChat').checked,
    nodes: [], edges: []
  };
  closeModal('newFlowModal');
  logMessage(`New flow: ${name}`, 'info');
}

// ── Log helpers ───────────────────────────────────────────
function logMessage(text, cls = 'info') {
  const el = document.getElementById('runLog');
  const line = document.createElement('div');
  line.className = `log-${cls}`;
  line.textContent = text;
  el.appendChild(line);
  el.scrollTop = 99999;
}

function clearLog() {
  document.getElementById('runLog').innerHTML = '';
  document.getElementById('finalOutputPanel').style.display = 'none';
}

// ── Data loaders ──────────────────────────────────────────
async function loadAgents() {
  const r = await fetch(`${API}/api/agents`).then(r=>r.json());
  agents = r.agents || [];
}

async function loadPlugins() {
  const r = await fetch(`${API}/api/plugins`).then(r=>r.json());
  plugins = r.plugins || [];
}

async function loadSubflows() {
  const r = await fetch(`${API}/api/subflows`).then(r=>r.json());
  subflows = r.subflows || [];
}

// ── Modal helpers ─────────────────────────────────────────
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

async function showRegisterPlugin() {
  const host = prompt('Enter plugin container hostname (e.g. wm-weather):');
  if (!host) return;
  const port = prompt('Port (default 5000):', '5000') || '5000';
  const r = await fetch(`${API}/api/plugins/register`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({host, port: parseInt(port)})
  }).then(r=>r.json());
  if (r.ok) {
    await loadPlugins();
    buildPalette();
    logMessage(`Plugin registered: ${r.plugin?.name || host}`, 'complete');
  } else {
    alert(`Failed to register: ${r.error}`);
  }
}
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

// ── Init ──────────────────────────────────────────────────
async function init() {
  await Promise.all([loadAgents(), loadPlugins(), loadSubflows()]);
  if (typeof LiteGraph === 'undefined') {
    document.getElementById('runLog').innerHTML =
      '<span class="log-error">Litegraph.js failed to load from CDN. Check your internet connection and refresh.</span>';
    return;
  }
  initLitegraph();
  buildPalette();
  setInterval(loadAgents, 30000);
}

window.addEventListener('resize', () => graphCanvas?.resize());
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.open').forEach(m=>m.classList.remove('open'));
});
init();
</script>
</body></html>
""",

}  # end WM_FLOW_FILES

WM_FLOW["files"] = WM_FLOW_FILES

PLUGIN_HOST = ("127.0.0.1", "flow.wickerman.local")
