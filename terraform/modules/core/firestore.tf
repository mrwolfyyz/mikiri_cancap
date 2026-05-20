# =============================================================================
# Firestore Database
# =============================================================================
# Creates the Firestore database for storing job data and chat messages.
# Firestore security rules must be deployed separately via Firebase CLI.
# =============================================================================

# -----------------------------------------------------------------------------
# Firestore Database
# -----------------------------------------------------------------------------

resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Enable point-in-time recovery for production
  point_in_time_recovery_enablement = var.environment == "prod" ? "POINT_IN_TIME_RECOVERY_ENABLED" : "POINT_IN_TIME_RECOVERY_DISABLED"

  # Deletion protection for production
  delete_protection_state = var.environment == "prod" ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"

  depends_on = [
    google_project_service.apis["firestore.googleapis.com"],
    time_sleep.api_propagation
  ]
}

# -----------------------------------------------------------------------------
# TTL policies (expire_at) — automatic deletion after retention period
# One TTL field per collection group. Deletion can lag; subcollections are not auto-deleted with parent.
# -----------------------------------------------------------------------------
resource "google_firestore_field" "jobs_expire_at_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"
  field      = "expire_at"

  ttl_config {}

  depends_on = [google_firestore_database.default]
}

resource "google_firestore_field" "chat_messages_expire_at_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "chat_messages"
  field      = "expire_at"

  ttl_config {}

  depends_on = [google_firestore_database.default]
}

resource "google_firestore_field" "prefill_sessions_expire_at_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "prefill_sessions"
  field      = "expire_at"

  ttl_config {}

  depends_on = [google_firestore_database.default]
}

resource "google_firestore_field" "endpoint_rate_limit_counters_expire_at_ttl" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "endpoint_rate_limit_counters"
  field      = "expire_at"

  ttl_config {}

  depends_on = [google_firestore_database.default]
}

# -----------------------------------------------------------------------------
# Firestore Indexes (if needed)
# -----------------------------------------------------------------------------
# Composite index for rate limiting query in api_gateway:
# .where("user_id", "==", ...).where("created_at", ">=", ...)
resource "google_firestore_index" "jobs_user_created" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_email_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "user_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_cars_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_email_cars_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "user_email"
    order      = "ASCENDING"
  }

  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_cars_created_desc" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }

  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }

  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
}

# Indexes for date-range queries (created_at >= / <=) combined with optional
# equality filters. When created_at is both filtered by range AND ordered,
# Firestore requires __name__ to be explicit in the index with the same
# direction as the sort (DESCENDING). The equality-only indexes above lack
# this and will return FAILED_PRECONDITION for any range + equality combination.

resource "google_firestore_index" "jobs_workflow_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_email_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_email"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_cars_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_cars_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_email_cars_created_range" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_email"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "created_at"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

# CARS prefix range queries — ordered by cars_reference_number + __name__
# Required for prefix-match (>=, <=) on input.cars_reference_number.

resource "google_firestore_index" "jobs_workflow_cars_prefix_name" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_cars_prefix_name" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "jobs_workflow_user_email_cars_prefix_name" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "jobs"

  fields {
    field_path = "workflow_type"
    order      = "ASCENDING"
  }
  fields {
    field_path = "user_email"
    order      = "ASCENDING"
  }
  fields {
    field_path = "input.cars_reference_number"
    order      = "ASCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

# -----------------------------------------------------------------------------
# NOTE: Firestore Security Rules
# -----------------------------------------------------------------------------
# Firestore security rules cannot be deployed via Terraform directly.
# Rules must be deployed using Firebase CLI after terraform apply:
#
#   cd frontend/skiptrace
#   firebase deploy --only firestore:rules --project=$PROJECT_ID
#
# Rules files are located at:
#   - frontend/skiptrace/firestore.rules
#   - frontend/origination/firestore.rules
#
# TTL policies on `expire_at` for `jobs` and `chat_messages` are defined above
# (google_firestore_field). Allow several minutes after apply for TTL to activate.
# -----------------------------------------------------------------------------
