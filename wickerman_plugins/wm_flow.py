"""
Wickerman OS v5.4.0 — Flow Editor plugin manifest.
"""

WM_FLOW = {
    "name": "Flow Editor",
    "description": "Visual node-based flow editor for agentic AI pipelines (Flowise)",
    "icon": "account_tree",
    "image": "flowiseai/flowise:latest",
    "container_name": "wm-flow",
    "url": "http://flow.wickerman.local",
    "ports": [3000],
    "gpu": False,
    "env": [
        "PORT=3000",
        "FLOWISE_USERNAME=wickerman",
        "FLOWISE_PASSWORD=wickerman",
        "DATABASE_PATH=/data/db",
        "APIKEY_PATH=/data",
        "SECRETKEY_PATH=/data",
        "LOG_PATH=/data/logs",
        "BLOB_STORAGE_PATH=/data/storage"
    ],
    "volumes": ["{self}/data:/data"],
    "nginx_host": "flow.wickerman.local",
    "help": "## Flow Editor (Flowise)\nDrag-and-drop node editor for building AI pipelines.\n\n**Default login:** wickerman / wickerman\n\n**Connecting to Llama Server:** Add a ChatLocalAI node, set base URL to `http://wm-llama:8080`\n\n**Connecting to Chat node:** Use HTTP Request node pointing to `http://wm-chat:5000/node/execute`\n\n**Custom Wickerman nodes** can be called via HTTP Request nodes using the `/node/execute` endpoint on any `wm-*` container."
}

PLUGIN_HOST = ("127.0.0.1", "flow.wickerman.local")
