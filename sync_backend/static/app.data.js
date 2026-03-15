            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(objectUrl);
        }

        function renderAdminImages(items) {
            adminImages = Array.isArray(items) ? items : [];
            if (!adminImages.length) {
                imagesBody.innerHTML = '<tr><td colspan="6" class="muted">No images loaded.</td></tr>';
                imagesSummary.textContent = '0 images loaded.';
                if (imagesSelectAll) imagesSelectAll.checked = false;
                return;
            }

            imagesBody.innerHTML = adminImages.map((item, idx) => `
                <tr>
                    <td><input type="checkbox" data-image-index="${idx}" /></td>
                    <td class="mono">${item.path || '-'}</td>
                    <td>${item.status || '-'}</td>
                    <td>${item.presentInS3 ? 's3' : 'db'}</td>
                    <td>${bytesToHuman(item.sizeBytes || 0)}</td>
                    <td>${fmtDate(item.mtimeMs || 0)}</td>
                </tr>
            `).join('');
            const s3Only = adminImages.filter((item) => item.presentInS3 && !item.indexedInDb).length;
            if (s3Only === 0) {
                imagesSummary.textContent = `${adminImages.length} images loaded. S3 and DB index are in sync.`;
            } else {
                imagesSummary.textContent = `${adminImages.length} images loaded. s3-only=${s3Only} (present in S3, missing DB index row).`;
            }
            if (imagesSelectAll) imagesSelectAll.checked = false;
        }

        async function loadAdminImages() {
            if (!isAdmin) return;
            const sortBy = String(imagesSortBy?.value || 'path');
            const order = String(imagesSortOrder?.value || 'asc');
            const query = new URLSearchParams({ sortBy, order, limit: '5000' }).toString();
            const payload = await api(`/api/admin/images?${query}`);
            renderAdminImages(payload.items || []);
        }

        function getSelectedAdminImagePaths() {
            const checks = Array.from(imagesBody.querySelectorAll('input[type="checkbox"][data-image-index]:checked'));
            const selected = [];
            for (const node of checks) {
                const idx = Number(node.getAttribute('data-image-index'));
                const item = Number.isFinite(idx) ? adminImages[idx] : null;
                if (item && item.path) selected.push(String(item.path));
            }
            return selected;
        }

        function stopSlideshowAutoplay() {
            if (slideshowState.timer) {
                clearInterval(slideshowState.timer);
                slideshowState.timer = null;
            }
        }

        function maybeStartSlideshowAutoplay() {
            stopSlideshowAutoplay();
            if (!slideshowState.open || !slideshowState.autoplay || slideshowState.items.length <= 1) return;
            const interval = Math.max(400, Number(slideshowState.intervalMs || 2200));
            slideshowState.timer = setInterval(() => {
                showSlideAt(slideshowState.index + 1).catch((error) => {
                    stopSlideshowAutoplay();
                    setMessage(error.message || String(error), true);
                });
            }, interval);
        }

        function getCurrentSlideItem() {
            if (!slideshowState.items.length) return null;
            const idx = Math.max(0, Math.min(slideshowState.index, slideshowState.items.length - 1));
            return slideshowState.items[idx] || null;
        }

        function shuffleCopy(items) {
            const out = items.slice();
            for (let i = out.length - 1; i > 0; i -= 1) {
                const j = Math.floor(Math.random() * (i + 1));
                const tmp = out[i];
                out[i] = out[j];
                out[j] = tmp;
            }
            return out;
        }

        function rebuildSlideshowItems(preservePath = '') {
            const byPath = new Map(adminImages.map((item) => [item.path, item]));
            let ordered = slideshowState.basePaths.map((path) => byPath.get(path)).filter(Boolean);
            if (!ordered.length) {
                ordered = adminImages.slice();
                slideshowState.basePaths = ordered.map((item) => item.path);
            }

            if (slideshowState.shuffle && ordered.length > 1) {
                if (preservePath) {
                    const current = ordered.find((item) => item.path === preservePath) || null;
                    const rest = ordered.filter((item) => item.path !== preservePath);
                    ordered = current ? [current, ...shuffleCopy(rest)] : shuffleCopy(ordered);
                } else {
                    ordered = shuffleCopy(ordered);
                }
            }

            slideshowState.items = ordered;
            if (!ordered.length) {
                slideshowState.index = 0;
                return;
            }

            const idx = preservePath ? ordered.findIndex((item) => item.path === preservePath) : -1;
            slideshowState.index = idx >= 0 ? idx : 0;
        }

        function drawSlideLabels(labels) {
            const canvas = slideOverlay;
            const img = slideImage;
            const ctx = canvas.getContext('2d');
            if (!ctx) return;

            const stageRect = slideStage.getBoundingClientRect();
            const imageRect = img.getBoundingClientRect();
            const stageW = Math.max(1, Math.round(Number(stageRect.width || 0)));
            const stageH = Math.max(1, Math.round(Number(stageRect.height || 0)));
            canvas.width = stageW;
            canvas.height = stageH;
            ctx.clearRect(0, 0, stageW, stageH);

            const drawW = Number(imageRect.width || 0);
            const drawH = Number(imageRect.height || 0);
            if (!drawW || !drawH) return;

            const offsetX = Number(imageRect.left || 0) - Number(stageRect.left || 0);
            const offsetY = Number(imageRect.top || 0) - Number(stageRect.top || 0);
            ctx.lineWidth = 2;
            ctx.font = '12px "IBM Plex Mono", monospace';

            for (const item of labels || []) {
                const pts = Array.isArray(item.points) ? item.points.map((v) => Number(v || 0)) : [];
                if (pts.length < 8) continue;
                const color = item.format === 'obb' ? '#ec6f08' : '#0d7662';
                ctx.strokeStyle = color;
                ctx.fillStyle = `${color}33`;
                ctx.beginPath();
                ctx.moveTo(offsetX + (pts[0] * drawW), offsetY + (pts[1] * drawH));
                ctx.lineTo(offsetX + (pts[2] * drawW), offsetY + (pts[3] * drawH));
                ctx.lineTo(offsetX + (pts[4] * drawW), offsetY + (pts[5] * drawH));
                ctx.lineTo(offsetX + (pts[6] * drawW), offsetY + (pts[7] * drawH));
                ctx.closePath();
                ctx.stroke();
                ctx.fill();

                const classText = `c${Number(item.classId || 0)}`;
                const tx = offsetX + (pts[0] * drawW);
                const ty = Math.max(12, offsetY + (pts[1] * drawH) - 4);
                ctx.fillStyle = color;
                ctx.fillText(classText, tx, ty);
            }
        }

        async function fetchSlideLabels(path) {
            if (!path) return [];
            if (slideshowState.labelsByPath.has(path)) {
                return slideshowState.labelsByPath.get(path) || [];
            }
            const payload = await api(`/api/admin/images/labels?path=${encodeURIComponent(path)}`);
            const labels = Array.isArray(payload.labels) ? payload.labels : [];
            slideshowState.labelsByPath.set(path, labels);
            return labels;
        }

        async function refreshSlideOverlay() {
            const current = getCurrentSlideItem();
            const ctx = slideOverlay.getContext('2d');
            if (ctx) {
                ctx.clearRect(0, 0, slideOverlay.width, slideOverlay.height);
            }
            if (!current || !slideshowState.showLabels) return;
            try {
                const labels = await fetchSlideLabels(current.path);
                drawSlideLabels(labels);
            } catch (error) {
                setMessage(`Label overlay failed: ${String(error.message || error)}`, true);
            }
        }

        async function showSlideAt(nextIndex) {
            if (!slideshowState.items.length) return;
            const len = slideshowState.items.length;
            const normalized = ((nextIndex % len) + len) % len;
            slideshowState.index = normalized;
            const current = slideshowState.items[normalized];
            if (!current) return;

            slideTitle.textContent = current.path || '-';
            slideCounter.textContent = `${normalized + 1} / ${len}`;
            slideStatusText.textContent = `status: ${current.status || '-'}`;
            slideSourceText.textContent = 'source: loading...';

            let payload = slideshowState.imageViewByPath.get(current.path) || null;
            if (!payload) {
                const maxWidth = Math.max(640, Math.min(2200, Math.floor(window.innerWidth * 0.9)));
                const maxHeight = Math.max(520, Math.min(2200, Math.floor(window.innerHeight * 0.8)));
                payload = await api(`/api/admin/images/view?path=${encodeURIComponent(current.path)}&maxWidth=${maxWidth}&maxHeight=${maxHeight}&quality=82`);
                slideshowState.imageViewByPath.set(current.path, payload);
                if (slideshowState.imageViewByPath.size > 80) {
                    const firstKey = slideshowState.imageViewByPath.keys().next().value;
                    if (firstKey) {
                        slideshowState.imageViewByPath.delete(firstKey);
                    }
                }
            }
            slideSourceText.textContent = `source: ${payload.source || '-'}`;

            await new Promise((resolve, reject) => {
                slideImage.onload = () => resolve();
                slideImage.onerror = () => reject(new Error('Failed to load image for slideshow'));
                slideImage.src = payload.url;
            });

            const naturalW = Number(slideImage.naturalWidth || 0);
            const naturalH = Number(slideImage.naturalHeight || 0);
            slideStage.classList.toggle('is-portrait', naturalH > naturalW && naturalW > 0);

            await refreshSlideOverlay();
        }

        async function setImageStatusForPath(path, status) {
            const imageName = (path || '').split('/').pop();
            if (!imageName) throw new Error('Invalid image path');
            await api('/api/image-status', 'POST', { imageName, status });
            for (const item of adminImages) {
                if (item && item.path === path) {
                    item.status = status;
                }
            }
            for (const item of slideshowState.items) {
                if (item && item.path === path) {
                    item.status = status;
                }
            }
            renderAdminImages(adminImages);
            const current = getCurrentSlideItem();
            if (current && current.path === path) {
                slideStatusText.textContent = `status: ${status}`;
            }
        }

        async function deleteCurrentSlideImage() {
            const current = getCurrentSlideItem();
            if (!current) return;
            const yes = confirm(`Delete image ${current.path}?`);
            if (!yes) return;
            await api('/api/admin/images/delete', 'POST', {
                paths: [current.path],
                deleteLabels: !!imagesDeleteLabels.checked,
            });

            adminImages = adminImages.filter((item) => item.path !== current.path);
            slideshowState.basePaths = slideshowState.basePaths.filter((path) => path !== current.path);
            rebuildSlideshowItems();
            slideshowState.labelsByPath.delete(current.path);
            slideshowState.imageViewByPath.delete(current.path);
            renderAdminImages(adminImages);

            if (!slideshowState.items.length) {
                closeSlideshow();
                return;
            }

            if (slideshowState.index >= slideshowState.items.length) {
                slideshowState.index = slideshowState.items.length - 1;
            }
            await showSlideAt(slideshowState.index);
        }

        function closeSlideshow() {
            slideshowState.open = false;
            stopSlideshowAutoplay();
            slideshowState.items = [];
            slideshowState.basePaths = [];
            slideshowState.index = 0;
            slideshowModal.classList.add('hidden');
            slideshowModal.setAttribute('aria-hidden', 'true');
            slideImage.src = '';
            const ctx = slideOverlay.getContext('2d');
            if (ctx) {
                ctx.clearRect(0, 0, slideOverlay.width, slideOverlay.height);
            }
        }

        async function openSlideshow() {
            if (!adminImages.length) {
                setMessage('No images loaded yet. Refresh Images first.', true);
                return;
            }

            const selected = getSelectedAdminImagePaths();
            const selectedSet = new Set(selected);
            let baseItems = selected.length
                ? adminImages.filter((item) => selectedSet.has(item.path))
                : adminImages.slice();

            slideshowState.shuffle = !!slideShuffle.checked;
            slideshowState.showLabels = !!slideShowLabels.checked;
            slideshowState.autoplay = !!slideAutoplay.checked;
            slideshowState.intervalMs = Math.max(400, Number(slideInterval.value || 2200));
            slideshowState.labelsByPath = new Map();
            slideshowState.basePaths = baseItems.map((item) => item.path).filter(Boolean);
            rebuildSlideshowItems();
            slideshowState.open = true;

            slideshowModal.classList.remove('hidden');
            slideshowModal.setAttribute('aria-hidden', 'false');
            await showSlideAt(slideshowState.index);
            maybeStartSlideshowAutoplay();
        }

        function sanitizeRelativePath(value) {
            const normalized = String(value || '').replace(/\\/g, '/').trim().replace(/^\/+/, '');
            const parts = normalized.split('/').filter(Boolean);
            if (!parts.length) return '';
            for (const part of parts) {
                if (part === '.' || part === '..') return '';
            }
            return parts.join('/');
        }

        function toQueuePath(prefix, relative) {
            const p = sanitizeRelativePath(prefix || '');
            const r = sanitizeRelativePath(relative || '');
            if (!r) return '';
            return p ? `${p}/${r}` : r;
        }

        function bytesToHuman(value) {
            const n = Number(value || 0);
            if (!Number.isFinite(n) || n <= 0) return '0 B';
            if (n < 1024) return `${n} B`;
            if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
            return `${(n / (1024 * 1024)).toFixed(1)} MB`;
        }

        function renderUploadQueue() {
            if (!uploadQueue.length) {
                uploadQueueBody.innerHTML = '<tr><td colspan="3" class="muted">No files queued.</td></tr>';
                uploadSummary.textContent = 'Queue empty.';
                return;
            }
            uploadQueueBody.innerHTML = uploadQueue.map(item => `
                <tr>
                    <td class="mono">${item.targetPath}</td>
                    <td>${bytesToHuman(item.file.size)}</td>
                    <td>${item.status || (item.conflict ? `queued (${item.conflict})` : 'queued')}</td>
                </tr>
            `).join('');
            uploadSummary.textContent = `${uploadQueue.length} file(s) queued.`;
        }

        function resetConflictReport() {
            conflictExisting.textContent = '0';
            conflictChangedSize.textContent = '0';
            conflictChangedHash.textContent = '0';
            conflictSummary.textContent = 'Conflict report: not generated.';
            for (const item of uploadQueue) {
                item.conflict = '';
            }
        }

        async function sha1HexForFile(file) {
            const buf = await file.arrayBuffer();
            const digest = await crypto.subtle.digest('SHA-1', buf);
            return Array.from(new Uint8Array(digest))
                .map((v) => v.toString(16).padStart(2, '0'))
                .join('');
        }

        async function analyzeConflicts() {
            if (!uploadQueue.length) {
                setMessage('Upload queue is empty.', true);
                resetConflictReport();
                return { existing: 0, changedSize: 0, changedHash: 0 };
            }
            if (!token) {
                setMessage('Login is required to analyze conflicts.', true);
                return { existing: 0, changedSize: 0, changedHash: 0 };
            }

            const payload = await api('/api/images/manifest');
            const remoteByPath = new Map();
            for (const item of payload.manifest || []) {
                remoteByPath.set(String(item.path || ''), {
                    size: Number(item.size || 0),
                    sha1: String(item.sha1 || '').toLowerCase(),
                });
            }

            let existing = 0;
            let changedSize = 0;
            let changedHash = 0;

            for (const item of uploadQueue) {
                item.conflict = '';
                const remote = remoteByPath.get(item.targetPath);
                if (!remote) continue;
                existing += 1;

                if (remote.size !== Number(item.file.size || 0)) {
                    changedSize += 1;
                    item.conflict = 'changed-size';
                    continue;
                }

                if (!remote.sha1) {
                    item.conflict = 'existing';
                    continue;
                }

                try {
                    const localSha1 = await sha1HexForFile(item.file);
                    if (localSha1.toLowerCase() !== remote.sha1) {
                        changedHash += 1;
                        item.conflict = 'changed-hash';
                    } else {
                        item.conflict = 'existing';
                    }
                } catch (_) {
                    item.conflict = 'existing';
                }
            }

            conflictExisting.textContent = String(existing);
            conflictChangedSize.textContent = String(changedSize);
            conflictChangedHash.textContent = String(changedHash);
            conflictSummary.textContent = `Conflict report ready. existing=${existing}, changed-size=${changedSize}, changed-hash=${changedHash}`;
            renderUploadQueue();
            return { existing, changedSize, changedHash };
        }

        function addFilesToQueue(entries) {
            const seen = new Set(uploadQueue.map(item => item.targetPath));
            let added = 0;
            for (const entry of entries) {
                const file = entry.file;
                const relative = sanitizeRelativePath(entry.relativePath || file.name || '');
                if (!relative) continue;
                const suffix = `.${relative.split('.').pop().toLowerCase()}`;
                if (!imageSuffixes.has(suffix)) continue;

                const prefix = document.getElementById('uploadTargetPrefix').value.trim();
                const targetPath = toQueuePath(prefix, relative);
                if (!targetPath || seen.has(targetPath)) continue;

                uploadQueue.push({ file, relativePath: relative, targetPath, status: 'queued' });
                seen.add(targetPath);
                added += 1;
            }
            renderUploadQueue();
            resetConflictReport();
            if (added > 0) setMessage(`Added ${added} file(s) to upload queue.`);
        }

        function remapQueuePathsFromPrefix() {
            const prefix = document.getElementById('uploadTargetPrefix').value.trim();
            uploadQueue = uploadQueue.map(item => ({
                ...item,
                targetPath: toQueuePath(prefix, item.relativePath),
            })).filter(item => !!item.targetPath);
            resetConflictReport();
            renderUploadQueue();
        }

        async function walkDroppedEntry(entry, parentPath = '') {
            if (!entry) return [];
            if (entry.isFile) {
                const file = await new Promise((resolve, reject) => {
                    entry.file(resolve, reject);
                });
                return [{ file, relativePath: sanitizeRelativePath(`${parentPath}${entry.name}`) }];
            }

            if (!entry.isDirectory) return [];
            const reader = entry.createReader();
            const items = [];
            while (true) {
                const chunk = await new Promise((resolve, reject) => {
                    reader.readEntries(resolve, reject);
                });
                if (!chunk.length) break;
                items.push(...chunk);
            }
