FROM debian:bullseye-slim

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

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
    avnav-ocharts-plugin \
    # avnav-oesenc-plugin \
    # avnav-eniro-plugin \
    avnav-mapproxy-plugin \
    # avnav-obp-plugin \
    && rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /charts /data /user /user/maps /user/tracks /user/routes

# Expose ports
# 8080: Web interface
# 8081: NMEA over TCP (input)
# 8082: NMEA over TCP (output)
EXPOSE 8080 8081 8082

# Start AVNav
CMD ["avnav"]