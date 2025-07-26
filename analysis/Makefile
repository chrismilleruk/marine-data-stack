# Sailing Polar Analysis Makefile

# Adjust these if your filenames differ
NMEA_LOGS := skserver-raw_*.log
TRACK_GPX := 2025-07-24.gpx
NMEA_CSV := nmea.csv
COMBINED := combined.csv
POLAR := polar.csv
PLOT := polar.png

.PHONY: all clean plot

all: $(PLOT)

$(NMEA_CSV): $(NMEA_LOGS)
	python parse_nmea.py $(NMEA_LOGS) > $(NMEA_CSV)

$(COMBINED): $(NMEA_CSV)
	python build_dataset.py $(NMEA_CSV) \
	  --output $(COMBINED) \
	  --exclude-engine \
	  --start-time 2025-07-26T13:15:00 \
	  --end-time 2025-07-26T15:15:00

$(POLAR): $(COMBINED)
	python analyze_polar.py $(COMBINED) \
	  --output $(POLAR) \
	  --by-tack \
	  --aws-bin-size 5

$(PLOT): $(POLAR)
	python plot_polar.py $(POLAR) --by-aws --output $(PLOT)

clean:
	rm -f $(NMEA_CSV) $(COMBINED) $(POLAR) $(PLOT)
