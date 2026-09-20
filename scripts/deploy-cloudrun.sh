#!/usr/bin/env bash
# Deploy the backend to Google Cloud Run. Run from the repo root:
#
#   PROJECT_ID=my-gcp-project CORS_ORIGINS=https://my-app.vercel.app scripts/deploy-cloudrun.sh
#
# Prerequisites you do yourself, once:
#   1. Install the gcloud CLI      https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login
#   3. A GCP project with billing enabled
#
# No local Docker is needed: --source uploads the repo and Cloud Build builds the root
# Dockerfile on linux/amd64. (Building on an Apple Silicon Mac would produce an arm64
# image that Cloud Run cannot run, so prefer this path.)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID to your GCP project id}"
SERVICE="${SERVICE:-traffic-ops-backend}"
REGION="${REGION:-us-central1}"
# Comma-separated browser origins allowed to call the API, e.g. the Vercel URL.
CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5173}"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT_ID"

# Why these flags. The backend is a stateful, always-running digital twin, not a request
# handler, so several Cloud Run defaults are wrong for it:
#
#   --no-cpu-throttling   Required. By default Cloud Run throttles CPU to ~0 between
#                         requests; the live SUMO thread would freeze whenever nobody was
#                         watching, and simulated time would stop advancing.
#   --min-instances 1     Keeps the city warm. At 0 the twin is rebuilt (and the 300 s
#                         warm-up re-run) on the first request after every scale-down.
#   --max-instances 1     Required. City state lives in one process and one thread owns the
#                         live TraCI connection. A second instance is a second, divergent
#                         city answering the same URL.
#   --cpu 4               Matches SCENARIO_WORKERS=4: one analysis runs four SUMO branch
#                         processes in parallel. Fewer vCPUs only makes a run slower.
#   --timeout 3600        The per-request cap, which for /ws/state is the WebSocket's
#                         lifetime. 3600 s is the Cloud Run maximum; the frontend
#                         reconnects after a second, so a drop is invisible.
#   --session-affinity    Belt and braces behind max-instances 1.
#
# CORS_ORIGINS is passed with gcloud's '^@^' delimiter syntax because the value itself
# contains commas, which gcloud would otherwise read as its own separator. (SUMO_GUI is
# already false in the image, so it is not repeated here.)
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 4 \
  --memory 4Gi \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 1 \
  --concurrency 80 \
  --timeout 3600 \
  --session-affinity \
  --set-env-vars "^@^CORS_ORIGINS=${CORS_ORIGINS}"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
cat <<MSG

Deployed: $URL
  health   $URL/api/health
  API docs $URL/docs
  MCP      $URL/mcp

Point the frontend at it (Vercel project -> Settings -> Environment Variables):
  VITE_API_BASE_URL=$URL
  VITE_WS_BASE_URL=${URL/https:/wss:}

This service is billed while idle (see the flag notes in this script). To stop paying
without deleting it:  gcloud run services update $SERVICE --region $REGION --min-instances 0
MSG
