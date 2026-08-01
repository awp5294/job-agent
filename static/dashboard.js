/* Dashboard — job cards, tabs, apply modal */
let allJobs = [];
let activeTab = 'new';
let selectedUjId = null;
let selectedApplyUrl = null;

async function loadJobs() {
  const res = await fetch('/api/jobs');
  allJobs = await res.json();
  renderTab(activeTab);
  updateCounts();
}

function updateCounts() {
  const counts = { new: 0, selected: 0, applied: 0 };
  allJobs.forEach(j => {
    if (j.status === 'new' || j.status === 'sent') counts.new++;
    else if (j.status === 'selected' || j.status === 'applying') counts.selected++;
    else if (j.status === 'applied') counts.applied++;
  });
  document.getElementById('count-new').textContent = counts.new || '';
  document.getElementById('count-selected').textContent = counts.selected || '';
  document.getElementById('count-applied').textContent = counts.applied || '';
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
  const statusMap = {
    new: ['new', 'sent'],
    selected: ['selected', 'applying'],
    applied: ['applied'],
  };
  const statuses = statusMap[tab];
  const jobs = allJobs.filter(j => statuses.includes(j.status));
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
  const domain = j.company_domain || '';
  const logoHtml = domain
    ? `<img class="company-logo" src="https://logo.clearbit.com/${domain}" onerror="this.outerHTML='<div class=company-logo-placeholder>${(j.company || '?')[0].toUpperCase()}</div>'">`
    : `<div class="company-logo-placeholder">${(j.company || '?')[0].toUpperCase()}</div>`;

  const salary = j.salary_min
    ? `$${(j.salary_min / 1000).toFixed(0)}k${j.salary_max ? '–$' + (j.salary_max / 1000).toFixed(0) + 'k' : '+'}`
    : '';

  const actions = tab === 'new'
    ? `<button class="job-actions btn-apply btn-primary" onclick="applyJob(${j.id})">Apply</button>
       <button class="job-actions btn-ignore btn-secondary" onclick="ignoreJob(${j.id}, this)">Ignore</button>`
    : tab === 'selected'
    ? `<button class="job-actions btn-apply btn-primary" onclick="openCoverLetterModal(${j.id})">View Cover Letter</button>`
    : `<a href="${j.apply_url}" target="_blank" class="job-actions btn-secondary" style="text-align:center;font-size:13px;padding:8px 10px;display:block;border-radius:8px">View Posting</a>`;

  return `<div class="job-card" id="card-${j.id}">
    <div class="job-card-top">
      ${logoHtml}
      <div>
        <div class="job-title">${j.title}</div>
        <div class="job-company">${j.company}</div>
      </div>
    </div>
    <div class="job-meta">
      ${j.location ? `<span class="meta-tag">📍 ${j.location}</span>` : ''}
      ${j.remote_type === 'remote' ? '<span class="meta-tag">🌐 Remote</span>' : ''}
      ${salary ? `<span class="meta-tag">💰 ${salary}</span>` : ''}
      <span class="score-badge">${j.score}% match</span>
    </div>
    <p class="job-reason">${j.score_reason || ''}</p>
    <div class="job-actions">${actions}</div>
  </div>`;
}

async function applyJob(ujId) {
  showToast('Generating cover letter...');
  const res = await fetch(`/api/jobs/${ujId}/select`, { method: 'POST' });
  const data = await res.json();
  selectedUjId = ujId;
  selectedApplyUrl = data.apply_url;

  const job = allJobs.find(j => j.id === ujId);
  document.getElementById('modal-title').textContent = job ? `${job.title} @ ${job.company}` : 'Cover Letter';
  document.getElementById('modal-subtitle').textContent = 'Review and edit before opening the application. The form will open in a new tab.';
  document.getElementById('cl-textarea').value = data.cover_letter || '(No cover letter generated)';
  document.getElementById('cl-modal').style.display = 'flex';

  // Update local state
  const idx = allJobs.findIndex(j => j.id === ujId);
  if (idx >= 0) {
    allJobs[idx].status = 'selected';
    allJobs[idx].cover_letter_text = data.cover_letter;
  }
  updateCounts();
}

async function ignoreJob(ujId, btn) {
  btn.disabled = true;
  await fetch(`/api/jobs/${ujId}/ignore`, { method: 'POST' });
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
  document.getElementById('modal-subtitle').textContent = 'Review your cover letter, then open the application.';
  document.getElementById('cl-textarea').value = job.cover_letter_text || '';
  document.getElementById('cl-modal').style.display = 'flex';
}

async function openApplication() {
  const cl = document.getElementById('cl-textarea').value;
  // Save any edits
  if (selectedUjId) {
    await fetch(`/api/jobs/${selectedUjId}/update-cover-letter`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cover_letter: cl }),
    });
    await fetch(`/api/jobs/${selectedUjId}/mark-applied`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cover_letter: cl }),
    });
    const idx = allJobs.findIndex(j => j.id === selectedUjId);
    if (idx >= 0) allJobs[idx].status = 'applied';
    updateCounts();
  }
  if (selectedApplyUrl) window.open(selectedApplyUrl, '_blank');
  closeModal();
}

function closeModal() {
  document.getElementById('cl-modal').style.display = 'none';
  selectedUjId = null;
  selectedApplyUrl = null;
}

async function runDigest() {
  showToast('Running digest... check back in ~60 seconds');
  await fetch('/api/run-digest', { method: 'POST' });
  setTimeout(loadJobs, 60000);
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3000);
}

// Init
loadJobs();
// Auto-refresh every 2 min
setInterval(loadJobs, 120000);
