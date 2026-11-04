const select = document.querySelector("#journey-select");
const selectLabel = document.querySelector("#journey-select-label");
const refreshButton = document.querySelector("#refresh-button");
const dashboard = document.querySelector("#dashboard");
const template = document.querySelector("#journey-template");
const jauntsButton = document.querySelector("#jaunts-button");
const jauntsDialog = document.querySelector("#jaunts-dialog");
const jauntsClose = document.querySelector("#jaunts-close");
const jauntsRefresh = document.querySelector("#jaunts-refresh");
const jauntsShowCompleted = document.querySelector("#jaunts-show-completed");
const jauntsList = document.querySelector("#jaunts-list");
const jauntNew = document.querySelector("#jaunt-new");
const jauntForm = document.querySelector("#jaunt-form");
const jauntTitle = document.querySelector("#jaunt-title");
const jauntNotes = document.querySelector("#jaunt-notes");
const jauntStatus = document.querySelector("#jaunt-status");
const jauntUpdated = document.querySelector("#jaunt-updated");
const jauntMessage = document.querySelector("#jaunt-message");
const jauntSave = document.querySelector("#jaunt-save");
const jauntComplete = document.querySelector("#jaunt-complete");
const jauntReopen = document.querySelector("#jaunt-reopen");
const jauntHistorySection = document.querySelector("#jaunt-history-section");
const jauntHistory = document.querySelector("#jaunt-history");
const query = new URLSearchParams(window.location.search);
const requestedJourney = query.get("journey") || query.get("project");
const requestedItem = query.get("item");
const sharedJourney = query.get("shared") === "1" ? requestedJourney : null;
const localAuthoringHosts = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
const isLocalAuthoringView = !sharedJourney && localAuthoringHosts.has(window.location.hostname);
let shareBaseUrl = window.location.origin;
let loadedJaunts = [];
let selectedJaunt = null;

const markers = {
  pending: "○",
  active: "●",
  blocked: "!",
  complete: "✓",
  abandoned: "×",
};

function formatDate(value) {
  if (!value) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function text(root, selector, value) {
  root.querySelector(selector).textContent = value || "—";
}

function buildShareUrl(journey) {
  const url = new URL(shareBaseUrl);
  url.search = "";
  url.searchParams.set("journey", journey.slug);
  url.searchParams.set("shared", "1");
  return url.toString();
}

async function copyUrl(control, url, defaultLabel) {
  let copyTarget;
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(url);
    } else {
      copyTarget = document.createElement("textarea");
      copyTarget.value = url;
      copyTarget.setAttribute("readonly", "");
      copyTarget.style.position = "fixed";
      copyTarget.style.opacity = "0";
      document.body.appendChild(copyTarget);
      copyTarget.select();
      if (!document.execCommand("copy")) throw new Error("Copy failed");
    }
    control.textContent = "Copied";
    window.setTimeout(() => { control.textContent = defaultLabel; }, 1800);
  } catch {
    window.prompt("Copy this shareable link:", url);
  } finally {
    copyTarget?.remove();
  }
}

function buildCodexUrl(journey, item) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("journey", journey.slug);
  url.searchParams.set("item", item.id);
  return url.toString();
}

function createCodexLink(journey, item) {
  const control = document.createElement("button");
  control.className = "codex-link";
  control.type = "button";
  control.textContent = "Codex link";
  control.title = `Copy Codex link for ${item.title}`;
  control.setAttribute("aria-label", control.title);
  control.addEventListener("click", () => copyUrl(control, buildCodexUrl(journey, item), "Codex link"));
  return control;
}

function renderEmpty(title, detail = "") {
  dashboard.innerHTML = `
    <section class="empty-state">
      <p class="eyebrow">Wander</p>
      <h2>${title}</h2>
      <p>${detail}</p>
    </section>`;
}

function buildWorkItemHierarchy(workItems) {
  const itemsById = new Map(workItems.map((item) => [item.id, item]));
  const childrenByParent = new Map();
  for (const item of workItems) {
    if (!item.parent_id || item.status === "abandoned") continue;
    const children = childrenByParent.get(item.parent_id) || [];
    children.push(item);
    childrenByParent.set(item.parent_id, children);
  }

  const referencedAncestors = new Set();
  let referencedItem = itemsById.get(requestedItem);
  while (referencedItem?.parent_id) {
    referencedAncestors.add(referencedItem.parent_id);
    referencedItem = itemsById.get(referencedItem.parent_id);
  }

  return { childrenByParent, referencedAncestors };
}

function renderSubtaskList(journey, parentId, hierarchy) {
  const checklist = document.createElement("ul");
  checklist.className = "subtask-list";
  for (const child of hierarchy.childrenByParent.get(parentId) || []) {
    const checklistItem = document.createElement("li");
    checklistItem.dataset.status = child.status;
    if (child.id === requestedItem) {
      checklistItem.dataset.referenced = "true";
      checklistItem.setAttribute("aria-current", "true");
    }

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.disabled = true;
    checkbox.checked = child.status === "complete";
    checkbox.setAttribute("aria-label", `${child.title}: ${child.status}`);
    const label = document.createElement("span");
    label.className = "subtask-title";
    label.textContent = child.title;
    checklistItem.append(checkbox, label);
    if (isLocalAuthoringView) {
      checklistItem.appendChild(createCodexLink(journey, child));
    }

    const hasChildren = hierarchy.childrenByParent.has(child.id);
    const shouldExpand = ["active", "blocked"].includes(child.status)
      || hierarchy.referencedAncestors.has(child.id);
    if (hasChildren && shouldExpand) {
      checklistItem.appendChild(renderSubtaskList(journey, child.id, hierarchy));
    }
    checklist.appendChild(checklistItem);
  }
  return checklist;
}

function renderJourney(journey) {
  const fragment = template.content.cloneNode(true);
  const hero = fragment.querySelector(".hero");
  hero.dataset.status = journey.status;
  text(fragment, "[data-field='status']", journey.status);
  text(fragment, "[data-field='version']", `version ${journey.version}`);
  text(fragment, "[data-field='title']", journey.title);
  text(fragment, "[data-field='objective']", journey.objective);
  text(fragment, "[data-field='updated']", `Updated ${formatDate(journey.updated_at)}`);
  const shareLink = fragment.querySelector("[data-action='share']");
  const shareUrl = buildShareUrl(journey);
  shareLink.href = shareUrl;
  shareLink.hidden = Boolean(sharedJourney);
  shareLink.addEventListener("click", (event) => {
    event.preventDefault();
    copyUrl(shareLink, shareUrl, "Share");
  });

  const journeyLinks = journey.links || [];
  const journeyLinksSection = fragment.querySelector("[data-section='journey-links']");
  const journeyLinksList = fragment.querySelector("[data-list='journey-links']");
  if (journeyLinks.length) {
    journeyLinksSection.hidden = false;
    for (const link of journeyLinks) {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      anchor.textContent = link.label;
      journeyLinksList.appendChild(anchor);
    }
  }

  const topLevelItems = journey.work_items.filter((item) => !item.parent_id);
  const hierarchy = buildWorkItemHierarchy(journey.work_items);
  const relevantItems = topLevelItems.filter((item) => item.status !== "abandoned");
  const completed = relevantItems.filter((item) => item.status === "complete").length;
  const progress = relevantItems.length ? Math.round((completed / relevantItems.length) * 100) : 0;
  text(fragment, "[data-field='progress']", `${completed}/${relevantItems.length} complete`);
  fragment.querySelector("[data-field='progress-bar']").style.width = `${progress}%`;

  const workList = fragment.querySelector("[data-list='work-items']");
  if (!topLevelItems.length) {
    workList.innerHTML = '<li class="work-notes">No work items have been added.</li>';
  } else {
    for (const item of topLevelItems) {
      const element = document.createElement("li");
      element.className = "work-item";
      element.dataset.status = item.status;
      const detail = item.completion_evidence || item.notes || item.completion_criteria || item.status;
      element.innerHTML = `
        <span class="work-marker" aria-label="${item.status}">${markers[item.status]}</span>
        <div>
          <p class="work-title"></p>
          <p class="work-notes"></p>
        </div>`;
      element.querySelector(".work-title").textContent = item.title;
      element.querySelector(".work-notes").textContent = detail;
      if (item.id === requestedItem) {
        element.dataset.referenced = "true";
        element.setAttribute("aria-current", "true");
      }
      if (isLocalAuthoringView) {
        element.appendChild(createCodexLink(journey, item));
      }

      const hasChildren = hierarchy.childrenByParent.has(item.id);
      const shouldExpand = ["active", "blocked"].includes(item.status)
        || hierarchy.referencedAncestors.has(item.id);
      if (hasChildren && shouldExpand) {
        element.querySelector("div").appendChild(renderSubtaskList(journey, item.id, hierarchy));
      }

      if (item.links?.length) {
        const commitLinks = document.createElement("div");
        commitLinks.className = "commit-links";
        const commitLabel = document.createElement("span");
        commitLabel.textContent = "Commits:";
        commitLinks.appendChild(commitLabel);
        for (const link of item.links) {
          const anchor = document.createElement("a");
          anchor.href = link.url;
          anchor.target = "_blank";
          anchor.rel = "noreferrer";
          anchor.textContent = link.label;
          commitLinks.appendChild(anchor);
        }
        element.querySelector("div").appendChild(commitLinks);
      }
      workList.appendChild(element);
    }
  }

  const eventList = fragment.querySelector("[data-list='events']");
  for (const event of journey.recent_events.slice(0, 8)) {
    const element = document.createElement("div");
    element.className = "event";
    const summary = document.createElement("strong");
    summary.textContent = event.summary;
    const meta = document.createElement("span");
    meta.textContent = `${event.operation} · ${formatDate(event.created_at)}`;
    element.append(summary, meta);
    eventList.appendChild(element);
  }

  dashboard.replaceChildren(fragment);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(detail.error || response.statusText);
  }
  return response.json();
}

function resetJauntEditor() {
  selectedJaunt = null;
  jauntForm.reset();
  jauntTitle.readOnly = false;
  jauntNotes.readOnly = false;
  jauntStatus.textContent = "New";
  jauntStatus.dataset.status = "new";
  jauntUpdated.textContent = "";
  jauntMessage.textContent = "";
  jauntSave.textContent = "Create Jaunt";
  jauntSave.hidden = false;
  jauntComplete.hidden = true;
  jauntReopen.hidden = true;
  jauntHistorySection.hidden = true;
  for (const item of jauntsList.querySelectorAll("[aria-current='true']")) {
    item.removeAttribute("aria-current");
  }
  jauntTitle.focus();
}

function renderJauntHistory(history) {
  jauntHistory.replaceChildren();
  for (const event of history || []) {
    const entry = document.createElement("div");
    entry.className = "jaunt-history-entry";
    const summary = document.createElement("strong");
    summary.textContent = event.summary;
    const meta = document.createElement("span");
    meta.textContent = `${event.operation} · ${formatDate(event.created_at)}`;
    entry.append(summary, meta);
    jauntHistory.appendChild(entry);
  }
  jauntHistorySection.hidden = !(history || []).length;
}

function populateJauntEditor(jaunt) {
  selectedJaunt = jaunt;
  jauntTitle.value = jaunt.title;
  jauntNotes.value = jaunt.notes;
  jauntStatus.textContent = jaunt.status;
  jauntStatus.dataset.status = jaunt.status;
  jauntUpdated.textContent = `Updated ${formatDate(jaunt.updated_at)}`;
  jauntMessage.textContent = "";
  const editable = jaunt.status === "open";
  jauntTitle.readOnly = !editable;
  jauntNotes.readOnly = !editable;
  jauntSave.textContent = "Save";
  jauntSave.hidden = !editable;
  jauntComplete.hidden = !editable;
  jauntReopen.hidden = jaunt.status !== "completed";
  renderJauntHistory(jaunt.history);
}

async function selectJaunt(jauntId) {
  try {
    const jaunt = await fetchJson(`/api/jaunts/${encodeURIComponent(jauntId)}`);
    populateJauntEditor(jaunt);
    for (const item of jauntsList.querySelectorAll(".jaunt-list-item")) {
      if (item.dataset.jauntId === jauntId) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
    }
  } catch (error) {
    jauntMessage.textContent = error.message;
  }
}

function renderJauntList() {
  jauntsList.replaceChildren();
  const visible = loadedJaunts.filter(
    (jaunt) => jaunt.status === "open" || jauntsShowCompleted.checked,
  );
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "jaunts-empty";
    empty.textContent = "No active Jaunts.";
    jauntsList.appendChild(empty);
    return;
  }
  for (const jaunt of visible) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "jaunt-list-item";
    item.dataset.jauntId = jaunt.id;
    item.dataset.status = jaunt.status;
    if (selectedJaunt?.id === jaunt.id) item.setAttribute("aria-current", "true");
    const title = document.createElement("strong");
    title.textContent = jaunt.title;
    const preview = document.createElement("span");
    preview.textContent = jaunt.notes.trim().split(/\r?\n/, 1)[0] || jaunt.status;
    const updated = document.createElement("small");
    updated.textContent = formatDate(jaunt.updated_at);
    item.append(title, preview, updated);
    item.addEventListener("click", () => selectJaunt(jaunt.id));
    jauntsList.appendChild(item);
  }
}

async function loadJaunts(preferredId = selectedJaunt?.id) {
  try {
    loadedJaunts = await fetchJson("/api/jaunts");
    renderJauntList();
    if (preferredId && loadedJaunts.some((jaunt) => jaunt.id === preferredId)) {
      await selectJaunt(preferredId);
    } else if (!selectedJaunt && loadedJaunts.some((jaunt) => jaunt.status === "open")) {
      await selectJaunt(loadedJaunts.find((jaunt) => jaunt.status === "open").id);
    } else if (selectedJaunt && !loadedJaunts.some((jaunt) => jaunt.id === selectedJaunt.id)) {
      resetJauntEditor();
    }
  } catch (error) {
    jauntMessage.textContent = error.message;
  }
}

async function saveJaunt(event) {
  event.preventDefault();
  const title = jauntTitle.value;
  const notes = jauntNotes.value;
  try {
    let saved;
    if (selectedJaunt) {
      if (title === selectedJaunt.title && notes === selectedJaunt.notes) {
        jauntMessage.textContent = "No changes to save.";
        return;
      }
      saved = await fetchJson(`/api/jaunts/${encodeURIComponent(selectedJaunt.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: selectedJaunt.version, title, notes }),
      });
    } else {
      saved = await fetchJson("/api/jaunts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, notes }),
      });
    }
    populateJauntEditor(saved);
    jauntMessage.textContent = "Saved.";
    await loadJaunts(saved.id);
  } catch (error) {
    jauntMessage.textContent = error.message;
  }
}

async function transitionJaunt(action) {
  if (!selectedJaunt) return;
  try {
    const changed = await fetchJson(
      `/api/jaunts/${encodeURIComponent(selectedJaunt.id)}/${action}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: selectedJaunt.version }),
      },
    );
    populateJauntEditor(changed);
    await loadJaunts(changed.id);
  } catch (error) {
    jauntMessage.textContent = error.message;
  }
}

async function loadJourneys(preferredSlug = select.value) {
  try {
    const journeys = await fetchJson("/api/journeys");
    const visibleJourneys = journeys.filter(
      (journey) => !["completed", "archived"].includes(journey.status),
    );
    if (!visibleJourneys.length) {
      select.replaceChildren();
      renderEmpty(
        "No current journeys",
        "Completed journeys remain safely stored in Wander's history.",
      );
      return;
    }
    select.replaceChildren(...visibleJourneys.map((journey) => {
      const option = document.createElement("option");
      option.value = journey.slug;
      option.textContent = `${journey.title} · ${journey.status}`;
      return option;
    }));
    const selected = visibleJourneys.some((journey) => journey.slug === preferredSlug)
      ? preferredSlug
      : visibleJourneys[0].slug;
    select.value = selected;
    await loadJourney(selected);
  } catch (error) {
    renderEmpty("Unable to load Wander", error.message);
  }
}

async function loadJourney(slug) {
  try {
    renderJourney(await fetchJson(`/api/journeys/${encodeURIComponent(slug)}`));
  } catch (error) {
    renderEmpty("Unable to load journey", error.message);
  }
}

async function loadConfiguration() {
  try {
    const health = await fetchJson("/api/health");
    if (health.share_base_url) shareBaseUrl = health.share_base_url;
  } catch {
    shareBaseUrl = window.location.origin;
  }
}

select.addEventListener("change", () => loadJourney(select.value));
refreshButton.addEventListener("click", () => {
  if (sharedJourney) loadJourney(sharedJourney);
  else loadJourneys(select.value);
});
jauntsButton.addEventListener("click", async () => {
  jauntsDialog.showModal();
  await loadJaunts();
});
jauntsClose.addEventListener("click", () => jauntsDialog.close());
jauntsRefresh.addEventListener("click", () => loadJaunts());
jauntsShowCompleted.addEventListener("change", renderJauntList);
jauntNew.addEventListener("click", resetJauntEditor);
jauntForm.addEventListener("submit", saveJaunt);
jauntComplete.addEventListener("click", () => transitionJaunt("complete"));
jauntReopen.addEventListener("click", () => transitionJaunt("reopen"));

async function initialize() {
  await loadConfiguration();
  jauntsButton.hidden = !isLocalAuthoringView;
  if (sharedJourney) {
    select.hidden = true;
    selectLabel.hidden = true;
    setInterval(() => loadJourney(sharedJourney), 15000);
    loadJourney(sharedJourney);
  } else {
    setInterval(() => loadJourneys(select.value), 15000);
    loadJourneys(requestedJourney);
  }
}

initialize();

