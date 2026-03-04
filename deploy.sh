#!/usr/bin/env bash
# =============================================================================
# VoiceKit SaaS — Cloud Deployment Script
#
# Supports:
#   render   — Render.com (recommended, fastest)
#   gcp      — GCP Cloud Run + Firebase Hosting
#
# Usage:
#   bash deploy.sh                  # interactive
#   bash deploy.sh render           # deploy to Render
#   bash deploy.sh gcp              # deploy to GCP
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'; DIM='\033[2m'

info()    { echo -e "${CYAN}  →  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
err()     { echo -e "${RED}  ✗  $*${RESET}"; }
header()  { echo -e "\n${BOLD}━━━  $*  ━━━${RESET}"; }
ask()     { echo -e "${YELLOW}  ?  $*${RESET}"; }
dim()     { echo -e "${DIM}      $*${RESET}"; }

die()  { err "$*"; exit 1; }

open_url() {
  if command -v open &>/dev/null; then open "$1"
  elif command -v xdg-open &>/dev/null; then xdg-open "$1"
  else info "Open in browser: $1"; fi
}

# Read a value from an env file:  get_env KEY file
get_env() { grep -E "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true; }

# Write or update a key in an env file:  set_env KEY value file
set_env() {
  local k="$1" v="$2" f="$3"
  if grep -qE "^${k}=" "$f" 2>/dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then sed -i '' "s|^${k}=.*|${k}=${v}|" "$f"
    else sed -i "s|^${k}=.*|${k}=${v}|" "$f"; fi
  else
    echo "${k}=${v}" >> "$f"
  fi
}

prompt_required() {
  local label="$1" default="${2:-}"
  while true; do
    if [[ -n "$default" ]]; then
      read -r -p "    $label [${default}]: " val
      val="${val:-$default}"
    else
      read -r -p "    $label: " val
    fi
    [[ -n "$val" ]] && echo "$val" && return
    warn "This field is required."
  done
}

require_cmd() {
  command -v "$1" &>/dev/null || die "'$1' is not installed. $2"
}

# ── Banner ────────────────────────────────────────────────────────────────────

clear
echo ""
echo -e "${BOLD}  ╔═══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}  ║   VoiceKit SaaS — Cloud Deploy Wizard     ║${RESET}"
echo -e "${BOLD}  ╚═══════════════════════════════════════════╝${RESET}"
echo ""

# ── Load existing credentials ─────────────────────────────────────────────────

BACKEND_ENV="$SCRIPT_DIR/backend/.env"
[[ -f "$BACKEND_ENV" ]] || die "backend/.env not found. Run setup-local.sh first."

GEMINI_KEY=$(get_env "GEMINI_API_KEY"            "$BACKEND_ENV")
G_CLIENT_ID=$(get_env "GEMINI_LIVE_GOOGLE_CLIENT_ID" "$BACKEND_ENV")
G_SECRET=$(get_env "GOOGLE_CLIENT_SECRET"        "$BACKEND_ENV")
BC_CLIENT_ID=$(get_env "BASECAMP_CLIENT_ID"      "$BACKEND_ENV")
BC_SECRET=$(get_env "BASECAMP_CLIENT_SECRET"     "$BACKEND_ENV")
BC_ACCOUNT=$(get_env "BASECAMP_ACCOUNT_ID"       "$BACKEND_ENV")
BC_AGENT=$(get_env "BASECAMP_USER_AGENT"         "$BACKEND_ENV")
ALLOWED_DOMAIN=$(get_env "GEMINI_LIVE_ALLOWED_DOMAIN" "$BACKEND_ENV")

# ── Collect any missing credentials ───────────────────────────────────────────

header "Credentials Check"

if [[ -z "$GEMINI_KEY" ]]; then
  warn "Gemini API key missing"
  info "Opening Google AI Studio..."; open_url "https://aistudio.google.com/apikey"
  GEMINI_KEY=$(prompt_required "Gemini API key")
  set_env "GEMINI_API_KEY" "$GEMINI_KEY" "$BACKEND_ENV"
else success "Gemini API key   ✓"; fi

if [[ -z "$G_CLIENT_ID" || -z "$G_SECRET" ]]; then
  warn "Google OAuth credentials missing"
  echo ""
  echo "  You need a Google OAuth 2.0 Web Client."
  echo "  You'll update the redirect URI after deployment."
  info "Opening Google Cloud Console..."; open_url "https://console.cloud.google.com/apis/credentials"
  echo ""
  echo -e "  ${BOLD}Create Credentials → OAuth client ID → Web application${RESET}"
  echo "  For now, add any placeholder redirect URI (we'll update it after deploy)."
  echo "  Authorized JavaScript origin: https://your-frontend-domain.com (update later)"
  echo ""
  read -r -p "  Press Enter once you've created the credentials..."
  G_CLIENT_ID=$(prompt_required "Google Client ID (ends with .apps.googleusercontent.com)")
  G_SECRET=$(prompt_required "Google Client Secret")
  set_env "GEMINI_LIVE_GOOGLE_CLIENT_ID" "$G_CLIENT_ID" "$BACKEND_ENV"
  set_env "GOOGLE_CLIENT_ID"             "$G_CLIENT_ID" "$BACKEND_ENV"
  set_env "GOOGLE_CLIENT_SECRET"         "$G_SECRET"    "$BACKEND_ENV"
else success "Google OAuth credentials  ✓"; fi

if [[ -z "$ALLOWED_DOMAIN" ]]; then
  ask "Restrict sign-in to a Google Workspace domain? (e.g. suzega.com — leave blank for any Google account)"
  read -r -p "    Domain: " ALLOWED_DOMAIN
  [[ -n "$ALLOWED_DOMAIN" ]] && set_env "GEMINI_LIVE_ALLOWED_DOMAIN" "$ALLOWED_DOMAIN" "$BACKEND_ENV"
else success "Allowed domain: $ALLOWED_DOMAIN  ✓"; fi

# ── Choose deployment target ──────────────────────────────────────────────────

header "Deployment Target"

TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
  echo ""
  echo "  Where do you want to deploy?"
  echo ""
  echo "    1) Render  — Easiest. GitHub → auto-deploy. ~\$14/month."
  echo "    2) GCP     — Cloud Run + Firebase. Scales to zero. ~\$10/month."
  echo ""
  read -r -p "  Enter 1 or 2: " choice
  case "$choice" in
    1) TARGET="render" ;;
    2) TARGET="gcp" ;;
    *) die "Invalid choice." ;;
  esac
fi

# =============================================================================
# ── RENDER ────────────────────────────────────────────────────────────────────
# =============================================================================

if [[ "$TARGET" == "render" ]]; then

  header "Deploying to Render"

  # Prerequisites
  require_cmd git  "Install git: https://git-scm.com"
  require_cmd gh   "Install GitHub CLI: brew install gh  OR  https://cli.github.com"

  # ── Git setup ────────────────────────────────────────────────────────────────

  header "Step 1 — GitHub Repository"

  cd "$SCRIPT_DIR"

  if [[ ! -d ".git" ]]; then
    info "Initializing git repository..."
    git init
    git add .
    git commit -m "Initial VoiceKit SaaS commit"
    success "Git repository initialized"
  else
    # Commit any uncommitted changes
    if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
      info "Committing current changes..."
      git add .
      git commit -m "Deploy: update VoiceKit SaaS" || true
    fi
    success "Git repository ready"
  fi

  # Check for remote
  GITHUB_REPO=""
  if git remote get-url origin &>/dev/null; then
    GITHUB_REPO=$(git remote get-url origin | sed 's|.*github.com[:/]||' | sed 's|\.git$||')
    success "GitHub remote: $GITHUB_REPO"
  else
    ask "Create a new private GitHub repo for this project?"
    read -r -p "    Repo name [voicekit-saas]: " REPO_NAME
    REPO_NAME="${REPO_NAME:-voicekit-saas}"

    # Try to get GitHub org from gh auth
    GH_USER=$(gh api user --jq '.login' 2>/dev/null || echo "")
    info "Creating GitHub repo: ${GH_USER}/${REPO_NAME}"
    gh repo create "${REPO_NAME}" --private --source=. --remote=origin --push || {
      # Might already exist
      read -r -p "    GitHub user/org and repo (e.g. suzega/voicekit-saas): " GITHUB_REPO
      git remote add origin "https://github.com/${GITHUB_REPO}.git"
    }
    git push -u origin main 2>/dev/null || git push -u origin master 2>/dev/null || true
    GITHUB_REPO=$(git remote get-url origin | sed 's|.*github.com[:/]||' | sed 's|\.git$||')
    success "Pushed to GitHub: $GITHUB_REPO"
  fi

  # ── Update render.yaml with real values ───────────────────────────────────────

  header "Step 2 — Configure render.yaml"

  RENDER_YAML="$SCRIPT_DIR/render.yaml"
  if [[ -f "$RENDER_YAML" ]]; then
    # Update allowed domain
    if [[ -n "$ALLOWED_DOMAIN" ]]; then
      if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|value: suzega.com|value: ${ALLOWED_DOMAIN}|g" "$RENDER_YAML"
      else
        sed -i "s|value: suzega.com|value: ${ALLOWED_DOMAIN}|g" "$RENDER_YAML"
      fi
    fi
    git add render.yaml
    git commit -m "Update render.yaml domain config" 2>/dev/null || true
    git push 2>/dev/null || true
    success "render.yaml ready"
  fi

  # ── Deploy on Render ──────────────────────────────────────────────────────────

  header "Step 3 — Deploy on Render"

  RENDER_URL="https://dashboard.render.com/select-repo?type=blueprint"

  echo ""
  echo -e "  ${BOLD}Manual steps (takes ~2 minutes):${RESET}"
  echo ""
  echo "  1. Render will open in your browser"
  echo "  2. Click 'New Blueprint Instance'"
  echo "  3. Connect GitHub and select: ${CYAN}${GITHUB_REPO}${RESET}"
  echo "  4. Render reads render.yaml and creates:"
  echo "     • voicekit-backend  (Web Service)"
  echo "     • voicekit-frontend (Static Site)"
  echo "     • voicekit-db       (PostgreSQL)"
  echo "  5. In the 'Environment' section, paste these secret values:"
  echo ""
  echo -e "     ${BOLD}GEMINI_API_KEY${RESET}              = ${DIM}${GEMINI_KEY:0:20}...${RESET}"
  echo -e "     ${BOLD}GEMINI_LIVE_GOOGLE_CLIENT_ID${RESET} = ${DIM}${G_CLIENT_ID}${RESET}"
  echo -e "     ${BOLD}GOOGLE_CLIENT_ID${RESET}             = ${DIM}${G_CLIENT_ID}${RESET}"
  echo -e "     ${BOLD}GOOGLE_CLIENT_SECRET${RESET}         = ${DIM}${G_SECRET:0:8}...${RESET}"
  [[ -n "$BC_CLIENT_ID" ]] && echo -e "     ${BOLD}BASECAMP_CLIENT_ID${RESET}           = ${DIM}${BC_CLIENT_ID}${RESET}"
  [[ -n "$BC_SECRET" ]]    && echo -e "     ${BOLD}BASECAMP_CLIENT_SECRET${RESET}       = ${DIM}${BC_SECRET:0:8}...${RESET}"
  [[ -n "$BC_ACCOUNT" ]]   && echo -e "     ${BOLD}BASECAMP_ACCOUNT_ID${RESET}          = ${DIM}${BC_ACCOUNT}${RESET}"
  [[ -n "$BC_AGENT" ]]     && echo -e "     ${BOLD}BASECAMP_USER_AGENT${RESET}          = ${DIM}${BC_AGENT}${RESET}"
  echo -e "     ${BOLD}REACT_APP_GEMINI_API_KEY${RESET}     = ${DIM}${GEMINI_KEY:0:20}...${RESET}"
  echo -e "     ${BOLD}REACT_APP_GOOGLE_CLIENT_ID${RESET}   = ${DIM}${G_CLIENT_ID}${RESET}"
  echo ""
  echo "  ─────────────────────────────────────────────────────"
  echo "  After deploy, Render gives you two URLs:"
  echo "    • Backend:  https://voicekit-backend-xxxx.onrender.com"
  echo "    • Frontend: https://voicekit-frontend-xxxx.onrender.com"
  echo "  ─────────────────────────────────────────────────────"
  echo ""

  # Save creds to a temp file for easy copy-paste
  CREDS_FILE="/tmp/voicekit-render-env.txt"
  cat > "$CREDS_FILE" << EOF
# Paste these into Render Dashboard → Environment Variables
GEMINI_API_KEY=${GEMINI_KEY}
GEMINI_LIVE_GOOGLE_CLIENT_ID=${G_CLIENT_ID}
GOOGLE_CLIENT_ID=${G_CLIENT_ID}
GOOGLE_CLIENT_SECRET=${G_SECRET}
BASECAMP_CLIENT_ID=${BC_CLIENT_ID:-}
BASECAMP_CLIENT_SECRET=${BC_SECRET:-}
BASECAMP_ACCOUNT_ID=${BC_ACCOUNT:-}
BASECAMP_USER_AGENT=${BC_AGENT:-}
REACT_APP_GEMINI_API_KEY=${GEMINI_KEY}
REACT_APP_GOOGLE_CLIENT_ID=${G_CLIENT_ID}
EOF
  success "Credentials saved to: $CREDS_FILE"

  read -r -p "  Press Enter to open Render in your browser..."
  open_url "$RENDER_URL"

  echo ""
  header "After Render Deploy — Update Google OAuth"
  echo ""
  echo "  Once deployed, go back to Google Cloud Console and update your"
  echo "  OAuth client with the real URLs:"
  echo ""
  echo "  Authorized JavaScript origins:"
  echo "    https://voicekit-frontend-xxxx.onrender.com"
  echo ""
  echo "  Authorized redirect URIs:"
  echo "    https://voicekit-backend-xxxx.onrender.com/gemini-live/auth/google/callback"
  echo "    https://voicekit-backend-xxxx.onrender.com/gemini-live/auth/basecamp/callback"
  echo ""
  echo "  And update these env vars on the backend Render service:"
  echo "    GEMINI_LIVE_BACKEND_PUBLIC_URL = https://voicekit-backend-xxxx.onrender.com"
  echo "    GEMINI_LIVE_FRONTEND_URL       = https://voicekit-frontend-xxxx.onrender.com"
  echo ""
  info "Opening Google Cloud Console credentials..."
  read -r -p "  Press Enter to open Google Cloud Console..."
  open_url "https://console.cloud.google.com/apis/credentials"

  success "Render deployment initiated!"

# =============================================================================
# ── GCP ───────────────────────────────────────────────────────────────────────
# =============================================================================

elif [[ "$TARGET" == "gcp" ]]; then

  header "Deploying to GCP (Cloud Run + Firebase)"

  # Prerequisites
  require_cmd git       "Install git: https://git-scm.com"
  require_cmd gcloud    "Install gcloud: https://cloud.google.com/sdk/install"
  require_cmd docker    "Install Docker: https://docker.com/get-started"
  require_cmd firebase  "Run: npm install -g firebase-tools"

  # ── GCP project ──────────────────────────────────────────────────────────────

  header "Step 1 — GCP Project"

  CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
  if [[ -n "$CURRENT_PROJECT" ]]; then
    success "Current GCP project: $CURRENT_PROJECT"
    read -r -p "    Use this project? (y/n) [y]: " use_current
    use_current="${use_current:-y}"
    if [[ "$use_current" != "y" ]]; then
      ask "Enter GCP project ID:"
      GCP_PROJECT=$(prompt_required "GCP project ID")
      gcloud config set project "$GCP_PROJECT"
    else
      GCP_PROJECT="$CURRENT_PROJECT"
    fi
  else
    info "Opening GCP Console to find your project ID..."
    open_url "https://console.cloud.google.com"
    GCP_PROJECT=$(prompt_required "GCP project ID")
    gcloud config set project "$GCP_PROJECT"
  fi

  GCP_REGION="${GCP_REGION:-us-central1}"
  ask "GCP region?"
  read -r -p "    Region [${GCP_REGION}]: " region_input
  GCP_REGION="${region_input:-$GCP_REGION}"

  SA_EMAIL="voicekit-sa@${GCP_PROJECT}.iam.gserviceaccount.com"
  ARTIFACT_REPO="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/voicekit"
  BACKEND_IMAGE="${ARTIFACT_REPO}/backend:latest"
  FRONTEND_IMAGE="${ARTIFACT_REPO}/frontend:latest"

  # ── Enable APIs + service account ────────────────────────────────────────────

  header "Step 2 — GCP APIs & Service Account"

  info "Enabling required GCP APIs..."
  gcloud services enable \
    run.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    --project="$GCP_PROJECT" --quiet
  success "APIs enabled"

  if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$GCP_PROJECT" &>/dev/null; then
    info "Creating service account: $SA_EMAIL"
    gcloud iam service-accounts create voicekit-sa \
      --display-name="VoiceKit Backend" \
      --project="$GCP_PROJECT"
    success "Service account created"
  else
    success "Service account exists: $SA_EMAIL"
  fi

  # ── Store secrets ─────────────────────────────────────────────────────────────

  header "Step 3 — Secret Manager"

  _upsert_secret() {
    local name="$1" value="$2"
    [[ -z "$value" ]] && return
    if gcloud secrets describe "$name" --project="$GCP_PROJECT" &>/dev/null; then
      echo -n "$value" | gcloud secrets versions add "$name" --project="$GCP_PROJECT" --data-file=- --quiet
    else
      echo -n "$value" | gcloud secrets create "$name" --project="$GCP_PROJECT" --data-file=- --quiet
    fi
    # Grant SA access
    gcloud secrets add-iam-policy-binding "$name" \
      --project="$GCP_PROJECT" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="roles/secretmanager.secretAccessor" --quiet
  }

  info "Storing secrets..."
  _upsert_secret "voicekit-gemini-api-key"       "$GEMINI_KEY"
  _upsert_secret "voicekit-google-client-id"     "$G_CLIENT_ID"
  _upsert_secret "voicekit-google-client-secret" "$G_SECRET"
  [[ -n "$ALLOWED_DOMAIN" ]] && _upsert_secret "voicekit-allowed-domain" "$ALLOWED_DOMAIN"
  [[ -n "$BC_CLIENT_ID"  ]] && _upsert_secret "voicekit-basecamp-client-id"     "$BC_CLIENT_ID"
  [[ -n "$BC_SECRET"     ]] && _upsert_secret "voicekit-basecamp-client-secret" "$BC_SECRET"
  [[ -n "$BC_ACCOUNT"    ]] && _upsert_secret "voicekit-basecamp-account-id"    "$BC_ACCOUNT"
  success "All secrets stored in Secret Manager"

  # ── Artifact Registry ─────────────────────────────────────────────────────────

  header "Step 4 — Container Registry"

  if ! gcloud artifacts repositories describe voicekit \
      --location="$GCP_REGION" --project="$GCP_PROJECT" &>/dev/null; then
    info "Creating Artifact Registry repository..."
    gcloud artifacts repositories create voicekit \
      --repository-format=docker \
      --location="$GCP_REGION" \
      --project="$GCP_PROJECT" --quiet
    success "Registry created: $ARTIFACT_REPO"
  else
    success "Registry exists: $ARTIFACT_REPO"
  fi

  gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

  # ── Build + push Docker images ────────────────────────────────────────────────

  header "Step 5 — Build & Push Docker Images"

  info "Building backend image..."
  docker build -t "$BACKEND_IMAGE" "$SCRIPT_DIR/backend"
  docker push "$BACKEND_IMAGE"
  success "Backend image pushed: $BACKEND_IMAGE"

  # ── Deploy backend to Cloud Run ───────────────────────────────────────────────

  header "Step 6 — Deploy Backend (Cloud Run)"

  # Build the env vars string
  CLOUD_RUN_ENV="GEMINI_LIVE_HOST=0.0.0.0,GEMINI_LIVE_CORS_ORIGINS=*"
  CLOUD_RUN_ENV+=",GEMINI_LIVE_BACKEND_PUBLIC_URL=https://voicekit-backend-placeholder.run.app"
  CLOUD_RUN_ENV+=",GEMINI_LIVE_FRONTEND_URL=https://placeholder.web.app"
  [[ -n "$ALLOWED_DOMAIN" ]] && CLOUD_RUN_ENV+=",GEMINI_LIVE_ALLOWED_DOMAIN=${ALLOWED_DOMAIN}"

  info "Deploying backend to Cloud Run..."
  BACKEND_URL=$(gcloud run deploy voicekit-backend \
    --image="$BACKEND_IMAGE" \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT" \
    --service-account="$SA_EMAIL" \
    --platform=managed \
    --allow-unauthenticated \
    --port=8080 \
    --min-instances=0 \
    --max-instances=5 \
    --memory=512Mi \
    --cpu=1 \
    --set-env-vars="$CLOUD_RUN_ENV" \
    --update-secrets="\
GEMINI_API_KEY=voicekit-gemini-api-key:latest,\
GEMINI_LIVE_GOOGLE_CLIENT_ID=voicekit-google-client-id:latest,\
GOOGLE_CLIENT_ID=voicekit-google-client-id:latest,\
GOOGLE_CLIENT_SECRET=voicekit-google-client-secret:latest" \
    --format="value(status.url)" \
    --quiet 2>&1 | tail -1)

  # Re-deploy with real URL
  info "Updating backend URL to: $BACKEND_URL"
  gcloud run services update voicekit-backend \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT" \
    --update-env-vars="GEMINI_LIVE_BACKEND_PUBLIC_URL=${BACKEND_URL}" \
    --quiet

  success "Backend live at: $BACKEND_URL"

  # ── Deploy frontend to Firebase ───────────────────────────────────────────────

  header "Step 7 — Deploy Frontend (Firebase Hosting)"

  cd "$SCRIPT_DIR/frontend"

  # Write .env.production with real backend URL
  cat > .env.production << EOF
REACT_APP_GEMINI_API_KEY=${GEMINI_KEY}
REACT_APP_GOOGLE_CLIENT_ID=${G_CLIENT_ID}
REACT_APP_VOICEKIT_API_URL=${BACKEND_URL}
EOF

  info "Building frontend..."
  npm run build

  # Init Firebase if needed
  if [[ ! -f ".firebaserc" ]]; then
    info "Initializing Firebase project..."
    firebase login --no-localhost 2>/dev/null || firebase login
    firebase use --add "$GCP_PROJECT"
  fi

  info "Deploying to Firebase Hosting..."
  firebase deploy --only hosting
  FRONTEND_URL=$(firebase hosting:channel:list 2>/dev/null | grep live | awk '{print $2}' || \
                 echo "https://${GCP_PROJECT}.web.app")

  success "Frontend live at: $FRONTEND_URL"

  # ── Update backend with frontend URL ─────────────────────────────────────────

  gcloud run services update voicekit-backend \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT" \
    --update-env-vars="GEMINI_LIVE_FRONTEND_URL=${FRONTEND_URL}" \
    --quiet

  # ── Update Basecamp secrets (if provided) ─────────────────────────────────────
  if [[ -n "$BC_CLIENT_ID" ]]; then
    gcloud run services update voicekit-backend \
      --region="$GCP_REGION" \
      --project="$GCP_PROJECT" \
      --update-secrets="\
BASECAMP_CLIENT_ID=voicekit-basecamp-client-id:latest,\
BASECAMP_CLIENT_SECRET=voicekit-basecamp-client-secret:latest,\
BASECAMP_ACCOUNT_ID=voicekit-basecamp-account-id:latest" \
      --quiet
  fi

  cd "$SCRIPT_DIR"

  # ── Final: update Google OAuth ────────────────────────────────────────────────

  header "Step 8 — Update Google OAuth Redirect URIs"

  echo ""
  echo "  Almost done! Update your Google OAuth client with the real URLs."
  echo ""
  info "Opening Google Cloud Console..."
  echo ""
  echo -e "  Add these to your OAuth 2.0 client:"
  echo ""
  echo -e "  ${BOLD}Authorized JavaScript origins:${RESET}"
  echo -e "    ${CYAN}${FRONTEND_URL}${RESET}"
  echo ""
  echo -e "  ${BOLD}Authorized redirect URIs:${RESET}"
  echo -e "    ${CYAN}${BACKEND_URL}/gemini-live/auth/google/callback${RESET}"
  echo -e "    ${CYAN}${BACKEND_URL}/gemini-live/auth/basecamp/callback${RESET}"
  echo ""
  read -r -p "  Press Enter to open Google Cloud Console..."
  open_url "https://console.cloud.google.com/apis/credentials"

  # ── Summary ───────────────────────────────────────────────────────────────────

  header "Deployment Complete!"
  echo ""
  echo -e "  ${GREEN}${BOLD}Backend:${RESET}   $BACKEND_URL"
  echo -e "  ${GREEN}${BOLD}Frontend:${RESET}  $FRONTEND_URL"
  echo ""
  echo "  Share the frontend URL with your team."
  echo "  They sign in with their @${ALLOWED_DOMAIN:-company} Google accounts."
  echo ""

  # Write summary
  cat > "$SCRIPT_DIR/DEPLOY_URLS.txt" << EOF
# VoiceKit SaaS Deployment — $(date)
BACKEND_URL=${BACKEND_URL}
FRONTEND_URL=${FRONTEND_URL}
GCP_PROJECT=${GCP_PROJECT}
GCP_REGION=${GCP_REGION}
EOF
  success "URLs saved to DEPLOY_URLS.txt"

else
  die "Unknown target: $TARGET. Use 'render' or 'gcp'."
fi
