const form = document.getElementById("generator-form");
const taskSelect = document.getElementById("task_type");
const backendSelect = document.getElementById("backend_mode");
const taskPanels = document.querySelectorAll(".task-panel");
const jobStatus = document.getElementById("job-status");
const jobId = document.getElementById("job-id");
const jobOutput = document.getElementById("job-output");
const commandPreview = document.getElementById("command-preview");
const logBox = document.getElementById("log-box");
const resultBox = document.getElementById("result-box");
const resultVideo = document.getElementById("result-video");

let activeJobId = null;
let pollHandle = null;

function showTaskPanel(task) {
  taskPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.task !== task);
  });
}

function renderJob(job) {
  if (!job) {
    return;
  }

  jobStatus.textContent = job.status || "unknown";
  jobId.textContent = job.job_id || "none";
  commandPreview.textContent = job.command || "No command yet.";
  logBox.textContent = job.logs && job.logs.length ? job.logs.join("\n") : "No logs yet.";

  if (job.status === "succeeded" && job.output_url) {
    jobOutput.textContent = job.output_path || "complete";
    resultBox.innerHTML = `<a href="${job.output_url}" target="_blank" rel="noreferrer">Open generated video</a>`;
    resultVideo.src = job.output_url;
    resultVideo.classList.remove("hidden");
  } else if (job.status === "failed") {
    jobOutput.textContent = "failed";
    resultBox.textContent = job.error || "The job failed.";
    resultVideo.classList.add("hidden");
    resultVideo.removeAttribute("src");
  } else {
    jobOutput.textContent = job.backend_mode ? `${job.status || "queued"} / ${job.backend_mode}` : (job.status || "queued");
    resultBox.textContent = "Waiting for output...";
    resultVideo.classList.add("hidden");
    resultVideo.removeAttribute("src");
  }
}

async function pollJob(jobIdValue) {
  const response = await fetch(`/api/jobs/${jobIdValue}`);
  const job = await response.json();
  renderJob(job);

  if (job.status === "queued" || job.status === "running") {
    pollHandle = window.setTimeout(() => pollJob(jobIdValue), 2000);
  }
}

taskSelect.addEventListener("change", () => {
  showTaskPanel(taskSelect.value);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (pollHandle) {
    window.clearTimeout(pollHandle);
    pollHandle = null;
  }

  const formData = new FormData(form);
  jobStatus.textContent = "submitting";
  resultBox.textContent = "Submitting job...";
  logBox.textContent = "Waiting for response...";
  resultVideo.classList.add("hidden");
  resultVideo.removeAttribute("src");

  const response = await fetch("/api/run", {
    method: "POST",
    body: formData,
  });

  const data = await response.json();
  if (!response.ok) {
    const errorMessage = data.error || "Something went wrong.";
    commandPreview.textContent = errorMessage;
    jobStatus.textContent = "failed";
    jobOutput.textContent = "failed";
    resultBox.textContent = errorMessage;
    return;
  }

  activeJobId = data.job_id;
  jobId.textContent = activeJobId;
  commandPreview.textContent = data.command || "No command returned.";
  jobStatus.textContent = data.backend_mode ? `${data.status || "queued"} / ${data.backend_mode}` : (data.status || "queued");
  jobOutput.textContent = "queued";
  resultBox.textContent = "Job started. Polling for output...";
  pollJob(activeJobId);
});

showTaskPanel(taskSelect.value);
