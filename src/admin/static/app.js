const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } = Vue;

const app = createApp({
    delimiters: ['[[', ']]'],
    setup() {
        // === Navigation ===
        const currentTab = ref('dashboard');
        const tabs = [
            { id: 'dashboard', label: 'Dashboard', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>' },
            { id: 'sites', label: 'Sites', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"/></svg>' },
            { id: 'config', label: 'Configuration', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
            { id: 'logs', label: 'Logs', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>' },
        ];

        // === Status ===
        const status = ref({ sites: {}, active_sites: [], available_plugins: [] });

        // === Sites ===
        const sites = ref({});
        const plugins = ref({});

        // === Site Modal ===
        const siteModal = reactive({ open: false, editing: null, tab: 'connection' });
        const siteModalTabs = [
            { id: 'connection', label: 'Connection' },
            { id: 'mappings', label: 'Mappings' },
            { id: 'scraping', label: 'Scraping' },
            { id: 'advanced', label: 'Advanced' },
            { id: 'code', label: 'Code Editor' },
        ];
        const siteForm = reactive({});
        const customConfig = reactive({});
        const codeRefs = reactive({});

        // CodeMirror instances for advanced tab
        const advancedEditors = {};

        // === Code Editor ===
        const codeEditor = reactive({
            currentFile: null,
            content: '',
            loading: false,
            saving: false,
            instance: null,
        });
        const codeEditorContainer = ref(null);

        // === Config ===
        const configForm = reactive({
            api_key: '',
            cf_bypass_url: '',
            cf_bypass_timeout: 60000,
            log_level: 'INFO',
        });
        const configSaved = ref(false);

        // === Logs ===
        const logs = ref([]);
        const logFilter = ref('');
        const logPaused = ref(false);
        const logContainer = ref(null);
        let eventSource = null;

        // === Toasts ===
        const toasts = ref([]);

        // === Computed ===
        const currentManifest = computed(() => {
            const pid = siteForm.plugin;
            return pid ? plugins.value[pid] : null;
        });

        const filteredLogs = computed(() => {
            if (!logFilter.value) return logs.value;
            return logs.value.filter(l => l.level === logFilter.value);
        });

        // === Helpers ===
        function toast(message, type = 'success') {
            toasts.value.push({ message, type });
            setTimeout(() => toasts.value.shift(), 3000);
        }

        function formatUptime(seconds) {
            if (!seconds) return '0s';
            const d = Math.floor(seconds / 86400);
            const h = Math.floor((seconds % 86400) / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            if (d > 0) return `${d}d ${h}h`;
            if (h > 0) return `${h}h ${m}m`;
            return `${m}m`;
        }

        // === Tab switching ===
        function switchTab(tabId) {
            currentTab.value = tabId;
            if (tabId === 'sites') fetchSites();
            if (tabId === 'config') fetchConfig();
        }

        // === API calls ===
        async function fetchStatus() {
            try {
                const r = await fetch('/admin/api/status');
                status.value = await r.json();
            } catch (e) { console.error('Status fetch failed:', e); }
        }

        async function fetchSites() {
            try {
                const r = await fetch('/admin/api/sites');
                sites.value = await r.json();
            } catch (e) { console.error('Sites fetch failed:', e); }
        }

        async function fetchPlugins() {
            try {
                const r = await fetch('/admin/api/plugins');
                plugins.value = await r.json();
            } catch (e) { console.error('Plugins fetch failed:', e); }
        }

        async function fetchConfig() {
            try {
                const r = await fetch('/admin/api/config');
                const data = await r.json();
                Object.assign(configForm, {
                    api_key: data.api_key || '',
                    cf_bypass_url: data.cf_bypass_url || data.flaresolverr_url || '',
                    cf_bypass_timeout: data.cf_bypass_timeout || data.flaresolverr_timeout || 60000,
                    log_level: data.log_level || 'INFO',
                });
            } catch (e) { console.error('Config fetch failed:', e); }
        }

        async function saveConfig() {
            try {
                const r = await fetch('/admin/api/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(configForm),
                });
                if (r.ok) {
                    configSaved.value = true;
                    setTimeout(() => configSaved.value = false, 3000);
                    toast('Configuration saved');
                }
            } catch (e) { toast('Failed to save config', 'error'); }
        }

        // === Site Modal ===
        function resetSiteForm() {
            Object.keys(siteForm).forEach(k => delete siteForm[k]);
            Object.keys(customConfig).forEach(k => delete customConfig[k]);
            Object.assign(siteForm, { name: '', plugin: '', base_url: '', username: '', password: '' });
            // Destroy advanced editors
            Object.keys(advancedEditors).forEach(k => { delete advancedEditors[k]; });
            if (codeEditor.instance) {
                codeEditor.instance = null;
            }
            codeEditor.currentFile = null;
            codeEditor.content = '';
        }

        function openAddSite() {
            resetSiteForm();
            siteModal.open = true;
            siteModal.editing = null;
            siteModal.tab = 'connection';
        }

        function openEditSite(name, cfg) {
            resetSiteForm();
            const pluginId = cfg.plugin || cfg.type || '';
            Object.assign(siteForm, {
                name: name,
                plugin: pluginId,
                base_url: cfg.base_url || '',
                username: cfg.username || '',
                password: '',
            });

            // Load custom config into editable format
            const manifest = plugins.value[pluginId];
            if (manifest && manifest.custom_config) {
                const siteCustom = cfg.custom || {};
                for (const [key, schema] of Object.entries(manifest.custom_config)) {
                    const value = siteCustom[key] ?? schema.default;
                    if (schema.type === 'key_value_map' && value) {
                        customConfig[key] = Object.entries(value).map(([k, v]) => ({ key: String(k), value: String(v) }));
                    } else if (schema.type === 'integer_list' && value) {
                        customConfig[key] = [...value];
                    } else if (schema.type === 'code') {
                        customConfig[key] = value || '';
                    }
                }
            }

            siteModal.open = true;
            siteModal.editing = name;
            siteModal.tab = 'connection';
        }

        function closeSiteModal() {
            siteModal.open = false;
            siteModal.editing = null;
            resetSiteForm();
        }

        function onPluginChange() {
            const manifest = currentManifest.value;
            if (!manifest) return;
            // Set default values from manifest
            for (const [key, schema] of Object.entries(manifest.config_schema || {})) {
                if (schema.default && !siteForm[key]) {
                    siteForm[key] = schema.default;
                }
            }
        }

        // Custom config helpers
        function addKvRow(key) {
            if (!customConfig[key]) customConfig[key] = [];
            customConfig[key].push({ key: '', value: '' });
        }

        function removeKvRow(key, index) {
            customConfig[key].splice(index, 1);
        }

        function addListItem(key) {
            if (!customConfig[key]) customConfig[key] = [];
            customConfig[key].push(0);
        }

        function buildCustomPayload() {
            const manifest = currentManifest.value;
            if (!manifest || !manifest.custom_config) return {};

            const result = {};
            for (const [key, schema] of Object.entries(manifest.custom_config)) {
                if (schema.type === 'key_value_map' && customConfig[key]) {
                    const obj = {};
                    for (const row of customConfig[key]) {
                        if (row.key !== '') obj[row.key] = row.value;
                    }
                    result[key] = obj;
                } else if (schema.type === 'integer_list' && customConfig[key]) {
                    result[key] = customConfig[key].map(v => parseInt(v) || 0);
                } else if (schema.type === 'code') {
                    // Read from CodeMirror instance if exists
                    if (advancedEditors[key]) {
                        result[key] = advancedEditors[key].getValue();
                    } else if (customConfig[key] !== undefined) {
                        result[key] = customConfig[key];
                    }
                }
            }
            return result;
        }

        async function saveSite() {
            if (siteModal.editing) {
                const payload = {
                    base_url: siteForm.base_url,
                    username: siteForm.username,
                };
                if (siteForm.password) payload.password = siteForm.password;
                payload.custom = buildCustomPayload();

                try {
                    const r = await fetch(`/admin/api/sites/${siteModal.editing}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                    const data = await r.json();
                    if (r.ok) {
                        toast(data.warning || 'Site updated');
                        fetchSites();
                        fetchStatus();
                    } else {
                        toast(data.error || 'Update failed', 'error');
                    }
                } catch (e) { toast('Failed to update site', 'error'); }
            } else {
                const payload = {
                    name: siteForm.name,
                    plugin: siteForm.plugin,
                    base_url: siteForm.base_url,
                    username: siteForm.username,
                    password: siteForm.password,
                };
                try {
                    const r = await fetch('/admin/api/sites', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                    const data = await r.json();
                    if (r.ok || r.status === 201) {
                        toast(data.warning || 'Site created');
                        fetchSites();
                        fetchStatus();
                    } else {
                        toast(data.error || 'Failed to create site', 'error');
                    }
                } catch (e) { toast('Failed to create site', 'error'); }
            }
            closeSiteModal();
        }

        async function toggleSite(name) {
            try {
                const r = await fetch(`/admin/api/sites/${name}/toggle`, { method: 'POST' });
                const data = await r.json();
                if (r.ok) {
                    toast(`Site ${data.enabled ? 'enabled' : 'disabled'}`);
                    fetchSites();
                    fetchStatus();
                }
            } catch (e) { toast('Toggle failed', 'error'); }
        }

        async function deleteSite(name) {
            if (!confirm(`Delete site "${name}"? This action cannot be undone.`)) return;
            try {
                const r = await fetch(`/admin/api/sites/${name}`, { method: 'DELETE' });
                if (r.ok) {
                    toast('Site deleted');
                    fetchSites();
                    fetchStatus();
                }
            } catch (e) { toast('Delete failed', 'error'); }
        }

        // === Code Editor (plugin files) ===
        async function loadPluginFile(file) {
            const pluginId = siteForm.plugin;
            if (!pluginId) return;

            codeEditor.loading = true;
            codeEditor.currentFile = file.path;

            try {
                const r = await fetch(`/admin/api/plugins/${pluginId}/files/${file.path}`);
                const data = await r.json();
                if (r.ok) {
                    codeEditor.content = data.content;
                    await nextTick();
                    initCodeEditor(file);
                } else {
                    toast(data.error || 'Failed to load file', 'error');
                }
            } catch (e) {
                toast('Failed to load file', 'error');
            } finally {
                codeEditor.loading = false;
            }
        }

        function initCodeEditor(file) {
            if (codeEditor.instance) {
                codeEditor.instance.toTextArea();
                codeEditor.instance = null;
            }

            const container = codeEditorContainer.value;
            if (!container) return;

            // Create textarea
            container.innerHTML = '';
            const textarea = document.createElement('textarea');
            container.appendChild(textarea);
            textarea.value = codeEditor.content;

            const modeMap = { python: 'python', xml: 'xml', json: 'application/json' };
            codeEditor.instance = CodeMirror.fromTextArea(textarea, {
                mode: modeMap[file.language] || 'python',
                theme: 'material-darker',
                lineNumbers: true,
                lineWrapping: true,
                tabSize: 4,
                indentWithTabs: false,
                indentUnit: 4,
                matchBrackets: true,
                autoCloseBrackets: true,
            });
            codeEditor.instance.setSize('100%', '400px');
        }

        async function savePluginFile() {
            if (!codeEditor.instance || !codeEditor.currentFile) return;
            const pluginId = siteForm.plugin;
            codeEditor.saving = true;

            try {
                const r = await fetch(`/admin/api/plugins/${pluginId}/files/${codeEditor.currentFile}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: codeEditor.instance.getValue() }),
                });
                const data = await r.json();
                if (r.ok) {
                    toast('File saved');
                } else {
                    toast(data.error || 'Save failed', 'error');
                }
            } catch (e) {
                toast('Failed to save file', 'error');
            } finally {
                codeEditor.saving = false;
            }
        }

        // Initialize CodeMirror for advanced tab code fields
        watch(() => siteModal.tab, async (newTab) => {
            if (newTab === 'advanced' && currentManifest.value) {
                await nextTick();
                const manifest = currentManifest.value;
                for (const [key, schema] of Object.entries(manifest.custom_config || {})) {
                    if (schema.type === 'code' && schema.group === 'advanced' && codeRefs[key]) {
                        if (advancedEditors[key]) continue; // Already initialized

                        const el = codeRefs[key];
                        el.innerHTML = '';
                        const textarea = document.createElement('textarea');
                        el.appendChild(textarea);
                        textarea.value = customConfig[key] || schema.default || '';

                        const modeMap = { python: 'python', xml: 'xml', json: 'application/json' };
                        advancedEditors[key] = CodeMirror.fromTextArea(textarea, {
                            mode: modeMap[schema.language] || 'text/plain',
                            theme: 'material-darker',
                            lineNumbers: true,
                            lineWrapping: true,
                            tabSize: 4,
                            indentWithTabs: false,
                        });
                        advancedEditors[key].setSize('100%', '250px');
                    }
                }
            }
        });

        // === Log Stream ===
        function connectLogStream() {
            if (eventSource) eventSource.close();
            eventSource = new EventSource('/admin/api/logs');
            eventSource.onmessage = (e) => {
                if (logPaused.value) return;
                try {
                    const entries = JSON.parse(e.data);
                    logs.value.push(...entries);
                    if (logs.value.length > 1000) {
                        logs.value = logs.value.slice(-500);
                    }
                    nextTick(() => {
                        if (logContainer.value) {
                            logContainer.value.scrollTop = logContainer.value.scrollHeight;
                        }
                    });
                } catch (err) { console.error('Log parse error:', err); }
            };
            eventSource.onerror = () => {
                setTimeout(connectLogStream, 5000);
            };
        }

        // === Lifecycle ===
        let statusInterval;

        onMounted(() => {
            fetchStatus();
            fetchSites();
            fetchPlugins();
            fetchConfig();
            connectLogStream();
            statusInterval = setInterval(() => {
                fetchStatus();
                if (currentTab.value === 'sites') fetchSites();
            }, 10000);
        });

        onUnmounted(() => {
            if (eventSource) eventSource.close();
            clearInterval(statusInterval);
        });

        return {
            // Nav
            currentTab, tabs, switchTab,
            // Status
            status,
            // Sites
            sites, plugins,
            siteModal, siteModalTabs,
            siteForm, customConfig, codeRefs,
            currentManifest,
            openAddSite, openEditSite, closeSiteModal,
            onPluginChange,
            addKvRow, removeKvRow, addListItem,
            saveSite, toggleSite, deleteSite,
            // Code Editor
            codeEditor, codeEditorContainer,
            loadPluginFile, savePluginFile,
            // Config
            configForm, configSaved, saveConfig,
            // Logs
            logs, logFilter, logPaused, logContainer, filteredLogs,
            // Toasts
            toasts,
            // Helpers
            formatUptime,
        };
    }
});

app.mount('#app');
