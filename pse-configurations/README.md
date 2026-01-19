# PSE Configuration Setup Guide

This directory contains documentation and configuration information for all Programmable Search Engines (PSEs) used in the skip trace and origination repository.

## Overview

This repository uses 6 unique Google Programmable Search Engines (PSEs) to perform various types of web searches. Since XML export functionality is currently broken in the Google PSE control panel, all PSEs must be configured manually using the documentation in this directory.

**Important**: The CX IDs shown in this documentation are from the original Mikiri deployment. When you create new PSEs, Google will assign different CX IDs. Use the CX IDs that Google generates for your PSEs in your environment variables.

## Quick Start

1. **Review the Master Reference**: See [PSE_CONFIGURATIONS.md](PSE_CONFIGURATIONS.md) for a complete overview of all PSEs and their mappings.

2. **Create Each PSE**: Follow the setup instructions in each individual PSE documentation file:
   - [Base Search Engine](base-search.md) - `GOOGLE_SEARCH_CX`
   - [Precision Search Engine](precision-search.md) - `PRECISION_PSE_CX` (also used by `RECALL_PSE_CX_2`)
   - [Recall Search Engine 1](recall-search-1.md) - `RECALL_PSE_CX`
   - [LinkedIn Search Engine](linkedin-search.md) - `LINKEDIN_PSE_CX`
   - [Reviews Search Engine](reviews-search.md) - `REVIEWS_PSE_CX`
   - [Complaints Search Engine](complaints-search.md) - `COMPLAINTS_PSE_CX`

3. **Configure Environment Variables**: Set the CX IDs as environment variables or secrets in your deployment (see [PSE_CONFIGURATIONS.md](PSE_CONFIGURATIONS.md) for the mapping).

## Prerequisites

Before setting up PSEs, complete these steps:

### 1. Enable Custom Search API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Navigate to **APIs & Services** → **Library**
4. Search for "Custom Search API"
5. Click on "Custom Search API" and click **Enable**

### 2. Create Google Custom Search API Key

1. In [Google Cloud Console](https://console.cloud.google.com/), navigate to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **API Key**
3. Copy the API key immediately and save it securely
4. (Recommended) Click **Restrict Key** to add security restrictions:
   - Under "API restrictions", select "Restrict key"
   - Select "Custom Search API" from the dropdown
   - Click **Save**
5. Save this API key as `GOOGLE_SEARCH_API_KEY` in your environment/secrets

### 3. Access to Google PSE Control Panel

Ensure you can access [https://programmablesearchengine.google.com/](https://programmablesearchengine.google.com/)

### 4. Deployment Environment

Know where to set environment variables/secrets in your deployment system (GCP Secret Manager, environment variables, etc.)

## Setup Process

### Step 1: Create Each PSE

For each PSE you need to create:

1. **Navigate to Google PSE Control Panel**: [https://programmablesearchengine.google.com/](https://programmablesearchengine.google.com/)

2. **Click "Add"** to create a new search engine

3. **Follow the specific PSE documentation**:
   - Open the relevant PSE markdown file (e.g., `base-search.md`)
   - Follow the "Manual Setup Instructions" section
   - Configure region, site restrictions, and search features exactly as documented

4. **Save and Copy CX ID**:
   - After creating the PSE, copy the "Search engine ID" (CX value)
   - The CX ID will be different from what's documented (Google generates a new ID for each PSE)
   - Save the CX ID for environment variable configuration
   - **Important**: When creating PRECISION_PSE_CX, save the same CX ID for both `PRECISION_PSE_CX` and `RECALL_PSE_CX_2` environment variables - they share the same PSE

5. **Configure Query Enhancement** (if applicable):
   - For PSEs with query enhancement (REVIEWS_PSE_CX and COMPLAINTS_PSE_CX), configure synonyms under **Search Features** → **Query Enhancement**
   - See the individual PSE documentation for specific synonym lists

### Step 2: Verify PSE Configuration

After creating each PSE, verify:

- ✅ Region is set to **Canada**
- ✅ Site restrictions match the documentation exactly
- ✅ Search features (Image search, SafeSearch, etc.) are configured correctly
- ✅ Query enhancement synonyms are configured (if applicable)
- ✅ CX ID is copied and saved

### Step 3: Set Environment Variables

Set the following environment variables (or secrets in your deployment system):

```bash
# Required for all searches
GOOGLE_SEARCH_API_KEY=<your-api-key>

# PSE CX IDs - use the CX IDs that Google generated for YOUR PSEs (not the values shown below)
# The values below are from the original Mikiri deployment and are shown as reference only

# Base search
GOOGLE_SEARCH_CX=<your-cx-id>

# Identity resolution PSEs
PRECISION_PSE_CX=<your-cx-id>
RECALL_PSE_CX=<your-cx-id>
RECALL_PSE_CX_2=<same-as-PRECISION_PSE_CX>  # IMPORTANT: Use the same CX ID as PRECISION_PSE_CX
LINKEDIN_PSE_CX=<your-cx-id>

# Address verification PSEs
REVIEWS_PSE_CX=<your-cx-id>
COMPLAINTS_PSE_CX=<your-cx-id>
```

**Important**: `RECALL_PSE_CX_2` should be set to the same value as `PRECISION_PSE_CX` (they use the same PSE). Only create 6 PSEs total, not 7.

## Common Issues

### Searches Return No Results

- **Check site restrictions**: Ensure site restrictions match the documentation exactly (including wildcards `*` and path patterns)
- **Verify region**: All PSEs should be set to Canada region
- **Check API key**: Verify `GOOGLE_SEARCH_API_KEY` is correctly set and has access to Custom Search API

### Query Enhancement Not Working

- **Verify synonyms are configured**: Check Search Features → Query Enhancement in Google PSE control panel
- **Check search term spelling**: Ensure the search term matches exactly (case-sensitive)
- **Maximum synonyms**: Google PSE allows a maximum of 10 synonyms per search term

### Site Restrictions Not Applied

- **Verify wildcard usage**: Use `*` at the end of domain patterns (e.g., `github.com/*`)
- **Check path restrictions**: Some sites require path restrictions (e.g., `ca.linkedin.com/in/*`)
- **Save configuration**: Ensure you save the PSE after adding site restrictions

## Testing

After setting up all PSEs:

1. **Test API Key**: Verify `GOOGLE_SEARCH_API_KEY` works with Custom Search API
2. **Test Each PSE**: Use the Google PSE control panel's "Try it now" feature to verify each PSE returns expected results
3. **Test in Application**: Run a test query through your application to verify all PSEs are configured correctly

## Documentation Files

- **[PSE_CONFIGURATIONS.md](PSE_CONFIGURATIONS.md)** - Master reference table with all PSEs and their mappings
- **[base-search.md](base-search.md)** - Base/unrestricted search engine
- **[precision-search.md](precision-search.md)** - Precision social platform search (also used for RECALL_PSE_CX_2)
- **[recall-search-1.md](recall-search-1.md)** - Lifestyle/hobby site search
- **[linkedin-search.md](linkedin-search.md)** - LinkedIn profile search
- **[reviews-search.md](reviews-search.md)** - Business review search
- **[complaints-search.md](complaints-search.md)** - Business complaint search

## Additional Resources

- [Google Custom Search API Documentation](https://developers.google.com/custom-search/v1/overview)
- [Programmable Search Engine Control Panel](https://programmablesearchengine.google.com/)
- [Custom Search API Quotas and Limits](https://developers.google.com/custom-search/v1/using_rest#quota)

## Support

If you encounter issues setting up PSEs:

1. Review the individual PSE documentation files for detailed setup instructions
2. Verify all configuration matches the documentation exactly
3. Check Google PSE control panel for any error messages or warnings
4. Ensure your Google Cloud project has Custom Search API enabled

## Notes

- **XML Export Not Available**: Due to XML export functionality being broken, PSEs cannot be imported from XML files. All configuration must be done manually.
- **CX IDs Are Unique**: Each PSE you create will have a unique CX ID. The CX IDs in the documentation are from the original setup and are provided as reference only.
- **RECALL_PSE_CX_2 Reuse**: The `RECALL_PSE_CX_2` environment variable uses the same PSE as `PRECISION_PSE_CX` - you only need to create one PSE for both variables.
