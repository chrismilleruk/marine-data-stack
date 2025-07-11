# Telegraf Configuration for Marine Data Stack

This directory contains Telegraf configurations for receiving data from SignalK and forwarding it to InfluxDB Cloud.

## Architecture

SignalK sends data to:
1. Local InfluxDB (port 8086) - for local storage and Grafana dashboards
2. Telegraf influxdb_listener (port 8186) - which forwards to InfluxDB Cloud v3

This approach avoids the need to sync data from InfluxDB, as SignalK can send to multiple endpoints.

## Configuration Files

1. **telegraf.conf** - Main configuration for receiving SignalK data and forwarding to cloud
2. **migrate-historic-data.conf** - (Deprecated) Bulk historic data migration 
3. **migrate-historic-rate-limited.sh** - (Deprecated) Rate-limited migration script

## Environment Variables Required

Create a `.env` file in the project root with:

```bash
# Cloud InfluxDB Configuration
CLOUD_INFLUX_URL=https://us-west-2-1.aws.cloud2.influxdata.com
CLOUD_INFLUX_TOKEN=your-cloud-influx-token
CLOUD_INFLUX_ORG=your-cloud-org
CLOUD_INFLUX_BUCKET=your-cloud-bucket

# Balena Device Name (optional)
BALENA_DEVICE_NAME_AT_INIT=my-boat-name
```

## Usage

### Continuous Data Forwarding

The main `telegraf.conf` is configured to:
- Listen on port 8186 for InfluxDB line protocol data from SignalK
- Forward this data to your InfluxDB Cloud v3 instance
- Run continuously as part of the docker-compose stack

To start:
```bash
docker-compose up -d telegraf
```

### SignalK Configuration

Configure SignalK to send data to both:
1. Local InfluxDB: `http://influxdb:8086`
2. Telegraf: `http://telegraf:8186`

In SignalK, add a second InfluxDB writer plugin instance pointing to `http://telegraf:8186`.

### Getting Your InfluxDB Cloud Token

1. Log into your InfluxDB Cloud account
2. Go to Data > Tokens
3. Create a new token with write access to your bucket

## Notes

- The continuous sync filters out "telegraf" measurements to avoid feedback loops
- Monitor the Telegraf logs for any errors: `docker logs telegraf`
- InfluxDB Cloud v3 has different rate limits than v2, check your plan's limits