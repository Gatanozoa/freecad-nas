ARG FREECAD_BASE=lscr.io/linuxserver/freecad:latest
ARG PYTHON_BASE=docker.io/library/python:3.13-slim-trixie
FROM ${PYTHON_BASE} AS api
LABEL org.opencontainers.image.version="1.3.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/cad
COPY requirements.txt /opt/cad/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY cad_service /opt/cad/cad_service
RUN mkdir -p /state /artifacts \
    && chown 10001:10001 /state /artifacts \
    && find /opt/cad -type d -exec chmod 0755 {} + \
    && find /opt/cad -type f -exec chmod 0644 {} +
USER 10001:10001
RUN python -c "from cad_service.api import create_app; from cad_service import __version__; assert __version__ == '1.3.0'; from pathlib import Path; [Path('cad_service/web', name).read_bytes() for name in ('index.html','app.js','style.css')]"
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "cad_service.api:application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM ${FREECAD_BASE} AS worker
LABEL org.opencontainers.image.version="1.3.0"
# Use the existing FreeCAD bundle; no apt/pip and no graphical desktop init.
USER root
WORKDIR /opt/cad
COPY cad_service /opt/cad/cad_service
RUN test -x /opt/freecad/usr/bin/python \
    && mkdir -p /state /artifacts \
    && chown 10001:10001 /state /artifacts \
    && find /opt/cad -type d -exec chmod 0755 {} + \
    && find /opt/cad -type f -exec chmod 0644 {} +
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/cad \
    LD_LIBRARY_PATH=/opt/freecad/usr/lib
USER 10001:10001
ENTRYPOINT ["/opt/freecad/usr/bin/python", "-m", "cad_service.worker"]
