                    const result = await api('/api/admin/backups/restore/dry-run', 'POST', { backupName });
                    const quickCheckText = result.quickCheckOk
                        ? 'ok'
                        : (result.quickCheckError || result.quickCheck || 'failed');
                    const health = result.ok ? 'ready' : 'not ready';
                    setMessage(
                        `Dry run ${health} for ${backupName}. size=${bytesToHuman(result.sizeBytes || 0)}, sha256=${result.sha256 || '-'}, quick_check=${quickCheckText}`,
                        !result.ok,
                        true,
                    );
                    return;
                }

                if (!target.matches('button[data-restore-backup]')) return;
                const backupName = target.getAttribute('data-restore-backup');
                if (!backupName) return;
                const warning = `You are about to restore backup: ${backupName}\n\nThis will replace the live sync database.\nType exactly:\nRESTORE ${backupName}`;
                const confirmText = prompt(warning, '');
                if (confirmText === null) return;
                const result = await api('/api/admin/backups/restore', 'POST', {
                    backupName,
                    confirmText,
                });
                setMessage(
                    `Database restored from ${result.restoredFrom}. Pre-restore snapshot: ${result.preRestoreSnapshot}.`,
                    false,
                    true,
                );
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true, true);
            }
        });

        document.getElementById('refreshImagesBtn').addEventListener('click', async () => {
            try {
                await loadAdminImages();
                setMessage('Image list refreshed.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('reconcileStatusesBtn').addEventListener('click', async () => {
            try {
                const yes = confirm('Create missing image statuses for images found in DB/S3?');
                if (!yes) return;
                const result = await api('/api/admin/images/reconcile-status', 'POST', {
                    defaultStatus: 'in_progress',
                    removeOrphans: false,
                });
                setMessage(`Status sync done. added=${result.added || 0}, removed=${result.removed || 0}`);
                await loadAdminImages();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('labelsSummaryBtn').addEventListener('click', async () => {
            try {
                const result = await api('/api/admin/labels/summary');
                setMessage(
                    `Label sync summary: images=${result.totalImages || 0}, label-files=${result.labelFiles || 0}, with-labels=${result.imagesWithLabels || 0}, missing=${result.imagesMissingLabels || 0}, rows=${result.parsedLabelRows || 0}.`
                );
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('labelSyncHelpBtn').addEventListener('click', () => {
            setMessage(
                'Local labels sync is in desktop app: Cloud -> Sync Local Labels Now (Cloud DB). Keep cloud sync connected, then run it once.',
                false,
                true
            );
        });

        normalizeImagesBtn.addEventListener('click', async () => {
            try {
                const yes = confirm('Normalize all project images to JPEG encoding in cloud storage/DB? This can take a while.');
                if (!yes) return;

                normalizeImagesBtn.disabled = true;
                normalizeImagesBtn.textContent = 'Normalizing...';
                setImagesOpsStatus('Starting JPEG normalization job...');
                setImagesOpsProgress(0, 1);
                setMessage('JPEG normalization started. Progress will appear in Images operations status.', false, true);

                const started = await api('/api/admin/images/normalize-jpeg/start', 'POST', {
                    paths: [],
                    quality: 90,
                });

                const jobId = String(started.jobId || '').trim();
                if (!jobId) {
                    throw new Error('Normalization job did not return a job ID');
                }
                activeNormalizeJobId = jobId;
                cancelNormalizeBtn.disabled = false;

                setImagesOpsProgress(0, Number(started.requested || 1));

                let finalResult = null;
                let transientErrors = 0;
                while (true) {
                    let job = null;
                    try {
                        job = await api(`/api/admin/images/normalize-jpeg/jobs/${encodeURIComponent(jobId)}`);
                        transientErrors = 0;
                    } catch (pollErr) {
                        const msgText = String(pollErr && pollErr.message ? pollErr.message : pollErr);
                        const isTransient = /HTTP\s*504|HTTP\s*503|timed out|timeout|NetworkError|Failed to fetch/i.test(msgText);
                        if (isTransient && transientErrors < 25) {
                            transientErrors += 1;
                            setImagesOpsStatus(`Waiting for normalization status... retry ${transientErrors}/25`, false);
                            await new Promise((resolve) => setTimeout(resolve, 1500));
                            continue;
                        }
                        throw pollErr;
                    }
                    const processed = Number(job.processed || 0);
                    const requested = Number(job.requested || 0);
                    const converted = Number(job.converted || 0);
                    const failed = Number(job.failed || 0);
                    const currentPath = String(job.currentPath || '');
                    const pathSuffix = currentPath ? ` | ${currentPath}` : '';
                    setImagesOpsProgress(processed, requested || 1);
                    setImagesOpsStatus(`JPEG normalize: ${processed}/${requested} processed, converted=${converted}, failed=${failed}${pathSuffix}`, job.status === 'error');

                    if (job.status === 'done') {
                        finalResult = job.result || job;
                        break;
                    }
                    if (job.status === 'canceled') {
                        finalResult = job.result || job;
                        break;
                    }
                    if (job.status === 'error') {
                        throw new Error(String(job.error || 'Normalization job failed'));
                    }
                    await new Promise((resolve) => setTimeout(resolve, 1000));
                }

                const failed = Number(finalResult.failed || 0);
                const requestedFinal = Number(finalResult.requested || 0);
                const processedFinal = Number(finalResult.processed || requestedFinal || 0);
                const inv = finalResult.cloudfrontInvalidation || null;
                const invText = inv
                    ? (inv.ok
                        ? ` CloudFront invalidation queued (id=${inv.invalidationId || '-'}, paths=${inv.pathCount || 0}).`
                        : ` CloudFront invalidation skipped/failed (${inv.reason || 'unknown'}).`)
                    : '';
                setImagesOpsProgress(processedFinal, Number(requestedFinal || 1));
                const sample = Array.isArray(finalResult.failedItems) && finalResult.failedItems.length
                    ? ` First failure: ${finalResult.failedItems[0].path} (${finalResult.failedItems[0].error}).`
                    : '';
                if (finalResult.canceled) {
                    const canceledBy = String(finalResult.cancelRequestedBy || '');
                    const canceledAt = Number(finalResult.canceledAt || 0);
                    const canceledAtText = canceledAt ? ` at ${fmtDate(canceledAt)}` : '';
                    const canceledByText = canceledBy ? ` by ${canceledBy}` : '';
                    setMessage(
                        `JPEG normalization canceled${canceledByText}${canceledAtText}. processed=${processedFinal}/${requestedFinal}, converted=${finalResult.converted || 0}, failed=${failed}.${sample}${invText}`,
                        true
                    );
                    setImagesOpsStatus(`JPEG normalization canceled${canceledByText}${canceledAtText}.`, true);
                    return;
                }
                setMessage(
                    `JPEG normalization done. requested=${finalResult.requested || 0}, converted=${finalResult.converted || 0}, failed=${failed}.${sample}${invText}`,
                    failed > 0
                );
                setImagesOpsStatus(`JPEG normalization completed. converted=${finalResult.converted || 0}, failed=${failed}.`, failed > 0);
                await loadAdminImages();
            } catch (e) {
                setMessage(e.message, true);
                setImagesOpsStatus(`JPEG normalization failed: ${String(e.message || e)}`, true);
            } finally {
                activeNormalizeJobId = '';
                cancelNormalizeBtn.disabled = true;
                normalizeImagesBtn.disabled = false;
                normalizeImagesBtn.textContent = 'Normalize To JPEG';
            }
        });

        cancelNormalizeBtn.addEventListener('click', async () => {
            try {
                if (!activeNormalizeJobId) {
                    setMessage('No active normalization job to cancel.', true);
                    return;
                }
                const yes = confirm('Cancel the running JPEG normalization job?');
                if (!yes) return;
                cancelNormalizeBtn.disabled = true;
                const canceled = await api(`/api/admin/images/normalize-jpeg/jobs/${encodeURIComponent(activeNormalizeJobId)}/cancel`, 'POST', {});
                const who = String(canceled.cancelRequestedBy || '');
                const at = Number(canceled.canceledAt || 0);
                const whoText = who ? ` by ${who}` : '';
                const atText = at ? ` at ${fmtDate(at)}` : '';
                setImagesOpsStatus('Cancel requested for normalization job. Waiting for worker to stop...', false);
                setMessage(`Cancel requested${whoText}${atText}. Waiting for worker to stop...`, false);
            } catch (e) {
                cancelNormalizeBtn.disabled = false;
                setMessage(e.message, true);
            }
        });

        document.getElementById('openSlideshowBtn').addEventListener('click', () => {
            openSlideshow().catch((error) => {
                setMessage(error.message || String(error), true);
            });
        });

        imagesSortBy.addEventListener('change', () => {
            loadAdminImages().catch((error) => setMessage(error.message || String(error), true));
        });

        imagesSortOrder.addEventListener('change', () => {
            loadAdminImages().catch((error) => setMessage(error.message || String(error), true));
        });

        imagesSelectAll.addEventListener('change', () => {
            const checks = Array.from(imagesBody.querySelectorAll('input[type="checkbox"][data-image-index]'));
            for (const node of checks) {
                node.checked = !!imagesSelectAll.checked;
            }
        });

        document.getElementById('deleteSelectedImagesBtn').addEventListener('click', async () => {
            try {
                const selected = getSelectedAdminImagePaths();
                if (!selected.length) {
                    throw new Error('Select at least one image first.');
                }
                const yes = confirm(`Delete ${selected.length} image(s) from this project?`);
                if (!yes) return;
                const payload = {
                    paths: selected,
                    deleteLabels: !!imagesDeleteLabels.checked,
                };
                const result = await api('/api/admin/images/delete', 'POST', payload);
                setMessage(`Deleted images=${result.deletedImageCount || 0}, file records=${result.deletedFileRecords || 0}.`);
                await loadAdminImages();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('purgeModeLabelsBtn').addEventListener('click', async () => {
            try {
                const yes = confirm('Purge all /labels/BB and /labels/OBB .txt files from this project?');
                if (!yes) return;
                const result = await api('/api/admin/labels/purge-mode-files', 'POST', {});
                setMessage(`Purged mode label files: ${result.purged || 0}`);
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('pickImageFilesBtn').addEventListener('click', () => {
            pickImageFilesInput.click();
        });

        document.getElementById('closeSlideshowBtn').addEventListener('click', closeSlideshow);
        slideshowModal.addEventListener('click', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.getAttribute('data-close-slideshow') === '1') {
                closeSlideshow();
            }
        });

        document.getElementById('slidePrevBtn').addEventListener('click', () => {
            showSlideAt(slideshowState.index - 1).catch((error) => setMessage(error.message || String(error), true));
        });
        document.getElementById('slideNextBtn').addEventListener('click', () => {
            showSlideAt(slideshowState.index + 1).catch((error) => setMessage(error.message || String(error), true));
        });
        document.getElementById('slideMarkCompletedBtn').addEventListener('click', () => {
            const current = getCurrentSlideItem();
            if (!current) return;
            setImageStatusForPath(current.path, 'completed').catch((error) => setMessage(error.message || String(error), true));
        });
        document.getElementById('slideMarkInProgressBtn').addEventListener('click', () => {
            const current = getCurrentSlideItem();
            if (!current) return;
            setImageStatusForPath(current.path, 'in_progress').catch((error) => setMessage(error.message || String(error), true));
        });
        document.getElementById('slideDeleteBtn').addEventListener('click', () => {
            deleteCurrentSlideImage().catch((error) => setMessage(error.message || String(error), true));
        });

        slideShowLabels.addEventListener('change', () => {
            slideshowState.showLabels = !!slideShowLabels.checked;
            refreshSlideOverlay().catch((error) => setMessage(error.message || String(error), true));
        });
        slideShuffle.addEventListener('change', () => {
            slideshowState.shuffle = !!slideShuffle.checked;
            if (!slideshowState.open) return;
            const current = getCurrentSlideItem();
            rebuildSlideshowItems(current ? current.path : '');
            showSlideAt(slideshowState.index).catch((error) => setMessage(error.message || String(error), true));
        });
        slideAutoplay.addEventListener('change', () => {
            slideshowState.autoplay = !!slideAutoplay.checked;
            maybeStartSlideshowAutoplay();
        });
        slideInterval.addEventListener('change', () => {
            slideshowState.intervalMs = Math.max(400, Number(slideInterval.value || 2200));
            maybeStartSlideshowAutoplay();
        });

        window.addEventListener('keydown', (event) => {
            if (!slideshowState.open) return;
            const target = event.target;
            if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
                return;
            }
            const key = String(event.key || '').toLowerCase();

            if (key === 'escape') {
                closeSlideshow();
                event.preventDefault();
                return;
            }
            if (key === 'arrowright' || key === ' ' || key === 'n') {
                showSlideAt(slideshowState.index + 1).catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'arrowleft' || key === 'p') {
                showSlideAt(slideshowState.index - 1).catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'c') {
                const current = getCurrentSlideItem();
                if (!current) return;
                setImageStatusForPath(current.path, 'completed').catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'v') {
                const current = getCurrentSlideItem();
                if (!current) return;
                setImageStatusForPath(current.path, 'in_progress').catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'delete') {
                deleteCurrentSlideImage().catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'l') {
                slideShowLabels.checked = !slideShowLabels.checked;
                slideshowState.showLabels = !!slideShowLabels.checked;
                refreshSlideOverlay().catch((error) => setMessage(error.message || String(error), true));
                event.preventDefault();
                return;
            }
            if (key === 'a') {
                slideAutoplay.checked = !slideAutoplay.checked;
                slideshowState.autoplay = !!slideAutoplay.checked;
                maybeStartSlideshowAutoplay();
                event.preventDefault();
            }
        });

        document.getElementById('pickImageFolderBtn').addEventListener('click', () => {
            pickImageFolderInput.click();
        });

        document.getElementById('syncProjectFolderBtn').addEventListener('click', () => {
            syncEntireLocalFolder().catch((error) => {
                setMessage(`Sync preparation failed: ${String(error.message || error)}`, true);
            });
        });

        document.getElementById('analyzeConflictsBtn').addEventListener('click', () => {
            analyzeConflicts().catch((error) => {
                setMessage(`Conflict analysis failed: ${String(error.message || error)}`, true);
            });
        });

        pickImageFilesInput.addEventListener('change', () => {
            const files = Array.from(pickImageFilesInput.files || []);
            addFilesToQueue(files.map(file => ({ file, relativePath: file.name })));
            pickImageFilesInput.value = '';
        });

        pickImageFolderInput.addEventListener('change', () => {
            const files = Array.from(pickImageFolderInput.files || []);
            addFilesToQueue(files.map(file => ({
                file,
                relativePath: file.webkitRelativePath || file.name,
            })));
            pickImageFolderInput.value = '';

            if (autoSyncAfterFolderPick) {
                autoSyncAfterFolderPick = false;
                analyzeConflicts()
                    .then(() => startUploadQueue())
                    .catch((error) => {
                        setMessage(`Sync failed: ${String(error.message || error)}`, true);
                    });
            }
        });

        document.getElementById('pickZipBtn').addEventListener('click', () => {
            pickZipInput.click();
        });

        pickZipInput.addEventListener('change', () => {
            selectedZipFile = (pickZipInput.files && pickZipInput.files[0]) ? pickZipInput.files[0] : null;
            zipSelectedName.textContent = selectedZipFile ? selectedZipFile.name : 'No zip selected.';
            pickZipInput.value = '';
        });

        document.getElementById('uploadZipBtn').addEventListener('click', () => {
            uploadZipArchive().catch((error) => {
                setMessage(`Zip upload failed: ${String(error.message || error)}`, true);
            });
        });

        uploadDropZone.addEventListener('dragover', (event) => {
            event.preventDefault();
            uploadDropZone.classList.add('active');
        });

        uploadDropZone.addEventListener('dragleave', () => {
            uploadDropZone.classList.remove('active');
        });

        uploadDropZone.addEventListener('drop', (event) => {
            handleDrop(event).catch((error) => {
                setMessage(`Drop parsing failed: ${String(error.message || error)}`, true);
            });
        });

        document.getElementById('uploadTargetPrefix').addEventListener('change', remapQueuePathsFromPrefix);
        document.getElementById('startUploadBtn').addEventListener('click', () => {
            startUploadQueue().catch((error) => {
                setMessage(`Upload failed: ${String(error.message || error)}`, true);
            });
        });
        document.getElementById('clearUploadQueueBtn').addEventListener('click', () => {
            uploadQueue = [];
            resetConflictReport();
            renderUploadQueue();
        });

        async function init() {
            setLoggedInState(false);
            applyThemeMode();
            applyCompactMode();
            refreshUploadProjectBadge();
            setDashboardPage(activeDashPage);
            updateAuthPanelSignOutVisibility();
            try {
                const info = await api('/api/public/info');
                requireProjectPassword = !!info.requireProjectPassword;
                projectPasswordWrap.classList.toggle('hidden', !requireProjectPassword);
                loginHint.textContent = requireProjectPassword
                    ? 'Project password is required at login.'
                    : 'Use your username/password, then load projects.';
                bootstrapWrap.classList.toggle('hidden', !!info.hasProjects);
            } catch (e) {
                setMessage(e.message, true);
            }

            if (currentUser) {
                document.getElementById('username').value = currentUser;
            }

            if (token) {
                setLoggedInState(true);
                await refreshAll(true);
                startTimer();
            }
        }

        window.addEventListener('resize', () => {
            applyCompactMode();
            if (slideshowState.open) {
                refreshSlideOverlay().catch(() => { });
            }
        });

        init();
