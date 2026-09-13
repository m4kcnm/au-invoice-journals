// --- SECURITY: CONTEXTUAL HTML ESCAPING ---
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// --- TOAST NOTIFICATIONS ---
function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add("show"), 10);
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get("toast") === "posted") {
  showToast("Bill successfully approved & posted to General Ledger.");
} else if (urlParams.get("toast") === "deleted") {
  showToast("Bill permanently deleted from records.", "success");
}

// --- TAB NAVIGATION SYSTEM ---
function initTabs() {
  function activateTab(tabName) {
    if (!tabName) return;

    document.querySelectorAll(".tabs button, .tab-btn").forEach((b) => b.classList.remove("on"));
    document.querySelectorAll(".tab, .tab-pane").forEach((p) => p.classList.remove("on"));

    const targetBtn = document.querySelector(`[data-tab="${tabName}"]`);
    if (targetBtn) targetBtn.classList.add("on");

    const targetPane = document.getElementById(`tab-${tabName}`);
    if (targetPane) targetPane.classList.add("on");
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tab]");
    if (!btn) return;

    e.preventDefault();
    const tabName = btn.dataset.tab;
    activateTab(tabName);
    window.location.hash = tabName;
  });

  if (window.location.hash) {
    const hash = window.location.hash.replace("#", "");
    activateTab(hash);
  }
}

// --- TEST RESOLUTION MAPPER ---
function initTestMapper() {
  const testBtn = document.getElementById("t-run");
  if (!testBtn) return;

  testBtn.addEventListener("click", async () => {
    const supplier = document.getElementById("t-supplier")?.value || "";
    const abn = document.getElementById("t-abn")?.value || "";
    const desc = document.getElementById("t-desc")?.value || "";
    const typeId = document.getElementById("t-type")?.value || "";
    const outBox = document.getElementById("t-out");

    if (outBox) outBox.textContent = "Resolving mapping hierarchy...";

    const q = new URLSearchParams({
      supplier: supplier,
      abn: abn,
      description: desc,
      invoice_type_id: typeId,
    });

    try {
      const res = await fetch(`/mapping/test?` + q.toString());
      const data = await res.json();
      if (outBox) outBox.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      if (outBox) outBox.textContent = "Error executing test mapper: " + err;
    }
  });
}

// --- DYNAMIC JOURNAL RECALCULATION & EXTRACTION POLLING ---
function initInvoiceDetail() {
  const invForm = document.getElementById("inv-form");
  const invContainer = document.getElementById("invoice-view");
  let debounceTimer;

  // Poll status endpoint if page loaded while invoice is extracting in background
  if (invContainer && invContainer.dataset.status === "imported") {
    const invId = invContainer.dataset.invoiceId;
    let pollCount = 0;
    const pollInterval = setInterval(async () => {
      pollCount++;
      try {
        const res = await fetch(`/invoices/${invId}/status?_t=${Date.now()}`);
        if (res.ok) {
          const data = await res.json();
          // Status updated from "imported" -> reload to populate form and proposed journal
          if (data.status && data.status !== "imported") {
            clearInterval(pollInterval);
            window.location.reload();
          }
        }
      } catch (e) {
        console.error("Status polling failed:", e);
      }
      if (pollCount > 60) {
        clearInterval(pollInterval);
      }
    }, 800);
  }

  function reindexLineInputs() {
    const tbody = document.querySelector("#lines tbody");
    if (!tbody) return;

    Array.from(tbody.rows).forEach((row, idx) => {
      const desc = row.querySelector('input[name^="line_desc_"]');
      const amt = row.querySelector('input[name^="line_amount_"]');
      const gst = row.querySelector('input[name^="line_gst_"]');
      const treat = row.querySelector('select[name^="line_treatment_"]');
      const acc = row.querySelector('select[name^="line_account_"]');

      if (desc) desc.name = `line_desc_${idx}`;
      if (amt) amt.name = `line_amount_${idx}`;
      if (gst) gst.name = `line_gst_${idx}`;
      if (treat) treat.name = `line_treatment_${idx}`;
      if (acc) acc.name = `line_account_${idx}`;
    });
  }

  async function recalculateJournal() {
    if (!invForm) return;
    reindexLineInputs();
    const formData = new FormData(invForm);
    const invId = invForm.dataset.invoiceId;

    try {
      const res = await fetch(`/invoices/${invId}/recalculate`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      renderJournalTable(data);
    } catch (e) {
      console.error("Failed to recalculate journal:", e);
    }
  }

  function renderJournalTable(data) {
    const tbody = document.querySelector("#preview-table tbody");
    const tfoot = document.querySelector("#preview-table tfoot");
    const balanceBadge = document.getElementById("journal-balance-badge");
    const postBtn = document.getElementById("btn-post-journal");
    const warningsContainer = document.getElementById("journal-warnings");

    if (warningsContainer) {
      warningsContainer.innerHTML = (data.warnings || [])
        .map((w) => `<div class="alert" style="margin-bottom: 0.6rem;">${escapeHtml(w)}</div>`)
        .join("");
    }

    if (tbody && data.lines) {
      tbody.innerHTML = data.lines
        .map(
          (ln) => `
        <tr>
          <td><strong>${escapeHtml(ln.account_code)}</strong> ${escapeHtml(ln.account_name)}</td>
          <td>${escapeHtml(ln.description)}</td>
          <td><span class="tag" style="background: #edf2f7;">${escapeHtml(ln.bas_label) || "—"}</span></td>
          <td style="text-align: right;">${ln.debit}</td>
          <td style="text-align: right;">${ln.credit}</td>
        </tr>
      `
        )
        .join("");
    }

    if (tfoot) {
      tfoot.innerHTML = `
        <tr>
          <th colspan="3">Totals</th>
          <th style="text-align: right;">${data.total_dr || "$0.00"}</th>
          <th style="text-align: right;">${data.total_cr || "$0.00"}</th>
        </tr>
      `;
    }

    if (balanceBadge) {
      balanceBadge.innerHTML = data.balanced
        ? `<span class="ok">● Balanced</span>`
        : `<span class="bad">● Out of Balance</span>`;
    }

    if (postBtn) {
      postBtn.disabled = !data.balanced;
    }
  }

  if (invForm) {
    invForm.addEventListener("input", (e) => {
      if (e.target.name === "total") {
        const tot = parseFloat(e.target.value) || 0;
        const gstInput = document.querySelector('input[name="gst_amount"]');
        const netInput = document.querySelector('input[name="subtotal_ex_gst"]');
        const isInclusive = document.querySelector('input[name="gst_inclusive"]')?.checked;

        if (tot > 0 && isInclusive) {
          const gst = (tot / 11).toFixed(2);
          const net = (tot - gst).toFixed(2);
          if (gstInput) gstInput.value = gst;
          if (netInput) netInput.value = net;
        }
      }

      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(recalculateJournal, 300);
    });
  }

  const addLine = document.getElementById("add-line");
  if (addLine) {
    addLine.addEventListener("click", () => {
      const tbody = document.querySelector("#lines tbody");
      const i = tbody.rows.length;
      const accountSelect = document.querySelector("select[name^='line_account_']");
      const treatSelect = document.querySelector("select[name^='line_treatment_']");
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input name="line_desc_${i}" placeholder="Line description" /></td>
        <td><input name="line_amount_${i}" value="0.00" style="text-align: right;" /></td>
        <td><input name="line_gst_${i}" value="0.00" style="text-align: right;" /></td>
        <td>${treatSelect ? treatSelect.outerHTML.replace(/line_treatment_\d+/, "line_treatment_" + i) : ""}</td>
        <td>${accountSelect ? accountSelect.outerHTML.replace(/line_account_\d+/, "line_account_" + i) : ""}</td>
        <td style="text-align: center;">
          <button type="button" class="btn-remove-line" title="Remove line item">&times;</button>
        </td>
      `;
      tbody.appendChild(tr);
      recalculateJournal();
    });
  }

  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("btn-remove-line")) {
      const row = e.target.closest("tr");
      if (row) {
        row.remove();
        recalculateJournal();
      }
    }
  });
}

// --- ASYNC LIVE SEARCH WITH LOADING SKELETON ---
function initSmartSearch() {
  const searchForm = document.getElementById("search-form");
  const searchInput = document.getElementById("search-input");
  const searchBtn = document.getElementById("search-btn");
  const loadingBanner = document.getElementById("search-loading");
  const resultsBody = document.getElementById("results-body");
  const intentContainer = document.getElementById("intent-container");
  const intentPre = document.getElementById("intent-pre");

  if (!searchForm || !searchInput) return;

  async function performSearch(query) {
    if (!query.trim()) {
      resultsBody.innerHTML = `
        <tr>
          <td colspan="8" class="muted" style="text-align: center; padding: 2.5rem;">
            Enter a natural language search query above to find matching records.
          </td>
        </tr>
      `;
      intentContainer.style.display = "none";
      return;
    }

    loadingBanner.style.display = "flex";
    searchBtn.disabled = true;
    searchBtn.textContent = "Searching...";

    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const data = await res.json();

      if (data.parsed_intent) {
        intentPre.textContent = JSON.stringify(data.parsed_intent, null, 2);
        intentContainer.style.display = "block";
      } else {
        intentContainer.style.display = "none";
      }

      if (data.results && data.results.length > 0) {
        resultsBody.innerHTML = data.results
          .map(
            (inv) => `
          <tr>
            <td><a href="/invoices/${inv.id}"><strong>#${inv.id}</strong></a></td>
            <td>${inv.date}</td>
            <td><a href="/invoices/${inv.id}"><strong>${escapeHtml(inv.supplier)}</strong></a></td>
            <td>${escapeHtml(inv.invoice_number)}</td>
            <td><span class="tag" style="background: #edf2f7; text-transform: none;">${escapeHtml(inv.ledger)}</span></td>
            <td style="text-align: right;">${inv.gst_amount}</td>
            <td style="text-align: right;"><strong>${inv.total}</strong></td>
            <td style="text-align: center;"><span class="tag ${inv.status}">${inv.status}</span></td>
          </tr>
        `
          )
          .join("");
      } else {
        resultsBody.innerHTML = `
          <tr>
            <td colspan="8" class="muted" style="text-align: center; padding: 2.5rem;">
              No invoices found matching "<strong>${escapeHtml(query)}</strong>".
            </td>
          </tr>
        `;
      }
    } catch (err) {
      resultsBody.innerHTML = `
        <tr>
          <td colspan="8" class="bad" style="text-align: center; padding: 2rem;">
            Failed to complete search: ${err.message}
          </td>
        </tr>
      `;
    } finally {
      loadingBanner.style.display = "none";
      searchBtn.disabled = false;
      searchBtn.textContent = "Search";
    }
  }

  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    performSearch(searchInput.value);
  });
}

// --- KEYBOARD SHORTCUTS ---
window.addEventListener("keydown", (e) => {
  const invForm = document.getElementById("inv-form");
  if ((e.metaKey || e.ctrlKey) && e.key === "s") {
    e.preventDefault();
    if (invForm) invForm.submit();
  }

  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    const postBtn = document.getElementById("btn-post-journal");
    if (postBtn && !postBtn.disabled) postBtn.click();
  }

  if (!["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    if (e.key === "]") {
      const nextLink = document.getElementById("next-invoice-link");
      if (nextLink) nextLink.click();
    }
  }
});

// --- FULL-SCREEN DRAG & DROP LISTENER ---
function initGlobalDropzone() {
  let dragCounter = 0;

  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    if (e.dataTransfer && e.dataTransfer.types && Array.from(e.dataTransfer.types).includes("Files")) {
      document.body.classList.add("dragging-file");
    }
  });

  window.addEventListener("dragover", (e) => {
    e.preventDefault();
  });

  window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      document.body.classList.remove("dragging-file");
    }
  });

  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragCounter = 0;
    document.body.classList.remove("dragging-file");

    const files = e.dataTransfer ? Array.from(e.dataTransfer.files) : [];
    const pdfFiles = files.filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));

    if (pdfFiles.length === 0) {
      showToast("Please drop a valid PDF invoice.", "bad");
      return;
    }

    showToast(`Uploading ${pdfFiles[0].name}...`, "success");

    const formData = new FormData();
    formData.append("file", pdfFiles[0]);

    try {
      const res = await fetch("/upload", {
        method: "POST",
        body: formData,
        redirect: "follow",
      });

      if (res.redirected) {
        window.location.href = res.url;
      } else {
        window.location.reload();
      }
    } catch (err) {
      showToast("Upload failed: " + err.message, "bad");
    }
  });
}

// Single Unified Bootstrapper
document.addEventListener("DOMContentLoaded", () => {
  initGlobalDropzone();
  initTabs();
  initTestMapper();
  initInvoiceDetail();
  initSmartSearch();
});

// --- ABA DOWNLOAD INTERCEPTOR ---
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('a[href="/payments/aba"]').forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const res = await fetch("/payments/aba");
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          const msg = data && data.error ? data.error : "Unable to export ABA file.";
          alert(msg);
          return;
        }

        const blob = await res.blob();
        const disposition = res.headers.get("Content-Disposition") || "";
        let filename = "PAYRUN.aba";
        const match = disposition.match(/filename=([^;]+)/);
        if (match && match[1]) {
          filename = match[1].replace(/["']/g, "").trim();
        }

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        alert("Network error attempting to export ABA file: " + err.message);
      }
    });
  });
});


// --- INVOICE DELETE INTERCEPTOR & URL ERROR TOAST ---
document.addEventListener("DOMContentLoaded", () => {
  // Check URL query parameters for redirected error messages
  const params = new URLSearchParams(window.location.search);
  if (params.has("error")) {
    alert(params.get("error"));
    // Clean URL without reloading
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, document.title, cleanUrl);
  }

  // Intercept any delete button forms
  document.querySelectorAll('form[action*="/delete"]').forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      
      const isInvoice = form.action.includes("/invoices/");
      const confirmMsg = isInvoice
        ? "Are you sure you want to delete this invoice? This action cannot be undone."
        : "Are you sure you want to delete this record?";
        
      if (!confirm(confirmMsg)) {
        return;
      }

      try {
        const res = await fetch(form.action, {
          method: "POST",
          headers: { "X-Requested-With": "XMLHttpRequest" }
        });

        // If server redirected back to an error parameter
        if (res.redirected && res.url.includes("error=")) {
          const urlObj = new URL(res.url);
          const errorMsg = urlObj.searchParams.get("error");
          alert(decodeURIComponent(errorMsg).replace(/\+/g, " "));
          return;
        }

        if (!res.ok) {
          const data = await res.json().catch(() => null);
          const msg = data && data.detail ? data.detail : "Deletion failed.";
          alert(msg);
          return;
        }

        // On successful deletion redirect to the destination URL
        window.location.href = res.url || "/invoices";
      } catch (err) {
        alert("Error submitting delete request: " + err.message);
      }
    });
  });
});
