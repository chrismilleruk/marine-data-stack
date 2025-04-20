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
    # avnav-ocharts-plugin \
    # avnav-oesenc-plugin \
    # avnav-eniro-plugin \
    avnav-mapproxy-plugin \
    # avnav-obp-plugin \
    && rm -rf /var/lib/apt/lists/*

# Create home directory with correct ownership
RUN mkdir -p /home/avnav && \
    # mkdir -p /home/avnav/.avnav && \
    mkdir -p /home/avnav/avnav && \
    mkdir -p /home/avnav/charts && \
    chown -R 1000:1000 /home/avnav && \
    chmod -R 755 /home/avnav

# Create user avnav with UID 1000 if it doesn't exist
RUN id -u avnav &>/dev/null || useradd -u 1000 -d /home/avnav -m -s /bin/bash avnav

# Expose ports
# 8080: Web interface
# 8081: NMEA over TCP (input)
# 8082: NMEA over TCP (output)
EXPOSE 8080 8081 8082

# Change to avnav user
USER avnav
WORKDIR /home/avnav

# Start AVNav
CMD ["avnav"]