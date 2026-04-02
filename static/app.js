const tokenInput = document.querySelector('#api-token');
const saveTokenButton = document.querySelector('#save-token');
const previewButton = document.querySelector('#preview-button');
const submitButton = document.querySelector('#submit-button');
const previewOutput = document.querySelector('#preview-output');
const previewSummary = document.querySelector('#preview-summary');
const activityOutput = document.querySelector('#activity-output');
const jobsOutput = document.querySelector('#jobs-output');
const refreshJobsButton = document.querySelector('#refresh-jobs');

const fields = {
  sourceHost: document.querySelector('#source-host'),
  sourcePort: document.querySelector('#source-port'),
  destinationHost: document.querySelector('#destination-host'),
  destinationPort: document.querySelector('#destination-port'),
  automap: document.querySelector('#automap'),
  syncInternalDates: document.querySelector('#sync-internal-dates'),
  delete2duplicates: document.querySelector('#delete2duplicates'),
  extraArgs: document.querySelector('#extra-args'),
  bulkLines: document.querySelector('#bulk-lines'),
};

tokenInput.value = localStorage.getItem('imap-sync-token') || '';

saveTokenButton.addEventListener('click', () => {
  localStorage.setItem('imap-sync-token', tokenInput.value.trim());
  setActivity('Saved API token in local browser storage.');
});

previewButton.addEventListener('click', () => {
  try {
    const jobs = buildJobs();
    renderPreview(jobs);
    setActivity(`Parsed ${jobs.length} job(s).`);
  } catch (error) {
    setActivity(error.message);
  }
});

submitButton.addEventListener('click', async () => {
  try {
    const jobs = buildJobs();
    renderPreview(jobs);
    setActivity(`Submitting ${jobs.length} job(s)...`);
    const response = await apiFetch('/jobs/bulk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({jobs}),
    });
    setActivity(`Queued ${response.count} job(s): ${response.ids.join(', ')}`);
    await loadJobs();
  } catch (error) {
    setActivity(error.message);
  }
});

refreshJobsButton.addEventListener('click', () => {
  loadJobs();
});

function parseExtraArgs(value) {
  return value
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function buildJobs() {
  const sourceHost = fields.sourceHost.value.trim();
  const destinationHost = fields.destinationHost.value.trim();
  if (!sourceHost || !destinationHost) {
    throw new Error('Source host and destination host are required.');
  }

  const lines = fields.bulkLines.value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));

  if (!lines.length) {
    throw new Error('Add at least one inbox line.');
  }

  const jobs = lines.map((line, index) => {
    const parts = line.split(',').map((part) => part.trim());
    if (parts.length !== 4) {
      throw new Error(`Line ${index + 1} must have 4 comma-separated values.`);
    }

    const [sourceUser, sourcePassword, destinationUser, destinationPassword] = parts;
    if (!sourceUser || !sourcePassword || !destinationUser || !destinationPassword) {
      throw new Error(`Line ${index + 1} contains an empty value.`);
    }

    return {
      source_host: sourceHost,
      source_port: Number(fields.sourcePort.value) || 993,
      source_user: sourceUser,
      source_password: sourcePassword,
      destination_host: destinationHost,
      destination_port: Number(fields.destinationPort.value) || 993,
      destination_user: destinationUser,
      destination_password: destinationPassword,
      automap: fields.automap.checked,
      sync_internal_dates: fields.syncInternalDates.checked,
      delete2duplicates: fields.delete2duplicates.checked,
      extra_args: parseExtraArgs(fields.extraArgs.value),
    };
  });

  return jobs;
}

function renderPreview(jobs) {
  const redacted = jobs.map((job) => ({
    ...job,
    source_password: '********',
    destination_password: '********',
  }));
  previewOutput.textContent = JSON.stringify(redacted, null, 2);
  previewSummary.textContent = `${jobs.length} job(s) ready for submission.`;
}

function setActivity(message) {
  activityOutput.textContent = message;
}

function getAuthHeaders() {
  const token = tokenInput.value.trim();
  if (!token) {
    return {};
  }
  return {Authorization: `Bearer ${token}`};
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...getAuthHeaders(),
      ...(options.headers || {}),
    },
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}`);
  }
  return payload;
}

async function loadJobs() {
  try {
    const payload = await apiFetch('/jobs');
    renderJobs(payload.jobs || []);
    setActivity(`Loaded ${payload.jobs.length} job(s).`);
  } catch (error) {
    setActivity(error.message);
  }
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsOutput.className = 'jobs-empty';
    jobsOutput.textContent = 'No jobs found.';
    return;
  }

  jobsOutput.className = 'jobs-grid';
  jobsOutput.innerHTML = jobs.map((job) => `
    <article class="job-card">
      <header>
        <strong>${escapeHtml(job.source_user)}</strong>
        <span class="status status-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
      </header>
      <p>${escapeHtml(job.source_host)} -> ${escapeHtml(job.destination_host)}</p>
      <p>${escapeHtml(job.destination_user)}</p>
      <p class="job-meta">id: ${escapeHtml(job.id)}</p>
      ${job.error ? `<p class="job-error">${escapeHtml(job.error)}</p>` : ''}
    </article>
  `).join('');
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

loadJobs();
