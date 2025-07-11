FROM bh.cr/g_lucas_houssa/browser

# Install additional tools for USB monitoring
RUN apt-get update && apt-get install -y \
    inotify-tools \
    usbutils \
    evtest \
    xinput \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the USB monitor script
COPY usb-monitor.sh /usr/local/bin/usb-monitor.sh
RUN chmod +x /usr/local/bin/usb-monitor.sh

# Create a supervisor config to run the monitor alongside the browser
RUN mkdir -p /etc/supervisor/conf.d
RUN echo "[program:usb-monitor]" > /etc/supervisor/conf.d/usb-monitor.conf && \
    echo "command=/usr/local/bin/usb-monitor.sh" >> /etc/supervisor/conf.d/usb-monitor.conf && \
    echo "autostart=true" >> /etc/supervisor/conf.d/usb-monitor.conf && \
    echo "autorestart=true" >> /etc/supervisor/conf.d/usb-monitor.conf && \
    echo "stderr_logfile=/var/log/usb-monitor.err.log" >> /etc/supervisor/conf.d/usb-monitor.conf && \
    echo "stdout_logfile=/var/log/usb-monitor.out.log" >> /etc/supervisor/conf.d/usb-monitor.conf 