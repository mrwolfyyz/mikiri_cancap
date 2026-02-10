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

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Firebase Hosting                             │
│  ┌─────────────────┐              ┌─────────────────┐           │
│  │ Skip Trace UI   │              │ Origination UI  │           │
│  │ (index.html)    │              │ (index.html)    │           │
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
│  • chat_sessions_* (conversation history)               │       │
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
├── VALIDATION_REPORT.md      # Static validation results
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
│   ├── functions/            # Cloud Functions source
│   │   ├── api_gateway/
│   │   ├── phase1_identity/
│   │   ├── domain_enrichment/
│   │   ├── address_geocoding/
│   │   ├── company_domain_lookup/
│   │   ├── aggregator/
│   │   ├── report_generator_skiptrace/
│   │   ├── report_generator_origination/
│   │   ├── chat_handler/
│   │   ├── chat_handler_origination/
│   │   └── address_verification/
│   ├── shared/               # Shared utilities
│   │   └── retry_utils.py
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
