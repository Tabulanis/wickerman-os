#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║   WICKERMAN OS — OMNISCIENT EDITION         ║
║   Single-file installer v5.2.0              ║
╚══════════════════════════════════════════════╝

Run:  python3 wickermaninstall.py
      python3 wickermaninstall.py --reset
      python3 wickermaninstall.py --hard-reset  # TOTAL WIPE (preserves ~/WickermanSupport)
sudo  python3 wickermaninstall.py               # auto-patches /etc/hosts

Requires: wickerman_support.py in the same directory.
"""
import os, sys, json, subprocess, argparse, shutil, time
from pathlib import Path

# Import embedded file contents from support module
try:
    from wickerman_support import (
        MAIN_PY, CORE_DOCKERFILE,
        DOWNLOADER_APP_PY, DOWNLOADER_INDEX_HTML,
        DOWNLOADER_REQUIREMENTS, DOWNLOADER_DOCKERFILE,
        GENERATE_NGINX_PY,
    )
except ImportError:
    print("[FATAL] wickerman_support.py not found. Place it next to this installer.")
    sys.exit(1)

try:
    from wickerman_plugins import ALL_PLUGINS, PLUGIN_HOSTS
except ImportError:
    print("[WARN] wickerman_plugins package not found. No bundled plugins will be installed.")
    ALL_PLUGINS, PLUGIN_HOSTS = {}, []

VERSION = "5.2.0"

_SUDO_USER = os.environ.get("SUDO_USER")
if _SUDO_USER:
    import pwd
    _REAL_HOME = Path(pwd.getpwnam(_SUDO_USER).pw_dir)
    _REAL_USER = _SUDO_USER
else:
    _REAL_HOME = Path.home()
    _REAL_USER = os.environ.get("USER", "")

INSTALL_DIR = _REAL_HOME / "wickerman"
SUPPORT_DIR = _REAL_HOME / "WickermanSupport"

HOSTS_NEEDED = [
    ("127.0.0.1", "wickerman.local"),
    ("127.0.0.1", "downloader.wickerman.local"),
]

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def run(cmd, ignore=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0 and not ignore:
        print(f"  Error: {r.stderr.strip()}")
    return r.returncode == 0

def write_file(path, content, chmod=None):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    if chmod:
        p.chmod(chmod)

# ══════════════════════════════════════════════════════════════════════════════
#  HARD RESET — nukes ~/wickerman but preserves ~/WickermanSupport
# ══════════════════════════════════════════════════════════════════════════════

def hard_nuke():
    print("\n  WARNING: --hard-reset detected. Scorching the earth in 5s...")
    print(f"  NOTE: {SUPPORT_DIR} is PRESERVED (models, datasets, LoRAs).")
    print(f"        Plugin manifests and build data will be reset.")
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        sys.exit(1)

    # Stop and remove all managed containers
    for c in subprocess.run(
        "docker ps -a --filter label=wickerman.managed=true --format '{{.Names}}'",
        shell=True, capture_output=True, text=True
    ).stdout.split():
        run(f"docker rm -f {c}", ignore=True)
    run("docker network rm wm-net", ignore=True)

    # Remove all Wickerman Docker images (core, downloader, and all plugin builds)
    for img in ("wickerman-core", "wickerman-downloader", "wickerman_core", "wickerman_downloader"):
        run(f"docker rmi -f {img}", ignore=True)
    # Remove plugin images built by the dashboard (wickerman/wm-*)
    result = subprocess.run(
        "docker images --format '{{.Repository}}:{{.Tag}}' | grep '^wickerman/'",
        shell=True, capture_output=True, text=True
    )
    for img in result.stdout.strip().split("\n"):
        if img.strip():
            run(f"docker rmi -f {img.strip()}", ignore=True)

    # Clear plugin data dirs and manifests so everything gets rewritten fresh
    if SUPPORT_DIR.exists():
        plugins_dir = SUPPORT_DIR / "plugins"
        if plugins_dir.exists():
            print("  Clearing plugin build data and manifests...")
            for item in plugins_dir.iterdir():
                if item.is_dir():
                    # Nuke the entire data/ subdir (extracted source, build cache, binaries)
                    data_dir = item / "data"
                    if data_dir.exists():
                        run(f"sudo rm -rf {data_dir}", ignore=True)
                elif item.suffix == ".json":
                    # Remove manifest so installer rewrites it from wickerman_plugins.py
                    run(f"sudo rm -f {item}", ignore=True)

    # Prune dangling Docker layers
    print("  Pruning dangling Docker resources...")
    run("docker system prune -f", ignore=True)

    # Remove install directory
    if INSTALL_DIR.exists():
        run(f"sudo rm -rf {INSTALL_DIR}", ignore=True)
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN INSTALLER
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Wickerman OS Installer")
    ap.add_argument("--reset", action="store_true", help="Stop managed containers")
    ap.add_argument("--hard-reset", action="store_true", help="Total wipe (preserves WickermanSupport)")
    args = ap.parse_args()

    print(f"\n  WICKERMAN OS v{VERSION} — Installer")
    print(f"  {'='*40}")

    if args.hard_reset:
        hard_nuke()
    elif args.reset:
        run("docker rm -f $(docker ps -a --filter label=wickerman.managed=true -q)", ignore=True)

    # ── Upgrade stash logic ──────────────────────────────────────────────
    STASH = INSTALL_DIR.parent / ".wickerman_upgrade_stash"
    USER_DIRS = ("plugins", "workspace", "data")
    APP_DIRS  = ("core_app", "downloader", "nginx")

    if STASH.exists():
        print("  Recovering from interrupted upgrade...")
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        for item in STASH.iterdir():
            dest = INSTALL_DIR / item.name
            if not dest.exists():
                try:
                    os.rename(str(item), str(dest))
                except OSError:
                    shutil.copytree(str(item), str(dest))
                    shutil.rmtree(str(item))
        shutil.rmtree(STASH, ignore_errors=True)

    if INSTALL_DIR.exists():
        run(f"sudo chown -R {_REAL_USER or '$USER'} {INSTALL_DIR}", ignore=True)
        run(f"chmod -R u+rwX {INSTALL_DIR}", ignore=True)
        STASH.mkdir(exist_ok=True)
        for d in USER_DIRS:
            src = INSTALL_DIR / d
            if src.exists():
                try:
                    os.rename(str(src), str(STASH / d))
                except OSError:
                    shutil.copytree(str(src), str(STASH / d))
                    shutil.rmtree(str(src))
        for d in APP_DIRS:
            p = INSTALL_DIR / d
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        for f in list(INSTALL_DIR.iterdir()):
            if f.is_file():
                try:
                    f.unlink()
                except PermissionError:
                    run(f"sudo rm -f {f}", ignore=True)
        for d in USER_DIRS:
            src = STASH / d
            if src.exists():
                try:
                    os.rename(str(src), str(INSTALL_DIR / d))
                except OSError:
                    shutil.copytree(str(src), str(INSTALL_DIR / d))
                    shutil.rmtree(str(src))
        shutil.rmtree(STASH, ignore_errors=True)

    # ── Create WickermanSupport persistent directory ─────────────────────
    print(f"  Persistent storage: {SUPPORT_DIR}")
    for d in ["models", "datasets", "loras", "cache", "plugins"]:
        (SUPPORT_DIR / d).mkdir(parents=True, exist_ok=True)
    run(f"chown -R {_REAL_USER or '$USER'} {SUPPORT_DIR}", ignore=True)

    # ── Copy bundled models ──────────────────────────────────────────
    INSTALLER_DIR = Path(__file__).parent.resolve()
    local_models = INSTALLER_DIR / "models"
    if local_models.is_dir():
        dest_models = SUPPORT_DIR / "models"
        copied = 0
        for model_file in local_models.iterdir():
            if model_file.is_file() and model_file.suffix == ".gguf":
                dest = dest_models / model_file.name
                if not dest.exists():
                    print(f"  Copying model: {model_file.name}...")
                    shutil.copy2(str(model_file), str(dest))
                    copied += 1
                else:
                    print(f"  Model already exists: {model_file.name}")
        if copied:
            print(f"  Copied {copied} model(s) to {dest_models}")
    else:
        print("  No local models/ directory found (models can be downloaded later)")

    # ── Stop old containers and remove cached images ─────────────────────
    for c in ("wm-core", "wm-gateway", "wm-downloader"):
        run(f"docker rm -f {c}", ignore=True)
    for img in ("wickerman-core", "wickerman-downloader", "wickerman_core", "wickerman_downloader"):
        if run(f"docker image inspect {img}", ignore=True):
            run(f"docker rmi -f {img}", ignore=True)

    # ── Create directory structure ───────────────────────────────────────
    for d in ["core_app", "downloader/templates", "nginx", "plugins", "workspace", "data/logs"]:
        (INSTALL_DIR / d).mkdir(parents=True, exist_ok=True)
    run(f"chown -R {_REAL_USER or '$USER'} {INSTALL_DIR}", ignore=True)

    # ── Write embedded files ─────────────────────────────────────────────
    print("  Writing application files...")
    write_file(INSTALL_DIR / "core_app/main.py", MAIN_PY)
    write_file(INSTALL_DIR / "core_app/Dockerfile", CORE_DOCKERFILE)
    write_file(INSTALL_DIR / "downloader/app.py", DOWNLOADER_APP_PY)
    write_file(INSTALL_DIR / "downloader/templates/index.html", DOWNLOADER_INDEX_HTML)
    write_file(INSTALL_DIR / "downloader/requirements.txt", DOWNLOADER_REQUIREMENTS)
    write_file(INSTALL_DIR / "downloader/Dockerfile", DOWNLOADER_DOCKERFILE)
    write_file(INSTALL_DIR / "nginx/generate_nginx.py", GENERATE_NGINX_PY, chmod=0o755)

    # ── Write bundled plugin manifests ───────────────────────────────
    if ALL_PLUGINS:
        print(f"  Writing {len(ALL_PLUGINS)} plugin manifests...")
        for fname, manifest in ALL_PLUGINS.items():
            support_path = SUPPORT_DIR / "plugins" / fname
            write_file(support_path, json.dumps(manifest, indent=2))

    # ── Docker Compose ───────────────────────────────────────────────────
    has_gpu = run("nvidia-smi -L", ignore=True)
    compose = f"""
services:
  core:
    build: core_app
    container_name: wm-core
    labels: [wickerman.managed=true]
    environment:
      - HOST_INSTALL_DIR={INSTALL_DIR}
      - HOST_SUPPORT_DIR={SUPPORT_DIR}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - "{INSTALL_DIR}:/app"
      - "{SUPPORT_DIR}:/support"
    restart: unless-stopped
    networks: [wm-net]
    {'''deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]''' if has_gpu else ''}
  downloader:
    build: downloader
    container_name: wm-downloader
    labels: [wickerman.managed=true]
    volumes:
      - "{SUPPORT_DIR}/models:/data/models"
      - "{SUPPORT_DIR}:/data/support"
      - "{INSTALL_DIR}/data:/data"
    restart: unless-stopped
    networks: [wm-net]
  gateway:
    image: nginx:alpine
    container_name: wm-gateway
    labels: [wickerman.managed=true]
    ports: ["80:80"]
    volumes: ["{INSTALL_DIR}/nginx/nginx.conf:/etc/nginx/nginx.conf:ro"]
    depends_on: [core, downloader]
    restart: unless-stopped
    networks: [wm-net]
networks:
  wm-net:
    external: true
"""
    write_file(INSTALL_DIR / "docker-compose.yml", compose)

    # Resolve the real non-root user
    shell_user = _REAL_USER or os.environ.get("USER", "")
    if not shell_user or shell_user == "root":
        if os.geteuid() == 0:
            print("[FATAL] Running as root without SUDO_USER set.")
            print("  Run with: sudo python3 wickermaninstall.py")
            print("  Do NOT run as: sudo su → python3 wickermaninstall.py")
            sys.exit(1)
        shell_user = os.environ.get("USER", "nobody")

    # ── Start script ─────────────────────────────────────────────────────
    sh = f"""#!/bin/bash
set -e
chown -R {shell_user} {INSTALL_DIR} 2>/dev/null || true
chown -R {shell_user} {SUPPORT_DIR} 2>/dev/null || true
docker network create wm-net 2>/dev/null || true
# Generate nginx config (must exist before compose mounts it)
python3 {INSTALL_DIR}/nginx/generate_nginx.py
# Verify vhosts were written
VHOST_COUNT=$(grep -c "server_name" {INSTALL_DIR}/nginx/nginx.conf)
echo "Nginx config: $VHOST_COUNT server blocks"
# Stop gateway so compose recreates it with the fresh config
docker rm -f wm-gateway 2>/dev/null || true
# Build and start all containers
docker compose up -d --build
sleep 2
# Verify the mounted config inside the container
echo "Verifying gateway config..."
docker exec wm-gateway grep "server_name" /etc/nginx/nginx.conf
echo "Wickerman OS is ready — http://wickerman.local"
"""
    write_file(INSTALL_DIR / "start.sh", sh, chmod=0o755)

    # ── Patch /etc/hosts (if running as root) ────────────────────────────
    if os.geteuid() == 0:
        all_hosts = HOSTS_NEEDED + list(PLUGIN_HOSTS)
        with open("/etc/hosts", "r") as f:
            current = f.read()
        missing = [f"{ip} {h}" for ip, h in all_hosts if h not in current]
        if missing:
            with open("/etc/hosts", "a") as f:
                f.write("\n" + "\n".join(missing) + "\n")
            print(f"  Added {len(missing)} hosts entries")

    # ── Initialize Git repos ────────────────────────────────────────────
    print("  Initializing version control...")

    # Install repo — tracks system config
    install_gitignore = """__pycache__/
*.pyc
*.log
.env
"""
    write_file(INSTALL_DIR / ".gitignore", install_gitignore)
    if not (INSTALL_DIR / ".git").exists():
        run(f"git -C {INSTALL_DIR} init", ignore=True)
        run(f"git -C {INSTALL_DIR} config user.name 'Wickerman OS'", ignore=True)
        run(f"git -C {INSTALL_DIR} config user.email 'wickerman@local'", ignore=True)
    run(f"git -C {INSTALL_DIR} add -A", ignore=True)
    install_has_commits = run(f"git -C {INSTALL_DIR} rev-parse HEAD", ignore=True)
    run(f'git -C {INSTALL_DIR} commit -m "Wickerman OS v{VERSION} — {"reinstall" if install_has_commits else "fresh install"}"', ignore=True)

    # Support repo — tracks plugins and configs (not models/datasets)
    support_gitignore = """models/
datasets/
loras/
*.gguf
*.bin
*.safetensors
__pycache__/
*.pyc
"""
    write_file(SUPPORT_DIR / ".gitignore", support_gitignore)
    if not (SUPPORT_DIR / ".git").exists():
        run(f"git -C {SUPPORT_DIR} init", ignore=True)
        run(f"git -C {SUPPORT_DIR} config user.name 'Wickerman OS'", ignore=True)
        run(f"git -C {SUPPORT_DIR} config user.email 'wickerman@local'", ignore=True)
    run(f"git -C {SUPPORT_DIR} add -A", ignore=True)
    support_has_commits = run(f"git -C {SUPPORT_DIR} rev-parse HEAD", ignore=True)
    run(f'git -C {SUPPORT_DIR} commit -m "WickermanSupport — {"post-install sync" if support_has_commits else "initial state"}"', ignore=True)

    # Fix .git ownership so host-side git commands work without sudo
    run(f"chown -R {shell_user} {INSTALL_DIR}/.git", ignore=True)
    run(f"chown -R {shell_user} {SUPPORT_DIR}/.git", ignore=True)

    # ── Done ─────────────────────────────────────────────────────────────
    print(f"\n  ✓ Wickerman OS v{VERSION} installed.")
    print(f"    Install dir : {INSTALL_DIR}")
    print(f"    Support dir : {SUPPORT_DIR}  (models, datasets — survives --hard-reset)")
    print(f"\n    Run:  cd {INSTALL_DIR} && ./start.sh\n")

if __name__ == "__main__":
    main()
