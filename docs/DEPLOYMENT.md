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
- ✅ API key ready (HIBP)
- ✅ Verification Checklist completed

**Once you have completed the Verification Checklist in PREREQUISITES.md, proceed to Initial Setup below.**

---

## Initial Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd <repository-directory>
```

**Note**: Replace `<repository-directory>` with the actual directory name created by the clone command. Throughout this guide, we'll refer to this as the "repository root" directory. If you already have the repository cloned, navigate to it now.

### 2. Set Environment Variables (Optional)

**Note**: The `.env` file is optional. Scripts accept command-line arguments and will work without it.

**Option A: Use command-line arguments (Recommended)**
- Scripts accept project ID and region as arguments
- Example: `./scripts/validate-deployment.sh PROJECT_ID REGION ENVIRONMENT`
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
./scripts/validate-deployment.sh "$GCP_PROJECT" "$GCP_REGION" dev
```

**Note**: Terraform uses `terraform.tfvars`, not `.env`. The `.env` file is only for shell convenience.

### 3. Verify Prerequisites Are Complete

Before proceeding, verify that you've completed authentication (this should already be done if you followed PREREQUISITES.md):

**Recommended: Use the authentication check script**

```bash
# Run authentication check script (validates all auth requirements)
./scripts/check-terraform-auth.sh YOUR_PROJECT_ID
```

This script verifies:
- Project is set correctly
- User is authenticated
- Application Default Credentials exist
- Quota project is configured
- Terraform state bucket is accessible

If the script passes, you're ready to proceed. If it fails, follow the instructions it provides.

**Manual verification (alternative to script):**

```bash
# Verify gcloud authentication
gcloud auth list

# Verify application default credentials
gcloud auth application-default print-access-token > /dev/null && echo "✓ Application default credentials configured" || echo "✗ Run: gcloud auth application-default login"

# Verify quota project is set (required for Identity Platform API)
# IMPORTANT: You must set this manually - it's required before terraform apply
gcloud auth application-default set-quota-project YOUR_PROJECT_ID

# Verify OAuth client project matches target project (required for Identity Platform API)
# This prevents "quota project" errors due to OAuth client mismatch
PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
ADC_CLIENT_ID=$(cat ~/.config/gcloud/application_default_credentials.json | grep -o '"client_id": "[^"]*"' | cut -d'"' -f4 | cut -d'-' -f1)
if [ "$ADC_CLIENT_ID" = "$PROJECT_NUMBER" ]; then
  echo "✓ OAuth client project matches target project"
else
  echo "⚠️  WARNING: OAuth client project ($ADC_CLIENT_ID) does not match target project ($PROJECT_NUMBER)"
  echo "   This will cause Identity Platform API to fail during terraform apply."
  echo "   Try re-authenticating ADC (may not work if OAuth client is org-level):"
  echo "   gcloud auth application-default login"
  echo "   gcloud auth application-default set-quota-project PROJECT_ID"
  echo "   If this doesn't fix it, configure Identity Platform manually via Firebase Console post-deployment."
fi

# Verify Firebase authentication
firebase projects:list > /dev/null && echo "✓ Firebase authenticated" || echo "✗ Run: firebase login"

# Verify project is set
gcloud config get-value project
```

**Note**: The quota project must be set in Application Default Credentials using `gcloud auth application-default set-quota-project`. This is a prerequisite for the Identity Platform API (`identitytoolkit.googleapis.com`). The `billing_project` parameter in Terraform provider blocks is configured but does not set the ADC quota project - it must be set separately using the `gcloud` command above.

**Important**: If the OAuth client project does not match your target project, the Identity Platform API will reject requests even if the quota project is set correctly. This can happen when:
1. ADC was created with credentials from a different project (re-authenticating ADC may fix this)
2. The OAuth client belongs to an organization-level or account-level project (cannot be changed by re-authenticating)

If re-authenticating ADC doesn't fix the OAuth client mismatch (common in organizational GCP setups), the `google_identity_platform_config` resource will fail during `terraform apply`. This is expected and non-blocking - all other resources will deploy successfully. You can configure Identity Platform manually via Firebase Console post-deployment. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for details and workarounds.

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
| `cors_allowed_origins` | CORS origins (required) | `*` (explicit dev choice) or `https://PROJECT-skiptrace.web.app,https://PROJECT-origination.web.app` (prod) |

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

**Working Directory**: Repository root

Run the preparation script to copy shared utilities:

```bash
./scripts/prepare-functions.sh
```

**Note**: This script copies shared Python utilities (`retry_utils.py`, `domain_utils.py`, etc.) to function directories that need them. If you're not in the repository root directory, navigate there first.

### Step 1b: Prepare Frontend Files

**Working Directory**: Repository root

Run the frontend preparation script to copy shared JS/CSS and process HTML templates:

```bash
./scripts/prepare-frontend.sh
```

**What this does**:
- Copies shared JavaScript modules to each platform's `public/` directory:
  - `app-core.js`, `chat-core.js`, `platform-config.js`, `report-renderer.js`, `results.js`, `shared-utils.js`
  - `address-verification.js` (only for platforms with that feature enabled in `platform.json`)
- Copies shared CSS files to each platform's `public/` directory:
  - `styles.css`, `shared.css`, `chat.css`, `results.css`
- Processes HTML templates from `frontend/shared/templates/` using platform-specific configuration from `platform.json`

**Note**: This must be run before Step 6 (Deploy Firebase Hosting). It can be run at any time before deploying frontends. The script requires `jq` (install with `brew install jq`).

### Step 2: Initialize Terraform

**Before running Terraform, verify authentication is correct:**

```bash
# Recommended: Run authentication check script
./scripts/check-terraform-auth.sh YOUR_PROJECT_ID

# If authentication check passes, proceed with Terraform
cd terraform/environments/dev  # or prod

# Initialize Terraform
terraform init

# Review the plan
terraform plan
```

**Important**: If the authentication check fails, follow the instructions it provides before proceeding with Terraform commands. This prevents `oauth2: "invalid_grant"` errors. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md#error-oauth2-invalid_grant---reauth-related-error) for details.

### Step 3: Apply Terraform

```bash
# Apply the configuration
terraform apply

# Type 'yes' when prompted
```

**Note**: If you encounter errors about Eventarc or Workflows service agents not existing, wait 5-10 minutes for service agents to propagate after API enablement, then run `terraform apply` again. The Identity Platform Config resource may fail due to OAuth client mismatch (documented in PREREQUISITES.md) - this is expected and non-blocking if you configure authentication manually via Firebase Console.

This will:
- Enable required GCP APIs
- Create Secret Manager secrets
- Deploy Cloud Functions (functions automatically wait for required IAM permissions)
- Create Cloud Workflows
- Configure Firebase (auth, hosting sites, web apps)
- Generate `firebase-config.json` for frontends
- Generate `config.js` for Chrome extension

**Note**: The Terraform configuration includes proper dependencies to ensure functions wait for Compute SA IAM permissions before building. This prevents the "Build failed" errors that can occur when Cloud Functions Gen2 builds start before IAM permissions are granted. If you still encounter build failures, see [TROUBLESHOOTING.md](./TROUBLESHOOTING.md#error-cloud-build-service-account-missing-permissions) for IAM propagation timing issues.

### Step 3b: Verify Generated Configuration Files

**Working Directory**: `terraform/environments/dev` (or `prod`)

After `terraform apply` completes, verify that configuration files were generated:

```bash
# Check API Gateway URL (you'll need this)
terraform output api_gateway_url

# Navigate to repository root to verify generated files
cd ../../..

# Verify firebase-config.json files exist
ls -la frontend/skiptrace/public/firebase-config.json
ls -la frontend/origination/public/firebase-config.json

# Verify Chrome extension config exists
ls -la chrome-extension/config.js
```

**Important**: If these files don't exist, the frontends won't work. Check Terraform outputs for any errors.

### Step 4: Add Secret Values

Terraform creates empty secrets. Add the actual values:

```bash
# Set your project ID (or add --project=PROJECT_ID to each command)
export PROJECT_ID="your-project-id"

# Add HIBP API key
echo -n "YOUR_API_KEY" | gcloud secrets versions add HIBP_API_KEY --data-file=- --project=$PROJECT_ID
```

**Verify secret was added**:

```bash
gcloud secrets list --project=$PROJECT_ID --filter="name~HIBP"
```

### Step 5: Configure Firebase for Hosting

**Working Directory**: Repository root

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
# Navigate from skiptrace to origination
cd ../origination
firebase use PROJECT_ID
cat .firebaserc
```

**Note**: The `.firebaserc` file should reference your project ID. If it doesn't exist or is incorrect, Firebase CLI will prompt you during deployment.

### Step 5b: Configure Firebase Hosting Targets

**Working Directory**: `frontend/origination` (from previous step)

Before deploying, you must configure the hosting targets to map the target names in `firebase.json` to the actual site IDs created by Terraform:

```bash
# Navigate to Skip Trace frontend
cd ../skiptrace
firebase target:apply hosting skiptrace PROJECT_ID-skiptrace

# Navigate to Origination frontend
cd ../origination
firebase target:apply hosting origination PROJECT_ID-origination
```

**Note**: The `firebase.json` files use target names (`skiptrace` and `origination`), but Terraform creates sites with IDs like `PROJECT_ID-skiptrace`. The `target:apply` command maps the target names to the actual site IDs.

### Step 6: Deploy Firebase Hosting

**Working Directory**: `frontend/origination` (from previous step)

```bash
# Deploy Skip Trace frontend
cd ../skiptrace
firebase deploy --only hosting

# Deploy Origination frontend
cd ../origination
firebase deploy --only hosting
```

**Note**: After configuring targets with `firebase target:apply`, use `firebase deploy --only hosting` (without the site ID) - Firebase will use the target mapping from `.firebaserc`.

### Step 6b: Deploy Firestore Security Rules

**Working Directory**: `frontend/origination` (from previous step)

Deploy Firestore security rules to enable chat history persistence and access control:

```bash
# Deploy Skip Trace Firestore rules
cd ../skiptrace
firebase deploy --only firestore:rules

# Deploy Origination Firestore rules
cd ../origination
firebase deploy --only firestore:rules
```

**Note**: Firestore rules are required for chat history to persist across page refreshes. Without deployed rules, chat messages cannot be saved or loaded. See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for more details on Firestore rules.

### Step 7: Validate Deployment

**Working Directory**: `frontend/origination` (from previous step)

Run the validation script from repository root:

```bash
# Navigate to repository root
cd ../..

# Run validation script
./scripts/validate-deployment.sh PROJECT_ID REGION ENVIRONMENT
```

Run smoke tests:

```bash
./scripts/smoke-test.sh PROJECT_ID REGION
```

---

## Post-Deployment

### Verify Endpoints

**Working Directory**: Repository root (from previous step)

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

### Configure Chrome Extension (Future Enhancement)

**Status**: The Chrome extension is not yet ready for production deployment. It will be enabled once the platform is stable in CanCap's environment.

**What it does**: The Chrome extension is a quality-of-life feature for both teams:
- **Origination team**: Extracts borrower data from CanCap's loan origination system and sends it to the Origination Intelligence frontend with one click
- **Skip tracing team**: Extracts borrower data from CanCap's skip trace system and sends it to the Skip Trace Intelligence frontend with one click

This eliminates manual copy/paste between CanCap's existing systems and the new intelligence platforms.

**Timeline**: This will be configured after:
1. The core platform is deployed and validated in CanCap's GCP environment
2. Initial testing with both teams is complete
3. Integration requirements with CanCap's existing systems are finalized

For now, both teams can access their respective platforms directly via the web interfaces and manually enter borrower information:
- Skip Trace Intelligence: `https://PROJECT_ID-skiptrace.web.app`
- Origination Intelligence: `https://PROJECT_ID-origination.web.app`

**Technical Note**: The extension configuration file (`chrome-extension/config.js`) is automatically generated by Terraform, but the extension itself should not be loaded in Chrome until explicitly approved for production use.

### Set Up Monitoring (Recommended)

1. **Cloud Monitoring**: Set up dashboards for function invocations, latency, errors
2. **Cloud Logging**: Configure log-based alerts for errors
3. **Uptime Checks**: Create uptime checks for the API Gateway

---

## Updating Deployments

### Update Functions

```bash
# Make code changes, then:
./scripts/prepare-functions.sh  # Copy shared Python utils to function dirs
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
./scripts/prepare-frontend.sh  # Copy shared JS/CSS/templates to platform dirs
cd frontend/skiptrace
firebase deploy --only hosting

cd ../origination
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

- Use `cors_allowed_origins = "*"` only as an explicit development choice
- Consider lower function memory/instance limits
- Firebase emulators can be used for local development

### Production

- Set specific CORS origins (no wildcard): `cors_allowed_origins = "https://your-domain.web.app"`
- Enable Firebase App Check (see firebase.tf comments)
- Configure alerting and monitoring
- Use production-grade secret management
- Consider VPC Service Controls

---

## Troubleshooting

See [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) for common issues and solutions.
