"""
Wickerman OS v5.2.0 — Model Router plugin manifest.
"""

WM_LLAMA = {
    "name": "Model Router",
    "description": "Agent orchestration with local models, remote APIs, and RAG",
    "icon": "hub",
    "build": True,
    "build_context": "data",
    "container_name": "wm-llama",
    "url": "http://llama.wickerman.local",
    "ports": [8080],
    "gpu": True,
    "env": [
        "MODEL_DIR=/models",
        "GPU_LAYERS=99",
        "CTX_SIZE=4096",
        "HOST=0.0.0.0",
        "PORT=8080"
    ],
    "volumes": ["{models}:/models", "{self}/data:/data"],
    "nginx_host": "llama.wickerman.local",
    "help": "## Model Router\nAgent orchestration layer: local models + remote APIs + RAG + system prompts.\n\n**API:** `http://wm-llama:8080/v1/chat/completions` (OpenAI-compatible)\n\n**Multi-model:** Load/unload models independently, each on its own port.\n\n**Settings:** Context size, GPU layers, KV cache quantization, RoPE, flash attention, and more.\n\n**Models:** Add GGUFs to ~/aidojo/models/ and reinstall, or use the Downloader."
}

WM_LLAMA_FILES = {
    "data/Dockerfile": r"""FROM nvidia/cuda:12.2.0-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git curl python3 python3-pip ca-certificates python3-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir faiss-cpu numpy tiktoken nvidia-ml-py
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /opt/llama.cpp
WORKDIR /opt/llama.cpp
ARG CACHEBUST=1
COPY entrypoint.sh /entrypoint.sh
COPY test_chat.html /opt/test_chat.html
COPY manager.py /opt/manager.py
RUN chmod +x /entrypoint.sh
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=3 CMD curl -sf http://localhost:8080/health || exit 1
ENTRYPOINT ["/entrypoint.sh"]
""",

    "data/entrypoint.sh": r"""#!/bin/bash
set -e
MODEL_DIR=${MODEL_DIR:-/models}
GPU_LAYERS=${GPU_LAYERS:-99}
CTX_SIZE=${CTX_SIZE:-4096}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8080}
BUILD_MARKER=/data/.build_done
BUILD_LOG=/data/build.log

echo "============================================"
echo "  WICKERMAN LLAMA SERVER — Hardware Detect"
echo "============================================"

CPU_FLAGS=$(cat /proc/cpuinfo | grep flags | head -1)
CMAKE_ARGS=""
if echo "$CPU_FLAGS" | grep -q 'avx2'; then
    echo "[CPU] AVX2 detected"; CMAKE_ARGS="$CMAKE_ARGS -DGGML_AVX2=ON"
elif echo "$CPU_FLAGS" | grep -q 'avx'; then
    echo "[CPU] AVX only (no AVX2)"; CMAKE_ARGS="$CMAKE_ARGS -DGGML_AVX2=OFF -DGGML_AVX=ON"
else
    echo "[CPU] No AVX — SSE3 fallback"; CMAKE_ARGS="$CMAKE_ARGS -DGGML_AVX2=OFF -DGGML_AVX=OFF"
fi

if nvidia-smi &>/dev/null; then
    echo "[GPU] NVIDIA detected — enabling CUDA"
    CMAKE_ARGS="$CMAKE_ARGS -DGGML_CUDA=ON"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "[GPU] No NVIDIA GPU — CPU only"; CMAKE_ARGS="$CMAKE_ARGS -DGGML_CUDA=OFF"; GPU_LAYERS=0
fi

BUILD_VER=2
if [ ! -f "$BUILD_MARKER" ] || [ ! -f "/data/llama-server" ] || [ "$(cat $BUILD_MARKER 2>/dev/null)" != "$BUILD_VER" ]; then
    echo "[BUILD] Compiling llama.cpp (first run only — cached after this)..."
    echo "[BUILD] FLAGS: $CMAKE_ARGS"
    cd /opt/llama.cpp && rm -rf build && mkdir build && cd build
    cmake .. $CMAKE_ARGS -DLLAMA_CURL=ON 2>&1 | tee $BUILD_LOG
    cmake --build . --config Release -j$(nproc) 2>&1 | tee -a $BUILD_LOG
    cp bin/llama-server /data/llama-server 2>/dev/null || \
    cp bin/server /data/llama-server 2>/dev/null || \
    { echo "[ERROR] Server binary not found"; ls -la bin/; exit 1; }
    chmod +x /data/llama-server
    # Copy shared libraries that llama-server links against
    mkdir -p /data/lib
    cp -a lib/*.so* /data/lib/ 2>/dev/null || cp -a bin/*.so* /data/lib/ 2>/dev/null || true
    echo "$BUILD_VER" > "$BUILD_MARKER"
    echo "[BUILD] Done!"
else
    echo "[BUILD] Using cached build"
fi
cp /data/llama-server /usr/local/bin/llama-server
chmod +x /usr/local/bin/llama-server
# Install shared libraries so the linker finds them
if [ -d /data/lib ] && ls /data/lib/*.so* &>/dev/null; then
    cp -a /data/lib/*.so* /usr/local/lib/
    ldconfig
    echo "[BUILD] Shared libraries installed"
fi

mkdir -p /opt/llama.cpp/examples/server/public
cp /opt/test_chat.html /opt/llama.cpp/examples/server/public/index.html

# List available models
MODEL_COUNT=$(ls $MODEL_DIR/*.gguf 2>/dev/null | wc -l)
echo "[MODEL] $MODEL_COUNT model(s) in $MODEL_DIR"
ls -lh $MODEL_DIR/*.gguf 2>/dev/null || echo "[MODEL] No models found — add GGUFs to ~/aidojo/models/ and reinstall"

echo ""
echo "Starting Wickerman Model Router (multi-model + unified API)"
echo "  Models dir: $MODEL_DIR"
echo "  GPU layers: $GPU_LAYERS | Context: $CTX_SIZE"
exec python3 /opt/manager.py
""",

    "data/test_chat.html": r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Router</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#11111b;--surface:#181825;--overlay:#1e1e2e;--border:#313244;--text:#cdd6f4;--subtext:#6c7086;--blue:#89b4fa;--green:#a6e3a1;--red:#f38ba8;--yellow:#f9e2af;--mauve:#cba6f7;--teal:#94e2d5;--peach:#fab387;--mono:'Courier New',monospace}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh}
.topbar{padding:12px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--surface)}
.topbar .logo{display:flex;align-items:center;gap:10px}
.topbar .logo .icon{width:28px;height:28px;background:var(--blue);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:13px;color:var(--bg);font-weight:700}
.topbar .logo span{font-size:16px;font-weight:700;color:var(--blue);letter-spacing:.5px}
.topbar .gpu-info{font-size:12px;font-family:var(--mono);color:var(--subtext);display:flex;align-items:center;gap:8px}
.topbar .gpu-info .dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.vram-bar{padding:12px 24px;background:var(--surface);border-bottom:1px solid var(--border)}
.vram-bar .lbl{display:flex;justify-content:space-between;font-size:11px;color:var(--subtext);margin-bottom:4px}
.vram-bar .bar{height:8px;background:var(--border);border-radius:4px;overflow:hidden;display:flex}
.vram-bar .legend{display:flex;gap:12px;margin-top:6px;flex-wrap:wrap}
.vram-bar .legend span{font-size:10px;display:flex;align-items:center;gap:4px;color:var(--subtext)}
.vram-bar .legend .sw{width:8px;height:8px;border-radius:2px;display:inline-block}
.content{padding:20px 24px;max-width:1200px;margin:0 auto}
.sec{font-size:11px;color:var(--subtext);text-transform:uppercase;letter-spacing:1px;margin:0 0 12px}
.loaded-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px;margin-bottom:28px}
.card{background:var(--overlay);border:1px solid var(--border);border-radius:10px;padding:16px}
.card.active{border-color:var(--blue)}
.card .hdr{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
.card .name{font-size:15px;font-weight:600}
.card .name.blue{color:var(--blue)}
.card .fname{font-size:11px;color:var(--subtext);margin-top:2px}
.badges{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.badge{font-size:10px;padding:2px 8px;border-radius:10px}
.badge.ready{background:rgba(166,227,161,.12);color:var(--green)}
.badge.loading{background:rgba(249,226,175,.12);color:var(--yellow)}
.badge.error{background:rgba(243,139,168,.12);color:var(--red)}
.badge.gpu{background:rgba(137,180,250,.12);color:var(--blue)}
.badge.cpu{background:rgba(108,112,134,.2);color:var(--subtext)}
.badge.remote{background:rgba(203,166,247,.12);color:var(--mauve)}
.badge.rag{background:rgba(148,226,213,.12);color:var(--teal)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;margin-bottom:12px}
.stat{background:var(--bg);padding:8px 10px;border-radius:6px}
.stat .l{color:var(--subtext);font-size:10px;margin-bottom:2px}
.stat .v{font-family:var(--mono)}
.prompt-preview{background:var(--bg);padding:8px 10px;border-radius:6px;font-size:11px;color:var(--subtext);margin-bottom:12px;max-height:60px;overflow:hidden;font-style:italic}
.actions{display:flex;gap:8px}
.btn{flex:1;padding:7px;text-align:center;border-radius:6px;border:1px solid;font-size:12px;cursor:pointer;background:transparent;color:inherit;font-family:inherit}
.btn-danger{border-color:rgba(243,139,168,.3);color:var(--red)}
.btn-danger:hover{background:rgba(243,139,168,.1)}
.btn-muted{border-color:rgba(49,50,68,.5);color:var(--subtext)}
.btn-muted:hover{background:rgba(49,50,68,.3)}
.btn-primary{border-color:var(--blue);background:var(--blue);color:var(--bg);font-weight:600;font-size:13px}
.btn-primary:hover{opacity:.9}
.btn-mauve{border-color:var(--mauve);background:var(--mauve);color:var(--bg);font-weight:600;font-size:13px}
.avail-card{background:var(--overlay);border:1px solid var(--border);border-radius:10px;margin-bottom:12px;overflow:hidden}
.avail-hdr{padding:14px 16px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.avail-hdr:hover{background:rgba(49,50,68,.3)}
.avail-hdr .info .name{font-size:14px;font-weight:500}
.avail-hdr .info .meta{font-size:11px;color:var(--subtext);margin-top:2px}
.avail-cfg{padding:0 16px 16px;display:none}
.avail-cfg.open{display:block}
.sgrid{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;font-size:12px;margin-bottom:10px}
.sgrid.c2{grid-template-columns:1fr 1fr}
.sgrid.c3{grid-template-columns:1fr 1fr 1fr}
.si{background:var(--bg);padding:8px 10px;border-radius:6px}
.si .l{color:var(--subtext);font-size:10px;margin-bottom:4px}
.si input,.si select,.si textarea{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:5px 8px;color:var(--text);font-family:var(--mono);font-size:12px;outline:none}
.si input:focus,.si select:focus,.si textarea:focus{border-color:var(--blue)}
.si textarea{font-family:inherit;height:60px;resize:vertical}
.toggle-group{display:flex;gap:0;border-radius:4px;overflow:hidden;border:1px solid var(--border)}
.toggle-group .opt{flex:1;padding:5px;text-align:center;font-size:11px;cursor:pointer;background:var(--surface);color:var(--subtext)}
.toggle-group .opt.active{background:var(--blue);color:var(--bg);font-weight:500}
.adv-toggle{font-size:11px;color:var(--blue);cursor:pointer;margin:8px 0 10px;display:inline-block}
.adv{display:none}.adv.open{display:block}
.provider-form{background:var(--overlay);border:1px solid var(--mauve);border-radius:10px;padding:16px;margin-bottom:12px}
.provider-form .title{font-size:14px;font-weight:600;color:var(--mauve);margin-bottom:12px}
.empty{padding:40px;text-align:center;color:var(--subtext);font-size:14px}
.tab-row{display:flex;gap:0;margin-bottom:20px;border-bottom:1px solid var(--border)}
.tab{padding:10px 20px;font-size:13px;cursor:pointer;color:var(--subtext);border-bottom:2px solid transparent}
.tab:hover{color:var(--text)}
.tab.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab-content{display:none}.tab-content.active{display:block}
</style></head><body>
<div class="topbar">
  <div class="logo"><div class="icon">R</div><span>MODEL ROUTER</span></div>
  <div class="gpu-info"><span class="dot" id="gpuDot" style="background:var(--subtext)"></span><span id="gpuInfo">Detecting...</span></div>
</div>
<div class="vram-bar" id="vramSection">
  <div class="lbl"><span>VRAM USAGE</span><span id="vramText">--</span></div>
  <div class="bar" id="vramBarInner"></div>
  <div class="legend" id="vramLegend"></div>
</div>
<div class="content">
  <div class="sec" id="loadedLabel">Loaded agents</div>
  <div class="loaded-grid" id="loadedGrid"></div>
  <div class="tab-row">
    <div class="tab active" onclick="showTab('local')">Local models</div>
    <div class="tab" onclick="showTab('remote')">Remote providers</div>
  </div>
  <div class="tab-content active" id="tab-local">
    <div id="availList"></div>
  </div>
  <div class="tab-content" id="tab-remote">
    <div class="provider-form" id="providerForm">
      <div class="title">Add remote provider</div>
      <div class="sgrid c2">
        <div class="si"><div class="l">Provider type</div><select id="pType"><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic</option><option value="google">Google Gemini</option><option value="custom">Custom endpoint</option></select></div>
        <div class="si"><div class="l">Alias</div><input id="pAlias" placeholder="e.g. gpt4-analyst"></div>
      </div>
      <div class="sgrid c2">
        <div class="si"><div class="l">API base URL</div><input id="pBase" placeholder="https://api.openai.com/v1"></div>
        <div class="si"><div class="l">Model name</div><input id="pModel" placeholder="gpt-4o"></div>
      </div>
      <div class="sgrid c2">
        <div class="si"><div class="l">API key</div><input id="pKey" type="password" placeholder="sk-..."></div>
        <div class="si"><div class="l">System prompt</div><textarea id="pSysPrompt" placeholder="You are a helpful assistant..."></textarea></div>
      </div>
      <div class="sgrid c3">
        <div class="si"><div class="l">Temperature</div><input id="pTemp" value="0.7" type="number" min="0" max="2" step="0.05"></div>
        <div class="si"><div class="l">Max tokens</div><input id="pMaxTok" value="4096" type="number" min="1" step="64"></div>
        <div class="si"><div class="l">RAG</div><label style="display:flex;align-items:center;gap:6px;padding-top:6px;font-size:12px;cursor:pointer"><input type="checkbox" id="pRag" style="width:auto"> Enabled</label></div>
      </div>
      <div style="margin-top:10px"><button class="btn btn-mauve" style="width:100%" onclick="addProvider()">Add provider</button></div>
    </div>
    <div id="remoteList"></div>
  </div>
</div>
<script>
const API=window.location.origin;
const COLORS=['#89b4fa','#a6e3a1','#cba6f7','#f9e2af','#94e2d5','#f38ba8','#fab387'];
let vramInfo={total_mb:0,used_mb:0,free_mb:0,gpu_name:''};
let slotsData={},modelsData=[];
let expandedCards={},advancedOpen={};
function showTab(t){document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));document.querySelectorAll('.tab-content').forEach(e=>e.classList.remove('active'));document.querySelector('.tab-content#tab-'+t).classList.add('active');event.target.classList.add('active')}
async function refresh(){try{const[sr,mr,vr]=await Promise.all([fetch(API+'/api/status'),fetch(API+'/api/models'),fetch(API+'/api/vram')]);const status=await sr.json();const models=await mr.json();vramInfo=await vr.json();slotsData=status.slots||{};modelsData=models.models||[];renderGpu();renderVram();renderLoaded();if(!Object.values(expandedCards).some(v=>v))renderAvailable();renderRemote()}catch(e){}}
function renderGpu(){const d=document.getElementById('gpuDot'),i=document.getElementById('gpuInfo');if(vramInfo.total_mb>0){d.style.background='var(--green)';i.textContent=vramInfo.gpu_name+' \u2014 '+Math.round(vramInfo.total_mb).toLocaleString()+' MB'}else{d.style.background='var(--red)';i.textContent='No GPU detected'}}
function renderVram(){const bar=document.getElementById('vramBarInner'),text=document.getElementById('vramText'),legend=document.getElementById('vramLegend');if(vramInfo.total_mb<=0){text.textContent='N/A';return}text.textContent=vramInfo.used_mb.toLocaleString()+' / '+vramInfo.total_mb.toLocaleString()+' MB ('+Math.round(vramInfo.used_mb/vramInfo.total_mb*100)+'%)';let bh='',lh='';let ci=0;Object.entries(slotsData).forEach(([a,s])=>{if(s.type==='local'&&s.vram_est_mb){const w=Math.max(1,s.vram_est_mb/vramInfo.total_mb*100);const c=COLORS[ci++%COLORS.length];bh+='<div style="width:'+w+'%;background:'+c+'"></div>';lh+='<span><span class="sw" style="background:'+c+'"></span>'+a+' \u2014 '+s.vram_est_mb.toLocaleString()+' MB</span>'}});bar.innerHTML=bh;legend.innerHTML=lh}
function renderLoaded(){const grid=document.getElementById('loadedGrid'),label=document.getElementById('loadedLabel');const entries=Object.entries(slotsData);if(!entries.length){grid.innerHTML='<div class="empty">No agents loaded. Configure a model below or add a remote provider.</div>';label.textContent='Loaded agents';return}label.textContent='Loaded agents ('+entries.length+')';grid.innerHTML=entries.map(([a,s],i)=>{const isLocal=s.type==='local';const isGpu=isLocal&&parseInt(s.settings?.gpu_layers||'99')>0;const sc=s.status==='ready'?'ready':s.status==='loading'?'loading':'error';const prompt=s.system_prompt||'';const hasRag=s.rag_enabled;return'<div class="card'+(s.status==='ready'?' active':'')+'"><div class="hdr"><div><div class="name'+(s.status==='ready'?' blue':'')+'">'+a+'</div><div class="fname">'+(isLocal?s.model_file:s.remote_model+' ('+s.provider_type+')')+'</div></div><div class="badges"><span class="badge '+sc+'">'+s.status+'</span>'+(isLocal?(isGpu?'<span class="badge gpu">GPU</span>':'<span class="badge cpu">CPU</span>'):'<span class="badge remote">remote</span>')+(hasRag?'<span class="badge rag">RAG</span>':'')+'</div></div>'+(prompt?'<div class="prompt-preview">\u201c'+esc(prompt)+'\u201d</div>':'')+'<div class="stats">'+(isLocal?'<div class="stat"><div class="l">Context</div><div class="v">'+(s.settings?.ctx_size||'4096')+'</div></div><div class="stat"><div class="l">VRAM</div><div class="v">'+(s.vram_est_mb||0).toLocaleString()+' MB</div></div>':'<div class="stat"><div class="l">Temp</div><div class="v">'+(s.settings?.temperature||'0.7')+'</div></div><div class="stat"><div class="l">Max tokens</div><div class="v">'+(s.settings?.max_tokens||'4096')+'</div></div>')+'</div><div class="actions"><button class="btn btn-danger" onclick="doUnload(\''+a+'\')">Unload</button></div></div>'}).join('')}
function renderAvailable(){const el=document.getElementById('availList');const avail=modelsData.filter(m=>!m.loaded);if(!avail.length){el.innerHTML=modelsData.length?'<div class="empty">All local models loaded.</div>':'<div class="empty">No .gguf files found in models directory.</div>';return}el.innerHTML=avail.map(m=>{const isOpen=expandedCards[m.name];const id=m.name.replace(/[^a-zA-Z0-9]/g,'_');return'<div class="avail-card"><div class="avail-hdr" onclick="toggleCfg(\''+m.name+'\')"><div class="info"><div class="name">'+m.auto_name+'</div><div class="meta">'+m.name+' \u2014 '+m.size+' (~'+m.estimated_vram_mb.toLocaleString()+' MB)</div></div><button class="btn btn-muted" style="flex:none;width:auto;padding:7px 16px" onclick="event.stopPropagation();toggleCfg(\''+m.name+'\')">'+(isOpen?'Collapse':'Configure')+'</button></div><div class="avail-cfg'+(isOpen?' open':'')+'">'+renderModelSettings(m)+'</div></div>'}).join('')}
function renderModelSettings(m){const id=m.name.replace(/[^a-zA-Z0-9]/g,'_');const free=vramInfo.free_mb;const fits=m.estimated_vram_mb<free;return'<div class="si" style="margin-bottom:10px"><div class="l">System prompt</div><textarea id="s_prompt_'+id+'" placeholder="Define this agent\'s identity and behavior..."></textarea></div><div class="sgrid"><div class="si"><div class="l">Context size</div><input id="s_ctx_'+id+'" value="4096" type="number" min="128" step="256"></div><div class="si"><div class="l">GPU layers</div><input id="s_ngl_'+id+'" value="99" type="number"></div><div class="si"><div class="l">Threads</div><input id="s_threads_'+id+'" value="" placeholder="auto"></div><div class="si"><div class="l">Alias</div><input id="s_alias_'+id+'" value="'+m.auto_name+'"></div></div><div class="sgrid c2"><div class="si"><div class="l">Offload mode</div><div class="toggle-group" id="s_offload_'+id+'"><div class="opt active" onclick="setOff(\''+id+'\',\'gpu\',this)">GPU</div><div class="opt" onclick="setOff(\''+id+'\',\'cpu\',this)">CPU only</div><div class="opt" onclick="setOff(\''+id+'\',\'split\',this)">Split</div></div></div><div class="si"><div class="l">Est. VRAM</div><div style="font-family:var(--mono);color:var(--yellow);font-size:14px;padding-top:4px">~'+m.estimated_vram_mb.toLocaleString()+' MB</div><div style="font-size:10px;color:var(--subtext);margin-top:2px">'+(fits?free.toLocaleString()+' MB free \u2014 should fit':free.toLocaleString()+' MB free \u2014 may not fit')+'</div></div></div><div class="sgrid c3"><div class="si"><div class="l">Temperature</div><input id="s_temp_'+id+'" value="0.7" type="number" min="0" max="2" step="0.05"></div><div class="si"><div class="l">Top P</div><input id="s_topp_'+id+'" value="0.95" type="number" min="0" max="1" step="0.05"></div><div class="si"><div class="l">RAG memory</div><label style="display:flex;align-items:center;gap:6px;padding-top:6px;font-size:12px;cursor:pointer"><input type="checkbox" id="s_rag_'+id+'" checked style="width:auto"> Enabled</label></div></div><span class="adv-toggle" onclick="toggleAdv(\''+id+'\')">Advanced settings \u25BC</span><div class="adv'+(advancedOpen[id]?' open':'')+'" id="adv_'+id+'"><div class="sgrid"><div class="si"><div class="l">Batch size</div><input id="s_batch_'+id+'" value="" placeholder="2048"></div><div class="si"><div class="l">Flash attention</div><select id="s_fa_'+id+'"><option value="">auto</option><option value="true">on</option></select></div><div class="si"><div class="l">KV cache (K)</div><select id="s_ctk_'+id+'"><option value="">f16</option><option value="q8_0">q8_0</option><option value="q4_0">q4_0</option></select></div><div class="si"><div class="l">Seed</div><input id="s_seed_'+id+'" value="" placeholder="-1"></div></div></div><div style="margin-top:12px"><button class="btn btn-primary" onclick="doLoad(\''+m.name+'\',\''+id+'\')">Load agent</button></div>'}
function renderRemote(){const el=document.getElementById('remoteList');const remote=Object.entries(slotsData).filter(([a,s])=>s.type!=='local');if(!remote.length){el.innerHTML='<div class="empty" style="padding:20px">No remote providers configured.</div>';return}el.innerHTML='<div class="sec">Active remote agents</div>'+remote.map(([a,s])=>'<div class="card" style="margin-bottom:12px"><div class="hdr"><div><div class="name" style="color:var(--mauve)">'+a+'</div><div class="fname">'+s.remote_model+' ('+s.provider_type+')</div></div><div class="badges"><span class="badge ready">ready</span><span class="badge remote">remote</span>'+(s.rag_enabled?'<span class="badge rag">RAG</span>':'')+'</div></div>'+(s.system_prompt?'<div class="prompt-preview">\u201c'+esc(s.system_prompt)+'\u201d</div>':'')+'<div class="actions"><button class="btn btn-danger" onclick="doUnload(\''+a+'\')">Remove</button></div></div>').join('')}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function toggleCfg(n){expandedCards[n]=!expandedCards[n];renderAvailable()}
function toggleAdv(id){advancedOpen[id]=!advancedOpen[id];const el=document.getElementById('adv_'+id);if(el)el.classList.toggle('open')}
function setOff(id,mode,el){const g=document.getElementById('s_offload_'+id);g.querySelectorAll('.opt').forEach(o=>o.classList.remove('active'));el.classList.add('active');const ngl=document.getElementById('s_ngl_'+id);if(mode==='cpu')ngl.value='0';else if(mode==='gpu')ngl.value='99';else ngl.value='20'}
function getVal(id){const e=document.getElementById(id);return e?e.value.trim():''}
async function doLoad(filename,id){const alias=getVal('s_alias_'+id)||null;const settings={};if(getVal('s_ctx_'+id))settings.ctx_size=getVal('s_ctx_'+id);if(getVal('s_ngl_'+id))settings.gpu_layers=getVal('s_ngl_'+id);if(getVal('s_threads_'+id))settings.threads=getVal('s_threads_'+id);if(getVal('s_temp_'+id))settings.temperature=getVal('s_temp_'+id);if(getVal('s_topp_'+id))settings.top_p=getVal('s_topp_'+id);if(getVal('s_batch_'+id))settings.batch_size=getVal('s_batch_'+id);if(getVal('s_fa_'+id))settings.flash_attn=getVal('s_fa_'+id);if(getVal('s_ctk_'+id))settings.cache_type_k=getVal('s_ctk_'+id);if(getVal('s_seed_'+id))settings.seed=getVal('s_seed_'+id);const sysPrompt=getVal('s_prompt_'+id);const ragEnabled=document.getElementById('s_rag_'+id)?.checked??true;try{const r=await fetch(API+'/api/slots/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:filename,alias:alias,settings:settings,system_prompt:sysPrompt,rag_enabled:ragEnabled,rag_top_k:3})});const d=await r.json();if(!d.ok)alert('Load failed: '+(d.detail||d.error))}catch(e){alert('Load failed: '+e)}expandedCards={};advancedOpen={};setTimeout(refresh,2000);setTimeout(refresh,6000)}
async function doUnload(alias){try{await fetch(API+'/api/slots/unload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias:alias})})}catch(e){alert('Unload failed: '+e)}setTimeout(refresh,500)}
async function addProvider(){const alias=getVal('pAlias');const type=getVal('pType');const base=getVal('pBase');const model=getVal('pModel');const key=getVal('pKey');const sysPrompt=getVal('pSysPrompt');const temp=getVal('pTemp');const maxTok=getVal('pMaxTok');const ragEnabled=document.getElementById('pRag')?.checked??false;if(!alias||!base||!model){alert('Alias, API base, and model name are required');return}try{const r=await fetch(API+'/api/providers/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alias:alias,type:type,api_base:base,api_key:key,remote_model:model,system_prompt:sysPrompt,settings:{temperature:parseFloat(temp)||0.7,max_tokens:parseInt(maxTok)||4096},rag_enabled:ragEnabled,rag_top_k:3})});const d=await r.json();if(!d.ok)alert('Failed: '+(d.detail||d.error));else{document.getElementById('pAlias').value='';document.getElementById('pKey').value='';document.getElementById('pModel').value='';document.getElementById('pSysPrompt').value=''}}catch(e){alert('Failed: '+e)}setTimeout(refresh,500)}
refresh();setInterval(refresh,3000);
</script></body></html>
""",

    "data/manager.py": r"""#!/usr/bin/env python3
# Wickerman Model Router v3.0
# Agent orchestration layer: local models + remote APIs + RAG + system prompts.
# Each slot is a self-contained agent. Callers just pick an alias.
import os, sys, json, glob, signal, subprocess, threading, time, socket, atexit, re, sqlite3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.error import URLError
import numpy as np
import faiss
import tiktoken

MODEL_DIR = os.environ.get("MODEL_DIR", "/models")
GPU_LAYERS = os.environ.get("GPU_LAYERS", "99")
CTX_SIZE = os.environ.get("CTX_SIZE", "4096")
LISTEN_PORT = 8080
PUBLIC_DIR = "/opt/llama.cpp/examples/server/public"
CONFIG_FILE = "/data/router_config.json"
PROVIDERS_FILE = "/data/providers.json"
RAG_DIR = "/data/rag"
os.makedirs(RAG_DIR, exist_ok=True)

_slots = {}
_slots_lock = threading.Lock()
_enc = tiktoken.get_encoding("cl100k_base")
EMBED_DIM = 384
CHUNK_SIZE = 200
CHUNK_OVERLAP = 50

# ── Utilities ────────────────────────────────────────────────
def _get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def _auto_name(filename):
    name = filename
    if name.endswith(".gguf"): name = name[:-5]
    name = re.sub(r'[-_][QqIi][0-9]+[-_][A-Za-z0-9_]+$', '', name)
    name = name.strip('-_').lower().replace(' ', '-').replace('.', '-')
    return name

def human_size(b):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def count_tokens(text):
    return len(_enc.encode(text))

def count_messages_tokens(messages):
    total = 0
    for m in messages: total += count_tokens(m.get("content", "")) + 4
    return total

def trim_messages(messages, ctx_size, reserve_pct=0.8):
    max_tokens = int(ctx_size * reserve_pct)
    total = count_messages_tokens(messages)
    if total <= max_tokens: return messages, [], total, ctx_size
    trimmed = []
    keep = list(messages)
    sys_msg = None
    if keep and keep[0].get("role") == "system":
        sys_msg = keep.pop(0)
    current_tokens = count_messages_tokens(([sys_msg] if sys_msg else []) + keep)
    while current_tokens > max_tokens and len(keep) > 2:
        popped = keep.pop(0)
        trimmed.append(popped)
        current_tokens -= (count_tokens(popped.get("content", "")) + 4)
    result = ([sys_msg] if sys_msg else []) + keep
    return result, trimmed, current_tokens, ctx_size

def list_models():
    models = []
    loaded_files = {s["model_file"] for s in _slots.values() if s.get("type") == "local"}
    for f in sorted(glob.glob(os.path.join(MODEL_DIR, "*.gguf"))):
        name = os.path.basename(f)
        size = os.path.getsize(f)
        models.append({"name": name, "auto_name": _auto_name(name), "size": human_size(size),
                        "bytes": size, "estimated_vram_mb": int(size / 1024 / 1024 * 1.2),
                        "loaded": name in loaded_files})
    return models

def get_vram_info():
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        gpu_name = pynvml.nvmlDeviceGetName(h)
        if isinstance(gpu_name, bytes): gpu_name = gpu_name.decode()
        return {"gpu_name": gpu_name, "total_mb": mem.total // (1024*1024),
                "used_mb": mem.used // (1024*1024), "free_mb": mem.free // (1024*1024)}
    except: return {"gpu_name": "Unknown", "total_mb": 0, "used_mb": 0, "free_mb": 0}

# ── Settings schema ──────────────────────────────────────────
SETTINGS_SCHEMA = {
    "ctx_size": {"flag": "--ctx-size", "default": "4096", "type": "int"},
    "gpu_layers": {"flag": "--n-gpu-layers", "default": "99", "type": "int"},
    "threads": {"flag": "--threads", "default": "", "type": "int"},
    "batch_size": {"flag": "--batch-size", "default": "", "type": "int"},
    "ubatch_size": {"flag": "--ubatch-size", "default": "", "type": "int"},
    "flash_attn": {"flag": "-fa", "default": "", "type": "flag"},
    "parallel": {"flag": "--parallel", "default": "", "type": "int"},
    "no_mmap": {"flag": "--no-mmap", "default": "", "type": "flag"},
    "threads_batch": {"flag": "--threads-batch", "default": "", "type": "int"},
    "cont_batching": {"flag": "--cont-batching", "default": "", "type": "flag"},
    "cache_type_k": {"flag": "--cache-type-k", "default": "", "type": "str"},
    "cache_type_v": {"flag": "--cache-type-v", "default": "", "type": "str"},
    "no_context_shift": {"flag": "--no-context-shift", "default": "", "type": "flag"},
    "split_mode": {"flag": "--split-mode", "default": "", "type": "str"},
    "tensor_split": {"flag": "--tensor-split", "default": "", "type": "str"},
    "main_gpu": {"flag": "--main-gpu", "default": "", "type": "int"},
    "rope_scaling": {"flag": "--rope-scaling", "default": "", "type": "str"},
    "rope_freq_base": {"flag": "--rope-freq-base", "default": "", "type": "float"},
    "rope_freq_scale": {"flag": "--rope-freq-scale", "default": "", "type": "float"},
    "yarn_orig_ctx": {"flag": "--yarn-orig-ctx", "default": "", "type": "int"},
    "yarn_ext_factor": {"flag": "--yarn-ext-factor", "default": "", "type": "float"},
    "chat_template": {"flag": "--chat-template", "default": "", "type": "str"},
    "jinja": {"flag": "--jinja", "default": "", "type": "flag"},
    "reasoning_format": {"flag": "--reasoning-format", "default": "", "type": "str"},
    "seed": {"flag": "--seed", "default": "", "type": "int"},
    "metrics": {"flag": "--metrics", "default": "", "type": "flag"},
    "verbose_prompt": {"flag": "--verbose-prompt", "default": "", "type": "flag"},
    "verbosity": {"flag": "--verbosity", "default": "", "type": "int"},
}

def _build_cmd(model_path, port, settings):
    cmd = ["/usr/local/bin/llama-server", "--model", model_path,
           "--host", "127.0.0.1", "--port", str(port)]
    for key, schema in SETTINGS_SCHEMA.items():
        val = settings.get(key, schema["default"])
        if not val and val != 0: continue
        val = str(val)
        if schema["type"] == "flag":
            if val.lower() in ("true", "1", "on", "yes"): cmd.append(schema["flag"])
        else: cmd.extend([schema["flag"], val])
    return cmd

# ── RAG ──────────────────────────────────────────────────────
def _get_rag_db(index_id):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', index_id)
    db_path = os.path.join(RAG_DIR, safe + ".db")
    idx_path = os.path.join(RAG_DIR, safe + ".faiss")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, text TEXT, timestamp REAL)")
    db.commit()
    if os.path.isfile(idx_path):
        index = faiss.read_index(idx_path)
    else:
        index = faiss.IndexIDMap(faiss.IndexFlatIP(EMBED_DIM))
    return db, index, idx_path

def _get_embedding_slot():
    for alias, s in _slots.items():
        if alias == "embedding" and s["status"] == "ready" and s.get("type") == "local":
            return s
    for s in _slots.values():
        if s["status"] == "ready" and s.get("type") == "local":
            return s
    return None

def get_embedding(text):
    slot = _get_embedding_slot()
    if not slot: return None
    try:
        req = Request(f"http://127.0.0.1:{slot['port']}/v1/embeddings",
                      data=json.dumps({"input": text}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        resp = urlopen(req, timeout=30)
        data = json.loads(resp.read())
        emb = data["data"][0]["embedding"]
        global EMBED_DIM
        if len(emb) != EMBED_DIM: EMBED_DIM = len(emb)
        return emb
    except Exception as e:
        print(f"[RAG] Embedding failed: {e}", flush=True)
        return None

def _split_long_text(text, max_tokens):
    if count_tokens(text) <= max_tokens: return [text]
    parts = text.split("\n\n")
    chunks, current = [], ""
    for part in parts:
        if count_tokens(current + "\n\n" + part) > max_tokens and current:
            chunks.append(current)
            current = part
        else:
            current = (current + "\n\n" + part).strip()
    if current: chunks.append(current)
    return chunks if chunks else [text[:max_tokens * 4]]

def chunk_messages(messages):
    chunks, current, current_tokens = [], [], 0
    for m in messages:
        text = m.get("role", "user") + ": " + m.get("content", "")
        msg_tokens = count_tokens(text)
        if msg_tokens > CHUNK_SIZE:
            if current:
                chunks.append("\n".join(current))
                current, current_tokens = [], 0
            for sub in _split_long_text(text, CHUNK_SIZE):
                chunks.append(sub)
            continue
        if current_tokens + msg_tokens > CHUNK_SIZE and current:
            chunks.append("\n".join(current))
            overlap_msgs, overlap_tokens = [], 0
            for prev in reversed(current):
                t = count_tokens(prev)
                if overlap_tokens + t > CHUNK_OVERLAP: break
                overlap_msgs.insert(0, prev)
                overlap_tokens += t
            current, current_tokens = overlap_msgs, overlap_tokens
        current.append(text)
        current_tokens += msg_tokens
    if current: chunks.append("\n".join(current))
    return chunks

def rag_archive(index_id, messages):
    if not messages: return 0
    chunks = chunk_messages(messages)
    db, index, idx_path = _get_rag_db(index_id)
    archived = 0
    for chunk_text in chunks:
        vec = get_embedding(chunk_text)
        if vec is None: continue
        vec_np = np.array([vec], dtype=np.float32)
        faiss.normalize_L2(vec_np)
        db.execute("INSERT INTO chunks (text, timestamp) VALUES (?, ?)", (chunk_text, time.time()))
        sqlite_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        index.add_with_ids(vec_np, np.array([sqlite_id], dtype=np.int64))
        archived += 1
    db.commit()
    db.close()
    tmp = idx_path + ".tmp"
    faiss.write_index(index, tmp)
    os.replace(tmp, idx_path)
    print(f"[RAG] Archived {archived} chunks for {index_id}", flush=True)
    return archived

def rag_search(index_id, query, top_k=3):
    idx_path = os.path.join(RAG_DIR, re.sub(r'[^a-zA-Z0-9_-]', '', index_id) + ".faiss")
    if not os.path.isfile(idx_path): return []
    vec = get_embedding(query)
    if vec is None: return []
    db, index, idx_path = _get_rag_db(index_id)
    if index.ntotal == 0:
        db.close()
        return []
    vec_np = np.array([vec], dtype=np.float32)
    faiss.normalize_L2(vec_np)
    k = min(top_k, index.ntotal)
    scores, ids = index.search(vec_np, k)
    results = []
    for i, sid in enumerate(ids[0]):
        if sid < 0: continue
        row = db.execute("SELECT text FROM chunks WHERE id = ?", (int(sid),)).fetchone()
        if row: results.append({"text": row[0], "score": float(scores[0][i])})
    db.close()
    return results

def rag_clear(index_id):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', index_id)
    for ext in [".db", ".faiss", ".faiss.tmp"]:
        p = os.path.join(RAG_DIR, safe + ext)
        if os.path.isfile(p): os.remove(p)

def rag_status(index_id):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', index_id)
    idx_path = os.path.join(RAG_DIR, safe + ".faiss")
    if not os.path.isfile(idx_path): return {"chunks": 0}
    try:
        idx = faiss.read_index(idx_path)
        return {"chunks": idx.ntotal}
    except: return {"chunks": 0}

# ── Slot Management ──────────────────────────────────────────
def load_model(model_file, alias=None, settings=None, system_prompt="", rag_enabled=True, rag_top_k=3):
    if not os.path.isfile(os.path.join(MODEL_DIR, model_file)):
        return False, None, f"Model not found: {model_file}"
    if alias is None: alias = _auto_name(model_file)
    if settings is None: settings = {}
    if "gpu_layers" not in settings: settings["gpu_layers"] = GPU_LAYERS
    if "ctx_size" not in settings: settings["ctx_size"] = CTX_SIZE

    with _slots_lock:
        if alias in _slots: return False, alias, f"Alias '{alias}' already in use"
        for a, s in _slots.items():
            if s.get("model_file") == model_file and s.get("type") == "local":
                return False, a, f"Model already loaded as '{a}'"

    port = _get_free_port()
    model_path = os.path.join(MODEL_DIR, model_file)
    file_size = os.path.getsize(model_path)

    slot = {
        "type": "local", "model_file": model_file, "alias": alias, "port": port,
        "process": None, "status": "loading", "detail": f"Loading {model_file}...",
        "vram_est_mb": int(file_size / 1024 / 1024 * 1.2),
        "system_prompt": system_prompt, "rag_enabled": rag_enabled, "rag_top_k": rag_top_k,
        "settings": settings, "loaded_at": None,
    }
    with _slots_lock: _slots[alias] = slot

    cmd = _build_cmd(model_path, port, settings)
    print(f"[ROUTER] Loading {model_file} as '{alias}' on port {port}", flush=True)
    try:
        proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        slot["process"] = proc
    except Exception as e:
        slot["status"] = "error"
        slot["detail"] = f"Failed to start: {e}"
        return False, alias, str(e)

    def _wait_ready():
        for i in range(300):
            time.sleep(0.5)
            slot["detail"] = f"Loading {model_file}... ({i//2}s)"
            try:
                r = urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
                if r.status == 200:
                    slot["status"] = "ready"
                    slot["detail"] = f"{alias} ready"
                    slot["loaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    print(f"[ROUTER] '{alias}' ready on port {port}", flush=True)
                    _save_config()
                    return
            except: pass
            if proc.poll() is not None:
                slot["status"] = "error"
                slot["detail"] = f"Crashed (exit {proc.returncode})"
                print(f"[ROUTER] '{alias}' exited with code {proc.returncode}", flush=True)
                return
        slot["status"] = "ready"
        slot["detail"] = f"{alias} (slow start)"
        slot["loaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_config()
    threading.Thread(target=_wait_ready, daemon=True).start()
    return True, alias, f"Loading {model_file} as '{alias}'"

def add_remote_provider(alias, provider_type, api_base, api_key, remote_model,
                         system_prompt="", settings=None, rag_enabled=False, rag_top_k=3):
    if not alias or not provider_type or not api_base or not remote_model:
        return False, "Missing required fields"
    with _slots_lock:
        if alias in _slots: return False, f"Alias '{alias}' already in use"
    slot = {
        "type": provider_type, "alias": alias, "api_base": api_base.rstrip("/"),
        "api_key": api_key, "remote_model": remote_model,
        "system_prompt": system_prompt, "rag_enabled": rag_enabled, "rag_top_k": rag_top_k,
        "settings": settings or {}, "status": "ready", "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _slots_lock: _slots[alias] = slot
    _save_config()
    _save_providers()
    print(f"[ROUTER] Added remote provider '{alias}' ({provider_type}: {remote_model})", flush=True)
    return True, f"Added '{alias}'"

def unload_model(alias):
    with _slots_lock:
        if alias not in _slots: return False, f"No agent loaded as '{alias}'"
        slot = _slots[alias]
        if slot.get("type") == "local":
            proc = slot.get("process")
            if proc and proc.poll() is None:
                print(f"[ROUTER] Unloading '{alias}' (pid {proc.pid})", flush=True)
                proc.terminate()
                try: proc.wait(timeout=10)
                except: proc.kill()
        del _slots[alias]
    _save_config()
    print(f"[ROUTER] '{alias}' unloaded", flush=True)
    return True, f"Unloaded '{alias}'"

def _resolve_model(model_name):
    if not model_name or model_name == "default":
        for s in _slots.values():
            if s["status"] == "ready" and s.get("type") == "local":
                return s, None
        for s in _slots.values():
            if s["status"] == "ready":
                return s, None
        return None, "No agents loaded"
    if model_name in _slots:
        s = _slots[model_name]
        if s["status"] == "ready": return s, None
        return None, f"Agent '{model_name}' is {s['status']}: {s.get('detail', '')}"
    for s in _slots.values():
        if s.get("model_file") == model_name and s["status"] == "ready": return s, None
    for alias, s in _slots.items():
        if model_name.lower() in alias.lower() and s["status"] == "ready": return s, None
    return None, f"Agent '{model_name}' not found. Available: {', '.join(_slots.keys()) or 'none'}"

# ── Agent Request Pipeline ───────────────────────────────────
def _apply_agent_pipeline(slot, messages, request_settings=None):
    # 1. System prompt: concatenate slot identity + incoming task context
    slot_prompt = slot.get("system_prompt", "")
    incoming_sys = None
    if messages and messages[0].get("role") == "system":
        incoming_sys = messages.pop(0)

    if slot_prompt and incoming_sys:
        combined = slot_prompt + "\n\n" + incoming_sys["content"]
        messages.insert(0, {"role": "system", "content": combined})
    elif slot_prompt:
        messages.insert(0, {"role": "system", "content": slot_prompt})
    elif incoming_sys:
        messages.insert(0, incoming_sys)

    # 2. Context trimming + auto-archive
    trimmed = []
    ctx_size = int(slot.get("settings", {}).get("ctx_size", 4096))
    messages, trimmed, _, _ = trim_messages(messages, ctx_size)
    if trimmed and slot.get("rag_enabled", False):
        index_id = slot.get("alias", "default")
        try:
            rag_archive(index_id, trimmed)
        except Exception as e:
            print(f"[RAG] Auto-archive error: {e}", flush=True)

    # 3. RAG: search for relevant context and inject
    rag_results = []
    if slot.get("rag_enabled", False):
        index_id = slot.get("alias", "default")
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        if user_msg:
            try:
                top_k = slot.get("rag_top_k", 3)
                rag_results = rag_search(index_id, user_msg, top_k=top_k)
            except Exception as e:
                print(f"[RAG] Search error: {e}", flush=True)
        if rag_results:
            rag_text = "\n---\n".join([r["text"] for r in rag_results])
            rag_note = {"role": "system", "content": "[Earlier context:]\n" + rag_text}
            if messages and messages[0].get("role") == "system":
                messages.insert(1, rag_note)
            else:
                messages.insert(0, rag_note)

    # 3. Merge settings: request overrides slot defaults
    merged = dict(slot.get("settings", {}))
    if request_settings:
        for k, v in request_settings.items():
            if v is not None and str(v) != "":
                merged[k] = v

    return messages, merged, rag_results, trimmed

def _proxy_local(slot, messages, settings):
    payload = {"model": slot["alias"], "messages": messages, "stream": False}
    for key in ["temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens", "seed"]:
        if key in settings and settings[key] is not None and str(settings[key]) != "":
            payload[key] = settings[key]
    if "max_tokens" not in payload: payload["max_tokens"] = 1024
    data = json.dumps(payload).encode()
    req = Request(f"http://127.0.0.1:{slot['port']}/v1/chat/completions",
                  data=data, headers={"Content-Type": "application/json"}, method="POST")
    resp = urlopen(req, timeout=300)
    return json.loads(resp.read())

def _proxy_openai(slot, messages, settings):
    payload = {"model": slot["remote_model"], "messages": messages, "stream": False}
    for key in ["temperature", "top_p", "max_tokens", "seed"]:
        if key in settings and settings[key] is not None and str(settings[key]) != "":
            payload[key] = settings[key]
    if "max_tokens" not in payload: payload["max_tokens"] = 1024
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {slot.get('api_key', '')}"}
    req = Request(f"{slot['api_base']}/chat/completions", data=data, headers=headers, method="POST")
    resp = urlopen(req, timeout=300)
    return json.loads(resp.read())

def _proxy_anthropic(slot, messages, settings):
    # Translate OpenAI format -> Anthropic Messages API
    sys_text = ""
    api_msgs = []
    for m in messages:
        if m["role"] == "system":
            sys_text = (sys_text + "\n" + m["content"]).strip()
        else:
            api_msgs.append({"role": m["role"], "content": m["content"]})
    payload = {"model": slot["remote_model"], "messages": api_msgs, "max_tokens": int(settings.get("max_tokens", 1024))}
    if sys_text: payload["system"] = sys_text
    if "temperature" in settings: payload["temperature"] = float(settings["temperature"])
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json",
               "x-api-key": slot.get("api_key", ""),
               "anthropic-version": "2023-06-01"}
    req = Request(f"{slot['api_base']}/messages", data=data, headers=headers, method="POST")
    resp = urlopen(req, timeout=300)
    result = json.loads(resp.read())
    # Translate back to OpenAI format
    content = ""
    for block in result.get("content", []):
        if block.get("type") == "text": content += block["text"]
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": result.get("usage", {})}

def _proxy_google(slot, messages, settings):
    # Translate OpenAI format -> Gemini generateContent
    sys_text = ""
    contents = []
    for m in messages:
        if m["role"] == "system":
            sys_text = (sys_text + "\n" + m["content"]).strip()
        else:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
    payload = {"contents": contents}
    if sys_text:
        payload["system_instruction"] = {"parts": [{"text": sys_text}]}
    gc = {}
    if "temperature" in settings: gc["temperature"] = float(settings["temperature"])
    if "max_tokens" in settings: gc["maxOutputTokens"] = int(settings["max_tokens"])
    if gc: payload["generationConfig"] = gc
    data = json.dumps(payload).encode()
    model = slot["remote_model"]
    url = f"{slot['api_base']}/v1beta/models/{model}:generateContent?key={slot.get('api_key', '')}"
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    resp = urlopen(req, timeout=300)
    result = json.loads(resp.read())
    content = ""
    for candidate in result.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            content += part.get("text", "")
    return {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {}}

def route_chat(slot, messages, request_settings=None):
    messages = [dict(m) for m in messages]
    messages, merged, rag_results, trimmed = _apply_agent_pipeline(slot, messages, request_settings)
    ptype = slot.get("type", "local")
    if ptype == "local": result = _proxy_local(slot, messages, merged)
    elif ptype == "openai" or ptype == "custom": result = _proxy_openai(slot, messages, merged)
    elif ptype == "anthropic": result = _proxy_anthropic(slot, messages, merged)
    elif ptype == "google": result = _proxy_google(slot, messages, merged)
    else: raise ValueError(f"Unknown provider type: {ptype}")
    result["_rag"] = {"chunks_used": len(rag_results), "trimmed": len(trimmed), "archived": len(trimmed) if trimmed and slot.get("rag_enabled") else 0}
    return result

# ── Config Persistence ───────────────────────────────────────
def _save_config():
    try:
        cfg = {"agents": []}
        for alias, s in _slots.items():
            entry = {"alias": s["alias"], "type": s.get("type", "local"),
                     "system_prompt": s.get("system_prompt", ""),
                     "rag_enabled": s.get("rag_enabled", False),
                     "rag_top_k": s.get("rag_top_k", 3),
                     "settings": s.get("settings", {})}
            if s.get("type") == "local":
                entry["model_file"] = s.get("model_file", "")
            else:
                entry["remote_model"] = s.get("remote_model", "")
                entry["provider_type"] = s.get("type", "openai")
                entry["api_base"] = s.get("api_base", "")
            if s["status"] in ("ready", "loading"):
                cfg["agents"].append(entry)
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f: json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[ROUTER] Config save failed: {e}", flush=True)

def _save_providers():
    try:
        keys = {}
        for alias, s in _slots.items():
            if s.get("api_key"):
                keys[alias] = s["api_key"]
        with open(PROVIDERS_FILE, "w") as f: json.dump(keys, f)
        os.chmod(PROVIDERS_FILE, 0o600)
    except Exception as e:
        print(f"[ROUTER] Provider save failed: {e}", flush=True)

def _load_config():
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE) as f: return json.load(f)
    except: pass
    return None

def _load_provider_keys():
    try:
        if os.path.isfile(PROVIDERS_FILE):
            with open(PROVIDERS_FILE) as f: return json.load(f)
    except: pass
    return {}

# ── Zombie Cleanup ───────────────────────────────────────────
def _kill_all_slots():
    print("[ROUTER] Cleaning up all model processes...", flush=True)
    with _slots_lock:
        for alias, slot in _slots.items():
            proc = slot.get("process")
            if proc and proc.poll() is None:
                print(f"[ROUTER] Killing '{alias}' (pid {proc.pid})", flush=True)
                proc.kill()
atexit.register(_kill_all_slots)

# ── HTTP Handler ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/status':
            slots_info = {}
            for alias, s in _slots.items():
                info = {"type": s.get("type", "local"), "status": s["status"],
                        "detail": s.get("detail", ""), "settings": s.get("settings", {}),
                        "system_prompt": s.get("system_prompt", "")[:100],
                        "rag_enabled": s.get("rag_enabled", False),
                        "rag_top_k": s.get("rag_top_k", 3),
                        "loaded_at": s.get("loaded_at")}
                if s.get("type") == "local":
                    info.update({"model_file": s.get("model_file", ""), "port": s.get("port"),
                                 "vram_est_mb": s.get("vram_est_mb", 0)})
                else:
                    info.update({"remote_model": s.get("remote_model", ""),
                                 "provider_type": s.get("type", "openai")})
                slots_info[alias] = info
            ready = sum(1 for s in _slots.values() if s["status"] == "ready")
            loading = sum(1 for s in _slots.values() if s["status"] == "loading")
            self._json(200, {"status": "ready" if ready > 0 else ("loading" if loading > 0 else "idle"),
                             "detail": f"{ready} agent(s) ready, {loading} loading", "slots": slots_info})

        elif self.path == '/api/models':
            self._json(200, {"models": list_models(),
                             "loaded": {a: {"model_file": s.get("model_file", ""), "type": s.get("type"),
                                            "settings": s.get("settings", {})} for a, s in _slots.items()}})

        elif self.path == '/api/slots':
            self._json(200, {"slots": [
                {"alias": a, "type": s.get("type", "local"), "status": s["status"],
                 "system_prompt": s.get("system_prompt", "")[:100],
                 "rag_enabled": s.get("rag_enabled"), "rag_top_k": s.get("rag_top_k", 3)}
                for a, s in _slots.items()]})

        elif self.path == '/api/vram':
            self._json(200, get_vram_info())

        elif self.path == '/api/settings-schema':
            self._json(200, SETTINGS_SCHEMA)

        elif self.path == '/health':
            ready = any(s["status"] == "ready" for s in _slots.values())
            self._json(200 if ready or not _slots else 503,
                       {"status": "ok" if ready else ("loading" if _slots else "no agents loaded")})

        elif self.path == '/v1/models':
            data = [{"id": a, "object": "model", "owned_by": s.get("type", "local")}
                    for a, s in _slots.items() if s["status"] == "ready"]
            self._json(200, {"object": "list", "data": data})

        elif self.path.startswith('/api/rag/'):
            parts = self.path.split('/')
            if len(parts) >= 5:
                index_id = parts[3]
                action = parts[4]
                if action == 'status':
                    self._json(200, rag_status(index_id))
                else:
                    self._json(404, {"error": "Unknown RAG action"})
            else:
                self._json(400, {"error": "Invalid RAG path"})

        elif self.path.startswith('/v1/'):
            slot, err = _resolve_model("default")
            if slot:
                self._proxy_to_local(slot)
            else:
                self._json(503, {"error": {"message": err, "type": "server_error"}})

        elif self.path == '/' or self.path == '/index.html':
            self._serve_file(os.path.join(PUBLIC_DIR, 'index.html'), 'text/html')
        else:
            safe = self.path.lstrip('/')
            fpath = os.path.join(PUBLIC_DIR, safe)
            if os.path.isfile(fpath):
                ct = 'text/html'
                if fpath.endswith('.js'): ct = 'application/javascript'
                elif fpath.endswith('.css'): ct = 'text/css'
                elif fpath.endswith('.json'): ct = 'application/json'
                self._serve_file(fpath, ct)
            else:
                self.send_response(404)
                self.end_headers()

    def _proxy_to_local(self, slot):
        url = f"http://127.0.0.1:{slot['port']}{self.path}"
        headers = {}
        for key in ['content-type', 'accept', 'authorization']:
            val = self.headers.get(key)
            if val: headers[key] = val
        try:
            req = Request(url, headers=headers, method="GET")
            resp = urlopen(req, timeout=300)
            data = resp.read()
            self.send_response(resp.status)
            self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except URLError as e:
            self._json(502, {"error": {"message": f"Backend unavailable: {e}"}})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''

        if self.path == '/api/slots/load':
            try:
                d = json.loads(body)
                ok, alias_out, detail = load_model(
                    d.get('model', ''), d.get('alias'), d.get('settings'),
                    d.get('system_prompt', ''), d.get('rag_enabled', True), d.get('rag_top_k', 3))
                self._json(200 if ok else 400, {"ok": ok, "alias": alias_out, "detail": detail})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == '/api/providers/add':
            try:
                d = json.loads(body)
                ok, detail = add_remote_provider(
                    d.get('alias', ''), d.get('type', 'openai'), d.get('api_base', ''),
                    d.get('api_key', ''), d.get('remote_model', ''),
                    d.get('system_prompt', ''), d.get('settings'), d.get('rag_enabled', False),
                    d.get('rag_top_k', 3))
                self._json(200 if ok else 400, {"ok": ok, "detail": detail})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == '/api/slots/unload':
            try:
                d = json.loads(body)
                ok, detail = unload_model(d.get('alias', ''))
                self._json(200 if ok else 400, {"ok": ok, "detail": detail})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path == '/api/slots/update':
            try:
                d = json.loads(body)
                alias = d.get('alias', '')
                if alias not in _slots:
                    self._json(404, {"error": f"Agent '{alias}' not found"})
                    return
                slot = _slots[alias]
                for key in ['system_prompt', 'rag_enabled', 'rag_top_k']:
                    if key in d: slot[key] = d[key]
                if 'settings' in d:
                    for k, v in d['settings'].items():
                        slot['settings'][k] = v
                _save_config()
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(500, {"error": str(e)})

        elif self.path.startswith('/api/rag/'):
            parts = self.path.split('/')
            if len(parts) >= 5:
                index_id = parts[3]
                action = parts[4]
                if action == 'clear':
                    rag_clear(index_id)
                    self._json(200, {"ok": True})
                elif action == 'archive':
                    try:
                        d = json.loads(body)
                        msgs = d.get('messages', [])
                        n = rag_archive(index_id, msgs)
                        self._json(200, {"ok": True, "archived": n})
                    except Exception as e:
                        self._json(500, {"error": str(e)})
                elif action == 'search':
                    try:
                        d = json.loads(body)
                        results = rag_search(index_id, d.get('query', ''), d.get('top_k', 3))
                        self._json(200, {"results": results})
                    except Exception as e:
                        self._json(500, {"error": str(e)})
                else:
                    self._json(404, {"error": "Unknown RAG action"})
            else:
                self._json(400, {"error": "Invalid RAG path"})

        elif self.path == '/v1/embeddings':
            try:
                d = json.loads(body) if body else {}
                model_name = d.get("model", "embedding")
            except: model_name = "embedding"
            slot, err = _resolve_model(model_name)
            if not slot: slot, err = _resolve_model("default")
            if slot and slot.get("type") == "local":
                url = f"http://127.0.0.1:{slot['port']}/v1/embeddings"
                try:
                    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
                    resp = urlopen(req, timeout=30)
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(data)
                except URLError as e:
                    self._json(502, {"error": {"message": f"Embedding unavailable: {e}"}})
            else:
                self._json(503, {"error": {"message": "No local model for embeddings"}})

        elif self.path == '/v1/chat/completions':
            try:
                d = json.loads(body) if body else {}
                model_name = d.get("model", "default")
                messages = d.get("messages", [])
                req_settings = {}
                for key in ["temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens", "seed"]:
                    if key in d: req_settings[key] = d[key]
            except Exception as e:
                self._json(400, {"error": {"message": str(e)}})
                return
            slot, err = _resolve_model(model_name)
            if not slot:
                self._json(503, {"error": {"message": err, "type": "server_error"}})
                return
            try:
                result = route_chat(slot, messages, req_settings)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self._json(502, {"error": {"message": str(e), "type": "server_error"}})

        elif self.path.startswith('/v1/'):
            try:
                d = json.loads(body) if body else {}
                model_name = d.get("model", "default")
            except: model_name = "default"
            slot, err = _resolve_model(model_name)
            if slot and slot.get("type") == "local":
                url = f"http://127.0.0.1:{slot['port']}{self.path}"
                try:
                    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
                    resp = urlopen(req, timeout=300)
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(data)
                except URLError as e:
                    self._json(502, {"error": {"message": f"Backend unavailable: {e}"}})
            else:
                self._json(503, {"error": {"message": err or "No local model", "type": "server_error"}})

        else:
            self._json(404, {"error": "Not found"})

    def _json(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        try: self.wfile.write(json.dumps(data).encode())
        except BrokenPipeError: pass

    def _serve_file(self, path, content_type):
        try:
            with open(path, 'rb') as f: data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

# ── Main ─────────────────────────────────────────────────────
def main():
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"[ROUTER] Model Router v3.0 listening on port {LISTEN_PORT}", flush=True)

    def _auto_load():
        cfg = _load_config()
        keys = _load_provider_keys()
        if cfg and cfg.get("agents"):
            print(f"[ROUTER] Restoring {len(cfg['agents'])} agent(s) from config", flush=True)
            for entry in cfg["agents"]:
                if entry.get("type", "local") == "local":
                    mf = entry.get("model_file", "")
                    if mf and os.path.isfile(os.path.join(MODEL_DIR, mf)):
                        load_model(mf, entry.get("alias"), entry.get("settings"),
                                   entry.get("system_prompt", ""), entry.get("rag_enabled", True),
                                   entry.get("rag_top_k", 3))
                        time.sleep(2)
                else:
                    alias = entry.get("alias", "")
                    key = keys.get(alias, "")
                    add_remote_provider(alias, entry.get("provider_type", "openai"),
                                        entry.get("api_base", ""), key,
                                        entry.get("remote_model", ""),
                                        entry.get("system_prompt", ""),
                                        entry.get("settings"), entry.get("rag_enabled", False),
                                        entry.get("rag_top_k", 3))
        else:
            models = list_models()
            if models:
                smallest = min(models, key=lambda m: m["bytes"])
                print(f"[ROUTER] First run - loading: {smallest['name']}", flush=True)
                load_model(smallest["name"])
                time.sleep(2)
                embed = [m for m in models if "minilm" in m["name"].lower() or "embed" in m["name"].lower()]
                if embed and embed[0]["name"] != smallest["name"]:
                    print(f"[ROUTER] Loading embedding: {embed[0]['name']}", flush=True)
                    load_model(embed[0]["name"], alias="embedding", settings={"gpu_layers": "0", "ctx_size": "512"})
            else:
                print(f"[ROUTER] No models in {MODEL_DIR}", flush=True)
    threading.Thread(target=_auto_load, daemon=True).start()

    def shutdown(sig, frame):
        print("[ROUTER] Shutting down...", flush=True)
        _kill_all_slots()
        server.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()

if __name__ == "__main__":
    main()
"""
}

WM_LLAMA["files"] = WM_LLAMA_FILES

PLUGIN_HOST = ("127.0.0.1", "llama.wickerman.local")
