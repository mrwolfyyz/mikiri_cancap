// Background service worker for the extension
// Handles extension icon clicks and opens Skip Trace Intelligence Platform

// Import configuration
importScripts('config.js');

// Get the URL from config (falls back to placeholder if not set)
const SKIP_TRACE_INTELLIGENCE_URL = CONFIG?.SKIP_TRACE_INTELLIGENCE_URL || 'https://YOUR-PROJECT-skiptrace.web.app/index.html';

// Listen for extension icon clicks
chrome.action.onClicked.addListener(async (tab) => {
  console.log('[Skip Trace Extension] Extension icon clicked on tab:', tab.url);
  console.log('[Skip Trace Extension] Using platform URL:', SKIP_TRACE_INTELLIGENCE_URL);
  
  // Check if config is properly set
  if (SKIP_TRACE_INTELLIGENCE_URL.includes('YOUR-PROJECT')) {
    showErrorNotification('Extension not configured. Please update config.js with your project settings.');
    return;
  }
  
  try {
    // Extract data from the current tab
    const extractedData = await extractDataFromTab(tab.id);
    
    if (!extractedData) {
      console.error('[Skip Trace Extension] Failed to extract data');
      showErrorNotification('Unable to extract form data. Please make sure you are on the loan origination page.');
      return;
    }

    // Check if we have at least some data
    const hasData = extractedData.name || extractedData.email || extractedData.city || extractedData.company;
    if (!hasData) {
      showErrorNotification('No form data found. Please fill out the loan application form first.');
      return;
    }

    // Create server-side prefill session (no PII in tab URL)
    const url = await buildPrefillUrl(extractedData);

    console.log('[Skip Trace Extension] Opening Skip Trace Intelligence Platform (prefill hash, no PII in query)');

    await chrome.tabs.create({ url: url });
    
  } catch (error) {
    console.error('[Skip Trace Extension] Error:', error);
    showErrorNotification('An error occurred: ' + error.message);
  }
});

// Extract data from the current tab using content script
async function extractDataFromTab(tabId) {
  try {
    // Inject content script and get data
    const results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: extractFormData
    });

    if (results && results[0] && results[0].result) {
      return results[0].result;
    }

    // Alternative: Try messaging the content script
    try {
      const response = await chrome.tabs.sendMessage(tabId, { action: 'extractData' });
      if (response && response.success) {
        return response.data;
      }
    } catch (messageError) {
      console.log('[Skip Trace Extension] Content script not available, using injected script');
    }

    return null;
  } catch (error) {
    console.error('[Skip Trace Extension] Error extracting data:', error);
    return null;
  }
}

// Function to extract form data (will be injected into the page)
function extractFormData() {
  const data = {
    name: '',
    email: '',
    city: '',
    company: ''
  };

  // Extract Full Name
  const nameField = document.getElementById('applicantName') || 
                    document.querySelector('input[name="applicantName"]');
  if (nameField) {
    data.name = nameField.value.trim();
  }

  // Extract Email
  const emailField = document.getElementById('applicantEmail') || 
                     document.querySelector('input[name="applicantEmail"]');
  if (emailField) {
    data.email = emailField.value.trim();
  }

  // Extract City
  const cityField = document.getElementById('applicantCity') || 
                    document.querySelector('input[name="applicantCity"]');
  if (cityField) {
    data.city = cityField.value.trim();
  }

  // Extract Company Name
  const companyField = document.getElementById('applicantCompany') || 
                       document.querySelector('input[name="applicantCompany"]');
  if (companyField) {
    data.company = companyField.value.trim();
  }

  return data;
}

/**
 * POST prefill payload to API Gateway; open skiptrace with #prefill=<token> only.
 * Fragment is not sent to the hosting server (avoids PII in access logs).
 */
async function buildPrefillUrl(data) {
  const apiBase = (CONFIG && CONFIG.API_GATEWAY_URL) || '';
  const secret = (CONFIG && CONFIG.EXTENSION_PREFILL_SECRET) || '';

  if (!apiBase || !secret) {
    throw new Error('Extension missing API_GATEWAY_URL or EXTENSION_PREFILL_SECRET in config.js');
  }

  const body = {
    full_name: (data.name || '').trim(),
    email: (data.email || '').trim(),
    city: (data.city || '').trim(),
    company_name: (data.company || '').trim(),
  };

  const res = await fetch(`${apiBase.replace(/\/$/, '')}/extension/prefill-session`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Extension-Prefill-Secret': secret,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errJson = await res.json();
      if (errJson && errJson.error) {
        detail = errJson.error;
      }
    } catch (_) {
      /* ignore */
    }
    throw new Error(detail || 'Prefill session failed');
  }

  const out = await res.json();
  const token = out && out.token;
  if (!token || typeof token !== 'string') {
    throw new Error('Invalid response from prefill API');
  }

  const base = SKIP_TRACE_INTELLIGENCE_URL.split('#')[0];
  return `${base}#prefill=${encodeURIComponent(token)}`;
}

// Show error notification
function showErrorNotification(message) {
  chrome.notifications.create({
    type: 'basic',
    iconUrl: 'icons/icon48.png',
    title: 'Skip Trace Extension',
    message: message
  });
}
