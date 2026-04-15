FROM balenalib/raspberrypi4-64-python:3.11-bookworm-run

RUN install_packages \
    python3-dev \
    python3-pip \
    gcc \
    libc6-dev \
    libopenjp2-7 \
    libtiff6 \
    fonts-dejavu-core \
    git

# Waveshare e-Paper library
RUN git clone --depth 1 https://github.com/waveshare/e-Paper.git /opt/e-Paper

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY tides_display.py /app/tides_display.py

WORKDIR /app

ENV EPAPER_LIB_PATH=/opt/e-Paper/RaspberryPi_JetsonNano/python/lib

CMD ["python3", "tides_display.py"]
