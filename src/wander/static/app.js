const select = document.querySelector("#project-select");
const selectLabel = document.querySelector("#project-select-label");
const refreshButton = document.querySelector("#refresh-button");
const dashboard = document.querySelector("#dashboard");
const template = document.querySelector("#project-template");
const query = new URLSearchParams(window.location.search);
const sharedProject = query.get("shared") === "1" ? query.get("project") : null;

const markers = {
  pending: "○",
  active: "●",
  blocked: "!",
  complete: "✓",
  abandoned: "—",
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
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("project", project.slug);
  url.searchParams.set("shared", "1");
  return url.toString();
}

async function copyShareUrl(link, url) {
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
    link.textContent = "Copied";
    window.setTimeout(() => { link.textContent = "Share"; }, 1800);
  } catch {
    window.prompt("Copy this shareable link:", url);
  } finally {
    copyTarget?.remove();
  }
}

function renderEmpty(title, detail = "") {
  dashboard.innerHTML = `
    <section class="empty-state">
      <p class="eyebrow">Wander</p>
      <h2>${title}</h2>
      <p>${detail}</p>
    </section>`;
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
  shareLink.addEventListener("click", (event) => {
    event.preventDefault();
    copyShareUrl(shareLink, shareUrl);
  });

  const relevantItems = project.work_items.filter((item) => item.status !== "abandoned");
  const completed = relevantItems.filter((item) => item.status === "complete").length;
  const progress = relevantItems.length ? Math.round((completed / relevantItems.length) * 100) : 0;
  text(fragment, "[data-field='progress']", `${completed}/${relevantItems.length} complete`);
  fragment.querySelector("[data-field='progress-bar']").style.width = `${progress}%`;

  const workList = fragment.querySelector("[data-list='work-items']");
  if (!project.work_items.length) {
    workList.innerHTML = '<li class="work-notes">No work items have been added.</li>';
  } else {
    for (const item of project.work_items) {
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
    if (!projects.length) {
      select.replaceChildren();
      renderEmpty("No projects yet", "Ask Codex to create the first project through Wander's MCP tools.");
      return;
    }
    select.replaceChildren(...projects.map((project) => {
      const option = document.createElement("option");
      option.value = project.slug;
      option.textContent = `${project.title} · ${project.status}`;
      return option;
    }));
    const selected = projects.some((project) => project.slug === preferredSlug)
      ? preferredSlug
      : projects[0].slug;
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

select.addEventListener("change", () => loadProject(select.value));
refreshButton.addEventListener("click", () => {
  if (sharedProject) loadProject(sharedProject);
  else loadProjects(select.value);
});

if (sharedProject) {
  select.hidden = true;
  selectLabel.hidden = true;
  setInterval(() => loadProject(sharedProject), 15000);
  loadProject(sharedProject);
} else {
  setInterval(() => loadProjects(select.value), 15000);
  loadProjects();
}

