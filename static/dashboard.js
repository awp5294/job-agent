/* Dashboard — job cards, tabs, apply modal */
let allJobs = [];
let activeTab = 'new';
let selectedUjId = null;
let selectedApplyUrl = null;
let digestPoll = null;

/* Job titles, companies and locations come from scraped pages — never drop them
   into innerHTML unescaped. */
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function loadJobs() {
  const res = await fetch('/api/jobs');
  if (res.status === 401) { window.location.href = '/signin'; return; }
  if (!res.ok) return;
  allJobs = await res.json();
  renderTab(activeTab);
  updateCounts();
}

const STATUS_MAP = {
  new: ['new', 'sent'],
  selected: ['selected', 'applying'],
  applied: ['applied'],
};

function updateCounts() {
  Object.entries(STATUS_MAP).forEach(([tab, statuses]) => {
    const n = allJobs.filter(j => statuses.includes(j.status)).length;
    document.getElementById(`count-${tab}`).textContent = n || '';
  });
}

function switchTab(tab, btn) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.jobs-grid').forEach(g => g.style.display = 'none');
  document.getElementById(`jobs-${tab}`).style.display = 'grid';
  renderTab(tab);
}

function renderTab(tab) {
  const jobs = allJobs.filter(j => STATUS_MAP[tab].includes(j.status));
  const container = document.getElementById(`jobs-${tab}`);

  if (jobs.length === 0) {
    container.innerHTML = '';
    document.getElementById('empty-state').style.display = 'block';
    return;
  }
  document.getElementById('empty-state').style.display = 'none';
  container.innerHTML = jobs.map(j => jobCard(j, tab)).join('');
}

function jobCard(j, tab) {
  const initial = esc((j.company || '?').charAt(0).toUpperCase());
  const logoHtml = j.company_domain
    ? `<img class="company-logo" src="https://logo.clearbit.com/${encodeURIComponent(j.company_domain)}" alt="" onerror="this.style.display='none'">`
    : `<div class="company-logo-placeholder">${initial}</div>`;

  const salary = j.salary_min
    ? `$${(j.salary_min / 1000).toFixed(0)}k${j.salary_max ? '–$' + (j.salary_max / 1000).toFixed(0) + 'k' : '+'}`
    : '';

  const actions = tab === 'new'
    ? `<button class="btn-apply btn-primary" onclick="applyJob(${j.id})">Apply</button>
       <button class="btn-ignore btn-secondary" onclick="ignoreJob(${j.id}, this)">Ignore</button>`
    : tab === 'selected'
    ? `<button class="btn-apply btn-primary" onclick="openCoverLetterModal(${j.id})">View Cover Letter</button>`
    : `<a href="${esc(j.apply_url)}" target="_blank" rel="noopener noreferrer" class="btn-secondary view-posting">View Posting</a>`;

  return `<div class="job-card" id="card-${j.id}">
    <div class="job-card-top">
      ${logoHtml}
      <div>
        <div class="job-title">${esc(j.title)}</div>
        <div class="job-company">${esc(j.company)}</div>
      </div>
    </div>
    <div class="job-meta">
      ${j.location ? `<span class="meta-tag">📍 ${esc(j.location)}</span>` : ''}
      ${j.remote_type === 'remote' ? '<span class="meta-tag">🌐 Remote</span>' : ''}
      ${salary ? `<span class="meta-tag">💰 ${esc(salary)}</span>` : ''}
      <span class="score-badge">${esc(j.score)}% match</span>
    </div>
    <p class="job-reason">${esc(j.score_reason)}</p>
    <div class="job-actions">${actions}</div>
  </div>`;
}

async function applyJob(ujId) {
  showToast('Writing your cover letter...');
  let data;
  try {
    const res = await fetch(`/api/jobs/${ujId}/select`, { method: 'POST' });
    data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Could not generate a cover letter.');
      await loadJobs();
      return;
    }
  } catch (e) {
    showToast('Could not generate a cover letter.');
    return;
  }

  selectedUjId = ujId;
  selectedApplyUrl = data.apply_url;

  const job = allJobs.find(j => j.id === ujId);
  document.getElementById('modal-title').textContent =
    job ? `${job.title} @ ${job.company}` : 'Cover Letter';
  document.getElementById('modal-subtitle').textContent =
    'Review and edit before opening the application. The form opens in a new tab.';
  document.getElementById('cl-textarea').value = data.cover_letter || '';
  document.getElementById('cl-modal').style.display = 'flex';

  const idx = allJobs.findIndex(j => j.id === ujId);
  if (idx >= 0) {
    allJobs[idx].status = 'selected';
    allJobs[idx].cover_letter_text = data.cover_letter;
  }
  updateCounts();
}

async function ignoreJob(ujId, btn) {
  btn.disabled = true;
  const res = await fetch(`/api/jobs/${ujId}/ignore`, { method: 'POST' });
  if (!res.ok) { btn.disabled = false; showToast('Could not ignore that job.'); return; }
  const card = document.getElementById(`card-${ujId}`);
  if (card) card.style.opacity = '0.4';
  const idx = allJobs.findIndex(j => j.id === ujId);
  if (idx >= 0) allJobs[idx].status = 'ignored';
  updateCounts();
}

function openCoverLetterModal(ujId) {
  const job = allJobs.find(j => j.id === ujId);
  if (!job) return;
  selectedUjId = ujId;
  selectedApplyUrl = job.apply_url;
  document.getElementById('modal-title').textContent = `${job.title} @ ${job.company}`;
  document.getElementById('modal-subtitle').textContent =
    'Review your cover letter, then open the application.';
  document.getElementById('cl-textarea').value = job.cover_letter_text || '';
  document.getElementById('cl-modal').style.display = 'flex';
}

async function openApplication() {
  const cl = document.getElementById('cl-textarea').value;
  const ujId = selectedUjId;
  const url = selectedApplyUrl;

  /* Open the tab before awaiting — popup blockers reject window.open() that
     isn't in the direct click handler path. */
  if (url) window.open(url, '_blank', 'noopener');

  if (ujId) {
    await fetch(`/api/jobs/${ujId}/mark-applied`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cover_letter: cl }),
    });
    const idx = allJobs.findIndex(j => j.id === ujId);
    if (idx >= 0) { allJobs[idx].status = 'applied'; allJobs[idx].cover_letter_text = cl; }
    updateCounts();
    renderTab(activeTab);
  }
  closeModal();
}

function closeModal() {
  document.getElementById('cl-modal').style.display = 'none';
  selectedUjId = null;
  selectedApplyUrl = null;
}

/* ── Digest ─────────────────────────────────────────────────────────── */

function setDigestStatus(text) {
  const el = document.getElementById('digest-status');
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
}

async function runDigest() {
  const btn = document.getElementById('run-digest-btn');
  btn.disabled = true;
  setDigestStatus('Searching job boards and scoring matches...');
  try {
    await fetch('/api/run-digest', { method: 'POST' });
  } catch (e) {
    setDigestStatus('Could not start the digest.');
    btn.disabled = false;
    return;
  }
  if (digestPoll) clearInterval(digestPoll);
  digestPoll = setInterval(checkDigest, 4000);
}

async function checkDigest() {
  const res = await fetch('/api/digest-status');
  if (!res.ok) return;
  const s = await res.json();
  if (s.status === 'running' || s.status === 'none') return;

  clearInterval(digestPoll);
  digestPoll = null;
  document.getElementById('run-digest-btn').disabled = false;
  await loadJobs();

  if (s.status === 'error') {
    setDigestStatus(`Digest failed: ${s.message}`);
    return;
  }
  let text = `Checked ${s.jobs_found} postings, ${s.jobs_matched} matched.`;
  if (s.email_sent) text += ' Digest emailed.';
  if (s.message) text += ` Notes: ${s.message}`;
  setDigestStatus(text);
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3000);
}

loadJobs();
setInterval(loadJobs, 120000);
