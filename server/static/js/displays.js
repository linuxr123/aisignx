/**
 * Digital Signage System - Displays Page JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    // Display management specific functionality
    initializeDisplayManagement();
});

function initializeDisplayManagement() {
    // Setup display screenshot refresh
    setupScreenshotRefresh();
    
    // Setup display controls
    setupDisplayControls();
    
    // Setup display monitoring
    setupDisplayMonitoring();
}

function setupScreenshotRefresh() {
    const refreshButtons = document.querySelectorAll('.btn-refresh-screenshot');
    refreshButtons.forEach(button => {
        button.addEventListener('click', function() {
            const displayId = this.getAttribute('data-id');
            refreshScreenshot(displayId);
        });
    });
}

function refreshScreenshot(displayId) {
    // Show loading indicator
    const screenshotContainer = document.querySelector(`.display-item[data-id="${displayId}"] .screenshot-container`);
    if (screenshotContainer) {
        screenshotContainer.innerHTML = '<div class="d-flex justify-content-center align-items-center h-100"><div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div></div>';
    }
    
    // Request new screenshot
    fetch(`/api/displays/${displayId}/screenshot/refresh`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Update screenshot after a short delay
            setTimeout(() => {
                const screenshotImg = document.querySelector(`.display-item[data-id="${displayId}"] .display-screenshot`);
                if (screenshotImg) {
                    // Add timestamp to url to force refresh
                    screenshotImg.src = `/static/screenshots/${displayId}.jpg?t=${Date.now()}`;
                }
            }, 2000);
        } else {
            showNotification('Failed to refresh screenshot: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('Error refreshing screenshot:', error);
        showNotification('Error refreshing screenshot. See console for details.', 'danger');
    });
}

function setupDisplayControls() {
    // Reboot button handlers
    document.querySelectorAll('.btn-reboot-display').forEach(button => {
        button.addEventListener('click', function() {
            const displayId = this.getAttribute('data-id');
            const displayName = this.getAttribute('data-name');
            
            if (confirm(`Are you sure you want to reboot display "${displayName}"?`)) {
                rebootDisplay(displayId);
            }
        });
    });
    
    // Update button handlers
    document.querySelectorAll('.btn-update-display').forEach(button => {
        button.addEventListener('click', function() {
            const displayId = this.getAttribute('data-id');
            const displayName = this.getAttribute('data-name');
            
            if (confirm(`Are you sure you want to update software on display "${displayName}"?`)) {
                updateDisplaySoftware(displayId);
            }
        });
    });
}

function rebootDisplay(displayId) {
    fetch(`/api/displays/${displayId}/reboot`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification('Reboot command sent successfully. The display will reboot shortly.', 'success');
        } else {
            showNotification('Failed to send reboot command: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('Error sending reboot command:', error);
        showNotification('Error sending reboot command. See console for details.', 'danger');
    });
}

function updateDisplaySoftware(displayId) {
    fetch(`/api/displays/${displayId}/update`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').getAttribute('content')
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showNotification('Update command sent successfully. The display will update and restart.', 'success');
        } else {
            showNotification('Failed to send update command: ' + data.message, 'danger');
        }
    })
    .catch(error => {
        console.error('Error sending update command:', error);
        showNotification('Error sending update command. See console for details.', 'danger');
    });
}

function setupDisplayMonitoring() {
    // Set up automatic status refresh if socket.io is not available
    if (typeof io === 'undefined') {
        setInterval(() => {
            refreshDisplayStatuses();
        }, 60000); // Refresh every minute
    }
}

function refreshDisplayStatuses() {
    fetch('/api/displays/status')
    .then(response => response.json())
    .then(data => {
        if (data.displays) {
            data.displays.forEach(display => {
                updateDisplayStatus(display);
            });
        }
    })
    .catch(error => {
        console.error('Error refreshing display statuses:', error);
    });
}