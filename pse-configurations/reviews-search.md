# Reviews Search Engine (REVIEWS_PSE_CX)

## Overview

This Programmable Search Engine (PSE) is configured for business review searches, used in address verification workflows.

## Configuration Details

- **CX ID**: `d5c9ade2080064150`
- **Region**: Canada
- **Engine Identifier**: `business_reviews`
- **Public URL**: `https://cse.google.com/cse?cx=d5c9ade2080064150`

## Environment Variable

- **Variable Name**: `REVIEWS_PSE_CX`
- **Usage**: Set this environment variable to the CX ID that Google generated when you created this PSE

## Site Restrictions

- **No site restrictions** - searches the entire web
- "You do not have sites to search" (unrestricted search)

## Search Features

- **Image search**: Disabled (OFF)
- **SafeSearch**: Disabled (OFF)
- **Search the entire web**: Enabled (ON)
- **Region restricted results**: Disabled (OFF)

## Query Enhancement

This PSE has synonyms configured for query enhancement:

- **Search term**: `reviews`
- **Synonyms**:
  - `ratings`
  - `feedback`
  - `testimonials`
  - `better business bureau`

When searching for "reviews", the PSE will also search for these related terms automatically.

## Usage

This PSE is used by:

- **address_verification** Cloud Function
  - Performs business review searches to verify business addresses
  - Used to find online reviews, ratings, and feedback about businesses
  - Helps validate business legitimacy and location accuracy

## Manual Setup Instructions

When creating this PSE in Google PSE control panel:

1. Create a new Programmable Search Engine
2. Name it "Reviews Search" or "Business Reviews" or similar
3. Set region to **Canada**
4. Disable **Image search**
5. Disable **SafeSearch**
6. Enable **Search the entire web**
7. Disable "Region restricted results"
8. **Do not add any site restrictions** - leave "Sites to search" empty (searches entire web)
9. **Configure Query Enhancement** (under Search Features → Query Enhancement):
   - Add search term: `reviews`
   - Add synonyms: `ratings`, `feedback`, `testimonials`, `better business bureau`
10. Save the PSE and copy the Search Engine ID (CX value) - Google will generate a new CX ID for your PSE
11. Set the `REVIEWS_PSE_CX` environment variable to the CX ID that Google generated

## Notes

- This PSE is specifically designed for finding business reviews and ratings
- The query enhancement synonyms help expand search coverage for related terms
- Unrestricted web search allows discovery across all review platforms (Google Reviews, Yelp, BBB, etc.)
- Used as part of address verification workflow in the origination frontend
