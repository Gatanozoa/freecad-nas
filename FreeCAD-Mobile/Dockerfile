ARG PYTHON_BASE=mirror.gcr.io/library/python:3.13-slim-trixie
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mobile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mobile ./mobile
RUN mkdir -p /data && chown 10001:10001 /data \
    && find /opt/mobile -type d -exec chmod 0755 {} + \
    && find /opt/mobile -type f -exec chmod 0644 {} +
USER 10001:10001
EXPOSE 8000
CMD ["python","-m","uvicorn","mobile.api:create_app","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]
