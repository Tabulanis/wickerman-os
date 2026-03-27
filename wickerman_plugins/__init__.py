"""
Wickerman OS v5.6.0 — Plugin package.
Auto-discovers all wm_*.py plugin files in this directory.
Drop a new wm_yourplugin.py in here and it will be picked up
automatically on the next install — no manual registration needed.
"""
import os
import importlib

ALL_PLUGINS = {}
PLUGIN_HOSTS = []

_here = os.path.dirname(os.path.abspath(__file__))

for _fname in sorted(os.listdir(_here)):
    if not (_fname.startswith("wm_") and _fname.endswith(".py")):
        continue

    _module_name = _fname[:-3]  # strip .py
    _plugin_key = _module_name.replace("_", "-") + ".json"  # wm_probe -> wm-probe.json

    try:
        _mod = importlib.import_module(f"wickerman_plugins.{_module_name}")

        # Find the manifest dict — convention: WM_PLUGINNAME in uppercase
        _manifest_var = _module_name.upper()  # wm_probe -> WM_PROBE
        if not hasattr(_mod, _manifest_var):
            # Try scanning for any dict with container_name key
            for _attr in dir(_mod):
                _val = getattr(_mod, _attr)
                if isinstance(_val, dict) and "container_name" in _val:
                    _manifest_var = _attr
                    break

        _manifest = getattr(_mod, _manifest_var, None)
        if _manifest and isinstance(_manifest, dict) and "container_name" in _manifest:
            ALL_PLUGINS[_plugin_key] = _manifest

        # Find PLUGIN_HOST — convention: PLUGIN_HOST in the module
        _host = getattr(_mod, "PLUGIN_HOST", None)
        if _host:
            PLUGIN_HOSTS.append(_host)

    except Exception as _e:
        print(f"[plugins] Warning: could not load {_fname}: {_e}")

