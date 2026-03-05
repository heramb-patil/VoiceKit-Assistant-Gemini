# VoiceKit Assistant — Privacy Policy & Compliance Notes

**Last updated:** March 2026
**Version:** 1.0

> **Note for legal review:** This document describes the actual technical data flows as implemented. Sections marked `[REQUIRED]` must be completed with your company name, contact details, and jurisdiction before publishing to end users.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Data Controller](#2-data-controller)
3. [Data We Collect](#3-data-we-collect)
4. [How We Use Your Data](#4-how-we-use-your-data)
5. [Third-Party Data Processors](#5-third-party-data-processors)
6. [Gemini Live API — Critical Compliance Notes](#6-gemini-live-api--critical-compliance-notes)
7. [Google API Limited Use Disclosure](#7-google-api-limited-use-disclosure)
8. [Data Storage & Retention](#8-data-storage--retention)
9. [Security](#9-security)
10. [Your Rights (GDPR / CCPA)](#10-your-rights-gdpr--ccpa)
11. [Children's Privacy](#11-childrens-privacy)
12. [Changes to This Policy](#12-changes-to-this-policy)

---

## 1. Overview

VoiceKit Assistant ("the Application") is an AI voice assistant that connects to your Google Workspace and Basecamp accounts to let you manage email, calendar events, files, tasks, and more through natural voice conversation.

The Application processes sensitive personal and business data — including the contents of emails, calendar entries, and voice audio — on your behalf. This document explains precisely what is collected, where it goes, and what control you have over it.

---

## 2. Data Controller

`[REQUIRED: Insert your company name, registered address, and data protection contact email here.]`

For GDPR purposes, `[Company Name]` acts as the **data controller** for data stored in its own backend database, and as a **data processor** for data it forwards to Google, Langfuse, and Basecamp on your behalf.

---

## 3. Data We Collect

### 3.1 Account Identity

**What:** Your Google account email address, display name, and profile picture URL.
**Source:** Extracted from your Google ID token on every authenticated request.
**Stored:** Yes — in the `users` table in the backend database (PostgreSQL/SQLite).
**Purpose:** Identify you across sessions; associate your credentials and tasks with your account.

### 3.2 OAuth Credentials

**What:** OAuth 2.0 access and refresh tokens for Google (Gmail, Calendar, Drive) and Basecamp.
**Source:** Issued by Google/Basecamp after you click "Connect" in the Integrations panel.
**Stored:** Yes — in the `user_credentials` table as serialized JSON, keyed by your email address and provider name.
**Purpose:** Allow the backend to call Google and Basecamp APIs on your behalf without you having to re-authenticate each time.

> **Security note:** OAuth tokens are stored at rest in the backend database. If your deployment does not encrypt the database or the `token_json` column, a database breach would expose these tokens. Production deployments should use column-level encryption or a secrets manager (e.g. Google Secret Manager, AWS Secrets Manager).

### 3.3 Voice Audio

**What:** Real-time audio captured from your microphone during a voice session.
**Source:** Your browser's microphone via the Web Audio API.
**Stored by VoiceKit:** **No.** Audio is streamed directly from your browser to the Gemini Live API over an encrypted WebSocket connection. VoiceKit's backend never receives or stores raw audio.
**Processed by Google:** Yes — see [Section 6](#6-gemini-live-api--critical-compliance-notes).

### 3.4 Email Content

**What:** Email subject lines, sender/recipient information, body text (up to 2,000 characters), and attachment metadata.
**Source:** Read from Gmail via the Gmail API when you issue a voice command such as "read my emails" or "search for emails from X."
**Stored by VoiceKit:** Transiently. Email content is fetched at the time of the request and forwarded to the Gemini Live API to generate a spoken response. It is **not persisted** in VoiceKit's database — however, if the tool call runs as a background task, the result (including email content) is stored in the `gemini_live_tasks` table until delivered.
**Processed by Google (Gemini):** Yes — see [Section 6](#6-gemini-live-api--critical-compliance-notes).

### 3.5 Calendar Data

**What:** Event titles, dates/times, attendees, locations, descriptions of calendar events.
**Source:** Read from and written to Google Calendar via the Calendar API.
**Stored by VoiceKit:** Same as email — not persisted except transiently in background task results.

### 3.6 Google Drive Files

**What:** File names, content of documents (when attached to emails or used in research tasks).
**Source:** Google Drive API.
**Stored by VoiceKit:** File content is not stored. File names may appear in background task results temporarily.

### 3.7 Basecamp Data

**What:** Project names, message board content, to-do items, check-in questions and responses.
**Source:** Basecamp API (v3).
**Stored by VoiceKit:** Not persisted beyond transient background task results.

### 3.8 Background Task Results

**What:** The output of any tool call that runs asynchronously (deep research, send email, create Basecamp post, etc.), including tool name, input arguments, and result text.
**Source:** Generated by the backend when executing tools on your behalf.
**Stored:** Yes — in the `gemini_live_tasks` table. The `tool_args` column may contain email addresses, search queries, or other personal data passed as tool parameters. The `result` column may contain email content, research summaries, or Basecamp responses.
**Retention:** `[REQUIRED: Define your task result retention policy, e.g. "deleted after 30 days."]`

### 3.9 MCP Server Configuration

**What:** Name, command, arguments, and environment variables for any Model Context Protocol (MCP) servers you configure.
**Source:** Entered manually in the MCP Servers panel.
**Stored:** Yes — in the `user_mcp_servers` table. Environment variables may contain API keys or secrets you provide.
**Note:** MCP server environment variables are stored as plain JSON. Do not store credentials in MCP env vars unless the database is encrypted at rest.

### 3.10 Usage Logs & Session Data

**What:** Server-side logs including timestamps, tool names invoked, session IDs, and error messages. No audio or message content is logged by default.
**Source:** FastAPI application logs.
**Stored:** On the server filesystem or logging infrastructure you deploy.
**Retention:** `[REQUIRED: Define log retention period.]`

---

## 4. How We Use Your Data

| Data | Purpose | Legal Basis (GDPR) |
|------|---------|-------------------|
| Account identity | Authentication, associating data with your account | Performance of contract |
| OAuth credentials | Calling Google/Basecamp APIs on your behalf | Performance of contract |
| Voice audio | Generating spoken AI responses in real time | Performance of contract |
| Email / Calendar / Drive content | Executing your voice commands | Performance of contract |
| Background task results | Delivering async tool outputs to your session | Performance of contract |
| MCP server config | Running user-defined tools | Performance of contract |
| Usage logs | Debugging, security monitoring, abuse prevention | Legitimate interest |

We do **not** use your data for advertising, sell it to third parties, or use it to train our own models.

---

## 5. Third-Party Data Processors

### 5.1 Google LLC

**Services used:**
- **Gemini Live API** — processes all voice audio and text conversation turns
- **Google OAuth 2.0** — authenticates users and issues access tokens
- **Gmail API, Calendar API, Drive API, Google Chat API** — provides access to your Google Workspace data

**Google's role:** Data processor (for API calls you initiate) and independent data controller (for data Google retains under its own terms).
**Data transfers:** Data is sent to Google servers in the US and potentially other regions where Google operates.
**Applicable terms:** [Google Cloud Terms of Service](https://cloud.google.com/terms), [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), [Gemini API Terms](https://ai.google.dev/gemini-api/terms).

> **See [Section 6](#6-gemini-live-api--critical-compliance-notes) for the most important compliance nuance regarding which Gemini API tier is in use.**

### 5.2 Langfuse (Optional)

**Service:** AI observability and tracing platform (self-hosted or Langfuse Cloud).
**What is sent:** If Langfuse is enabled in your deployment (`LANGFUSE_*` environment variables), full AI conversation traces — including tool inputs and outputs — are sent to your configured Langfuse endpoint. This **may include email content, calendar data, and other personal information** that appeared in conversation turns.
**Stored by:** Your Langfuse instance or Langfuse Cloud, depending on your deployment.
**Recommendation:** For deployments processing personal data under GDPR, use self-hosted Langfuse or review [Langfuse's Data Processing Agreement](https://langfuse.com/dpa). If Langfuse is not needed, set `ENABLE_LANGFUSE=false`.

### 5.3 Basecamp LLC / 37signals

**Service:** Basecamp project management platform.
**What is sent:** Tool calls may read from or write to Basecamp on your behalf, transmitting project and message content to the Basecamp API.
**Applicable terms:** [Basecamp Privacy Policy](https://basecamp.com/about/policies/privacy).

### 5.4 MCP Server Providers (User-Configured)

Any MCP servers you add in the MCP Servers panel run as subprocesses and may send data to third-party services. VoiceKit has no visibility into what those servers do with the data they receive. You are responsible for reviewing the privacy practices of any MCP server you connect.

---

## 6. Gemini Live API — Critical Compliance Notes

This section covers the most significant privacy nuance in the Application's architecture.

### 6.1 Two Tiers, Two Different Data Policies

The Gemini API is available under two different products with materially different data use policies:

| Tier | API Key Format | Google's Data Use Policy |
|------|---------------|--------------------------|
| **Google AI Studio** (free/paid via AI Studio) | `AIza...` key from [aistudio.google.com](https://aistudio.google.com) | Google **may use** your inputs and outputs to improve its products and ML models, unless you opt out |
| **Google Cloud Vertex AI** (enterprise) | Service account credentials from Google Cloud | Google **does not** use your data to train models by default; Data Processing Addendum available |

**This application is currently configured to use a Google AI Studio API key** (`REACT_APP_GEMINI_API_KEY`). Under Google AI Studio's default terms, voice audio, email content read aloud, calendar data, and all other conversation turns sent to the Gemini Live API **may be used by Google for model training and improvement**.

### 6.2 What This Means in Practice

Every turn of a voice conversation — including:
- The words you speak
- Email content read back to you
- Calendar events discussed
- Basecamp messages processed
- Search results and research summaries

— is transmitted to Google's Gemini Live API and subject to Google's data retention and use policies.

### 6.3 Recommendations by Use Case

**Internal / personal use (single user):** Google AI Studio API key is acceptable if the user is the data subject and consents to Google's terms.

**B2B SaaS / enterprise deployment processing employee data:** You should:
1. Switch to **Google Cloud Vertex AI** (Gemini on Vertex) to get contractual data processing protections.
2. Execute a **Google Cloud Data Processing Addendum (DPA)**.
3. Avoid sending identifiable personal data (names, email addresses) as free text in prompts where possible.

**EU deployment under GDPR:** Transferring personal data from the EU to Google's US servers requires a valid transfer mechanism. Google Cloud provides Standard Contractual Clauses (SCCs). Google AI Studio does not provide this for the free tier.

### 6.4 Migrating to Vertex AI

To switch to Vertex AI:
1. Enable the Vertex AI API in your Google Cloud project.
2. Replace the `@google/genai` Gemini Live client with the Vertex AI equivalent or use the `vertexai` endpoint in the genai SDK.
3. Update `REACT_APP_GEMINI_API_KEY` to use service account authentication.
4. Execute a Cloud DPA with Google at [cloud.google.com/terms/data-processing-addendum](https://cloud.google.com/terms/data-processing-addendum).

---

## 7. Google API Limited Use Disclosure

VoiceKit's use of Google user data obtained through Google APIs complies with the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

Specifically:

- **Use is limited to providing the service.** Data obtained from Google APIs (Gmail, Calendar, Drive) is used solely to execute the voice commands you request. It is not used for advertising, selling to third parties, or any purpose unrelated to the in-app functionality you initiated.
- **No transfer to third parties** except as necessary to provide the service (i.e., the data is forwarded to the Gemini Live API to generate a response to your query).
- **No use for training models.** VoiceKit does not train its own AI models on your Google data.
- **Human access.** No human reads your Google data except as required by law or with your explicit permission for debugging.

The following Google API scopes are requested by the Application:

| Scope | Purpose |
|-------|---------|
| `openid`, `email`, `profile` | Authentication and user identity |
| `https://www.googleapis.com/auth/gmail.readonly` | Reading emails on your behalf |
| `https://www.googleapis.com/auth/gmail.send` | Sending emails on your behalf |
| `https://www.googleapis.com/auth/calendar` | Reading and creating calendar events |
| `https://www.googleapis.com/auth/drive.readonly` | Reading files to attach to emails / research |
| `https://www.googleapis.com/auth/chat.messages` | Reading/sending Google Chat messages |

`[REQUIRED: Verify this scope list matches exactly what is requested in your OAuth consent screen. Any scope requested but not listed here would violate the Limited Use Policy.]`

---

## 8. Data Storage & Retention

### 8.1 What Is Stored in VoiceKit's Database

| Table | Contents | Sensitive? |
|-------|----------|-----------|
| `users` | email, name, picture URL, timestamps | Low |
| `user_credentials` | OAuth tokens (Google + Basecamp) | **High** — encrypt at rest |
| `user_mcp_servers` | MCP server commands + env vars (may contain secrets) | **High** — encrypt at rest |
| `gemini_live_tasks` | Tool name, args, result text, status, timestamps | **Medium** — may contain email content |

### 8.2 What Is NOT Stored

- Raw voice audio (never stored by VoiceKit)
- Full email bodies beyond what appears in a task result
- Conversation history or transcript (not persisted by VoiceKit's backend)
- Video or screen capture data

### 8.3 Retention Periods

`[REQUIRED: Define and implement retention periods for each table. Suggested defaults:]`

| Data | Suggested Retention |
|------|-------------------|
| User account record | Duration of account + 90 days after deletion request |
| OAuth credentials | Until user disconnects the integration or deletes account |
| Background task results | 30 days after delivery |
| MCP server configs | Until user deletes them |
| Server logs | 30–90 days |

### 8.4 Data Location

The backend database runs wherever you deploy the Docker container or cloud service. `[REQUIRED: Specify the region(s) your production deployment uses, e.g. "US East (us-east1, GCP)"]`. Ensure this is consistent with your users' jurisdictions.

---

## 9. Security

- **Transport:** All communication between the browser and the backend uses HTTPS/TLS. Communication with Gemini Live uses an encrypted WebSocket (WSS).
- **Authentication:** Every API request is authenticated with a short-lived Google ID token (JWT, ~1 hour lifetime) verified server-side against Google's public keys. No session cookies are used.
- **Domain restriction:** The backend can be configured to restrict login to a specific Google Workspace domain (`ALLOWED_DOMAIN` config).
- **OAuth tokens:** Stored in the backend database. Production deployments should encrypt the `token_json` and `env_json` columns or use a secrets manager.
- **MCP subprocess isolation:** MCP servers run as subprocesses. They inherit the backend process's network access. Only add MCP servers from trusted sources.

`[REQUIRED: Add any additional security certifications (SOC 2, ISO 27001) or penetration testing status here if applicable.]`

---

## 10. Your Rights (GDPR / CCPA)

### GDPR (EU/EEA/UK users)

You have the right to:

- **Access** — request a copy of all personal data VoiceKit holds about you
- **Rectification** — correct inaccurate data
- **Erasure ("right to be forgotten")** — request deletion of your account and all associated data
- **Restriction** — ask us to limit processing of your data
- **Portability** — receive your data in a machine-readable format
- **Object** — object to processing based on legitimate interests
- **Withdraw consent** — at any time, where processing is based on consent

To exercise these rights, contact: `[REQUIRED: data privacy contact email]`

**Note:** Exercising these rights with VoiceKit does not automatically remove data that Google, Langfuse, or Basecamp may hold independently. You must contact those providers separately for data they hold under their own terms.

### CCPA (California residents)

You have the right to:

- Know what personal information is collected and how it is used
- Delete personal information (subject to certain exceptions)
- Opt out of the sale of personal information — **VoiceKit does not sell personal information**
- Non-discrimination for exercising your rights

To submit a California privacy request, contact: `[REQUIRED: privacy contact]`

### Account Deletion

To delete your account and all associated data:
1. Disconnect all integrations from the Integrations panel (this revokes OAuth tokens)
2. Contact `[REQUIRED: support email]` with a deletion request
3. We will delete your account record, credentials, task history, and MCP configs within `[REQUIRED: timeframe, e.g. 30 days]`

---

## 11. Children's Privacy

VoiceKit is not directed at children under 13 (or under 16 in the EU). We do not knowingly collect personal data from children. If you believe a child has provided personal data, contact us and we will delete it promptly.

---

## 12. Changes to This Policy

We will notify users of material changes to this policy by `[REQUIRED: choose a method — email, in-app notice, etc.]` at least 14 days before the change takes effect. The "Last updated" date at the top of this document reflects the most recent revision.

---

## Appendix A: Quick Compliance Checklist

Before deploying VoiceKit to end users, verify:

- [ ] Company name and contact details filled in throughout this document
- [ ] Google OAuth consent screen configured with exact scopes listed in Section 7
- [ ] OAuth consent screen submitted for Google verification (required for >100 users or sensitive scopes)
- [ ] Privacy Policy URL added to the Google OAuth consent screen
- [ ] Decision made: **Google AI Studio key vs Vertex AI** (see Section 6) — critical for GDPR compliance
- [ ] If using Vertex AI: Google Cloud DPA executed
- [ ] If using Langfuse Cloud: Langfuse DPA executed or switched to self-hosted
- [ ] Database encryption at rest enabled for `user_credentials.token_json` and `user_mcp_servers.env_json`
- [ ] Data retention periods defined and automated cleanup implemented
- [ ] Deployment region confirmed and documented
- [ ] GDPR Standard Contractual Clauses in place if EU users are served and using non-Vertex Google APIs
- [ ] `ALLOWED_DOMAIN` configured in production to restrict access to intended users

## Appendix B: Data Flow Diagram

```
User's Browser
    │
    ├── Voice audio ──────────────────────────────────► Gemini Live API (Google)
    │                                                        │
    ├── Text turns / tool results ──────────────────────────►│
    │                                                         │
    │◄─────────────────── Spoken AI response ────────────────┘
    │
    ├── Auth token ──────────────────────────────────► VoiceKit Backend
    │                                                        │
    │◄───────────────── Tool results / tasks ────────────────┤
    │                                                        │
    │                                           ┌────────────┤
    │                                           │            │
    │                                    Database            ├──► Gmail API (Google)
    │                                    (users,             ├──► Calendar API (Google)
    │                                    credentials,        ├──► Drive API (Google)
    │                                    tasks,              ├──► Basecamp API
    │                                    mcp_servers)        ├──► MCP Servers (user-defined)
    │                                                        └──► Langfuse (if enabled)
```

---

*This document was prepared to reflect the technical implementation of VoiceKit as of March 2026. It is not legal advice. Consult a qualified privacy attorney before deploying to end users, particularly in regulated industries (healthcare, finance, legal) or jurisdictions with strict data protection laws.*
