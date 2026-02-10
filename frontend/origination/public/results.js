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

        firebase.initializeApp(FIREBASE_CONFIG);
        db = firebase.firestore();

        initDarkMode();

        const params = new URLSearchParams(window.location.search);
        currentJobId = params.get('job_id');
        currentWorkflow = params.get('workflow') || 'origination';

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
    const response = await fetch('firebase-config.json');
    if (!response.ok) {
        throw new Error('Failed to load configuration');
    }
    const config = await response.json();
    API_URL = config.apiUrl;
    FIREBASE_CONFIG = {
        apiKey: config.apiKey,
        authDomain: config.authDomain,
        projectId: config.projectId,
        storageBucket: config.storageBucket,
        messagingSenderId: config.messagingSenderId,
        appId: config.appId
    };
}

async function authenticateUser() {
    return new Promise((resolve, reject) => {
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                resolve(user);
            } else {
                try {
                    await firebase.auth().signInAnonymously();
                    resolve(firebase.auth().currentUser);
                } catch (error) {
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

    const getToken = async () => {
        const user = firebase.auth().currentUser;
        if (!user) throw new Error('Not authenticated');
        return await user.getIdToken(true);
    };

    const markdownReports = await window.ReportRenderer.loadMarkdownReports(API_URL, currentJobId, getToken);

    window.ReportRenderer.renderReport(elements.resultsContent, {
        jobId: currentJobId,
        workflowType: currentWorkflow,
        jobData,
        markdownReports,
        chatUrl: `chat.html?job_id=${currentJobId}`
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
