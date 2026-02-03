# BounceBack Loan Origination Investigation Workflow
# This orchestrates the loan origination investigation pipeline (simplified version)
# Only runs identity resolution, domain enrichment, address geocoding, and aggregator
# Excludes regulator, litigation, corporate, and salaries phases
#
# NOTE: This is a Terraform template file. Variables like $${...} are workflow
# expressions that get passed through. Single $ variables are Terraform
# template variables that get replaced during terraform apply.

main:
  params: [input]
  steps:
    - init:
        assign:
          - job_id: $${input.job_id}
          - email: $${input.email}
          - full_name: $${input.full_name}
          - city: $${input.city}
          - company_name: $${default(map.get(input, "company_name"), "")}
          - project_id: "${project_id}"

    - update_status_running:
        call: googleapis.firestore.v1.projects.databases.documents.patch
        args:
          name: $${"projects/" + project_id + "/databases/(default)/documents/jobs/" + job_id}
          updateMask:
            fieldPaths: ["status", "started_at", "workflow_type"]
          body:
            fields:
              status:
                stringValue: "running"
              started_at:
                timestampValue: $${time.format(sys.now())}
              workflow_type:
                stringValue: "origination"
        result: update_result

    # Company Domain Lookup (only if company_name provided)
    - check_company_name:
        switch:
          - condition: $${company_name != ""}
            steps:
              - company_domain_lookup:
                  try:
                    call: http.post
                    args:
                      url: "${company_domain_lookup_url}"
                      auth:
                        type: OIDC
                      body:
                        company_name: $${company_name}
                        job_id: $${job_id}
                      timeout: 120
                    result: company_domain_result
                  retry:
                    max_attempts: 2
                    interval: 2s
                    max_interval: 30s
                    multiplier: 2.0
                  except:
                    as: e
                    assign:
                      - company_domain_result: null

    # Phase 1: Identity Resolution
    - phase1_identity:
        try:
          call: http.post
          args:
            url: "${phase1_identity_url}"
            auth:
              type: OIDC
            body:
              job_id: $${job_id}
              email: $${email}
              full_name: $${full_name}
              city: $${city}
              company_name: $${company_name}
            timeout: 180
          result: identity_result
        retry:
          max_attempts: 3
          interval: 2s
          max_interval: 60s
          multiplier: 2.0
        except:
          as: e
          raise: $${e}

    # Initialize phase2 results and errors (origination only)
    - init_phase2:
        assign:
          - domain_enrichment_result:
              body: null
          - geocoding_identity_result:
              body: null
          - contact_extraction_result:
              body: null
          - phase2_errors:
              domain_enrichment: null
              geocoding_identity: null
              contact_extraction: null
          # Extract company_domain from company_domain_lookup result if available
          - company_domain: $${default(map.get(map.get(company_domain_result, "body"), "domain"), "")}

    # Phase 2: Parallel execution of domain enrichment, identity geocoding, and contact extraction
    - phase2_parallel:
        parallel:
          shared: [identity_result, domain_enrichment_result, geocoding_identity_result, contact_extraction_result, phase2_errors, company_domain, job_id]
          branches:
            - domain_enrichment_branch:
                steps:
                  - call_domain_enrichment:
                      try:
                        call: http.post
                        args:
                          url: "${domain_enrichment_url}"
                          auth:
                            type: OIDC
                          body:
                            email: $${email}
                            company_domain: $${company_domain}
                          timeout: 60
                        result: domain_enrichment_result
                      retry:
                        max_attempts: 3
                        interval: 2s
                        max_interval: 60s
                        multiplier: 2.0
                      except:
                        as: e
                        steps:
                          - set_domain_enrichment_error:
                              assign:
                                - domain_enrichment_result:
                                    body: null
                                - phase2_errors.domain_enrichment: $${string(e.message)}

            - geocoding_identity_branch:
                steps:
                  - call_geocoding_identity:
                      try:
                        call: http.post
                        args:
                          url: "${address_geocoding_url}"
                          auth:
                            type: OIDC
                          body:
                            identity: $${identity_result.body}
                            corporate: null
                          timeout: 600
                        result: geocoding_identity_result
                      retry:
                        max_attempts: 3
                        interval: 2s
                        max_interval: 60s
                        multiplier: 2.0
                      except:
                        as: e
                        steps:
                          - set_geocoding_identity_error:
                              assign:
                                - geocoding_identity_result:
                                    body: null
                                - phase2_errors.geocoding_identity: $${string(e.message)}

            - contact_extraction_branch:
                steps:
                  - call_contact_extraction:
                      try:
                        call: http.post
                        args:
                          url: "${contact_extraction_url}"
                          auth:
                            type: OIDC
                          body:
                            job_id: $${job_id}
                            identity: $${identity_result.body}
                          timeout: 300
                        result: contact_extraction_result
                      retry:
                        max_attempts: 3
                        interval: 2s
                        max_interval: 60s
                        multiplier: 2.0
                      except:
                        as: e
                        steps:
                          - set_contact_extraction_error:
                              assign:
                                - contact_extraction_result:
                                    body: null
                                - phase2_errors.contact_extraction: $${string(e.message)}

    # Sanitize errors for aggregator
    - sanitize_phase2_errors:
        assign:
          - sanitized_phase2_errors:
              regulator: ""
              litigation: ""
              corporate: ""
              salaries: ""
              domain_enrichment: $${string(default(phase2_errors.domain_enrichment, ""))}
              address_geocoding: $${string(default(phase2_errors.geocoding_identity, ""))}
              contact_extraction: $${string(default(phase2_errors.contact_extraction, ""))}

    # Aggregate results (pass null for regulator, litigation, corporate, salaries)
    # Note: geocoding_identity_result.body already has {"addresses": {...}} structure
    - aggregate:
        try:
          call: http.post
          args:
            url: "${aggregator_url}"
            auth:
              type: OIDC
            body:
              job_id: $${job_id}
              identity: $${identity_result.body}
              regulator: null
              litigation: null
              corporate: null
              salaries: null
              domain_enrichment: $${default(domain_enrichment_result.body, null)}
              address_geocoding: $${default(geocoding_identity_result.body, null)}
              contact_extraction: $${default(contact_extraction_result.body, null)}
              errors: $${sanitized_phase2_errors}
            timeout: 30
          result: aggregated_result
        retry:
          max_attempts: 3
          interval: 2s
          max_interval: 60s
          multiplier: 2.0
        except:
          as: e
          raise: $${e}

    - prepare_firestore_data:
        assign:
          - aggregated_body: $${aggregated_result.body}
          - result_json: $${json.encode_to_string(aggregated_body)}
          - result_summary_json: $${json.encode_to_string(aggregated_body.result_summary)}
          - errors_json: $${json.encode_to_string(aggregated_body.errors)}

    - update_status_post_processing:
        call: googleapis.firestore.v1.projects.databases.documents.patch
        args:
          name: $${"projects/" + project_id + "/databases/(default)/documents/jobs/" + job_id}
          updateMask:
            fieldPaths: ["status", "result", "result_summary", "partial_failure", "errors"]
          body:
            fields:
              status:
                stringValue: "post_processing"
              result:
                stringValue: $${result_json}
              result_summary:
                stringValue: $${result_summary_json}
              partial_failure:
                booleanValue: $${aggregated_body.partial_failure}
              errors:
                stringValue: $${errors_json}
        result: final_update

    - return_success:
        return:
          job_id: $${job_id}
          status: "post_processing"
