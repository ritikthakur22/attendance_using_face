(() => {
  const video = document.getElementById('attendanceVideo');
  const captureCanvas = document.getElementById('attendanceCapture');
  const overlay = document.getElementById('attendanceOverlay');
  const placeholder = document.getElementById('attendancePlaceholder');
  const startButton = document.getElementById('startAttendanceButton');
  const stopButton = document.getElementById('stopAttendanceButton');
  const liveIndicator = document.getElementById('liveIndicator');
  const status = document.getElementById('attendanceStatus');
  const attendanceList = document.getElementById('attendanceList');
  const presentMetric = document.getElementById('presentMetric');
  const registeredMetric = document.getElementById('registeredMetric');
  const manualButton = document.getElementById('manualMarkButton');
  let stream = null;
  let scanning = false;
  let requestInFlight = false;
  let timer = null;

  const setStatus = (message, type = '') => {
    status.textContent = message;
    status.className = `inline-status ${type}`.trim();
  };

  const settings = () => ({
    session_name: document.getElementById('sessionName').value.trim(),
    class_name: document.getElementById('attendanceClass').value.trim(),
    tolerance: Number(document.getElementById('tolerance').value),
    confirm_frames: Number(document.getElementById('confirmFrames').value),
    scan_id: document.getElementById('scanId').value,
  });

  const renderAttendance = (rows) => {
    if (!rows.length) {
      attendanceList.innerHTML = '<div class="empty-state compact"><strong>No attendance yet</strong><span>Recognized students will appear here.</span></div>';
      return;
    }
    attendanceList.innerHTML = '';
    rows.slice().reverse().forEach(row => {
      const item = document.createElement('div');
      item.className = 'attendance-item';
      const initials = row.name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
      item.innerHTML = `
        <span class="attendance-avatar">${escapeHtml(initials || '?')}</span>
        <span><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.student_id)} · ${escapeHtml(row.class_name || 'No class')}</small></span>
        <span class="attendance-time">${escapeHtml(row.time)}</span>`;
      attendanceList.appendChild(item);
    });
  };

  const escapeHtml = (value) => String(value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

  const drawFaces = (faces) => {
    overlay.width = captureCanvas.width;
    overlay.height = captureCanvas.height;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    ctx.lineWidth = 3;
    ctx.font = '600 16px system-ui';
    faces.forEach(face => {
      const { left, top, right, bottom } = face.box;
      const color = face.status === 'present' ? '#20b26b' : face.status === 'confirming' ? '#ffad35' : '#ef5350';
      const label = face.status === 'confirming'
        ? `${face.name} ${face.progress}/${Number(document.getElementById('confirmFrames').value)}`
        : face.status === 'present' ? `${face.name} · Present` : 'Unknown';
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.strokeRect(left, top, right - left, bottom - top);
      const labelWidth = Math.min(right - left, Math.max(110, ctx.measureText(label).width + 18));
      const labelY = Math.max(0, top - 28);
      ctx.fillRect(left, labelY, labelWidth, 28);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, left + 8, labelY + 19);
    });
  };

  const captureFrame = () => {
    const maxWidth = 720;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    captureCanvas.width = Math.round(video.videoWidth * scale);
    captureCanvas.height = Math.round(video.videoHeight * scale);
    const ctx = captureCanvas.getContext('2d');
    ctx.save();
    ctx.translate(captureCanvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);
    ctx.restore();
    return captureCanvas.toDataURL('image/jpeg', 0.78);
  };

  const scanOnce = async () => {
    if (!scanning || requestInFlight || !video.videoWidth) return;
    requestInFlight = true;
    try {
      const result = await fetchJson('/api/attendance/recognize', {
        method: 'POST',
        body: JSON.stringify({ ...settings(), image: captureFrame() }),
      });
      drawFaces(result.faces);
      renderAttendance(result.attendance);
      presentMetric.textContent = result.present_count;
      registeredMetric.textContent = `${result.registered_count} registered`;
      if (result.newly_marked.length) {
        result.newly_marked.forEach(person => showToast(`${person.name} marked present.`, 'success'));
        setStatus(`${result.newly_marked.map(item => item.name).join(', ')} marked present.`, 'success');
      } else if (result.faces.length) {
        setStatus('Faces detected. Hold still while recognition confirms.');
      } else {
        setStatus('Scanning… no face detected in the current frame.');
      }
    } catch (error) {
      setStatus(error.message, 'danger');
      if (/No registered face|not installed/i.test(error.message)) stopAttendance();
    } finally {
      requestInFlight = false;
    }
  };

  const scheduleScan = () => {
    window.clearInterval(timer);
    timer = window.setInterval(scanOnce, 650);
  };

  const startAttendance = async () => {
    const current = settings();
    if (!current.session_name) {
      setStatus('Enter a session name first.', 'danger');
      return;
    }
    if (!(current.tolerance >= 0.1 && current.tolerance <= 0.9)) {
      setStatus('Tolerance must be between 0.1 and 0.9.', 'danger');
      return;
    }
    if (!(current.confirm_frames >= 1 && current.confirm_frames <= 20)) {
      setStatus('Confirmation frames must be between 1 and 20.', 'danger');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      placeholder.style.display = 'none';
      scanning = true;
      startButton.disabled = true;
      stopButton.disabled = false;
      liveIndicator.classList.add('active');
      liveIndicator.lastChild.textContent = 'Scanning';
      setStatus(`Scanning session “${current.session_name}”…`, 'success');
      scheduleScan();
      await scanOnce();
    } catch (error) {
      setStatus(`Could not start the camera: ${error.message}`, 'danger');
    }
  };

  const stopAttendance = () => {
    scanning = false;
    window.clearInterval(timer);
    timer = null;
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
    const ctx = overlay.getContext('2d');
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    placeholder.style.display = 'grid';
    startButton.disabled = false;
    stopButton.disabled = true;
    liveIndicator.classList.remove('active');
    liveIndicator.lastChild.textContent = 'Not scanning';
    setStatus('Attendance scanning stopped.');
  };

  const manualMark = async () => {
    const studentId = document.getElementById('manualStudentId').value.trim();
    const sessionName = document.getElementById('sessionName').value.trim();
    if (!studentId || !sessionName) {
      setStatus('Enter both a session name and student ID.', 'danger');
      return;
    }
    try {
      const result = await fetchJson('/api/attendance/manual', {
        method: 'POST',
        body: JSON.stringify({ student_id: studentId, session_name: sessionName }),
      });
      showToast(result.message, result.inserted ? 'success' : '');
      setStatus(result.message, result.inserted ? 'success' : '');
      document.getElementById('manualStudentId').value = '';
      const current = await fetch(`/api/attendance/current?session=${encodeURIComponent(sessionName)}`).then(response => response.json());
      if (current.ok) {
        renderAttendance(current.attendance);
        presentMetric.textContent = current.attendance.length;
      }
    } catch (error) {
      setStatus(error.message, 'danger');
    }
  };

  startButton.addEventListener('click', startAttendance);
  stopButton.addEventListener('click', stopAttendance);
  manualButton.addEventListener('click', manualMark);
  window.addEventListener('beforeunload', stopAttendance);
})();
