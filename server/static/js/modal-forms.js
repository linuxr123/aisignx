/**
 * Wire Bootstrap modals so Enter in a field submits the primary action
 * (Create / Save / etc.), even when the button sits in modal-footer outside <form>.
 */
(function () {
  'use strict';

  const ACTION_RE = /\bbtn-(primary|info|success|danger|warning)\b/;

  function findModalPrimaryButton(modal) {
    const footer = modal.querySelector('.modal-footer');
    if (!footer) return null;
    const buttons = Array.from(footer.querySelectorAll('button')).filter((b) => {
      if (b.hasAttribute('data-bs-dismiss')) return false;
      if (b.classList.contains('btn-secondary')) return false;
      if (b.classList.contains('btn-close')) return false;
      return ACTION_RE.test(b.className);
    });
    return buttons.length ? buttons[buttons.length - 1] : null;
  }

  function ensureGhostSubmit(form) {
    if (form.querySelector('button[type="submit"]')) return;
    const ghost = document.createElement('button');
    ghost.type = 'submit';
    ghost.className = 'visually-hidden';
    ghost.setAttribute('aria-hidden', 'true');
    ghost.tabIndex = -1;
    ghost.textContent = 'Submit';
    form.appendChild(ghost);
  }

  function wireFormToButton(form, primary) {
    if (form.dataset.enterSubmitWired === '1') return;
    form.dataset.enterSubmitWired = '1';
    ensureGhostSubmit(form);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (primary.disabled) return;
      primary.click();
    });
  }

  const ENTER_INPUT_SEL = [
    '.modal-body input[type="text"]',
    '.modal-body input[type="url"]',
    '.modal-body input[type="email"]',
    '.modal-body input[type="search"]',
    '.modal-body input[type="number"]',
    '.modal-body input[type="password"]',
    '.modal-body input:not([type])',
    '.modal-body select',
  ].join(', ');

  function wireLooseInputs(modal, primary) {
    if (modal.dataset.enterLooseWired === '1') return;
    modal.dataset.enterLooseWired = '1';
    modal.querySelectorAll(ENTER_INPUT_SEL).forEach((el) => {
      el.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        if (e.isComposing) return;
        e.preventDefault();
        if (primary.disabled) return;
        primary.click();
      });
    });
  }

  function preventNativeNavigate(form) {
    if (form.dataset.enterSubmitWired === '1') return;
    form.dataset.enterSubmitWired = '1';
    form.addEventListener('submit', (e) => e.preventDefault());
  }

  function initModal(modal) {
    if (modal.dataset.noEnterSubmit === '1') return;
    const primary = findModalPrimaryButton(modal);
    if (!primary) return;

    const form = primary.closest('form') || modal.querySelector('form');

    if (primary.type === 'submit' && form) {
      preventNativeNavigate(form);
      return;
    }

    if (form) {
      wireFormToButton(form, primary);
      return;
    }
    if (modal.querySelector(ENTER_INPUT_SEL)) {
      wireLooseInputs(modal, primary);
    }
  }

  function initAll() {
    document.querySelectorAll('.modal').forEach(initModal);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
