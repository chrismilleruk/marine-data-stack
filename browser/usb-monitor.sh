#!/bin/bash

# USB Touchscreen Monitor Script
# Monitors for USB touchscreen connection and reinitializes input when detected

LOG_FILE="/var/log/usb-touchscreen-monitor.log"
TOUCHSCREEN_DETECTED=false

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to check if touchscreen is present
check_touchscreen() {
    # Check for common touchscreen identifiers in USB devices
    if lsusb | grep -iE "touch|digitizer|eGalax|EETI|Elo|3M MicroTouch" > /dev/null; then
        return 0
    fi
    
    # Check for HID devices that might be touchscreens
    if [ -d /sys/class/hidraw ]; then
        for device in /sys/class/hidraw/hidraw*/device/uevent; do
            if [ -f "$device" ] && grep -iE "touch|digitizer" "$device" > /dev/null 2>&1; then
                return 0
            fi
        done
    fi
    
    # Check input devices
    if grep -iE "touch|digitizer" /proc/bus/input/devices > /dev/null 2>&1; then
        return 0
    fi
    
    return 1
}

# Function to reinitialize X input devices
reinit_x_input() {
    log "Reinitializing X input devices..."
    
    # Set display and auth
    export DISPLAY=:6
    export XAUTHORITY=/tmp/serverauth.NlHelpPbD0
    
    # Try to detect new input devices in X
    if command -v xinput > /dev/null 2>&1; then
        # List current devices
        xinput list 2>/dev/null | grep -v "Virtual" | while read -r line; do
            log "Detected X input device: $line"
        done
        
        # Enable all pointer devices
        xinput list 2>/dev/null | grep "slave  pointer" | grep -v "Virtual" | sed 's/.*id=\([0-9]*\).*/\1/' | while read -r id; do
            xinput enable "$id" 2>/dev/null && log "Enabled input device ID: $id"
        done
    fi
    
    # Restart chromium if needed (optional - uncomment if you want full restart)
    # log "Restarting browser..."
    # pkill -f chromium
    # sleep 2
    # /usr/src/app/startx.sh &
}

# Function to setup udev rules for the touchscreen
setup_udev_rules() {
    cat > /etc/udev/rules.d/99-usb-touchscreen.rules << 'EOF'
# USB Touchscreen hotplug rules
ACTION=="add", SUBSYSTEM=="usb", ENV{ID_INPUT_TOUCHSCREEN}=="1", RUN+="/usr/local/bin/touchscreen-added.sh"
ACTION=="add", SUBSYSTEM=="input", ENV{ID_INPUT_TOUCHSCREEN}=="1", RUN+="/usr/local/bin/touchscreen-added.sh"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{bInterfaceClass}=="03", ATTRS{bInterfaceProtocol}=="00", RUN+="/usr/local/bin/touchscreen-added.sh"
EOF

    # Create the helper script
    cat > /usr/local/bin/touchscreen-added.sh << 'EOF'
#!/bin/bash
echo "$(date): Touchscreen added - $DEVPATH" >> /var/log/touchscreen-events.log
# Signal the monitor script
touch /tmp/touchscreen-added
EOF
    chmod +x /usr/local/bin/touchscreen-added.sh
    
    # Reload udev rules
    udevadm control --reload-rules
}

# Main monitoring loop
log "Starting USB touchscreen monitor..."
setup_udev_rules

# Initial check
if check_touchscreen; then
    TOUCHSCREEN_DETECTED=true
    log "Touchscreen already connected at startup"
    reinit_x_input
else
    log "No touchscreen detected at startup"
fi

# Monitor for changes
log "Monitoring for USB touchscreen changes..."

# Use inotifywait if available, otherwise poll
if command -v inotifywait > /dev/null 2>&1; then
    # Monitor /dev/input for changes
    while true; do
        inotifywait -e create,delete /dev/input/ /tmp/ 2>/dev/null | while read -r event; do
            if [[ "$event" == *"touchscreen-added"* ]] || [[ "$event" == *"/dev/input/"* ]]; then
                sleep 1  # Give device time to initialize
                if check_touchscreen; then
                    if [ "$TOUCHSCREEN_DETECTED" = false ]; then
                        log "Touchscreen connected!"
                        TOUCHSCREEN_DETECTED=true
                        reinit_x_input
                    fi
                else
                    if [ "$TOUCHSCREEN_DETECTED" = true ]; then
                        log "Touchscreen disconnected!"
                        TOUCHSCREEN_DETECTED=false
                    fi
                fi
                rm -f /tmp/touchscreen-added 2>/dev/null
            fi
        done
    done
else
    # Fallback: Poll every 2 seconds
    while true; do
        if check_touchscreen; then
            if [ "$TOUCHSCREEN_DETECTED" = false ]; then
                log "Touchscreen connected!"
                TOUCHSCREEN_DETECTED=true
                reinit_x_input
            fi
        else
            if [ "$TOUCHSCREEN_DETECTED" = true ]; then
                log "Touchscreen disconnected!"
                TOUCHSCREEN_DETECTED=false
            fi
        fi
        sleep 2
    done
fi 