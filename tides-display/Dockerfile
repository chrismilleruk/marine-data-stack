FROM balenalib/raspberrypi4-64-python:3.11-bookworm-run

RUN install_packages \
    gcc \
    libc6-dev \
    libopenjp2-7 \
    libtiff6 \
    fonts-dejavu-core

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /app/requirements.txt && \
    python3 -c "import requests, PIL; print('deps OK')"

COPY waveshare_epd/ /app/waveshare_epd/
COPY tides_display.py /app/tides_display.py

WORKDIR /app

CMD ["python3", "tides_display.py"]
