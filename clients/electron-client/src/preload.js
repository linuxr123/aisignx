const { contextBridge, ipcRenderer } = require('electron');

const signageApi = {
  getConfig:       ()      => ipcRenderer.invoke('get-config'),
  getDeviceId:     ()      => ipcRenderer.invoke('get-device-id'),
  getAppVersion:   ()      => ipcRenderer.invoke('get-app-version'),
  getSystemInfo:   ()      => ipcRenderer.invoke('get-system-info'),
  saveServerUrl:   (url)   => ipcRenderer.invoke('save-server-url', url),
  registerDevice:  (data)  => ipcRenderer.invoke('register-device', data),
  clearConfig:     ()      => ipcRenderer.invoke('clear-config'),
  checkForUpdate:  ()      => ipcRenderer.invoke('check-for-update'),
  setDisplayAutoUpdate: (on) => ipcRenderer.invoke('set-display-auto-update', on),
  applyClientConfig: (jsonText) => ipcRenderer.invoke('apply-client-config', jsonText),
  browseClientConfig: () => ipcRenderer.invoke('browse-client-config'),
  prepareSetupFilePicker: () => ipcRenderer.invoke('prepare-setup-file-picker'),
  finishSetupFilePicker: (state) => ipcRenderer.invoke('finish-setup-file-picker', state),
  runCommand:      (action, payload) => ipcRenderer.invoke('run-command', action, payload),
  unlockMinimize:  ()      => ipcRenderer.invoke('unlock-minimize'),
  onRelock:        (cb)    => ipcRenderer.on('relock-kiosk', (_e, info) => { try { cb(info); } catch (_) {} }),
  onApproved:      (cb)    => ipcRenderer.on('registration-approved', cb),
  onDeclined:      (cb)    => ipcRenderer.on('registration-declined', cb)
};

try {
  contextBridge.exposeInMainWorld('signage', signageApi);
  contextBridge.exposeInMainWorld('aisignxBridgeReady', true);
} catch (err) {
  console.error('[AISignX preload] failed to expose signage API:', err);
}
