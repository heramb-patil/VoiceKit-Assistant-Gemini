# VoiceKit SaaS — Quick Start

Get the multi-tenant voice assistant running locally in under 15 minutes.

---

## Prerequisites

- Python 3.12+
- Node.js 18+ and npm
- A [Gemini API key](https://aistudio.google.com/apikey)
- A [Google Cloud project](https://console.cloud.google.com/) with an OAuth 2.0 Web Client ID

---

## 1. Google Cloud OAuth setup (one-time)

1. Go to **Google Cloud Console → APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Web application**
4. Add **Authorized redirect URIs**:
   - `http://localhost:8001/gemini-live/auth/google/callback`
   - `http://localhost:8001/gemini-live/auth/basecamp/callback` *(if using Basecamp)*
5. Add **Authorized JavaScript origins**:
   - `http://localhost:3000`
6. Download the credentials — you'll need the **Client ID** and **Client Secret**

---

## 2. Backend setup

```bash
cd SAAS/backend

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` and set the required values:

```env
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_LIVE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Optional: restrict sign-in to your company domain
GEMINI_LIVE_ALLOWED_DOMAIN=yourcompany.com
```

Start the backend:

```bash
OAUTHLIB_INSECURE_TRANSPORT=1 uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

> `OAUTHLIB_INSECURE_TRANSPORT=1` is needed for OAuth over HTTP on localhost. Use HTTPS in production.

Verify:

```bash
curl -H "Authorization: Bearer <your-token>" http://localhost:8001/gemini-live/health
```

---

## 3. Frontend setup

```bash
cd SAAS/frontend

npm install

cp .env.example .env.local
```

Edit `.env.local`:

```env
REACT_APP_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
REACT_APP_GEMINI_API_KEY=your-gemini-api-key-here
REACT_APP_VOICEKIT_API_URL=http://localhost:8001
```

Start the frontend:

```bash
npm start
```

Open **http://localhost:3000** — you'll see the Google Sign-In page.

---

## 4. Sign in

1. Click **Sign in with Google**
2. Use your company Google account (or any account if `ALLOWED_DOMAIN` is not set)
3. The voice interface loads automatically after successful sign-in

---

## 5. Connect integrations (optional)

### Google (Gmail, Calendar, Chat)
1. In the **Integrations** panel (bottom-left), click **Connect Google**
2. A Google OAuth consent screen opens in a new tab
3. Authorize access — you'll be redirected back to the app
4. The panel shows your Google account as connected

### Basecamp
1. Set in `backend/.env`:
   ```env
   BASECAMP_CLIENT_ID=your-basecamp-client-id
   BASECAMP_CLIENT_SECRET=your-basecamp-client-secret
   BASECAMP_ACCOUNT_ID=your-account-id
   BASECAMP_USER_AGENT=YourApp (your@email.com)
   ```
2. Click **Connect Basecamp** in the Integrations panel
3. Authorize on Basecamp — redirected back automatically

---

## 6. Start talking

Click the microphone button and try:
- *"What's in my inbox?"* — requires Google connected
- *"What are my Basecamp todos?"* — requires Basecamp connected
- *"Research [any topic] for me"* — background deep research
- *"What's on my calendar today?"*

---

## Multi-user behaviour

Each signed-in user gets their own integration credentials:
- User A connecting Google does **not** affect User B
- Tokens stored per-user in the local SQLite database (`backend/data/voicekit.db`)
- Sign out clears the browser session; integration tokens persist for next sign-in

---

## Docker

```bash
cd SAAS
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
# Fill in required values in both files, then:
docker compose up
```

---

## Common issues

**"Missing auth token" (401)** — You are not signed in or the session expired. Refresh and sign in again.

**"Not in allowed domain" (403)** — Sign in with your company Google account (`@yourcompany.com`), not a personal Gmail.

**Google Connect button opens a tab that shows an error** — Confirm the redirect URI `http://localhost:8001/gemini-live/auth/google/callback` is added in Google Cloud Console.

**"VoiceKit Backend Unavailable"** — Make sure `uvicorn main:app --port 8001` is running and `REACT_APP_VOICEKIT_API_URL=http://localhost:8001` is set.

---

## Project structure

```
SAAS/
├── backend/
│   ├── auth.py                 Google ID token verification (FastAPI dependency)
│   ├── api.py                  REST endpoints + web OAuth callbacks
│   ├── orchestration.py        Per-user tool registry
│   ├── config.py               Pydantic settings
│   ├── database/models.py      User + UserCredential + BackgroundTask tables
│   ├── integrations/
│   │   ├── google/auth.py      GoogleAuth with DB-backed token storage
│   │   └── basecamp/auth.py    BasecampAuth with DB-backed token storage
│   └── tests/
│
├── frontend/
│   └── src/
│       ├── contexts/AuthContext.tsx     Google Sign-In session state
│       ├── components/LoginPage.tsx     Sign-in screen
│       ├── components/IntegrationsPanel.tsx  Connect Google / Basecamp
│       └── lib/voicekit-bridge.ts       HTTP client (Bearer token auth)
│
└── infra/
    ├── setup.sh           One-time GCP setup (APIs, service account, secrets)
    ├── cloud-run.yaml     Cloud Run service definition
    └── .env.infra.example Infrastructure config template
```

**GCP deployment:** see `infra/setup.sh` and `infra/cloud-run.yaml`
