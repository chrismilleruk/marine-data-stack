# Telegraf Configuration for Marine Data Stack

This directory contains Telegraf configurations for syncing data between local and cloud InfluxDB instances.

## Configuration Files

1. **telegraf.conf** - Main configuration for continuous data sync
2. **migrate-historic-data.conf** - Bulk historic data migration (for paid plans)
3. **migrate-historic-rate-limited.sh** - Rate-limited migration script for free tier

## Environment Variables Required

Create a `.env` file in the project root with:

```bash
# Local InfluxDB Configuration
LOCAL_INFLUX_TOKEN=your-local-influx-token
LOCAL_INFLUX_ORG=marine
LOCAL_INFLUX_BUCKET=signalk

# Cloud InfluxDB Configuration
CLOUD_INFLUX_URL=https://us-west-2-1.aws.cloud2.influxdata.com
CLOUD_INFLUX_TOKEN=your-cloud-influx-token
CLOUD_INFLUX_ORG=your-cloud-org
CLOUD_INFLUX_BUCKET=your-cloud-bucket

# Balena Device Name (optional)
BALENA_DEVICE_NAME_AT_INIT=my-boat-name
```

## Usage

### Continuous Data Sync

The main `telegraf.conf` is configured to:
- Read recent data (last 5 minutes) from local InfluxDB every minute
- Write this data to your cloud InfluxDB instance
- Run continuously as part of the docker-compose stack

To start:
```bash
docker-compose up -d telegraf
```

### One-Time Historic Data Migration

The migration script is designed to run inside the Telegraf container on your Balena device where all environment variables are already configured.

```bash
# SSH into your Balena device
balena ssh <device-name>

# Enter the Telegraf container
docker-compose exec telegraf /bin/bash

# Inside the container, run the migration script
cd /etc/telegraf
./migrate-historic-rate-limited.sh          # Migrate last 30 days
./migrate-historic-rate-limited.sh 7        # Migrate last 7 days  
./migrate-historic-rate-limited.sh 30 30   # Migrate days 30-60 ago
./migrate-historic-rate-limited.sh 30 60   # Migrate days 60-90 ago
```

The script will:
- Process data day by day to respect the 5MB/5min rate limit
- Wait 2 minutes between each day's migration
- Skip days without data automatically
- Provide a detailed summary at the end
- Suggest the command to continue with older data

### Getting Your InfluxDB Tokens

1. **Local InfluxDB Token**:
   ```bash
   # On the Balena device
   docker exec -it influxdb influx auth list
   ```

2. **Cloud InfluxDB Token**:
   - Log into your InfluxDB Cloud account
   - Go to Data > Tokens
   - Create a new token with read/write access to your bucket

## Notes

- The continuous sync filters out "telegraf" measurements to avoid feedback loops
- Historic migration runs once per invocation and processes the specified date range
- The migration script respects the 5MB/5min rate limit for free tier accounts
- Each day's data is migrated separately with 2-minute delays between days
- Monitor the Telegraf logs for any errors: `docker logs telegraf`
- If migrations fail due to rate limits, wait 5 minutes before retrying