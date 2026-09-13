/* GaiaPulse — client-side JS */

// ─── Toast System ─────────────────────────────────────────────────────────────
const toastContainer = document.createElement('div');
toastContainer.className = 'toast-container';
document.body.appendChild(toastContainer);

window.showToast = function(message, type = 'info', duration = 4000) {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 300ms ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
};

// ─── HTMX global event handlers ───────────────────────────────────────────────
document.addEventListener('htmx:afterRequest', function(evt) {
  const xhr = evt.detail.xhr;
  if (xhr.status >= 400) {
    let msg = 'Something went wrong.';
    try { msg = JSON.parse(xhr.responseText)?.detail || msg; } catch {}
    showToast(msg, 'error');
  }
});

document.addEventListener('htmx:responseError', function(evt) {
  showToast('Network error. Please try again.', 'error');
});

// ─── Voice Recording (MediaRecorder API) ──────────────────────────────────────
window.VoiceRecorder = function(targetInputId, statusElementId) {
  let mediaRecorder = null;
  let chunks = [];
  let stream = null;

  return {
    isRecording: false,
    hasAudio: false,
    audioBlob: null,
    error: null,

    async startRecording() {
      this.error = null;
      this.hasAudio = false;
      this.audioBlob = null;
      chunks = [];

      if (!navigator.mediaDevices?.getUserMedia) {
        this.error = 'Microphone not supported in this browser.';
        return;
      }

      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
        mediaRecorder.onstop = () => {
          this.audioBlob = new Blob(chunks, { type: 'audio/webm' });
          this.hasAudio = true;
          stream.getTracks().forEach(t => t.stop());
        };
        mediaRecorder.start();
        this.isRecording = true;
      } catch (err) {
        this.error = 'Could not access microphone: ' + err.message;
      }
    },

    stopRecording() {
      if (mediaRecorder && this.isRecording) {
        mediaRecorder.stop();
        this.isRecording = false;
      }
    },

    async submitAudio(previewTargetId) {
      if (!this.audioBlob) return;
      const formData = new FormData();
      formData.append('audio', this.audioBlob, 'recording.webm');

      const previewArea = document.getElementById(previewTargetId);
      if (previewArea) {
        previewArea.innerHTML = '<div class="py-8 text-center text-slate-400 text-sm htmx-indicator">Transcribing and parsing...</div>';
      }

      try {
        const resp = await fetch('/capture/transcribe', {
          method: 'POST',
          body: formData,
        });
        const html = await resp.text();
        if (previewArea) previewArea.innerHTML = html;
        // Re-process Alpine on inserted HTML
        if (window.Alpine) Alpine.initTree(previewArea);
      } catch (err) {
        if (previewArea) previewArea.innerHTML = `<div class="text-rose-600 text-sm p-4">Error: ${err.message}</div>`;
      }
    }
  };
};

// ─── Chart.js global defaults ─────────────────────────────────────────────────
if (window.Chart) {
  Chart.defaults.font.family = "'Inter', 'system-ui', sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = '#64748b';
}

// ─── Active nav link highlighting ─────────────────────────────────────────────
(function() {
  const current = window.location.pathname;
  document.querySelectorAll('nav a[href]').forEach(a => {
    const href = a.getAttribute('href');
    if (href && href !== '/' && current.startsWith(href)) {
      a.classList.add('nav-active');
    } else if (href === '/' && current === '/') {
      a.classList.add('nav-active');
    }
  });
})();
