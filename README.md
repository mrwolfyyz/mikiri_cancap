# Mikiri: Skip Trace & Origination Intelligence Platform

**The Name**: Mikiri (見切り) is the Japanese word for "understanding" or "seeing through." In samurai tradition, it meant "know your enemy" - the art of understanding your opponent's capabilities and intentions before engagement. Miyamoto Musashi, Japan's most legendary swordsman, exemplified this principle in his famous duel with Sasaki Kojirō. Through careful reconnaissance, Musashi observed that Kojirō wielded an exceptionally long sword called the "Drying Pole." In response, Musashi carved a bokken (wooden sword) from a boat oar - deliberately making it one inch longer than Kojirō's blade. This tactical insight, gained through patient observation, gave Musashi the critical reach advantage that secured his victory. Like Musashi's approach, this platform uses thorough investigation and analysis to reveal hidden information that provides decisive advantage in risk assessment.

**Dual-purpose risk intelligence for subprime auto lending** - combining origination fraud detection with skip trace vehicle recovery using exclusively free and open-source data. Delivers comprehensive borrower investigations in under 60 seconds, identifying risk at loan origination and locating borrowers and vehicles for collections.

**The innovation**: Creates a continuous learning feedback loop where skip trace findings automatically inform origination decisions. Real-world recovery outcomes - what actually works in collections - deploy back to underwriting risk models within hours, not months. All powered by AI analysis of public data sources, eliminating expensive third-party data subscriptions.

**Current status**: Beta deployment for CanCap's internal use, designed for rapid iteration based on skip tracing and origination team feedback.

## Overview

This platform provides two main investigation types:

### Skip Trace Intelligence
- Identity resolution and verification
- Domain/company enrichment
- Address geocoding and verification
- Social media and professional profile discovery
- AI-powered analysis and reporting

### Origination Intelligence
- Comprehensive borrower background checks
- Corporate and litigation research
- Regulatory compliance verification
- Business address verification
- Risk assessment reporting

## Components

An investigation flows through the system in two phases. Before Phase 1 begins, **Company Domain Lookup** resolves the borrower's employer to a web domain. In **Phase 1**, the Query Constructor and phase1_identity work together to build search queries, execute them against Vertex AI Search, and resolve the borrower's identity. In **Phase 2**, Domain Enrichment, Contact Extractor, Address Geocoding, and (for origination) Business Verification run in parallel against the Phase 1 results. The Aggregator then merges everything, and the Report Generator produces the final Markdown report.

### Front-end
Two single-page web applications (Skip Trace and Origination) hosted on Firebase, each with search, results, and chat views. Built from shared HTML templates and JavaScript/CSS, they authenticate users via Firebase Anonymous Auth and communicate with the back-end exclusively through the API Gateway.

### API Gateway
The single HTTP entry point for all front-end requests. It verifies Firebase auth tokens, validates incoming requests, triggers the appropriate Cloud Workflow for new investigations, and serves job status back to the front-end while the investigation runs.

### Workflows
Two Google Cloud Workflow definitions (skip trace and origination) that orchestrate the full investigation pipeline. Each workflow calls Phase 1 functions sequentially, fans out to Phase 2 functions in parallel, then calls the Aggregator and sets the job status to `post_processing` to trigger report generation.

### Company Domain Lookup
An HTTP Cloud Function called at the start of both workflows. It uses Gemini with live Google Search grounding to resolve a borrower's declared employer name to its official web domain, then writes the result back to the Firestore job record so downstream functions (Domain Enrichment, Business Verification) can use it.

### Query Constructor
An HTTP Cloud Function that uses Gemini to generate realistic name variations for a borrower (e.g. "Alexander MacKay" → "Alex MacKay", "Sandy MacKay") and combines them with their city and province into a structured boolean query for Vertex AI Search. The output is passed directly to phase1_identity to drive the identity search.

### phase1_identity
The core identity resolution function. It executes up to six Vertex AI Search queries in parallel across three purpose-built search engines (social/professional profiles, lifestyle/hobby sites, LinkedIn), then feeds all results to Gemini with live Google Search grounding to identify social handles, infer location, and surface identity clues. It also runs a HaveIBeenPwned breach lookup in parallel and produces a contactability score based on the borrower's online footprint and breach history.

### Domain Enrichment
An HTTP Cloud Function that runs WHOIS and MX record lookups in parallel against the borrower's email domain and the company domain resolved by Company Domain Lookup. WHOIS reveals how long the domain has been registered; MX analysis classifies the mail provider (Google Workspace, Microsoft 365, parked domain, etc.) and assigns a risk level used to assess employer legitimacy.

### Contact Extractor
An HTTP Cloud Function that uses Gemini to extract phone numbers, email addresses, and physical addresses directly from the search result snippets already collected by phase1_identity — no additional web requests are made. Results are confidence-scored and returned to the Workflow for the Aggregator.

### Address Geocoding
An HTTP Cloud Function that uses Gemini to identify physical addresses belonging to the target person from across all search result snippets (identity and corporate queries), assigning a confidence level to each. Each extracted address is then geocoded to lat/lng coordinates via the Nominatim (OpenStreetMap) API, with requests paced at 1 per second to comply with Nominatim's rate limit.

### Aggregator
An HTTP Cloud Function that receives the outputs of phase1_identity, Domain Enrichment, Contact Extractor, and Address Geocoding from the Workflow and merges them into a single result document written to Firestore. It handles partial failures gracefully — if a Phase 2 function failed, its section is set to empty and an error is recorded, but aggregation continues.

### Report Generators
Two Firestore-triggered Cloud Functions (one for skip trace, one for origination) that fire when a job's status transitions to `post_processing`. They use Gemini to synthesize the aggregated findings into a detailed Markdown investigation report, store the report in Firestore for the chat feature, and upload it to a per-borrower folder in Google Drive.

### Firestore
The platform's primary datastore, holding job records, aggregated investigation results, Markdown reports, and per-job chat message history. Firestore security rules enforce strict user ownership so no tenant can access another's data. A 7-day TTL is set on completed jobs.

### Chat Handlers
Two HTTP Cloud Functions (skip trace and origination) that power the post-report AI chat interface. They receive a user message and the job's conversation history, prepend the full Markdown investigation report as context on the first turn, and call Gemini 3 Flash to generate a response. These handlers are intentionally experimental — designed to let the skip tracing and origination teams ask follow-up questions about completed investigations and provide early feedback on both the report quality and the chat experience.

### Business Verification
An HTTP Cloud Function used in origination investigations. It uses Gemini with live Google Search grounding to verify that a declared employer actually exists at the claimed address, specifically detecting virtual offices (Regus, WeWork), mailbox services (UPS Store, FedEx Office), and addresses with no evidence of business presence. It also geocodes the address and generates a Google Maps Street View URL for manual review.

### Chrome Extension
*(Future enhancement — not part of the current production setup.)* A planned quality-of-life tool for both CanCap teams. Once the core platform is stable and validated, it will allow underwriters and skip tracers to extract borrower data from their existing loan origination system with one click and open the appropriate Mikiri interface with fields pre-populated, eliminating manual data entry.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Firebase Hosting                             │
│  ┌─────────────────┐              ┌─────────────────┐           │
│  │ Skip Trace UI   │              │ Origination UI  │           │
│  │ (index.html)    │              │ (index.html)    │           │
│  │ (results.html)  │              │ (results.html)  │           │
│  │ (chat.html)     │              │ (chat.html)     │           │
│  └────────┬────────┘              └────────┬────────┘           │
└───────────┼─────────────────────────────────┼───────────────────┘
            │ Firebase Auth                   │
            │ (Anonymous)                     │
            ▼                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                       API Gateway                                │
│              (Cloud Function Gen2 - HTTP)                        │
│  • Authentication verification                                   │
│  • Request validation                                           │
│  • Workflow triggering                                          │
│  • Job status retrieval                                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  Skip Trace   │   │  Origination  │   │   Address     │
│   Workflow    │   │   Workflow    │   │ Verification  │
└───────┬───────┘   └───────┬───────┘   └───────────────┘
        │                   │
        │  Orchestrates     │
        ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Cloud Functions (Gen2)                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │  Phase 1    │ │   Domain    │ │   Address   │               │
│  │  Identity   │ │ Enrichment  │ │  Geocoding  │               │
│  └─────────────┘ └─────────────┘ └─────────────┘               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐               │
│  │  Company    │ │ Aggregator  │ │   Report    │───────┐       │
│  │  Domain     │ │             │ │  Generator  │       │       │
│  └─────────────┘ └─────────────┘ └──────┬──────┘       │       │
│  ┌─────────────────────────────────────────────┐       │       │
│  │            Chat Handlers (x2)               │       │       │
│  │        (Firestore-triggered AI Chat)        │       │       │
│  └─────────────────────────────────────────────┘       │       │
└─────────────────────────────────────────────────────────┼───────┘
                             │                            │
                             ▼                            │
┌─────────────────────────────────────────────────────────┼───────┐
│                        Firestore                        │       │
│  • jobs (investigation results, markdown reports)       │       │
│  • jobs/{jobId}/chat_messages (conversation history)     │       │
└─────────────────────────────────────────────────────────┘       │
                                                                   │
                                                                   │
                             ┌─────────────────────────────────────┘
                             │ Markdown Reports
                             ▼
                    ┌─────────────────┐
                    │  Google Drive   │
                    │  (Per-borrower  │
                    │   folders)      │
                    └────────┬────────┘
                             │ Sync (external)
                             ▼
                    ┌─────────────────┐
                    │    Obsidian     │
                    │ (Knowledge Base)│
                    └─────────────────┘
```

## Features

- **Multi-tenant** - User isolation via Firebase Anonymous Auth
- **Serverless** - Auto-scaling Cloud Functions and Workflows
- **AI-Powered** - Vertex AI Gemini for intelligent analysis and chat
- **Secure** - Secret Manager for API keys, Firestore security rules
- **Infrastructure as Code** - Full Terraform deployment

## Directory Structure

```
skip-trace-origination/
├── .env.example              # Environment configuration template (optional)
├── .gitignore                # Git exclusions
├── README.md
├── chrome-extension/          # Browser extension for loan system integration
├── docs/                      # Documentation
│   ├── DEPLOYMENT.md         
│   ├── PREREQUISITES.md      
│   ├── REPOSITORY_SETUP.md   # Guide for setting up git repository
│   └── TROUBLESHOOTING.md    
├── frontend/                  
│   ├── shared/               # Shared frontend code (source of truth)
│   │   ├── public/           # Shared JS/CSS (copied by prepare-frontend.sh)
│   │   └── templates/        # HTML templates (processed per platform)
│   ├── skiptrace/            # Skip trace web app
│   │   └── public/
│   └── origination/          # Origination web app
│       └── public/
├── gcp/                       
│   ├── functions/            # Cloud Functions source (13 functions)
│   │   ├── api_gateway/
│   │   ├── phase1_identity/
│   │   ├── query_constructor/
│   │   ├── contact_extraction/
│   │   ├── domain_enrichment/
│   │   ├── address_geocoding/
│   │   ├── company_domain_lookup/
│   │   ├── aggregator/
│   │   ├── report_generator_skiptrace/
│   │   ├── report_generator_origination/
│   │   ├── chat_handler/
│   │   ├── chat_handler_origination/
│   │   └── address_verification/
│   ├── shared/               # Shared Python utilities (copied by prepare-functions.sh)
│   │   ├── retry_utils.py
│   │   ├── address_utils.py
│   │   ├── contact_extraction_utils.py
│   │   ├── domain_utils.py
│   │   ├── report_utils.py
│   │   └── chat_handler_base.py
│   └── workflows/            # Cloud Workflow templates
│       ├── investigate-skiptrace.yaml.tpl
│       └── investigate-origination.yaml.tpl
├── scripts/                  # Deployment and utility scripts
│   ├── prepare-functions.sh  # Copy shared Python utils to functions
│   ├── prepare-frontend.sh   # Copy shared JS/CSS and process HTML templates
│   ├── validate-deployment.sh
│   ├── smoke-test.sh
│   └── get-function-urls.sh
└── terraform/                # Infrastructure as Code
    ├── modules/
    │   └── core/            # Main Terraform module
    └── environments/
        ├── dev/             # Development environment
        └── prod/            # Production environment
```

## Getting Started

⚠️ **IMPORTANT**: This platform requires careful, step-by-step deployment. Do not attempt shortcuts.

**Complete deployment requires following the documentation in this exact order:**

1. **[docs/PREREQUISITES.md](docs/PREREQUISITES.md)** - Set up GCP project, APIs, and required tools
2. **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** - Deploy infrastructure, secrets, and frontends
3. **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Reference for common issues

Allow 2-3 hours for first-time deployment.

**Deployment validation**: After completing deployment, run `./scripts/validate-deployment.sh PROJECT_ID REGION` to verify all components are correctly configured.

## Configuration

Key Terraform variables to configure in `terraform.tfvars`:

| Variable | Description | Default |
|----------|-------------|---------|
| `project_id` | GCP Project ID | (required) |
| `region` | GCP Region | `northamerica-northeast1` |
| `cors_allowed_origins` | CORS policy | `*` (dev) |

**Note**: Additional variables like `function_memory`, `function_timeout`, and `function_max_instances` are configured per-function with sensible defaults. See `terraform/modules/core/variables.tf` for all configuration options.

## Security

- **Authentication**: Firebase Anonymous Auth with token verification
- **Authorization**: Firestore security rules enforce user ownership
- **Secrets**: All API keys in Secret Manager
- **CORS**: Configurable origins (restrict in production)
- **IAM**: Least-privilege service accounts

### Production Recommendations

1. Set specific CORS origins
2. Enable Firebase App Check (see `terraform/modules/core/firebase.tf` for configuration)
3. Configure monitoring and alerting
4. Use VPC Service Controls
5. Enable audit logging

## Development

### Local Testing

```bash
# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies for a function
cd gcp/functions/api_gateway
pip install -r requirements.txt

# Test locally with functions-framework
functions-framework --target=main --debug
```

### Firebase Emulators

```bash
# Start emulators
firebase emulators:start

# Access at http://localhost:4000
```

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues.

## License

Proprietary - All rights reserved.

## Support

For issues and questions, contact the development team.
