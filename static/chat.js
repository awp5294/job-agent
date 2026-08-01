/* Onboarding chat UI */
const config = window.JOB_AGENT || { totalSteps: 9, googleAvailable: false, firstPrompt: 'Hi!' };
const chatWindow = document.getElementById('chat-window');
const chatInput = document.getElementById('chat-input');
const progressFill = document.getElementById('progress-fill');
const progressLabel = document.getElementById('progress-label');
let inputLocked = false;

function appendBot(text) {
  append('bubble bubble-bot', text);
}

function appendUser(text) {
  append('bubble bubble-user', text);
}

function append(className, text) {
  const div = document.createElement('div');
  div.className = className;
  div.textContent = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'bubble bubble-bot typing';
  div.id = 'typing-indicator';
  div.innerHTML = '<span></span><span></span><span></span>';
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return div;
}

function removeTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

/* step_num is the 0-based index of the question now being asked. */
function updateProgress(step) {
  const shown = Math.min(step + 1, config.totalSteps);
  progressFill.style.width = Math.round((shown / config.totalSteps) * 100) + '%';
  progressLabel.textContent = `Step ${shown} of ${config.totalSteps}`;
}

function lockInput(locked) {
  inputLocked = locked;
  chatInput.disabled = locked;
  document.getElementById('send-btn').disabled = locked;
}

function handleAction(action) {
  if (!action) return;
  if (action === 'show_resume_upload') {
    document.getElementById('resume-upload-area').style.display = 'flex';
  } else if (action === 'show_finish') {
    document.getElementById('resume-upload-area').style.display = 'none';
    document.getElementById('finish-area').style.display = 'flex';
    lockInput(true);
    if (config.googleAvailable) {
      document.getElementById('gmail-btn').style.display = 'inline-flex';
    } else {
      document.getElementById('finish-btn').style.display = 'inline-flex';
      document.getElementById('finish-note').textContent =
        'Gmail is not set up on this deployment — you will get a private sign-in link instead.';
    }
  } else if (action.startsWith('redirect:')) {
    setTimeout(() => { window.location.href = action.slice('redirect:'.length); }, 800);
  }
}

function applyResponse(data) {
  removeTyping();
  updateProgress(data.step_num || 0);
  appendBot(data.reply);
  handleAction(data.action);
}

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || inputLocked) return;

  appendUser(text);
  chatInput.value = '';
  chatInput.style.height = 'auto';
  showTyping();
  lockInput(true);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const finished = data.action === 'show_finish';
    applyResponse(data);
    lockInput(finished);
  } catch (e) {
    removeTyping();
    appendBot('Something went wrong. Please try again.');
    lockInput(false);
  } finally {
    if (!inputLocked) chatInput.focus();
  }
}

async function finishWithoutGmail() {
  const btn = document.getElementById('finish-btn');
  btn.disabled = true;
  showTyping();
  try {
    const res = await fetch('/api/finish-signup', { method: 'POST' });
    const data = await res.json();
    removeTyping();
    if (!data.ok) {
      appendBot(data.message || 'Could not create your account.');
      btn.disabled = false;
      return;
    }
    appendBot('Account created. Bookmark this private sign-in link — it is how you get back in:');
    const link = append('bubble bubble-bot signin-link', data.signin_url);
    link.textContent = data.signin_url;
    appendBot('Taking you to your dashboard...');
    setTimeout(() => { window.location.href = '/dashboard'; }, 4000);
  } catch (e) {
    removeTyping();
    appendBot('Could not create your account. Please try again.');
    btn.disabled = false;
  }
}

chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
});

document.getElementById('resume-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  appendUser(`Uploading: ${file.name}`);
  showTyping();

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/resume-upload', { method: 'POST', body: form });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    applyResponse(await res.json());
  } catch (err) {
    removeTyping();
    appendBot('Upload failed. Paste your resume text instead.');
  }
});

/* Start the conversation. */
updateProgress(0);
appendBot(config.firstPrompt);
