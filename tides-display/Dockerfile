FROM balenalib/raspberrypi4-64-python:3.11-bookworm-run

RUN install_packages \
    python3-dev \
    python3-pip \
    gcc \
    libc6-dev \
    libopenjp2-7 \
    libtiff6 \
    fonts-dejavu-core

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY waveshare_epd/ /app/waveshare_epd/
COPY tides_display.py /app/tides_display.py

WORKDIR /app

CMD ["python3", "tides_display.py"]
