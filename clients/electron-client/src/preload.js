const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('signage', {
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
  // Used by the player to relay admin-pushed commands (reboot / update / reload)
  runCommand:      (action, payload) => ipcRenderer.invoke('run-command', action, payload),
  onApproved:      (cb)    => ipcRenderer.on('registration-approved', cb),
  onDeclined:      (cb)    => ipcRenderer.on('registration-declined', cb)
});
