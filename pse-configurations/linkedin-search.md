# LinkedIn Search Engine (LINKEDIN_PSE_CX)

## Overview

This Programmable Search Engine (PSE) is configured specifically for LinkedIn profile searches, restricted to Canadian LinkedIn profiles.

## Configuration Details

- **CX ID**: `03146b58fead44d18`
- **Region**: Canada
- **Engine Identifier**: `linkedin_canada_only`
- **Public URL**: `https://cse.google.com/cse?cx=03146b58fead44d18`

## Environment Variable

- **Variable Name**: `LINKEDIN_PSE_CX`
- **Usage**: Set this environment variable to the CX ID that Google generated when you created this PSE

## Site Restrictions

This PSE is restricted to Canadian LinkedIn profiles:

- `ca.linkedin.com/in/*`

## Search Features

- **Region restricted results**: Disabled (OFF)
- All other search features use default settings

## Usage

This PSE is used by:

- **phase1_identity** Cloud Function
  - Performs LinkedIn-specific profile searches
  - Used for finding professional LinkedIn profiles
  - Query type: `linkedin`

## Manual Setup Instructions

When creating this PSE in Google PSE control panel:

1. Create a new Programmable Search Engine
2. Name it "LinkedIn Search" or "LinkedIn Canada Only" or similar
3. Set region to **Canada**
4. Disable "Region restricted results"
5. Add site restriction: `ca.linkedin.com/in/*`
6. Save the PSE and copy the Search Engine ID (CX value) - Google will generate a new CX ID for your PSE
7. Set the `LINKEDIN_PSE_CX` environment variable to the CX ID that Google generated

## Notes

- This PSE is specifically configured for Canadian LinkedIn profiles (`ca.linkedin.com`)
- The restriction to `/in/*` paths targets individual profile pages
- Used for professional profile discovery in identity resolution workflows
- The site restriction must be exactly `ca.linkedin.com/in/*` to match LinkedIn profile URL patterns
