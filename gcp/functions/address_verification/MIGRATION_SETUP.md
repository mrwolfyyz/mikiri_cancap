# Address Verification Migration Setup Guide

## Prerequisites Completed
- ✅ PSE (Programmable Search Engine) configured with synonyms
- ✅ PSE CX: `b345e1e90697640a5`
- ✅ API Key: `AIzaSyDS0smVcMgI6cRh9swwhmY29Sexb9B9Gbo`
- ✅ Service Account: `custom-search@bounceback-demo.iam.gserviceaccount.com`

## Secret Manager Setup

Before deploying, create the following secrets in Google Cloud Secret Manager:

### 1. Create GOOGLE_SEARCH_API_KEY Secret

```bash
echo -n "AIzaSyDS0smVcMgI6cRh9swwhmY29Sexb9B9Gbo" | gcloud secrets create GOOGLE_SEARCH_API_KEY \
  --data-file=- \
  --project=bounceback-demo
```

### 2. Create GOOGLE_SEARCH_CX Secret

```bash
echo -n "b345e1e90697640a5" | gcloud secrets create GOOGLE_SEARCH_CX \
  --data-file=- \
  --project=bounceback-demo
```

### 3. Grant Secret Access to Cloud Function Service Account

After deploying the function, grant access to the service account:

```bash
# Get the service account email
SERVICE_ACCOUNT=$(gcloud functions describe address_verification \
  --region=northamerica-northeast1 \
  --gen2 \
  --format='value(serviceConfig.serviceAccountEmail)')

# Grant secret accessor role
gcloud secrets add-iam-policy-binding GOOGLE_SEARCH_API_KEY \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor" \
  --project=bounceback-demo

gcloud secrets add-iam-policy-binding GOOGLE_SEARCH_CX \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor" \
  --project=bounceback-demo
```

## Deployment

Deploy using the updated `deploy.sh` script:

```bash
cd gcp
./deploy.sh address_verification
```

The deployment script will:
- Deploy with new secrets (`GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`)
- Set environment variables (`GCP_PROJECT`, `GCP_LOCATION`)
- Increase timeout to 120s
- Grant `roles/aiplatform.user` to the service account

## Verification

After deployment, test the function:

```bash
curl -X POST https://REGION-PROJECT.cloudfunctions.net/address_verification \
  -H "Content-Type: application/json" \
  -d '{
    "address": "123 Main St, Toronto, ON M5H 2N2",
    "business_name": "Test Business"
  }'
```

## Changes Summary

### Code Changes
- ✅ Replaced `serper_search` with `google_search` (Custom Search API)
- ✅ Replaced `openrouter_analyze_address` with `vertex_ai_analyze_address` (Gemini 3.0 Flash)
- ✅ Maintained all 6 search query patterns
- ✅ Preserved geocoding functionality (Nominatim)
- ✅ Preserved retry logic and error handling
- ✅ Updated to use `num=10` (PSE API limit per request)

### Dependencies
- ✅ Added `google-cloud-aiplatform>=1.38.0` to requirements.txt

### Deployment
- ✅ Updated `deploy.sh` with new secrets and env vars
- ✅ Increased timeout to 120s
- ✅ Added IAM role assignment for Vertex AI

## Notes

- **PSE API Limits**: Custom Search API returns max 10 results per request. The code now requests 10 results per query (down from 20).
- **Grounding Tool**: The implementation uses `GoogleSearch` grounding tool from Vertex AI. If this import fails, we may need to adjust the import path or use manual context passing.
- **Service Account**: The Cloud Function's service account will automatically authenticate with Vertex AI (no API key needed for Vertex AI).










