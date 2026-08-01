/* Onboarding chat UI */
const chatWindow = document.getElementById('chat-window');
const chatInput = document.getElementById('chat-input');
const progressFill = document.getElementById('progress-fill');
const progressLabel = document.getElementById('progress-label');
const TOTAL_STEPS = 8;
let currentStep = 0;
let waitingForResume = false;

function appendBot(text) {
  const div = document.createElement('div');
  div.className = 'bubble bubble-bot';
  div.textContent = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendUser(text) {
  const div = document.createElement('div');
  div.className = 'bubble bubble-user';
  div.textContent = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
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

function updateProgress(step) {
  const pct = Math.round((step / TOTAL_STEPS) * 100);
  progressFill.style.width = pct + '%';
  progressLabel.textContent = `Step ${step + 1} of ${TOTAL_STEPS}`;
}

function handleAction(action) {
  if (!action) return;
  if (action === 'show_resume_upload') {
    document.getElementById('resume-upload-area').style.display = 'flex';
    waitingForResume = true;
  } else if (action === 'show_gmail_button') {
    document.getElementById('gmail-btn-area').style.display = 'flex';
    document.getElementById('chat-input').disabled = true;
    document.getElementById('send-btn').disabled = true;
    waitingForResume = false;
  } else if (action && action.startsWith('redirect:')) {
    setTimeout(() => { window.location.href = action.replace('redirect:', ''); }, 800);
  }
}

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || waitingForResume) return;

  appendUser(text);
  chatInput.value = '';
  chatInput.style.height = 'auto';

  const typing = showTyping();
  const sendBtn = document.getElementById('send-btn');
  sendBtn.disabled = true;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    removeTyping();
    currentStep = (data.step_num || 0) + 1;
    updateProgress(currentStep);
    appendBot(data.reply);
    handleAction(data.action);
  } catch (e) {
    removeTyping();
    appendBot('Something went wrong. Please try again.');
  } finally {
    sendBtn.disabled = false;
    chatInput.focus();
  }
}

// Enter to send (Shift+Enter for newline)
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-grow textarea
chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
});

// PDF upload handler
document.getElementById('resume-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  appendUser(`Uploading: ${file.name}`);
  const typing = showTyping();

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/resume-upload', { method: 'POST', body: form });
    const data = await res.json();
    removeTyping();
    waitingForResume = false;
    document.getElementById('resume-upload-area').style.display = 'none';
    appendBot(data.reply);
    currentStep = (data.step_num || 0) + 1;
    updateProgress(currentStep);
    handleAction(data.action);
  } catch (err) {
    removeTyping();
    appendBot('Upload failed. Paste your resume text instead.');
    waitingForResume = false;
  }
});
