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
      return `<li>${text}</li>`;
    };

    renderer.link = function (href, title, text) {
      const isExternal = href && href.charAt(0) !== "#";
      const attrs = isExternal
        ? ' target="_blank" rel="noopener noreferrer"'
        : "";
      const titleAttr = title
        ? ` title="${title.replace(/"/g, "&quot;")}"`
        : "";
      return `<a href="${href}"${titleAttr}${attrs}>${text}</a>`;
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

  function makeHitsSectionsCollapsible(markdownEl) {
    if (!markdownEl || !markdownEl.querySelector) return;
    const doc = markdownEl.ownerDocument;

    function isHitsMarker(el) {
      if (!el) return false;
      const text = (el.textContent || "").trim();
      if (!/^hits$/i.test(text)) return false;
      if (el.tagName === "STRONG") return true;
      const strong = el.querySelector("strong");
      return !!(strong && /^hits$/i.test((strong.textContent || "").trim()));
    }

    function getSiblingsUntilNextHeader(header) {
      const siblings = [];
      let el = header.nextElementSibling;
      while (el && el.tagName !== "H2" && el.tagName !== "H3" && !isHitsMarker(el)) {
        siblings.push(el);
        el = el.nextElementSibling;
      }
      return siblings;
    }

    function countHitsInBlock(siblings) {
      let count = 0;
      siblings.forEach((s) => {
        const lis = s.querySelectorAll("ol > li");
        count += lis.length;
      });
      if (count > 0) return count;
      return siblings.filter((s) => s.tagName === "H3").length;
    }

    function wrapSection(header, siblings, count) {
      const details = doc.createElement("details");
      details.className = "collapsible hits-collapsible";
      const summary = doc.createElement("summary");
      const icon = doc.createElement("span");
      icon.className = "hits-toggle-icon";
      icon.setAttribute("aria-hidden", "true");

      const title = doc.createElement("span");
      title.className = "hits-title";
      title.textContent = (header.textContent || "").trim() || "Hits";

      const label = doc.createElement("span");
      label.className = "hits-toggle-label";
      label.textContent = "Show hits";

      const countEl = doc.createElement("span");
      countEl.className = "hits-count";
      countEl.textContent = count + " hits";

      summary.appendChild(icon);
      summary.appendChild(title);
      summary.appendChild(label);
      summary.appendChild(countEl);
      details.appendChild(summary);
      siblings.forEach((s) => details.appendChild(s));
      header.parentNode.replaceChild(details, header);

      details.addEventListener("toggle", () => {
        label.textContent = details.open ? "Hide hits" : "Show hits";
      });
    }

    const headersRuleA = [];
    markdownEl.querySelectorAll("h2, h3").forEach((h) => {
      if (/hits/i.test(h.textContent || "")) headersRuleA.push(h);
    });

    headersRuleA.forEach((header) => {
      const siblings = getSiblingsUntilNextHeader(header);
      const count = countHitsInBlock(siblings);
      wrapSection(header, siblings, count);
    });

    const hitsMarkers = [];
    markdownEl.querySelectorAll("strong").forEach((strong) => {
      if (!/^hits$/i.test((strong.textContent || "").trim())) return;
      const marker = strong.closest("p") || strong;
      if (marker.closest(".hits-collapsible")) return;
      hitsMarkers.push(marker);
    });

    hitsMarkers.forEach((marker) => {
      const siblings = getSiblingsUntilNextHeader(marker);
      const count = countHitsInBlock(siblings);
      if (count === 0) return;
      wrapSection(marker, siblings, count);
    });

    markdownEl.querySelectorAll("h3").forEach((header) => {
      if (header.closest(".hits-collapsible")) return;
      const siblings = getSiblingsUntilNextHeader(header);
      if (siblings.some((s) => s.classList && s.classList.contains("hits-collapsible"))) {
        return;
      }
      const olCount = siblings.reduce((acc, s) => {
        const ols = s.querySelectorAll("ol");
        return acc + Array.from(ols).reduce((a, ol) => a + ol.querySelectorAll(":scope > li").length, 0);
      }, 0);
      if (olCount === 0) return;
      wrapSection(header, siblings, olCount);
    });
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
    const { jobId, workflowType, jobData, markdownReports, chatUrl, onSubmitFeedback, existingFeedback } = options;
    const input = jobData?.input || {};
    const name = input.full_name || jobData?.full_name || "Unknown";
    const city = input.city || jobData?.city || "";
    const createdAt = parseCreatedAt(
      jobData?.created_at ?? jobData?.started_at ?? jobData?.completed_at
    );

    // Humorous feedback response messages
    const positiveFeedbackMessages = [
      "You're making us blush! 🎉",
      "High five! We'll keep the good vibes coming.",
      "Aw shucks, you're too kind! 🌟",
      "We knew you'd love it. Okay, we hoped. Okay, we prayed.",
      "Nailed it! Our algorithm just did a little victory dance.",
      "Thanks! We'll add this to our 'Wall of Awesome' 🏆",
      "You just made an AI very happy. Possibly sentient now.",
      "We're blushing harder than a first date. Thanks! 💕",
      "This feedback is chef's kiss. You rock!",
      "Outstanding! We're basically best friends now.",
    ];
    const negativeFeedbackMessages = [
      "Ouch. But we appreciate the honesty! 🔧",
      "Noted. Our engineers are already stress-eating. We'll do better!",
      "Fair enough. Back to the drawing board we go! 📐",
      "Thanks for keeping us humble. We'll level up! 💪",
      "Constructive criticism accepted. Coffee consumed. Improvements incoming.",
      "We hear you loud and clear. Time to work our magic! ✨",
      "Challenge accepted. We're on it! 🚀",
      "Your feedback = our fuel. Let's make this better!",
      "Not our best work? Let's fix that. Stand by! 🔨",
      "Thanks for the reality check. Improvement mode: activated.",
    ];

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

    const originationHtml = !isSkiptrace
      ? `
            <div class="origination-actions" id="reportOriginationActions">
                <div class="risk-badge" id="reportRiskBadge" style="display: none;"><span id="reportRiskLevel"></span></div>
            </div>
        `
      : "";

    const feedbackAlreadySubmitted = !!(existingFeedback && existingFeedback.rating);
    const feedbackWidgetHtml = `
                <div class="feedback-widget" id="feedbackWidget">
                    <div class="feedback-prompt" id="feedbackPrompt" ${feedbackAlreadySubmitted ? 'style="display:none"' : ""}>
                        <span class="feedback-label">Was this report helpful?</span>
                        <div class="feedback-buttons">
                            <button class="feedback-btn" data-rating="positive" title="Yes, helpful">
                                <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M6 9V17M2 11V17C2 17.5523 2.44772 18 3 18H5.5C5.77614 18 6 17.7761 6 17.5V9.5C6 9.22386 5.77614 9 5.5 9H3C2.44772 9 2 9.44772 2 10V11ZM9.5 18H14.382C15.177 18 15.871 17.454 16.062 16.682L17.809 9.682C18.072 8.628 17.277 7.6 16.189 7.6H12.4L13.1 4.11C13.169 3.763 13.074 3.405 12.842 3.133L12 2.2L7.7 7.87C7.254 8.44 7 9.15 7 9.883V16C7 17.105 7.895 18 9 18H9.5Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            </button>
                            <button class="feedback-btn" data-rating="negative" title="Not helpful">
                                <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M14 11V3M18 9V3C18 2.44772 17.5523 2 17 2H14.5C14.2239 2 14 2.22386 14 2.5V10.5C14 10.7761 14.2239 11 14.5 11H17C17.5523 11 18 10.5523 18 10V9ZM10.5 2H5.618C4.823 2 4.129 2.546 3.938 3.318L2.191 10.318C1.928 11.372 2.723 12.4 3.811 12.4H7.6L6.9 15.89C6.831 16.237 6.926 16.595 7.158 16.867L8 17.8L12.3 12.13C12.746 11.56 13 10.85 13 10.117V4C13 2.895 12.105 2 11 2H10.5Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            </button>
                        </div>
                    </div>
                    <div class="feedback-comment" id="feedbackComment" style="display:none">
                        <textarea class="feedback-textarea" id="feedbackTextarea" placeholder="Any additional comments? (optional)" maxlength="1000" rows="3"></textarea>
                        <div class="feedback-comment-actions">
                            <button class="button-secondary feedback-submit" id="feedbackSubmitBtn">Submit Feedback</button>
                        </div>
                    </div>
                    <div class="feedback-thanks" id="feedbackThanks" ${feedbackAlreadySubmitted ? "" : 'style="display:none"'}>
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M13.5 4.5L6 12L2.5 8.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        <span>Thank you for your feedback!</span>
                    </div>
                </div>`;

    container.innerHTML = `
            <div class="report-header">
                <div class="report-info">
                    <h2 id="reportTitle">${escapeHtml(name)}${
      city ? " - " + escapeHtml(city) : ""
    }</h2>
                    <p class="report-meta" id="reportMeta">Generated on ${createdAt}</p>
                </div>
                <div class="action-bar">
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
                ${feedbackWidgetHtml}
            </div>
            <nav class="report-tabs" id="reportTabs">${tabsHtml}</nav>
            <div class="tab-panels" id="reportTabPanels">${panelsHtml}</div>
        `;

    container.querySelectorAll(".tab-panel .markdown-content").forEach(makeHitsSectionsCollapsible);

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

    // Feedback widget event handling
    if (onSubmitFeedback && !feedbackAlreadySubmitted) {
      let selectedRating = null;
      const feedbackPrompt = container.querySelector("#feedbackPrompt");
      const feedbackComment = container.querySelector("#feedbackComment");
      const feedbackThanks = container.querySelector("#feedbackThanks");
      const feedbackTextarea = container.querySelector("#feedbackTextarea");
      const feedbackSubmitBtn = container.querySelector("#feedbackSubmitBtn");

      container.querySelectorAll(".feedback-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          selectedRating = btn.dataset.rating;
          container.querySelectorAll(".feedback-btn").forEach((b) => b.classList.remove("selected"));
          btn.classList.add("selected");
          if (feedbackComment) feedbackComment.style.display = "block";
        });
      });

      if (feedbackSubmitBtn) {
        feedbackSubmitBtn.addEventListener("click", async () => {
          if (!selectedRating) return;
          feedbackSubmitBtn.disabled = true;
          feedbackSubmitBtn.textContent = "Submitting...";
          try {
            const comment = feedbackTextarea ? feedbackTextarea.value.trim() : "";
            await onSubmitFeedback(jobId, selectedRating, comment);
            if (feedbackPrompt) feedbackPrompt.style.display = "none";
            if (feedbackComment) feedbackComment.style.display = "none";
            const messages = selectedRating === "positive" ? positiveFeedbackMessages : negativeFeedbackMessages;
            const randomMessage = messages[Math.floor(Math.random() * messages.length)];
            const thanksText = feedbackThanks ? feedbackThanks.querySelector("span") : null;
            if (thanksText) thanksText.textContent = randomMessage;
            if (feedbackThanks) feedbackThanks.style.display = "flex";
          } catch (e) {
            console.error("Feedback submission failed:", e);
            feedbackSubmitBtn.disabled = false;
            feedbackSubmitBtn.textContent = "Submit Feedback";
          }
        });
      }
    }

    if (!isSkiptrace) {
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
