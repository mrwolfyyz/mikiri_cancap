# Base Search Engine (GOOGLE_SEARCH_CX)

## Overview

This Programmable Search Engine (PSE) provides unrestricted web search capabilities for general queries without site restrictions.

## Configuration Details

- **CX ID**: `b345e1e90697640a5`
- **Region**: Canada
- **Engine Identifier**: `general_search`
- **Public URL**: `https://cse.google.com/cse?cx=b345e1e90697640a5`

## Environment Variable

- **Variable Name**: `GOOGLE_SEARCH_CX`
- **Usage**: Set this environment variable to `b345e1e90697640a5` for all Cloud Functions that use this PSE

## Site Restrictions

- **No site restrictions** - searches the entire web
- "You do not have sites to search" (unrestricted search)

## Search Features

- **Image search**: Disabled (OFF)
- **SafeSearch**: Disabled (OFF)
- **Search the entire web**: Enabled (ON)
- **Region restricted results**: Disabled (OFF)

## Usage

This PSE is used by:

- **company_domain_lookup** Cloud Function
  - Performs general searches to find company domains
- **phase1_identity** Cloud Function
  - Performs general context searches with queries like: `intext:{prefix} OR {full_name}`
  - Query type: `context`

## Manual Setup Instructions

When creating this PSE in Google PSE control panel:

1. Create a new Programmable Search Engine
2. Name it "Base Search" or "General Search" or similar
3. Set region to **Canada**
4. Disable **Image search**
5. Disable **SafeSearch**
6. Enable **Search the entire web**
7. Disable "Region restricted results"
8. **Do not add any site restrictions** - leave "Sites to search" empty
9. Save the PSE and copy the Search Engine ID (CX value) - it should match `b345e1e90697640a5` if recreating the exact configuration
10. Set the `GOOGLE_SEARCH_CX` environment variable to this CX ID

## Notes

- This is the base/unrestricted search engine - no site restrictions should be configured
- Used for general web searches where site-specific restrictions are not needed
- The unrestricted nature allows for broad discovery across any website
