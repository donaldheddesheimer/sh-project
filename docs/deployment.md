# Deploy to Google Cloud Run

The root `Dockerfile` builds the React console and serves it from FastAPI. One Cloud Run URL
therefore carries the UI, REST API, MCP endpoint, WebSocket, and SUMO simulation.

This deployment is intentionally stateful: use one instance, keep CPU allocated, and expect
the in-memory city and filesystem-backed lessons to reset when the instance is replaced.

## Prerequisites

- A Google Cloud project with billing enabled
- The [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- Permission to enable APIs, deploy Cloud Run, create a service account, and manage secrets

Authenticate and enable the required services:

```bash
gcloud auth login
gcloud config set project PROJECT_ID
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com
```

## 1. Create the runtime identity

```bash
gcloud iam service-accounts create traffic-ops-runner \
  --display-name="Traffic Ops Cloud Run"
```

Replace `PROJECT_ID` below with the same project ID selected above.

## 2. Store model credentials

Create only the secrets you plan to use:

```bash
gcloud secrets create anthropic-api-key --replication-policy=automatic
gcloud secrets versions add anthropic-api-key --data-file=-
# Paste only the Anthropic key, then press Ctrl-D.

gcloud secrets create nvidia-api-key --replication-policy=automatic
gcloud secrets versions add nvidia-api-key --data-file=-
# Paste only the NVIDIA key, then press Ctrl-D.
```

Grant the runtime service account access:

```bash
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:traffic-ops-runner@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding nvidia-api-key \
  --member="serviceAccount:traffic-ops-runner@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Skip the commands for a provider you do not plan to configure.

## 3. Deploy Mock first

Prove the container, console, SUMO network, API, and WebSocket before attaching paid model
credentials:

```bash
gcloud run deploy traffic-ops-demo \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --service-account=traffic-ops-runner@PROJECT_ID.iam.gserviceaccount.com \
  --cpu=4 \
  --memory=4Gi \
  --min=1 \
  --max=1 \
  --concurrency=80 \
  --timeout=3600 \
  --session-affinity \
  --no-cpu-throttling
```

Why these settings matter:

- `--max=1` keeps one authoritative live city.
- `--no-cpu-throttling` lets SUMO advance between HTTP requests.
- `--min=1` keeps the warmed city available, but incurs idle cost.
- Four CPUs match the default four parallel scenario workers.
- The 60-minute timeout supports long-lived WebSockets; the frontend reconnects after a drop.

The repository also provides `PROJECT_ID=your-project scripts/deploy-cloudrun.sh` as the
short Mock-only deployment path.

## 4. Attach credentials without rebuilding

After the Mock deployment works:

```bash
gcloud run services update traffic-ops-demo \
  --region us-central1 \
  --update-secrets=ANTHROPIC_API_KEY=anthropic-api-key:latest,NVIDIA_API_KEY=nvidia-api-key:latest
```

For an organization-level Anthropic key, also run:

```bash
gcloud run services update traffic-ops-demo \
  --region us-central1 \
  --update-env-vars=ANTHROPIC_WORKSPACE_ID=YOUR_WORKSPACE_ID
```

Reload the console and verify that the **Agent** workspace names the analyst and reviewer models
you configured. Attaching the NVIDIA key is what makes the deployment model-backed, so prove the
deployed simulation path first: deploy without secrets and run an episode on the deterministic
team, then add the key.

## Security and lifecycle

The command above makes the service public, and this application has no authentication.
Anyone with the URL can operate the simulation and invoke exposed endpoints. Restrict ingress
or remove `--allow-unauthenticated` outside a controlled demonstration.

Lessons live on Cloud Run's ephemeral filesystem. They can survive repeated requests to a
warm instance but are not durable across replacement or restart.

When the demo ends, stop the always-warm instance:

```bash
gcloud run services update traffic-ops-demo --region us-central1 --min=0
```

Or remove the service:

```bash
gcloud run services delete traffic-ops-demo --region us-central1
```

Useful references: [deploying from source](https://cloud.google.com/run/docs/deploying-source-code),
[WebSockets](https://cloud.google.com/run/docs/triggering/websockets), and
[Secret Manager integration](https://cloud.google.com/run/docs/configuring/services/secrets).
