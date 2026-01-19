# Skip Trace & Origination Intelligence Platform

A comprehensive platform for conducting skip trace investigations and loan origination background checks, built on Google Cloud Platform.

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
│  │  Company    │ │ Aggregator  │ │   Report    │               │
│  │  Domain     │ │             │ │  Generator  │               │
│  └─────────────┘ └─────────────┘ └─────────────┘               │
│  ┌─────────────────────────────────────────────┐               │
│  │            Chat Handlers (x2)               │               │
│  │        (Firestore-triggered AI Chat)        │               │
│  └─────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Firestore                                 │
│  • jobs (investigation results)                                 │
│  • chat_sessions_* (conversation history)                       │
└─────────────────────────────────────────────────────────────────┘
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
├── chrome-extension/          # Browser extension for loan system integration
├── docs/                      # Documentation
│   ├── DEPLOYMENT.md         
│   ├── PREREQUISITES.md      
│   └── TROUBLESHOOTING.md    
├── frontend/                  
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
│   └── workflows/            # Cloud Workflow definitions
│       ├── investigate-skiptrace.yaml.tpl
│       └── investigate-origination.yaml.tpl
├── pse-configurations/       # Programmable Search Engine setup guides
├── scripts/                  # Deployment and utility scripts
│   ├── prepare-functions.sh
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

## Quick Start

### Prerequisites

- GCP Project with billing enabled
- gcloud CLI authenticated
- Terraform >= 1.5.0
- Firebase CLI
- API keys (Google Search, HIBP)

See [docs/PREREQUISITES.md](docs/PREREQUISITES.md) for detailed setup.

### Deployment

```bash
# 1. Clone and configure
git clone <repo-url>
cd skip-trace-origination
cp .env.example .env

# 2. Prepare functions
./scripts/prepare-functions.sh

# 3. Deploy infrastructure
cd terraform/environments/dev
terraform init
terraform apply

# 4. Add secrets
echo -n "API_KEY" | gcloud secrets versions add GOOGLE_SEARCH_API_KEY --data-file=-
# ... add other secrets

# 5. Deploy frontends
cd frontend/skiptrace
firebase deploy --only hosting

# 6. Validate
./scripts/validate-deployment.sh PROJECT_ID REGION
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the full guide.

## Configuration

Key Terraform variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `project_id` | GCP Project ID | (required) |
| `region` | GCP Region | `northamerica-northeast1` |
| `cors_allowed_origins` | CORS policy | `*` (dev) |
| `function_memory` | Function memory | 512MB |
| `function_timeout` | Function timeout | 60-540s |

See `terraform/modules/core/variables.tf` for all options.

## Security

- **Authentication**: Firebase Anonymous Auth with token verification
- **Authorization**: Firestore security rules enforce user ownership
- **Secrets**: All API keys in Secret Manager
- **CORS**: Configurable origins (restrict in production)
- **IAM**: Least-privilege service accounts

### Production Recommendations

1. Set specific CORS origins
2. Enable Firebase App Check
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
