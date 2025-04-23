FROM debian:bullseye-slim

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC
ENV HOME=/home/avnav

# Install dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gpg \
    apt-transport-https \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Add AVNav repository
RUN wget -O - https://www.free-x.de/debian/oss.boating.gpg.key | gpg --dearmor > /usr/share/keyrings/oss.boating.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/oss.boating.gpg] https://www.free-x.de/debian bullseye main contrib non-free" > /etc/apt/sources.list.d/boating.list

# Install AVNav
RUN apt-get update && apt-get install -y \
    avnav \
    && rm -rf /var/lib/apt/lists/*

# Optional: Install additional plugins if needed
RUN apt-get update && apt-get install -y \
    avnav-mapproxy-plugin \
    && rm -rf /var/lib/apt/lists/*

# Create home directory with correct permissions
RUN mkdir -p /home/avnav && \
    mkdir -p /home/avnav/avnav/log && \
    mkdir -p /home/avnav/avnav/user/maps && \
    mkdir -p /home/avnav/avnav/user/tracks && \
    mkdir -p /home/avnav/avnav/user/routes && \
    mkdir -p /home/avnav/charts && \
    # Important: For Balena, make these directories world-writable
    chmod -R 777 /home/avnav

# Create user avnav with UID 1000
RUN id -u avnav &>/dev/null || useradd -u 1000 -d /home/avnav -m -s /bin/bash avnav

# Copy config file
COPY avnav_server.xml /home/avnav/avnav_server.xml

# Expose ports
# 8080: Web interface
# 8081: NMEA over TCP (input)
# 8082: NMEA over TCP (output)
EXPOSE 8080 8081 8082

# For Balena: Use root to start avnav but specify config path
USER root
WORKDIR /home/avnav

# Start AVNav with explicit config path
CMD ["avnav", "-t", "/home/avnav/avnav_server.xml"]