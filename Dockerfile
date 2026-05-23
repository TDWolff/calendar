FROM docker.io/python:3.10

WORKDIR /

# --- [Install python and pip] ---
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y python3 python3-pip git

COPY requirements.txt /requirements.txt
RUN pip install -r requirements.txt
RUN pip install gunicorn

COPY . /

ENV GUNICORN_CMD_ARGS="--workers=1 --bind=0.0.0.0:8086"

EXPOSE 8086

CMD [ "gunicorn", "main:app" ]
