# Prerequisites

This document outlines all prerequisites needed before deploying the Skip Trace & Origination Intelligence Platform.

## Table of Contents

1. [GCP Setup](#gcp-setup)
2. [Local Tools](#local-tools)
3. [API Keys & Services](#api-keys--services)
4. [Verification Checklist](#verification-checklist)

---

## GCP Setup

**Important**: Before starting GCP Setup, ensure you have installed and authenticated the required local tools. See [Local Tools](#local-tools) section below. You'll need `gcloud` CLI installed to follow the CLI options in this section.

### 1. Create a GCP Project

> ⚠ **Project ID length constraint — choose a project ID ≤ 18 characters.**
> Firebase Hosting site IDs (`${project_id}-skiptrace` and
> `${project_id}-origination`) are capped at 30 characters. The `-origination`
> suffix consumes 12 chars, so the project ID itself must be ≤ 18 chars or
> `terraform apply` will fail late when creating the hosting sites. `terraform
> plan` does **not** catch this — the API call does, after the project,
> state bucket, OAuth client, and most resources are already created.

**Option A: Using Google Cloud Console**

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select an existing one
3. **Important**: Note your Project ID (not Project Name)

**Option B: Using gcloud CLI**

```bash
# Create a new project
gcloud projects create YOUR_PROJECT_ID --name="Your Project Name"

# Or select an existing project
gcloud config set project YOUR_PROJECT_ID

# Verify the project ID
gcloud config get-value project
```

**Note**: After running `gcloud config set project`, you may see a warning: *"Your active project does not match the quota project in your local Application Default Credentials file."* This is expected and harmless at this stage — it will be resolved in the [Local Tools](#local-tools) section when you configure Application Default Credentials.

**Important**: Note your Project ID (not Project Name) - you'll need this throughout the deployment.

### 2. Enable Billing

**Option A: Using Google Cloud Console**

1. Navigate to Billing in GCP Console
2. Link a billing account to your project

**Option B: Using gcloud CLI**

```bash
# First, list your billing accounts (if you don't know the account ID)
gcloud billing accounts list

# Link a billing account to your project
gcloud billing projects link YOUR_PROJECT_ID --billing-account=BILLING_ACCOUNT_ID

# Verify billing is enabled
gcloud billing projects describe YOUR_PROJECT_ID
```

### 3. Set Up Owner/Editor Access

Ensure your account has the following roles:
- `roles/owner` OR these specific roles:
  - `roles/editor`
  - `roles/iam.securityAdmin`
  - `roles/secretmanager.admin`
  - `roles/firebase.admin`

**How to check your current roles** (after creating project):
```bash
PROJECT_ID="your-project-id"
gcloud projects get-iam-policy $PROJECT_ID --flatten="bindings[].members" \
  --filter="bindings.members:user:$(gcloud config get-value account)"
```

**How to grant roles** (if you're project owner):
- Go to [IAM & Admin](https://console.cloud.google.com/iam-admin/iam) in GCP Console
- Find your account and edit permissions
- Add the required roles listed above

### 4. Enable Required APIs

**Note**: Terraform automatically enables most APIs during deployment, so this section is *mostly* optional.

**However**, the following APIs **must be enabled before Terraform runs**, because steps below in this file (still in PREREQUISITES) call them directly:

- `secretmanager.googleapis.com` — required by the OAuth-secret step in § 3 below.
- `iam.googleapis.com` / `cloudresourcemanager.googleapis.com` — required by the IAM role check earlier in this section.

The pragmatic choice is to just enable the full set now in **two batches** (`gcloud services enable` supports max 20 services per command). It costs nothing extra and avoids hitting `SERVICE_DISABLED` errors mid-flow:

```bash
# Batch 1 (20 services)
gcloud services enable \
  cloudfunctions.googleapis.com \
  run.googleapis.com \
  workflows.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  eventarc.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  drive.googleapis.com \
  customsearch.googleapis.com \
  discoveryengine.googleapis.com \
  firebase.googleapis.com \
  firebasehosting.googleapis.com \
  identitytoolkit.googleapis.com \
  firebaseappcheck.googleapis.com \
  recaptchaenterprise.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com

# Batch 2 (remaining service)
gcloud services enable \
  serviceusage.googleapis.com
```

### 5. Create Terraform State Bucket

```bash
# Replace with your project ID and preferred region
PROJECT_ID="your-project-id"
REGION="northamerica-northeast1"

# Create bucket
gsutil mb -l $REGION -p $PROJECT_ID gs://${PROJECT_ID}-terraform-state

# Enable versioning for state recovery
# Note: on a freshly-created project, this command can 403 with
# "storage.buckets.update denied" for ~30 seconds after `gsutil mb` while
# bucket-level IAM propagates. If you hit that, wait 30s and re-run this line.
gsutil versioning set on gs://${PROJECT_ID}-terraform-state
```

---

## Local Tools

### Windows Setup (WSL2 or Google Cloud Shell)

If you are deploying from Windows, you have two options to avoid line-ending and compatibility issues:

**Option A: Google Cloud Shell (Easiest)**
Use [Google Cloud Shell](https://console.cloud.google.com) directly in your browser. It comes with `gcloud`, `terraform`, `jq`, and `npm` pre-installed. You only need to install the Firebase CLI (`npm install -g firebase-tools`) and clone this repository. Note: When logging into Firebase in Cloud Shell, you must use `firebase login --no-localhost`.

After you configure Application Default Credentials (ADC) below, read **[Google Cloud Shell: ADC file location](#google-cloud-shell-adc-file-location)**—Cloud Shell may store ADC under `$CLOUDSDK_CONFIG` (often `/tmp/...`), while this guide’s checks and `scripts/check-terraform-auth.sh` expect `~/.config/gcloud/application_default_credentials.json`.

**Option B: WSL2 (Ubuntu)**
Use **WSL2 (Ubuntu)** and run all commands from the WSL terminal. This repository relies on Bash scripts (`.sh`) and Unix shell tools.

1. Install WSL2 and Ubuntu:
   - In PowerShell (as Administrator): `wsl --install -d Ubuntu`
2. Open Ubuntu and install required tools in WSL:
   - `gcloud` CLI
   - `terraform`
   - `jq`
   - Node.js + npm (for Firebase CLI)
   - Firebase CLI: `npm install -g firebase-tools`
3. Clone the repository in WSL and run all commands from Bash.

**Important (Windows)**:
- Do **not** run deployment scripts from PowerShell/CMD.
- Keep shell scripts with LF line endings (CRLF can break `.sh` scripts).
- Prefer working in the WSL filesystem for best reliability.

### 1. Google Cloud SDK (gcloud)

**Install:**

```bash
# macOS (Homebrew)
brew install google-cloud-sdk

# Ubuntu/Debian
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get install google-cloud-cli

# Windows
# Download from: https://cloud.google.com/sdk/docs/install
```

**Configure:**

```bash
# Initialize and authenticate (only needed on a fresh gcloud install;
# skip if `gcloud auth list` already shows an active account)
gcloud init

# Set default project
# IMPORTANT: Do this BEFORE authenticating ADC to ensure OAuth client matches your project
gcloud config set project YOUR_PROJECT_ID

# Set default region
# Note: use --quiet to avoid a prompt to enable the Compute Engine API (not required).
# You will see "WARNING: Property validation for compute/region was skipped." —
# that is the expected counterpart of --quiet and is safe to ignore here.
gcloud config set compute/region northamerica-northeast1 --quiet

# Authenticate application default credentials (for Terraform)
# IMPORTANT: Ensure the correct project is active before running this command
# Note: The OAuth client used may belong to your organization/account level,
# not necessarily the active project. This can cause Identity Platform API
# issues if the OAuth client project doesn't match your target project.
gcloud auth application-default login

# Set quota project (required for Identity Platform API)
gcloud auth application-default set-quota-project YOUR_PROJECT_ID

# Verify OAuth client project matches your project (prevents Identity Platform API errors)
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
ADC_CLIENT_ID=$(cat ~/.config/gcloud/application_default_credentials.json | grep -o '"client_id": "[^"]*"' | cut -d'"' -f4 | cut -d'-' -f1)
if [ "$ADC_CLIENT_ID" = "$PROJECT_NUMBER" ]; then
  echo "✓ OAuth client project matches target project"
else
  echo "⚠ NOTE: OAuth client project does not match target project."
  echo "   This is expected when ADC uses gcloud's shared client (client ID prefix"
  echo "   764086051850) — which is the default on most setups. For projects where"
  echo "   you have created a project-bound Web OAuth client in Console (used by"
  echo "   Firebase Auth, not by ADC), terraform apply will still succeed."
  echo "   If terraform apply fails on google_identity_platform_config, see"
  echo "   TROUBLESHOOTING.md § Unable to initialize authentication for workarounds."
fi
```

**Note**: The order matters — always set the project before authenticating ADC (`gcloud auth application-default login`). On most setups the OAuth client that ADC uses is gcloud's well-known shared client (ID prefix `764086051850`), which is **not** project-bound; that mismatch is normal and does not cause failures by itself. The corner case to watch is **organization-managed GCP setups** where an org-level OAuth client AND an org policy together can block the Identity Platform API during `terraform apply`. If you encounter that specific failure, see [TROUBLESHOOTING.md § "Unable to initialize authentication"](./TROUBLESHOOTING.md#error-unable-to-initialize-authentication-frontend-authentication-error) for the Firebase Console workaround.

#### Google Cloud Shell: ADC file location

In [Google Cloud Shell](https://console.cloud.google.com), the environment variable **`CLOUDSDK_CONFIG`** is often set to a directory under **`/tmp/...`**. `gcloud` then reads and writes SDK state—including **`application_default_credentials.json`**—under **`$CLOUDSDK_CONFIG`**, not necessarily under **`$HOME/.config/gcloud/`**.

This repository’s verification snippets (above) and **`./scripts/check-terraform-auth.sh`** assume ADC exists at:

`$HOME/.config/gcloud/application_default_credentials.json`

**After** `gcloud auth application-default login` and `gcloud auth application-default set-quota-project YOUR_PROJECT_ID`, check both locations:

```bash
ls -la ~/.config/gcloud/application_default_credentials.json 2>/dev/null || echo "missing: ~/.config/gcloud/application_default_credentials.json"
echo "CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-<unset>}"
ls -la "${CLOUDSDK_CONFIG}/application_default_credentials.json" 2>/dev/null || echo "missing: \$CLOUDSDK_CONFIG/application_default_credentials.json"
```

If the file exists only under **`$CLOUDSDK_CONFIG`**, copy it once to the default path (safe if the destination file is missing or you intend to replace it with the same login):

```bash
mkdir -p ~/.config/gcloud
cp "${CLOUDSDK_CONFIG}/application_default_credentials.json" ~/.config/gcloud/application_default_credentials.json
chmod 600 ~/.config/gcloud/application_default_credentials.json
```

Re-apply the quota project so the JSON on disk stays consistent with what Identity Platform expects:

```bash
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Verify:

```bash
jq -r '.quota_project_id' ~/.config/gcloud/application_default_credentials.json
```

**OAuth client check (`✗` mismatch) in Cloud Shell:** The snippet that compares `ADC_CLIENT_ID` to the project number often prints **`✗`** on Cloud Shell because the SDK uses a **shared** OAuth client. That is separate from the **`CLOUDSDK_CONFIG`** path issue. If `quota_project_id` is correct and you still hit Identity Platform errors during `terraform apply`, follow [TROUBLESHOOTING.md](./TROUBLESHOOTING.md).

**Pasting commands:** Run **one command per line**. Pasting multiple lines into a prompt that already contains text can merge commands and corrupt input; open a **new Cloud Shell tab** if your prompt looks wrong.

### 2. Terraform

**Install:**

```bash
# macOS (Homebrew)
brew install terraform

# Ubuntu/Debian
wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform

# Verify (must be >= 1.14.0; 1.14.8 tested in Cloud Shell)
terraform version
```

### 3. Firebase CLI

**Install:**

```bash
# Using npm
npm install -g firebase-tools

# Verify
firebase --version

# Authenticate
firebase login

# NOTE: If using Google Cloud Shell or a headless environment, use:
# firebase login --no-localhost
```

### 4. jq

Required by deployment helper scripts such as `scripts/check-terraform-auth.sh` and `scripts/prepare-frontend.sh`.

```bash
# macOS (Homebrew)
brew install jq

# Ubuntu/Debian
sudo apt update && sudo apt install -y jq

# Verify
jq --version
```

### 5. Python (Optional, for local development)

```bash
# Verify Python 3.11+
python3 --version

# Create virtual environment (optional)
python3 -m venv venv
source venv/bin/activate
```

---

## API Keys & Services

### 1. Have I Been Pwned (HIBP) API

Required for breach detection functionality.

1. Go to [Have I Been Pwned API](https://haveibeenpwned.com/API/Key)
2. Purchase an API key (supports the service)
3. **Store the API key securely** - you'll add it to Secret Manager in the 'Add Secret Values' step in DEPLOYMENT.md

**Note**: Keep your API key in a secure location temporarily. You'll use it when adding secrets after Terraform deployment.

### 2. Google Vertex AI

Required for AI-powered analysis.

1. Vertex AI is enabled automatically via Terraform
2. Ensure your project has quota for Gemini models
3. Check quota: [Vertex AI Console > Quotas](https://console.cloud.google.com/vertex-ai/quota)
   - Look for "Generative Language API" quotas
   - Default quotas are usually sufficient for development/testing

### 3. Google Workspace OAuth Client (Required for SSO)

Required when `enable_sso=true` (default in this deployment model).

For a brand-new project, complete these Console steps first:

1. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview?project=YOUR_PROJECT_ID)
2. Click **Get started** in Google Auth Platform and complete app setup (this is the OAuth consent/app configuration step in the current Console UI). Internal is typical for Workspace-only deployments.
3. In Google Auth Platform, choose **Create OAuth client** (you may also reach the same flow from [APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials?project=YOUR_PROJECT_ID)):
   - Type: **Web application**
   - Name: e.g. `workspace-sso`
   - If prompted for redirect URIs, add:
     - `https://YOUR_PROJECT_ID.firebaseapp.com/__/auth/handler`
4. Copy and store:
   - OAuth **Client ID** (used in `terraform.tfvars`)
   - OAuth **Client Secret** (stored in Secret Manager)
   - **Do not continue until both values are captured**

Then configure it for Terraform:

5. Save the client ID for `google_workspace_oauth_client_id` in `terraform.tfvars`
6. Add the client secret to Secret Manager **before** Terraform plan/apply:

```bash
PROJECT_ID="your-project-id"

# Create secret once (ignore error if already exists)
gcloud secrets create workspace-oauth-client-secret \
  --replication-policy=automatic \
  --project=$PROJECT_ID

# Add secret value
echo -n "YOUR_GOOGLE_OAUTH_CLIENT_SECRET" | \
  gcloud secrets versions add workspace-oauth-client-secret \
  --data-file=- \
  --project=$PROJECT_ID

# Verify at least one version exists
gcloud secrets versions list workspace-oauth-client-secret --project=$PROJECT_ID
```

**Why this is required pre-Terraform**: `terraform plan` reads this secret via a data source. If the secret is missing or has no versions, plan/apply fails.

### 4. Google Drive API (Post-Deployment Setup)

**Note**: Drive configuration is **not** part of prerequisites. After deployment, you'll configure Drive access through the web interface.

1. Drive API is enabled automatically via Terraform
2. A service account (`functions-sa@PROJECT_ID.iam.gserviceaccount.com`) is created automatically
3. **After deployment completes**, follow the setup wizard in the web application's "Setup & Help" tab to:
   - Create a folder in a Google Shared Drive
   - Share it with the service account
   - Configure the folder URL

**Why this is post-deployment**: Google Drive access requires manual folder sharing with the service account, which can only be done after the service account is created by Terraform.

---

## Verification Checklist

Run through this checklist before deployment. **Verify each item with the commands provided.**

### GCP Setup
- [ ] **GCP Project created with billing enabled**
  ```bash
  # Replace with your project ID
  PROJECT_ID="your-project-id"
  gcloud billing projects describe $PROJECT_ID
  # Should show: billingEnabled: true
  ```

- [ ] **Project ID noted (not Project Name)**
  ```bash
  # Store your project ID (you'll need this throughout deployment)
  export PROJECT_ID="your-project-id"
  echo "Project ID: $PROJECT_ID"
  ```
  **Note**: Keep this project ID available - you'll need it for all deployment commands.

- [ ] **Terraform state bucket created with versioning**
  ```bash
  # Verify bucket exists (empty output is expected on a fresh bucket — the
  # command succeeds; it just has nothing to list yet)
  gsutil ls gs://${PROJECT_ID}-terraform-state
  
  # Verify versioning is enabled
  gsutil versioning get gs://${PROJECT_ID}-terraform-state
  # Should output: gs://PROJECT_ID-terraform-state: Enabled
  ```

- [ ] **Account has required IAM roles**
  ```bash
  # Check your account's roles on the project
  gcloud projects get-iam-policy $PROJECT_ID --flatten="bindings[].members" \
    --filter="bindings.members:user:$(gcloud config get-value account)"
  ```
  **Required roles**: `roles/owner` OR `roles/editor`, `roles/iam.securityAdmin`, `roles/secretmanager.admin`, `roles/firebase.admin`

### Local Tools
- [ ] **gcloud CLI installed and authenticated**
  ```bash
  gcloud version
  # Should show gcloud CLI version
  
  gcloud auth list
  # Should show at least one account marked ACTIVE
  ```

- [ ] **Terraform >= 1.14.0 installed**
  ```bash
  terraform version
  # Should show version >= 1.14.0
  ```

- [ ] **Firebase CLI installed and authenticated**
  ```bash
  firebase --version
  # Should show Firebase CLI version
  
  firebase projects:list
  # Should list your projects (verifies authentication)
  ```

- [ ] **`gcloud auth application-default login` completed**
  ```bash
  gcloud auth application-default print-access-token > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "✓ Application default credentials configured"
  else
    echo "✗ Run: gcloud auth application-default login"
  fi
  ```

- [ ] **Quota project set for Application Default Credentials (required for Identity Platform API)**
  ```bash
  # Set quota project (replace with your project ID)
  gcloud auth application-default set-quota-project YOUR_PROJECT_ID
  
  # Verify it's set
  gcloud auth application-default print-access-token --project=YOUR_PROJECT_ID > /dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "✓ Quota project configured"
  else
    echo "✗ Run: gcloud auth application-default set-quota-project YOUR_PROJECT_ID"
  fi
  ```

### API Keys Ready
- [ ] **HIBP API key purchased**
  ```bash
  # Same as above - store securely for later use
  echo "HIBP API key purchased and stored securely"
  ```

- [ ] **Google Workspace OAuth client configured for SSO**
  ```bash
  PROJECT_ID="your-project-id"
  gcloud secrets versions list workspace-oauth-client-secret --project=$PROJECT_ID
  # Should show at least one ENABLED version
  ```
- [ ] **Google Workspace OAuth client ID captured for Terraform**
  ```bash
  echo "Set google_workspace_oauth_client_id in terraform.tfvars"
  ```

---

## Quick Start Commands

**Note**: These commands assume you've cloned the repository and are in the repository root directory.

```bash
# 0. Navigate to repository (if not already there)
# Example: cd /path/to/skip-trace-origination

# 1. Verify tools
gcloud version
terraform version
firebase --version

# 2. Authenticate
gcloud auth login
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID  # Required for Identity Platform API
firebase login  # Use 'firebase login --no-localhost' in Cloud Shell

# 3. Set project
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# 4. Verify project access
gcloud projects describe $PROJECT_ID

# 5. Create state bucket
gsutil mb -l northamerica-northeast1 -p $PROJECT_ID gs://${PROJECT_ID}-terraform-state
gsutil versioning set on gs://${PROJECT_ID}-terraform-state

# 6. Navigate to Terraform environment and initialize
cd terraform/environments/dev
terraform init
```

---

## Next Steps

Once all prerequisites are met, proceed to [DEPLOYMENT.md](./DEPLOYMENT.md).
