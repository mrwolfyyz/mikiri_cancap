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
- **Usage**: Set this environment variable to `03146b58fead44d18` for all Cloud Functions that use this PSE

## Site Restrictions

This PSE is restricted to Canadian LinkedIn profiles:

- `ca.linkedin.com/in/*`

## Search Features

- **Image search**: Enabled
- **SafeSearch**: Enabled
- **Search the entire web**: Enabled
- **Region restricted results**: Disabled (OFF)

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
4. Enable **Image search**
5. Enable **SafeSearch**
6. Enable **Search the entire web**
7. Disable "Region restricted results"
8. Add site restriction: `ca.linkedin.com/in/*`
9. Save the PSE and copy the Search Engine ID (CX value) - it should match `03146b58fead44d18` if recreating the exact configuration
10. Set the `LINKEDIN_PSE_CX` environment variable to this CX ID

## Notes

- This PSE is specifically configured for Canadian LinkedIn profiles (`ca.linkedin.com`)
- The restriction to `/in/*` paths targets individual profile pages
- Used for professional profile discovery in identity resolution workflows
- The site restriction must be exactly `ca.linkedin.com/in/*` to match LinkedIn profile URL patterns
