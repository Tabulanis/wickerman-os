#!/bin/bash
# Stops the Core, Gateway, and all running Plugins

echo "🔌 Shutting down Wickerman OS..."

# 1. Stop the Compose Stack (Core + Gateway)
cd ~/wickerman || exit
if [ -f "docker-compose.yml" ]; then
    docker compose down
else
    echo "Compose file not found, skipping..."
fi

# 2. Stop any remaining Plugin containers (Anything named wm-*)
# We use 'docker ps' to find them, and 'docker stop' to halt them safely.
PLUGINS=$(docker ps -q --filter name="wm-*")

if [ -n "$PLUGINS" ]; then
    echo "🛑 Stopping plugins..."
    docker stop $PLUGINS
    echo "✓ Plugins stopped."
else
    echo "✓ No active plugins found."
fi

echo "😴 System Offline."
