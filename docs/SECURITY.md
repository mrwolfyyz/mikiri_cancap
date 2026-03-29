# Security Architecture

This document describes the security architecture of the Mikiri Skip Trace & Origination Intelligence Platform.

**Last Updated**: January 2026  
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

External APIs (minimal):
• HIBP API - email breach checks only
• Vertex AI - Google-hosted LLM in YOUR project
• Gemini 2.5 Flash - Google Search grounding for address verification
```

All borrower data stays in your GCP environment except:
- Email addresses sent to HIBP for breach checking
- Investigation data sent to Vertex AI (stays within your GCP project)

---

## Authentication & Authorization

### Current (Beta)
- **Frontend**: Firebase Anonymous Authentication
- **API Gateway**: Verifies Firebase ID tokens on job, chat, markdown, feedback, and investigation routes; health checks remain unauthenticated
- **Firestore Rules**: Job and chat paths require `resource.data.user_id == request.auth.uid` (no cross-tenant reads by job ID)
- **Rationale**: Keep beta deployment simple

### Production Plan
- **Implementation Time**: ~1 hour
- **Provider Flexibility**: Google Workspace, Azure AD, Okta, or any SAML/OIDC provider
- **Firebase Integration**: Built-in support for all major identity providers
- **User Attribution**: Investigations tied to specific employee identities

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
- **Data Sent**: Investigation data for AI analysis
- **Data Received**: Analysis and summaries
- **Location**: Runs in YOUR GCP project, billed to you
- **Note**: This is Google Cloud AI, not a third-party service

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
- **Configurable**: `cors_allowed_origins` in `terraform.tfvars`, propagated to API Gateway, chat handlers, and address verification via environment variable
- **Production**: Restricted to specific Firebase Hosting URLs
  - `https://PROJECT_ID-skiptrace.web.app`
  - `https://PROJECT_ID-origination.web.app`
- **Development**: Can be set to `*` for easier testing

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
- Anonymous auth means no correlation to specific CanCap employees
- **Production Solution**: Add identity provider for full attribution

---

## Identified Gaps & Roadmap

### High Priority (Before Production)

**1. Penetration Testing**
- **Current**: Not tested by security professionals
- **Fix**: CanCap's security team should conduct testing
- **Timeline**: Before production

### Medium Priority (Post-Beta)

**2. Data Retention Policy**
- **Current**: Manual deletion only
- **Plan**: Automated TTL enforcement (currently 7 days, target 1 day)
- **Timeline**: Based on operational learnings

**3. PII in Cloud Logs**
- **Current**: Function logs may contain borrower PII
- **Options**: 
  - Redact PII in logs
  - Restrict access to Cloud Logging
  - Accept as standard GCP logging behavior
- **Timeline**: Based on compliance requirements

**4. User Attribution**
- **Current**: Anonymous auth
- **Plan**: Add identity provider (1 hour implementation)
- **Timeline**: When CanCap specifies preferred provider

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
- ✅ **Token Verification** (all API calls authenticated)
- ✅ **CORS Restricted** (configurable per environment, locked to hosting URLs in production)
- ✅ **Server-Side Rate Limiting** (per-user, 5 requests per 5 minutes via Firestore)
- ✅ **Request Size Limits** (50KB for investigations, 500KB for chat including markdown context)
- ✅ **Conversation History Cap** (frontend: 40 messages, backend: rejects >50 messages)
- ✅ **Input Validation** (request size limits, field length limits, city character whitelist, province code validation)
- ✅ **XSS Prevention** (DOMPurify sanitization on injected HTML)
- ✅ **Infrastructure as Code** (Terraform = auditable, repeatable)
- ✅ **Short Data Retention** (7 days → 1 day target)
- ✅ **Public Data Only** (no proprietary databases or vendor dependencies)

---

## Common Questions

**Q: What PII leaves our environment?**
A: Only email addresses (to HIBP for breach checking). Vertex AI processing stays within your GCP project.

**Q: Can we audit who ran which investigation?**
A: Not in beta (anonymous auth). For production, specify your preferred identity provider (Google Workspace, Azure AD, etc.) and we'll implement it via Firebase in ~1 hour.

**Q: How do we delete borrower data?**  
A: Firestore documents can be deleted via console or API at any time. No vendor coordination required. We can implement automated retention policies based on your compliance requirements.

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
- Production auth provider (1 hour to implement)
- Other enhancements based on operational learnings

This is **customer-controlled infrastructure**, not vendor SaaS. You own the code, the data, and the security controls.
