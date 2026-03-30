FROM python:3.12-slim

WORKDIR /app

COPY replay_server.py .
COPY tracks/ ./tracks/
COPY manifest.json .

EXPOSE 10110/tcp
EXPOSE 10111/udp
EXPOSE 10180/tcp

ENV TRACK=""
ENV SPEED="1.0"
ENV EXCLUDE="AIVDM,AIVDO"

CMD python3 replay_server.py \
    --tracks-dir ./tracks \
    --tcp-port 10110 \
    --udp-port 10111 \
    --udp-dest 255.255.255.255 \
    --http-port 10180 \
    --speed ${SPEED} \
    --exclude ${EXCLUDE} \
    ${TRACK:+--track $TRACK --autoplay}
