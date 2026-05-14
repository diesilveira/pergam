FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=1111

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY server.py /app/server.py

RUN useradd --no-create-home --shell /usr/sbin/nologin --uid 10001 pergam && \
    chown -R pergam:pergam /app
USER pergam

EXPOSE 1111

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1111/healthz', timeout=2).status==200 else 1)"

CMD ["python3", "/app/server.py"]
