#!/usr/bin/env python3
"""
Generate Markdown reports for skip trace investigations.
Generates Identity report only (no alerts/warnings, no public salaries).
"""

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

# Shared utilities (copied by prepare-functions.sh from gcp/shared/)
from domain_utils import extract_email_domain, is_personal_email_domain
from report_utils import (
    check_domain_mx_records,
    generate_google_search_url,
    generate_google_search_url_for_email,
    generate_google_search_url_for_phone,
    generate_street_view_url,
    get_domain_registration_date,
    get_gravatar_profile,
    is_disposable_email_domain,
    load_disposable_email_blocklist,
    normalize_address,
    slugify,
)

# ==========================================
# Skip Trace Report Functions
# ==========================================


def get_navigation_bar_skiptrace(data: dict[str, Any], name: str, current_report: str) -> str:
    """
    Generate a navigation bar for skip trace reports.
    """
    wiki_name = name.replace(" ", "_")

    nav_bar = f"""> [!abstract] -
> **[[Identity___{wiki_name}|🟢 Identity]]**

---

"""
    return nav_bar


def generate_identity_report_skiptrace(
    data: dict[str, Any],
    name: str,
    output_dir: Path,
    company_domain: str = None,
    enrichment_data: dict[str, Any] = None,
) -> None:
    """
    Generate the Identity markdown file for skip trace (no salaries, no alerts/warnings).

    Args:
        data: Investigation data
        name: Borrower name
        output_dir: Output directory for markdown file
        company_domain: Optional company domain from company_domain_lookup
        enrichment_data: Optional pre-fetched enrichment data with 'domains' and 'addresses' keys
    """
    # This is a simplified version that removes:
    # - Public salaries section
    # - All contextual alerts/warnings (no breach warnings, disposable email alerts, LinkedIn alerts, domain/MX warnings)

    report_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    scored = data["scored"]
    seed = data["seed"]

    # Build snapshot section
    email = seed["email"]
    location = scored.get("location", {}).get("city", "Unknown")
    location_confidence = scored.get("location", {}).get("confidence", "unknown")

    # Extract domain and perform whois lookup if not personal email
    domain = extract_email_domain(email)
    whois_result = None
    mx_result = None

    # Use pre-fetched enrichment data if available
    if enrichment_data and enrichment_data.get("domains"):
        domain_enrichment = enrichment_data["domains"].get(domain, {})
        if domain_enrichment:
            whois_result = domain_enrichment.get("whois")
            mx_result = domain_enrichment.get("mx")
            print(f"[Identity Report SkipTrace] Using pre-fetched enrichment data for domain: {domain}")

    # Fallback to inline lookups if enrichment data not available (parallelized)
    if domain and not is_personal_email_domain(domain):
        need_whois = not whois_result
        need_mx = not mx_result
        if need_whois or need_mx:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}
                if need_whois:
                    print(
                        f"[Identity Report SkipTrace] WARNING: Falling back to inline whois lookup for {domain} (domain_enrichment data missing)"
                    )
                    futures[executor.submit(get_domain_registration_date, domain)] = "whois"
                if need_mx:
                    print(
                        f"[Identity Report SkipTrace] WARNING: Falling back to inline MX lookup for {domain} (domain_enrichment data missing)"
                    )
                    futures[executor.submit(check_domain_mx_records, domain)] = "mx"
                for future in as_completed(futures):
                    lookup_type = futures[future]
                    try:
                        result = future.result()
                        if lookup_type == "whois":
                            whois_result = result
                        else:
                            mx_result = result
                    except Exception as e:
                        print(f"[Identity Report SkipTrace] WARNING: Inline {lookup_type} lookup failed: {e}")

    # Perform company domain checks if provided
    company_whois_result = None
    company_mx_result = None
    if company_domain:
        company_domain = company_domain.strip()
        if company_domain:
            # Use pre-fetched enrichment data if available
            if enrichment_data and enrichment_data.get("domains") and company_domain in enrichment_data["domains"]:
                company_enrichment = enrichment_data["domains"][company_domain]
                company_whois_result = company_enrichment.get("whois")
                company_mx_result = company_enrichment.get("mx")
            else:
                # Parallelize company domain WHOIS and MX lookups
                from concurrent.futures import ThreadPoolExecutor, as_completed

                print(
                    f"[Identity Report SkipTrace] WARNING: Falling back to inline lookups for company domain {company_domain} (domain_enrichment data missing)"
                )
                with ThreadPoolExecutor(max_workers=2) as executor:
                    whois_future = executor.submit(get_domain_registration_date, company_domain)
                    mx_future = executor.submit(check_domain_mx_records, company_domain)
                    try:
                        company_whois_result = whois_future.result()
                    except Exception as e:
                        print(f"[Identity Report SkipTrace] WARNING: Company domain whois lookup failed: {e}")
                    try:
                        company_mx_result = mx_future.result()
                    except Exception as e:
                        print(f"[Identity Report SkipTrace] WARNING: Company domain MX lookup failed: {e}")

    # Check Gravatar profile if personal email
    gravatar_result = None
    if domain and is_personal_email_domain(domain):
        gravatar_result = get_gravatar_profile(email)

    # Load disposable email blocklist and check if email is disposable
    blocklist_path = Path(__file__).parent / "disposable_email_blocklist.conf"
    disposable_blocklist = load_disposable_email_blocklist(blocklist_path)
    _is_disposable = is_disposable_email_domain(email, disposable_blocklist)

    contactability = data.get("contactability", {})
    score = contactability.get("score", "unknown")
    footprint_bucket = contactability.get("footprint_bucket", "unknown")
    breach_bucket = contactability.get("breach_bucket", "unknown")

    breaches = data.get("breaches", [])
    top_handles = scored.get("top_handles", [])

    # --- Front matter tags ---
    borrower_slug = slugify(name)
    email_slug = slugify(email)
    location_slug = slugify(location)
    contact_score_slug = slugify(str(score))
    footprint_slug = slugify(str(footprint_bucket))
    breach_bucket_slug = slugify(str(breach_bucket))

    tags = [
        f"borrower/{borrower_slug}",
        "note/identity",
        f"email/{email_slug}",
        f"location/{location_slug}",
        f"contactability/score/{contact_score_slug}",
        f"contactability/footprint/{footprint_slug}",
        f"contactability/breach/{breach_bucket_slug}",
    ]

    # Breach tags
    for breach in breaches or []:
        breach_name = breach.get("name")
        if breach_name:
            breach_slug = slugify(breach_name)
            if breach_slug != "unknown":
                tags.append(f"breach/{breach_slug}")

    # Social platform + handle tags
    platforms = set()
    for handle in top_handles or []:
        platform = handle.get("platform")
        handle_name = handle.get("handle")
        if platform:
            platform_slug = slugify(platform)
            if platform_slug != "unknown":
                platforms.add(platform_slug)
                tags.append(f"social/platform/{platform_slug}")
                if handle_name:
                    handle_slug = slugify(handle_name)
                    if handle_slug != "unknown":
                        tags.append(f"social/handle/{platform_slug}/{handle_slug}")

    tags = sorted(set(tags))
    tags_block = "\n".join(f"  - {t}" for t in tags)
    header = f"---\ntags:\n{tags_block}\n---\n\n"

    # Get primary and secondary handles
    primary_handle = top_handles[0] if len(top_handles) > 0 else None
    secondary_handle = top_handles[1] if len(top_handles) > 1 else None

    content = header
    content += get_navigation_bar_skiptrace(data, name, "identity")
    content += "> [!info] Snapshot\n"
    content += f"> - **Name:** {name}  \n"
    content += f"> - **Email:** `{email}`  \n"
    content += f"> - **Location (scored):** {location} ({location_confidence} confidence)  \n"

    if primary_handle:
        ph_name = primary_handle['handle'] or '(no handle)'
        content += f"> - **Primary handle:** {primary_handle['platform']} — `{ph_name}` ({primary_handle.get('confidence', 'medium')} confidence)  \n"

    if secondary_handle:
        sh_name = secondary_handle['handle'] or '(no handle)'
        content += f"> - **Secondary handle:** {secondary_handle['platform']} — `{sh_name}` ({secondary_handle.get('confidence', 'medium')} confidence)\n"

    content += "\n\n"
    content += "\n---\n\n"
    content += "## Identity Confirmation\n\n"
    content += "> [!note] Rationale\n"
    content += f"> {scored.get('rationale', 'No rationale provided')}\n\n"

    # Grounding metadata section (below Rationale)
    grounding_metadata = data.get("grounding_metadata", {})
    search_queries = grounding_metadata.get("search_queries", [])

    if search_queries:
        content += "### Grounding Searches\n\n"
        content += "The following searches were performed by the AI to verify identity:\n\n"
        for query in search_queries:
            encoded_query = quote_plus(query)
            google_url = f"https://www.google.com/search?q={encoded_query}"
            content += f"- [{query}]({google_url})\n"
        content += "\n"

    content += "---\n\n"

    # Social handles section
    content += "## Social handles\n\n"
    for handle in top_handles or []:
        platform = handle["platform"]
        handle_name = handle["handle"]
        handle_url = handle["url"]
        confidence = handle.get("confidence", "medium")

        content += f"- **{platform}**  \n"
        if handle_name:
            whatsmyname_url = f"https://whatsmyname.app/?q={quote_plus(handle_name)}"
            content += f"  - Handle: [`{handle_name}`]({whatsmyname_url})  \n"
        content += f"  - Confidence: **{confidence}**  \n"
        content += f"  - URL: <{handle_url}>  \n"

        # Try to find a snippet for this handle from the queries
        snippet = None
        for query in data.get("queries", []):
            for hit in query.get("hits", []):
                hit_url = hit.get("url", "").lower()
                if handle_url.lower() == hit_url or (hit_url and hit_url.rstrip("/") == handle_url.lower().rstrip("/")):
                    snippet = hit.get("snippet", "")
                    break
            if snippet:
                break

        if snippet:
            content += "  - Snippet:  \n"
            content += f"    > {snippet}\n"

        content += "\n"

    content += "---\n\n"

    # Data Breaches section (NO ALERTS - just list the breaches)
    content += "## Data Breaches\n\n"
    if breaches and len(breaches) > 0:
        # Sort breaches chronologically by date (oldest first)
        def sort_key(breach):
            date_str = breach.get("date")
            if date_str:
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    return datetime.max
            else:
                return datetime.max

        sorted_breaches = sorted(breaches, key=sort_key)

        # Display as a table
        content += "| Breach Name | Date |\n"
        content += "|-------------|------|\n"

        for breach in sorted_breaches:
            breach_name = breach.get("name", "Unknown")
            breach_date = breach.get("date", "")
            if breach_date:
                content += f"| {breach_name} | {breach_date} |\n"
            else:
                content += f"| {breach_name} | *(Unknown)* |\n"
    else:
        content += "*(None)*\n"

    content += f"Source: [Have I Been Pwned](https://haveibeenpwned.com) ·Timestamp: {report_timestamp}\n\n"

    content += "\n---\n\n"

    # Gravatar profile section (if available)
    if gravatar_result and gravatar_result.get("success"):
        content += "### Gravatar Profile\n\n"
        content += f"![Gravatar Avatar]({gravatar_result['thumbnail_url']})  \n"
        content += f"[View Full Profile →]({gravatar_result['profile_url']})  \n"
        content += "\n"

    content += "---\n\n"

    # Domain Registration section (informational only, NO warnings/alerts)
    if whois_result and whois_result.get("success") and whois_result.get("registration_date"):
        reg_date = whois_result["registration_date"]

        # Calculate age for display
        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"

        content += "## Domain Registration\n\n"
        content += "> [!info] Domain Registration\n"
        content += f"> **Domain:** {domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += "\n---\n\n"

    # MX Record Analysis section (informational only, NO warnings/alerts)
    if domain and not is_personal_email_domain(domain):
        content += "## Email Infrastructure (MX Records)\n\n"
        content += "> [!info] Email Infrastructure\n"
        content += f"> **Domain:** {domain}  \n"

        if mx_result and mx_result.get("success"):
            primary_mx = mx_result.get("mx_records", [""])[0] if mx_result.get("mx_records") else "N/A"
            provider = mx_result.get("provider_detected", "Unknown")
            risk_level = mx_result.get("risk_level", "UNKNOWN")
            status = mx_result.get("status", "Unknown")

            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
        else:
            error_msg = mx_result.get("error", "Unknown error") if mx_result else "MX lookup not performed"
            status = mx_result.get("status", "Lookup Failed") if mx_result else "Lookup Failed"
            risk_level = mx_result.get("risk_level", "UNKNOWN") if mx_result else "UNKNOWN"

            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"

        content += "\n---\n\n"

    # Company Domain Registration section (informational only, NO warnings/alerts)
    if company_whois_result and company_whois_result.get("success") and company_whois_result.get("registration_date"):
        reg_date = company_whois_result["registration_date"]

        try:
            reg_date_obj = datetime.strptime(reg_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - reg_date_obj).days
            if age_days < 30:
                age_text = f"{age_days} days"
            elif age_days < 365:
                age_text = f"{age_days // 30} months"
            else:
                age_text = f"{age_days // 365} years"
        except Exception:
            age_text = "unknown"

        content += "## Company Domain Registration\n\n"
        content += "> [!info] Company Domain Registration\n"
        content += f"> **Domain:** {company_domain}  \n"
        content += f"> **Registration Date:** {reg_date}  \n"
        content += f"> **Domain Age:** {age_text}  \n"
        content += "\n---\n\n"

    # Company Email Infrastructure section (informational only, NO warnings/alerts)
    if company_domain:
        content += "## Company Email Infrastructure (MX Records)\n\n"
        content += "> [!info] Company Email Infrastructure\n"
        content += f"> **Domain:** {company_domain}  \n"

        if company_mx_result and company_mx_result.get("success"):
            primary_mx = company_mx_result.get("mx_records", [""])[0] if company_mx_result.get("mx_records") else "N/A"
            provider = company_mx_result.get("provider_detected", "Unknown")
            risk_level = company_mx_result.get("risk_level", "UNKNOWN")
            status = company_mx_result.get("status", "Unknown")

            content += f"> **Primary MX Record:** `{primary_mx}`  \n"
            content += f"> **Provider:** {provider}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            content += f"> **Status:** {status}  \n"
        else:
            error_msg = (
                company_mx_result.get("error", "Unknown error") if company_mx_result else "MX lookup not performed"
            )
            status = company_mx_result.get("status", "Lookup Failed") if company_mx_result else "Lookup Failed"
            risk_level = company_mx_result.get("risk_level", "UNKNOWN") if company_mx_result else "UNKNOWN"

            content += f"> **Status:** {status}  \n"
            content += f"> **Risk Level:** {risk_level}  \n"
            if error_msg:
                content += f"> **Error:** {error_msg}  \n"

        content += "\n---\n\n"

    # Possible Phone Number(s) section
    # Get contact info from enrichment data (extracted during Phase 2)
    contact_info = {}
    if enrichment_data and enrichment_data.get("contacts"):
        contact_info = enrichment_data.get("contacts", {})
    else:
        # Fallback to empty structure if enrichment data not available
        contact_info = {"phones": [], "emails": [], "addresses": []}

    phones = contact_info.get("phones", [])

    if phones:
        content += "## Phone Number(s) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected phone numbers from open-web sources.\n\n"

        for i, p in enumerate(phones, start=1):
            google_url = generate_google_search_url_for_phone(p)
            content += f"{i}. **[{p['number_raw']}]({google_url})**  \n"
            content += f"   - **Confidence:** {p.get('confidence', 'medium')}  \n"
            if p.get("source_url"):
                content += f"   - **Source:** {p['source_url']}  \n"
            if p.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {p['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Possible Email Address(es) section
    emails = contact_info.get("emails", [])
    if emails:
        content += "## Email Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected email addresses from open-web sources.\n\n"

        for i, e in enumerate(emails, start=1):
            google_url = generate_google_search_url_for_email(e["email"])
            content += f"{i}. **[{e['email']}]({google_url})**  \n"
            content += f"   - **Confidence:** {e.get('confidence', 'medium')}  \n"
            if e.get("source_url"):
                content += f"   - **Source:** {e['source_url']}  \n"
            if e.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {e['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Possible Address(es) section
    # Get addresses from contact_info (has source_url and snippet)
    # Use enrichment_data only for geocoding coordinates
    addresses = contact_info.get("addresses", [])

    if addresses:
        content += "## Address(es) of interest\n\n"
        content += "> [!info]\n"
        content += "> Automatically detected addresses from open-web sources.\n\n"

        # Get geocoding data from enrichment if available
        geocoding_data = {}
        if enrichment_data and enrichment_data.get("addresses"):
            geocoding_data = enrichment_data["addresses"]

        # Build normalized lookup for fuzzy address matching (contact_extraction and address_geocoding use separate LLM calls)
        geocoding_normalized = {normalize_address(k): v for k, v in geocoding_data.items()}

        for i, a in enumerate(addresses, start=1):
            raw_addr = a["address_raw"]

            # Use cached coordinates if available (normalized matching)
            cached_coords = None
            normalized_key = normalize_address(raw_addr)
            if normalized_key in geocoding_normalized:
                geocode_result = geocoding_normalized[normalized_key]
                if geocode_result.get("lat") and geocode_result.get("lon"):
                    cached_coords = {"lat": geocode_result["lat"], "lon": geocode_result["lon"]}

            # Never geocode inline during report generation - only use cached coordinates or fall back to search URL
            street_view_url = generate_street_view_url(raw_addr, geocode=False, cached_coords=cached_coords)
            google_url = generate_google_search_url(a)
            content += f"{i}. **[{raw_addr}]({google_url})**  \n"
            content += f"   - [📍 View Property]({street_view_url})  \n"
            content += f"   - **Confidence:** {a.get('confidence', 'medium')}  \n"

            if a.get("source_url"):
                content += f"   - **Source:** {a['source_url']}  \n"
            if a.get("snippet"):
                content += "   - **Snippet:**  \n"
                content += f"     > {a['snippet']}\n"
            content += "\n"

        content += "---\n\n"

    # Sources section
    content += "## Sources\n\n"
    for query in data.get("queries", []):
        query_id = query.get("id", "")
        query_type = query.get("type", "").replace("_", " ").title()
        query_text = query.get("query", "")
        hits = query.get("hits", [])

        section_name = query_id.replace("_", " ").title() if query_id else query_type

        content += f"### {section_name}\n\n"
        # Determine source label from hits (check first hit's source field, or default to google_search)
        source = "google_search"
        if hits and len(hits) > 0:
            source = hits[0].get("source", "google_search")
            # Debug logging for LinkedIn source detection
            if query_id == "company_name_linkedin":
                all_sources = [h.get("source", "MISSING") for h in hits]
                print(
                    f"[Report Generator] LinkedIn query: query_id={query_id}, hits_count={len(hits)}, first_hit_source={source}, all_sources={all_sources}"
                )
        if source == "vertex_ai_linkedin":
            source_label = "Vertex AI Search (LinkedIn)"
        elif source == "vertex_ai_precision":
            source_label = "Vertex AI Search (Social)"
        elif source == "vertex_ai_recall":
            source_label = "Vertex AI Search (Lifestyle)"
        elif source == "google_search":
            source_label = "Google"
        else:
            source_label = source.replace("_", " ").title()

        # Always generate a Google link; use site:linkedin.com for LinkedIn queries
        is_linkedin_query = "linkedin" in query_id
        if is_linkedin_query:
            source_url = f"https://www.google.com/search?q=site%3Alinkedin.com+{quote_plus(query_text)}"
        else:
            source_url = f"https://www.google.com/search?q={quote_plus(query_text)}"

        content += f"Source: {source_label} · Timestamp: {report_timestamp}\n\n"
        content += "**Query**\n\n"
        content += f"[`{query_text}`]({source_url})\n\n"
        content += "**Hits**\n\n"

        if hits:
            for i, hit in enumerate(hits, 1):
                title = hit.get("title", "Untitled")
                url = hit.get("url", "")
                snippet = hit.get("snippet", "")
                content += f"{i}. [{title}]({url})  \n"
                content += f"   - **URL:** {url}  \n"
                content += f"   > {snippet}\n\n"
        else:
            content += "*(None)*\n\n"

        content += "---\n\n"

    # Write file
    wiki_name = name.replace(" ", "_")
    output_file = output_dir / f"Identity___{wiki_name}.md"
    output_file.write_text(content, encoding="utf-8")
    print(f"[Identity Report SkipTrace] Generated: {output_file}")
