/**
 * Digital Signage System - Main JavaScript
 * Version 1.0
 */

// DOM Ready function
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Initialize popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });
    
    // Handle sidebar toggle
    const sidebarToggle = document.querySelector('#sidebarToggle');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function(e) {
            e.preventDefault();
            document.body.classList.toggle('sb-sidenav-toggled');
            localStorage.setItem('sb|sidebar-toggle', document.body.classList.contains('sb-sidenav-toggled'));
        });
    }
    
    // Check for sidebar state in localStorage
    if (localStorage.getItem('sb|sidebar-toggle') === 'true') {
        document.body.classList.add('sb-sidenav-toggled');
    }
    
    // Setup AJAX CSRF token
    setupAjaxCSRF();
    
    // Initialize dataTables if present
    initializeDataTables();
    
    // Initialize socket.io if enabled
    initializeSocketIO();
});

/**
 * Set up AJAX CSRF token for all requests
 */
function setupAjaxCSRF() {
    // Get CSRF token from meta tag
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    
    if (csrfToken) {
        // Set up axios defaults if axios is used
        if (typeof axios !== 'undefined') {
            axios.defaults.headers.common['X-CSRF-TOKEN'] = csrfToken;
        }
        
        // Set up jQuery AJAX defaults if jQuery is used
        if (typeof $ !== 'undefined') {
            $.ajaxSetup({
                headers: {
                    'X-CSRF-TOKEN': csrfToken
                }
            });
        }
    }
}

/**
 * Initialize DataTables for table elements with class 'datatable'
 */
function initializeDataTables() {
    if (typeof $ !== 'undefined' && $.fn.DataTable) {
        $('.datatable').DataTable({
            responsive: true,
            language: {
                search: "_INPUT_",
                searchPlaceholder: "Search...",
                lengthMenu: "Show _MENU_ entries",
                info: "Showing _START_ to _END_ of _TOTAL_ entries"
            }
        });
    }
}

/**
 * Initialize Socket.IO connection for real-time updates
 */
function initializeSocketIO() {
    if (typeof io !== 'undefined') {
        // Connect to socket server
        const socket = io();
        
        // Listen for connection event
        socket.on('connect', function() {
            console.log('Socket.IO connected');
        });
        
        // Listen for display status updates
        socket.on('display_status_update', function(data) {
            updateDisplayStatus(data);
        });
        
        // Listen for general notifications
        socket.on('notification', function(data) {
            showNotification(data.message, data.type);
        });
    }
}

/**
 * Display notification toast
 * 
 * @param {string} message - Message to display
 * @param {string} type - Type of notification (success, info, warning, danger)
 */
function showNotification(message, type = 'info') {
    // Check if toasts container exists, if not create it
    let toastContainer = document.getElementById('toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toast-container';
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create toast
    const toastId = 'toast-' + Date.now();
    const toastElement = document.createElement('div');
    toastElement.className = 'toast';
    toastElement.id = toastId;
    toastElement.setAttribute('role', 'alert');
    toastElement.setAttribute('aria-live', 'assertive');
    toastElement.setAttribute('aria-atomic', 'true');
    
    // Set toast content
    toastElement.innerHTML = `
        <div class="toast-header bg-${type} text-white">
            <strong class="me-auto">${type.charAt(0).toUpperCase() + type.slice(1)}</strong>
            <small>${new Date().toLocaleTimeString()}</small>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">
            ${message}
        </div>
    `;
    
    // Add toast to container
    toastContainer.appendChild(toastElement);
    
    // Show toast using Bootstrap
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
    
    // Remove toast after it's hidden
    toastElement.addEventListener('hidden.bs.toast', function() {
        toastElement.remove();
    });
}

/**
 * Update display status in the UI
 * 
 * @param {Object} data - Display status data
 */
function updateDisplayStatus(data) {
    const displayElement = document.querySelector(`.display-item[data-id="${data.display_id}"]`);
    
    if (displayElement) {
        // Update status badge
        const statusBadge = displayElement.querySelector('.status-badge');
        if (statusBadge) {
            statusBadge.className = `badge status-badge bg-${data.online ? 'success' : 'danger'}`;
            statusBadge.textContent = data.online ? 'Online' : 'Offline';
        }
        
        // Update last seen
        const lastSeenElement = displayElement.querySelector('.last-seen');
        if (lastSeenElement) {
            lastSeenElement.textContent = data.last_seen || 'Never';
        }
        
        // Update screenshot if provided
        if (data.screenshot) {
            const screenshotElement = displayElement.querySelector('.display-screenshot');
            if (screenshotElement) {
                screenshotElement.src = `data:image/jpeg;base64,${data.screenshot}`;
                screenshotElement.alt = `${data.display_name} Screenshot`;
            }
        }
    }
}

/**
 * Format file size to human-readable format
 * 
 * @param {number} bytes - Size in bytes
 * @param {number} decimals - Decimal places
 * @returns {string} Formatted file size
 */
function formatFileSize(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Format date and time
 * 
 * @param {string|Date} dateTime - Date and time to format
 * @param {boolean} includeTime - Whether to include time
 * @returns {string} Formatted date and time
 */
function formatDateTime(dateTime, includeTime = true) {
    if (!dateTime) return '';
    
    const date = new Date(dateTime);
    
    if (isNaN(date)) return '';
    
    const options = {
        year: 'numeric', 
        month: 'short', 
        day: 'numeric'
    };
    
    if (includeTime) {
        options.hour = '2-digit';
        options.minute = '2-digit';
    }
    
    return date.toLocaleDateString(undefined, options);
}