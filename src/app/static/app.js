
let currentPlan = null;

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 3500);
}

function hideModals() {
  document.getElementById("success-modal").classList.add("hidden");
  document.getElementById("error-modal").classList.add("hidden");
}

function formatWindow(chosenWindow) {
  const start = new Date(chosenWindow.start_local);
  const end = new Date(chosenWindow.end_local);
  const fmt = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  return `${start.toLocaleString(undefined, fmt)} - ${end.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
}

function renderSuccessModal(plan, resolutionNotes) {
  currentPlan = plan;
  const container = document.getElementById("success-groups");
  container.innerHTML = "";

  if (resolutionNotes && resolutionNotes.length) {
    const notesEl = document.createElement("p");
    notesEl.className = "muted";
    notesEl.textContent = resolutionNotes.join(" ");
    container.appendChild(notesEl);
  }

  plan.groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "group-card";
    card.innerHTML = `
      <h3>Group ${group.group_number}</h3>
      <p>${formatWindow(group.chosen_window)}</p>
      <p>${group.student_ids.join(", ")}</p>
    `;
    container.appendChild(card);
  });

  if (plan.excluded_students && plan.excluded_students.length) {
    const excludedEl = document.createElement("div");
    excludedEl.className = "excluded-list";
    excludedEl.textContent =
      "Still excluded: " + plan.excluded_students.map((s) => s.student_id).join(", ");
    container.appendChild(excludedEl);
  }

  document.getElementById("override-controls").classList.add("hidden");
  document.getElementById("override-rows").innerHTML = "";
  document.getElementById("error-modal").classList.add("hidden");
  document.getElementById("success-modal").classList.remove("hidden");
}

function renderErrorModal(plan, message, resolutionNotes, allowRetry) {
  currentPlan = plan;
  document.getElementById("error-message").textContent =
    message || "The planner could not cover every student under the current constraints.";

  const excludedEl = document.getElementById("error-excluded");
  excludedEl.innerHTML = "";
  if (plan && plan.excluded_students && plan.excluded_students.length) {
    const list = document.createElement("ul");
    plan.excluded_students.forEach((s) => {
      const li = document.createElement("li");
      li.textContent = `${s.student_id}: ${s.reason}`;
      list.appendChild(li);
    });
    excludedEl.appendChild(list);
  }

  const notesEl = document.getElementById("resolution-notes");
  notesEl.innerHTML = "";
  (resolutionNotes || []).forEach((note) => {
    const li = document.createElement("li");
    li.textContent = note;
    notesEl.appendChild(li);
  });

  document.getElementById("run-resolution-btn").style.display = allowRetry ? "inline-block" : "none";
  document.getElementById("success-modal").classList.add("hidden");
  document.getElementById("error-modal").classList.remove("hidden");
}

// --- Availability calendar grid ---
// Click-and-drag slot picker, replacing one-block-at-a-time entry. Selected
// cells are converted into block_start/block_end hidden inputs right before
// the form submits, so the backend route is unchanged.

const GRID_START_HOUR = 8;
const GRID_END_HOUR = 22;
const SLOT_MINUTES = 30;
const SLOTS_PER_DAY = ((GRID_END_HOUR - GRID_START_HOUR) * 60) / SLOT_MINUTES;
const DAY_COUNT = 7;

let isPaintingGrid = false;
let paintValue = true;

// --- Existing-students' availability overlay ---
// Colors are painted directly on each .grid-cell via backgroundImage rather
// than as separate DOM overlay elements. This avoids all pointer-events and
// z-index issues: the cell itself remains the only interactive element, so
// drag-selection always works regardless of whether someone else is marked
// available in that slot.

// Perceptually distinct hues — maximally spread around the wheel, skipping
// near-yellow (50-70°) which has poor contrast at low opacity.
const STUDENT_HUES = [4, 211, 142, 31, 271, 185, 328, 88, 305, 158];

// Assign colors by sorted position so every student always gets a unique hue
// regardless of how their IDs happen to hash.
function loadStudentColors() {
  const dataEl = document.getElementById("existing-availability-data");
  if (!dataEl) return [];
  let students;
  try { students = JSON.parse(dataEl.textContent || "[]"); } catch { return []; }
  return students.map((s, i) => {
    const hue = STUDENT_HUES[i % STUDENT_HUES.length];
    return { ...s, hue, css: `hsla(${hue}, 72%, 45%, 0.42)` };
  });
}

function expandBlockToVisibleRanges(block, weekStart) {
  const ranges = [];
  const blockStart = new Date(block.start_local);
  const blockEnd = new Date(block.end_local);

  for (let day = 0; day < DAY_COUNT; day++) {
    const dayWindowStart = new Date(weekStart);
    dayWindowStart.setDate(dayWindowStart.getDate() + day);
    dayWindowStart.setHours(GRID_START_HOUR, 0, 0, 0);
    const dayWindowEnd = new Date(weekStart);
    dayWindowEnd.setDate(dayWindowEnd.getDate() + day);
    dayWindowEnd.setHours(GRID_END_HOUR, 0, 0, 0);

    const clippedStart = new Date(Math.max(blockStart.getTime(), dayWindowStart.getTime()));
    const clippedEnd = new Date(Math.min(blockEnd.getTime(), dayWindowEnd.getTime()));
    if (clippedEnd <= clippedStart) continue;

    const startMinutes = (clippedStart.getTime() - dayWindowStart.getTime()) / 60000;
    const endMinutes = (clippedEnd.getTime() - dayWindowStart.getTime()) / 60000;
    const startSlot = Math.ceil(startMinutes / SLOT_MINUTES);
    const endSlotExclusive = Math.floor(endMinutes / SLOT_MINUTES);
    if (endSlotExclusive <= startSlot) continue;

    ranges.push({ day, startSlot, endSlot: endSlotExclusive - 1 });
  }
  return ranges;
}

function renderExistingAvailabilityOverlay(weekStartLocalStr) {
  const students = loadStudentColors();
  if (!students.length) return;

  const weekStart = new Date(`${weekStartLocalStr}T00:00:00`);

  // coverage["day,slot"] = [{name, css}]
  const coverage = {};
  students.forEach((student) => {
    (student.blocks || []).forEach((block) => {
      expandBlockToVisibleRanges(block, weekStart).forEach((range) => {
        for (let slot = range.startSlot; slot <= range.endSlot; slot++) {
          const key = `${range.day},${slot}`;
          if (!coverage[key]) coverage[key] = [];
          coverage[key].push({ name: student.name, css: student.css });
        }
      });
    });
  });

  // Paint directly on each .grid-cell — no extra elements, no z-index conflicts.
  // Multiple students in the same slot appear as equal-width colour bands side by side.
  // The cell's own selected/too-short classes override via CSS specificity, so
  // the user's drag selection always wins visually.
  Object.entries(coverage).forEach(([key, participants]) => {
    const [day, slot] = key.split(",");
    const cell = document.querySelector(`.grid-cell[data-day="${day}"][data-slot="${slot}"]`);
    if (!cell) return;

    if (participants.length === 1) {
      cell.style.backgroundImage = `linear-gradient(${participants[0].css}, ${participants[0].css})`;
    } else {
      const stops = participants.flatMap((p, i) => {
        const a = (i / participants.length * 100).toFixed(1);
        const b = ((i + 1) / participants.length * 100).toFixed(1);
        return [`${p.css} ${a}%`, `${p.css} ${b}%`];
      });
      cell.style.backgroundImage = `linear-gradient(to right, ${stops.join(", ")})`;
    }
    cell.title = participants.map((p) => p.name).join(", ");
  });
}

function renderAvailabilityLegend() {
  const legendEl = document.getElementById("availability-legend");
  if (!legendEl) return;

  const students = loadStudentColors();
  if (!students.length) { legendEl.innerHTML = ""; return; }

  const swatches = students.map((s) => {
    const solidCss = `hsla(${s.hue}, 72%, 45%, 0.85)`;
    return `<span class="legend-item"><span class="legend-swatch" style="background:${solidCss}"></span>${s.name}</span>`;
  }).join("");

  const detailRows = students.map((s) => {
    const solidCss = `hsla(${s.hue}, 72%, 45%, 0.85)`;
    const blockTexts = (s.blocks || []).map((b) => {
      const start = new Date(b.start_local);
      const end = new Date(b.end_local);
      const dateFmt = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" };
      const timeFmt = { hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" };
      return `${start.toLocaleString(undefined, dateFmt)} – ${end.toLocaleTimeString(undefined, timeFmt)}`;
    });
    const blocksHtml = blockTexts.length
      ? blockTexts.map((t) => `<div class="avail-block-text">${t}</div>`).join("")
      : `<span class="muted">No blocks submitted</span>`;
    return `<div class="avail-detail-row">
      <span class="legend-swatch" style="background:${solidCss};flex-shrink:0"></span>
      <div><strong>${s.name}</strong>${blocksHtml}</div>
    </div>`;
  }).join("");

  legendEl.innerHTML = `
    <p class="muted legend-label">Submitted availability:</p>
    <div class="legend-items">${swatches}</div>
    <details id="availability-details" style="margin-top:0.6rem">
      <summary class="muted" style="cursor:pointer;font-size:0.85rem;user-select:none">Show everyone's availability in PST</summary>
      <div class="avail-details-content">${detailRows}</div>
    </details>`;
}

function renderPlanGroups(plan, studentsById) {
  const container = document.getElementById("plan-groups");
  if (!container) return;
  container.innerHTML = "";

  if (!plan.groups || !plan.groups.length) {
    const msg = document.createElement("p");
    msg.className = "muted";
    msg.textContent = "No groups could be formed with the current availability.";
    container.appendChild(msg);
  } else {
    plan.groups.forEach((group) => {
      const names = group.student_ids.map((id) => studentsById[id] || id).join(", ");
      const start = new Date(group.chosen_window.start_local);
      const end = new Date(group.chosen_window.end_local);
      const dateFmt = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
      const timeFmt = { hour: "numeric", minute: "2-digit" };
      const card = document.createElement("div");
      card.className = "group-card";
      card.innerHTML = `
        <h3>Group ${group.group_number}</h3>
        <p class="muted">${start.toLocaleString(undefined, dateFmt)} &rarr; ${end.toLocaleTimeString(undefined, timeFmt)}</p>
        <p>${names}</p>
      `;
      container.appendChild(card);
    });
  }

  if (plan.excluded_students && plan.excluded_students.length) {
    const excluded = document.createElement("p");
    excluded.className = "excluded-list";
    excluded.textContent = "Could not schedule: " +
      plan.excluded_students.map((e) => studentsById[e.student_id] || e.student_id).join(", ");
    container.appendChild(excluded);
  }
}

function initPlanSelector() {
  const dataEl = document.getElementById("all-plans-data");
  if (!dataEl) return;

  let allPlans;
  try { allPlans = JSON.parse(dataEl.textContent || "[]"); } catch { return; }
  if (!allPlans.length) return;

  const studentsById = {};
  document.querySelectorAll('select[name="student_id"] option').forEach((opt) => {
    if (opt.value) studentsById[opt.value] = opt.textContent.replace(/\s*✓\s*$/, "").trim();
  });

  const section = document.getElementById("plans-section");
  if (section) section.classList.remove("hidden");

  const selector = document.getElementById("plan-selector");
  if (selector) {
    allPlans.forEach((item, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = item.label;
      selector.appendChild(opt);
    });
    selector.addEventListener("change", () => {
      renderPlanGroups(allPlans[parseInt(selector.value, 10)].plan, studentsById);
    });
  }

  renderPlanGroups(allPlans[0].plan, studentsById);
}

function buildAvailabilityGrid(weekStartLocalStr) {
  const weekStart = new Date(`${weekStartLocalStr}T00:00:00`);
  const grid = document.getElementById("availability-grid");
  grid.innerHTML = "";
  grid.style.gridTemplateColumns = `64px repeat(${DAY_COUNT}, 1fr)`;
  // Pin every slot row to a fixed 16px and let only the header row size
  // naturally -- without this, CSS Grid's auto row-sizing inflates short
  // rows to fit tall multi-row-spanning overlay content (the per-student
  // availability blocks added below), stretching the whole grid.
  grid.style.gridTemplateRows = `auto repeat(${SLOTS_PER_DAY}, 16px)`;

  grid.appendChild(document.createElement("div"));
  for (let day = 0; day < DAY_COUNT; day++) {
    const date = new Date(weekStart);
    date.setDate(date.getDate() + day);
    const header = document.createElement("div");
    header.className = "grid-day-header";
    header.textContent = date.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    grid.appendChild(header);
  }

  for (let slot = 0; slot < SLOTS_PER_DAY; slot++) {
    const totalMinutes = GRID_START_HOUR * 60 + slot * SLOT_MINUTES;
    const label = document.createElement("div");
    label.className = "grid-time-label";
    if (totalMinutes % 60 === 0) {
      const labelDate = new Date(2000, 0, 1, 0, totalMinutes);
      label.textContent = labelDate.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      });
    }
    grid.appendChild(label);

    for (let day = 0; day < DAY_COUNT; day++) {
      const cell = document.createElement("div");
      cell.className = "grid-cell";
      cell.dataset.day = String(day);
      cell.dataset.slot = String(slot);
      grid.appendChild(cell);
    }
  }
}

function attachGridInteractions() {
  const grid = document.getElementById("availability-grid");

  grid.addEventListener("mousedown", (event) => {
    const cell = event.target.closest(".grid-cell");
    if (!cell) return;
    isPaintingGrid = true;
    paintValue = !cell.classList.contains("selected");
    cell.classList.toggle("selected", paintValue);
    updateSelectionSummary();
    event.preventDefault();
  });

  // Track the pointer via document-level mousemove + elementFromPoint rather
  // than per-cell mouseover: on a fast drag across these small (16px) cells,
  // the browser can coalesce mousemove events and skip firing mouseover on
  // an intermediate cell entirely. elementFromPoint re-resolves the cell
  // under the cursor on every mousemove tick instead of depending on enter
  // events for each individual cell.
  document.addEventListener("mousemove", (event) => {
    if (!isPaintingGrid) return;
    const target = document.elementFromPoint(event.clientX, event.clientY);
    const cell = target && target.closest(".grid-cell");
    if (!cell) return;
    cell.classList.toggle("selected", paintValue);
    updateSelectionSummary();
  });

  document.addEventListener("mouseup", () => {
    isPaintingGrid = false;
  });

  document.getElementById("clear-grid-btn").addEventListener("click", () => {
    grid.querySelectorAll(".grid-cell.selected").forEach((cell) => cell.classList.remove("selected"));
    updateSelectionSummary();
  });
}

function getMinimumBlockMinutes() {
  return parseInt(document.body.dataset.sessionMinutes, 10) || 120;
}

function computeSelectedRanges() {
  const ranges = [];
  for (let day = 0; day < DAY_COUNT; day++) {
    const selectedSlots = [];
    for (let slot = 0; slot < SLOTS_PER_DAY; slot++) {
      const cell = document.querySelector(`.grid-cell[data-day="${day}"][data-slot="${slot}"]`);
      if (cell && cell.classList.contains("selected")) {
        selectedSlots.push(slot);
      }
    }

    let rangeStart = null;
    let prevSlot = null;
    selectedSlots.forEach((slot, index) => {
      if (rangeStart === null) {
        rangeStart = slot;
      } else if (slot !== prevSlot + 1) {
        ranges.push({ day, startSlot: rangeStart, endSlot: prevSlot });
        rangeStart = slot;
      }
      prevSlot = slot;
      if (index === selectedSlots.length - 1) {
        ranges.push({ day, startSlot: rangeStart, endSlot: prevSlot });
      }
    });
  }
  return ranges;
}

function rangeDurationMinutes(range) {
  return (range.endSlot - range.startSlot + 1) * SLOT_MINUTES;
}

function highlightShortRanges(ranges) {
  document.querySelectorAll(".grid-cell.too-short").forEach((cell) => cell.classList.remove("too-short"));
  const minimumMinutes = getMinimumBlockMinutes();
  const shortRanges = ranges.filter((range) => rangeDurationMinutes(range) < minimumMinutes);
  shortRanges.forEach((range) => {
    for (let slot = range.startSlot; slot <= range.endSlot; slot++) {
      const cell = document.querySelector(`.grid-cell[data-day="${range.day}"][data-slot="${slot}"]`);
      if (cell) cell.classList.add("too-short");
    }
  });
  return shortRanges;
}

function updateSelectionSummary() {
  const ranges = computeSelectedRanges();
  const count = document.querySelectorAll("#availability-grid .grid-cell.selected").length;
  const hours = (count * SLOT_MINUTES) / 60;
  const shortRanges = highlightShortRanges(ranges);
  const summaryEl = document.getElementById("grid-selection-summary");

  if (!count) {
    summaryEl.textContent = "";
    summaryEl.classList.remove("warning");
  } else if (shortRanges.length) {
    summaryEl.textContent =
      `${hours} hour(s) selected — ${shortRanges.length} block(s) are shorter than the required ` +
      `${getMinimumBlockMinutes()} minutes (highlighted in red)`;
    summaryEl.classList.add("warning");
  } else {
    summaryEl.textContent = `${hours} hour(s) selected across the week`;
    summaryEl.classList.remove("warning");
  }
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function toDatetimeLocalString(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function slotsToBlock(weekStart, day, startSlot, endSlotInclusive) {
  const start = new Date(weekStart);
  start.setDate(start.getDate() + day);
  const startMinutes = GRID_START_HOUR * 60 + startSlot * SLOT_MINUTES;
  start.setHours(0, 0, 0, 0);
  start.setMinutes(startMinutes);

  const end = new Date(weekStart);
  end.setDate(end.getDate() + day);
  const endMinutes = GRID_START_HOUR * 60 + (endSlotInclusive + 1) * SLOT_MINUTES;
  end.setHours(0, 0, 0, 0);
  end.setMinutes(endMinutes);

  return { start: toDatetimeLocalString(start), end: toDatetimeLocalString(end) };
}

function extractBlocksFromGrid(weekStartLocalStr) {
  const weekStart = new Date(`${weekStartLocalStr}T00:00:00`);
  return computeSelectedRanges().map((range) =>
    slotsToBlock(weekStart, range.day, range.startSlot, range.endSlot)
  );
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return response.json();
}

function handlePlanResult(result, resolutionNotesOverride) {
  if (result.status === "error") {
    showToast(result.message);
    return;
  }
  const notes = resolutionNotesOverride || result.resolution_notes;
  if (result.status === "success") {
    renderSuccessModal(result.plan, notes);
  } else {
    renderErrorModal(result.plan, result.message, notes, true);
  }
}

// This same script runs on both the coordinator page (index.html) and the
// public submission page (submit.html), which doesn't have the generate/
// modal/override controls. `on()` no-ops when an id isn't present on the
// current page instead of throwing, so one file can serve both without
// per-page branching.
function on(id, eventName, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(eventName, handler);
}

document.addEventListener("DOMContentLoaded", () => {
  const weekStartLocal = document.body.dataset.weekStart;
  const tzInput = document.getElementById("viewer-timezone");
  if (tzInput) tzInput.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
  buildAvailabilityGrid(weekStartLocal);
  renderExistingAvailabilityOverlay(weekStartLocal);
  renderAvailabilityLegend();
  attachGridInteractions();
  initPlanSelector();

  on("availability-form", "submit", (event) => {
    const ranges = computeSelectedRanges();
    if (!ranges.length) {
      event.preventDefault();
      showToast("Select at least one availability block on the calendar first.");
      return;
    }

    const minimumMinutes = getMinimumBlockMinutes();
    const shortRanges = highlightShortRanges(ranges);
    if (shortRanges.length) {
      event.preventDefault();
      showToast(
        `${shortRanges.length} selected block(s) are shorter than the required ${minimumMinutes} ` +
        "minutes. Extend or remove the blocks highlighted in red before adding this student."
      );
      return;
    }

    const blocks = extractBlocksFromGrid(weekStartLocal);
    const container = document.getElementById("block-inputs");
    container.innerHTML = "";
    blocks.forEach((block) => {
      const startInput = document.createElement("input");
      startInput.type = "hidden";
      startInput.name = "block_start";
      startInput.value = block.start;
      container.appendChild(startInput);

      const endInput = document.createElement("input");
      endInput.type = "hidden";
      endInput.name = "block_end";
      endInput.value = block.end;
      container.appendChild(endInput);
    });
    // No preventDefault from here on: the hidden inputs above are appended
    // synchronously, so the browser includes them in this same submission.
  });

  const generateBtn = document.getElementById("generate-btn");
  if (generateBtn) {
    generateBtn.addEventListener("click", async () => {
      generateBtn.disabled = true;
      try {
        const result = await postJson("/generate");
        handlePlanResult(result);
      } finally {
        generateBtn.disabled = false;
      }
    });
  }

  on("run-resolution-btn", "click", async () => {
    const result = await postJson("/resolve-conflict");
    // Conflict resolution is attempted once per click; further clicks would
    // recompute the same deterministic result, so we only allow a retry if
    // the server still reports a conflict but hasn't exhausted its attempt.
    handlePlanResult(result, result.resolution_notes);
    if (result.status === "conflict") {
      document.getElementById("run-resolution-btn").style.display = "none";
    }
  });

  on("restart-btn", "click", async () => {
    await postJson("/restart");
    window.location.reload();
  });

  on("accept-plan-btn", "click", async () => {
    const result = await postJson("/plan/approve");
    if (result.status === "approved") {
      showToast("Plan approved.");
      hideModals();
    } else {
      showToast(result.message);
    }
  });

  on("reject-plan-btn", "click", async () => {
    await postJson("/plan/reject");
    showToast("Draft discarded. Availability was kept.");
    hideModals();
  });

  on("modify-plan-btn", "click", () => {
    document.getElementById("override-controls").classList.remove("hidden");
  });

  on("add-override-row", "click", () => {
    if (!currentPlan) return;
    const allStudentIds = currentPlan.groups.flatMap((g) => g.student_ids);
    const groupNumbers = currentPlan.groups.map((g) => g.group_number);

    const row = document.createElement("div");
    row.className = "override-row";
    row.innerHTML = `
      <select class="override-student">
        ${allStudentIds.map((id) => `<option value="${id}">${id}</option>`).join("")}
      </select>
      <span>to group</span>
      <select class="override-group">
        ${groupNumbers.map((n) => `<option value="${n}">${n}</option>`).join("")}
      </select>
    `;
    document.getElementById("override-rows").appendChild(row);
  });

  on("save-overrides-btn", "click", async () => {
    const rows = document.querySelectorAll("#override-rows .override-row");
    const moves = Array.from(rows).map((row) => ({
      student_id: row.querySelector(".override-student").value,
      to_group_number: parseInt(row.querySelector(".override-group").value, 10),
    }));
    if (!moves.length) return;
    const result = await postJson("/plan/override", { moves });
    if (result.status === "overridden") {
      renderSuccessModal(result.plan, ["Manual override applied."]);
      showToast("Override applied.");
    } else {
      showToast(result.message);
    }
  });
});
