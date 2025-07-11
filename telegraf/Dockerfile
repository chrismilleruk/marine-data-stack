FROM telegraf:latest

# Copy configuration files
COPY telegraf.conf /etc/telegraf/telegraf.conf
COPY migrate-historic-data.conf /etc/telegraf/migrate-historic-data.conf

# Copy migration script and make it executable
COPY migrate-historic-rate-limited.sh /etc/telegraf/migrate-historic-rate-limited.sh
RUN chmod +x /etc/telegraf/migrate-historic-rate-limited.sh

# The default CMD from the base image will run Telegraf with the config 