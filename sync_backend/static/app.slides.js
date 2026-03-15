
            const out = [];
            for (const child of items) {
                const nested = await walkDroppedEntry(child, `${parentPath}${entry.name}/`);
                out.push(...nested);
            }
            return out;
        }

        async function handleDrop(event) {
            event.preventDefault();
            uploadDropZone.classList.remove('active');

            const items = Array.from(event.dataTransfer?.items || []);
            const fromEntries = [];
            for (const item of items) {
                const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
                if (!entry) continue;
                try {
                    const walked = await walkDroppedEntry(entry, '');
                    fromEntries.push(...walked);
                } catch (_) {
                }
            }

            if (fromEntries.length) {
                addFilesToQueue(fromEntries);
                return;
            }

            const rawFiles = Array.from(event.dataTransfer?.files || []);
            addFilesToQueue(rawFiles.map(file => ({
                file,
                relativePath: file.webkitRelativePath || file.name,
            })));
        }

        async function startUploadQueue() {
            if (!uploadQueue.length) {
                setMessage('Upload queue is empty.', true);
                return;
            }
            if (!token) {
                setMessage('Login is required to upload images.', true);
                return;
            }
            if (!currentProjectId) {
                setMessage('Active project is unknown. Refresh first.', true);
                return;
            }

            const overwrite = !!document.getElementById('uploadOverwrite').checked;
            let uploaded = 0;
            let skipped = 0;
            let failed = 0;

            for (let i = 0; i < uploadQueue.length; i += 1) {
                const item = uploadQueue[i];
                item.status = 'uploading...';
                renderUploadQueue();
                const body = new FormData();
                body.append('file', item.file, item.file.name);
                body.append('path', item.targetPath);
                body.append('expected_project_id', currentProjectId);
                body.append('overwrite', overwrite ? '1' : '0');
                try {
                    const res = await fetch('/api/admin/images/upload', {
                        method: 'POST',
                        headers: { Authorization: `Bearer ${token}` },
                        body,
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) {
                        const detail = data.detail || data.error || `HTTP ${res.status}`;
                        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
                    }

                    if (data.uploaded) {
                        uploaded += 1;
                        item.status = 'uploaded';
                    } else if (data.skipped) {
                        skipped += 1;
                        item.status = 'skipped (exists)';
                    } else {
                        failed += 1;
                        item.status = 'failed';
                    }
                } catch (error) {
                    failed += 1;
                    item.status = `failed: ${String(error.message || error)}`;
                }
                renderUploadQueue();
            }

            setMessage(`Upload finished. Uploaded: ${uploaded}, skipped: ${skipped}, failed: ${failed}`, failed > 0);
            await refreshAll(true);
        }


        async function syncEntireLocalFolder() {
            if (!token) {
                setMessage('Login is required to sync local folder.', true);
                return;
            }
            autoSyncAfterFolderPick = true;
            pickImageFolderInput.click();
        }

        async function uploadZipArchive() {
            if (!selectedZipFile) {
                setMessage('Pick a zip archive first.', true);
                return;
            }
            if (!token) {
                setMessage('Login is required to upload zip archives.', true);
                return;
            }
            if (!currentProjectId) {
                setMessage('Active project is unknown. Refresh first.', true);
                return;
            }

            const overwrite = !!document.getElementById('uploadOverwrite').checked;
            const prefix = document.getElementById('uploadTargetPrefix').value.trim();
            const body = new FormData();
            body.append('archive', selectedZipFile, selectedZipFile.name);
            body.append('target_prefix', prefix);
            body.append('expected_project_id', currentProjectId);
            body.append('overwrite', overwrite ? '1' : '0');

            const res = await fetch('/api/admin/images/upload-zip', {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
                body,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const detail = data.detail || data.error || `HTTP ${res.status}`;
                throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }

            setMessage(
                `Zip upload finished. uploaded=${data.uploaded || 0}, skipped=${data.skippedExisting || 0}, overwritten=${data.overwritten || 0}, failed=${data.failed || 0}, ignored-non-images=${data.ignoredNonImages || 0}`,
                Number(data.failed || 0) > 0,
            );
            await refreshAll(true);
        }

        async function loadProjectOptions() {
            const u = document.getElementById('username').value.trim();
            const p = document.getElementById('password').value;
            if (!u || !p) throw new Error('Enter username and password first');

            const data = await api('/api/auth/project-options', 'POST', { username: u, password: p });
            requireProjectPassword = !!data.requireProjectPassword;
            projectPasswordWrap.classList.toggle('hidden', !requireProjectPassword);
            renderProjects(data.projects || []);
            loginHint.textContent = `${(data.projects || []).length} project(s) found.`;
        }

        async function refreshAll(silent = false) {
            if (!token) return;
            try {
                const [status, summary, changes] = await Promise.all([
                    api('/api/sync/status'),
                    api('/api/project/summary'),
                    api('/api/project/recent-changes?limit=30'),
                ]);

                currentProjectId = String(status.projectId || summary.projectId || '');
                refreshUploadProjectBadge();

                role = status.role || summary.role || '';
                isAdmin = !!status.isAdmin;
                adminPanel.classList.toggle('hidden', false);
                document.body.classList.toggle('is-admin', isAdmin);
                if (!isAdmin && (activeDashPage === 'admin' || activeDashPage === 'images')) {
                    activeDashPage = 'uploads';
                }
                setDashboardPage(activeDashPage);

                renderSummary(summary, status);
                renderChanges(changes.changes || []);

                if (isAdmin) {
                    const users = await api('/api/admin/users');
                    renderUsers(users);
                    const storage = await api('/api/admin/project/storage');
                    document.getElementById('storageMode').value = storage.storageMode || 'auto';
                    document.getElementById('imageAccessMode').value = storage.imageAccessMode || 'local';
                    await loadBackups();
                    await loadDbTables();
                    await loadAdminImages();
                }
            } catch (e) {
                if (!silent) setMessage(e.message, true);
                if (String(e.message || '').includes('401')) {
                    handleLogout(false);
                }
            }
        }

        async function handleLogout(notify = true) {
            try {
                if (token) await api('/api/auth/logout', 'POST', {});
            } catch (_) {
            }
            closeSlideshow();
            token = '';
            role = '';
            isAdmin = false;
            document.body.classList.remove('is-admin');
            currentUser = '';
            currentProjectId = '';
            refreshUploadProjectBadge();
            localStorage.removeItem('syncToken');
            localStorage.removeItem('syncUsername');
            clearTimer();
            setLoggedInState(false);
            if (notify) setMessage('Logged out.');
            updateAuthPanelSignOutVisibility();
        }

        document.getElementById('showBootstrapBtn').addEventListener('click', () => {
            bootstrapBox.classList.toggle('hidden');
        });

        document.getElementById('bootstrapBox').addEventListener('submit', async (event) => {
            event.preventDefault();
            try {
                const payload = {
                    projectId: document.getElementById('bootProject').value.trim(),
                    projectPassword: document.getElementById('bootProjectPassword').value,
                    username: document.getElementById('bootUser').value.trim(),
                    password: document.getElementById('bootPass').value,
                };
                const bootstrapToken = document.getElementById('bootToken').value.trim();
                const headers = { 'Content-Type': 'application/json' };
                if (bootstrapToken) headers['X-Bootstrap-Token'] = bootstrapToken;
                await api('/api/admin/bootstrap', 'POST', payload, headers);
                setMessage('Bootstrap complete. Login using the owner credentials.');
                bootstrapBox.classList.add('hidden');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('loadProjectsBtn').addEventListener('click', async () => {
            try {
                await loadProjectOptions();
                setMessage('Projects loaded.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('loginBox').addEventListener('submit', async (event) => {
            event.preventDefault();
            setGlobalLoading(true, 'Logging in...');
            try {
                if (!projectIdSelect.value) {
                    await loadProjectOptions();
                }
                if (!projectIdSelect.value) throw new Error('Select a project before login');

                const payload = {
                    projectId: projectIdSelect.value,
                    projectPassword: document.getElementById('projectPassword').value,
                    username: document.getElementById('username').value.trim(),
                    password: document.getElementById('password').value,
                };

                const data = await api('/api/auth/login', 'POST', payload);
                token = data.token;
                currentUser = data.username || payload.username;
                role = data.role || (data.isAdmin ? 'admin' : 'user');
                isAdmin = !!data.isAdmin;
                localStorage.setItem('syncToken', token);
                localStorage.setItem('syncUsername', currentUser);
                setLoggedInState(true);
                await refreshAll();
                startTimer();
                setMessage(`Logged in as ${currentUser}`);
                updateAuthPanelSignOutVisibility();
            } catch (e) {
                setMessage(e.message, true);
            } finally {
                setGlobalLoading(false);
            }
        });

        signOutExistingBtn.addEventListener('click', async () => {
            await handleLogout(true);
        });

        document.getElementById('refreshBtn').addEventListener('click', () => refreshAll(false));
        document.getElementById('logoutBtn').addEventListener('click', () => handleLogout());
        compactModeBtn.addEventListener('click', cycleCompactModePreference);
        themeModeBtn.addEventListener('click', cycleThemeModePreference);

        dashNav.addEventListener('click', (event) => {
            const target = event.target;
            if (!target || !(target instanceof HTMLElement)) return;
            const button = target.closest('button[data-page]');
            if (!button) return;
            const page = button.getAttribute('data-page') || 'uploads';
            setDashboardPage(page);
        });

        document.getElementById('createUserBtn').addEventListener('click', async () => {
            try {
                const payload = {
                    username: document.getElementById('newUser').value.trim(),
                    password: document.getElementById('newPass').value,
                    isAdmin: !!document.getElementById('newIsAdmin').checked,
                };
                const data = await api('/api/admin/users', 'POST', payload);
                setMessage(`User ${data.username} created (${data.role}).`);
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('backupBtn').addEventListener('click', async () => {
            try {
                await api('/api/admin/backup-now', 'POST', {});
                setMessage('Backup created.');
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('saveBackupRetentionBtn').addEventListener('click', async () => {
            try {
                const retentionValue = Math.max(1, Number(backupRetentionValue.value || 14));
                const retentionUnit = String(backupRetentionUnit.value || 'days');
                const result = await api('/api/admin/backups/retention', 'POST', {
                    retentionValue,
                    retentionUnit,
                });
                setMessage(`Backup retention updated to ${result.retentionValue} ${result.retentionUnit} (${result.retentionDays} day(s)).`);
                await loadBackups();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('cleanupBackupsBtn').addEventListener('click', async () => {
            try {
                const yes = confirm('Run backup cleanup now using current retention rules?');
                if (!yes) return;
                const result = await api('/api/admin/backups/cleanup-now', 'POST', {});
                setMessage(`Backup cleanup complete. removed=${result.removed || 0}, remaining=${result.remaining || 0}.`);
                await loadBackups();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('refreshBackupsBtn').addEventListener('click', async () => {
            try {
                await loadBackups();
                setMessage('Backups refreshed.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('refreshDbTablesBtn').addEventListener('click', async () => {
            try {
                await loadDbTables();
                setMessage('Database tables refreshed.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('loadDbTableBtn').addEventListener('click', async () => {
            try {
                await loadDbRows();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('clearDbFilterBtn').addEventListener('click', async () => {
            try {
                dbTableSearch.value = '';
                dbSearchColumn.value = '';
                dbTableOffset.value = '0';
                await loadDbRows();
                setMessage('Database table filter cleared.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        dbTableSearch.addEventListener('keydown', async (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            try {
                dbTableOffset.value = '0';
                await loadDbRows();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        dbSearchColumn.addEventListener('change', async () => {
            try {
                dbTableOffset.value = '0';
                await loadDbRows();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        dbTableSelect.addEventListener('change', async () => {
            try {
                dbTableOffset.value = '0';
                await loadDbRows();
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('saveStorageBtn').addEventListener('click', async () => {
            try {
                const mode = document.getElementById('storageMode').value;
                await api('/api/admin/project/storage', 'POST', { storageMode: mode });
                setMessage(`Storage mode set to ${mode}.`);
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('saveImageAccessBtn').addEventListener('click', async () => {
            try {
                const mode = document.getElementById('imageAccessMode').value;
                await api('/api/admin/project/image-access', 'POST', { imageAccessMode: mode });
                setMessage(`Image access mode set to ${mode}.`);
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        document.getElementById('deleteSelfBtn').addEventListener('click', async () => {
            try {
                const yes = confirm('Delete your user account? This logs you out immediately.');
                if (!yes) return;
                await api(`/api/users/${encodeURIComponent(currentUser)}`, 'DELETE');
                setMessage('Your user was deleted.');
                await handleLogout(false);
                setMessage('Your user was deleted.');
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        usersBody.addEventListener('click', async (event) => {
            const target = event.target;
            if (!target || !target.matches('button[data-delete-user]')) return;
            const username = target.getAttribute('data-delete-user');
            if (!username) return;
            try {
                const yes = confirm(`Delete user ${username}?`);
                if (!yes) return;
                await api(`/api/users/${encodeURIComponent(username)}`, 'DELETE');
                setMessage(`User ${username} deleted.`);
                await refreshAll(true);
            } catch (e) {
                setMessage(e.message, true);
            }
        });

        backupsBody.addEventListener('click', async (event) => {
            const target = event.target;
            if (!target) return;
            try {
                if (target.matches('button[data-download-backup]')) {
                    const backupName = target.getAttribute('data-download-backup');
                    if (!backupName) return;
                    await downloadBackupFile(backupName);
                    setMessage(`Backup download started: ${backupName}`);
                    return;
                }

                if (target.matches('button[data-dryrun-backup]')) {
                    const backupName = target.getAttribute('data-dryrun-backup');
                    if (!backupName) return;
