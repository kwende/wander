const select = document.querySelector("#project-select");
const selectLabel = document.querySelector("#project-select-label");
const refreshButton = document.querySelector("#refresh-button");
const dashboard = document.querySelector("#dashboard");
const template = document.querySelector("#project-template");
const query = new URLSearchParams(window.location.search);
const requestedProject = query.get("project");
const requestedItem = query.get("item");
const sharedProject = query.get("shared") === "1" ? query.get("project") : null;
const localAuthoringHosts = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
const isLocalAuthoringView = !sharedProject && localAuthoringHosts.has(window.location.hostname);
let shareBaseUrl = window.location.origin;

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

function buildShareUrl(project) {
  const url = new URL(shareBaseUrl);
  url.search = "";
  url.searchParams.set("project", project.slug);
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

function buildCodexUrl(project, item) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("project", project.slug);
  url.searchParams.set("item", item.id);
  return url.toString();
}

function createCodexLink(project, item) {
  const control = document.createElement("button");
  control.className = "codex-link";
  control.type = "button";
  control.textContent = "Codex link";
  control.title = `Copy Codex link for ${item.title}`;
  control.setAttribute("aria-label", control.title);
  control.addEventListener("click", () => copyUrl(control, buildCodexUrl(project, item), "Codex link"));
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

function renderSubtaskList(project, parentId, hierarchy) {
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
      checklistItem.appendChild(createCodexLink(project, child));
    }

    const hasChildren = hierarchy.childrenByParent.has(child.id);
    const shouldExpand = ["active", "blocked"].includes(child.status)
      || hierarchy.referencedAncestors.has(child.id);
    if (hasChildren && shouldExpand) {
      checklistItem.appendChild(renderSubtaskList(project, child.id, hierarchy));
    }
    checklist.appendChild(checklistItem);
  }
  return checklist;
}

function renderProject(project) {
  const fragment = template.content.cloneNode(true);
  const hero = fragment.querySelector(".hero");
  hero.dataset.status = project.status;
  text(fragment, "[data-field='status']", project.status);
  text(fragment, "[data-field='version']", `version ${project.version}`);
  text(fragment, "[data-field='title']", project.title);
  text(fragment, "[data-field='objective']", project.objective);
  text(fragment, "[data-field='updated']", `Updated ${formatDate(project.updated_at)}`);
  const shareLink = fragment.querySelector("[data-action='share']");
  const shareUrl = buildShareUrl(project);
  shareLink.href = shareUrl;
  shareLink.hidden = Boolean(sharedProject);
  shareLink.addEventListener("click", (event) => {
    event.preventDefault();
    copyUrl(shareLink, shareUrl, "Share");
  });

  const projectLinks = project.links || [];
  const projectLinksSection = fragment.querySelector("[data-section='project-links']");
  const projectLinksList = fragment.querySelector("[data-list='project-links']");
  if (projectLinks.length) {
    projectLinksSection.hidden = false;
    for (const link of projectLinks) {
      const anchor = document.createElement("a");
      anchor.href = link.url;
      anchor.target = "_blank";
      anchor.rel = "noreferrer";
      anchor.textContent = link.label;
      projectLinksList.appendChild(anchor);
    }
  }

  const topLevelItems = project.work_items.filter((item) => !item.parent_id);
  const hierarchy = buildWorkItemHierarchy(project.work_items);
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
        element.appendChild(createCodexLink(project, item));
      }

      const hasChildren = hierarchy.childrenByParent.has(item.id);
      const shouldExpand = ["active", "blocked"].includes(item.status)
        || hierarchy.referencedAncestors.has(item.id);
      if (hasChildren && shouldExpand) {
        element.querySelector("div").appendChild(renderSubtaskList(project, item.id, hierarchy));
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
  for (const event of project.recent_events.slice(0, 8)) {
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

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(detail.error || response.statusText);
  }
  return response.json();
}

async function loadProjects(preferredSlug = select.value) {
  try {
    const projects = await fetchJson("/api/projects");
    const visibleProjects = projects.filter(
      (project) => !["completed", "archived"].includes(project.status),
    );
    if (!visibleProjects.length) {
      select.replaceChildren();
      renderEmpty(
        "No current projects",
        "Completed projects remain safely stored in Wander's history.",
      );
      return;
    }
    select.replaceChildren(...visibleProjects.map((project) => {
      const option = document.createElement("option");
      option.value = project.slug;
      option.textContent = `${project.title} · ${project.status}`;
      return option;
    }));
    const selected = visibleProjects.some((project) => project.slug === preferredSlug)
      ? preferredSlug
      : visibleProjects[0].slug;
    select.value = selected;
    await loadProject(selected);
  } catch (error) {
    renderEmpty("Unable to load Wander", error.message);
  }
}

async function loadProject(slug) {
  try {
    renderProject(await fetchJson(`/api/projects/${encodeURIComponent(slug)}`));
  } catch (error) {
    renderEmpty("Unable to load project", error.message);
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

select.addEventListener("change", () => loadProject(select.value));
refreshButton.addEventListener("click", () => {
  if (sharedProject) loadProject(sharedProject);
  else loadProjects(select.value);
});

async function initialize() {
  await loadConfiguration();
  if (sharedProject) {
    select.hidden = true;
    selectLabel.hidden = true;
    setInterval(() => loadProject(sharedProject), 15000);
    loadProject(sharedProject);
  } else {
    setInterval(() => loadProjects(select.value), 15000);
    loadProjects(requestedProject);
  }
}

initialize();

