# Deploying lux-mcp to Google Cloud Run

This guide deploys `lux_mcp.py` as a streamable-HTTP MCP server on
[Google Cloud Run](https://cloud.google.com/run). Cloud Run is a good fit:
the container scales to zero, the free tier covers personal use, and the
deploy is one command. Once deployed you get an HTTPS URL like
`https://lux-mcp-abc123-uc.a.run.app/mcp` that any MCP client can connect to.

Local stdio use is unaffected — the same `lux_mcp.py` runs in both modes
depending on the `--http` flag.

## Prerequisites

- A Google account.
- A GCP project with **billing enabled** (Cloud Run has a generous free
  tier, but a billing account must be attached even to use it). Create one
  at <https://console.cloud.google.com/billing>.
- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) installed
  and on your PATH.
- This repo checked out locally with the `Dockerfile` and
  `requirements.txt` at the root (already present).

You do **not** need Docker installed locally — `gcloud run deploy --source`
builds the container in Cloud Build for you.

## One-time project setup

```bash
# Authenticate. Opens a browser.
gcloud auth login

# Create a project (or reuse an existing one). Project IDs are globally
# unique; pick something like lux-mcp-<your-initials>.
gcloud projects create lux-mcp-cwd --name="lux-mcp"

# Make it the active project for subsequent commands.
gcloud config set project lux-mcp-cwd

# Pick a default region. us-central1 is the cheapest and has the largest
# free tier. Use europe-west1 / asia-northeast1 etc. if latency matters.
gcloud config set run/region us-central1

# Link a billing account. List them with `gcloud billing accounts list`.
gcloud billing projects link lux-mcp-cwd \
  --billing-account=XXXXXX-XXXXXX-XXXXXX

# Enable the APIs the deploy needs (Cloud Run, Cloud Build, Artifact Registry).
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

## Deploy

From the repo root:

```bash
gcloud run deploy lux-mcp \
  --source . \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --timeout=3600 \
  --concurrency=20 \
  --max-instances=3
```

What this does, flag by flag:

- `--source .` — Cloud Build packages the current directory into a
  container using the `Dockerfile`, pushes it to Artifact Registry, then
  rolls out a new Cloud Run revision. First deploy creates the
  `cloud-run-source-deploy` repo automatically; subsequent deploys reuse it.
- `--allow-unauthenticated` — makes the service publicly reachable. See
  [Authentication](#authentication-public-vs-iam) below for the trade-off
  and the IAM alternative.
- `--memory=512Mi --cpu=1` — comfortably sized for the Lux API client; the
  bulk of the work is HTTP and small JSON parsing.
- `--timeout=3600` — the maximum request duration (60 min). MCP streamable
  HTTP can hold a connection open for the duration of a session, so set
  this to the max.
- `--concurrency=20` — how many in-flight requests a single container
  handles. The Lux API is the bottleneck, so going wide is wasteful; keep
  this modest.
- `--max-instances=3` — caps blast radius. A misbehaving client can't
  fan out and burn your free tier or push the Lux API into rate limits.

First deploy takes 2–4 minutes (image build + cold start). On success
`gcloud` prints a URL:

```
Service URL: https://lux-mcp-abc123-uc.a.run.app
```

The MCP endpoint is at `/mcp`, so the full URL your client needs is:

```
https://lux-mcp-abc123-uc.a.run.app/mcp
```

## Smoke-test the deployment

```bash
# Should return 406 Not Acceptable — the MCP transport requires specific
# Accept headers that plain curl doesn't send. 406 confirms the server is
# up and routing correctly.
curl -i https://lux-mcp-abc123-uc.a.run.app/mcp
```

Anything other than `HTTP/2 406` (`404`, `502`, connection refused, etc.)
means the service didn't come up correctly — jump to
[Logs and debugging](#logs-and-debugging).

## Connecting an MCP client

### Claude Code

```bash
claude mcp add --transport http lux-remote https://lux-mcp-abc123-uc.a.run.app/mcp
```

Then `/mcp` inside Claude Code should show `lux-remote` connected. If you
also have the local stdio `lux` server registered, you can keep both
side-by-side; rename to taste.

### Claude Desktop

Open **Settings → Connectors → Add custom connector**, paste the `/mcp`
URL, and give it a name. The connector appears in the tools menu after
restart.

(Older Claude Desktop builds that only support stdio can't connect to a
remote MCP server directly — upgrade, or run a local stdio shim that
proxies to the remote URL.)

## Authentication: public vs IAM

The `--allow-unauthenticated` flag above makes the endpoint world-readable.
For a server that only proxies a public read-only API (Lux), this is
defensible — the worst a stranger can do is run searches you could already
run yourself in a browser. The `--max-instances` cap bounds the cost
exposure.

If you'd rather lock it down, drop `--allow-unauthenticated` and require
callers to present a Google identity token:

```bash
gcloud run deploy lux-mcp --source . --no-allow-unauthenticated ...

# Grant yourself invoker rights:
gcloud run services add-iam-policy-binding lux-mcp \
  --member="user:you@example.com" \
  --role="roles/run.invoker"
```

The catch is that **MCP clients don't natively send GCP identity tokens.**
You'd need a local proxy (e.g. `gcloud auth print-identity-token` piped
into a wrapper) to inject the `Authorization: Bearer ...` header on each
request. For most personal/research uses, public + max-instances is
simpler and good enough. Revisit if the URL leaks or you start seeing
unwanted traffic in the logs.

## Updating

Re-running the same `gcloud run deploy` command rebuilds the image and
rolls out a new revision. Traffic shifts to the new revision atomically
once it's healthy; the old one stays around so rollback is instant:

```bash
gcloud run services update-traffic lux-mcp --to-revisions=lux-mcp-00003-abc=100
```

List revisions with `gcloud run revisions list --service=lux-mcp`.

## Logs and debugging

```bash
# Tail recent logs.
gcloud run services logs read lux-mcp --limit=50

# Or stream in real time.
gcloud run services logs tail lux-mcp

# Open in the console for filtering and graphs:
gcloud run services describe lux-mcp --format='value(status.url)'
# …then navigate to https://console.cloud.google.com/run
```

Common failure modes:

- **Container failed to start** — check that `Dockerfile` `CMD` matches
  the script's `--http` flag and that `EXPOSE`/`ENV PORT` line up with
  what `lux_mcp.py` reads (`PORT` env var, currently `8080`).
- **502 / 503 on first request after idle** — cold start exceeded the
  client's timeout. Add `--cpu-boost` to the deploy command, or set
  `--min-instances=1` (no longer free — billed for a warm container 24/7).
- **`/mcp` returns 404** — the streamable-HTTP transport is mounted at
  `/mcp` by FastMCP. If you set a custom mount path, update the client URL.

## Cost expectations

For personal use you will almost certainly stay inside the always-free
tier:

- **2 M requests/month** free; you'll use tens to low hundreds.
- **360 000 GB-seconds of memory** and **180 000 vCPU-seconds** free.
  A 512 Mi / 1 vCPU container running for a 10-minute session uses
  ~300 GB-s and ~600 vCPU-s.
- Cloud Build gives 120 free build-minutes/day (deploys take ~3 min).
- Artifact Registry storage for the image is ~$0.10/GB/month; the image
  is ~150 MB.

Realistic monthly cost for a single user: **$0**. Set a billing alert at
$5 anyway:

```bash
# In the console: Billing → Budgets & alerts → Create budget.
```

## Custom domain (optional)

Cloud Run supports domain mapping via Cloud Run Domains (managed certs
included). Skip unless you actually want a vanity URL — the `*.run.app`
domain is fine for an MCP endpoint and has a valid HTTPS cert already.

```bash
gcloud beta run domain-mappings create \
  --service=lux-mcp \
  --domain=lux-mcp.example.com
```

You'll need to add the CNAME / A records it prints to your DNS.

## Tearing it down

```bash
# Remove the service (revisions, traffic config, URL).
gcloud run services delete lux-mcp

# Optional: remove the built images to stop the trivial Artifact Registry
# storage charge.
gcloud artifacts repositories delete cloud-run-source-deploy \
  --location=us-central1
```

Deleting the GCP project (`gcloud projects delete lux-mcp-cwd`) wipes
everything in one shot if you'd rather start fresh later.
