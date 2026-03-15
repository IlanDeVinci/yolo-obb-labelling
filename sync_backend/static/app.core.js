        const msg = document.getElementById('msg');
        const authPanel = document.getElementById('authPanel');
        const dashboardPanel = document.getElementById('dashboardPanel');
        const bootstrapWrap = document.getElementById('bootstrapWrap');
        const bootstrapBox = document.getElementById('bootstrapBox');
        const sessionText = document.getElementById('sessionText');
        const overviewSessionMirror = document.getElementById('overviewSessionMirror');
        const adminPanel = document.getElementById('adminPanel');
        const summaryCards = document.getElementById('summaryCards');
        const changesBody = document.getElementById('changesBody');
        const usersBody = document.getElementById('usersBody');
        const backupsBody = document.getElementById('backupsBody');
        const backupMeta = document.getElementById('backupMeta');
        const backupRetentionValue = document.getElementById('backupRetentionValue');
        const backupRetentionUnit = document.getElementById('backupRetentionUnit');
        const dbTableSelect = document.getElementById('dbTableSelect');
        const dbTableSearch = document.getElementById('dbTableSearch');
        const dbSearchColumn = document.getElementById('dbSearchColumn');
        const dbTableLimit = document.getElementById('dbTableLimit');
        const dbTableOffset = document.getElementById('dbTableOffset');
        const dbRowsHead = document.getElementById('dbRowsHead');
        const dbRowsBody = document.getElementById('dbRowsBody');
        const dbExplorerMeta = document.getElementById('dbExplorerMeta');
        const imagesBody = document.getElementById('imagesBody');
        const dashNav = document.getElementById('dashNav');
        const uploadDropZone = document.getElementById('uploadDropZone');
        const uploadQueueBody = document.getElementById('uploadQueueBody');
        const uploadSummary = document.getElementById('uploadSummary');
        const uploadProjectBadge = document.getElementById('uploadProjectBadge');
        const pickImageFilesInput = document.getElementById('pickImageFilesInput');
        const pickImageFolderInput = document.getElementById('pickImageFolderInput');
        const pickZipInput = document.getElementById('pickZipInput');
        const zipSelectedName = document.getElementById('zipSelectedName');
        const conflictSummary = document.getElementById('conflictSummary');
        const conflictExisting = document.getElementById('conflictExisting');
        const conflictChangedSize = document.getElementById('conflictChangedSize');
        const conflictChangedHash = document.getElementById('conflictChangedHash');
        const projectIdSelect = document.getElementById('projectIdSelect');
        const projectPasswordWrap = document.getElementById('projectPasswordWrap');
        const loginHint = document.getElementById('loginHint');
        const compactModeBtn = document.getElementById('compactModeBtn');
        const compactModeLabel = document.getElementById('compactModeLabel');
        const themeModeBtn = document.getElementById('themeModeBtn');
        const themeModeLabel = document.getElementById('themeModeLabel');
        const themeModeIconUse = document.getElementById('themeModeIconUse');
        const imagesSortBy = document.getElementById('imagesSortBy');
        const imagesSortOrder = document.getElementById('imagesSortOrder');
        const imagesDeleteLabels = document.getElementById('imagesDeleteLabels');
        const imagesSelectAll = document.getElementById('imagesSelectAll');
        const imagesSummary = document.getElementById('imagesSummary');
        const imagesOpsStatus = document.getElementById('imagesOpsStatus');
        const imagesOpsProgress = document.getElementById('imagesOpsProgress');
        const imagesOpsPercent = document.getElementById('imagesOpsPercent');
        const slideshowModal = document.getElementById('slideshowModal');
        const slideStage = document.getElementById('slideStage');
        const slideImage = document.getElementById('slideImage');
        const slideOverlay = document.getElementById('slideOverlay');
        const slideTitle = document.getElementById('slideTitle');
        const slideCounter = document.getElementById('slideCounter');
        const slideStatusText = document.getElementById('slideStatusText');
        const slideSourceText = document.getElementById('slideSourceText');
        const slideShowLabels = document.getElementById('slideShowLabels');
        const slideShuffle = document.getElementById('slideShuffle');
        const slideAutoplay = document.getElementById('slideAutoplay');
        const slideInterval = document.getElementById('slideInterval');
        const normalizeImagesBtn = document.getElementById('normalizeImagesBtn');
        const cancelNormalizeBtn = document.getElementById('cancelNormalizeBtn');
        const signOutExistingBtn = document.getElementById('signOutExistingBtn');
        const globalLoadingOverlay = document.getElementById('globalLoadingOverlay');
        const globalLoadingText = document.getElementById('globalLoadingText');

        let messageTimer = null;
        let activeNormalizeJobId = '';

        const DASH_PAGE_STORAGE_KEY = 'syncDashPage';
        const THEME_MODE_STORAGE_KEY = 'syncThemeMode';
        let token = localStorage.getItem('syncToken') || '';
        let currentUser = localStorage.getItem('syncUsername') || '';
        let currentProjectId = '';
        let role = '';
        let isAdmin = false;
        let requireProjectPassword = false;
        let refreshTimer = null;
        let activeDashPage = localStorage.getItem(DASH_PAGE_STORAGE_KEY) || 'uploads';
        let uploadQueue = [];
        let backupItems = [];
        let dbTables = [];
        let dbColumns = [];
        let selectedZipFile = null;
        let autoSyncAfterFolderPick = false;
        let compactPreference = localStorage.getItem('syncCompactMode') || 'auto';
        let themePreference = localStorage.getItem(THEME_MODE_STORAGE_KEY) || 'dark';
        let adminImages = [];
        const slideshowState = {
            open: false,
            index: 0,
            items: [],
            timer: null,
            showLabels: true,
            shuffle: false,
            autoplay: false,
            intervalMs: 2200,
            labelsByPath: new Map(),
            imageViewByPath: new Map(),
            basePaths: [],
        };
        const imageSuffixes = new Set(['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp']);

        function shouldUseAutoCompactMode() {
            return window.innerWidth <= 1366 || window.innerHeight <= 820;
        }

        function updateCompactModeLabel() {
            const map = {
                auto: 'Layout: Auto',
                on: 'Layout: Compact',
                off: 'Layout: Comfortable',
            };
            compactModeLabel.textContent = map[compactPreference] || map.auto;
        }

        function applyCompactMode() {
            const useCompact = compactPreference === 'on' || (compactPreference === 'auto' && shouldUseAutoCompactMode());
            document.body.classList.toggle('compact-mode', useCompact);
            updateCompactModeLabel();
        }

        function cycleCompactModePreference() {
            if (compactPreference === 'auto') {
                compactPreference = 'on';
            } else if (compactPreference === 'on') {
                compactPreference = 'off';
            } else {
                compactPreference = 'auto';
            }
            localStorage.setItem('syncCompactMode', compactPreference);
            applyCompactMode();
        }

        function updateThemeModeLabel() {
            if (!themeModeLabel) return;
            themeModeLabel.textContent = themePreference === 'light' ? 'Theme: Light' : 'Theme: Dark';
        }

        function updateThemeModeIcon() {
            if (!themeModeIconUse) return;
            const isLight = themePreference === 'light';
            themeModeIconUse.setAttribute('href', isLight ? '#i-sun' : '#i-theme');
        }

        function applyThemeMode() {
            const mode = themePreference === 'light' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', mode);
            updateThemeModeLabel();
            updateThemeModeIcon();
        }

        function cycleThemeModePreference() {
            themePreference = themePreference === 'dark' ? 'light' : 'dark';
            localStorage.setItem(THEME_MODE_STORAGE_KEY, themePreference);
            applyThemeMode();
        }

        function setMessage(text, isError = false, persist = false) {
            if (messageTimer) {
                clearTimeout(messageTimer);
                messageTimer = null;
            }
            msg.textContent = text || '';
            msg.className = isError ? 'msg err show' : 'msg show';
            if (!persist && text) {
                messageTimer = setTimeout(() => {
                    msg.className = isError ? 'msg err' : 'msg';
                }, 5600);
            }
        }

        function setImagesOpsStatus(text, isError = false) {
            if (!imagesOpsStatus) return;
            imagesOpsStatus.textContent = text || '';
            imagesOpsStatus.classList.toggle('err', !!isError);
        }

        function setImagesOpsProgress(processed, total) {
            if (!imagesOpsProgress || !imagesOpsPercent) return;
            const safeTotal = Math.max(1, Number(total || 1));
            const safeProcessed = Math.max(0, Math.min(Number(processed || 0), safeTotal));
            const pct = Math.max(0, Math.min(100, Math.round((safeProcessed / safeTotal) * 100)));
            imagesOpsProgress.max = safeTotal;
            imagesOpsProgress.value = safeProcessed;
            imagesOpsPercent.textContent = `${pct}%`;
        }

        function updateAuthPanelSignOutVisibility() {
            if (!signOutExistingBtn) return;
            const hasSession = !!token;
            signOutExistingBtn.classList.toggle('hidden', !hasSession);
        }

        function setGlobalLoading(active, text = 'Loading...') {
            if (!globalLoadingOverlay || !globalLoadingText) return;
            globalLoadingText.textContent = text;
            globalLoadingOverlay.classList.toggle('hidden', !active);
        }

        function fmtDate(ms) {
            if (!ms) return '-';
            const d = new Date(Number(ms));
            return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString();
        }

        function setLoggedInState(loggedIn) {
            authPanel.classList.toggle('hidden', loggedIn);
            dashboardPanel.classList.toggle('hidden', !loggedIn);
        }

        function setDashboardPage(page) {
            const allowed = new Set(['overview', 'activity', 'uploads', 'images', 'admin']);
            const requested = allowed.has(page) ? page : 'uploads';
            let next = requested;
            if (!isAdmin && (next === 'admin' || next === 'images')) {
                next = token ? requested : 'uploads';
            }
            activeDashPage = next;
            localStorage.setItem(DASH_PAGE_STORAGE_KEY, activeDashPage);

            const views = Array.from(document.querySelectorAll('#dashboardPanel .page-view[data-page]'));
            for (const view of views) {
                view.classList.toggle('hidden', view.getAttribute('data-page') !== next);
            }

            const navButtons = Array.from(document.querySelectorAll('#dashboardPanel .nav-btn[data-page]'));
            for (const btn of navButtons) {
                btn.classList.toggle('active', btn.getAttribute('data-page') === next);
            }
        }

        function refreshUploadProjectBadge() {
            const name = String(currentProjectId || '').trim();
            if (name) {
                uploadProjectBadge.textContent = `Uploading into: ${name}`;
                uploadProjectBadge.classList.remove('warn');
                return;
            }
            uploadProjectBadge.textContent = 'Uploading into: (not connected)';
            uploadProjectBadge.classList.add('warn');
        }

        function clearTimer() {
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = null;
            }
        }

        function startTimer() {
            clearTimer();
            if (!token) return;
            refreshTimer = setInterval(() => refreshAll(true), 5000);
        }

        async function api(path, method = 'GET', body = null, customHeaders = null) {
            const headers = customHeaders || { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const res = await fetch(path, {
                method,
                headers,
                body: body ? JSON.stringify(body) : null,
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const detail = data.detail || data.error || `HTTP ${res.status}`;
                throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }
            return data;
        }

        function renderProjects(projects) {
            const options = ['<option value="">Select a project...</option>'];
            for (const p of projects || []) {
                options.push(`<option value="${p.projectId}">${p.projectId} (${p.role || (p.isAdmin ? 'admin' : 'user')})</option>`);
            }
            projectIdSelect.innerHTML = options.join('');
            if ((projects || []).length === 1) {
                projectIdSelect.value = projects[0].projectId;
            }
        }

        function renderSummary(summary, status) {
            const cards = [
                { label: 'Project', value: summary.projectId || '-' },
                { label: 'Your Role', value: status.role || summary.role || '-' },
                { label: 'Image Access', value: summary.imageAccessMode || '-' },
                { label: 'Storage', value: summary.storageMode || '-' },
                { label: 'Online Users', value: status.onlineUsers || 0 },
                { label: 'Latest Seq', value: status.latestSeq || 0 },
                { label: 'Files', value: summary.totals?.files || 0 },
                { label: 'Changes', value: summary.totals?.changes || 0 },
            ];
            summaryCards.innerHTML = cards.map(c => `<article class="card-mini"><p>${c.label}</p><strong>${c.value}</strong></article>`).join('');
            const line = `${status.username || currentUser} on ${summary.projectId || '-'} | role: ${status.role || '-'} | active: ${status.activeFile || '-'}`;
            sessionText.textContent = line;
            overviewSessionMirror.textContent = line;
        }

        function renderChanges(changes) {
            if (!changes || !changes.length) {
                changesBody.innerHTML = '<tr><td colspan="5" class="muted">No changes yet.</td></tr>';
                return;
            }
            changesBody.innerHTML = changes.map(item => `
                <tr>
                    <td>${item.seq}</td>
                    <td>${item.username}</td>
                    <td class="mono">${item.path}</td>
                    <td>${item.deleted ? 'deleted' : 'updated'}</td>
                    <td>${fmtDate(item.createdAt)}</td>
                </tr>
            `).join('');
        }

        function renderUsers(payload) {
            const users = payload.users || [];
            if (!users.length) {
                usersBody.innerHTML = '<tr><td colspan="5" class="muted">No users.</td></tr>';
                return;
            }
            usersBody.innerHTML = users.map(u => {
                const deleteBtn = u.canDelete
                    ? `<button data-delete-user="${u.username}" class="btn danger small-btn">Delete</button>`
                    : '';
                return `
                    <tr>
                        <td>${u.username}</td>
                        <td>${u.role || (u.isAdmin ? 'admin' : 'user')}</td>
                        <td>${u.createdBy || '-'}</td>
                        <td>${u.createdAt || '-'}</td>
                        <td>${deleteBtn}</td>
                    </tr>
                `;
            }).join('');
        }

        function renderBackups(payload) {
            const items = Array.isArray(payload?.items) ? payload.items : [];
            backupItems = items;
            const retentionValue = Number(payload?.retentionValue || 14);
            const retentionUnit = String(payload?.retentionUnit || 'days');
            const retentionDays = Number(payload?.retentionDays || retentionValue || 14);

            backupRetentionValue.value = String(retentionValue);
            backupRetentionUnit.value = retentionUnit;
            backupMeta.textContent = `${items.length} backup(s) in ${payload?.backupDir || '-'}. Auto cleanup: ${retentionValue} ${retentionUnit} (${retentionDays} day(s)).`;

            if (!items.length) {
                backupsBody.innerHTML = '<tr><td colspan="5" class="muted">No backups found.</td></tr>';
                return;
            }

            backupsBody.innerHTML = items.map((item) => `
                <tr>
                    <td class="mono">${item.name}</td>
                    <td>${item.reason || '-'}</td>
                    <td>${bytesToHuman(item.sizeBytes || 0)}</td>
                    <td>${fmtDate(item.createdAt || item.modifiedAt || 0)}</td>
                    <td>
                        <button type="button" class="btn ghost small-btn" data-download-backup="${item.name}">Download</button>
                        <button type="button" class="btn ghost small-btn" data-dryrun-backup="${item.name}">Dry Run</button>
                        <button type="button" class="btn danger small-btn" data-restore-backup="${item.name}">Restore</button>
                    </td>
                </tr>
            `).join('');
        }

        async function loadBackups() {
            if (!isAdmin) return;
            const payload = await api('/api/admin/backups');
            renderBackups(payload);
        }

        function renderDbTables(payload) {
            dbTables = Array.isArray(payload?.tables) ? payload.tables : [];
            const current = String(dbTableSelect.value || '').trim();
            const options = ['<option value="">Select table...</option>'];
            for (const table of dbTables) {
                options.push(`<option value="${table}">${table}</option>`);
            }
            dbTableSelect.innerHTML = options.join('');
            if (current && dbTables.includes(current)) {
                dbTableSelect.value = current;
            } else if (dbTables.length) {
                dbTableSelect.value = dbTables[0];
            }
            dbExplorerMeta.textContent = `DB: ${payload?.dbPath || '-'} | ${dbTables.length} table(s)`;
            renderDbSearchColumns([]);
        }

        function renderDbSearchColumns(columns) {
            dbColumns = Array.isArray(columns) ? columns : [];
            const current = String(dbSearchColumn.value || '').trim();
            const options = ['<option value="">All columns</option>'];
            for (const col of dbColumns) {
                options.push(`<option value="${col}">${col}</option>`);
            }
            dbSearchColumn.innerHTML = options.join('');
            if (current && dbColumns.includes(current)) {
                dbSearchColumn.value = current;
            }
        }

        function renderDbRows(payload) {
            const columns = Array.isArray(payload?.columns) ? payload.columns : [];
            const rows = Array.isArray(payload?.rows) ? payload.rows : [];
            renderDbSearchColumns(columns);

            if (!columns.length) {
                dbRowsHead.innerHTML = '<tr><th>Row Data</th></tr>';
                dbRowsBody.innerHTML = '<tr><td class="muted">No columns returned.</td></tr>';
                return;
            }

            dbRowsHead.innerHTML = `<tr>${columns.map((col) => `<th>${col}</th>`).join('')}</tr>`;

            if (!rows.length) {
                dbRowsBody.innerHTML = `<tr><td colspan="${columns.length}" class="muted">No rows in this page.</td></tr>`;
                dbExplorerMeta.textContent = `${payload.table}: 0 row(s) in page. total=${payload.total || 0}`;
                return;
            }

            dbRowsBody.innerHTML = rows.map((row) => {
                const cells = columns.map((col) => {
                    const value = row?.[col];
                    if (value === null || value === undefined) return '<td class="muted">NULL</td>';
                    if (typeof value === 'object') {
                        return `<td class="mono">${JSON.stringify(value)}</td>`;
                    }
                    return `<td class="mono">${String(value)}</td>`;
                }).join('');
                return `<tr>${cells}</tr>`;
            }).join('');

            dbExplorerMeta.textContent = `${payload.table}: showing ${rows.length} row(s) from offset ${payload.offset || 0} (total ${payload.total || 0})`;
        }

        async function loadDbTables() {
            if (!isAdmin) return;
            const payload = await api('/api/admin/database/tables');
            renderDbTables(payload);
        }

        async function loadDbRows() {
            if (!isAdmin) return;
            const table = String(dbTableSelect.value || '').trim();
            if (!table) {
                throw new Error('Select a table first.');
            }
            const limit = Math.max(1, Math.min(500, Number(dbTableLimit.value || 100)));
            const offset = Math.max(0, Number(dbTableOffset.value || 0));
            const search = String(dbTableSearch.value || '').trim();
            const searchColumn = String(dbSearchColumn.value || '').trim();
            const payload = await api('/api/admin/database/table', 'POST', {
                table,
                limit,
                offset,
                search,
                searchColumn,
            });
            renderDbRows(payload);
        }

        async function downloadBackupFile(backupName) {
            if (!backupName) {
                throw new Error('Backup name is required.');
            }

            const headers = {};
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const res = await fetch(`/api/admin/backups/${encodeURIComponent(backupName)}/download`, {
                method: 'GET',
                headers,
            });

            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                const detail = data.detail || data.error || `HTTP ${res.status}`;
                throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
            }

            const blob = await res.blob();
            const objectUrl = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = backupName;
            document.body.appendChild(anchor);
