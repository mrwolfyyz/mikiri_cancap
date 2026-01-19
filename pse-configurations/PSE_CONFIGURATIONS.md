# PSE Configuration Master Reference

This document provides a master reference for all Programmable Search Engines (PSEs) used in the skip trace and origination repository.

## Summary

**Total Unique PSEs**: 6  
**Total Environment Variables**: 7 (RECALL_PSE_CX_2 reuses PRECISION_PSE_CX)

## PSE Configuration Table

| Environment Variable | CX ID | Engine Identifier | Region | Site Restrictions | Usage |
|---------------------|-------|-------------------|--------|-------------------|-------|
| `GOOGLE_SEARCH_CX` | `b345e1e90697640a5` | general_search | Canada | None (entire web) | Base search, company domain lookup |
| `PRECISION_PSE_CX` | `73bc98f068711495f` | precision_identity | Canada | Social platforms (see below) | Precision social searches |
| `RECALL_PSE_CX_2` | `73bc98f068711495f` | precision_identity | Canada | Social platforms (same as PRECISION_PSE_CX) | Additional recall searches (reuses PRECISION_PSE_CX) |
| `RECALL_PSE_CX` | `a332dc1c537154367` | recall_indentity | Canada | Lifestyle sites (see below) | Lifestyle/hobby searches |
| `LINKEDIN_PSE_CX` | `03146b58fead44d18` | linkedin_canada_only | Canada | `ca.linkedin.com/in/*` | LinkedIn profile searches |
| `REVIEWS_PSE_CX` | `d5c9ade2080064150` | business_reviews | Canada | None (entire web) | Business review searches |
| `COMPLAINTS_PSE_CX` | `f55c8831c767349da` | business_complaints | Canada | None (entire web) | Business complaint searches |

## Quick Reference by PSE Type

### Identity Resolution PSEs (phase1_identity)

- **GOOGLE_SEARCH_CX**: General context searches
- **PRECISION_PSE_CX**: High-precision social platform searches
- **RECALL_PSE_CX**: Lifestyle and hobby site searches
- **RECALL_PSE_CX_2**: Additional recall searches (reuses PRECISION_PSE_CX)
- **LINKEDIN_PSE_CX**: LinkedIn-specific profile searches

### Address Verification PSEs (address_verification)

- **REVIEWS_PSE_CX**: Business review and rating searches
- **COMPLAINTS_PSE_CX**: Business complaint and fraud searches

### Company Domain Lookup PSE

- **GOOGLE_SEARCH_CX**: General web search for company domains

## Site Restrictions Summary

### PRECISION_PSE_CX Sites

- www.youtube.com/*
- www.pressreader.com/canada/*
- *.legacy.com/*
- nationalpost.remembering.ca/*
- ca.linkedin.com/in/*
- facebook.com/*
- github.com/*
- gravatar.com/*
- instagram.com/*
- linkedin.com/in/*
- tiktok.com/*
- twitter.com/*
- x.com/*

### RECALL_PSE_CX Sites

- federalcorporation.ca/corporation/*
- www.canadacompanyregistry.com/*
- *.contactout.com/*
- houzz.com/professionals/*
- *.myhockeyrankings.com/*
- alltrails.com/*
- chess.com/member/*
- discogs.com/user/*
- fiverr.com/*
- flickr.com/people/*
- github.com/*
- goodreads.com/user/*
- gravatar.com/*
- inaturalist.org/people/*
- poshmark.ca/closet/*
- ravelry.com/designers/*
- t.me/*
- theknot.com/*
- untappd.com/*
- upwork.com/*
- varagesale.com/store/*
- zola.com/*

## Environment Variable Setup

When deploying, set these environment variables (or secrets) to their corresponding CX IDs:

```bash
GOOGLE_SEARCH_CX=b345e1e90697640a5
PRECISION_PSE_CX=73bc98f068711495f
RECALL_PSE_CX=a332dc1c537154367
RECALL_PSE_CX_2=73bc98f068711495f  # Same as PRECISION_PSE_CX
LINKEDIN_PSE_CX=03146b58fead44d18
REVIEWS_PSE_CX=d5c9ade2080064150
COMPLAINTS_PSE_CX=f55c8831c767349da
```

## Query Enhancement

The following PSEs have query enhancement (synonyms) configured:

### REVIEWS_PSE_CX
- Search term: `reviews`
- Synonyms: `ratings`, `feedback`, `testimonials`, `better business bureau`

### COMPLAINTS_PSE_CX
- Search term: `complaints`
- Synonyms: `issues`, `scam`, `fraud`, `lawsuit`, `investigation`

## Detailed Documentation

For detailed setup instructions and configuration details for each PSE, see:

- [Base Search Engine (GOOGLE_SEARCH_CX)](base-search.md)
- [Precision Search Engine (PRECISION_PSE_CX)](precision-search.md)
- [Recall Search Engine 1 (RECALL_PSE_CX)](recall-search-1.md)
- [LinkedIn Search Engine (LINKEDIN_PSE_CX)](linkedin-search.md)
- [Reviews Search Engine (REVIEWS_PSE_CX)](reviews-search.md)
- [Complaints Search Engine (COMPLAINTS_PSE_CX)](complaints-search.md)

## Important Notes

1. **RECALL_PSE_CX_2 Reuses PRECISION_PSE_CX**: The `RECALL_PSE_CX_2` environment variable should be set to the same CX ID as `PRECISION_PSE_CX` (`73bc98f068711495f`). They use the same PSE configuration.

2. **Region**: All PSEs are configured for the **Canada** region.

3. **Site Restrictions Must Match**: The site restrictions listed above must be configured exactly as shown in the Google PSE control panel. Incorrect restrictions will cause searches to fail or return no results.

4. **XML Export Not Available**: Due to XML export functionality being broken in Google PSE control panel, these configurations must be set up manually using the instructions in each PSE's documentation file.

5. **Query Enhancement**: Some PSEs use query enhancement (synonyms) to expand search coverage. These must be configured manually in the Google PSE control panel under Search Features → Query Enhancement.
