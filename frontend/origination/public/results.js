// =============================================================================
// Results Page - Shared Results Logic
// =============================================================================
// THIS FILE IS THE SOURCE OF TRUTH
// Copied to platform directories by scripts/prepare-frontend.sh
//
// Contains standalone results page logic. The default workflow type is
// configured via PlatformConfig (loaded from platform.json at runtime).
//
// Requires: platform-config.js (must be loaded first)
// =============================================================================

// ===========================
// Configuration
// ===========================
let API_URL = null;
let FIREBASE_CONFIG = null;
let db = null;
let currentJobId = null;
let currentWorkflow = null;

// DOM Elements
const elements = {
    loadingState: document.getElementById('loadingState'),
    errorState: document.getElementById('errorState'),
    errorMessage: document.getElementById('errorMessage'),
    resultsContent: document.getElementById('resultsContent'),
    jobIdBreadcrumb: document.getElementById('jobIdBreadcrumb'),
};

// ===========================
// Initialization
// ===========================
async function init() {
    try {
        await loadConfig();

        initializeFirebase(FIREBASE_CONFIG);
        db = firebase.firestore();
        initSignOutButton();

        initDarkMode();

        const params = new URLSearchParams(window.location.search);
        currentJobId = params.get('job_id');
        currentWorkflow = params.get('workflow') || PlatformConfig.get('defaultWorkflow');

        if (!currentJobId) {
            throw new Error('No job_id provided in URL');
        }

        elements.jobIdBreadcrumb.textContent = currentJobId;

        await authenticateUser();

        await loadInvestigationData();

    } catch (error) {
        console.error('Initialization error:', error);
        showError(error.message);
    }
}

async function loadConfig() {
    await PlatformConfig.load();

    API_URL = PlatformConfig.apiUrl;
    FIREBASE_CONFIG = PlatformConfig.firebaseConfig;
}

async function authenticateUser() {
    return new Promise((resolve, reject) => {
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                resolve(user);
            } else {
                try {
                    await ensureSignedIn();
                    resolve(firebase.auth().currentUser);
                } catch (error) {
                    if (PlatformConfig.requireSso) {
                        showSignInRequired("Please sign in with Google to view results.", () => {
                            window.location.reload();
                        });
                    }
                    reject(error);
                }
            }
        });
    });
}

// ===========================
// Data Loading
// ===========================
async function loadInvestigationData() {
    const jobDoc = await db.collection('jobs').doc(currentJobId).get();

    if (!jobDoc.exists) {
        throw new Error('Investigation not found');
    }

    const jobData = jobDoc.data();

    // getAuthToken() is in shared-utils.js (loaded before this file)
    const markdownReports = await window.ReportRenderer.loadMarkdownReports(API_URL, currentJobId, authHeaders);

    window.ReportRenderer.renderReport(elements.resultsContent, {
        jobId: currentJobId,
        workflowType: currentWorkflow,
        jobData,
        markdownReports,
    });

    elements.loadingState.style.display = 'none';
    elements.resultsContent.style.display = 'block';
}

// ===========================
// Dark Mode
// ===========================
function initDarkMode() {
    const darkMode = localStorage.getItem('darkMode') === 'true';
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light');

    const darkModeToggle = document.getElementById('darkModeToggle');
    if (darkModeToggle) {
        darkModeToggle.addEventListener('click', () => {
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('darkMode', (next === 'dark').toString());
        });
    }
}

// ===========================
// Error Handling
// ===========================
function showError(message) {
    elements.loadingState.style.display = 'none';
    elements.resultsContent.style.display = 'none';
    elements.errorState.style.display = 'block';
    elements.errorMessage.textContent = message;
}

document.addEventListener('DOMContentLoaded', init);
