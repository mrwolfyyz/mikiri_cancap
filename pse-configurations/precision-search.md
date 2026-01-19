# Precision Search Engine (PRECISION_PSE_CX)

## Overview

This Programmable Search Engine (PSE) is configured for high-precision social platform searches, targeting major social media and professional networking sites.

## Configuration Details

- **CX ID**: `73bc98f068711495f`
- **Region**: Canada
- **Engine Identifier**: `precision_identity`
- **Public URL**: `https://cse.google.com/cse?cx=73bc98f068711495f`

## Environment Variables

- **Variable Name**: `PRECISION_PSE_CX`
  - **Usage**: Set this environment variable to the CX ID that Google generated when you created this PSE
- **Variable Name**: `RECALL_PSE_CX_2` (reuses this same PSE)
  - **Usage**: Set this environment variable to the same CX ID as PRECISION_PSE_CX
  - **Note**: RECALL_PSE_CX_2 uses the same PSE configuration as PRECISION_PSE_CX for additional recall searches

## Site Restrictions

This PSE is restricted to the following sites/patterns:

- `www.youtube.com/*`
- `www.pressreader.com/canada/*`
- `*.legacy.com/*`
- `nationalpost.remembering.ca/*`
- `ca.linkedin.com/in/*`
- `facebook.com/*`
- `github.com/*`
- `gravatar.com/*`
- `instagram.com/*`
- `linkedin.com/in/*`
- `tiktok.com/*`
- `twitter.com/*`
- `x.com/*`

## Search Features

- **Region restricted results**: OFF
- All other search features use default settings

## Usage

This PSE is used by:

- **phase1_identity** Cloud Function
  - **PRECISION_PSE_CX**: Performs high-precision social profile searches
    - Used for precision queries targeting social platforms
    - Query type: `precision`
  - **RECALL_PSE_CX_2**: Performs additional recall searches using the same PSE
    - Used as a second recall query with the pattern: `intext:{prefix} OR "{full_name}"`
    - Query type: `high_recall`
    - **Note**: This provides broader coverage by using the precision PSE for recall purposes

## Manual Setup Instructions

When creating this PSE in Google PSE control panel:

1. Create a new Programmable Search Engine
2. Name it "Precision Search - Social Platforms" or similar
3. Set region to **Canada**
4. Disable "Region restricted results"
5. Add all sites listed in the "Site Restrictions" section above
6. Save the PSE and copy the Search Engine ID (CX value) - Google will generate a new CX ID for your PSE
7. Set both `PRECISION_PSE_CX` and `RECALL_PSE_CX_2` environment variables to this same CX ID

## Notes

- This PSE targets major social media and professional networking platforms
- The same PSE is used for both precision searches (PRECISION_PSE_CX) and additional recall searches (RECALL_PSE_CX_2)
- When setting up environment variables, both `PRECISION_PSE_CX` and `RECALL_PSE_CX_2` should point to this same PSE ID
- The site restrictions must match exactly what's listed above for the code to work correctly
