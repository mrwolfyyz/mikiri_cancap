# Troubleshooting Guide

Common issues and solutions for the Skip Trace & Origination Intelligence Platform.

## Table of Contents

1. [Terraform Issues](#terraform-issues)
2. [Function Deployment Issues](#function-deployment-issues)
3. [Firebase Issues](#firebase-issues)
4. [Runtime Issues](#runtime-issues)
5. [Frontend Issues](#frontend-issues)
6. [Workflow Issues](#workflow-issues)

---

## Terraform Issues

### Error: "API not enabled"

**Symptom:**
```
Error: Error creating ...: googleapi: Error 403: ... API has not been enabled for project ...
```

**Solution:**
APIs need time to propagate. Terraform includes `time_sleep` resources, but sometimes you need to wait longer:

```bash
# Manually enable the API
gcloud services enable SERVICE_NAME.googleapis.com

# Wait a few minutes, then retry
terraform apply
```

---

### Error: "Permission denied"

**Symptom:**
```
Error: googleapi: Error 403: The caller does not have permission
```

**Solution:**

1. Verify your account has required roles:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID --format="table(bindings.role)"
   ```

2. Re-authenticate:
   ```bash
   gcloud auth application-default login
   ```

3. Check if using a service account:
   ```bash
   gcloud auth list
   ```

---

### Error: "Resource already exists"

**Symptom:**
```
Error: Error creating ...: googleapi: Error 409: Resource already exists
```

This means the resource exists in GCP but not in Terraform state — typically from a manual creation in the console/gcloud, or from a partial apply where the GCP create succeeded but the state write failed.

**Solution:** prefer `terraform import` over `state rm`. `state rm` followed by `terraform apply` only works if Terraform can recreate the resource; for resources where recreation triggers downtime or behaviour change (IAM bindings, Vertex AI Search corpora, anything indexed), import is safer.

1. **Find the GCP resource name.** For most resources `gcloud <service> list` works. For Vertex AI Search target sites (where the resource ID is an opaque base64 blob), you must call the Discovery Engine REST API directly:
   ```bash
   TOKEN=$(gcloud auth print-access-token)
   curl -s -H "Authorization: Bearer $TOKEN" \
        -H "x-goog-user-project: PROJECT_ID" \
        "https://discoveryengine.googleapis.com/v1/projects/PROJECT_ID/locations/global/collections/default_collection/dataStores/DATA_STORE_ID/siteSearchEngine/targetSites?pageSize=200"
   ```
   The `x-goog-user-project` header is required — without it the API returns `403 PERMISSION_DENIED` complaining about a missing quota project.

2. **Import into state:**
   ```bash
   terraform import 'module.path.RESOURCE_TYPE.RESOURCE_NAME' 'FULL_GCP_RESOURCE_NAME'
   ```

3. **Re-plan.** Terraform will show whatever diff exists between the imported real-world state and your config (often an attribute drift). If the diff requires a replace and that's unacceptable, either adjust the config to match reality or accept the brief downtime.

#### Special case: Firestore composite index race on first apply

On a brand-new project, several `jobs` composite indexes commonly fail the first `terraform apply` with `Error 409: index already exists`. The GCP create succeeded but Terraform did not record the result in state — typically because indexes are created in parallel and the API returns the same ID before state writes complete.

To map each failing Terraform address to its GCP index ID:

```bash
PROJECT_ID="your-project-id"

# 1. Get all jobs indexes with their field shapes
gcloud firestore indexes composite list --project=$PROJECT_ID --format=json \
  | jq -r '.[] | "\(.name | split("/")[-1])  |  \([.fields[] | "\(.fieldPath):\(.order // .arrayConfig)"] | join(", "))"'
```

The output is one line per index: `<INDEX_ID>  |  field1:ORDER, field2:ORDER, ...`. Match each failing Terraform resource (e.g. `module.core.google_firestore_index.jobs_workflow_user_created_desc`) to the row whose fields match the resource's `fields { ... }` blocks in `terraform/modules/core/firestore.tf`. GCP appends `__name__:DESCENDING` to every index automatically — ignore that trailing field when matching unless the Terraform resource also declares an explicit `__name__` field.

Then `terraform import` each one:

```bash
terraform import 'module.core.google_firestore_index.RESOURCE_NAME' \
  'projects/PROJECT_ID/databases/(default)/collectionGroups/jobs/indexes/INDEX_ID'
```

After all imports, run `terraform plan`. The 409s disappear; any remaining `~`/`+`/`-` is normal post-import drift (commonly a regenerated `local_file.*` or in-place update on `google_identity_platform_config`). Re-run `terraform apply` to converge.

---

### Error: state move blocked — destination already exists

**Symptom:**
```
Warning: Unresolved resource instance address changes
  - module.X.RESOURCE_A could not move to module.X.RESOURCE_B[0]
Terraform has planned to destroy these objects.
```

`moved {}` blocks in the module code told Terraform to rename a resource, but both addresses already exist in state — usually because a prior targeted apply (or `terraform import`) created the new name without removing the old. The planned `destroy` will resolve the duplicate **in state**, but it will also call the underlying API to delete the real resource.

**This is especially dangerous for `google_*_iam_member` resources.** Deleting one of two duplicate state entries that point to the same binding removes the binding from GCP. The "winning" address remains in state as a no-op, so it won't re-create the binding until the next apply detects drift — meanwhile the real binding is gone.

**Solution:** drop the stale duplicate from state without calling the API.

1. Verify both addresses point to the same real resource (matching `id`, `etag`, role/member):
   ```bash
   terraform show -json PLAN_FILE | jq '.resource_changes[] | select(.address | contains("RESOURCE_NAME"))'
   ```

2. Remove the stale name from state (the one that's NOT the new canonical name from the `moved` block):
   ```bash
   terraform state rm 'module.path.RESOURCE_TYPE.STALE_NAME'
   ```
   `state rm` only edits the local state JSON. It does not call any GCP API.

3. Re-plan. The destroy entry should disappear; the surviving address (already a no-op) continues to manage the real resource.

If you accidentally accept the planned destroy on an IAM resource, recovery is `terraform apply` again immediately — drift detection will re-create the binding, but there will be a gap during which public/cross-service access is revoked.

---

### Error: "State lock"

**Symptom:**
```
Error: Error locking state: Error acquiring the state lock
```

**Solution:**

```bash
# Force unlock (use with caution!)
terraform force-unlock LOCK_ID

# Or wait for other processes to complete
```

---

### Error: "oauth2: invalid_grant - reauth related error"

**Symptom:**
```
Error: oauth2: "invalid_grant" "reauth related error (invalid_rapt)"
```

**Root Cause:**

Application Default Credentials (ADC) are not properly configured for Terraform. This commonly occurs when:
1. ADC credentials have expired or become invalid
2. Quota project is not set in ADC
3. Authentication commands were run in the wrong order
4. Project was changed but ADC wasn't updated

**Solution:**

Run authentication commands in EXACT order:

```bash
# 1. Set project FIRST (before any authentication)
gcloud config set project mikiri-demo-test

# 2. Login with user account (opens browser)
gcloud auth login

# 3. Set up Application Default Credentials
gcloud auth application-default login

# 4. Set quota project (CRITICAL - not automatic)
gcloud auth application-default set-quota-project mikiri-demo-test
```

**Verification:**

```bash
# Verify active account
gcloud auth list

# Verify project is set
gcloud config get-value project

# Verify quota project in ADC
cat ~/.config/gcloud/application_default_credentials.json | jq .quota_project_id
# Should show: "mikiri-demo-test"

# Test GCS access (Terraform backend)
gsutil ls -b gs://mikiri-demo-test-terraform-state
```

**Pre-Deployment Check:**

To prevent authentication issues, run this check before any Terraform operations:

```bash
./scripts/check-terraform-auth.sh
```

This script validates:
- Project is set correctly
- User is authenticated
- ADC credentials exist
- Quota project matches target project
- Terraform state bucket is accessible

**Note**: The quota project must be manually set with `gcloud auth application-default set-quota-project` - it is NOT inherited from `billing_project` in Terraform provider blocks. This is a prerequisite for Terraform operations.

---

### Google Cloud Shell: ADC missing at `~/.config/gcloud/application_default_credentials.json`

**Symptom:** After `gcloud auth application-default login`, this fails:

```bash
ls ~/.config/gcloud/application_default_credentials.json
```

but `gcloud auth application-default print-access-token` may still succeed, and `./scripts/check-terraform-auth.sh` exits with **Application Default Credentials not set**.

**Cause:** Cloud Shell sets **`CLOUDSDK_CONFIG`** to a per-session directory (commonly under **`/tmp/...`**). The gcloud CLI stores **`application_default_credentials.json`** under **`$CLOUDSDK_CONFIG`** in that setup. **`check-terraform-auth.sh`** only checks **`$HOME/.config/gcloud/application_default_credentials.json`**.

**Fix (copy ADC to the path this repo expects):**

```bash
echo "CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-<unset>}"
ls -la "${CLOUDSDK_CONFIG}/application_default_credentials.json"
mkdir -p ~/.config/gcloud
cp "${CLOUDSDK_CONFIG}/application_default_credentials.json" ~/.config/gcloud/application_default_credentials.json
chmod 600 ~/.config/gcloud/application_default_credentials.json
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
jq -r '.quota_project_id' ~/.config/gcloud/application_default_credentials.json
```

Replace **`YOUR_PROJECT_ID`** with your GCP project ID. See also [PREREQUISITES.md — Google Cloud Shell: ADC file location](./PREREQUISITES.md#google-cloud-shell-adc-file-location).

**Tip:** Paste **one command at a time** into Cloud Shell; merged lines can produce confusing errors.

---

### Error: "Application Default Credentials quota project not set"

**Symptom:**
```
Error: Error creating identity platform config: Your application is authenticating by using local Application Default Credentials. The identitytoolkit.googleapis.com API requires a quota project, which is not set by default.
Details:
  "consumer": "projects/XXXXX"  # May show different project number than expected
```

**Root Cause:**

Identity Toolkit (Identity Platform) API requires a quota project to be explicitly set in Application Default Credentials (ADC). The quota project is the project billed or associated with quota consumption for API usage.

**Common Issues:**

1. **Quota project not set**: ADC credentials don't have a quota project configured.
2. **OAuth client project mismatch**: If ADC credentials were created with an OAuth client from a different project, the error may show a different "consumer" project number (e.g., `projects/764086051850`) than your target project number.
3. **Organizational/account-level OAuth client**: The OAuth client may belong to an organization-level or account-level project that cannot be changed by re-authenticating ADC. Even when setting the correct project before `gcloud auth application-default login`, the OAuth client may still belong to a different project due to organizational OAuth consent screen settings.

**Solution:**

1. **Ensure correct project is active**:
   ```bash
   gcloud config set project PROJECT_ID
   ```

2. **Set quota project in Application Default Credentials** (required):
   ```bash
   gcloud auth application-default set-quota-project PROJECT_ID
   ```

3. **If OAuth client mismatch, try re-authenticating ADC**:
   ```bash
   # Re-authenticate with correct project active
   gcloud config set project PROJECT_ID
   gcloud auth application-default login
   
   # Then set quota project again
   gcloud auth application-default set-quota-project PROJECT_ID
   
   # Verify if OAuth client matches (may still belong to org-level project)
   PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
   ADC_CLIENT_ID=$(cat ~/.config/gcloud/application_default_credentials.json | grep -o '"client_id": "[^"]*"' | cut -d'"' -f4 | cut -d'-' -f1)
   if [ "$ADC_CLIENT_ID" != "$PROJECT_NUMBER" ]; then
     echo "⚠️  OAuth client belongs to different project. This may be an org-level client."
     echo "   Workaround: Configure Identity Platform manually via Firebase Console (see below)"
   fi
   ```
   
   **Note**: If re-authentication doesn't fix the OAuth client mismatch, the OAuth client likely belongs to an organization-level or account-level project that cannot be changed. This is common in organizational GCP setups where the OAuth consent screen is configured at the organization level. In this case, you have two options:
   
   - **Option A (Recommended)**: Configure Identity Platform manually via Firebase Console (see "Unable to initialize authentication" section below)
   - **Option B**: Use a service account key for Terraform authentication instead of user credentials (requires creating and downloading a service account key)

4. **Verify quota project is set**:
   ```bash
   cat ~/.config/gcloud/application_default_credentials.json | grep quota_project_id
   ```
   
   Should show: `"quota_project_id": "your-project-id"`

5. **Verify IAM permissions**: Ensure the user/service account has `roles/serviceusage.serviceUsageConsumer` on the quota project:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID \
     --flatten="bindings[].members" \
     --filter="bindings.members:user:YOUR_EMAIL"
   ```

6. Re-run `terraform apply`.

**Note**: The `billing_project` parameter in Terraform provider blocks is for billing purposes but does not set the ADC quota project. Identity Platform API requires the quota project to be set in Application Default Credentials via `gcloud auth application-default set-quota-project`. This is a prerequisite step that must be completed before running `terraform apply`.

---

### Error: "Eventarc Service Account does not exist"

**Symptom:**
```
Error: Error creating IAM member: Service account service-PROJECT_NUMBER@gcp-sa-eventarc.iam.gserviceaccount.com does not exist
```

**Solution:**

1. This is expected GCP behavior - the Eventarc service account is Google-managed and created automatically when the Eventarc API is enabled, but it may take 2-3 minutes to be created.

2. Functions are now configured to be created independently of the Eventarc SA IAM binding. If the IAM binding fails on first apply, proceed:

   - Functions will be created successfully (they don't depend on the binding at creation time)
   - The IAM binding (`roles/eventarc.eventReceiver`) will succeed on second `terraform apply` once the SA exists
   - Cloud Run IAM bindings (`roles/run.invoker`) are created after functions, so they will also succeed once the SA exists

3. If you want to verify the SA exists before retry:
   ```bash
   gcloud iam service-accounts list --filter="email:*eventarc*"
   ```

4. Wait 2-3 minutes after enabling Eventarc API, then run `terraform apply` again.

**Note**: This is legitimate GCP API timing behavior, not a Terraform flaw. The IAM bindings are required and follow best practices - the issue is only timing of SA creation.

---

### Error: "Cloud Build service account missing permissions"

**Symptom:**
```
Error: Build failed with status: FAILURE. Could not build the function due to a missing permission on the build service account
```

**Root Cause:**

As of May-June 2024, GCP changed the default service account behavior for Cloud Build in new projects. Cloud Functions Gen2 builds (which use Cloud Build internally) now use the **default Compute Engine service account** (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`) instead of the legacy Cloud Build service account (`PROJECT_NUMBER@cloudbuild.gserviceaccount.com`).

This is **expected GCP behavior**, not a bug. The Terraform configuration grants permissions to both service accounts to ensure compatibility.

**Note**: The current Terraform configuration (as of the fix) automatically ensures functions wait for Compute SA IAM bindings to be created before starting builds. This should prevent this error in most cases. However, if IAM propagation is very slow (5-10 minutes), you may still encounter this error even with the fix.

**Solution:**

1. **Check IAM propagation**: GCP IAM changes can take 5-10 minutes to propagate. Wait 5-10 minutes after granting permissions, then retry.

2. **Verify required permissions are granted**: Both service accounts need these permissions:
   - `roles/cloudfunctions.developer`
   - `roles/run.admin`
   - `roles/iam.serviceAccountUser`
   - `roles/storage.objectViewer` (at project level)
   - `roles/artifactregistry.writer`
   - `roles/storage.objectAdmin` (on function source bucket)

   Check Cloud Build SA permissions:
   ```bash
   PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
   gcloud projects get-iam-policy PROJECT_ID \
     --flatten="bindings[].members" \
     --filter="bindings.members:serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
   ```

   Check Compute SA permissions:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID \
     --flatten="bindings[].members" \
     --filter="bindings.members:serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
   ```

3. **Check Cloud Build logs** to identify which service account is being used:
   ```bash
   gcloud builds list --limit=5
   gcloud builds log BUILD_ID
   ```
   
   Look for lines indicating which service account is authenticating:
   - `"serviceAccount: PROJECT_NUMBER-compute@developer.gserviceaccount.com"` (default as of 2024)
   - `"serviceAccount: PROJECT_NUMBER@cloudbuild.gserviceaccount.com"` (legacy)

4. **Verify Terraform has granted permissions to both SAs**: The Terraform configuration creates IAM bindings for both `compute_functions_developer`, `compute_run_admin`, etc. (for Compute SA) and `cloudbuild_functions_developer`, `cloudbuild_run_admin`, etc. (for Cloud Build SA). Additionally, all functions include Compute SA dependencies in their `depends_on` clauses to ensure IAM bindings are created before builds start.

5. **Organization policy restrictions**: If using an organization, check if there are policies restricting service account permissions.

**Note**: This dual-service-account approach is the **standard best practice** for Cloud Functions Gen2 deployments as of 2024. It ensures compatibility with both legacy and new default behaviors. The Terraform configuration automatically handles the dependencies - you should not encounter this error unless IAM propagation is unusually slow.

---

## Function Deployment Issues

### Error: "Build failed"

**Symptom:**
```
Error: Error creating function: googleapi: Error 400: Build failed
```

**Solution:**

1. Check Cloud Build logs:
   ```bash
   gcloud builds list --limit=5
   gcloud builds log BUILD_ID
   ```

2. Common causes:
   - Missing `requirements.txt`
   - Invalid Python syntax
   - Missing imports

3. Test locally:
   ```bash
   cd gcp/functions/function_name
   pip install -r requirements.txt
   python -c "from main import main"
   ```

---

### Error: "Missing retry_utils.py"

**Symptom:**
```
ModuleNotFoundError: No module named 'retry_utils'
```

**Solution:**

Run the prepare script:
```bash
./scripts/prepare-functions.sh
```

Or manually copy:
```bash
cp gcp/shared/retry_utils.py gcp/functions/domain_enrichment/
```

---

### Error: "Function invocation timeout"

**Symptom:**
Function returns 504 or "DEADLINE_EXCEEDED"

**Solution:**

1. Increase timeout in `terraform.tfvars`:
   ```hcl
   function_timeout = {
     api_gateway = 300  # Increase as needed
   }
   ```

2. Check if external APIs are slow
3. Add logging to identify bottleneck

---

## Firebase Issues

### Error: "Firebase project not found"

**Symptom:**
```
Error: Error creating FirebaseProject: googleapi: Error 404: Firebase project not found
```

**Solution:**

1. Ensure Firebase is linked to project:
   ```bash
   firebase projects:list
   ```

2. Add Firebase to project:
   ```bash
   firebase projects:addfirebase PROJECT_ID
   ```

---

### Error: "Unable to initialize authentication" (Frontend Authentication Error)

**Symptom:**
```
Unable to initialize authentication. Please refresh the page.
```

**Root Cause:**

The frontend cannot initialize Google SSO because Firebase Identity Platform/Google provider configuration is missing or incomplete. This usually happens when `google_identity_platform_config` and/or `google_identity_platform_default_supported_idp_config` failed during Terraform apply (often due to quota/OAuth client mismatch issues) or were never applied.

**Solution (Choose One):**

**Option A: Configure Google Sign-In via Firebase Console (Quick Fix)**

1. Navigate to Firebase Console:
   - Go to: https://console.firebase.google.com/project/PROJECT_ID
   - Click "Authentication" in the left menu
   - Click "Sign-in method" tab

2. Enable Google provider:
   - Find "Google" in the list
   - Click "Google"
   - Toggle "Enable" to ON
   - Configure support email
   - If prompted for Web SDK credentials, use your OAuth Client ID/Client Secret
   - Click "Save"

3. Verify the frontend works:
   - Refresh the frontend page
   - Sign in with an allowed-domain Google account

**Option B: Fix Terraform and Apply (Recommended for Infrastructure as Code)**

If the Identity Platform config failed due to quota project issues:

1. **Fix ADC quota project** (if not already done):
   ```bash
   gcloud config set project PROJECT_ID
   gcloud auth application-default login
   gcloud auth application-default set-quota-project PROJECT_ID
   ```

2. **Apply Identity Platform config**:
   ```bash
   cd terraform/environments/dev  # or prod
   terraform apply
   ```

**Note**: 
- **Option A (Firebase Console)** is the quickest and most reliable workaround, especially when dealing with organizational OAuth client mismatches.
- **Option B (Terraform)** is preferred for infrastructure as code, but can fail if there's an OAuth client project mismatch that prevents the quota project from being used correctly. This is common in organizational GCP setups where the OAuth consent screen is configured at the organization level and the OAuth client belongs to a different project than the target project.

**Organizational OAuth Client Limitation**: If your organization has an OAuth consent screen configured at the organization level, the OAuth client used by `gcloud auth application-default login` may belong to an organization-level project that cannot be changed by the user. Re-authenticating ADC will not fix this. In such cases, Option A (Firebase Console) is the recommended solution, or use service account-based Terraform auth.

---

### Error: "App Check token required" after first enforcement

**Symptom:**

Immediately after `terraform apply` enables App Check enforcement for a project for the first time, authenticated API calls fail with:

```
{"error": "App Check token required"}
```

or:

```
{"error": "App Check failed"}
```

Direct Firestore reads from the browser may also fail with "Missing or insufficient permissions" even though the user is signed in with an allowed-domain account.

**Root Cause:**

A newly provisioned reCAPTCHA Enterprise key takes 60–90 seconds to propagate before it will mint App Check tokens that the Firebase backend accepts. During that window the backend correctly rejects requests because the client cannot produce a valid token. The same error appears if the frontend bundle was not redeployed after `terraform apply` and is still running the old anonymous-auth initializer without `firebase.appCheck().activate(...)`.

**Solution:**

1. Wait 60–90 seconds after `terraform apply` completes.
2. Hard-refresh the browser (forces the Firebase JS SDK to re-run `appCheck.activate()` with the freshly provisioned key).
3. Retry the request.

If the errors persist beyond ~5 minutes:

1. Confirm the frontend was redeployed after the upgrade (the shared bundle must include `auth.js`):

   ```bash
   curl -s https://PROJECT_ID-skiptrace.web.app/auth.js | \
     grep -c "ReCaptchaEnterpriseProvider"
   # Expect: 1 (or more). If 0, run ./scripts/prepare-frontend.sh and
   # `firebase deploy --only hosting` for both frontends.
   ```

2. Confirm `recaptchaSiteKey` in the deployed `firebase-config.json` matches the key Terraform manages:

   ```bash
   curl -s https://PROJECT_ID-skiptrace.web.app/firebase-config.json | \
     python -c "import sys, json; print(json.load(sys.stdin)['recaptchaSiteKey'])"

   cd terraform/environments/<env>
   terraform state show module.core.google_recaptcha_enterprise_key.web | grep '^\s*name'
   ```

3. Confirm Firestore App Check enforcement is actually on:

   ```bash
   TOKEN=$(gcloud auth application-default print-access-token)
   curl -s \
     -H "Authorization: Bearer $TOKEN" \
     -H "x-goog-user-project: PROJECT_ID" \
     "https://firebaseappcheck.googleapis.com/v1/projects/PROJECT_ID/services/firestore.googleapis.com"
   # Expect: "enforcementMode": "ENFORCED"
   ```

   The `x-goog-user-project` header is required; without it the Firebase App Check API returns 403 on ADC credentials.

---

### Error: "Firebase hosting deployment failed"

**Symptom:**
```
Error: Deploy target skiptrace not configured for project PROJECT_ID. Configure with:
  firebase target:apply hosting skiptrace <resources...>
```

or

```
Error: Hosting site or target PROJECT_ID-skiptrace not detected in firebase.json
```

**Root Cause:**

The `firebase.json` file uses target names (`skiptrace`, `origination`), but Firebase needs these targets mapped to the actual site IDs created by Terraform (`PROJECT_ID-skiptrace`, `PROJECT_ID-origination`). The mapping is configured via `firebase target:apply`.

**Solution:**

1. **Configure hosting targets** (required before first deployment):
   ```bash
   cd frontend/skiptrace
   firebase target:apply hosting skiptrace PROJECT_ID-skiptrace
   
   cd ../origination
   firebase target:apply hosting origination PROJECT_ID-origination
   ```

2. **Then deploy** (after targets are configured):
   ```bash
   cd frontend/skiptrace
   firebase deploy --only hosting
   ```

3. **Verify target configuration**:
   ```bash
   cat .firebaserc
   ```
   
   Should show the target mapping in the `targets` section.

**Note**: The `firebase target:apply` command must be run before the first deployment. See [DEPLOYMENT.md Step 5b](./DEPLOYMENT.md#step-5b-configure-firebase-hosting-targets) for details.

---

**Alternative Error**: If you see `Error: HTTP Error: 403, Permission denied`:

1. Authenticate Firebase:
   ```bash
   firebase logout
   firebase login
   ```

2. Verify `.firebaserc` has correct project:
   ```bash
   cat .firebaserc
   ```

---

## Runtime Issues

### Error: "Secret not found"

**Symptom:**
```
PermissionDenied: 7 PERMISSION_DENIED: Permission denied on resource project PROJECT_ID
```

**Solution:**

1. Check secret exists:
   ```bash
   gcloud secrets list
   ```

2. Add secret value:
   ```bash
   echo -n "VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-
   ```

3. Verify IAM:
   ```bash
   gcloud secrets get-iam-policy SECRET_NAME
   ```

---

### Error: "Firestore permission denied"

**Symptom:**
```
PERMISSION_DENIED: Missing or insufficient permissions
```

**Solution:**

1. Check Firestore rules:
   ```bash
   firebase deploy --only firestore:rules
   ```

2. Verify service account has Datastore User role:
   ```bash
   gcloud projects get-iam-policy PROJECT_ID | grep datastore
   ```

---

### Error: "Chat history not persisting" or "Chat history lost on refresh"

**Symptom:**
- Chat messages are sent and appear in the UI
- After refreshing the browser, chat history is empty
- Browser console shows `[loadChatHistory] Found 0 messages`

**Root Cause:**

Firestore security rules are not deployed. Without deployed rules, the frontend cannot read or write to the `chat_messages` subcollection.

**Solution:**

Deploy Firestore security rules (required for chat functionality):

```bash
# Deploy Skip Trace Firestore rules
cd frontend/skiptrace
firebase deploy --only firestore:rules

# Deploy Origination Firestore rules
cd ../origination
firebase deploy --only firestore:rules
```

**Verification:**

After deploying rules:
1. Send a test message in the chat
2. Refresh the browser
3. Chat history should persist and reload

**Note**: Firestore rules deployment is documented in [DEPLOYMENT.md Step 6b](./DEPLOYMENT.md#step-6b-deploy-firestore-security-rules). This is a required step for chat functionality to work.

---

### Error: "Workflow execution failed"

**Symptom:**
Workflow shows "FAILED" status

**Solution:**

1. Check execution logs:
   ```bash
   gcloud workflows executions list --workflow=WORKFLOW_NAME --location=REGION
   gcloud workflows executions describe EXECUTION_ID --workflow=WORKFLOW_NAME --location=REGION
   ```

2. Common causes:
   - Function URL incorrect (check Gen2 format)
   - Authentication issues (OIDC)
   - Timeout

---

## Frontend Issues

### Error: "firebase-config.json not found"

**Symptom:**
Browser console shows "Failed to load firebase-config.json"

**Solution:**

1. Verify Terraform generated the file:
   ```bash
   ls frontend/skiptrace/public/firebase-config.json
   cat frontend/skiptrace/public/firebase-config.json
   ```

2. Re-run Terraform if missing:
   ```bash
   terraform apply -target=local_file.firebase_config_skiptrace
   ```

3. Manually create from Terraform output:
   ```bash
   terraform output firebase_config > frontend/skiptrace/public/firebase-config.json
   ```

---

### Error: "CORS error"

**Symptom:**
```
Access to fetch at '...' from origin '...' has been blocked by CORS policy
```

**Solution:**

1. Check CORS configuration:
   ```bash
   terraform output cors_allowed_origins
   ```

2. For development, set:
   ```hcl
   cors_allowed_origins = "*"
   ```

3. For production, use specific domain:
   ```hcl
   cors_allowed_origins = "https://your-project-skiptrace.web.app"
   ```

4. Re-deploy API Gateway after changes

---

### Error: "API URL incorrect"

**Symptom:**
Network errors when submitting investigations

**Solution:**

1. Verify `firebase-config.json` has correct URL:
   ```bash
   cat frontend/skiptrace/public/firebase-config.json | jq .apiUrl
   ```

2. URL should be Gen2 format (Cloud Run):
   ```
   https://api-gateway-HASH-REGION.a.run.app
   ```
   
   NOT Gen1 format:
   ```
   https://REGION-PROJECT_ID.cloudfunctions.net/api_gateway
   ```

---

## Workflow Issues

### Error: "Function URL not accessible"

**Symptom:**
Workflow fails with "Connection refused" or 403

**Solution:**

1. Verify function is deployed:
   ```bash
   gcloud functions list --gen2 --region=REGION
   ```

2. Check IAM allows workflow to invoke:
   ```bash
   gcloud run services get-iam-policy FUNCTION_NAME --region=REGION
   ```

3. Verify URL format in workflow template matches deployed function

---

### Error: "OIDC authentication failed"

**Symptom:**
```
UNAUTHENTICATED: Request had invalid authentication credentials
```

**Solution:**

1. Verify workflow service account exists:
   ```bash
   gcloud iam service-accounts list | grep workflow
   ```

2. Verify service account has invoker role:
   ```bash
   gcloud run services get-iam-policy FUNCTION_NAME --region=REGION
   ```

3. Check workflow uses correct service account:
   ```bash
   gcloud workflows describe WORKFLOW_NAME --location=REGION | grep serviceAccount
   ```

---

## Getting Help

### Logs to Check

1. **Cloud Functions:**
   ```bash
   gcloud functions logs read FUNCTION_NAME --gen2 --region=REGION --limit=100
   ```

2. **Cloud Build:**
   ```bash
   gcloud builds list --limit=5
   ```

3. **Workflows:**
   ```bash
   gcloud workflows executions list --workflow=WORKFLOW_NAME --location=REGION
   ```

4. **Firestore:**
   Check Firebase Console > Firestore Database > Data

### Support Resources

- [Google Cloud Documentation](https://cloud.google.com/docs)
- [Firebase Documentation](https://firebase.google.com/docs)
- [Terraform Google Provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs)

### Reporting Issues

When reporting issues, include:
1. Error message (full text)
2. Terraform version: `terraform version`
3. gcloud version: `gcloud version`
4. Relevant log output
5. Steps to reproduce
