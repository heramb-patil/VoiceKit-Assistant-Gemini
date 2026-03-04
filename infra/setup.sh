#!/usr/bin/env bash
# =============================================================================
# VoiceKit SaaS — One-time GCP infrastructure setup
#
# Run once per project to:
#   1. Enable required GCP APIs
#   2. Create the Cloud Run service account
#   3. Store all secrets in Secret Manager
#   4. Grant the service account access to secrets
#
# Prerequisites:
#   gcloud CLI installed and authenticated as a project owner
#   Environment variables set (see .env.infra.example)
#
# Usage:
#   cp infra/.env.infra.example infra/.env.infra
#   # Edit infra/.env.infra with your values
#   bash infra/setup.sh
# =============================================================================

set -euo pipefail

# ── Load config ───────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.infra"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found."
  echo "Copy infra/.env.infra.example to infra/.env.infra and fill in values."
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${GCP_PROJECT:?GCP_PROJECT must be set in .env.infra}"
: "${GCP_REGION:=us-central1}"
: "${SERVICE_NAME:=voicekit-backend}"
: "${SA_NAME:=voicekit-sa}"

# ── Helpers ───────────────────────────────────────────────────────────────────

secret_exists() {
  gcloud secrets describe "$1" --project="$GCP_PROJECT" &>/dev/null
}

create_or_update_secret() {
  local name="$1"
  local value="$2"
  if secret_exists "$name"; then
    echo "  ↺ Updating secret: $name"
    echo -n "$value" | gcloud secrets versions add "$name" \
      --project="$GCP_PROJECT" --data-file=-
  else
    echo "  + Creating secret: $name"
    echo -n "$value" | gcloud secrets create "$name" \
      --project="$GCP_PROJECT" --data-file=-
  fi
}

# ── Step 1: Enable APIs ───────────────────────────────────────────────────────

echo ""
echo "=== Step 1: Enabling GCP APIs ==="

gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$GCP_PROJECT"

echo "APIs enabled."

# ── Step 2: Create service account ───────────────────────────────────────────

echo ""
echo "=== Step 2: Creating service account ==="

SA_EMAIL="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$GCP_PROJECT" &>/dev/null; then
  echo "  Service account already exists: $SA_EMAIL"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="VoiceKit Backend" \
    --project="$GCP_PROJECT"
  echo "  Created: $SA_EMAIL"
fi

# Grant Cloud SQL access (if using Cloud SQL)
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/cloudsql.client" \
  --quiet

echo "Service account ready."

# ── Step 3: Store secrets ─────────────────────────────────────────────────────

echo ""
echo "=== Step 3: Storing secrets in Secret Manager ==="

create_or_update_secret "voicekit-gemini-api-key"       "${GEMINI_API_KEY:?}"
create_or_update_secret "voicekit-google-client-id"     "${GOOGLE_CLIENT_ID:?}"
create_or_update_secret "voicekit-google-client-secret" "${GOOGLE_CLIENT_SECRET:?}"
create_or_update_secret "voicekit-allowed-domain"       "${ALLOWED_DOMAIN:-}"

if [[ -n "${BASECAMP_CLIENT_ID:-}" ]]; then
  create_or_update_secret "voicekit-basecamp-client-id"     "$BASECAMP_CLIENT_ID"
  create_or_update_secret "voicekit-basecamp-client-secret" "${BASECAMP_CLIENT_SECRET:?}"
  create_or_update_secret "voicekit-basecamp-account-id"    "${BASECAMP_ACCOUNT_ID:?}"
fi

if [[ -n "${DB_URL:-}" ]]; then
  create_or_update_secret "voicekit-db-url" "$DB_URL"
fi

echo "Secrets stored."

# ── Step 4: Grant SA access to secrets ───────────────────────────────────────

echo ""
echo "=== Step 4: Granting secret access to service account ==="

SECRETS=(
  voicekit-gemini-api-key
  voicekit-google-client-id
  voicekit-google-client-secret
  voicekit-allowed-domain
)

if [[ -n "${BASECAMP_CLIENT_ID:-}" ]]; then
  SECRETS+=(voicekit-basecamp-client-id voicekit-basecamp-client-secret voicekit-basecamp-account-id)
fi
if [[ -n "${DB_URL:-}" ]]; then
  SECRETS+=(voicekit-db-url)
fi

for secret in "${SECRETS[@]}"; do
  if secret_exists "$secret"; then
    gcloud secrets add-iam-policy-binding "$secret" \
      --project="$GCP_PROJECT" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet
    echo "  Granted access: $secret"
  fi
done

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Deploy backend:  bash infra/deploy-backend.sh"
echo "  2. Deploy frontend: cd frontend && npm run build && firebase deploy"
echo "  3. Set Cloud Run env vars in GCP Console or via deploy script"
echo ""
echo "Service account: $SA_EMAIL"
echo "Project:         $GCP_PROJECT"
echo "Region:          $GCP_REGION"
