# Backend container for the Traffic Operations Center (FastAPI + Eclipse SUMO).
# Built for Google Cloud Run, but it runs on any container host (Fly.io, Render, a VM).
#
# The build context is the REPO ROOT: app.config.REPO_ROOT resolves SCENARIO_DIR and
# MEMORY_DIR relative to the directory that holds backend/, so simulation/ must sit
# alongside backend/ in the image.
FROM python:3.12-slim

# Runtime shared libraries the SUMO binaries link against. SUMO itself ships inside the
# `eclipse-sumo` wheel (a py3-none-manylinux_2_28_x86_64 wheel carrying the compiled
# binaries), so there is no apt package for SUMO here. libgl1/libx11-6 are only needed by
# sumo-gui, which never runs in a container; they are kept so an interactive debug session
# inside the image works.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 \
      libxml2 \
      libgl1 \
      libx11-6 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so the ~150 MB SUMO wheel layer is cached across code changes.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --upgrade pip && pip install -r backend/requirements.txt

# Application code plus the network/demand data it reads relative to REPO_ROOT.
COPY backend ./backend
COPY simulation ./simulation
# Lessons are written here at runtime. On Cloud Run the filesystem is ephemeral, so the
# memory store resets when the instance restarts (see README, "Hosting the backend").
RUN mkdir -p ./memory

WORKDIR /srv/backend

# Cloud Run injects $PORT (8080); 8000 keeps a bare `docker run` matching local dev.
ENV PORT=8000
EXPOSE 8000

# Never try to open sumo-gui in a container.
ENV SUMO_GUI=false

# Exactly one uvicorn worker, always. The service is stateful: one thread owns the live
# TraCI connection and the city state lives in that process. A second worker would be a
# second, divergent city behind the same URL.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
