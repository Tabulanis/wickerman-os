"""
Wickerman OS v5.6.0 - Chat plugin manifest.
Thin conversation UI. Agents (prompts, RAG, settings) managed by the Model Router.
"""

WM_CHAT = {
    "name": "Chat",
    "description": "Conversation UI for Wickerman agents",
    "icon": "chat",
    "build": True,
    "build_context": "data",
    "container_name": "wm-chat",
    "url": "http://chat.wickerman.local",
    "ports": [5000],
    "gpu": False,
    "env": [
        "LLAMA_API=http://wm-llama:8080"
    ],
    "volumes": ["{self}/app_data:/data"],
    "nginx_host": "chat.wickerman.local",
    "help": "## Chat\nConversation UI for Wickerman agents.\n\n**Agents** are configured in the Model Router (system prompt, RAG, settings).\nChat just picks an agent and manages conversation history.\n\n**Node API:** `/node/schema` and `/node/execute` for wm-flow integration."
}

WM_CHAT_FILES = {
    "data/Dockerfile": r"""FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir flask==3.0.* requests==2.32.* gunicorn==22.*
WORKDIR /app
ARG CACHEBUST=1
COPY . .
EXPOSE 5000
HEALTHCHECK --interval=10s --timeout=3s CMD curl -sf http://localhost:5000/health || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
""",
}

WM_CHAT_FILES["data/app.py"] = 'from flask import Flask, render_template, request, jsonify\nimport os, json, requests, uuid, time, glob\n\napp = Flask(__name__)\nLLAMA_API = os.environ.get("LLAMA_API", "http://wm-llama:8080")\nDATA_DIR = "/data"\nCONV_DIR = os.path.join(DATA_DIR, "conversations")\nos.makedirs(CONV_DIR, exist_ok=True)\n\ndef _conv_path(cid):\n    if not cid.isalnum(): raise ValueError("Invalid Conversation ID")\n    return os.path.join(CONV_DIR, cid + ".json")\n\ndef save_conversation(conv):\n    target = _conv_path(conv["id"])\n    tmp = target + ".tmp"\n    with open(tmp, "w") as f: json.dump(conv, f)\n    os.replace(tmp, target)\n\ndef load_conversation(cid):\n    p = _conv_path(cid)\n    if os.path.isfile(p):\n        with open(p) as f: return json.load(f)\n    return None\n\ndef list_conversations():\n    convs = []\n    for p in glob.glob(os.path.join(CONV_DIR, "*.json")):\n        try:\n            with open(p) as f: c = json.load(f)\n            convs.append({"id": c["id"], "title": c.get("title","Untitled"), "agent": c.get("agent","default"), "created": c.get("created",0), "message_count": len(c.get("messages",[]))})\n        except: pass\n    convs.sort(key=lambda c: c.get("created",0), reverse=True)\n    return convs\n\ndef delete_conversation(cid):\n    p = _conv_path(cid)\n    if os.path.isfile(p):\n        os.remove(p)\n        return True\n    return False\n\n@app.route("/")\ndef index(): return render_template("index.html")\n\n@app.route("/health")\ndef health(): return jsonify({"status": "ok"})\n\n@app.route("/api/agents")\ndef api_agents():\n    try:\n        r = requests.get(LLAMA_API + "/v1/models", timeout=5)\n        return jsonify(r.json())\n    except: return jsonify({"data": []})\n\n@app.route("/api/conversations", methods=["GET"])\ndef api_list_convs(): return jsonify({"conversations": list_conversations()})\n\n@app.route("/api/conversations", methods=["POST"])\ndef api_create_conv():\n    d = request.json or {}\n    cid = str(uuid.uuid4())[:8]\n    conv = {"id": cid, "title": d.get("title", "New Chat"), "agent": d.get("agent", "default"), "messages": [], "created": time.time()}\n    save_conversation(conv)\n    return jsonify(conv)\n\n@app.route("/api/conversations/<cid>", methods=["GET"])\ndef api_get_conv(cid):\n    conv = load_conversation(cid)\n    if not conv: return jsonify({"error": "Not found"}), 404\n    return jsonify(conv)\n\n@app.route("/api/conversations/<cid>", methods=["PUT"])\ndef api_update_conv(cid):\n    conv = load_conversation(cid)\n    if not conv: return jsonify({"error": "Not found"}), 404\n    d = request.json or {}\n    for key in ["title", "agent"]:\n        if key in d: conv[key] = d[key]\n    save_conversation(conv)\n    return jsonify(conv)\n\n@app.route("/api/conversations/<cid>", methods=["DELETE"])\ndef api_delete_conv(cid):\n    if delete_conversation(cid): return jsonify({"ok": True})\n    return jsonify({"error": "Not found"}), 404\n\n@app.route("/api/conversations/<cid>/chat", methods=["POST"])\ndef api_chat(cid):\n    conv = load_conversation(cid)\n    if not conv: return jsonify({"error": "Not found"}), 404\n    d = request.json or {}\n    user_msg = d.get("message", "").strip()\n    if not user_msg: return jsonify({"error": "Empty message"}), 400\n    if conv["title"] == "New Chat" and len(conv["messages"]) == 0:\n        conv["title"] = user_msg[:50] + ("..." if len(user_msg) > 50 else "")\n    api_msgs = list(conv["messages"]) + [{"role": "user", "content": user_msg}]\n    try:\n        payload = {"model": conv.get("agent", "default"), "messages": api_msgs, "stream": False}\n        r = requests.post(LLAMA_API + "/v1/chat/completions", json=payload, timeout=300)\n        r.raise_for_status()\n        data = r.json()\n        reply = data["choices"][0]["message"]["content"]\n        usage = data.get("usage", {})\n        rag_info = data.get("_rag", {})\n        conv["messages"].append({"role": "user", "content": user_msg})\n        conv["messages"].append({"role": "assistant", "content": reply})\n        save_conversation(conv)\n        return jsonify({"response": reply, "usage": usage, "context": {"trimmed": rag_info.get("trimmed", 0), "rag_chunks": rag_info.get("chunks_used", 0), "archived": rag_info.get("archived", 0)}})\n    except requests.exceptions.ConnectionError:\n        return jsonify({"error": "Cannot reach Model Router. Is wm-llama running?"}), 502\n    except Exception as e:\n        return jsonify({"error": str(e)}), 500\n\n@app.route("/api/conversations/<cid>/context", methods=["GET"])\ndef api_context_info(cid):\n    conv = load_conversation(cid)\n    if not conv: return jsonify({"error": "Not found"}), 404\n    rag_chunks = 0\n    try:\n        r = requests.get(LLAMA_API + "/api/rag/" + conv.get("agent", "default") + "/status", timeout=3)\n        if r.ok: rag_chunks = r.json().get("chunks", 0)\n    except: pass\n    return jsonify({"messages": len(conv.get("messages", [])), "rag_chunks": rag_chunks})\n\n@app.route("/api/conversations/<cid>/clear", methods=["POST"])\ndef api_clear_conv(cid):\n    conv = load_conversation(cid)\n    if not conv: return jsonify({"error": "Not found"}), 404\n    conv["messages"] = []\n    save_conversation(conv)\n    return jsonify({"ok": True})\n\n@app.route("/node/schema")\ndef node_schema():\n    return jsonify({"name": "chat", "description": "Send a message to a Wickerman agent", "inputs": [{"name": "user_message", "type": "string", "required": True}, {"name": "agent", "type": "string", "required": False, "default": "default"}, {"name": "conversation_history", "type": "array", "required": False}], "outputs": [{"name": "response", "type": "string"}, {"name": "tokens_used", "type": "object"}, {"name": "messages", "type": "array"}]})\n\n@app.route("/node/execute", methods=["POST"])\ndef node_execute():\n    d = request.json or {}\n    user_msg = d.get("user_message", "")\n    if not user_msg: return jsonify({"error": "user_message is required"}), 400\n    history = d.get("conversation_history", [])\n    messages = history + [{"role": "user", "content": user_msg}]\n    agent = d.get("agent", "default")\n    try:\n        payload = {"model": agent, "messages": messages, "stream": False}\n        r = requests.post(LLAMA_API + "/v1/chat/completions", json=payload, timeout=300)\n        r.raise_for_status()\n        data = r.json()\n        reply = data["choices"][0]["message"]["content"]\n        messages.append({"role": "assistant", "content": reply})\n        return jsonify({"response": reply, "tokens_used": data.get("usage", {}), "messages": messages})\n    except Exception as e:\n        return jsonify({"error": str(e)}), 500\n\nif __name__ == "__main__": app.run(host="0.0.0.0", port=5000)\n'

WM_CHAT_FILES["data/templates/index.html"] = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wickerman Chat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;--text:#cdd6f4;--subtext:#6c7086;--blue:#89b4fa;--green:#a6e3a1;--red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;--mono:'Courier New',monospace}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;height:100vh;display:flex;overflow:hidden}
.sidebar{width:260px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
.sidebar .hdr{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.sidebar .hdr .icon{width:24px;height:24px;background:var(--mauve);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--bg);font-weight:700}
.sidebar .hdr span{font-size:14px;font-weight:700;color:var(--mauve);letter-spacing:.5px}
.new-chat{margin:12px;padding:10px;text-align:center;border:1px dashed var(--border);border-radius:8px;color:var(--subtext);font-size:13px;cursor:pointer}
.new-chat:hover{border-color:var(--blue);color:var(--blue)}
.conv-list{flex:1;overflow-y:auto;padding:0 8px}
.conv-item{padding:10px 12px;border-radius:8px;cursor:pointer;margin-bottom:2px;display:flex;justify-content:space-between;align-items:center}
.conv-item:hover{background:var(--overlay)}
.conv-item.active{background:var(--overlay);border:1px solid var(--mauve)}
.conv-item .info{overflow:hidden;flex:1}
.conv-item .title{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conv-item .meta{font-size:10px;color:var(--subtext);margin-top:2px}
.conv-item .del{color:var(--subtext);font-size:14px;cursor:pointer;padding:2px 6px;border-radius:4px;opacity:0;flex-shrink:0}
.conv-item:hover .del{opacity:1}
.conv-item .del:hover{color:var(--red);background:rgba(243,139,168,.1)}
.sidebar-bottom{border-top:1px solid var(--border);padding:12px}
.sidebar-bottom .lbl{font-size:10px;color:var(--subtext);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.sidebar-bottom select{width:100%;background:var(--overlay);border:1px solid var(--border);color:var(--text);padding:6px 8px;border-radius:6px;font-size:12px;outline:none}
.sidebar-bottom select:focus{border-color:var(--blue)}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.topbar{padding:10px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;background:var(--surface)}
.topbar .conv-title{font-size:15px;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar .badge{font-size:11px;background:var(--overlay);padding:4px 10px;border-radius:12px;color:var(--teal);border:1px solid var(--border);font-family:var(--mono)}
.topbar .status{font-size:11px;padding:3px 8px;border-radius:8px}
.topbar .status.ok{background:rgba(166,227,161,.1);color:var(--green)}
.topbar .status.err{background:rgba(243,139,168,.1);color:var(--red)}
.messages{flex:1;overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:75%;padding:12px 16px;font-size:14px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word}
.msg.user{align-self:flex-end;background:var(--blue);color:var(--bg);border-radius:16px 16px 4px 16px;font-weight:500}
.msg.assistant{align-self:flex-start;background:var(--overlay);border:1px solid var(--border);border-radius:16px 16px 16px 4px}
.msg.error{align-self:center;background:rgba(243,139,168,.1);color:var(--red);font-size:12px;border-radius:8px;padding:8px 16px}
.msg.info{align-self:center;font-size:11px;border-radius:8px;padding:6px 12px}
.msg code{background:var(--bg);padding:2px 6px;border-radius:4px;font-family:var(--mono);font-size:13px}
.msg pre{background:var(--bg);padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0;font-family:var(--mono);font-size:12px;border:1px solid var(--border);white-space:pre-wrap}
.typing{color:var(--subtext)}.typing::after{content:'...';animation:dots 1.5s infinite}
@keyframes dots{0%,20%{content:'.'}40%{content:'..'}60%,100%{content:'...'}}
.input-area{padding:14px 20px;border-top:1px solid var(--border);display:flex;gap:10px;background:var(--surface)}
.input-area textarea{flex:1;padding:12px 16px;border-radius:12px;border:1px solid var(--border);background:var(--overlay);color:var(--text);font-size:14px;outline:none;resize:none;height:52px;font-family:inherit;line-height:1.4}
.input-area textarea:focus{border-color:var(--blue)}
.input-area button{padding:12px 24px;border-radius:12px;border:none;background:var(--blue);color:var(--bg);font-weight:700;font-size:14px;cursor:pointer}
.input-area button:hover{background:var(--teal)}
.input-area button:disabled{opacity:.4;cursor:default}
.empty-state{flex:1;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:var(--subtext)}
.empty-state .big{font-size:48px;opacity:.3}
</style></head><body>
<div class="sidebar">
  <div class="hdr"><div class="icon">C</div><span>WICKERMAN CHAT</span></div>
  <div class="new-chat" onclick="newConv()">+ New Chat</div>
  <div class="conv-list" id="convList"></div>
  <div class="sidebar-bottom">
    <div class="lbl">Agent</div>
    <select id="selAgent" onchange="updateAgent()"></select>
  </div>
</div>
<div class="main">
  <div class="topbar">
    <div class="conv-title" id="convTitle">Select or create a chat</div>
    <div id="ctxBar" style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--subtext)">
      <span id="ctxLabel">--</span>
      <div style="width:80px;height:6px;background:var(--border);border-radius:3px;overflow:hidden"><div id="ctxFill" style="height:100%;width:0%;background:var(--green);border-radius:3px;transition:width .3s"></div></div>
    </div>
    <span class="badge" id="agentBadge">--</span>
    <span class="status" id="routerStatus">...</span>
  </div>
  <div class="messages" id="msgs"><div class="empty-state"><div class="big">&#x1f4ac;</div><p>Create a new chat or select one from the sidebar</p></div></div>
  <div class="input-area">
    <textarea id="inp" placeholder="Type a message..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
    <button id="sendBtn" onclick="send()">Send</button>
  </div>
</div>
<script>
let conversations=[],activeConvId=null,activeConv=null,sending=false;
async function init(){await checkRouter();await loadAgents();await loadConversations();if(conversations.length>0)selectConv(conversations[0].id)}
async function checkRouter(){const st=document.getElementById('routerStatus');try{const r=await fetch('/api/agents');if(r.ok){st.textContent='connected';st.className='status ok';return}}catch(e){}st.textContent='offline';st.className='status err'}
async function loadAgents(){try{const r=await fetch('/api/agents');const d=await r.json();const sel=document.getElementById('selAgent');const agents=d.data||[];sel.innerHTML=agents.map(a=>'<option value="'+a.id+'">'+a.id+' ('+a.owned_by+')</option>').join('')||'<option value="default">default</option>'}catch(e){}}
async function loadConversations(){try{const r=await fetch('/api/conversations');const d=await r.json();conversations=d.conversations||[];renderConvList()}catch(e){}}
function renderConvList(){const el=document.getElementById('convList');el.innerHTML=conversations.map(c=>{const active=c.id===activeConvId;return'<div class="conv-item'+(active?' active':'')+'" onclick="selectConv(\''+c.id+'\')"><div class="info"><div class="title">'+esc(c.title)+'</div><div class="meta">'+c.message_count+' msgs'+(c.agent?' \u00b7 '+c.agent:'')+'</div></div><span class="del" onclick="event.stopPropagation();deleteConv(\''+c.id+'\')" title="Delete">\u2715</span></div>'}).join('')||'<div style="padding:16px;text-align:center;color:var(--subtext);font-size:12px">No conversations yet</div>'}
async function newConv(){const agent=document.getElementById('selAgent').value||'default';try{const r=await fetch('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:agent})});const conv=await r.json();await loadConversations();selectConv(conv.id)}catch(e){alert('Failed: '+e)}}
async function selectConv(cid){activeConvId=cid;try{const r=await fetch('/api/conversations/'+cid);activeConv=await r.json();document.getElementById('convTitle').textContent=activeConv.title;document.getElementById('agentBadge').textContent=activeConv.agent||'default';document.getElementById('selAgent').value=activeConv.agent||'default';renderMessages();renderConvList();fetchCtx()}catch(e){}}
async function deleteConv(cid){try{await fetch('/api/conversations/'+cid,{method:'DELETE'});if(activeConvId===cid){activeConvId=null;activeConv=null}await loadConversations();if(!activeConvId&&conversations.length>0)selectConv(conversations[0].id);else if(!conversations.length){renderMessages();document.getElementById('convTitle').textContent='Select or create a chat';document.getElementById('agentBadge').textContent='--'}}catch(e){}}
async function updateAgent(){if(!activeConvId||!activeConv)return;activeConv.agent=document.getElementById('selAgent').value;document.getElementById('agentBadge').textContent=activeConv.agent||'default';try{await fetch('/api/conversations/'+activeConvId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:activeConv.agent})})}catch(e){}}
function renderMessages(){const el=document.getElementById('msgs');if(!activeConv||!activeConv.messages||activeConv.messages.length===0){if(!activeConv){el.innerHTML='<div class="empty-state"><div class="big">&#x1f4ac;</div><p>Create a new chat or select one from the sidebar</p></div>';return}el.innerHTML='<div class="empty-state"><div class="big">&#x1f4ac;</div><p>Start typing to begin</p></div>';return}el.innerHTML=activeConv.messages.map(m=>'<div class="msg '+m.role+'">'+formatMsg(m.content)+'</div>').join('');el.scrollTop=el.scrollHeight}
function formatMsg(t){t=esc(t);t=t.replace(/```([\s\S]*?)```/g,'<pre>$1</pre>');t=t.replace(/`([^`]+)`/g,'<code>$1</code>');return t}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function updateCtxBar(ctx){const label=document.getElementById('ctxLabel');const fill=document.getElementById('ctxFill');if(!ctx){label.textContent='--';fill.style.width='0%';return}const pct=ctx.pct||0;let info=pct+'%';if(ctx.rag_chunks>0)info+=' \u00b7 RAG:'+ctx.rag_chunks;label.textContent=info;fill.style.width=pct+'%';fill.style.background=pct>85?'var(--red)':pct>60?'var(--yellow)':'var(--green)'}
async function fetchCtx(){if(!activeConvId)return;try{const r=await fetch('/api/conversations/'+activeConvId+'/context');if(r.ok){const d=await r.json();updateCtxBar(d)}}catch(e){}}
async function send(){if(sending)return;const inp=document.getElementById('inp');const text=inp.value.trim();if(!text)return;if(text.length>16000){if(!confirm('Message very long (~'+Math.round(text.length/4)+' tokens). May exceed context. Send anyway?'))return}if(!activeConvId){await newConv();if(!activeConvId){sending=false;return}}sending=true;inp.value='';document.getElementById('sendBtn').disabled=true;const msgEl=document.getElementById('msgs');const emptyEl=msgEl.querySelector('.empty-state');if(emptyEl)emptyEl.remove();msgEl.innerHTML+='<div class="msg user">'+esc(text)+'</div>';msgEl.innerHTML+='<div class="msg assistant" id="typingMsg"><span class="typing">Thinking</span></div>';msgEl.scrollTop=msgEl.scrollHeight;try{const r=await fetch('/api/conversations/'+activeConvId+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});const d=await r.json();const typing=document.getElementById('typingMsg');if(typing)typing.remove();if(d.error){msgEl.innerHTML+='<div class="msg error">Error: '+esc(d.error)+'</div>'}else{msgEl.innerHTML+='<div class="msg assistant">'+formatMsg(d.response)+'</div>';if(activeConv){activeConv.messages.push({role:'user',content:text});activeConv.messages.push({role:'assistant',content:d.response})}if(d.context){updateCtxBar(d.context);if(d.context.trimmed>0)msgEl.innerHTML+='<div class="msg info" style="background:rgba(249,226,175,.1);color:var(--yellow)">Context trimmed: '+d.context.trimmed+' messages archived to agent memory.</div>';if(d.context.rag_chunks>0&&d.context.trimmed===0)msgEl.innerHTML+='<div class="msg info" style="background:rgba(148,226,213,.1);color:var(--teal)">RAG: '+d.context.rag_chunks+' chunk(s) from agent memory</div>'}}msgEl.scrollTop=msgEl.scrollHeight;loadConversations()}catch(e){const typing=document.getElementById('typingMsg');if(typing)typing.remove();msgEl.innerHTML+='<div class="msg error">Error: '+esc(e.message)+'</div>'}sending=false;document.getElementById('sendBtn').disabled=false;inp.focus()}
init();
</script></body></html>"""

WM_CHAT["files"] = WM_CHAT_FILES

PLUGIN_HOST = ("127.0.0.1", "chat.wickerman.local")
