# Recall Search Engine 1 (RECALL_PSE_CX)

## Overview

This Programmable Search Engine (PSE) is configured for lifestyle and hobby site searches, providing broader coverage for identity resolution.

## Configuration Details

- **CX ID**: `a332dc1c537154367`
- **Region**: Canada
- **Engine Identifier**: `recall_identity`
- **Public URL**: `https://cse.google.com/cse?cx=a332dc1c537154367`

## Environment Variable

- **Variable Name**: `RECALL_PSE_CX`
- **Usage**: Set this environment variable to the CX ID that Google generated when you created this PSE

## Site Restrictions

This PSE is restricted to the following sites/patterns:

- `federalcorporation.ca/corporation/*`
- `www.canadacompanyregistry.com/*`
- `*.contactout.com/*`
- `houzz.com/professionals/*`
- `*.myhockeyrankings.com/*`
- `alltrails.com/*`
- `chess.com/member/*`
- `discogs.com/user/*`
- `fiverr.com/*`
- `flickr.com/people/*`
- `github.com/*`
- `goodreads.com/user/*`
- `gravatar.com/*`
- `inaturalist.org/people/*`
- `poshmark.ca/closet/*`
- `ravelry.com/designers/*`
- `t.me/*`
- `theknot.com/*`
- `untappd.com/*`
- `upwork.com/*`
- `varagesale.com/store/*`
- `zola.com/*`

## Search Features

- **Region restricted results**: OFF
- All other search features use default settings

## Usage

This PSE is used by:

- **phase1_identity** Cloud Function
  - Performs broader lifestyle/hobby profile searches
  - Used for recall queries with the pattern: `intext:{prefix} OR "{full_name}"`
  - Query type: `high_recall`

## Manual Setup Instructions

When creating this PSE in Google PSE control panel:

1. Create a new Programmable Search Engine
2. Name it "Recall Search - Lifestyle Sites" or similar
3. Set region to **Canada**
4. Disable "Region restricted results"
5. Add all sites listed in the "Site Restrictions" section above
6. Save the PSE and copy the Search Engine ID (CX value) - Google will generate a new CX ID for your PSE
7. Set the `RECALL_PSE_CX` environment variable to the CX ID that Google generated

## Notes

- This PSE focuses on lifestyle and hobby sites that may reveal personal interests, activities, and professional profiles
- The site restrictions must match exactly what's listed above for the code to work correctly
- This PSE is separate from RECALL_PSE_CX_2, which actually uses the PRECISION_PSE_CX configuration (see precision-search.md)
