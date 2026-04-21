# Security Architecture

This document describes the security architecture of the Mikiri Skip Trace & Origination Intelligence Platform.

**Last Updated**: April 2026  
**Status**: Beta Deployment

---

## Privacy by Design

Mikiri was built with Privacy by Design principles from the beginning:

### Core Principles

**1. Public Data Sources Only**
- No data broker subscriptions or proprietary databases
- Only free, open-source public information
- Eliminates third-party vendor risk and data sharing agreements

**2. Lender Infrastructure**
- Runs entirely in your GCP environment (not SaaS)
- You own the data, control access, and can delete at any time
- No vendor lock-in - all code and infrastructure is yours

**3. Minimal Data Storage**
- **Beta**: 7-day retention for operational learning (skip trace findings → origination risk models)
- **Production**: 1-day retention target
- Automatic deletion after retention period
- Purpose: Keep data only as long as needed for the feedback loop

**4. Data Minimization**
- Only collect what's necessary for investigation
- Email is optional (auto-generated from name if not provided)
- No unnecessary data collection

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Your GCP Environment                    │
│                                                             │
│  Frontend (Firebase) → API Gateway → Workflows → Functions │
│                              ↓                              │
│                         Firestore                           │
│                    (7-day TTL → 1-day)                      │
└─────────────────────────────────────────────────────────────┘

External touchpoints (data still primarily stored in your GCP project):
• HIBP API — email breach checks
• Vertex AI (Gemini) — LLM in your project; some flows use Google Search grounding (queries touch the public web)
• OpenStreetMap Nominatim — address geocoding (full address strings)
• Gravatar — profile lookup (email-derived hash; no raw email in URL)
• Public WHOIS / DNS — domain registration and MX lookups for email domains
```

Primary investigation results and chat stay in Firestore in your GCP project. The sections below list what is sent outside GCP (or to Google APIs that query the public web), in addition to in-project Vertex processing.

---

## Authentication & Authorization

### Current (Phase 1 SSO Baseline)
- **Frontend**: Google sign-in via Firebase Auth (no anonymous auth in dev/prod)
- **API Gateway**: Verifies Firebase ID tokens on investigation (`POST /investigate-skiptrace`, `POST /investigate-origination`), job status (`GET /jobs/{job_id}`), markdown (`GET /get_markdown/{job_id}`), chat (`POST /chat_handler`, `POST /chat_handler_origination`), feedback (`POST /jobs/{job_id}/feedback`), and address verification (`POST /address-verification`)
- **SSO Policy**: Requires `firebase.sign_in_provider == google.com`, `email_verified == true`, and email domain in `ALLOWED_EMAIL_DOMAINS`
- **App Check**: Requests must include a valid `X-Firebase-AppCheck` token when enforcement is enabled
- **Public Endpoints**: `GET /health`, `GET /`, and `OPTIONS` preflight remain unauthenticated
- **Firestore Rules**: Job and chat paths require `resource.data.user_id == request.auth.uid` (no cross-tenant reads by job ID)

### Production Controls
- **SSO Requirement**: Production must set `enable_sso = true` and one or more `allowed_email_domains`
- **Domain Restriction**: Domain allowlist blocks personal/non-corporate accounts even if OAuth succeeds
- **App Check Requirement**: Production enforces App Check for browser API calls
- **Future Hardening**: IAP/load-balancer ingress controls remain phase 2 and are additive

---

## Data Flow & Storage

### What Stays in Your Environment
- Investigation results (Firestore with TTL on `expire_at` for `jobs` and `chat_messages` collection groups; parent TTL does not delete subcollections automatically—see Terraform `google_firestore_field` in `terraform/modules/core/firestore.tf`)
- Borrower PII (names, addresses, phones, emails)
- Generated reports (Firestore + optional Google Drive export)
- Chat history (Firestore)
- Authentication tokens (Firebase)

### What Leaves Your Environment

**1. Have I Been Pwned (HIBP) API**
- **Data Sent**: Email addresses only
- **Data Received**: Breach names and dates
- **Industry Standard**: Used by password managers and enterprises globally

**2. Vertex AI (Gemini)**
- **Data Sent**: Investigation-related prompts and context for AI analysis (varies by function)
- **Data Received**: Analysis and summaries
- **Location**: Runs in YOUR GCP project, billed to you
- **Note**: This is Google Cloud AI, not a random third-party SaaS

**3. Google Search grounding (Gemini tool)**
- **Where**: Phase 1 identity resolution, company domain lookup, and address verification use Gemini 2.5 Flash with Google Search grounding
- **What**: The model issues search queries; snippets and retrieval involve the public web. Processing and billing remain in your GCP project.

**4. OpenStreetMap Nominatim (geocoding)**
- **Data Sent**: Address strings derived from investigation output (geocoding step)
- **Service**: Public Nominatim API at `nominatim.openstreetmap.org`

**5. Gravatar**
- **Data Sent**: MD5 hash of normalized email (standard Gravatar URL pattern)
- **Data Received**: Optional public profile metadata when a profile exists
- **Where**: Report generation (skip trace and origination)

**6. WHOIS and public DNS (domain enrichment)**
- **Data Sent**: Email domain / registration lookups
- **What**: Queries hit public WHOIS and DNS infrastructure (not stored vendor databases; standard internet resolution paths)

---

## Secrets Management

All API keys stored in **Google Secret Manager**:
- `HIBP_API_KEY` - Have I Been Pwned API key

**IAM Controls**:
- Functions Service Account: `roles/secretmanager.secretAccessor`
- No hardcoded secrets anywhere in code
- Secrets loaded via environment variables at function runtime

---

## IAM & Least Privilege

### Service Accounts

**1. Functions Service Account** (`functions-sa@PROJECT_ID.iam.gserviceaccount.com`)
- `roles/aiplatform.user` - Use Vertex AI
- `roles/datastore.user` - Read/write Firestore
- `roles/workflows.invoker` - Invoke workflows
- `roles/discoveryengine.user` - Vertex AI Search

**2. Workflow Service Account** (`workflow-sa@PROJECT_ID.iam.gserviceaccount.com`)
- `roles/datastore.user` - Read/write Firestore
- `roles/workflows.invoker` - Invoke workflows

**3. Cloud Build / Compute Engine Service Accounts** (Google-managed)
- Standard permissions for Cloud Functions Gen2 deployment

**Principle**: Service accounts have **only** the permissions they need. No `roles/editor` or `roles/owner` granted.

---

## Network Security

### Current Configuration
- Functions run in Google's default network
- All communication is HTTPS
- API Gateway is public (requires authentication token)
- All other functions are private (only invoked by workflows)

### CORS
- `CORS_ALLOWED_ORIGINS` is required at startup in api_gateway — the function fails at import if missing or blank; no implicit wildcard default
- Terraform validation rejects `cors_allowed_origins = "*"` when environment is not dev (enforced at plan time)
- Deployment validation script fails non-zero if the deployed api-gateway has `CORS_ALLOWED_ORIGINS` set to `*` in non-dev environments
- **Development**: can be explicitly set to `*` for easier testing
- **Production**: restricted to specific Firebase Hosting URLs (`https://PROJECT_ID-skiptrace.web.app`, `https://PROJECT_ID-origination.web.app`)

---

## Audit & Logging

### Cloud Logging
- All functions generate logs automatically
- Logs may contain PII (names, emails in function inputs/outputs)
- Default retention: 30 days
- **Note**: This is standard GCP behavior

### Firestore Audit Trail
- Every investigation creates a document with:
  - `job_id`, `user_id` (Firebase UID), `created_at` timestamp
  - Investigation parameters and results
  - Status (pending, running, complete, failed)

### Current Limitation
- User attribution depends on correct SSO/provider setup in Firebase Identity Platform
- **Operational Requirement**: Keep Google provider + allowed domain configuration healthy in Terraform/Firebase

---

## Identified Gaps & Roadmap

### High Priority (Before Production)

**1. Penetration Testing**
- **Current**: Not tested by security professionals
- **Fix**: CanCap's security team should conduct testing
- **Timeline**: Before production

### Medium Priority (Post-Beta)

**2. Data Retention Tuning**
- **Current**: Firestore TTL is enabled on `expire_at` for `jobs` and `chat_messages` collection groups (automatic deletion after the retention window; see Terraform `google_firestore_field` in `terraform/modules/core/firestore.tf`). Parent document TTL does not automatically delete subcollections—operational cleanup may still be needed for edge cases.
- **Plan**: Shorten the window from the current **7-day** job lifetime (beta) toward a **1-day** production target, aligned with product and compliance needs
- **Timeline**: Based on operational learnings

**3. PII in Cloud Logs**
- **Current**: Function logs may contain borrower PII
- **Options**: 
  - Redact PII in logs
  - Restrict access to Cloud Logging
  - Accept as standard GCP logging behavior
- **Timeline**: Based on compliance requirements

**4. User Attribution**
- **Current**: Google Workspace SSO with allowed-domain enforcement
- **Plan**: Add centralized enterprise identity controls (for example IAP) as a Phase 2 hardening step
- **Timeline**: Post-beta hardening

### Optional Enhancements

- VPC Service Controls for network isolation
- Firebase App Check for abuse prevention
- Cloud Monitoring dashboards for security metrics
- Automated secret rotation

---

## Compliance Considerations

### PIPEDA (Canadian Privacy Law)
- **Consent**: CanCap already has consent via loan application process
- **Data Minimization**: Only collect what's necessary ✓
- **Storage Limitation**: 7-day (beta) → 1-day (production) TTL ✓
- **Right to Deletion**: Firestore documents can be deleted immediately ✓

### Data Retention
- Short retention period (7 days → 1 day) aligns with privacy best practices
- Business justification: Operational feedback loop (skip trace → origination)
- Can be adjusted based on compliance requirements

---

## Incident Response

Because everything runs in your GCP environment, **you have full control**:

1. **Disable Functions**: Remove IAM permissions via GCP console
2. **Rotate Secrets**: Add new secret versions in Secret Manager (zero downtime)
3. **Revoke Access**: Disable Firebase authentication
4. **Delete Data**: Purge Firestore collections via console or API
5. **Review Logs**: Cloud Logging has complete audit trail
6. **Rollback Infrastructure**: Terraform state versioned in GCS

**No vendor coordination required** - you control your own incident response.

---

## What Makes This Secure

### Compared to Typical SaaS Solutions

| SaaS Vendor Concern | Mikiri Approach |
|---------------------|-----------------|
| Data leaves your environment | ❌ Stays in your GCP |
| Vendor has access to data | ❌ You control all access |
| Vendor security audit required | ❌ Audit your own environment |
| Data sharing agreements | ❌ Only public data sources |
| Vendor lock-in | ❌ You own the code |
| Black-box processing | ❌ All code is open to you |

### Security Best Practices

- ✅ **Secrets in Secret Manager** (no hardcoded credentials)
- ✅ **Least-Privilege IAM** (no editor/owner service accounts)
- ✅ **Firestore Security Rules** (user isolation enforced)
- ✅ **Token Verification** (Firebase ID token required for all user-data API routes; health and CORS preflight are public)
- ✅ **CORS Restricted** (required at api_gateway startup; Terraform and deploy validation block wildcard in non-dev; hosting URLs in production)
- ✅ **Server-Side Rate Limiting** (per-user, 5 requests per 5 minutes via Firestore; fails closed on Firestore error (returns 429 rather than allowing unlimited requests through))
- ✅ **Request Size Limits** (50KB for investigations, 500KB for chat including markdown context)
- ✅ **Conversation History Cap** (frontend: 40 messages, backend: rejects >50 messages)
- ✅ **Input Validation** (request size limits, field length limits; `full_name` and `city` validated via NFKC normalization + Unicode letter allow-list in shared module `gcp/shared/llm_input_validators.py`, applied consistently across `api_gateway` and `query_constructor`; province validated as two-letter code or allow-listed free text; HTTP 400 on invalid input — fail closed)
- ✅ **XSS Prevention** (defense in depth: `escapeHtml` via browser-native DOM for text values; `textContent` for user input; URL allowlisting via `sanitizeMarkdownLinkUrl` and `isSafeHttpUrl`; attribute encoding via `escapeHtmlAttr`; DOMPurify with explicit tag/attribute allowlists for markdown and vendor HTML)
- ✅ **Infrastructure as Code** (Terraform = auditable, repeatable)
- ✅ **Short Data Retention** (7 days → 1 day target)
- ✅ **Public Data Only** (no proprietary databases or vendor dependencies)

---

## Common Questions

**Q: What PII leaves our environment?**
A: **Email addresses** go to HIBP for breach checking. **Address strings** may be sent to OpenStreetMap Nominatim for geocoding. **MD5 hashes of email** (Gravatar’s standard) are used for Gravatar lookups. **Domains** are resolved via public WHOIS/DNS. **Investigation text and context** are sent to Vertex AI (Gemini) in your project; flows that use **Google Search grounding** also cause the model to query the public web as part of analysis. Primary structured results remain in Firestore in your project.

**Q: Can we audit who ran which investigation?**
A: Yes. With SSO enabled, each investigation is tied to a Firebase UID and verified email identity from Google sign-in.

**Q: How do we delete borrower data?**  
A: Firestore documents can be deleted via console or API at any time. TTL on `expire_at` also removes job and chat message documents automatically after the configured retention window. Retention length can be adjusted for compliance requirements.

**Q: What happens if HIBP goes down?**  
A: Investigation continues without breach data. Platform degrades gracefully if external APIs fail.

**Q: Can employees see each other's investigations?**  
A: No - Firestore security rules enforce user isolation. Each user can only access their own data.

**Q: How do we rotate API keys?**  
A: Add new version to Secret Manager. Functions pick up new version on next invocation. Zero downtime.

---

## Summary

Mikiri's security architecture follows **Privacy by Design principles**:

- Built with privacy as a core principle, not an afterthought
- Uses only public data sources (no vendor dependencies)
- Runs entirely in your GCP environment (you control everything)
- Minimal data storage with TTL (7 days → 1 day target)
- Proper secrets management, IAM, and authentication
- Infrastructure as Code (auditable and repeatable)

**Identified gaps are addressable**:
- Penetration testing (your security team)
- Identity hardening controls (IAP/network restrictions) as Phase 2
- Other enhancements based on operational learnings

This is **customer-controlled infrastructure**, not vendor SaaS. You own the code, the data, and the security controls.
