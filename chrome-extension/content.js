// Content script to extract data from the loan origination form
// This script runs on the mock loan origination page

// Function to extract form data
function extractFormData() {
  const data = {
    name: '',
    email: '',
    city: '',
    company: ''
  };

  // Try multiple selectors for robustness
  // Full Name
  const nameField = document.getElementById('applicantName') || 
                    document.querySelector('input[name="applicantName"]') ||
                    document.querySelector('input[id*="name"][id*="applicant"]');
  if (nameField) {
    data.name = nameField.value.trim();
  }

  // Email
  const emailField = document.getElementById('applicantEmail') || 
                     document.querySelector('input[name="applicantEmail"]') ||
                     document.querySelector('input[type="email"]');
  if (emailField) {
    data.email = emailField.value.trim();
  }

  // City
  const cityField = document.getElementById('applicantCity') || 
                    document.querySelector('input[name="applicantCity"]') ||
                    document.querySelector('input[id*="city"]');
  if (cityField) {
    data.city = cityField.value.trim();
  }

  // Company Name
  const companyField = document.getElementById('applicantCompany') || 
                       document.querySelector('input[name="applicantCompany"]') ||
                       document.querySelector('input[id*="company"]');
  if (companyField) {
    data.company = companyField.value.trim();
  }

  return data;
}

// Listen for messages from the background script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extractData') {
    const extractedData = extractFormData();

    // Send response back to background script
    sendResponse({ success: true, data: extractedData });
    return true; // Keep message channel open for async response
  }
});

