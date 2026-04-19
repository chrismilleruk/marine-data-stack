FROM signalk/signalk-server:latest

USER root

# Ensure bcryptjs is available for security.json seeding
# (should already exist in the SK server image, but pin it)
RUN cd /home/node/signalk && npm ls bcryptjs 2>/dev/null || npm install --no-save bcryptjs

# Copy seed config files
COPY config/settings.json /opt/signalk-seed/settings.json
COPY config/package.json /opt/signalk-seed/package.json
COPY config/plugin-config-data/ /opt/signalk-seed/plugin-config-data/

# Copy entrypoint and make executable
COPY entrypoint.sh /opt/signalk-seed/entrypoint.sh
RUN chmod +x /opt/signalk-seed/entrypoint.sh && \
    chown -R node:node /opt/signalk-seed

# Run entrypoint as root so it can write to the volume, then startup.sh drops to node
ENTRYPOINT ["/opt/signalk-seed/entrypoint.sh"]
