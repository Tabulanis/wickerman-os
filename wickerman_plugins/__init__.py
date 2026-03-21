"""
Wickerman OS v5.1.0 — Plugin package.
Each plugin lives in its own file for isolated editing.
Imported by wickermaninstall.py.
"""

from wickerman_plugins.wm_llama import WM_LLAMA, PLUGIN_HOST as LLAMA_HOST
from wickerman_plugins.wm_chat import WM_CHAT, PLUGIN_HOST as CHAT_HOST
from wickerman_plugins.wm_flow import WM_FLOW, PLUGIN_HOST as FLOW_HOST
from wickerman_plugins.wm_trainer import WM_TRAINER, PLUGIN_HOST as TRAINER_HOST
from wickerman_plugins.wm_forge import WM_FORGE, PLUGIN_HOST as FORGE_HOST

ALL_PLUGINS = {
    "wm-llama.json": WM_LLAMA,
    "wm-chat.json": WM_CHAT,
    "wm-flow.json": WM_FLOW,
    "wm-trainer.json": WM_TRAINER,
    "wm-forge.json": WM_FORGE,
}

PLUGIN_HOSTS = [
    LLAMA_HOST,
    CHAT_HOST,
    FLOW_HOST,
    TRAINER_HOST,
    FORGE_HOST,
]
