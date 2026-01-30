# Contact Extraction Cloud Function

Extracts contact information (phone numbers, emails, addresses) from web search query results using Vertex AI Gemini 2.5 Flash.

## Purpose

This function runs during Phase 2 of both skip trace and origination workflows, executing in parallel with `domain_enrichment` and `address_geocoding`. It provides intelligent contact extraction with confidence scoring, making contact information available early in the pipeline (via the aggregator) rather than waiting until report generation.

## Input

Expects JSON POST body:
```json
{
  "job_id": "abc123",
  "identity": {
    "seed": {
      "full_name": "John Doe",
      "email": "john@example.com",
      "last_known_city": "Toronto",
      "company_name": "Acme Corp"
    },
    "queries": [
      {
        "id": "query_id",
        "type": "search_type",
        "query": "search text",
        "hits": [
          {
            "title": "Page title",
            "snippet": "Page excerpt",
            "url": "https://...",
            "source": "google_search"
          }
        ]
      }
    ]
  }
}
```

## Output

Returns JSON:
```json
{
  "contacts": {
    "phones": [
      {
        "number_raw": "(416) 555-1234",
        "number_digits": "4165551234",
        "confidence": "high",
        "source_url": "https://...",
        "snippet": "Context snippet"
      }
    ],
    "emails": [
      {
        "email": "john.doe@company.com",
        "confidence": "medium",
        "source_url": "https://...",
        "snippet": "Context snippet"
      }
    ],
    "addresses": [
      {
        "address_raw": "123 Main St, Toronto, ON M5H 2N2",
        "confidence": "high",
        "source_url": "https://...",
        "snippet": "Context snippet"
      }
    ]
  }
}
```

## Features

- **LLM-powered extraction**: Uses Gemini 2.5 Flash with structured JSON schema
- **Confidence scoring**: Three-tier confidence (high/medium/low) based on context
- **Deduplication**: Removes duplicate phones, emails, and addresses
- **Source attribution**: Includes source URL and snippet for each extracted item
- **Address validation**: Filters out city-only addresses (requires street information)
- **Email filtering**: Automatically excludes seed email and generic addresses
- **Retry logic**: Exponential backoff with 3 max attempts for transient errors

## Environment Variables

- `GCP_PROJECT`: GCP project ID (required)
- `GCP_LOCATION`: Vertex AI location (default: "global")

## Dependencies

- `vertexai`: For Gemini 2.5 Flash LLM calls
- `functions-framework`: Cloud Functions runtime
- `retry_utils`: Shared retry logic with exponential backoff

## Deployment

Deployed via Terraform as part of the core module:
```bash
cd terraform/environments/prod
terraform apply
```

See `terraform/modules/core/functions.tf` for configuration.
