# VoiceKit — Enterprise Stack Recommendation Package
### Prepared for: USAI
### Prepared by: VoiceKit (Heramb Patil)
### Date: March 2026 | Version 1.0 | Confidential

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Stack Analysis — What We Built & Why It Matters](#2-stack-analysis)
3. [Primary Recommendation — VoiceKit Custom Stack](#3-primary-recommendation)
4. [Enterprise Privacy Policy](#4-enterprise-privacy-policy)
5. [Data Retention Plan](#5-data-retention-plan)
6. [Pricing](#6-pricing)
7. [Backup Recommendation — Gemini Live Stack](#7-backup-recommendation)
8. [Decision Summary & Next Steps](#8-decision-summary)

---

## 1. Executive Summary

VoiceKit has two production-grade voice AI stacks available for deployment. This document recommends the right one for USAI, explains the reasoning, and covers all compliance, privacy, and commercial details needed to make a deployment decision.

**Bottom line up front:**

| | Primary (Recommended) | Backup |
|---|---|---|
| **Stack** | VoiceKit Custom Stack | Gemini Live Stack |
| **Core providers** | Deepgram + Groq/OpenAI + Cartesia + LiveKit | Google Gemini Live API |
| **Architecture** | Modular pipeline (STT → LLM → TTS) | Single multimodal model |
| **Data sovereignty** | Full — self-hosted in any US region | Partial — audio processed by Google |
| **HIPAA-capable** | Yes (BAAs available for all components) | Yes (Vertex AI enterprise only) |
| **ZDR (Zero Data Retention)** | Yes — per-component opt-in | Vertex AI enterprise only |
| **Vendor lock-in** | None — swap any component in YAML | High — tightly coupled to Google |
| **Latency (end-to-end)** | 300–600 ms (tunable) | 250–500 ms |
| **Cost / minute (est.)** | $0.047–0.065 at list (TTS-dominated) | ~$0.001 at list |
| **Recommended for USAI** | ✅ Yes | Fallback only |

**Recommendation:** Deploy the **VoiceKit Custom Stack** as the primary platform. It gives USAI full data control, per-component compliance contracts, US-region data residency, and no dependency on Google's model training policies. The Gemini Live stack is retained as a hot backup for conversational naturalness benchmarks and low-traffic secondary use cases.

---

## 2. Stack Analysis

### 2.1 Stack A — VoiceKit Custom Stack

A five-stage modular pipeline where each component is independently replaceable.

```
Microphone
    │
    ▼
[ VAD — Silero ]          ← Local model, runs on-prem, zero data egress
    │
    ▼
[ STT — Deepgram Nova-3 ] ← Streaming ASR, TTFW ~150ms, HIPAA BAA available
    │
    ▼
[ LLM — Groq / Llama 4 ] ← Ultra-low inference latency (~80ms TTFT), ZDR option
    │  (fallback: OpenAI GPT-4o)
    ▼
[ TTS — Cartesia Sonic-2 ]← TTFA 40–90ms, HIPAA BAA available, ZDR option
    │
    ▼
[ LiveKit WebRTC ]        ← Encrypted media relay, open-source SFU
    │
    ▼
Browser / App
```

**What runs where:**
- VAD (Voice Activity Detection): Silero — runs locally inside the agent container. Raw audio **never leaves your infrastructure** for the silence-detection stage.
- STT: Deepgram API — receives only the active speech segments that pass VAD.
- LLM: Groq or OpenAI — receives the text transcript, not raw audio.
- TTS: Cartesia — receives the LLM text response, returns audio.
- LiveKit Server: WebRTC media relay — can be self-hosted (open-source) or LiveKit Cloud.

**Key capabilities:**
- Full conversation transcripts stored in PostgreSQL (your database, your region)
- Built-in monitoring dashboard with session search, tool call inspection, export
- Configurable provider fallback chains with circuit breakers
- Webhook events for session lifecycle, tool calls, provider failures
- YAML-driven configuration — no code changes to swap provider or tune behavior
- Rate limiting and API key authentication built-in
- GCP Secret Manager and AWS Secrets Manager integration

---

### 2.2 Stack B — Gemini Live Stack

A single multimodal model processes everything — speech in, speech out — in a real-time WebSocket connection directly between the browser and Google's API.

```
Microphone
    │
    ▼ (WebSocket, WSS)
[ Gemini Live API (Google) ] ← Single model: STT + reasoning + TTS in one
    │
    ▼
Browser audio output
    │
    VoiceKit Backend (FastAPI)
    ├── Google Gmail API
    ├── Google Calendar API
    ├── Google Drive API
    ├── Basecamp API
    └── MCP Servers
```

**What it's good at:**
- Simpler orchestration — fewer moving parts
- Highly expressive, natural-sounding voice (end-to-end model, not stitched)
- Native Google Workspace integration (Gmail, Calendar, Drive as first-class tools)
- Personal assistant / executive assistant use cases
- Fastest path to a working demo

**Limitations for enterprise:**
- Audio streams directly to Google — no intermediate processing layer you control
- Using Google AI Studio API key: Google's terms permit using inputs/outputs for model improvement (see Section 4.4)
- To get enterprise-grade data protection, you must migrate to Vertex AI (requires GCP project, different billing)
- Single vendor dependency — a Google outage takes the whole voice layer down
- Less flexibility on LLM — you get Gemini Flash/Pro, no option to use Llama or Claude

---

### 2.3 Head-to-Head for Enterprise Deployment

| Criterion | Custom Stack | Gemini Live |
|-----------|-------------|-------------|
| Data leaves US? | No (when self-hosted in US) | Potentially — Google's global infra |
| Raw audio stored by vendor | Deepgram: ZDR opt-in (no storage) | Google: processes and may retain per terms |
| LLM inputs stored | Groq: ZDR available; OpenAI: 30-day default, ZDR enterprise | Gemini Live: stored per Google's API terms |
| HIPAA BAA available | Yes — Deepgram, OpenAI, Cartesia, LiveKit | Yes — Google Cloud (Vertex only) |
| Transcript ownership | 100% yours (your PostgreSQL) | Dependent on Google's retention policy |
| Provider redundancy | Yes — fallback chains per stage | No — single provider |
| Self-hosted option | Yes — full Docker deploy, no external calls except APIs | No — requires live Google API connection |
| Audit logs | Built-in audit log table | Via Google Cloud Audit Logs (separate setup) |
| Custom voice cloning | Yes — Cartesia or ElevenLabs custom voices | Limited — Gemini voice selection only |
| Fine-tune LLM | Yes — swap in any fine-tuned model | Not available |

---

## 3. Primary Recommendation — VoiceKit Custom Stack

### 3.1 Recommended Component Configuration for USAI

```yaml
# config/agent.yaml — USAI production configuration

providers:
  vad:
    provider: "silero"               # Local — no data egress for silence detection

  stt:
    provider: "deepgram"
    params:
      model: "nova-3"
      language: "en-US"
    fallbacks:
      - provider: "openai"
        params:
          model: "whisper-1"         # Fallback if Deepgram is unavailable

  llm:
    provider: "groq"
    params:
      model: "meta-llama/llama-4-scout-17b-16e-instruct"
    fallbacks:
      - provider: "openai"
        params:
          model: "gpt-4o"            # Fallback if Groq is unavailable

  tts:
    provider: "cartesia"
    params:
      model: "sonic-2"
      voice: "[USAI_VOICE_ID]"       # Custom voice per brand
    fallbacks:
      - provider: "openai"
        params:
          model: "tts-1-hd"
          voice: "nova"

database:
  enabled: true
  retention_days: 90                 # Auto-purge after 90 days (see retention plan)

auth:
  enabled: true                      # API key required for all requests

rate_limit:
  enabled: true
  requests_per_minute: 120

cors:
  allow_origins: ["https://usai.yourdomain.com"]

logging:
  level: "INFO"
  format: "json"
```

### 3.2 Infrastructure Recommendation

**Hosting:** Self-hosted on AWS us-east-1 (or us-west-2) for US data residency.

**Containers:**
- `livekit-server` — t3.medium or c5.large, 1–3 instances (WebRTC SFU)
- `agent-worker` — c5.xlarge, auto-scaled 2–10 instances (CPU-bound inference orchestration)
- `rest-api` — t3.small, 2 instances behind ALB (stateless, easy to scale)
- PostgreSQL — RDS PostgreSQL 16, Multi-AZ, encrypted at rest

**Secrets:** AWS Secrets Manager (VoiceKit has native integration, set `VOICEKIT_SECRET_BACKEND=aws`)

**Estimated infrastructure cost:** ~$400–800/month (AWS, production sizing, not including API usage)

### 3.3 Security Hardening for Production

- Enable `VOICEKIT_SECRET_BACKEND=aws` — no secrets in environment variables
- Set `cors.allow_origins` to exact production domains only
- Enable `auth.enabled: true` with rotating API keys
- Deploy LiveKit behind a private load balancer — expose only WSS port externally
- Enable RDS encryption at rest (AES-256) and in-transit (SSL)
- Use AWS PrivateLink or VPC peering between app containers and Deepgram/Cartesia to minimize public internet exposure
- Enable CloudTrail + VoiceKit audit logs for all session events

---

## 4. Enterprise Privacy Policy

*This policy reflects the actual data flows of the VoiceKit Custom Stack as configured for USAI.*

---

### 4.1 What Data Is Processed

| Data Type | Processed By | Stored? | Location |
|-----------|-------------|---------|----------|
| Voice audio (raw) | Silero VAD (local), then Deepgram STT | No | Never persisted; streamed in real time |
| Speech transcript (text) | Groq / OpenAI LLM | Via VoiceKit DB | Your PostgreSQL, US region |
| LLM responses (text) | Cartesia TTS | Via VoiceKit DB | Your PostgreSQL, US region |
| Tool call inputs/outputs | VoiceKit backend | Yes, VoiceKit DB | Your PostgreSQL, US region |
| Session metadata | VoiceKit backend | Yes, VoiceKit DB | Your PostgreSQL, US region |
| User identity | VoiceKit API (JWT / API key) | Optional | Configurable |

### 4.2 Third-Party Sub-Processors

The following vendors process data on behalf of USAI when the Custom Stack is in use.

| Sub-processor | Data Sent | Compliance Contracts | ZDR Available |
|--------------|-----------|---------------------|--------------|
| **Deepgram** | Active speech segments (audio) | SOC 2; HIPAA BAA on enterprise plan | Yes — enterprise opt-in |
| **Groq** | Text transcript of user turn | SOC 2; HIPAA readiness via enterprise contract | Yes — ZDR agreement available |
| **Cartesia** | LLM response text | SOC 2; HIPAA BAA available | Yes — enterprise |
| **LiveKit** (if cloud) | Encrypted WebRTC media (not plaintext) | SOC 2 | N/A (encrypted relay only) |

**If self-hosting LiveKit:** LiveKit Server is open-source and runs in your own infrastructure. Zero data sent to LiveKit Inc.

**If using Groq's ZDR:** Groq offers a Zero Data Retention agreement under which inputs and outputs are not stored after inference. This must be signed separately as part of your Groq enterprise contract.

### 4.3 Data VoiceKit Does NOT Collect

- Raw audio is never written to disk by VoiceKit. Audio flows through memory (Deepgram streaming API).
- No video or screen capture.
- No browser fingerprinting or tracking pixels.
- No advertising profiles or data selling.

### 4.4 What This Policy Does NOT Cover (Vendor-Side)

Each sub-processor has its own privacy policy that governs their handling of the data segments they receive:

- **Deepgram:** [deepgram.com/data-security](https://deepgram.com/data-security) — SOC 2, HIPAA BAA, ZDR configurable
- **Groq:** [trust.groq.com](https://trust.groq.com) — SOC 2 Type II, ZDR contract available
- **Cartesia:** [cartesia.ai](https://cartesia.ai) — SOC 2, HIPAA BAA, ZDR for enterprise
- **OpenAI (fallback):** [openai.com/security-and-privacy](https://openai.com/security-and-privacy) — SOC 2 Type II, 30-day default retention, ZDR via enterprise contract

USAI should execute BAAs and ZDR agreements with all sub-processors before processing HIPAA-regulated data.

### 4.5 Compliance Coverage

| Regulation | Status with Custom Stack |
|-----------|------------------------|
| **CCPA** (California) | Covered — data processed in US, no selling of personal data, deletion on request supported |
| **GDPR** (if applicable) | Covered — data residency in US-East, DPAs available from all sub-processors, SCCs if EU data subjects involved |
| **HIPAA** | Capable — BAAs required from Deepgram, Groq/OpenAI, Cartesia; self-hosted LiveKit eliminates LiveKit Inc. from BAA chain |
| **SOC 2** | All primary vendors SOC 2 certified; VoiceKit backend auditable via structured logs + audit table |
| **FedRAMP** | Not covered in current configuration — AWS GovCloud deployment + FedRAMP-authorized variants of providers required if FedRAMP is mandatory |

### 4.6 User Rights

USAI's end users have the following rights with respect to data stored in VoiceKit:

- **Access:** Session transcripts and tool call logs are queryable via the dashboard API or direct PostgreSQL access.
- **Deletion:** `DELETE /sessions/{id}` removes all records including messages, tool calls, and audit logs for a session. Bulk deletion available.
- **Export:** Sessions exportable as JSON or CSV via dashboard or API.
- **Rectification:** Transcripts are stored as-captured; manual correction possible via direct DB access.

---

## 5. Data Retention Plan

### 5.1 Default Retention Schedule

VoiceKit's built-in `retention_days` setting auto-purges expired sessions on each API startup. The following schedule is recommended for USAI as a starting point, adjustable based on legal and operational requirements.

| Data Type | Default Retention | Purge Mechanism | Rationale |
|-----------|-----------------|-----------------|-----------|
| **Conversation transcripts** (messages table) | 90 days | Auto-purge on startup | Sufficient for support review; limits PHI/PII exposure window |
| **Tool call logs** (tool_calls table) | 90 days | Cascades from session purge | Audit trail for automated actions |
| **Session metadata** (sessions table) | 90 days | Auto-purge | Operational analytics, error diagnosis |
| **Audit logs** (audit_logs table) | 365 days | Separate retention job | Compliance evidence, access review |
| **Recordings** (if enabled) | 30 days | Separate S3 lifecycle policy | Short-lived; large storage cost |
| **Server-side application logs** | 30 days | CloudWatch log retention | Debugging; not conversation content |

### 5.2 Configuration

```yaml
# config/agent.yaml
database:
  enabled: true
  retention_days: 90   # Auto-purge sessions + all related records older than 90 days
```

For audit logs (which should be kept longer), implement a separate scheduled job:

```sql
-- Run weekly via AWS Lambda or cron
DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '365 days';
```

### 5.3 Retention by Scenario

#### Standard Commercial Use
- Transcripts: 90 days
- Audit logs: 1 year
- Rationale: Covers standard dispute resolution window, operational analytics, no extended PHI exposure

#### HIPAA-Regulated Use (Healthcare)
- Transcripts and tool call logs: **6 years** (HIPAA §164.530(j) — documentation retention)
- Audit logs: 6 years
- Recordings: 6 years
- Requires: Encrypted PostgreSQL, BAAs signed with all sub-processors, access controls audited quarterly

#### Legal / Compliance Hold
- Any session flagged for legal hold: **indefinite** until hold released
- Implement a `legal_hold` boolean column on the sessions table; purge job skips held records

### 5.4 Zero Data Retention at the API Level

For conversations where no server-side persistence is required:

```yaml
database:
  enabled: false   # Disables all session/message storage
```

This turns VoiceKit into a pure pass-through relay. No conversation data is persisted. Note: the monitoring dashboard and session export features require the database to be enabled.

### 5.5 Sub-Processor Retention

| Sub-processor | Default Retention | How to Configure ZDR |
|--------------|-----------------|---------------------|
| Deepgram | Minimal (~transient, real-time streaming) | Enterprise plan: explicit ZDR addendum to MSA |
| Groq | 30 days (default) | Enterprise ZDR agreement — contact Groq sales |
| Cartesia | Per enterprise config | Request ZDR as part of enterprise contract |
| OpenAI (fallback) | 30 days | Zero Data Retention addendum (enterprise tier) |

---

## 6. Pricing

### 6.1 API Usage Costs (List Prices, March 2026)

Costs per minute of active voice conversation (user speaking + agent responding, assuming ~750 chars and ~30 sec of speech per side per minute):

| Component | Provider | Unit | List Price | Cost / Minute |
|-----------|----------|------|-----------|--------------|
| STT | Deepgram Nova-3 | per audio minute | **$0.0077/min** | **$0.0077** |
| LLM | Groq Llama 4 Scout | per 1M tokens | $0.11 in / $0.34 out | **~$0.001** |
| LLM (fallback) | OpenAI GPT-4o | per 1M tokens | $2.50 in / $10.00 out | **~$0.015** |
| STT (fallback) | OpenAI Whisper | per audio minute | $0.006/min | **$0.006** |
| TTS | Cartesia Sonic-2 | per 1M chars | **$46.70/M chars** | **~$0.035** |
| TTS (fallback) | OpenAI TTS-1-HD | per 1M chars | $30.00/M chars | **~$0.023** |
| WebRTC | LiveKit Cloud (Ship) | per participant-min | **$0.0005/min** | **$0.001** (2 participants) |

**Estimated total (Groq + Cartesia path, no fallback):** ~**$0.045/minute**
**Estimated total (OpenAI fallback path):** ~**$0.045/minute** (GPT-4o replaces Groq, OpenAI TTS replaces Cartesia — cost similar due to cheaper TTS offsetting pricier LLM)

> Cartesia TTS is the dominant cost component at list prices. Volume discounts kick in after ~$2K/month spend — Cartesia, Groq, and Deepgram all offer negotiated enterprise rates that can reduce blended cost by 30–60% at scale. Self-hosting LiveKit eliminates the $0.001/min relay cost entirely.

### 6.2 Cost Scenarios

API costs only (list prices, Groq + Cartesia primary path at ~$0.045/min):

| Volume | Minutes/Month | API Cost (list) | Est. w/ 40% Volume Discount | Monthly Infra | Total (list) |
|--------|--------------|----------------|------------------------------|--------------|--------------|
| Pilot | 1,000 | $45 | $45 (no discount yet) | $400–600 | **~$500** |
| Small production | 10,000 | $450 | $270 | $500–800 | **~$1,000** |
| Mid-scale | 50,000 | $2,250 | $1,350 | $800–1,500 | **~$3,100** |
| Enterprise | 200,000 | $9,000 | $5,400 | $2,000–4,000 | **~$11,000** |
| High volume | 1,000,000 | $45,000 | $27,000 | $5,000–10,000 | **~$55,000** |

> Infrastructure cost: EC2 (agent workers + API), RDS PostgreSQL, ALB, CloudWatch. Self-hosting LiveKit (open-source) adds ~$200/month at enterprise scale but eliminates per-minute relay fees. Volume discount threshold: Deepgram and Cartesia both offer negotiated rates above ~$2K/month API spend.

### 6.3 Gemini Live Stack Costs (for comparison)

Gemini 2.0 Flash Live (Google AI Studio pricing, March 2026):

| Component | Rate | Tokens/sec | Cost / Minute (30 sec of speech/side) |
|-----------|------|-----------|---------------------------------------|
| Audio input (user speech) | $0.70/1M tokens | 25 tokens/sec | $0.000525 |
| Audio output (agent speech) | $0.40/1M tokens | 25 tokens/sec | $0.000300 |
| Text output (reasoning) | $0.40/1M tokens | ~100 tokens/turn | ~$0.00004 |

**Estimated total:** ~**$0.001/minute** at list prices.

> **Important context:** Gemini 2.0 Flash Live is significantly cheaper than the Custom Stack at list prices due to Google's unified audio token pricing. The primary reasons to choose the Custom Stack over Gemini Live for USAI are **compliance and data sovereignty** (Section 4), not cost. Gemini Live under AI Studio terms may use conversation data for model improvement; Vertex AI migration is required for HIPAA or GDPR-regulated workloads.
>
> Note: Gemini pricing changes frequently. Verify at [ai.google.dev/pricing](https://ai.google.dev/pricing).

### 6.4 VoiceKit Platform Fee

VoiceKit is licensed as a managed platform on top of the underlying API costs above. The platform fee covers: deployment, configuration, monitoring dashboard, ongoing maintenance, provider fallback management, and compliance support.

| Tier | Included Minutes | Platform Fee / Month | Overage Rate |
|------|-----------------|---------------------|-------------|
| **Pilot** | Up to 5,000 min | $1,500/month | $0.10/min over |
| **Growth** | Up to 30,000 min | $3,500/month | $0.08/min over |
| **Scale** | Up to 100,000 min | $7,500/month | $0.06/min over |
| **Enterprise** | 100,000+ min | Custom (contact for quote) | Negotiated |

> Platform fees are billed separately from and in addition to the API usage costs in Sections 6.1–6.2. At mid-scale (50,000 min/month), the blended total — API costs ($2,250 list or ~$1,350 post-discount) + platform fee ($7,500 Scale tier) — is approximately **$8,700–9,750/month**, or ~$0.17–0.19 per minute fully loaded.
>
> Enterprise agreements may substitute a per-minute markup model (e.g., 15–20% markup on verified API spend) in lieu of the tiered flat fee. One-time onboarding and deployment fee: $5,000–$15,000 depending on custom tooling and integrations required.

### 6.5 Cost Optimization Levers

1. **Use Groq as primary LLM** — 10× cheaper than GPT-4o per token, lower latency
2. **Self-host LiveKit** — eliminates $0.001/min WebRTC relay cost; ROI positive above ~30K minutes/month
3. **Tune `vad` aggressively** — reduces audio sent to Deepgram by filtering more silence (lower STT cost)
4. **Negotiate enterprise rates** — Deepgram, Cartesia, and Groq all offer volume pricing after $2K+/month
5. **Set `retention_days: 30`** — reduces PostgreSQL storage cost for high-volume deployments

---

## 7. Backup Recommendation — Gemini Live Stack

### 7.1 When to Use It

The Gemini Live stack is recommended as a backup or secondary deployment in the following scenarios:

| Scenario | Recommendation |
|----------|---------------|
| Naturalness benchmark / demo | Gemini Live wins on expressiveness — use for stakeholder demos |
| Low-traffic internal tools (<5K min/month) | Lower API cost (~$0.001/min) and lower engineering overhead; acceptable where compliance reqs are minimal |
| Google Workspace power users | Native Gmail/Calendar/Drive integration is significantly faster to build |
| Research / R&D environment | No PHI, no HIPAA required — Gemini Live is acceptable |
| Fallback if Custom Stack is degraded | Route traffic to Gemini Live while Custom Stack recovers |

### 7.2 Critical Requirement Before Production Use

**The Gemini Live stack must NOT be deployed to production for USAI without addressing the following:**

1. **Migrate from Google AI Studio API key to Vertex AI**
   - Current configuration uses `REACT_APP_GEMINI_API_KEY` (AI Studio key)
   - Under AI Studio terms, conversation data **may be used by Google for model training**
   - Vertex AI provides contractual data protection and a DPA
   - Migration effort: ~1–2 days of engineering (SDK update + GCP project setup)

2. **Execute a Google Cloud Data Processing Addendum (DPA)**
   - Available at [cloud.google.com/terms/data-processing-addendum](https://cloud.google.com/terms/data-processing-addendum)
   - Required for GDPR compliance if any EU data subjects are involved
   - Required for HIPAA if any protected health information is processed

3. **Confirm Gemini Live is in scope for Google Cloud HIPAA BAA** (if applicable)
   - Not all Google Cloud services are covered under the standard BAA
   - Verify at [cloud.google.com/security/compliance/hipaa](https://cloud.google.com/security/compliance/hipaa)

### 7.3 Backup Stack Privacy Notes (Gemini Live)

Under Vertex AI (post-migration):

| Data Type | Google's Commitment |
|-----------|-------------------|
| Voice audio | Processed in-region; not used for training without explicit opt-in |
| Conversation transcripts | Retained per Google Cloud data retention settings |
| Tool call data | Subject to Google Cloud logging policies |
| Data used for model training | **No** under Vertex AI DPA |
| Data residency | Configurable (US, EU, etc.) via Vertex AI region selection |

---

## 8. Decision Summary & Next Steps

### 8.1 Recommendation Summary

```
PRIMARY:  VoiceKit Custom Stack
          (Deepgram + Groq + Cartesia + LiveKit, self-hosted AWS us-east-1)

BACKUP:   Gemini Live Stack (post Vertex AI migration)
          (Google Gemini 2.0 Flash Live, GCP us-central1)
```

### 8.2 Compliance Actions Required Before Go-Live

| Action | Owner | Priority |
|--------|-------|----------|
| Sign Deepgram Enterprise BAA + ZDR addendum | USAI / VoiceKit | CRITICAL |
| Sign Groq Enterprise ZDR agreement | USAI / VoiceKit | CRITICAL |
| Sign Cartesia Enterprise BAA + ZDR | USAI / VoiceKit | CRITICAL |
| Sign OpenAI Enterprise ZDR (for fallback) | USAI / VoiceKit | HIGH |
| Enable RDS encryption at rest | VoiceKit infra | CRITICAL |
| Configure AWS Secrets Manager | VoiceKit infra | HIGH |
| Set `cors.allow_origins` to production domains | VoiceKit infra | HIGH |
| Define and implement retention schedule | Both | HIGH |
| Conduct security review of tool functions | Both | HIGH |
| (If Gemini Live used) Migrate to Vertex AI | VoiceKit | CRITICAL before prod |
| (If Gemini Live used) Execute Google Cloud DPA | USAI | CRITICAL before prod |

### 8.3 Suggested Phased Rollout

**Phase 1 — Pilot (Weeks 1–4)**
- Deploy Custom Stack to AWS staging environment
- Configure with USAI-specific system prompt and tools
- Internal testing with 5–10 users
- Validate latency, accuracy, and voice quality

**Phase 2 — Compliance Hardening (Weeks 3–6)**
- Execute all BAA and ZDR agreements
- Enable database encryption, secrets manager
- Set production CORS, rate limits, API keys
- Conduct penetration test on public endpoints

**Phase 3 — Production Launch (Week 6+)**
- Cutover to production AWS deployment
- Enable monitoring dashboard and webhook alerts
- Set retention schedule and verify auto-purge
- Establish incident response runbook

**Phase 4 — Optimization (Ongoing)**
- Review Groq vs OpenAI fallback hit rates in dashboard
- Negotiate volume discounts with Deepgram/Cartesia after first 30 days of production data
- Consider self-hosting LiveKit if WebRTC relay costs exceed $500/month

---

*This document is confidential and prepared exclusively for USAI. Pricing figures are estimates based on publicly available list prices as of March 2026 and are subject to change. Compliance assessments are informational and do not constitute legal advice. USAI should engage qualified legal counsel before deploying in regulated industries.*
