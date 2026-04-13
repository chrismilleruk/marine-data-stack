#!/bin/bash
# SignalK first-boot config seeder
# Runs as root, seeds config files, then starts SignalK as node user

SIGNALK_HOME="/home/node/.signalk"
SEED_DIR="/opt/signalk-seed"

echo "=== SignalK Config Seeder ==="

# Seed settings.json if missing or outdated
if [ ! -f "$SIGNALK_HOME/settings.json" ] || ! grep -q "ttyAMA" "$SIGNALK_HOME/settings.json" 2>/dev/null; then
    echo "Seeding settings.json"
    cp "$SEED_DIR/settings.json" "$SIGNALK_HOME/settings.json"
    chown node:node "$SIGNALK_HOME/settings.json"
fi

# Seed package.json if missing plugins
if ! grep -q "signalk-auto-polar" "$SIGNALK_HOME/package.json" 2>/dev/null; then
    echo "Seeding package.json"
    cp "$SEED_DIR/package.json" "$SIGNALK_HOME/package.json"
    chown node:node "$SIGNALK_HOME/package.json"
fi

# Seed plugin configs if missing
if [ ! -d "$SIGNALK_HOME/plugin-config-data" ] || [ -z "$(ls -A $SIGNALK_HOME/plugin-config-data 2>/dev/null)" ]; then
    echo "Seeding plugin-config-data"
    mkdir -p "$SIGNALK_HOME/plugin-config-data"
    cp "$SEED_DIR/plugin-config-data/"*.json "$SIGNALK_HOME/plugin-config-data/"
    chown -R node:node "$SIGNALK_HOME/plugin-config-data"
fi

# Install plugins if node_modules is missing
if [ ! -d "$SIGNALK_HOME/node_modules" ]; then
    echo "Installing plugins from package.json (this may take a few minutes on first boot)..."
    cd "$SIGNALK_HOME"
    su -s /bin/sh node -c "npm install --omit=dev 2>&1" | tail -10
    echo "Plugin install complete"
fi

# Ensure all files in signalk home are owned by node
chown -R node:node "$SIGNALK_HOME"

echo "=== Starting SignalK ==="
export HOME=/home/node
exec su -s /bin/sh node -c "/home/node/signalk/startup.sh"
