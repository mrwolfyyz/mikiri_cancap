// ===========================
// Configuration
// ===========================
let API_URL = null;
let FIREBASE_CONFIG = null;
let db = null;
let currentJobId = null;
let currentWorkflow = null;
let markdownReports = {};

// DOM Elements
const elements = {
    loadingState: document.getElementById('loadingState'),
    errorState: document.getElementById('errorState'),
    errorMessage: document.getElementById('errorMessage'),
    resultsContent: document.getElementById('resultsContent'),
    jobIdBreadcrumb: document.getElementById('jobIdBreadcrumb'),
    reportTitle: document.getElementById('reportTitle'),
    reportMeta: document.getElementById('reportMeta'),
    reportTabs: document.getElementById('reportTabs'),
    tabPanels: document.getElementById('tabPanels'),
    skipTraceActions: document.getElementById('skipTraceActions'),
    originationActions: document.getElementById('originationActions'),
    checklistToggle: document.getElementById('checklistToggle'),
    checklistProgress: document.getElementById('checklistProgress'),
    resetProgress: document.getElementById('resetProgress'),
    openChatButton: document.getElementById('openChatButton'),
    darkModeToggle: document.getElementById('darkModeToggle'),
    riskBadge: document.getElementById('riskBadge'),
    riskLevel: document.getElementById('riskLevel')
};

// ===========================
// Initialization
// ===========================
async function init() {
    try {
        // Load configuration
        await loadConfig();

        // Initialize Firebase
        firebase.initializeApp(FIREBASE_CONFIG);
        db = firebase.firestore();

        // Initialize dark mode
        initDarkMode();

        // Get job_id and workflow from URL parameters
        const params = new URLSearchParams(window.location.search);
        currentJobId = params.get('job_id');
        currentWorkflow = params.get('workflow') || 'skiptrace'; // default to skiptrace

        if (!currentJobId) {
            throw new Error('No job_id provided in URL');
        }

        // Update breadcrumb
        elements.jobIdBreadcrumb.textContent = currentJobId;

        // Authenticate
        await authenticateUser();

        // Load investigation data
        await loadInvestigationData();
        
        // Setup event delegation for wiki link tab switching
        setupWikiLinkHandlers();

    } catch (error) {
        console.error('Initialization error:', error);
        showError(error.message);
    }
}

// Setup event delegation for wiki link clicks (tab switching)
function setupWikiLinkHandlers() {
    // Delegate clicks on links with href starting with #
    document.addEventListener('click', (event) => {
        const link = event.target.closest('a[href^="#"]');
        if (!link) return;
        
        const href = link.getAttribute('href');
        if (!href || href === '#') return;
        
        // Extract tab ID from href (e.g., "#identity" -> "identity")
        const tabId = href.slice(1).split('#')[0]; // Handle #identity#sources -> identity
        
        // Check if this tab exists
        const tabExists = document.querySelector(`[data-tab="${tabId}"]`);
        if (tabExists) {
            event.preventDefault();
            switchTab(tabId);
        }
    });
}

// Load configuration from firebase-config.json
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

// Authenticate user
async function authenticateUser() {
    return new Promise((resolve, reject) => {
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                console.log('User authenticated:', user.uid);
                resolve(user);
            } else {
                // Anonymous sign-in
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
    try {
        // Load job data from Firestore
        const jobDoc = await db.collection('jobs').doc(currentJobId).get();

        if (!jobDoc.exists) {
            throw new Error('Investigation not found');
        }

        const jobData = jobDoc.data();

        // Update report header
        updateReportHeader(jobData);

        // Load markdown reports
        await loadMarkdownReports();

        // Setup action bar based on workflow
        setupActionBar();

        // Render tabs and content
        renderTabs();
        renderTabContent();

        // Show results
        elements.loadingState.style.display = 'none';
        elements.resultsContent.style.display = 'block';

        // Initialize checkbox state if skip trace
        if (currentWorkflow === 'skiptrace') {
            initializeCheckboxState();
            updateProgressIndicator();
        }

    } catch (error) {
        console.error('Error loading investigation data:', error);
        throw error;
    }
}

function updateReportHeader(jobData) {
    const input = jobData.input || {};
    const name = input.full_name || jobData.full_name || 'Unknown';
    const city = input.city || jobData.city || '';

    elements.reportTitle.textContent = `${name}${city ? ' - ' + city : ''}`;

    const createdAt = jobData.created_at ? new Date(jobData.created_at.toDate()).toLocaleDateString() : 'Unknown date';
    elements.reportMeta.textContent = `Generated on ${createdAt}`;

    // Setup chat button
    elements.openChatButton.href = `chat.html?job_id=${currentJobId}`;
}

async function loadMarkdownReports() {
    try {
        // Get Firebase auth token
        const user = firebase.auth().currentUser;
        if (!user) {
            throw new Error('User not authenticated');
        }
        // Force token refresh to ensure we have a valid token
        const token = await user.getIdToken(true);

        const response = await fetch(`${API_URL}/get_markdown/${currentJobId}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                // Try refresh token
                const newToken = await user.getIdToken(true);
                const retryResponse = await fetch(`${API_URL}/get_markdown/${currentJobId}`, {
                    headers: {
                        'Authorization': `Bearer ${newToken}`
                    }
                });
                if (!retryResponse.ok) {
                    const errorData = await retryResponse.json().catch(() => ({}));
                    throw new Error(errorData.error || 'Authentication failed. Please refresh the page.');
                }
                markdownReports = await retryResponse.json();
                console.log('Loaded markdown reports:', Object.keys(markdownReports));
                return;
            }
            // Get detailed error message from response
            const errorData = await response.json().catch(() => ({}));
            const errorMsg = errorData.error || `HTTP ${response.status}: ${response.statusText}`;
            console.error('API Error:', errorMsg, 'Status:', response.status);
            throw new Error(`Failed to load markdown reports: ${errorMsg}`);
        }
        markdownReports = await response.json();
        console.log('Loaded markdown reports:', Object.keys(markdownReports));
    } catch (error) {
        console.error('Error loading markdown reports:', error);
        throw error;
    }
}

// ===========================
// UI Setup
// ===========================
function setupActionBar() {
    if (currentWorkflow === 'skiptrace') {
        elements.skipTraceActions.style.display = 'flex';
        elements.originationActions.style.display = 'none';

        // Setup checklist toggle
        const savedToggle = localStorage.getItem('skipTraceChecklistVisible');
        if (savedToggle !== null) {
            elements.checklistToggle.checked = savedToggle === 'true';
        }

        elements.checklistToggle.addEventListener('change', handleChecklistToggle);
        elements.resetProgress.addEventListener('click', handleResetProgress);
    } else {
        elements.skipTraceActions.style.display = 'none';
        elements.originationActions.style.display = 'flex';

        // Calculate and display risk level
        calculateRiskLevel();
    }
}

function renderTabs() {
    const tabs = getTabsForWorkflow();
    elements.reportTabs.innerHTML = tabs.map((tab, index) => `
        <button
            class="tab-button ${index === 0 ? 'active' : ''}"
            data-tab="${tab.id}"
            onclick="switchTab('${tab.id}')"
        >
            ${tab.label}${tab.badge ? ` <span class="tab-badge">${tab.badge}</span>` : ''}
        </button>
    `).join('');
}

function getTabsForWorkflow() {
    // Build tabs dynamically based on what reports actually exist
    const tabs = [];

    // Always show Identity if it exists
    if (markdownReports.identity) {
        tabs.push({ id: 'identity', label: 'Identity' });
    }

    // Skip trace specific
    if (markdownReports.skiptrace) {
        const taskCount = countCheckboxes(markdownReports.skiptrace);
        tabs.push({
            id: 'skiptrace',
            label: 'Skip Trace Checklist',
            badge: taskCount > 0 ? taskCount : null
        });
    }

    // Origination specific (these don't actually exist yet, but prepare for future)
    if (markdownReports.corporate) {
        tabs.push({ id: 'corporate', label: 'Corporate' });
    }
    if (markdownReports.adverse_media) {
        tabs.push({ id: 'adverse_media', label: 'Adverse Media' });
    }
    if (markdownReports.regulator) {
        tabs.push({ id: 'regulator', label: 'Regulator' });
    }

    return tabs;
}

function renderTabContent() {
    const tabs = getTabsForWorkflow();
    elements.tabPanels.innerHTML = tabs.map((tab, index) => {
        const markdown = markdownReports[tab.id] || `# ${tab.label}\n\nNo data available.`;
        return `
            <div class="tab-panel ${index === 0 ? 'active' : ''}" data-tab="${tab.id}">
                <div class="markdown-content">
                    ${renderMarkdown(markdown, tab.id)}
                </div>
            </div>
        `;
    }).join('');

    // Apply checklist visibility if skip trace
    if (currentWorkflow === 'skiptrace') {
        applyChecklistVisibility();
    }
}

function switchTab(tabId) {
    // Update active tab button
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    // Update active tab panel
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.tab === tabId);
    });
}

// ===========================
// Markdown Rendering with marked.js
// ===========================

/**
 * Preprocess Obsidian-style callouts into HTML divs before marked.js parsing.
 * Handles blockquotes starting with > [!type] Title
 */
function preprocessCallouts(markdown) {
    // Match blockquotes starting with > [!type] optional_title
    // Capture all continuation lines that start with >
    const calloutRegex = /^>\s*\[!(\w+)\]\s*([^\n]*)\n((?:^>.*$\n?)*)/gm;
    
    return markdown.replace(calloutRegex, (match, type, title, content) => {
        const icon = getCalloutIcon(type.toLowerCase());
        
        // Remove leading > from content lines and trim
        const contentLines = content.split('\n')
            .map(line => line.replace(/^>\s?/, ''))
            .join('\n')
            .trim();
        
        // Build HTML callout div
        let html = `<div class="callout callout-${type.toLowerCase()}">\n`;
        html += `<div class="callout-title">${icon} ${title.trim()}</div>\n`;
        
        if (contentLines) {
            html += `<div class="callout-content">\n\n${contentLines}\n\n</div>\n`;
        }
        
        html += `</div>\n\n`;
        return html;
    });
}

/**
 * Preprocess Obsidian-style wiki links into markdown links.
 * Converts [[Page|Alias]] to [Alias](#tabId) and [[Page]] to [Page](#tabId)
 */
function preprocessWikiLinks(markdown) {
    // Match [[Page|Alias]] or [[Page]] with optional #anchor suffix
    const wikiLinkRegex = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\](#\w+)?/g;
    
    return markdown.replace(wikiLinkRegex, (match, page, alias, anchor) => {
        // Extract tab ID from page name
        const tabId = extractTabId(page);
        const displayText = alias || extractDisplayName(page);
        const anchorSuffix = anchor || '';
        
        // Convert to markdown link with tab ID as fragment
        return `[${displayText}](#${tabId}${anchorSuffix})`;
    });
}

/**
 * Strip the navigation bar block from markdown before client HTML rendering.
 * The nav bar is an [!abstract] callout followed by a horizontal rule.
 * Markdown files keep it for Obsidian; we remove it for the web UI.
 */
function stripNavigationBar(markdown) {
    // Match: > [!abstract] -\n (-> or >) content lines\n\n---\n\n
    // Skip trace uses "-> " for continuation; origination uses "> "
    const navBarRegex = /^>\s*\[!abstract\]\s*-\s*\n[\s\S]*?^-{3,}\s*\n+/gm;
    return markdown.replace(navBarRegex, '');
}

/**
 * Extract tab ID from wiki page name.
 * E.g., "Identity___Sari_Cornfield" -> "identity"
 */
function extractTabId(pageName) {
    const prefix = pageName.split('___')[0].toLowerCase();
    
    // Map page prefixes to tab IDs
    const tabMap = {
        'identity': 'identity',
        'skiptrace': 'skiptrace',
        'regulator': 'regulator',
        'corporate': 'corporate',
        'adverse_media': 'adverse_media',
        'borrower_summary': 'identity' // Summary maps to identity tab
    };
    
    return tabMap[prefix] || 'identity';
}

/**
 * Extract display name from page name if no alias provided.
 * E.g., "Identity___Sari_Cornfield" -> "Identity"
 */
function extractDisplayName(pageName) {
    const prefix = pageName.split('___')[0];
    
    // Convert snake_case to Title Case
    return prefix.split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ');
}

function renderMarkdown(markdown, reportType) {
    if (!markdown) return '<p>No content available.</p>';

    // Strip YAML front matter (tags block at beginning)
    markdown = markdown.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, '');
    
    // Strip nav bar for client display (kept in markdown for Obsidian)
    markdown = stripNavigationBar(markdown);
    
    // Preprocess Obsidian syntax before marked.js
    markdown = preprocessCallouts(markdown);
    markdown = preprocessWikiLinks(markdown);

    // Configure marked
    marked.setOptions({
        breaks: true,
        gfm: true,
        headerIds: true,
        mangle: false
    });

    // Custom renderer for callouts and task lists
    const renderer = new marked.Renderer();

    // Custom blockquote rendering for GitHub-style callouts
    renderer.blockquote = function(quote) {
        // Debug: log what we're receiving
        if (quote.includes('[!')) {
            console.log('Blockquote content:', quote.substring(0, 200));
        }

        // Match pattern: [!type] Title (with or without content)
        // The quote parameter contains HTML, so we need to extract the callout marker
        const calloutMatch = quote.match(/^\s*<p>\[!(\w+)\]\s*([^<]*)<\/p>([\s\S]*)/i);
        if (calloutMatch) {
            const [, type, title, content] = calloutMatch;
            const icon = getCalloutIcon(type.toLowerCase());
            // Clean up content - remove leading/trailing whitespace and empty paragraphs
            const cleanContent = content.trim().replace(/^<p>\s*<\/p>/, '');
            console.log('Callout matched:', type, title.substring(0, 50));
            return `
                <div class="callout callout-${type.toLowerCase()}">
                    <div class="callout-title">${icon} ${title.trim()}</div>
                    ${cleanContent ? `<div class="callout-content">${cleanContent}</div>` : ''}
                </div>
            `;
        }
        // Not a callout, return normal blockquote
        return `<blockquote>${quote}</blockquote>`;
    };

    // Custom list item rendering for task lists
    const originalListitem = renderer.listitem.bind(renderer);
    renderer.listitem = function(text, task, checked) {
        // Only treat it as a task list if task is explicitly true (not just truthy)
        if (task === true) {
            const taskId = generateTaskId(text);
            const savedState = getTaskState(currentJobId, taskId);
            const isChecked = savedState !== null ? savedState : checked;

            return `
                <li class="task-list-item">
                    <input
                        type="checkbox"
                        ${isChecked ? 'checked' : ''}
                        data-task-id="${taskId}"
                        onchange="saveTaskState('${currentJobId}', '${taskId}', this.checked)"
                    />
                    <span>${text}</span>
                </li>
            `;
        }
        // Regular list item
        return `<li>${text}</li>`;
    };

    try {
        return marked.parse(markdown, { renderer });
    } catch (error) {
        console.error('Markdown rendering error:', error);
        return `<p>Error rendering markdown content.</p>`;
    }
}

function getCalloutIcon(type) {
    const icons = {
        'abstract': '📋',
        'danger': '🔴',
        'warning': '⚠️',
        'info': 'ℹ️',
        'note': '📝',
        'tip': '💡'
    };
    return icons[type] || 'ℹ️';
}

// ===========================
// Checkbox State Management
// ===========================
function generateTaskId(text) {
    // Create stable ID from task text
    return text.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 50);
}

function getTaskState(jobId, taskId) {
    const key = `checklist_${jobId}`;
    const state = JSON.parse(localStorage.getItem(key) || '{}');
    return state[taskId] !== undefined ? state[taskId] : null;
}

function saveTaskState(jobId, taskId, checked) {
    const key = `checklist_${jobId}`;
    const state = JSON.parse(localStorage.getItem(key) || '{}');
    state[taskId] = checked;
    localStorage.setItem(key, JSON.stringify(state));
    updateProgressIndicator();
}

function initializeCheckboxState() {
    // Attach change listeners to all checkboxes
    document.querySelectorAll('.task-list-item input[type="checkbox"]').forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            const taskId = this.dataset.taskId;
            saveTaskState(currentJobId, taskId, this.checked);
        });
    });
}

function updateProgressIndicator() {
    const checkboxes = document.querySelectorAll('.task-list-item input[type="checkbox"]');
    const total = checkboxes.length;
    const completed = Array.from(checkboxes).filter(cb => cb.checked).length;
    const percentage = total > 0 ? Math.round((completed / total) * 100) : 0;

    elements.checklistProgress.textContent = `${completed}/${total} completed (${percentage}%)`;
}

function handleChecklistToggle(event) {
    const visible = event.target.checked;
    localStorage.setItem('skipTraceChecklistVisible', visible);
    applyChecklistVisibility();
}

function applyChecklistVisibility() {
    const visible = elements.checklistToggle.checked;
    const skiptracePanel = document.querySelector('[data-tab="skiptrace"]');
    if (skiptracePanel) {
        skiptracePanel.style.display = visible ? 'block' : 'none';
    }

    // If skiptrace tab is hidden and active, switch to identity tab
    if (!visible && skiptracePanel && skiptracePanel.classList.contains('active')) {
        switchTab('identity');
    }
}

function handleResetProgress() {
    if (!confirm('Are you sure you want to reset all checkbox progress? This cannot be undone.')) {
        return;
    }

    // Clear localStorage for this job
    const key = `checklist_${currentJobId}`;
    localStorage.removeItem(key);

    // Uncheck all checkboxes
    document.querySelectorAll('.task-list-item input[type="checkbox"]').forEach(checkbox => {
        checkbox.checked = false;
    });

    // Update progress indicator
    updateProgressIndicator();
}

function countCheckboxes(markdown) {
    const matches = markdown.match(/- \[ \]/g);
    return matches ? matches.length : 0;
}

// ===========================
// Risk Level Calculation (Origination)
// ===========================
function calculateRiskLevel() {
    // Count danger and warning callouts in all reports
    let dangerCount = 0;
    let warningCount = 0;

    Object.values(markdownReports).forEach(markdown => {
        dangerCount += (markdown.match(/\[!danger\]/gi) || []).length;
        warningCount += (markdown.match(/\[!warning\]/gi) || []).length;
    });

    let riskLevel = 'Low';
    let riskClass = 'low';

    if (dangerCount > 0) {
        riskLevel = 'High';
        riskClass = 'high';
    } else if (warningCount > 2) {
        riskLevel = 'High';
        riskClass = 'high';
    } else if (warningCount > 0) {
        riskLevel = 'Medium';
        riskClass = 'medium';
    }

    if (dangerCount > 0 || warningCount > 0) {
        elements.riskBadge.style.display = 'block';
        elements.riskLevel.textContent = `Risk Level: ${riskLevel}`;
        elements.riskLevel.className = `risk-level risk-${riskClass}`;
    }
}

// ===========================
// Dark Mode
// ===========================
function initDarkMode() {
    // Get saved preference or system preference
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = savedTheme || (systemPrefersDark ? 'dark' : 'light');

    document.documentElement.setAttribute('data-theme', theme);

    elements.darkModeToggle.addEventListener('click', toggleDarkMode);
}

function toggleDarkMode() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
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

// ===========================
// Initialize on page load
// ===========================
document.addEventListener('DOMContentLoaded', init);

// Make functions globally available for onclick handlers
window.switchTab = switchTab;
window.saveTaskState = saveTaskState;
