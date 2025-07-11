#!/bin/bash

# Rate-limited historic data migration for InfluxDB Cloud free tier
# This script runs INSIDE the Telegraf container on the Balena device
# All environment variables are already available

# Usage: ./migrate-inside-container.sh [days] [start_offset]
# Examples:
#   ./migrate-inside-container.sh          # Migrate last 30 days
#   ./migrate-inside-container.sh 7        # Migrate last 7 days
#   ./migrate-inside-container.sh 30 30   # Migrate days 30-60 ago

# Number of days to migrate (default: 30)
DAYS_TO_MIGRATE=${1:-30}
# Starting offset in days (default: 0, meaning start from today)
START_OFFSET=${2:-0}

echo "Starting rate-limited migration..."
echo "Migrating $DAYS_TO_MIGRATE days of data, starting from $START_OFFSET days ago"
echo "This will cover the period from $((START_OFFSET + DAYS_TO_MIGRATE)) to $START_OFFSET days ago"
echo "Estimated time: $(($DAYS_TO_MIGRATE * 2)) minutes"
echo ""

# Track statistics
TOTAL_MIGRATED=0
DAYS_WITH_DATA=0
DAYS_WITHOUT_DATA=0
FAILED_DAYS=0

# Create a temporary config file for each day
for i in $(seq 0 $((DAYS_TO_MIGRATE - 1))); do
    day=$((START_OFFSET + DAYS_TO_MIGRATE - i))
    echo "[$((i + 1))/$DAYS_TO_MIGRATE] Processing data from $day days ago..."
    
    # Calculate date range
    START_DAY=$((day))
    END_DAY=$((day - 1))
    
    # Create migration config for this specific day
    cat > /tmp/migrate-day-${day}.conf << EOF
# Telegraf configuration for migrating day -${START_DAY}
[agent]
  interval = "1s"
  round_interval = false
  metric_batch_size = 500
  metric_buffer_limit = 10000
  collection_jitter = "0s"
  flush_interval = "1s"
  flush_jitter = "0s"
  precision = "s"
  hostname = "${BALENA_DEVICE_NAME_AT_INIT}"
  omit_hostname = false
  # Run once and exit
  once = true

[[outputs.influxdb_v2]]
  urls = ["${CLOUD_INFLUX_URL}"]
  token = "${CLOUD_INFLUX_TOKEN}"
  organization = "${CLOUD_INFLUX_ORG}"
  bucket = "${CLOUD_INFLUX_BUCKET}"
  timeout = "60s"

[[inputs.influxdb_v2]]
  urls = ["http://influxdb:8086"]
  token = "${LOCAL_INFLUX_TOKEN}"
  organization = "${LOCAL_INFLUX_ORG}"
  bucket = "${LOCAL_INFLUX_BUCKET}"
  query = '''
    from(bucket: "${LOCAL_INFLUX_BUCKET}")
      |> range(start: -${START_DAY}d, stop: -${END_DAY}d)
      |> filter(fn: (r) => r._measurement != "telegraf")
  '''
  interval = "1s"
  timeout = "5m"
EOF

    # Run Telegraf for this day's data
    telegraf --config /tmp/migrate-day-${day}.conf --once
    
    # Check if migration was successful
    if [ $? -eq 0 ]; then
        echo "  ✓ Successfully migrated data from $day days ago"
        TOTAL_MIGRATED=$((TOTAL_MIGRATED + 1))
        DAYS_WITH_DATA=$((DAYS_WITH_DATA + 1))
    else
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 1 ]; then
            echo "  ○ No data found for day $day (or already migrated)"
            DAYS_WITHOUT_DATA=$((DAYS_WITHOUT_DATA + 1))
        else
            echo "  ✗ Failed to migrate data from $day days ago (exit code: $EXIT_CODE)"
            FAILED_DAYS=$((FAILED_DAYS + 1))
        fi
    fi
    
    # Clean up temporary config
    rm -f /tmp/migrate-day-${day}.conf
    
    # Wait 2 minutes between days to respect rate limits
    if [ $i -lt $((DAYS_TO_MIGRATE - 1)) ] && [ $DAYS_WITH_DATA -gt 0 ]; then
        echo "  ⏳ Waiting 2 minutes before next migration to respect rate limits..."
        sleep 120
    fi
    
    echo ""
done

echo "Migration Summary:"
echo "=================="
echo "✓ Days successfully migrated: $TOTAL_MIGRATED"
echo "○ Days without data: $DAYS_WITHOUT_DATA"
echo "✗ Days failed: $FAILED_DAYS"
echo ""

if [ $FAILED_DAYS -gt 0 ]; then
    echo "Note: Some days failed to migrate. Wait 5 minutes for rate limits to reset,"
    echo "then run the script again with appropriate parameters to retry failed days."
fi

echo "To migrate older data, run:"
echo "  ./migrate-inside-container.sh $DAYS_TO_MIGRATE $((START_OFFSET + DAYS_TO_MIGRATE))" 