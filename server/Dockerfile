# Build context = repo root (the server needs shared/ too):
#   docker build -f server/Dockerfile https://github.com/NAKE-1/chessfish.git#main
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/shared:/app/server CHESSFISH_DATA=/data
WORKDIR /app/server
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY server/app ./app
COPY server/web ./web
RUN useradd --system --uid 10001 chessfish && mkdir /data && chown chessfish /data
USER chessfish
VOLUME /data
EXPOSE 6767 6768
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6767/healthz', timeout=3)"
CMD ["python", "-m", "app.main"]
