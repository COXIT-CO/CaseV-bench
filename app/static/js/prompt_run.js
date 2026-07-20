(function () {
  const panel = document.getElementById('run-panel');
  if (!panel) return;
  const runId = panel.dataset.runId;
  const statusPill = document.getElementById('run-status-pill');
  const statusText = document.getElementById('run-status-text');
  const downloadLink = document.getElementById('run-download-link');
  const errorWrap = document.getElementById('run-error-wrap');
  const errorEl = document.getElementById('run-error');
  let currentStatus = statusText.textContent.trim();

  function poll() {
    if (!['PENDING', 'RUNNING'].includes(currentStatus)) return;
    fetch(`/runs/${runId}/status`)
      .then(response => response.json())
      .then(data => {
        currentStatus = data.status;
        statusPill.className = `status-pill status-${data.status}`;
        statusText.textContent = data.status;
        if (data.error_message) {
          errorEl.textContent = data.error_message;
          errorWrap.style.display = '';
        }
        if (data.status === 'COMPLETED') {
          if (downloadLink) downloadLink.style.display = '';
          window.location.reload();
          return;
        }
        if (data.status === 'PENDING' || data.status === 'RUNNING') {
          setTimeout(poll, 2000);
        }
      })
      .catch(() => setTimeout(poll, 3000));
  }
  poll();
})();
