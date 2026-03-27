"""
Wickerman OS v5.6.0 - Model Probe plugin manifest.
Find your model's weaknesses. Fix them.
"""

WM_PROBE = {
    "name": "Model Probe",
    "description": "Systematic model evaluation — find weaknesses, generate correction datasets",
    "icon": "science",
    "build": True,
    "build_context": "data",
    "container_name": "wm-probe",
    "url": "http://probe.wickerman.local",
    "ports": [5000],
    "gpu": False,
    "env": [
        "LLAMA_API=http://wm-llama:8080",
        "PROBES_DIR=/probes",
        "DATA_DIR=/data",
        "DATASETS_DIR=/datasets",
    ],
    "volumes": [
        "{support}/datasets/probes:/probes",
        "{datasets}:/datasets",
        "{self}/data:/data",
    ],
    "nginx_host": "probe.wickerman.local",
    "help": (
        "## Model Probe\n"
        "Systematic evaluation of any loaded agent.\n\n"
        "**Run tab:** Pick a model to test and a judge agent. "
        "Select probe categories and hit Run.\n\n"
        "**Report tab:** See scores by category. Drill into failures. "
        "Approve correction pairs for training.\n\n"
        "**Compare tab:** Side-by-side before/after comparison of two runs.\n\n"
        "**Probes tab:** Browse the built-in probe bank. Add custom probes.\n\n"
        "**Probe banks:** Drop .jsonl files into "
        "~/WickermanSupport/datasets/probes/ to add new probe sets.\n\n"
        "**Export:** Send approved corrections directly to the Trainer."
    ),
}

WM_PROBE_FILES = {

"data/Dockerfile": r"""FROM python:3.11-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.* gunicorn==22.* requests==2.32.*
RUN useradd -m -s /bin/bash -u 1001 probeuser
WORKDIR /app
ARG CACHEBUST=1
COPY . .
RUN mkdir -p /probes /data /data/runs /datasets /datasets/probes && \
    chown -R probeuser:probeuser /app /probes /data /datasets
USER probeuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", \
     "--threads", "4", "--timeout", "300", "app:app"]
""",

"data/app.py": r"""#!/usr/bin/env python3
# Wickerman Model Probe - Backend
import os, json, re, time, threading, hashlib, glob, random, copy
from collections import defaultdict
from flask import Flask, request, jsonify, render_template
import requests as req_lib

app = Flask(__name__)

LLAMA_API   = os.environ.get("LLAMA_API",    "http://wm-llama:8080")
PROBES_DIR  = os.environ.get("PROBES_DIR",   "/probes")
DATA_DIR    = os.environ.get("DATA_DIR",     "/data")
DATASETS_DIR = os.environ.get("DATASETS_DIR", "/datasets")

os.makedirs(PROBES_DIR,  exist_ok=True)
os.makedirs(DATA_DIR,    exist_ok=True)
os.makedirs(DATASETS_DIR, exist_ok=True)

RUNS_DIR    = os.path.join(DATA_DIR, "runs")
CUSTOM_DIR  = os.path.join(PROBES_DIR, "custom")
os.makedirs(RUNS_DIR,   exist_ok=True)
os.makedirs(CUSTOM_DIR, exist_ok=True)

# ── Probe loading ──────────────────────────────────────────
def load_probe_banks():
    banks = {}
    for path in sorted(glob.glob(os.path.join(PROBES_DIR, "*.jsonl")) +
                       glob.glob(os.path.join(CUSTOM_DIR, "*.jsonl"))):
        name = os.path.splitext(os.path.basename(path))[0]
        probes = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        probes.append(json.loads(line))
            banks[name] = probes
        except Exception as e:
            print(f"[PROBE] Failed to load {path}: {e}", flush=True)
    return banks

def get_all_probes():
    banks = load_probe_banks()
    all_probes = []
    for bank_name, probes in banks.items():
        for p in probes:
            p = dict(p)
            p["bank"] = bank_name
            all_probes.append(p)
    return all_probes

def expand_probe(probe):
    # Variable injection: replace {var} in prompt with random sample from vars
    p = dict(probe)
    if p.get("vars") and "{" in p.get("prompt", ""):
        chosen = random.choice(p["vars"])
        p["prompt_rendered"] = re.sub(r'\{[^}]+\}', chosen, p["prompt"])
        p["var_used"] = chosen
    else:
        p["prompt_rendered"] = p.get("prompt", "")
        p["var_used"] = None
    return p

# ── Router calls ───────────────────────────────────────────
def call_agent(prompt, system_prompt, agent="default", max_tokens=1024):
    payload = {
        "model": agent,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        r = req_lib.post(
            f"{LLAMA_API}/v1/chat/completions",
            json=payload, timeout=120
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], None
    except Exception as e:
        try:
            req_lib.get(f"{LLAMA_API}/health", timeout=3)
            return None, f"Agent call failed: {e}"
        except:
            return None, "Model Router unreachable. Please load a model first."

def get_loaded_agents():
    try:
        r = req_lib.get(f"{LLAMA_API}/api/status", timeout=5)
        data = r.json()
        return [a for a, s in data.get("slots", {}).items()
                if s.get("status") == "ready"]
    except:
        return []

# ── Scoring ────────────────────────────────────────────────
def score_auto_keyword(response, probe):
    keywords = probe.get("keywords", [])
    resp_lower = response.lower()
    for kw in keywords:
        if kw.lower() in resp_lower:
            return "pass", "Keyword match found.", None
    return "fail", f"None of the expected keywords found: {keywords}", None

def score_auto_hedge(response, probe):
    # For questions that SHOULD express uncertainty
    hedge_words = [
        "i don't know", "i'm not sure", "uncertain", "unknown",
        "cannot predict", "impossible to know", "no way to know",
        "depends", "varies", "it's unclear", "unclear",
        "not possible to", "i cannot", "i can't",
    ]
    resp_lower = response.lower()
    for hw in hedge_words:
        if hw in resp_lower:
            return "pass", "Response appropriately expresses uncertainty.", None
    return "fail", "Response does not acknowledge uncertainty on a question that requires it.", None

def score_judge_agent(response, probe, judge_agent):
    rubric = probe.get("rubric", "")
    expected = probe.get("expected_behavior", "")
    system = (
        "You are a strict, objective evaluator assessing whether an AI model's response "
        "meets empirical standards. You apply the provided rubric exactly as written. "
        "You respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON. "
        'The JSON must have exactly these keys: "score" (one of: pass, fail, partial, review), '
        '"confidence" (0.0 to 1.0), "reasoning" (one sentence explaining the score).'
    )
    eval_prompt = (
        f"PROBE QUESTION:\n{probe.get('prompt_rendered', probe.get('prompt', ''))}\n\n"
        f"MODEL RESPONSE:\n{response}\n\n"
        f"EXPECTED BEHAVIOR:\n{expected}\n\n"
        f"SCORING RUBRIC:\n{rubric}\n\n"
        "Evaluate the model response against the rubric. Return only the JSON."
    )
    reply, err = call_agent(eval_prompt, system, judge_agent, max_tokens=256)
    if err:
        return "review", f"Judge unavailable: {err}", None

    # Parse judge response
    reply = reply.strip()
    if reply.startswith("```"):
        reply = re.sub(r'^```[a-zA-Z]*\n', '', reply)
        reply = re.sub(r'\n```$', '', reply.strip())
    # Extract JSON object
    match = re.search(r'\{.*\}', reply, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            score = result.get("score", "review").lower()
            if score not in ("pass", "fail", "partial", "review"):
                score = "review"
            reasoning = result.get("reasoning", "No reasoning provided.")
            confidence = float(result.get("confidence", 0.5))
            return score, reasoning, confidence
        except:
            pass
    return "review", f"Could not parse judge response: {reply[:100]}", None

def score_probe(probe, response, judge_agent):
    scoring = probe.get("scoring", "judge_agent")
    if scoring == "auto_keyword":
        return score_auto_keyword(response, probe)
    elif scoring == "auto_hedge":
        return score_auto_hedge(response, probe)
    elif scoring == "consistency_pair":
        # Consistency scoring is handled separately at run level
        return "pass", "Consistency checked at run level.", None
    else:
        return score_judge_agent(response, probe, judge_agent)

# ── Run management ─────────────────────────────────────────
_active_runs = {}
_runs_lock = threading.Lock()

def save_run(run_id, data):
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_run(run_id):
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)

def list_runs():
    runs = []
    for path in sorted(glob.glob(os.path.join(RUNS_DIR, "*.json")), reverse=True):
        try:
            with open(path) as f:
                data = json.load(f)
            runs.append({
                "id": data["id"],
                "agent": data["agent"],
                "started_at": data["started_at"],
                "status": data["status"],
                "total": data.get("total", 0),
                "complete": data.get("complete", 0),
                "scores": data.get("summary", {}),
            })
        except:
            pass
    return runs[:20]  # Last 20 runs

def execute_run(run_id):
    with _runs_lock:
        run = _active_runs.get(run_id)
        if not run:
            return

    agent = run["agent"]
    judge = run["judge_agent"]
    categories = set(run["categories"])

    # Load and filter probes
    all_probes = get_all_probes()
    selected = [p for p in all_probes if p.get("category", "") in categories]
    if not selected:
        run["status"] = "error"
        run["error"] = "No probes found for selected categories"
        save_run(run_id, run)
        return

    run["total"] = len(selected)
    run["complete"] = 0
    run["results"] = []
    run["status"] = "running"
    save_run(run_id, run)

    print(f"[PROBE] Run {run_id}: {len(selected)} probes, agent={agent}, judge={judge}", flush=True)

    # Expand probes (variable injection)
    expanded = [expand_probe(p) for p in selected]

    # Track consistency pairs
    consistency_pairs = defaultdict(list)

    for i, probe in enumerate(expanded):
        with _runs_lock:
            if _active_runs.get(run_id, {}).get("cancelled"):
                run["status"] = "cancelled"
                save_run(run_id, run)
                return

        prompt = probe.get("prompt_rendered", probe.get("prompt", ""))
        probe_system = "You are a knowledgeable assistant. Answer the question clearly and accurately."

        # Get model response
        response, err = call_agent(prompt, probe_system, agent, max_tokens=512)
        if err:
            score, reasoning, confidence = "review", f"Model unavailable: {err}", None
            response = ""
        else:
            score, reasoning, confidence = score_probe(probe, response, judge)

        result = {
            "probe_id": probe.get("id", f"probe_{i}"),
            "category": probe.get("category", "unknown"),
            "tier": probe.get("tier", 2),
            "prompt": probe.get("prompt", ""),
            "prompt_rendered": prompt,
            "var_used": probe.get("var_used"),
            "response": response,
            "score": score,
            "reasoning": reasoning,
            "confidence": confidence,
            "expected_behavior": probe.get("expected_behavior", ""),
            "rubric": probe.get("rubric", ""),
            # For correction dataset generation
            "fail_auto_train": probe.get("fail_auto_train", True),
            "partial_human_review": probe.get("partial_human_review", True),
        }

        # Track consistency pairs
        if probe.get("scoring") == "consistency_pair":
            paired_id = probe.get("keywords", [""])[0]
            pair_key = tuple(sorted([probe.get("id", ""), paired_id]))
            consistency_pairs[pair_key].append(result)

        run["results"].append(result)
        run["complete"] = i + 1

        # Periodic save
        if (i + 1) % 5 == 0:
            save_run(run_id, run)

    # Post-process consistency pairs
    for pair_key, pair_results in consistency_pairs.items():
        if len(pair_results) == 2:
            r1, r2 = pair_results
            # Simple consistency check via judge
            if r1["response"] and r2["response"]:
                system = (
                    "You are evaluating whether two responses to related questions are factually consistent. "
                    'Respond ONLY with JSON: {"consistent": true/false, "reasoning": "one sentence"}'
                )
                cons_prompt = (
                    f"Question 1: {r1['prompt_rendered']}\nAnswer 1: {r1['response']}\n\n"
                    f"Question 2: {r2['prompt_rendered']}\nAnswer 2: {r2['response']}\n\n"
                    "Are these answers factually consistent with each other?"
                )
                reply, _ = call_agent(cons_prompt, system, judge, max_tokens=128)
                if reply:
                    try:
                        match = re.search(r'\{.*\}', reply, re.DOTALL)
                        if match:
                            d = json.loads(match.group())
                            for r in pair_results:
                                r["score"] = "pass" if d.get("consistent", True) else "fail"
                                r["reasoning"] = d.get("reasoning", "")
                    except:
                        pass

    # Build summary
    summary = defaultdict(lambda: {"pass": 0, "fail": 0, "partial": 0, "review": 0, "total": 0})
    for result in run["results"]:
        cat = result["category"]
        score = result["score"]
        summary[cat][score] += 1
        summary[cat]["total"] += 1

    # Calculate pass rates
    summary_out = {}
    for cat, counts in summary.items():
        total = counts["total"]
        passed = counts["pass"]
        pct = round(passed / total * 100) if total > 0 else 0
        summary_out[cat] = {**counts, "pass_pct": pct}

    run["summary"] = summary_out
    run["status"] = "complete"
    run["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_run(run_id, run)

    with _runs_lock:
        if run_id in _active_runs:
            _active_runs[run_id].update(run)

    print(f"[PROBE] Run {run_id} complete.", flush=True)

# ── Human review queue ─────────────────────────────────────
def get_review_queue(run_id):
    run = load_run(run_id)
    if not run:
        return []
    return [r for r in run.get("results", [])
            if r["score"] in ("partial", "review")]

def approve_result(run_id, probe_id, chosen_response, action):
    run = load_run(run_id)
    if not run:
        return False
    for r in run.get("results", []):
        if r["probe_id"] == probe_id:
            r["human_action"] = action
            r["human_chosen"] = chosen_response
            break
    save_run(run_id, run)
    return True

# ── Dataset export ─────────────────────────────────────────
def export_corrections(run_id):
    run = load_run(run_id)
    if not run:
        return None, "Run not found"

    pairs = []
    for r in run.get("results", []):
        score = r.get("score")
        human_action = r.get("human_action")

        if score == "fail" and r.get("fail_auto_train", True):
            # Auto-generate correction pair
            pairs.append({
                "prompt": r["prompt_rendered"],
                "chosen": r["expected_behavior"],
                "rejected": r["response"],
                "probe_id": r["probe_id"],
                "category": r["category"],
                "source": "wm-probe",
                "run_id": run_id,
            })
        elif human_action == "approve" and r.get("human_chosen"):
            # Human-approved correction
            pairs.append({
                "prompt": r["prompt_rendered"],
                "chosen": r["human_chosen"],
                "rejected": r["response"],
                "probe_id": r["probe_id"],
                "category": r["category"],
                "source": "wm-probe-human",
                "run_id": run_id,
            })

    if not pairs:
        return None, "No correction pairs to export"

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"probe_corrections_{ts}.jsonl"
    out_path = os.path.join(DATASETS_DIR, filename)
    with open(out_path, "w") as f:
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")

    return filename, None

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
    return jsonify({"agents": get_loaded_agents()})

# ── Probe bank ─────────────────────────────────────────────
@app.route("/api/probes")
def api_probes():
    banks = load_probe_banks()
    summary = {}
    for name, probes in banks.items():
        cats = defaultdict(int)
        for p in probes:
            cats[p.get("category", "unknown")] += 1
        summary[name] = {"total": len(probes), "categories": dict(cats)}
    return jsonify({"banks": summary})

@app.route("/api/probes/all")
def api_probes_all():
    category = request.args.get("category")
    all_probes = get_all_probes()
    if category:
        all_probes = [p for p in all_probes if p.get("category") == category]
    return jsonify({"probes": all_probes, "total": len(all_probes)})

@app.route("/api/probes/categories")
def api_probe_categories():
    all_probes = get_all_probes()
    cats = defaultdict(int)
    for p in all_probes:
        cats[p.get("category", "unknown")] += 1
    return jsonify({"categories": dict(cats)})

@app.route("/api/probes/custom", methods=["POST"])
def api_add_custom_probe():
    d = request.json or {}
    required = ["prompt", "expected_behavior", "rubric", "category"]
    if any(k not in d for k in required):
        return jsonify({"error": f"Required: {required}"}), 400

    probe = {
        "id": f"custom_{hashlib.md5(d['prompt'].encode()).hexdigest()[:8]}",
        "category": d["category"],
        "tier": d.get("tier", 6),
        "prompt": d["prompt"],
        "expected_behavior": d["expected_behavior"],
        "scoring": d.get("scoring", "judge_agent"),
        "rubric": d["rubric"],
        "fail_auto_train": d.get("fail_auto_train", True),
        "partial_human_review": True,
    }
    if d.get("vars"):
        probe["vars"] = d["vars"]

    out_path = os.path.join(CUSTOM_DIR, "user_custom.jsonl")
    with open(out_path, "a") as f:
        f.write(json.dumps(probe) + "\n")
    return jsonify({"ok": True, "probe": probe})

@app.route("/api/probes/generate", methods=["POST"])
def api_generate_probes():
    d = request.json or {}
    description = d.get("description", "").strip()
    category = d.get("category", "custom_domain")
    count = min(int(d.get("count", 5)), 10)
    agent = d.get("agent", "default")

    if not description:
        return jsonify({"error": "description required"}), 400

    # Check router
    try:
        req_lib.get(f"{LLAMA_API}/health", timeout=3)
    except:
        return jsonify({"error": "Model Router unreachable. Load a model first."}), 503

    system = (
        "You are generating probe questions for model evaluation. "
        "Return ONLY a valid JSON array of probe objects. No markdown, no backticks. "
        "Each object must have: prompt (string), expected_behavior (string), rubric (string). "
        "Questions must be neutral — do not hint at the expected answer in the question itself."
    )
    user_prompt = (
        f"Generate {count} probe questions to test an AI model on this topic:\n{description}\n\n"
        "Return a JSON array. Each probe: {\"prompt\": \"...\", \"expected_behavior\": \"...\", \"rubric\": \"...\"}"
    )

    reply, err = call_agent(user_prompt, system, agent, max_tokens=2048)
    if err:
        return jsonify({"error": err}), 503

    reply = reply.strip()
    if reply.startswith("```"):
        reply = re.sub(r'^```[a-zA-Z]*\n', '', reply)
        reply = re.sub(r'\n```$', '', reply.strip())
    bracket_end = reply.rfind("]")
    if bracket_end != -1:
        reply = reply[:bracket_end + 1]

    try:
        raw = json.loads(reply)
        probes = []
        for i, p in enumerate(raw):
            if isinstance(p, dict) and "prompt" in p:
                probes.append({
                    "id": f"gen_{hashlib.md5(p['prompt'].encode()).hexdigest()[:8]}",
                    "category": category,
                    "tier": 6,
                    "prompt": p.get("prompt", ""),
                    "expected_behavior": p.get("expected_behavior", ""),
                    "scoring": "judge_agent",
                    "rubric": p.get("rubric", "Evaluate whether the response is accurate and well-reasoned."),
                    "fail_auto_train": True,
                    "partial_human_review": True,
                })
        return jsonify({"probes": probes})
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Could not parse generated probes: {e}"}), 500

# ── Runs ───────────────────────────────────────────────────
@app.route("/api/runs")
def api_list_runs():
    return jsonify({"runs": list_runs()})

@app.route("/api/runs/start", methods=["POST"])
def api_start_run():
    d = request.json or {}
    agent = d.get("agent", "default")
    judge = d.get("judge_agent", "default")
    categories = d.get("categories", [])

    if not categories:
        return jsonify({"error": "Select at least one probe category"}), 400

    # Estimate time
    all_probes = get_all_probes()
    count = len([p for p in all_probes if p.get("category") in categories])
    est_seconds = count * 8  # rough estimate

    run_id = f"run_{int(time.time())}_{hashlib.md5(agent.encode()).hexdigest()[:6]}"
    run = {
        "id": run_id,
        "agent": agent,
        "judge_agent": judge,
        "categories": categories,
        "status": "starting",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total": count,
        "complete": 0,
        "results": [],
        "summary": {},
        "estimated_seconds": est_seconds,
    }
    with _runs_lock:
        _active_runs[run_id] = run
    save_run(run_id, run)

    threading.Thread(target=execute_run, args=(run_id,), daemon=True).start()
    return jsonify({"ok": True, "run_id": run_id, "total": count, "estimated_seconds": est_seconds})

@app.route("/api/runs/<run_id>/status")
def api_run_status(run_id):
    with _runs_lock:
        run = _active_runs.get(run_id)
    if not run:
        run = load_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({
        "id": run["id"],
        "status": run["status"],
        "total": run.get("total", 0),
        "complete": run.get("complete", 0),
        "agent": run["agent"],
        "started_at": run["started_at"],
        "summary": run.get("summary", {}),
        "error": run.get("error"),
    })

@app.route("/api/runs/<run_id>/results")
def api_run_results(run_id):
    category = request.args.get("category")
    score_filter = request.args.get("score")
    run = load_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    results = run.get("results", [])
    if category:
        results = [r for r in results if r.get("category") == category]
    if score_filter:
        results = [r for r in results if r.get("score") == score_filter]
    return jsonify({"results": results, "total": len(results)})

@app.route("/api/runs/<run_id>/cancel", methods=["POST"])
def api_cancel_run(run_id):
    with _runs_lock:
        if run_id in _active_runs:
            _active_runs[run_id]["cancelled"] = True
    return jsonify({"ok": True})

@app.route("/api/runs/<run_id>/review")
def api_review_queue(run_id):
    return jsonify({"items": get_review_queue(run_id)})

@app.route("/api/runs/<run_id>/approve", methods=["POST"])
def api_approve_result(run_id):
    d = request.json or {}
    probe_id = d.get("probe_id", "")
    chosen = d.get("chosen_response", "")
    action = d.get("action", "approve")
    ok = approve_result(run_id, probe_id, chosen, action)
    return jsonify({"ok": ok})

@app.route("/api/runs/<run_id>/export", methods=["POST"])
def api_export_run(run_id):
    filename, err = export_corrections(run_id)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, "filename": filename,
                    "path": os.path.join(DATASETS_DIR, filename)})

@app.route("/api/runs/<run_id>/compare")
def api_compare_runs(run_id):
    other_id = request.args.get("other")
    if not other_id:
        return jsonify({"error": "other run_id required"}), 400
    run_a = load_run(run_id)
    run_b = load_run(other_id)
    if not run_a or not run_b:
        return jsonify({"error": "One or both runs not found"}), 404

    comparison = {}
    all_cats = set(list(run_a.get("summary", {}).keys()) +
                   list(run_b.get("summary", {}).keys()))
    for cat in all_cats:
        a = run_a.get("summary", {}).get(cat, {})
        b = run_b.get("summary", {}).get(cat, {})
        pct_a = a.get("pass_pct", 0)
        pct_b = b.get("pass_pct", 0)
        comparison[cat] = {
            "run_a": pct_a, "run_b": pct_b,
            "delta": pct_b - pct_a,
            "direction": "improved" if pct_b > pct_a else ("regressed" if pct_b < pct_a else "unchanged"),
        }
    return jsonify({
        "run_a": {"id": run_id, "agent": run_a["agent"], "started_at": run_a["started_at"]},
        "run_b": {"id": other_id, "agent": run_b["agent"], "started_at": run_b["started_at"]},
        "comparison": comparison,
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
""",

"data/templates/index.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Probe</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;
  --text:#cdd6f4;--sub:#6c7086;--blue:#89b4fa;--green:#a6e3a1;
  --red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;
  --mono:'Courier New',monospace;
}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh}
.topbar{padding:12px 24px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.topbar .logo{font-size:15px;font-weight:700;color:var(--teal);letter-spacing:.5px}
.topbar .pill{font-size:11px;padding:3px 10px;border-radius:10px;font-weight:600}
.pill-idle{background:#313244;color:var(--sub)}
.pill-running{background:#2e2a1e;color:var(--yellow)}
.pill-complete{background:#1e3a2e;color:var(--green)}
.pill-error{background:#302030;color:var(--red)}
.spacer{flex:1}
.tab-row{display:flex;border-bottom:1px solid var(--border);background:var(--surface);padding:0 24px}
.tab{padding:11px 18px;font-size:13px;cursor:pointer;color:var(--sub);border-bottom:2px solid transparent}
.tab:hover{color:var(--text)}
.tab.active{color:var(--teal);border-bottom-color:var(--teal)}
.tab-content{display:none;padding:24px;max-width:1000px;margin:0 auto}
.tab-content.active{display:block}
.card{background:var(--overlay);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:16px}
.card-title{font-size:14px;font-weight:600;color:var(--teal);margin-bottom:4px}
.card-sub{font-size:12px;color:var(--sub);margin-bottom:14px}
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;color:var(--sub);margin-bottom:5px}
.field select,.field input,.field textarea{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:9px 11px;border-radius:6px;font-size:13px;outline:none;font-family:inherit}
.field select:focus,.field input:focus,.field textarea:focus{border-color:var(--teal)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{padding:9px 18px;border-radius:6px;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.btn-primary{background:var(--teal);color:var(--bg)}.btn-primary:hover{opacity:.9}
.btn-success{background:var(--green);color:var(--bg)}.btn-success:hover{opacity:.9}
.btn-danger{background:transparent;border:1px solid rgba(243,139,168,.4);color:var(--red)}.btn-danger:hover{background:rgba(243,139,168,.1)}
.btn-muted{background:var(--border);color:var(--text)}.btn-muted:hover{background:#45475a}
.btn:disabled{opacity:.4;cursor:not-allowed}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-bottom:14px}
.cat-item{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;display:flex;align-items:flex-start;gap:10px}
.cat-item:hover{border-color:var(--teal)}
.cat-item.selected{border-color:var(--teal);background:rgba(148,226,213,.06)}
.cat-item input[type=checkbox]{margin-top:2px;accent-color:var(--teal);flex-shrink:0}
.cat-name{font-size:13px;font-weight:600;margin-bottom:2px}
.cat-count{font-size:11px;color:var(--sub)}
.progress-wrap{margin:12px 0}
.progress-bar{height:8px;background:var(--border);border-radius:4px;overflow:hidden}
.progress-fill{height:100%;background:var(--teal);border-radius:4px;transition:width .4s ease}
.progress-label{display:flex;justify-content:space-between;font-size:11px;color:var(--sub);margin-top:4px}
.score-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:20px}
.score-card{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px;cursor:pointer}
.score-card:hover{border-color:var(--teal)}
.score-card .cat-label{font-size:12px;color:var(--sub);margin-bottom:6px}
.score-card .pct{font-size:28px;font-weight:700;margin-bottom:6px}
.pct-good{color:var(--green)}
.pct-warn{color:var(--yellow)}
.pct-bad{color:var(--red)}
.score-card .bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-bottom:6px}
.score-card .bar-fill{height:100%;border-radius:3px}
.score-card .counts{font-size:11px;color:var(--sub);display:flex;gap:10px}
.result-list{display:flex;flex-direction:column;gap:10px}
.result-item{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px}
.result-item.pass{border-left:3px solid var(--green)}
.result-item.fail{border-left:3px solid var(--red)}
.result-item.partial{border-left:3px solid var(--yellow)}
.result-item.review{border-left:3px solid var(--sub)}
.result-score{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;margin-bottom:8px}
.score-pass{background:rgba(166,227,161,.15);color:var(--green)}
.score-fail{background:rgba(243,139,168,.15);color:var(--red)}
.score-partial{background:rgba(249,226,175,.15);color:var(--yellow)}
.score-review{background:rgba(108,112,134,.2);color:var(--sub)}
.result-prompt{font-size:13px;font-weight:600;margin-bottom:8px}
.result-response{background:var(--surface);padding:10px;border-radius:6px;font-size:12px;font-family:var(--mono);margin-bottom:8px;max-height:120px;overflow-y:auto;white-space:pre-wrap;line-height:1.5}
.result-reasoning{font-size:12px;color:var(--sub);font-style:italic;margin-bottom:8px}
.result-expected{font-size:12px;color:var(--teal);margin-bottom:8px}
.correction-box{background:rgba(137,180,250,.06);border:1px solid rgba(137,180,250,.2);border-radius:6px;padding:10px;margin-top:8px}
.correction-label{font-size:11px;color:var(--blue);font-weight:600;margin-bottom:6px}
.correction-edit{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:4px;font-size:12px;outline:none;font-family:inherit;resize:vertical;min-height:60px}
.correction-edit:focus{border-color:var(--blue)}
.btn-row{display:flex;gap:8px;margin-top:8px}
.compare-row{display:grid;grid-template-columns:1fr 1fr;gap:2px;margin-bottom:4px;font-size:12px}
.compare-cat{padding:8px 12px;background:var(--bg);border-radius:4px 0 0 4px;color:var(--sub);font-weight:600}
.compare-a{padding:8px 12px;background:var(--surface)}
.compare-b{padding:8px 12px;background:var(--surface);border-radius:0 4px 4px 0}
.delta-improved{color:var(--green)}
.delta-regressed{color:var(--red)}
.delta-unchanged{color:var(--sub)}
.run-item{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px;display:flex;align-items:center;gap:12px;cursor:pointer}
.run-item:hover{border-color:var(--teal)}
.run-item .run-agent{font-size:13px;font-weight:600;color:var(--teal)}
.run-item .run-meta{font-size:11px;color:var(--sub)}
.run-item .run-score{font-size:18px;font-weight:700;margin-left:auto}
.info-box{background:rgba(148,226,213,.07);border:1px solid rgba(148,226,213,.2);border-radius:6px;padding:12px;font-size:12px;color:var(--sub);margin-bottom:14px;line-height:1.6}
.est-time{font-size:12px;color:var(--sub);margin-top:8px}
.section-label{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
</style>
</head><body>

<div class="topbar">
  <span class="logo">⬡ MODEL PROBE</span>
  <span class="pill pill-idle" id="statusPill">Idle</span>
  <div class="spacer"></div>
</div>

<div class="tab-row">
  <div class="tab active" onclick="showTab('run')">Run</div>
  <div class="tab" onclick="showTab('report')">Report</div>
  <div class="tab" onclick="showTab('compare')">Compare</div>
  <div class="tab" onclick="showTab('probes')">Probes</div>
</div>

<!-- ══ RUN TAB ══════════════════════════════════════════ -->
<div class="tab-content active" id="tab-run">
  <div class="card">
    <div class="card-title">Configure probe run</div>
    <div class="card-sub">Pick a model to test, a judge to evaluate responses, and which categories to probe.</div>

    <div class="grid2">
      <div class="field">
        <label>Model to test</label>
        <select id="runAgent"><option value="default">default</option></select>
      </div>
      <div class="field">
        <label>Judge agent (evaluates responses)</label>
        <select id="judgeAgent"><option value="default">default</option></select>
      </div>
    </div>

    <div class="info-box">
      💡 For best results, use a stronger or different agent as the judge than the model you are testing.
      A remote provider (Claude, GPT-4o) makes an excellent judge for Tier 2 empirical clarity probes.
    </div>

    <div class="field">
      <label>Probe categories</label>
      <div class="cat-grid" id="catGrid">
        <div style="color:var(--sub);font-size:13px">Loading probe banks...</div>
      </div>
    </div>

    <div class="est-time" id="estTime"></div>

    <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
      <button class="btn btn-primary" id="runBtn" onclick="startRun()">Run Probe</button>
      <button class="btn btn-danger" id="cancelBtn" onclick="cancelRun()" style="display:none">Cancel</button>
    </div>
  </div>

  <div id="runProgress" style="display:none">
    <div class="card">
      <div class="card-title">Running...</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        <div class="progress-label"><span id="progressLabel">Starting...</span><span id="progressPct">0%</span></div>
      </div>
      <div id="liveCats" style="margin-top:12px;font-size:12px;color:var(--sub)"></div>
    </div>
  </div>
</div>

<!-- ══ REPORT TAB ════════════════════════════════════════ -->
<div class="tab-content" id="tab-report">
  <div id="noReport" class="card">
    <div class="card-sub">No probe runs yet. Go to the Run tab to test a model.</div>
  </div>
  <div id="reportContent" style="display:none">
    <div class="card">
      <div class="card-title" id="reportTitle">Report</div>
      <div class="card-sub" id="reportMeta"></div>
      <div class="score-grid" id="scoreGrid"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="btn btn-success" id="exportBtn" onclick="exportCorrections()">Export corrections to Trainer</button>
        <button class="btn btn-muted" onclick="showReviewQueue()">Review partial responses</button>
      </div>
      <div id="exportStatus" style="font-size:12px;color:var(--sub);margin-top:8px"></div>
    </div>

    <div id="drillDown" style="display:none">
      <div class="card">
        <div class="card-title" id="drillTitle">Category detail</div>
        <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
          <button class="btn btn-muted" onclick="filterResults('all')" id="filterAll">All</button>
          <button class="btn btn-danger" onclick="filterResults('fail')" id="filterFail">Failures</button>
          <button class="btn btn-muted" onclick="filterResults('partial')" id="filterPartial">Partial</button>
          <button class="btn btn-muted" onclick="filterResults('pass')" id="filterPass">Passes</button>
        </div>
        <div class="result-list" id="resultList"></div>
      </div>
    </div>

    <div id="reviewQueue" style="display:none">
      <div class="card">
        <div class="card-title">Human review queue</div>
        <div class="card-sub">These responses need your input before they can be used for training.</div>
        <div class="result-list" id="reviewList"></div>
      </div>
    </div>
  </div>
</div>

<!-- ══ COMPARE TAB ══════════════════════════════════════ -->
<div class="tab-content" id="tab-compare">
  <div class="card">
    <div class="card-title">Compare two runs</div>
    <div class="card-sub">See exactly what changed between runs — improved, regressed, or unchanged.</div>
    <div class="grid2">
      <div class="field">
        <label>Run A (baseline)</label>
        <select id="compareA"><option value="">Select a run...</option></select>
      </div>
      <div class="field">
        <label>Run B (compare to)</label>
        <select id="compareB"><option value="">Select a run...</option></select>
      </div>
    </div>
    <button class="btn btn-primary" onclick="runCompare()">Compare</button>
  </div>
  <div id="compareResult" style="display:none">
    <div class="card">
      <div class="card-title" id="compareTitle">Comparison</div>
      <div style="margin-bottom:12px">
        <div class="compare-row" style="font-weight:600;margin-bottom:6px">
          <div class="compare-cat">Category</div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px">
            <div class="compare-a" id="compareALabel" style="font-weight:600">Run A</div>
            <div class="compare-b" id="compareBLabel" style="font-weight:600">Run B</div>
            <div style="padding:8px 12px;background:var(--surface);font-weight:600">Change</div>
          </div>
        </div>
        <div id="compareRows"></div>
      </div>
    </div>
  </div>
</div>

<!-- ══ PROBES TAB ════════════════════════════════════════ -->
<div class="tab-content" id="tab-probes">
  <div class="card">
    <div class="card-title">Probe bank</div>
    <div class="card-sub">Browse loaded probe banks. Drop .jsonl files into ~/WickermanSupport/datasets/probes/ to add more.</div>
    <div id="bankList"><div style="color:var(--sub);font-size:13px">Loading...</div></div>
  </div>

  <div class="card">
    <div class="card-title">Add a custom probe</div>
    <div class="field">
      <label>Question (use {placeholder} for variable injection)</label>
      <input id="cpPrompt" placeholder="e.g. How did {subject} develop?">
    </div>
    <div class="field">
      <label>Variables (comma-separated, optional)</label>
      <input id="cpVars" placeholder="e.g. humans, life on Earth, homo sapiens">
    </div>
    <div class="field">
      <label>Expected behavior</label>
      <textarea id="cpExpected" rows="2" placeholder="Describe what a good response looks like..."></textarea>
    </div>
    <div class="field">
      <label>Scoring rubric</label>
      <textarea id="cpRubric" rows="2" placeholder="FAIL if... PASS if..."></textarea>
    </div>
    <div class="field">
      <label>Category</label>
      <input id="cpCat" placeholder="e.g. empirical_clarity_custom">
    </div>
    <button class="btn btn-primary" onclick="addCustomProbe()">Add probe</button>
    <span id="cpStatus" style="font-size:12px;color:var(--sub);margin-left:10px"></span>
  </div>

  <div class="card">
    <div class="card-title">AI-generate probes</div>
    <div class="card-sub">Describe what you want to test and an agent will generate probe questions for review.</div>
    <div class="field">
      <label>What do you want to test?</label>
      <textarea id="genDesc" rows="2" placeholder="e.g. Test whether the model correctly explains how vaccines work without anti-vaccine bias"></textarea>
    </div>
    <div class="grid2">
      <div class="field">
        <label>Category name</label>
        <input id="genCat" placeholder="e.g. medical_accuracy">
      </div>
      <div class="field">
        <label>Number of probes (max 10)</label>
        <input id="genCount" type="number" value="5" min="1" max="10">
      </div>
    </div>
    <button class="btn btn-primary" id="genBtn" onclick="generateProbes()">Generate probes</button>
    <div id="generatedProbes" style="margin-top:14px;display:none">
      <div class="section-label">Review generated probes</div>
      <div id="genProbeList"></div>
      <button class="btn btn-success" onclick="saveGeneratedProbes()" style="margin-top:10px">Save approved probes</button>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;
let currentRunId = null;
let pollTimer = null;
let currentReportRunId = null;
let currentDrillCategory = null;
let currentDrillResults = [];
let pendingGeneratedProbes = [];

// ── Tabs ──────────────────────────────────────────────────
function showTab(t) {
  document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  const tabs = ['run','report','compare','probes'];
  const idx = tabs.indexOf(t);
  if(idx >= 0) document.querySelectorAll('.tab')[idx].classList.add('active');
  if(t==='report') loadReport();
  if(t==='compare') loadRunSelects();
  if(t==='probes') loadBankList();
}

// ── Init ──────────────────────────────────────────────────
async function loadAgents() {
  try {
    const r = await fetch(API+'/api/agents').then(r=>r.json());
    const agents = r.agents || [];
    ['runAgent','judgeAgent'].forEach(id => {
      const sel = document.getElementById(id);
      const cur = sel.value;
      sel.innerHTML = '<option value="default">default</option>' +
        agents.filter(a=>a!=='default').map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join('');
      if(cur && [...sel.options].some(o=>o.value===cur)) sel.value = cur;
    });
  } catch(e) {}
}

async function loadCategories() {
  try {
    const r = await fetch(API+'/api/probes/categories').then(r=>r.json());
    const cats = r.categories || {};
    const grid = document.getElementById('catGrid');
    if(!Object.keys(cats).length) {
      grid.innerHTML = '<div style="color:var(--sub);font-size:13px">No probe banks found. Drop .jsonl files into ~/WickermanSupport/datasets/probes/</div>';
      return;
    }
    grid.innerHTML = Object.entries(cats).map(([cat, count]) =>
      `<div class="cat-item selected" id="cat_${esc(cat)}" onclick="toggleCat('${esc(cat)}',this)">
        <input type="checkbox" checked onchange="toggleCat('${esc(cat)}',this.parentElement)">
        <div><div class="cat-name">${esc(catLabel(cat))}</div><div class="cat-count">${count} probes</div></div>
      </div>`
    ).join('');
    updateEstTime();
  } catch(e) {}
}

function catLabel(cat) {
  return cat.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}

function toggleCat(cat, el) {
  el.classList.toggle('selected');
  const cb = el.querySelector('input[type=checkbox]');
  if(cb) cb.checked = el.classList.contains('selected');
  updateEstTime();
}

async function updateEstTime() {
  const selected = getSelectedCats();
  if(!selected.length) { document.getElementById('estTime').textContent=''; return; }
  try {
    const r = await fetch(API+'/api/probes/categories').then(r=>r.json());
    const cats = r.categories || {};
    let total = selected.reduce((s,c) => s + (cats[c]||0), 0);
    const mins = Math.ceil(total * 8 / 60);
    document.getElementById('estTime').textContent = `Estimated time: ~${mins} minute${mins!==1?'s':''} (${total} probes)`;
  } catch(e) {}
}

function getSelectedCats() {
  return [...document.querySelectorAll('.cat-item.selected')]
    .map(el => el.id.replace('cat_',''));
}

// ── Run ───────────────────────────────────────────────────
async function startRun() {
  const agent = document.getElementById('runAgent').value;
  const judge = document.getElementById('judgeAgent').value;
  const cats = getSelectedCats();
  if(!cats.length) { alert('Select at least one probe category.'); return; }

  document.getElementById('runBtn').disabled = true;
  document.getElementById('cancelBtn').style.display = 'inline-block';
  document.getElementById('runProgress').style.display = 'block';
  document.getElementById('progressFill').style.width = '0%';
  document.getElementById('progressLabel').textContent = 'Starting...';
  document.getElementById('progressPct').textContent = '0%';

  const r = await fetch(API+'/api/runs/start', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({agent, judge_agent: judge, categories: cats})
  }).then(r=>r.json());

  if(r.error) { alert(r.error); resetRunUI(); return; }
  currentRunId = r.run_id;
  document.getElementById('statusPill').textContent = 'Running';
  document.getElementById('statusPill').className = 'pill pill-running';

  pollTimer = setInterval(pollRun, 2000);
}

async function pollRun() {
  if(!currentRunId) return;
  try {
    const r = await fetch(`${API}/api/runs/${currentRunId}/status`).then(r=>r.json());
    const pct = r.total > 0 ? Math.round(r.complete/r.total*100) : 0;
    document.getElementById('progressFill').style.width = pct+'%';
    document.getElementById('progressLabel').textContent = `${r.complete} / ${r.total} probes`;
    document.getElementById('progressPct').textContent = pct+'%';

    // Live category scores
    if(r.summary && Object.keys(r.summary).length) {
      document.getElementById('liveCats').innerHTML = Object.entries(r.summary)
        .map(([cat,s]) => `<span style="margin-right:16px">${catLabel(cat)}: <strong>${s.pass_pct}%</strong></span>`)
        .join('');
    }

    if(r.status === 'complete' || r.status === 'error' || r.status === 'cancelled') {
      clearInterval(pollTimer);
      pollTimer = null;
      const pill = document.getElementById('statusPill');
      if(r.status === 'complete') {
        pill.textContent = 'Complete'; pill.className = 'pill pill-complete';
        currentReportRunId = currentRunId;
        showTab('report');
      } else {
        pill.textContent = r.status; pill.className = 'pill pill-error';
      }
      resetRunUI();
    }
  } catch(e) {}
}

async function cancelRun() {
  if(!currentRunId) return;
  await fetch(`${API}/api/runs/${currentRunId}/cancel`, {method:'POST'});
  clearInterval(pollTimer);
  resetRunUI();
}

function resetRunUI() {
  document.getElementById('runBtn').disabled = false;
  document.getElementById('cancelBtn').style.display = 'none';
}

// ── Report ────────────────────────────────────────────────
async function loadReport(runId) {
  const rid = runId || currentReportRunId;
  if(!rid) {
    // Show list of past runs
    const runs = await fetch(API+'/api/runs').then(r=>r.json());
    if(!runs.runs?.length) return;
    currentReportRunId = runs.runs[0].id;
    return loadReport(currentReportRunId);
  }
  currentReportRunId = rid;

  const r = await fetch(`${API}/api/runs/${rid}/status`).then(r=>r.json());
  if(r.error || r.status === 'starting' || r.status === 'running') return;

  document.getElementById('noReport').style.display = 'none';
  document.getElementById('reportContent').style.display = 'block';
  document.getElementById('drillDown').style.display = 'none';
  document.getElementById('reviewQueue').style.display = 'none';

  document.getElementById('reportTitle').textContent = `Report — ${r.agent}`;
  document.getElementById('reportMeta').textContent =
    `${r.total} probes  |  Run ${r.started_at}  |  ${r.complete} complete`;

  const grid = document.getElementById('scoreGrid');
  grid.innerHTML = Object.entries(r.summary || {}).map(([cat, s]) => {
    const pct = s.pass_pct || 0;
    const color = pct >= 85 ? 'var(--green)' : pct >= 65 ? 'var(--yellow)' : 'var(--red)';
    const pctClass = pct >= 85 ? 'pct-good' : pct >= 65 ? 'pct-warn' : 'pct-bad';
    return `<div class="score-card" onclick="drillInto('${esc(cat)}')">
      <div class="cat-label">${catLabel(cat)}</div>
      <div class="pct ${pctClass}">${pct}%</div>
      <div class="bar"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="counts">
        <span style="color:var(--green)">✓ ${s.pass||0}</span>
        <span style="color:var(--red)">✗ ${s.fail||0}</span>
        <span style="color:var(--yellow)">~ ${s.partial||0}</span>
        <span style="color:var(--sub)">? ${s.review||0}</span>
      </div>
    </div>`;
  }).join('');
}

async function drillInto(cat) {
  currentDrillCategory = cat;
  const r = await fetch(`${API}/api/runs/${currentReportRunId}/results?category=${encodeURIComponent(cat)}`).then(r=>r.json());
  currentDrillResults = r.results || [];
  document.getElementById('drillTitle').textContent = catLabel(cat) + ' — All results';
  document.getElementById('drillDown').style.display = 'block';
  document.getElementById('reviewQueue').style.display = 'none';
  renderResults(currentDrillResults);
  document.getElementById('drillDown').scrollIntoView({behavior:'smooth'});
}

function filterResults(scoreFilter) {
  let filtered = currentDrillResults;
  if(scoreFilter !== 'all') filtered = filtered.filter(r=>r.score===scoreFilter);
  renderResults(filtered);
}

function renderResults(results) {
  const el = document.getElementById('resultList');
  if(!results.length) { el.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:8px">No results in this filter.</div>'; return; }
  el.innerHTML = results.map(r => {
    const isFail = r.score === 'fail';
    const isPartial = r.score === 'partial' || r.score === 'review';
    const correctionHtml = (isFail || isPartial) ? `
      <div class="correction-box">
        <div class="correction-label">${isFail ? 'Suggested correction (will be sent to Trainer)' : 'Needs your review — rewrite the correct response:'}</div>
        <textarea class="correction-edit" id="chosen_${esc(r.probe_id)}" rows="3">${esc(r.expected_behavior)}</textarea>
        <div class="btn-row">
          <button class="btn btn-success" style="font-size:11px;padding:5px 10px" onclick="approveResult('${esc(r.probe_id)}','approve')">✓ Approve</button>
          <button class="btn btn-muted" style="font-size:11px;padding:5px 10px" onclick="approveResult('${esc(r.probe_id)}','skip')">Skip</button>
        </div>
      </div>` : '';
    return `<div class="result-item ${r.score}">
      <span class="result-score score-${r.score}">${scoreIcon(r.score)} ${r.score.toUpperCase()}</span>
      <div class="result-prompt">${esc(r.prompt_rendered || r.prompt)}${r.var_used?` <span style="color:var(--sub);font-weight:400;font-size:11px">[${esc(r.var_used)}]</span>`:''}</div>
      <div class="result-response">${esc(r.response || '(no response)')}</div>
      ${r.reasoning ? `<div class="result-reasoning">Judge: ${esc(r.reasoning)}</div>` : ''}
      ${r.score !== 'pass' ? `<div class="result-expected">Expected: ${esc(r.expected_behavior)}</div>` : ''}
      ${correctionHtml}
    </div>`;
  }).join('');
}

function scoreIcon(s) {
  return s==='pass'?'✓':s==='fail'?'✗':s==='partial'?'~':'?';
}

async function approveResult(probeId, action) {
  const chosen = document.getElementById('chosen_'+probeId)?.value || '';
  await fetch(`${API}/api/runs/${currentReportRunId}/approve`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({probe_id: probeId, chosen_response: chosen, action})
  });
  const el = document.querySelector(`#chosen_${probeId}`)?.closest('.result-item');
  if(el) el.style.opacity = '0.4';
}

async function exportCorrections() {
  const btn = document.getElementById('exportBtn');
  const status = document.getElementById('exportStatus');
  btn.disabled = true; status.textContent = 'Exporting...';
  const r = await fetch(`${API}/api/runs/${currentReportRunId}/export`, {method:'POST'}).then(r=>r.json());
  btn.disabled = false;
  if(r.ok) {
    status.textContent = `Saved: ${r.filename} — open the Trainer to use it.`;
    status.style.color = 'var(--green)';
  } else {
    status.textContent = 'Error: '+(r.error||'unknown');
    status.style.color = 'var(--red)';
  }
}

async function showReviewQueue() {
  const r = await fetch(`${API}/api/runs/${currentReportRunId}/review`).then(r=>r.json());
  const items = r.items || [];
  document.getElementById('reviewQueue').style.display = 'block';
  document.getElementById('drillDown').style.display = 'none';
  const el = document.getElementById('reviewList');
  if(!items.length) {
    el.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:8px">No items need review.</div>';
    return;
  }
  currentDrillResults = items;
  renderResults(items);
  el.innerHTML = document.getElementById('resultList').innerHTML;
}

// ── Compare ───────────────────────────────────────────────
async function loadRunSelects() {
  const r = await fetch(API+'/api/runs').then(r=>r.json());
  const runs = r.runs || [];
  const opts = runs.map(r => `<option value="${r.id}">${r.agent} — ${r.started_at} (${r.scores ? Object.values(r.scores).reduce((s,c)=>s+(c.pass||0),0) : '?'} pass)</option>`).join('');
  document.getElementById('compareA').innerHTML = '<option value="">Select...</option>' + opts;
  document.getElementById('compareB').innerHTML = '<option value="">Select...</option>' + opts;
}

async function runCompare() {
  const a = document.getElementById('compareA').value;
  const b = document.getElementById('compareB').value;
  if(!a || !b) { alert('Select two runs to compare.'); return; }
  const r = await fetch(`${API}/api/runs/${a}/compare?other=${b}`).then(r=>r.json());
  if(r.error) { alert(r.error); return; }

  document.getElementById('compareResult').style.display = 'block';
  document.getElementById('compareTitle').textContent =
    `${r.run_a.agent} vs ${r.run_b.agent}`;
  document.getElementById('compareALabel').textContent = r.run_a.started_at;
  document.getElementById('compareBLabel').textContent = r.run_b.started_at;

  document.getElementById('compareRows').innerHTML = Object.entries(r.comparison)
    .sort((a,b) => Math.abs(b[1].delta) - Math.abs(a[1].delta))
    .map(([cat, c]) => {
      const deltaClass = c.direction === 'improved' ? 'delta-improved' :
                         c.direction === 'regressed' ? 'delta-regressed' : 'delta-unchanged';
      const deltaStr = c.delta > 0 ? '+'+c.delta+'%' : c.delta+'%';
      return `<div style="display:grid;grid-template-columns:200px 1fr 1fr 1fr;gap:2px;margin-bottom:2px;font-size:12px">
        <div style="padding:8px 12px;background:var(--bg);color:var(--sub);font-weight:600">${catLabel(cat)}</div>
        <div style="padding:8px 12px;background:var(--surface)">${c.run_a}%</div>
        <div style="padding:8px 12px;background:var(--surface)">${c.run_b}%</div>
        <div class="${deltaClass}" style="padding:8px 12px;background:var(--surface);font-weight:600">${deltaStr} ${c.direction==='unchanged'?'':'↑'}</div>
      </div>`;
    }).join('');
}

// ── Probes tab ────────────────────────────────────────────
async function loadBankList() {
  const r = await fetch(API+'/api/probes').then(r=>r.json());
  const el = document.getElementById('bankList');
  if(!Object.keys(r.banks||{}).length) {
    el.innerHTML = '<div style="color:var(--sub);font-size:13px">No probe banks loaded.</div>';
    return;
  }
  el.innerHTML = Object.entries(r.banks).map(([name,info]) =>
    `<div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1"><strong style="font-size:13px">${esc(name)}</strong>
      <div style="font-size:11px;color:var(--sub);margin-top:2px">${info.total} probes — ${Object.entries(info.categories).map(([c,n])=>`${catLabel(c)}: ${n}`).join(', ')}</div>
      </div>
    </div>`
  ).join('');
}

async function addCustomProbe() {
  const prompt = document.getElementById('cpPrompt').value.trim();
  const vars = document.getElementById('cpVars').value.trim();
  const expected = document.getElementById('cpExpected').value.trim();
  const rubric = document.getElementById('cpRubric').value.trim();
  const cat = document.getElementById('cpCat').value.trim();
  if(!prompt||!expected||!rubric||!cat) { alert('All fields required.'); return; }
  const body = {prompt, expected_behavior: expected, rubric, category: cat};
  if(vars) body.vars = vars.split(',').map(v=>v.trim()).filter(Boolean);
  const r = await fetch(API+'/api/probes/custom', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
  }).then(r=>r.json());
  if(r.ok) {
    document.getElementById('cpStatus').textContent = 'Probe added!';
    document.getElementById('cpStatus').style.color = 'var(--green)';
    ['cpPrompt','cpVars','cpExpected','cpRubric','cpCat'].forEach(id=>document.getElementById(id).value='');
    loadCategories();
  } else {
    document.getElementById('cpStatus').textContent = 'Error: '+r.error;
    document.getElementById('cpStatus').style.color = 'var(--red)';
  }
}

async function generateProbes() {
  const desc = document.getElementById('genDesc').value.trim();
  const cat = document.getElementById('genCat').value.trim() || 'custom_domain';
  const count = parseInt(document.getElementById('genCount').value) || 5;
  const agent = document.getElementById('runAgent').value;
  if(!desc) { alert('Describe what you want to test.'); return; }
  const btn = document.getElementById('genBtn');
  btn.disabled = true; btn.textContent = 'Generating...';
  const r = await fetch(API+'/api/probes/generate', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({description: desc, category: cat, count, agent})
  }).then(r=>r.json());
  btn.disabled = false; btn.textContent = 'Generate probes';
  if(r.error) { alert(r.error); return; }
  pendingGeneratedProbes = r.probes || [];
  document.getElementById('generatedProbes').style.display = 'block';
  document.getElementById('genProbeList').innerHTML = pendingGeneratedProbes.map((p,i) =>
    `<div class="result-item review" style="margin-bottom:8px">
      <input type="checkbox" id="gencheck_${i}" checked style="accent-color:var(--teal);margin-right:8px">
      <strong style="font-size:13px">${esc(p.prompt)}</strong>
      <div style="font-size:11px;color:var(--sub);margin-top:4px">${esc(p.expected_behavior)}</div>
    </div>`
  ).join('');
}

async function saveGeneratedProbes() {
  const toSave = pendingGeneratedProbes.filter((_,i) => {
    const cb = document.getElementById('gencheck_'+i);
    return cb && cb.checked;
  });
  for(const p of toSave) {
    await fetch(API+'/api/probes/custom', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p)
    });
  }
  document.getElementById('generatedProbes').style.display = 'none';
  document.getElementById('genDesc').value = '';
  alert(`${toSave.length} probe${toSave.length!==1?'s':''} saved.`);
  loadCategories();
  loadBankList();
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Init ──────────────────────────────────────────────────
loadAgents();
loadCategories();
setInterval(loadAgents, 30000);
</script>
</body></html>
""",

}  # end WM_PROBE_FILES

WM_PROBE["files"] = WM_PROBE_FILES

PLUGIN_HOST = ("127.0.0.1", "probe.wickerman.local")
