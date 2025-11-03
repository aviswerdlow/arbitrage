# Containerization and ECR Deployment Guide

This project now ships with a single Dockerfile that can build images for each
service (`ingest`, `matcher`, `signals`, `execution`, `api`, `ui`). Follow the
steps below to build and push new images before updating the ECS services.

---

## 1. Prerequisites

- Docker CLI available locally and logged into the correct AWS account.
- AWS CLI configured with credentials that have permission to create and push to
  Elastic Container Registry (ECR).
- (Optional) Existing ECR repositories for each service. Repository names are
  expected to follow the pattern `arbitrage-<service>`.

---

## 2. Build Images Locally

```bash
chmod +x scripts/build_images.sh
TAG="$(date +%Y%m%d%H%M)" ./scripts/build_images.sh
```

This command builds six images tagged `arbitrage-<service>:${TAG}` using the
shared `Dockerfile`. Each container exposes port `8000` and runs uvicorn with
the service-specific FastAPI application.

To target a specific registry prefix (for example an ECR URI), set `REGISTRY`:

```bash
REGISTRY=123456789012.dkr.ecr.us-west-1.amazonaws.com TAG=main ./scripts/build_images.sh
```

---

## 3. Create/Update ECR Repositories

For each service:

```bash
aws ecr create-repository --repository-name arbitrage-<service>
# Ignore the error if the repository already exists.
```

---

## 4. Push Images to ECR

Authenticate Docker with ECR (replace the region if needed):

```bash
aws ecr get-login-password --region us-west-1 \
  | docker login --username AWS --password-stdin 123456789012.dkr.ecr.us-west-1.amazonaws.com
```

Push each image produced in step 2:

```bash
for service in ingest matcher signals execution api ui; do
  docker push 123456789012.dkr.ecr.us-west-1.amazonaws.com/arbitrage-${service}:main
done
```

---

## 5. Update ECS Services

For each task definition family (`arbitrage-<service>`):

1. Register a new revision with the updated `image` value pointing to the pushed
   ECR URI.
2. Update the corresponding ECS service to use the new revision.
3. Wait for the service to report `1/1 tasks running` with the new image.

Each container already sets:
- `AWS_REGION=us-west-1`
- `REQUIRE_SECRETS=true`

The entrypoint automatically selects the correct FastAPI module. The `ui`
service serves the dashboard via `arbitrage.dashboard.api:create_dashboard_app`.

---

## 6. Secrets Verification (Optional)

After updating a service, you can exec into a task and run:

```bash
python - <<'PY'
import boto3
client = boto3.client("secretsmanager")
secret = client.get_secret_value(SecretId="arbitrage/polymarket")
print("Secret fetched:", secret["ARN"])
PY
```

Only run this check if you are authorized to view the secret contents.

---

With these steps complete, ECS will be running the latest application code for
each service using images built from this repository.
