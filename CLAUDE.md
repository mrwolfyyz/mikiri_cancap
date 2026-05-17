# CLAUDE.md

## Project Overview
Cloud Functions-based data intelligence platform with 13 serverless GCP Cloud Functions orchestrated by Cloud Workflows. Two frontend apps (skiptrace + origination) with Chrome extension.

## Project Structure
- `gcp/functions/` — 13 Cloud Functions (each self-contained with own `requirements.txt`)
- `gcp/shared/` — Shared utilities (copied to functions before deploy, NOT imported)
- `gcp/workflows/` — Google Cloud Workflows YAML definitions
- `terraform/` — Infrastructure-as-code (environments: dev, prod)
- `tests/` — pytest test suite
- `scripts/` — Deployment and utility scripts
- `frontend/` — Web UI (skiptrace + origination)
- `chrome-extension/` — Chrome extension

## Running Tests
```bash
# Run all tests locally (excludes golden-set integration tests)
python3.13 -m pytest tests/ -v --ignore=tests/test_golden_set.py

# Run specific test file
python3.13 -m pytest tests/test_domain_enrichment.py -v

# Golden-set tests (requires live API)
python3.13 -m pytest tests/test_golden_set.py --golden-url=<url> --golden-token=<token>
```
- Local dev uses Python 3.13 (`python3.13 -m pytest`), CI runs on 3.12
- Config: `pytest.ini`
- Test files mock `sys.modules` before importing function modules

## Linting & Formatting
```bash
ruff check gcp/ tests/           # Lint
ruff format --check gcp/ tests/  # Format check
ruff format gcp/ tests/          # Auto-format
```
- Config: `ruff.toml` — line length 120, double quotes, spaces
- Rules: F, E, W, I (isort), UP, B

## Security Scanning
```bash
bandit -r gcp/ -x gcp/functions/*/test_* --quiet
```
- nosec format: `# nosec B### — explanation` (em-dash + reason required)
- Common: B110 (bare except), B311 (random non-crypto), B324 (MD5 non-security)

## Type Checking
```bash
mypy gcp/shared/
```
- Config: `mypy.ini` — `ignore_missing_imports = True`

## CI Pipeline (.github/workflows/ci.yml)
Triggers on push to main + PRs. All jobs run on Ubuntu + Python 3.12:
1. **test** — `pytest tests/ -v --ignore=tests/test_golden_set.py`
2. **lint** — `ruff check` + `ruff format --check`
3. **security** — `bandit -r gcp/`
4. **typecheck** — `mypy gcp/shared/`
5. **dep-audit** — `pip-audit` on all requirements.txt files
6. **terraform-validate** — `terraform fmt -check -recursive terraform/`

## Deployment to Prod (mikiri-demo-test)

### Step 1: Prepare shared utilities
```bash
bash scripts/prepare-functions.sh
```
Copies files from `gcp/shared/` into each function directory. Must run before deploy.

### Step 2: Terraform apply
```bash
cd terraform/environments/prod
terraform plan    # Review changes
terraform apply   # Deploy
```

### Targeted deployment (only changed functions)
```bash
cd terraform/environments/prod
terraform apply \
  -target='module.core.google_cloudfunctions2_function.<function_name>' \
  -target='module.core.google_storage_bucket_object.<function_name>'
```
Function names: `domain_enrichment`, `report_generator_origination`, `report_generator_skiptrace`, `api_gateway`, `phase1_identity`, `address_geocoding`, `company_domain_lookup`, `query_constructor`, `aggregator`, `chat_handler`, `chat_handler_origination`, `address_verification`, `contact_extraction`

### Prod config
- Project: `mikiri-demo-test`
- Region: `northamerica-northeast1`
- State bucket: `mikiri-demo-test-terraform-state`
- tfvars: `terraform/environments/prod/terraform.tfvars` (gitignored, not committed)

### Worktree deployment
`terraform.tfvars` and `backend.tf` are gitignored and won't exist in worktrees. Copy both from the main repo before deploying:
```bash
cp /Users/bradleymarks/mikiri/skip-trace-origination/terraform/environments/prod/terraform.tfvars \
   <worktree>/terraform/environments/prod/terraform.tfvars
cp /Users/bradleymarks/mikiri/skip-trace-origination/terraform/environments/prod/backend.tf \
   <worktree>/terraform/environments/prod/backend.tf
```
Then run `terraform init`, `prepare-functions.sh`, and `terraform apply` from within the worktree.

**Note:** On a fresh worktree, `prepare-functions.sh` generates new zip hashes for all functions (shared utilities not yet present), so the targeted plan may show extra functions beyond the one you changed. The content is identical to the main repo — only the targeted function will have meaningful code differences.

## Key Conventions

### Logging
Use `print()` with bracketed function name prefix. Never use Python `logging` module.
```python
print(f"[FunctionName] message here")
print(f"[FunctionName] WARNING: something went wrong")
```

### Shared utilities
Source of truth is `gcp/shared/`. Files are COPIED to function directories by `scripts/prepare-functions.sh`. Never import across function boundaries.

### Requirements
Each function has its own `requirements.txt`. All include `functions-framework==3.*`.

### Test isolation
When mocking `functions_framework` in tests, set BOTH decorators:
```python
_ff.cloud_event = lambda f: f
_ff.http = lambda f: f
```
Missing either causes cross-file test pollution via `sys.modules`.
