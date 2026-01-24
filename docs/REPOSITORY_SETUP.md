# Repository Setup Guide

This guide explains how to set up a new GitHub repository for the Skip Trace & Origination Intelligence Platform.

## Prerequisites

- GitHub account
- Git installed locally
- Access to the `skip-trace-origination` directory

## Steps

### 1. Create a New GitHub Repository

1. Go to [GitHub](https://github.com) and sign in
2. Click the "+" icon in the top right → "New repository"
3. Fill in the repository details:
   - **Repository name**: `skip-trace-origination` (or your preferred name)
   - **Description**: "Skip Trace & Origination Intelligence Platform - GCP Serverless Deployment"
   - **Visibility**: Choose Private (recommended) or Public
   - **DO NOT** initialize with README, .gitignore, or license (we already have these)
4. Click "Create repository"

### 2. Initialize Git in the Local Directory

```bash
cd skip-trace-origination

# Initialize git repository (if not already initialized)
git init

# Add all files
git add .

# Create initial commit
git commit -m "Initial commit: Skip Trace & Origination Intelligence Platform"
```

### 3. Connect to GitHub and Push

```bash
# Add the remote repository (replace YOUR_USERNAME and YOUR_REPO_NAME)
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git

# Or if using SSH:
# git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPO_NAME.git

# Push to GitHub
git branch -M main
git push -u origin main
```

### 4. Verify the Repository

1. Go to your GitHub repository page
2. Verify that all files are present:
   - `docs/` directory with all documentation
   - `terraform/` directory with infrastructure code
   - `gcp/` directory with Cloud Functions and workflows
   - `frontend/` directory with both frontend applications
   - `scripts/` directory with deployment scripts
   - `.gitignore` file
   - `README.md` file

### 5. Set Up Branch Protection (Recommended)

For production deployments, consider setting up branch protection:

1. Go to repository Settings → Branches
2. Add a branch protection rule for `main` branch
3. Enable:
   - Require pull request reviews
   - Require status checks to pass
   - Require branches to be up to date

## What's Included

The repository includes:

- ✅ All Terraform infrastructure code
- ✅ All Cloud Functions source code
- ✅ Both frontend applications (Skip Trace and Origination)
- ✅ Complete documentation (DEPLOYMENT.md, PREREQUISITES.md, TROUBLESHOOTING.md)
- ✅ Deployment and validation scripts
- ✅ Chrome extension code
- ✅ PSE configuration guides
- ✅ `.gitignore` configured to exclude secrets and build artifacts

## What's Excluded

The `.gitignore` file ensures these are NOT committed:

- ❌ `.firebaserc` files (project-specific, generated during deployment)
- ❌ `.env` files (contains secrets)
- ❌ Terraform state files (`.tfstate`)
- ❌ Build artifacts and temporary files
- ❌ Service account keys and credentials
- ❌ IDE configuration files

## Next Steps

After setting up the repository:

1. Share the repository with your team or third-party deployer
2. Follow [PREREQUISITES.md](./PREREQUISITES.md) to set up the deployment environment
3. Follow [DEPLOYMENT.md](./DEPLOYMENT.md) to deploy the platform

## Important Notes

- **Never commit secrets**: The `.gitignore` is configured to prevent committing secrets, but always double-check before pushing
- **Project-specific files**: Files like `.firebaserc` are generated during deployment and should not be in the repository
- **Service account emails**: The frontend automatically displays the correct service account email based on the Firebase project ID loaded from `firebase-config.json`

## Troubleshooting

### "Repository already exists" error

If you get this error when trying to add the remote:
```bash
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
```

### Large file warnings

If you encounter warnings about large files:
- Check that `.gitignore` is properly configured
- Ensure build artifacts and temporary files are excluded
- Use `git rm --cached <file>` to remove accidentally tracked files

### Missing files after push

If some files are missing:
- Check `.gitignore` to ensure they're not being excluded
- Verify files exist locally: `ls -la`
- Check git status: `git status`
