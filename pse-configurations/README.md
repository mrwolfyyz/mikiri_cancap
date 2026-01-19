# PSE Configuration Setup Guide

This directory contains documentation and configuration information for all Programmable Search Engines (PSEs) used in the skip trace and origination repository.

## Overview

This repository uses 6 unique Google Programmable Search Engines (PSEs) to perform various types of web searches. Since XML export functionality is currently broken in the Google PSE control panel, all PSEs must be configured manually using the documentation in this directory.

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

Before setting up PSEs, ensure you have:

1. **Google Cloud Project** with Custom Search API enabled
2. **Google Custom Search API Key** - Create credentials in [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
3. **Access to Google PSE Control Panel** - [https://programmablesearchengine.google.com/](https://programmablesearchengine.google.com/)
4. **Understanding of your deployment environment** - Know where to set environment variables/secrets

## Setup Process

### Step 1: Create Google Custom Search API Key

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to **APIs & Services** → **Credentials**
3. Click **Create Credentials** → **API Key**
4. Restrict the API key to **Custom Search API** (recommended)
5. Save the API key as `GOOGLE_SEARCH_API_KEY` in your environment/secrets

### Step 2: Create Each PSE

For each PSE you need to create:

1. **Navigate to Google PSE Control Panel**: [https://programmablesearchengine.google.com/](https://programmablesearchengine.google.com/)

2. **Click "Add"** to create a new search engine

3. **Follow the specific PSE documentation**:
   - Open the relevant PSE markdown file (e.g., `base-search.md`)
   - Follow the "Manual Setup Instructions" section
   - Configure region, site restrictions, and search features exactly as documented

4. **Save and Copy CX ID**:
   - After creating the PSE, copy the "Search engine ID" (CX value)
   - Verify it matches the expected CX ID in the documentation (if recreating the exact configuration)
   - Save the CX ID for environment variable configuration

5. **Configure Query Enhancement** (if applicable):
   - For PSEs with query enhancement (REVIEWS_PSE_CX and COMPLAINTS_PSE_CX), configure synonyms under **Search Features** → **Query Enhancement**
   - See the individual PSE documentation for specific synonym lists

### Step 3: Verify PSE Configuration

After creating each PSE, verify:

- ✅ Region is set to **Canada**
- ✅ Site restrictions match the documentation exactly
- ✅ Search features (Image search, SafeSearch, etc.) are configured correctly
- ✅ Query enhancement synonyms are configured (if applicable)
- ✅ CX ID is copied and saved

### Step 4: Set Environment Variables

Set the following environment variables (or secrets in your deployment system):

```bash
# Required for all searches
GOOGLE_SEARCH_API_KEY=<your-api-key>

# Base search
GOOGLE_SEARCH_CX=b345e1e90697640a5

# Identity resolution PSEs
PRECISION_PSE_CX=73bc98f068711495f
RECALL_PSE_CX=a332dc1c537154367
RECALL_PSE_CX_2=73bc98f068711495f  # Same as PRECISION_PSE_CX
LINKEDIN_PSE_CX=03146b58fead44d18

# Address verification PSEs
REVIEWS_PSE_CX=d5c9ade2080064150
COMPLAINTS_PSE_CX=f55c8831c767349da
```

**Important**: `RECALL_PSE_CX_2` should be set to the same value as `PRECISION_PSE_CX` (they use the same PSE).

## Common Issues

### Searches Return No Results

- **Check site restrictions**: Ensure site restrictions match the documentation exactly (including wildcards `*` and path patterns)
- **Verify region**: All PSEs should be set to Canada region
- **Check API key**: Verify `GOOGLE_SEARCH_API_KEY` is correctly set and has access to Custom Search API

### PSE Configuration Doesn't Match Expected CX ID

- **Expected behavior**: If you're recreating PSEs from scratch, the CX ID will be different. This is normal.
- **Solution**: Use the CX ID that Google assigns when you create the PSE, and update your environment variables accordingly.
- **Note**: The CX IDs in the documentation are from the original setup - new PSEs will have different IDs.

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
