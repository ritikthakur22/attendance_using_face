(() => {
  const video = document.getElementById('cameraVideo');
  const canvas = document.getElementById('captureCanvas');
  const placeholder = document.getElementById('cameraPlaceholder');
  const startButton = document.getElementById('startCameraButton');
  const captureButton = document.getElementById('captureButton');
  const saveButton = document.getElementById('saveStudentButton');
  const clearButton = document.getElementById('clearImagesButton');
  const fileInput = document.getElementById('imageFiles');
  const previewGrid = document.getElementById('previewGrid');
  const counter = document.getElementById('imageCounter');
  const status = document.getElementById('registrationStatus');
  const images = [];
  let stream = null;

  const setStatus = (message, type = '') => {
    status.textContent = message;
    status.className = `inline-status ${type}`.trim();
  };

  const updatePreviews = () => {
    counter.textContent = `${images.length} / 20`;
    previewGrid.innerHTML = '';
    images.forEach((image, index) => {
      const card = document.createElement('div');
      card.className = 'preview-card';
      const img = document.createElement('img');
      img.src = image;
      img.alt = `Face image ${index + 1}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove image ${index + 1}`);
      remove.addEventListener('click', () => {
        images.splice(index, 1);
        updatePreviews();
      });
      card.append(img, remove);
      previewGrid.appendChild(card);
    });
  };

  const addImage = (dataUrl) => {
    if (images.length >= 20) {
      showToast('A maximum of 20 images is allowed.', 'danger');
      return;
    }
    images.push(dataUrl);
    updatePreviews();
  };

  const startCamera = async () => {
    try {
      if (stream) stream.getTracks().forEach(track => track.stop());
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      placeholder.style.display = 'none';
      captureButton.disabled = false;
      startButton.textContent = 'Restart camera';
      setStatus('Camera ready. Capture several clear images.', 'success');
    } catch (error) {
      setStatus(`Could not start the camera: ${error.message}`, 'danger');
    }
  };

  const captureImage = () => {
    if (!video.videoWidth || !video.videoHeight) return;
    const maxWidth = 800;
    const scale = Math.min(1, maxWidth / video.videoWidth);
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext('2d');
    context.save();
    context.translate(canvas.width, 0);
    context.scale(-1, 1);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    context.restore();
    addImage(canvas.toDataURL('image/jpeg', 0.88));
    setStatus(`Captured ${images.length} image${images.length === 1 ? '' : 's'}.`, 'success');
  };

  const readFiles = async (fileList) => {
    const selected = Array.from(fileList).slice(0, Math.max(0, 20 - images.length));
    for (const file of selected) {
      if (!['image/jpeg', 'image/png'].includes(file.type)) {
        showToast(`${file.name} is not a JPEG or PNG image.`, 'danger');
        continue;
      }
      if (file.size > 5 * 1024 * 1024) {
        showToast(`${file.name} is larger than 5 MB.`, 'danger');
        continue;
      }
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      addImage(dataUrl);
    }
    fileInput.value = '';
  };

  const saveStudent = async () => {
    const studentId = document.getElementById('studentId').value.trim();
    const name = document.getElementById('studentName').value.trim();
    const className = document.getElementById('className').value.trim();
    const addImages = document.getElementById('addImages').checked;
    if (!studentId || !name) {
      setStatus('Student ID and full name are required.', 'danger');
      return;
    }
    if (!images.length) {
      setStatus('Capture or select at least one image.', 'danger');
      return;
    }

    saveButton.disabled = true;
    setStatus('Checking faces and saving encodings…');
    try {
      const result = await fetchJson('/api/students/register', {
        method: 'POST',
        body: JSON.stringify({
          student_id: studentId,
          name,
          class_name: className,
          add_images: addImages,
          images,
        }),
      });
      setStatus(result.message, 'success');
      showToast(result.message, 'success');
      images.splice(0, images.length);
      updatePreviews();
      if (!addImages) {
        document.getElementById('registrationForm').reset();
      }
    } catch (error) {
      setStatus(error.message, 'danger');
      showToast(error.message, 'danger');
    } finally {
      saveButton.disabled = false;
    }
  };

  startButton.addEventListener('click', startCamera);
  captureButton.addEventListener('click', captureImage);
  clearButton.addEventListener('click', () => { images.splice(0, images.length); updatePreviews(); setStatus('Images cleared.'); });
  fileInput.addEventListener('change', () => readFiles(fileInput.files));
  saveButton.addEventListener('click', saveStudent);
  window.addEventListener('beforeunload', () => stream?.getTracks().forEach(track => track.stop()));
  updatePreviews();
})();
