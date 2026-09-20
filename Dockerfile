# Backend container for the Traffic Operations Center (FastAPI + Eclipse SUMO).
# Built for Google Cloud Run, but it runs on any container host (Fly.io, Render, a VM).
#
# The build context is the REPO ROOT: app.config.REPO_ROOT resolves SCENARIO_DIR and
# MEMORY_DIR relative to the directory that holds backend/, so simulation/ must sit
# alongside backend/ in the image.
FROM python:3.12-slim

# Runtime shared libraries the SUMO binaries link against. SUMO itself ships inside the
# `eclipse-sumo` wheel (py3-none-manylinux_2_28_x86_64, carrying the compiled binaries), so
# there is no apt package for SUMO here. The wheel bundles most of its dependencies (FOX,
# xerces, proj, geos, gdal, freetype, ...); this list is exactly the DT_NEEDED entries it
# does NOT bundle, minus what python:3.12-slim already has.
#
# The X11 libraries are not optional and not only for sumo-gui: the headless `sumo` binary
# is linked against FOX, so it fails at startup with "error while loading shared libraries:
# libXrender.so.1" without them. There is no display involved.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 \
      libx11-6 \
      libxext6 \
      libxrender1 \
      libatomic1 \
      libexpat1 \
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
