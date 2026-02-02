/**
 * Report Renderer - Shared module for rendering investigation reports.
 * Used by both index.html (inline) and results.html (standalone).
 */
(function (global) {
  "use strict";

  // ===========================
  // Markdown Helpers
  // ===========================
  function preprocessCallouts(markdown) {
    const calloutRegex = /^>\s*\[!(\w+)\]\s*([^\n]*)\n((?:^>.*$\n?)*)/gm;
    return markdown.replace(calloutRegex, (match, type, title, content) => {
      const icon = getCalloutIcon(type.toLowerCase());
      const contentLines = content
        .split("\n")
        .map((line) => line.replace(/^>\s?/, ""))
        .join("\n")
        .trim();
      let html = `<div class="callout callout-${type.toLowerCase()}">\n`;
      html += `<div class="callout-title">${icon} ${title.trim()}</div>\n`;
      if (contentLines) {
        html += `<div class="callout-content">\n\n${contentLines}\n\n</div>\n`;
      }
      html += `</div>\n\n`;
      return html;
    });
  }

  function preprocessWikiLinks(markdown) {
    const wikiLinkRegex = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\](#\w+)?/g;
    return markdown.replace(wikiLinkRegex, (match, page, alias, anchor) => {
      const tabId = extractTabId(page);
      const displayText = alias || extractDisplayName(page);
      const anchorSuffix = anchor || "";
      return `[${displayText}](#${tabId}${anchorSuffix})`;
    });
  }

  function stripNavigationBar(markdown) {
    const navBarRegex = /^>\s*\[!abstract\]\s*-\s*\n[\s\S]*?^-{3,}\s*\n+/gm;
    return markdown.replace(navBarRegex, "");
  }

  function extractTabId(pageName) {
    const prefix = pageName.split("___")[0].toLowerCase();
    const tabMap = {
      identity: "identity",
      skiptrace: "skiptrace",
      regulator: "regulator",
      corporate: "corporate",
      adverse_media: "adverse_media",
      borrower_summary: "identity",
      summary: "summary",
    };
    return tabMap[prefix] || "identity";
  }

  function extractDisplayName(pageName) {
    const prefix = pageName.split("___")[0];
    return prefix
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(" ");
  }

  function getCalloutIcon(type) {
    const icons = {
      abstract: "📋",
      danger: "🔴",
      warning: "⚠️",
      info: "ℹ️",
      note: "📝",
      tip: "💡",
    };
    return icons[type] || "ℹ️";
  }

  function countCheckboxes(markdown) {
    const matches = markdown ? markdown.match(/- \[ \]/g) : null;
    return matches ? matches.length : 0;
  }

  // ===========================
  // Checkbox State
  // ===========================
  function getTaskState(jobId, taskId) {
    const key = `checklist_${jobId}`;
    const state = JSON.parse(global.localStorage.getItem(key) || "{}");
    return state[taskId] !== undefined ? state[taskId] : null;
  }

  function generateTaskId(text) {
    return text.replace(/[^a-zA-Z0-9]/g, "_").substring(0, 50);
  }

  // ===========================
  // Render Markdown
  // ===========================
  function renderMarkdown(container, markdown, reportType, jobId) {
    if (!markdown) return "<p>No content available.</p>";
    if (typeof marked === "undefined")
      return "<p>Markdown renderer not loaded.</p>";

    markdown = markdown.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, "");
    markdown = stripNavigationBar(markdown);
    markdown = preprocessCallouts(markdown);
    markdown = preprocessWikiLinks(markdown);

    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: true,
      mangle: false,
    });

    const renderer = new marked.Renderer();
    renderer.blockquote = function (quote) {
      const calloutMatch = quote.match(
        /^\s*<p>\[!(\w+)\]\s*([^<]*)<\/p>([\s\S]*)/i
      );
      if (calloutMatch) {
        const [, type, title, content] = calloutMatch;
        const icon = getCalloutIcon(type.toLowerCase());
        const cleanContent = content.trim().replace(/^<p>\s*<\/p>/, "");
        return `<div class="callout callout-${type.toLowerCase()}">
                    <div class="callout-title">${icon} ${title.trim()}</div>
                    ${
                      cleanContent
                        ? `<div class="callout-content">${cleanContent}</div>`
                        : ""
                    }
                </div>`;
      }
      return `<blockquote>${quote}</blockquote>`;
    };

    renderer.listitem = function (text, task, checked) {
      if (task === true) {
        const strippedText = text
          .replace(
            /(^\s*|<p>\s*)<input\s[^>]*type\s*=\s*["']checkbox["'][^>]*>\s*/i,
            "$1"
          )
          .trim();
        const taskId = generateTaskId(strippedText);
        const savedState = getTaskState(jobId, taskId);
        const isChecked = savedState !== null ? savedState : checked;
        return `<li class="task-list-item">
                    <input type="checkbox" ${
                      isChecked ? "checked" : ""
                    } data-task-id="${taskId}" data-job-id="${jobId}" />
                    <span>${strippedText}</span>
                </li>`;
      }
      return `<li>${text}</li>`;
    };

    try {
      return marked.parse(markdown, { renderer });
    } catch (e) {
      return "<p>Error rendering content.</p>";
    }
  }

  // ===========================
  // Get Tabs for Workflow
  // ===========================
  function getTabsForWorkflow(markdownReports) {
    const tabs = [];
    if (markdownReports.summary) {
      tabs.push({ id: "summary", label: "Summary" });
    }
    if (markdownReports.identity) {
      tabs.push({ id: "identity", label: "Identity" });
    }
    if (markdownReports.skiptrace) {
      const taskCount = countCheckboxes(markdownReports.skiptrace);
      tabs.push({
        id: "skiptrace",
        label: "Skip Trace Checklist",
        badge: taskCount > 0 ? taskCount : null,
      });
    }
    if (markdownReports.corporate)
      tabs.push({ id: "corporate", label: "Corporate" });
    if (markdownReports.adverse_media)
      tabs.push({ id: "adverse_media", label: "Adverse Media" });
    if (markdownReports.regulator)
      tabs.push({ id: "regulator", label: "Regulator" });
    return tabs;
  }

  // ===========================
  // Public API
  // ===========================
  async function loadMarkdownReports(apiUrl, jobId, getToken) {
    const token = await getToken();
    const response = await fetch(`${apiUrl}/get_markdown/${jobId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      if (response.status === 401) {
        const newToken = await getToken();
        const retryResponse = await fetch(`${apiUrl}/get_markdown/${jobId}`, {
          headers: { Authorization: `Bearer ${newToken}` },
        });
        if (!retryResponse.ok) {
          const err = await retryResponse.json().catch(() => ({}));
          throw new Error(err.error || "Authentication failed.");
        }
        return await retryResponse.json();
      }
      const err = await response.json().catch(() => ({}));
      throw new Error(
        err.error || `Failed to load reports: ${response.status}`
      );
    }
    return await response.json();
  }

  function parseCreatedAt(createdAt) {
    if (!createdAt) return "Unknown date";
    let date;
    if (createdAt.toDate && typeof createdAt.toDate === "function") {
      date = createdAt.toDate();
    } else if (createdAt.toMillis && typeof createdAt.toMillis === "function") {
      date = new Date(createdAt.toMillis());
    } else if (
      typeof createdAt === "object" &&
      (createdAt.seconds != null || createdAt._seconds != null)
    ) {
      const sec = createdAt.seconds ?? createdAt._seconds ?? 0;
      const ns = createdAt.nanoseconds ?? createdAt._nanoseconds ?? 0;
      date = new Date(sec * 1000 + ns / 1e6);
    } else if (typeof createdAt === "string" || typeof createdAt === "number") {
      // API returns ISO strings with redundant "+00:00Z" - normalize so Date() parses correctly
      const s =
        typeof createdAt === "string"
          ? createdAt.replace(/\+00:00Z$/, "Z")
          : String(createdAt);
      date = new Date(s);
    } else {
      date = new Date(createdAt);
    }
    const formatted = date.toLocaleDateString();
    return formatted === "Invalid Date" ? "Unknown date" : formatted;
  }

  function renderReport(container, options) {
    const { jobId, workflowType, jobData, markdownReports, chatUrl } = options;
    const input = jobData?.input || {};
    const name = input.full_name || jobData?.full_name || "Unknown";
    const city = input.city || jobData?.city || "";
    const createdAt = parseCreatedAt(
      jobData?.created_at ?? jobData?.started_at ?? jobData?.completed_at
    );

    const tabs = getTabsForWorkflow(markdownReports);
    if (tabs.length === 0) {
      container.innerHTML = "<p>No report content available.</p>";
      return;
    }

    function escapeHtml(text) {
      const div = global.document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }

    const tabsHtml = tabs
      .map(
        (tab, i) => `
            <button class="tab-button ${i === 0 ? "active" : ""}" data-tab="${
          tab.id
        }">
                ${tab.label}${
          tab.badge ? ` <span class="tab-badge">${tab.badge}</span>` : ""
        }
            </button>
        `
      )
      .join("");

    const panelsHtml = tabs
      .map((tab, i) => {
        const md =
          markdownReports[tab.id] || `# ${tab.label}\n\nNo data available.`;
        const html = renderMarkdown(container, md, tab.id, jobId);
        return `<div class="tab-panel ${i === 0 ? "active" : ""}" data-tab="${
          tab.id
        }">
                <div class="markdown-content">${html}</div>
            </div>`;
      })
      .join("");

    const isSkiptrace = workflowType === "skiptrace";
    const skipTraceHtml = isSkiptrace
      ? `
            <div class="skip-trace-actions" id="reportSkipTraceActions">
                <label class="toggle-control">
                    <input type="checkbox" id="reportChecklistToggle" checked>
                    <span>Show Checklist</span>
                </label>
                <div class="progress-indicator"><span id="reportChecklistProgress">0/0 completed</span></div>
                <button id="reportResetProgress" class="button-secondary">
                    <svg class="button-icon" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M14 8C14 11.3137 11.3137 14 8 14C4.68629 14 2 11.3137 2 8C2 4.68629 4.68629 2 8 2" stroke="currentColor" stroke-width="1.5"/><path d="M12 2V4.5H9.5" stroke="currentColor" stroke-width="1.5"/></svg>
                    Reset Progress
                </button>
            </div>
        `
      : "";

    const originationHtml = !isSkiptrace
      ? `
            <div class="origination-actions" id="reportOriginationActions">
                <div class="risk-badge" id="reportRiskBadge" style="display: none;"><span id="reportRiskLevel"></span></div>
            </div>
        `
      : "";

    container.innerHTML = `
            <div class="report-header">
                <div class="report-info">
                    <h2 id="reportTitle">${escapeHtml(name)}${
      city ? " - " + escapeHtml(city) : ""
    }</h2>
                    <p class="report-meta" id="reportMeta">Generated on ${createdAt}</p>
                </div>
                <div class="action-bar">
                    ${skipTraceHtml}
                    ${originationHtml}
                    <div class="common-actions">
                        <a id="reportOpenChatButton" href="${
                          chatUrl || "#"
                        }" target="_blank" class="button-secondary">
                            <svg class="button-icon" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M14 2H2C1.45 2 1 2.45 1 3V11C1 11.55 1.45 12 2 12H4V14.5L7 12H14C14.55 12 15 11.55 15 11V3C15 2.45 14.55 2 14 2Z" stroke="currentColor" stroke-width="1.5"/></svg>
                            Ask Questions
                        </a>
                        <button class="button-secondary" onclick="window.print()">
                            <svg class="button-icon" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M9 1H3C2.45 1 2 1.45 2 2V14C2 14.55 2.45 15 3 15H13C13.55 15 14 14.55 14 14V6L9 1Z" stroke="currentColor" stroke-width="1.5"/><path d="M9 1V6H14" stroke="currentColor" stroke-width="1.5"/></svg>
                            Export PDF
                        </button>
                    </div>
                </div>
            </div>
            <nav class="report-tabs" id="reportTabs">${tabsHtml}</nav>
            <div class="tab-panels" id="reportTabPanels">${panelsHtml}</div>
        `;

    // Tab switching
    function switchTab(tabId) {
      container
        .querySelectorAll(".tab-button")
        .forEach((btn) =>
          btn.classList.toggle("active", btn.dataset.tab === tabId)
        );
      container
        .querySelectorAll(".tab-panel")
        .forEach((p) => p.classList.toggle("active", p.dataset.tab === tabId));
    }

    container.querySelectorAll(".tab-button").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Wiki link handling (tab switching via # links)
    container.addEventListener("click", (e) => {
      const link = e.target.closest('a[href^="#"]');
      if (!link) return;
      const href = link.getAttribute("href");
      if (!href || href === "#") return;
      const tabId = href.slice(1).split("#")[0];
      if (container.querySelector(`[data-tab="${tabId}"]`)) {
        e.preventDefault();
        switchTab(tabId);
      }
    });

    // Skip trace: checklist toggle, reset, checkbox state
    if (isSkiptrace) {
      const checklistToggle = container.querySelector("#reportChecklistToggle");
      const resetBtn = container.querySelector("#reportResetProgress");
      const progressEl = container.querySelector("#reportChecklistProgress");

      const savedToggle = global.localStorage.getItem(
        "skipTraceChecklistVisible"
      );
      if (savedToggle !== null)
        checklistToggle.checked = savedToggle === "true";

      function applyVisibility() {
        const visible = checklistToggle.checked;
        global.localStorage.setItem("skipTraceChecklistVisible", visible);
        const panel = container.querySelector('[data-tab="skiptrace"]');
        if (panel) panel.style.display = visible ? "block" : "none";
        progressEl.parentElement.style.display = visible ? "block" : "none";
        resetBtn.style.display = visible ? "inline-block" : "none";
        if (!visible && panel?.classList.contains("active"))
          switchTab("identity");
      }

      checklistToggle.addEventListener("change", applyVisibility);
      applyVisibility();

      function updateProgress() {
        const cbs = container.querySelectorAll(
          '.task-list-item input[type="checkbox"]'
        );
        const total = cbs.length;
        const completed = Array.from(cbs).filter((cb) => cb.checked).length;
        progressEl.textContent = `${completed}/${total} completed (${
          total ? Math.round((completed / total) * 100) : 0
        }%)`;
      }

      container
        .querySelectorAll('.task-list-item input[type="checkbox"]')
        .forEach((cb) => {
          cb.addEventListener("change", function () {
            const key = `checklist_${jobId}`;
            const state = JSON.parse(global.localStorage.getItem(key) || "{}");
            state[this.dataset.taskId] = this.checked;
            global.localStorage.setItem(key, JSON.stringify(state));
            updateProgress();
          });
        });
      updateProgress();

      resetBtn.addEventListener("click", () => {
        if (!global.confirm("Reset all checkbox progress?")) return;
        global.localStorage.removeItem(`checklist_${jobId}`);
        container
          .querySelectorAll('.task-list-item input[type="checkbox"]')
          .forEach((cb) => {
            cb.checked = false;
          });
        updateProgress();
      });
    } else {
      // Origination: risk level
      let dangerCount = 0,
        warningCount = 0;
      Object.values(markdownReports).forEach((md) => {
        dangerCount += (md.match(/\[!danger\]/gi) || []).length;
        warningCount += (md.match(/\[!warning\]/gi) || []).length;
      });
      let riskLevel = "Low",
        riskClass = "low";
      if (dangerCount > 0) {
        riskLevel = "High";
        riskClass = "high";
      } else if (warningCount > 2) {
        riskLevel = "High";
        riskClass = "high";
      } else if (warningCount > 0) {
        riskLevel = "Medium";
        riskClass = "medium";
      }
      if (dangerCount > 0 || warningCount > 0) {
        const badge = container.querySelector("#reportRiskBadge");
        const level = container.querySelector("#reportRiskLevel");
        badge.style.display = "block";
        level.textContent = `Risk Level: ${riskLevel}`;
        level.className = `risk-level risk-${riskClass}`;
      }
      // Apply risk class to report container for box outline coloring
      container.classList.remove("risk-high", "risk-medium", "risk-low");
      container.classList.add("risk-" + riskClass);
    }
  }

  global.ReportRenderer = { loadMarkdownReports, renderReport };
})(typeof window !== "undefined" ? window : this);
