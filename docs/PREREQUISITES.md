# Prerequisites

This document outlines all prerequisites needed before deploying the Skip Trace & Origination Intelligence Platform.

## Table of Contents

1. [GCP Setup](#gcp-setup)
2. [Local Tools](#local-tools)
3. [API Keys & Services](#api-keys--services)
4. [Programmable Search Engines](#programmable-search-engines)
5. [Verification Checklist](#verification-checklist)

---

## GCP Setup

**Important**: Before starting GCP Setup, ensure you have installed and authenticated the required local tools. See [Local Tools](#local-tools) section below. You'll need `gcloud` CLI installed to follow the CLI options in this section.

### 1. Create a GCP Project

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

**Note**: Terraform automatically enables these APIs during deployment. You don't need to enable them manually unless you want to verify they're enabled beforehand.

If you want to enable them manually (optional):

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  workflows.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  run.googleapis.com \
  eventarc.googleapis.com \
  cloudbuild.googleapis.com \
  drive.googleapis.com \
  customsearch.googleapis.com \
  firebase.googleapis.com \
  firebasehosting.googleapis.com \
  identitytoolkit.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

### 5. Create Terraform State Bucket

```bash
# Replace with your project ID and preferred region
PROJECT_ID="your-project-id"
REGION="northamerica-northeast1"

# Create bucket
gsutil mb -l $REGION -p $PROJECT_ID gs://${PROJECT_ID}-terraform-state

# Enable versioning for state recovery
gsutil versioning set on gs://${PROJECT_ID}-terraform-state
```

---

## Local Tools

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
# Initialize and authenticate
gcloud init

# Set default project
gcloud config set project YOUR_PROJECT_ID

# Set default region
gcloud config set compute/region northamerica-northeast1

# Authenticate application default credentials (for Terraform)
gcloud auth application-default login

# Set quota project (required for Identity Platform API)
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

### 2. Terraform

**Install:**

```bash
# macOS (Homebrew)
brew install terraform

# Ubuntu/Debian
wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform

# Verify (must be >= 1.5.0)
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
```

### 4. Python (Optional, for local development)

```bash
# Verify Python 3.11+
python3 --version

# Create virtual environment (optional)
python3 -m venv venv
source venv/bin/activate
```

---

## API Keys & Services

### 1. Google Custom Search API

Required for web search functionality.

1. Go to [Google Cloud Console > APIs & Services](https://console.cloud.google.com/apis/credentials)
2. Click "Create Credentials" > "API Key"
3. Restrict the key to "Custom Search API" only
4. **Store the API key securely** - you'll add it to Secret Manager in DEPLOYMENT.md Step 4

**Note**: Keep your API key in a secure location temporarily (e.g., password manager, encrypted file). You'll use it when adding secrets after Terraform deployment.

### 2. Have I Been Pwned (HIBP) API

Required for breach detection functionality.

1. Go to [Have I Been Pwned API](https://haveibeenpwned.com/API/Key)
2. Purchase an API key (supports the service)
3. **Store the API key securely** - you'll add it to Secret Manager in DEPLOYMENT.md Step 4

**Note**: Keep your API key in a secure location temporarily. You'll use it when adding secrets after Terraform deployment.

### 3. Google Vertex AI

Required for AI-powered analysis.

1. Vertex AI is enabled automatically via Terraform
2. Ensure your project has quota for Gemini models
3. Check quota: [Vertex AI Console > Quotas](https://console.cloud.google.com/vertex-ai/quota)
   - Look for "Generative Language API" quotas
   - Default quotas are usually sufficient for development/testing

### 4. Google Drive API (Optional)

Required for report export to Google Drive.

1. Drive API is enabled automatically via Terraform
2. Service account must have access to target folders

---

## Programmable Search Engines

The platform uses several Google Programmable Search Engines (PSE) for specialized searches.

### Required PSEs

**Total**: 6 unique PSEs (7 environment variables - RECALL_PSE_CX_2 reuses PRECISION_PSE_CX)

**IMPORTANT**: Follow the detailed step-by-step guides in `pse-configurations/` directory. The table below is a quick reference only.

| PSE Name | Purpose | Environment Variable |
|----------|---------|---------------------|
| Base Search | General web search | `GOOGLE_SEARCH_CX` |
| Precision PSE | Social platform search | `PRECISION_PSE_CX` (also used for `RECALL_PSE_CX_2`) |
| Recall PSE | Lifestyle/hobby sites | `RECALL_PSE_CX` |
| LinkedIn PSE | LinkedIn profiles only | `LINKEDIN_PSE_CX` |
| Reviews PSE | Business reviews | `REVIEWS_PSE_CX` |
| Complaints PSE | Business complaints | `COMPLAINTS_PSE_CX` |

**Note**: `RECALL_PSE_CX_2` uses the **same CX value** as `PRECISION_PSE_CX` (they're the same PSE).

### Creating PSEs

**Follow the step-by-step guides in `pse-configurations/` directory:**

- [Base Search Engine](pse-configurations/base-search.md) - `GOOGLE_SEARCH_CX`
- [Precision Search Engine](pse-configurations/precision-search.md) - `PRECISION_PSE_CX` (also `RECALL_PSE_CX_2`)
- [Recall Search Engine](pse-configurations/recall-search-1.md) - `RECALL_PSE_CX`
- [LinkedIn Search Engine](pse-configurations/linkedin-search.md) - `LINKEDIN_PSE_CX`
- [Reviews Search Engine](pse-configurations/reviews-search.md) - `REVIEWS_PSE_CX`
- [Complaints Search Engine](pse-configurations/complaints-search.md) - `COMPLAINTS_PSE_CX`

**After creating each PSE**, copy the Search Engine ID (CX) and store it securely. You'll add all CX values to Secret Manager in DEPLOYMENT.md Step 4.

---

## Verification Checklist

Run through this checklist before deployment. **Verify each item with the commands provided.**

### GCP Setup
- [ ] **GCP Project created with billing enabled**
  ```bash
  # Replace with your project ID
  PROJECT_ID="your-project-id"
  gcloud projects describe $PROJECT_ID
  # Verify billing account is linked (check output)
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
  # Verify bucket exists
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

- [ ] **Terraform >= 1.5.0 installed**
  ```bash
  terraform version
  # Should show version >= 1.5.0
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
- [ ] **Google Custom Search API key created**
  ```bash
  # Store your API key temporarily (you'll add it to Secret Manager after terraform apply)
  # You can keep it in a secure note or encrypted file
  echo "API key created and stored securely"
  ```
  **Note**: API keys will be added to Secret Manager in DEPLOYMENT.md Step 4.

- [ ] **HIBP API key purchased**
  ```bash
  # Same as above - store securely for later use
  echo "HIBP API key purchased and stored securely"
  ```

- [ ] **All PSE CX values noted**
  ```bash
  # Store all 7 CX values (including RECALL_PSE_CX_2)
  # Example storage method:
  cat > /tmp/pse-cx-values.txt <<EOF
  GOOGLE_SEARCH_CX=your-cx-here
  PRECISION_PSE_CX=your-cx-here
  RECALL_PSE_CX=your-cx-here
  RECALL_PSE_CX_2=your-cx-here  # Same as PRECISION_PSE_CX
  LINKEDIN_PSE_CX=your-cx-here
  REVIEWS_PSE_CX=your-cx-here
  COMPLAINTS_PSE_CX=your-cx-here
  EOF
  ```
  **Note**: These will be added to Secret Manager in DEPLOYMENT.md Step 4.

### PSEs Created
- [ ] **Precision PSE (CX: ____________)**
  ```bash
  # Verify by testing search: https://cse.google.com/cse?cx=YOUR_CX
  ```

- [ ] **Recall PSE (CX: ____________)**
  ```bash
  # Verify by testing search: https://cse.google.com/cse?cx=YOUR_CX
  ```

- [ ] **Recall PSE 2 (CX: ____________)**
  ```bash
  # Note: RECALL_PSE_CX_2 uses the SAME CX as PRECISION_PSE_CX
  # Verify this CX matches PRECISION_PSE_CX above
  ```

- [ ] **LinkedIn PSE (CX: ____________)**
  ```bash
  # Verify by testing search: https://cse.google.com/cse?cx=YOUR_CX
  ```

- [ ] **Reviews PSE (CX: ____________)**
  ```bash
  # Verify by testing search: https://cse.google.com/cse?cx=YOUR_CX
  ```

- [ ] **Complaints PSE (CX: ____________)**
  ```bash
  # Verify by testing search: https://cse.google.com/cse?cx=YOUR_CX
  ```

**Important**: RECALL_PSE_CX_2 should be the **same value** as PRECISION_PSE_CX (they use the same PSE).

---

## Quick Start Commands

```bash
# 1. Verify tools
gcloud version
terraform version
firebase --version

# 2. Authenticate
gcloud auth login
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID  # Required for Identity Platform API
firebase login

# 3. Set project
export PROJECT_ID="your-project-id"
gcloud config set project $PROJECT_ID

# 4. Verify project access
gcloud projects describe $PROJECT_ID

# 5. Create state bucket
gsutil mb -l northamerica-northeast1 -p $PROJECT_ID gs://${PROJECT_ID}-terraform-state
gsutil versioning set on gs://${PROJECT_ID}-terraform-state

# 6. Ready to deploy!
cd skip-trace-origination/terraform/environments/dev
terraform init
```

---

## Next Steps

Once all prerequisites are met, proceed to [DEPLOYMENT.md](./DEPLOYMENT.md).
