#!/usr/bin/env bash
# =============================================================================
# VoiceKit SaaS — Local Setup Wizard
#
# Run once to configure credentials and start the dev stack.
# Usage: bash setup-local.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ENV="$SCRIPT_DIR/backend/.env"
FRONTEND_ENV="$SCRIPT_DIR/frontend/.env.local"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  →  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
error()   { echo -e "${RED}  ✗  $*${RESET}"; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }
ask()     { echo -e "${YELLOW}  ?  $*${RESET}"; }

# ── Helpers ───────────────────────────────────────────────────────────────────

get_env() { grep -E "^$1=" "$2" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo ""; }

set_env() {
  local key="$1" value="$2" file="$3"
  if grep -qE "^$key=" "$file" 2>/dev/null; then
    # Replace existing line
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' "s|^${key}=.*|${key}=${value}|" "$file"
    else
      sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    fi
  else
    echo "${key}=${value}" >> "$file"
  fi
}

open_url() {
  local url="$1"
  if command -v open &>/dev/null; then open "$url"        # macOS
  elif command -v xdg-open &>/dev/null; then xdg-open "$url" # Linux
  else info "Open this URL: $url"; fi
}

prompt() {
  local var_name="$1" prompt_text="$2" default="${3:-}"
  if [[ -n "$default" ]]; then
    read -r -p "    ${prompt_text} [${default}]: " input
    echo "${input:-$default}"
  else
    read -r -p "    ${prompt_text}: " input
    echo "$input"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────

clear
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║     VoiceKit SaaS — Setup Wizard         ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo "  This wizard will:"
echo "    1. Check existing credentials"
echo "    2. Guide you to create Google OAuth credentials"
echo "    3. Write everything to .env files"
echo "    4. Start the backend and frontend"
echo ""
read -r -p "  Press Enter to start..."

# ── Step 1: Check existing credentials ───────────────────────────────────────

header "Step 1 — Checking existing credentials"

GEMINI_KEY=$(get_env "GEMINI_API_KEY" "$BACKEND_ENV")
GOOGLE_CLIENT_ID=$(get_env "GEMINI_LIVE_GOOGLE_CLIENT_ID" "$BACKEND_ENV")
GOOGLE_SECRET=$(get_env "GOOGLE_CLIENT_SECRET" "$BACKEND_ENV")

[[ -n "$GEMINI_KEY" ]]      && success "Gemini API key found"      || warn "Gemini API key missing"
[[ -n "$GOOGLE_CLIENT_ID" ]] && success "Google Client ID found"   || warn "Google Client ID missing"
[[ -n "$GOOGLE_SECRET" ]]   && success "Google Client Secret found" || warn "Google Client Secret missing"

# ── Step 2: Gemini API key ────────────────────────────────────────────────────

if [[ -z "$GEMINI_KEY" ]]; then
  header "Step 2a — Gemini API Key"
  echo ""
  info "Opening Google AI Studio..."
  open_url "https://aistudio.google.com/apikey"
  echo ""
  ask "Paste your Gemini API key:"
  GEMINI_KEY=$(prompt "GEMINI_API_KEY" "Gemini API key")
  if [[ -z "$GEMINI_KEY" ]]; then error "Gemini API key is required. Exiting."; exit 1; fi
  set_env "GEMINI_API_KEY" "$GEMINI_KEY" "$BACKEND_ENV"
  set_env "GOOGLE_API_KEY" "$GEMINI_KEY" "$BACKEND_ENV"
  success "Gemini API key saved"
fi

# ── Step 3: Google OAuth credentials ─────────────────────────────────────────

if [[ -z "$GOOGLE_CLIENT_ID" || -z "$GOOGLE_SECRET" ]]; then
  header "Step 2b — Google OAuth Credentials"
  echo ""
  echo "  You need a Google OAuth 2.0 Web Client ID."
  echo ""
  info "Opening Google Cloud Console → Credentials..."
  open_url "https://console.cloud.google.com/apis/credentials"
  echo ""
  echo "  In the console:"
  echo -e "    ${BOLD}1.${RESET} Click  Create Credentials → OAuth client ID"
  echo -e "    ${BOLD}2.${RESET} Application type: ${BOLD}Web application${RESET}"
  echo -e "    ${BOLD}3.${RESET} Add Authorized JavaScript origin:"
  echo -e "         ${CYAN}http://localhost:3000${RESET}"
  echo -e "    ${BOLD}4.${RESET} Add Authorized redirect URIs:"
  echo -e "         ${CYAN}http://localhost:8001/gemini-live/auth/google/callback${RESET}"
  echo -e "         ${CYAN}http://localhost:8001/gemini-live/auth/basecamp/callback${RESET}"
  echo -e "    ${BOLD}5.${RESET} Click Create → copy the Client ID and Client Secret"
  echo ""
  read -r -p "  Press Enter once you've created the credentials..."
  echo ""

  ask "Paste your Google OAuth Client ID (ends with .apps.googleusercontent.com):"
  GOOGLE_CLIENT_ID=$(prompt "GOOGLE_CLIENT_ID" "Client ID")
  if [[ -z "$GOOGLE_CLIENT_ID" ]]; then error "Google Client ID is required. Exiting."; exit 1; fi

  ask "Paste your Google OAuth Client Secret:"
  GOOGLE_SECRET=$(prompt "GOOGLE_SECRET" "Client Secret")
  if [[ -z "$GOOGLE_SECRET" ]]; then error "Google Client Secret is required. Exiting."; exit 1; fi

  # Write to backend .env
  set_env "GEMINI_LIVE_GOOGLE_CLIENT_ID" "$GOOGLE_CLIENT_ID" "$BACKEND_ENV"
  set_env "GOOGLE_CLIENT_ID"             "$GOOGLE_CLIENT_ID" "$BACKEND_ENV"
  set_env "GOOGLE_CLIENT_SECRET"         "$GOOGLE_SECRET"    "$BACKEND_ENV"
  success "Google OAuth credentials saved to backend/.env"

  # Write to frontend .env.local (create if missing)
  if [[ ! -f "$FRONTEND_ENV" ]]; then
    cp "$SCRIPT_DIR/frontend/.env.example" "$FRONTEND_ENV" 2>/dev/null || touch "$FRONTEND_ENV"
  fi
  set_env "REACT_APP_GOOGLE_CLIENT_ID" "$GOOGLE_CLIENT_ID" "$FRONTEND_ENV"
  set_env "REACT_APP_GEMINI_API_KEY"   "$GEMINI_KEY"       "$FRONTEND_ENV"
  set_env "REACT_APP_VOICEKIT_API_URL" "http://localhost:8001" "$FRONTEND_ENV"
  success "Google Client ID saved to frontend/.env.local"
fi

# ── Make sure frontend .env.local has all values ──────────────────────────────

if [[ ! -f "$FRONTEND_ENV" ]]; then
  cp "$SCRIPT_DIR/frontend/.env.example" "$FRONTEND_ENV" 2>/dev/null || touch "$FRONTEND_ENV"
fi
FRONTEND_CLIENT_ID=$(get_env "REACT_APP_GOOGLE_CLIENT_ID" "$FRONTEND_ENV")
if [[ -z "$FRONTEND_CLIENT_ID" ]]; then
  set_env "REACT_APP_GOOGLE_CLIENT_ID" "$GOOGLE_CLIENT_ID" "$FRONTEND_ENV"
fi
FRONTEND_GEMINI=$(get_env "REACT_APP_GEMINI_API_KEY" "$FRONTEND_ENV")
if [[ -z "$FRONTEND_GEMINI" ]]; then
  set_env "REACT_APP_GEMINI_API_KEY"   "$GEMINI_KEY"           "$FRONTEND_ENV"
  set_env "REACT_APP_VOICEKIT_API_URL" "http://localhost:8001" "$FRONTEND_ENV"
fi

# ── Fix DB path to local ──────────────────────────────────────────────────────

DB_PATH=$(get_env "GEMINI_LIVE_VOICEKIT_DB_PATH" "$BACKEND_ENV")
if [[ "$DB_PATH" == "../../data/voicekit.db" ]]; then
  set_env "GEMINI_LIVE_VOICEKIT_DB_PATH" "./data/voicekit.db" "$BACKEND_ENV"
fi
mkdir -p "$SCRIPT_DIR/backend/data"

# ── Step 4: Install dependencies ─────────────────────────────────────────────

header "Step 3 — Installing dependencies"

# Backend
cd "$SCRIPT_DIR/backend"
if [[ ! -d ".venv" ]]; then
  info "Creating Python virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
info "Installing Python packages..."
pip install -r requirements.txt -q
success "Backend dependencies installed"

# Frontend
cd "$SCRIPT_DIR/frontend"
if [[ ! -d "node_modules" ]]; then
  info "Installing npm packages (this takes ~1 min)..."
  npm install --silent
fi
success "Frontend dependencies ready"

# ── Step 5: Start the stack ───────────────────────────────────────────────────

header "Step 4 — Starting VoiceKit SaaS"
echo ""
success "All credentials configured!"
echo ""
echo "  Starting backend on  http://localhost:8001"
echo "  Starting frontend on http://localhost:3000"
echo ""
echo -e "  ${YELLOW}Sign in with your Google account at http://localhost:3000${RESET}"
echo ""

# Start backend in background
cd "$SCRIPT_DIR/backend"
source .venv/bin/activate
OAUTHLIB_INSECURE_TRANSPORT=1 uvicorn main:app --host 0.0.0.0 --port 8001 --reload \
  > /tmp/voicekit-backend.log 2>&1 &
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID  (logs: /tmp/voicekit-backend.log)"

# Wait for backend to be ready
info "Waiting for backend..."
for i in $(seq 1 20); do
  if curl -sf http://localhost:8001/gemini-live/health > /dev/null 2>&1; then
    success "Backend is up"
    break
  fi
  sleep 1
done

# Open browser
open_url "http://localhost:3000"

# Start frontend (foreground — Ctrl+C to stop both)
cd "$SCRIPT_DIR/frontend"
echo ""
echo -e "  ${BOLD}Press Ctrl+C to stop everything${RESET}"
echo ""
trap "kill $BACKEND_PID 2>/dev/null; echo ''; echo 'Stopped.'" EXIT
npm start
