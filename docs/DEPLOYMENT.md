# Deployment Guide

This guide covers the complete deployment process for the Skip Trace & Origination Intelligence Platform.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Configuration](#configuration)
4. [Deployment Steps](#deployment-steps)
5. [Post-Deployment](#post-deployment)
6. [Updating Deployments](#updating-deployments)
7. [Rollback](#rollback)

---

## Prerequisites

**⚠️ IMPORTANT: Complete ALL prerequisites before starting deployment.**

You must complete [PREREQUISITES.md](./PREREQUISITES.md) first, including:

- ✅ GCP Project created with billing enabled
- ✅ Terraform state bucket created
- ✅ Local tools installed (gcloud, terraform, firebase-tools)
- ✅ Tools authenticated (`gcloud auth login`, `gcloud auth application-default login`, `firebase login`)
- ✅ API keys ready (Google Search, HIBP)
- ✅ All PSEs created (6 PSEs with CX IDs noted)
- ✅ Verification Checklist completed

**Once you have completed the Verification Checklist in PREREQUISITES.md, proceed to Initial Setup below.**

---

## Initial Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd skip-trace-origination
```

**Note**: If you already have the repository cloned, you can skip this step.

### 2. Set Environment Variables (Optional)

**Note**: The `.env` file is optional. Scripts accept command-line arguments and will work without it.

**Option A: Use command-line arguments (Recommended)**
- Scripts accept project ID and region as arguments
- Example: `./scripts/validate-deployment.sh PROJECT_ID REGION`
- No `.env` file needed

**Option B: Set environment variables in your shell**
```bash
export GCP_PROJECT="your-project-id"
export GCP_REGION="northamerica-northeast1"
```
Scripts will use these if no arguments are provided.

**Option C: Create `.env` file (not auto-sourced)**
```bash
# Copy example environment file
cp .env.example .env

# Edit with your values
nano .env
```

If you create `.env`, you must manually source it before running scripts:
```bash
source .env
./scripts/validate-deployment.sh
```

**Note**: Terraform uses `terraform.tfvars`, not `.env`. The `.env` file is only for shell convenience.

### 3. Verify Prerequisites Are Complete

Before proceeding, verify that you've completed authentication (this should already be done if you followed PREREQUISITES.md):

```bash
# Verify gcloud authentication
gcloud auth list

# Verify application default credentials
gcloud auth application-default print-access-token > /dev/null && echo "✓ Application default credentials configured" || echo "✗ Run: gcloud auth application-default login"

# Verify quota project is set (required for Identity Platform API)
# IMPORTANT: You must set this manually - it's required before terraform apply
gcloud auth application-default set-quota-project PROJECT_ID

# Verify Firebase authentication
firebase projects:list > /dev/null && echo "✓ Firebase authenticated" || echo "✗ Run: firebase login"

# Verify project is set
gcloud config get-value project
```

**Note**: The quota project must be set in Application Default Credentials using `gcloud auth application-default set-quota-project`. This is a prerequisite for the Identity Platform API (`identitytoolkit.googleapis.com`). The `billing_project` parameter in Terraform provider blocks is configured but does not set the ADC quota project - it must be set separately using the `gcloud` command above.

**If any checks fail**, go back to [PREREQUISITES.md](./PREREQUISITES.md) and complete the missing steps.

---

## Configuration

### Terraform Variables

Navigate to your environment directory and configure variables:

```bash
cd terraform/environments/dev  # or prod

# Copy example files
cp backend.tf.example backend.tf
cp terraform.tfvars.example terraform.tfvars

# Edit terraform.tfvars with your values
nano terraform.tfvars
```

**Note**: Both `dev` and `prod` directories have `terraform.tfvars.example` files. Copy and configure for your environment.

Key variables to configure:

| Variable | Description | Example |
|----------|-------------|---------|
| `project_id` | GCP Project ID | `my-project-123` |
| `region` | GCP Region | `northamerica-northeast1` |
| `location` | GCP Location (for Firestore) | `northamerica-northeast1` |
| `cors_allowed_origins` | CORS origins | `https://myapp.web.app` (prod) or `*` (dev) |

### Backend State (GCS)

**Note**: If you completed PREREQUISITES.md, you already created the state bucket. If not, create it now:

```bash
# Replace PROJECT_ID and REGION with your values
PROJECT_ID="your-project-id"
REGION="northamerica-northeast1"

# Create state bucket
gsutil mb -l $REGION -p $PROJECT_ID gs://${PROJECT_ID}-terraform-state

# Enable versioning
gsutil versioning set on gs://${PROJECT_ID}-terraform-state
```

Update `backend.tf` with your bucket name:

After copying `backend.tf.example` to `backend.tf`, edit it and replace `YOUR_PROJECT_ID` with your actual project ID:

```bash
# The backend.tf.example contains:
# bucket = "YOUR_PROJECT_ID-terraform-state"
#
# Replace YOUR_PROJECT_ID with your actual project ID
nano backend.tf
```

---

## Deployment Steps

### Step 1: Prepare Functions

Run the preparation script to copy shared utilities:

```bash
# From the repository root directory
cd skip-trace-origination
./scripts/prepare-functions.sh
```

**Note**: This script copies `retry_utils.py` to function directories that need it.

### Step 2: Initialize Terraform

```bash
cd terraform/environments/dev  # or prod

# Initialize Terraform
terraform init

# Review the plan
terraform plan
```

### Step 3: Apply Terraform

```bash
# Apply the configuration
terraform apply

# Type 'yes' when prompted
```

This will:
- Enable required GCP APIs
- Create Secret Manager secrets
- Deploy Cloud Functions
- Create Cloud Workflows
- Configure Firebase (auth, hosting sites, web apps)
- Generate `firebase-config.json` for frontends
- Generate `config.js` for Chrome extension

### Step 3b: Verify Generated Configuration Files

After `terraform apply` completes, verify that configuration files were generated:

```bash
# Verify firebase-config.json files exist
ls -la frontend/skiptrace/public/firebase-config.json
ls -la frontend/origination/public/firebase-config.json

# Verify Chrome extension config exists
ls -la chrome-extension/config.js

# Check API Gateway URL (you'll need this)
cd terraform/environments/dev  # or prod
terraform output api_gateway_url
```

**Important**: If these files don't exist, the frontends won't work. Check Terraform outputs for any errors.

### Step 4: Add Secret Values

Terraform creates empty secrets. Add the actual values:

```bash
# Set your project ID (or add --project=PROJECT_ID to each command)
export PROJECT_ID="your-project-id"

# Add Google Search API key
echo -n "YOUR_API_KEY" | gcloud secrets versions add GOOGLE_SEARCH_API_KEY --data-file=- --project=$PROJECT_ID

# Add other secrets similarly
echo -n "YOUR_CX" | gcloud secrets versions add GOOGLE_SEARCH_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add PRECISION_PSE_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add RECALL_PSE_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add RECALL_PSE_CX_2 --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add LINKEDIN_PSE_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add REVIEWS_PSE_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_CX" | gcloud secrets versions add COMPLAINTS_PSE_CX --data-file=- --project=$PROJECT_ID
echo -n "YOUR_API_KEY" | gcloud secrets versions add HIBP_API_KEY --data-file=- --project=$PROJECT_ID
```

**Verify secrets were added**:

```bash
gcloud secrets list --project=$PROJECT_ID --filter="name~GOOGLE_SEARCH OR name~PSE OR name~HIBP"
```

### Step 5: Configure Firebase for Hosting

Before deploying, ensure Firebase is configured for each frontend:

```bash
# Navigate to Skip Trace frontend
cd frontend/skiptrace

# Verify firebase.json exists
cat firebase.json

# Ensure you're using the correct project
firebase use PROJECT_ID  # Replace with your project ID

# Verify .firebaserc exists and has correct project
cat .firebaserc
```

Repeat for Origination frontend:

```bash
cd ../origination
firebase use PROJECT_ID
cat .firebaserc
```

**Note**: The `.firebaserc` file should reference your project ID. If it doesn't exist or is incorrect, Firebase CLI will prompt you during deployment.

### Step 5b: Configure Firebase Hosting Targets

Before deploying, you must configure the hosting targets to map the target names in `firebase.json` to the actual site IDs created by Terraform:

```bash
# Configure Skip Trace hosting target
cd frontend/skiptrace
firebase target:apply hosting skiptrace PROJECT_ID-skiptrace

# Configure Origination hosting target
cd ../origination
firebase target:apply hosting origination PROJECT_ID-origination
```

**Note**: The `firebase.json` files use target names (`skiptrace` and `origination`), but Terraform creates sites with IDs like `PROJECT_ID-skiptrace`. The `target:apply` command maps the target names to the actual site IDs.

### Step 6: Deploy Firebase Hosting

```bash
# Deploy Skip Trace frontend
cd frontend/skiptrace
firebase deploy --only hosting

# Deploy Origination frontend
cd ../origination
firebase deploy --only hosting
```

**Note**: After configuring targets with `firebase target:apply`, use `firebase deploy --only hosting` (without the site ID) - Firebase will use the target mapping from `.firebaserc`.

### Step 6b: Deploy Firestore Security Rules

Deploy Firestore security rules to enable chat history persistence and access control:

```bash
# Deploy Skip Trace Firestore rules
cd frontend/skiptrace
firebase deploy --only firestore:rules

# Deploy Origination Firestore rules
cd ../origination
firebase deploy --only firestore:rules
```

**Note**: Firestore rules are required for chat history to persist across page refreshes. Without deployed rules, chat messages cannot be saved or loaded. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for more details on Firestore rules.

### Step 7: Validate Deployment

Run the validation script (from repository root):

```bash
# From skip-trace-origination directory
cd skip-trace-origination  # if not already there
./scripts/validate-deployment.sh PROJECT_ID REGION
```

Run smoke tests:

```bash
./scripts/smoke-test.sh PROJECT_ID REGION
```

---

## Post-Deployment

### Verify Endpoints

1. **API Gateway Health**:
   
   First, get the API Gateway URL from Terraform outputs:
   ```bash
   cd terraform/environments/dev  # or prod
   terraform output api_gateway_url
   ```
   
   Then test the health endpoint (Gen2 functions use `.a.run.app` URLs):
   ```bash
   curl https://api-gateway-HASH-REGION.a.run.app/health
   # Or use the URL from terraform output:
   curl $(terraform output -raw api_gateway_url)/health
   ```

2. **Skip Trace Frontend**:
   Open `https://PROJECT_ID-skiptrace.web.app`

3. **Origination Frontend**:
   Open `https://PROJECT_ID-origination.web.app`

### Configure Chrome Extension

1. The Chrome extension config file is automatically generated by Terraform at:
   ```
   skip-trace-origination/chrome-extension/config.js
   ```
   
   Verify it exists:
   ```bash
   cat chrome-extension/config.js
   ```

2. Load the extension in Chrome:
   - Navigate to `chrome://extensions/`
   - Enable "Developer mode"
   - Click "Load unpacked"
   - Select the `chrome-extension` directory

### Set Up Monitoring (Recommended)

1. **Cloud Monitoring**: Set up dashboards for function invocations, latency, errors
2. **Cloud Logging**: Configure log-based alerts for errors
3. **Uptime Checks**: Create uptime checks for the API Gateway

---

## Updating Deployments

### Update Functions

```bash
# Make code changes, then:
terraform apply
```

### Update Secrets

```bash
# Add new version of a secret
echo -n "NEW_VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-

# Functions will pick up new secrets on next invocation
```

### Update Frontend

```bash
cd frontend/skiptrace  # or origination
firebase deploy --only hosting
```

---

## Rollback

### Terraform Rollback

```bash
# View state history (if using GCS backend with versioning)
gsutil ls -la gs://PROJECT_ID-terraform-state/

# Restore previous state
gsutil cp gs://PROJECT_ID-terraform-state/default.tfstate#VERSION ./terraform.tfstate

# Apply to rollback
terraform apply
```

### Function Rollback

Cloud Functions Gen2 are Cloud Run services:

```bash
# List revisions
gcloud run revisions list --service=api-gateway --region=REGION

# Rollback to specific revision
gcloud run services update-traffic api-gateway --to-revisions=REVISION_NAME=100 --region=REGION
```

### Firebase Hosting Rollback

```bash
# List release history
firebase hosting:channel:list

# Clone previous version to live
firebase hosting:clone PROJECT_ID:PREVIOUS_RELEASE_ID PROJECT_ID:live
```

---

## Environment-Specific Notes

### Development

- Use `cors_allowed_origins = "*"` for easier testing
- Consider lower function memory/instance limits
- Firebase emulators can be used for local development

### Production

- Set specific CORS origins: `cors_allowed_origins = "https://your-domain.web.app"`
- Enable Firebase App Check (see firebase.tf comments)
- Configure alerting and monitoring
- Use production-grade secret management
- Consider VPC Service Controls

---

## Troubleshooting

See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for common issues and solutions.
