# Sailing Polar Analysis Makefile

# Adjust these if your filenames differ
NMEA_LOGS := skserver-raw_*.log
TRACK_GPX := 2025-07-24.gpx
NMEA_CSV := nmea.csv
COMBINED := combined.csv
POLAR := polar.csv
PLOT := polar.png
PLOT_TACK := polar_tack.png
VMG_RESULTS := vmg_results.csv
VMG_PLOT := vmg_analysis.png

.PHONY: all clean plot plot-tack vmg

all: $(PLOT) $(PLOT_TACK) $(VMG_RESULTS)

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

$(PLOT_TACK): $(POLAR)
	python plot_polar.py $(POLAR) --by-tack --output $(PLOT_TACK)

$(VMG_RESULTS): $(POLAR)
	python analyze_vmg.py $(POLAR) --output $(VMG_RESULTS) --plot $(VMG_PLOT)

# Convenience targets
plot: $(PLOT)
plot-tack: $(PLOT_TACK)
vmg: $(VMG_RESULTS)

clean:
	rm -f $(NMEA_CSV) $(COMBINED) $(POLAR) $(PLOT) $(PLOT_TACK) $(VMG_RESULTS) $(VMG_PLOT)
