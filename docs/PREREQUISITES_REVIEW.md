# PREREQUISITES.md Complete Review

Systematic review of EVERY section to identify gaps and unclear instructions.

## Section 1: GCP Setup

### 1. Create a GCP Project
- **Status**: Manual step - cannot verify without GCP access
- **Gap**: No verification command provided (wait, it's in Verification Checklist later)
- **Action**: None needed - manual step

### 2. Enable Billing
- **Status**: Manual step - cannot verify without GCP access
- **Gap**: No verification command in this section (it's in Verification Checklist)
- **Action**: None needed - manual step

### 3. Set Up Owner/Editor Access
- **Status**: Manual step
- **Gap**: Says "Ensure your account has the following roles" but doesn't say HOW to check or grant them
- **Action**: Add command to check current roles OR clarify this is done via GCP Console

### 4. Enable Required APIs
- **Status**: Says "enabled automatically by Terraform" but also shows manual command
- **Gap**: Contradictory - says automatic but also shows manual commands. Unclear when manual is needed.
- **Action**: Clarify that Terraform enables them, but you CAN enable manually if desired

### 5. Create Terraform State Bucket
- **Status**: Has commands, verification is in Verification Checklist
- **Gap**: None - commands are clear

---

## Section 2: Local Tools

### 1. Google Cloud SDK (gcloud)
- **Status**: Has install instructions for macOS/Ubuntu/Windows
- **Gap**: Installation section is good, but Configure section references `YOUR_PROJECT_ID` placeholder without context
- **Action**: Should reference that project ID comes from Section 1 step 1

### 2. Terraform
- **Status**: Has install and verify commands
- **Gap**: None apparent

### 3. Firebase CLI
- **Status**: Has install and auth commands
- **Gap**: None apparent

### 4. Python (Optional)
- **Status**: Marked optional, has commands
- **Gap**: None apparent

---

## Section 3: API Keys & Services

### 1. Google Custom Search API
- **Status**: Manual step with clear instructions
- **Gap**: Says "Note the API key for Secret Manager" but doesn't say WHERE to note it or how to store it temporarily
- **Action**: Add guidance on storing API key temporarily (same issue as Verification Checklist)

### 2. HIBP API
- **Status**: Manual step
- **Gap**: Same as above - no guidance on storing temporarily

### 3. Google Vertex AI
- **Status**: Says enabled automatically by Terraform
- **Gap**: Says "Ensure your project has quota" - how do you verify quota?
- **Action**: Add command or link to check quota

### 4. Google Drive API
- **Status**: Optional, marked clearly
- **Gap**: None

---

## Section 4: Programmable Search Engines

### Required PSEs Table (lines 199-206)
- **Status**: Table shows simplified descriptions
- **Gap**: Table is inaccurate/misleading:
  - Says "Precision PSE: linkedin.com, twitter.com, facebook.com" but actually has 13 sites
  - Says "Recall PSE: Entire web" but actually has 22 specific sites
  - Says "Recall PSE 2: Entire web" but actually uses same PSE as Precision
  - Says "Reviews PSE: bbb.org, trustpilot.com, google.com/maps" but actually searches entire web with query enhancement
  - Says "Complaints PSE: Court sites, BBB, consumer agencies" but actually searches entire web with query enhancement
- **Action**: Either fix table OR make it clearer that details are in pse-configurations/

### Creating a PSE (lines 208-217)
- **Status**: High-level instructions
- **Gap**: This conflicts with the detailed step-by-step guides we created in pse-configurations/ - which should be followed?
- **Action**: Remove this generic section and point directly to pse-configurations/ files

### PSE Configuration Documentation (line 219-221)
- **Status**: Points to pse-configurations/ directory
- **Gap**: None - this is correct

---

## Section 5: Verification Checklist

### Status
- **Gap**: We already fixed this to add verification commands
- **Status**: ✅ Fixed in previous update

---

## CRITICAL GAPS IDENTIFIED

1. **Section 3 (Set Up Owner/Editor Access)**: No command to check/grant roles
2. **Section 4 (Enable Required APIs)**: Contradictory (automatic vs manual)
3. **Section 3 (API Keys)**: No guidance on storing keys temporarily
4. **Section 3 (Vertex AI)**: No command to verify quota
5. **Section 4 (PSE table)**: Inaccurate/misleading - contradicts detailed docs
6. **Section 4 (Creating a PSE)**: Conflicts with detailed pse-configurations/ guides

---

## Fixes Required

Before PREREQUISITES.md is bulletproof, these must be fixed.
