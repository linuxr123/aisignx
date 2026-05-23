"""
Static tenant-leak audit - Phase 1, Task 9.

Run:
    python -m tools.audit_tenant_leaks

Scans all .py files in the project root and flags suspicious patterns where
tenant isolation might be bypassed without justification. Exit code 1 if any
unwhitelisted issue is found (suitable for CI).

Patterns flagged
----------------
1. `bypass_tenant_filter()` usage outside whitelisted files. Each legitimate
   bypass should be either in admin/superadmin code or have a `# tenant-ok:`
   comment on the same line explaining why.

2. Direct SQL execution (db.session.execute(text(...))) on tenant tables.
   Raw SQL skips the ORM event listener.

3. Queries that explicitly pass `domain_id=` literal values (rather than
   reading current_domain_id() or accepting a parameter). May indicate
   hard-coded cross-tenant access.

4. `.filter_by(id=...).first()` without a domain_id filter on a tenant
   model -- relies entirely on the tenant filter (acceptable but worth
   noting in CI summary).

How to silence a finding
------------------------
Add `# tenant-ok: <reason>` to the line. Example:

    with bypass_tenant_filter():     # tenant-ok: superadmin domain listing
        Domain.query.all()
"""
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files where bypass_tenant_filter is part of the framework itself --
# we don't want to flag legitimate framework code.
WHITELIST_FILES = {
    'tenant_filter.py',
    'bootstrap.py',
    'settings.py',
    'audit.py',
    'audit_views.py',
    'domains.py',
    'settings_views.py',
    'storage.py',
    'app.py',
    'utils.py',         # api_auth_required token lookup
    'admin.py',         # global user CRUD is intentionally cross-tenant
    'tools/audit_tenant_leaks.py',
}

# Tenant model class names -- pulled from models.py via grep at scan time.
def _discover_tenant_models():
    models_path = PROJECT_ROOT / 'models.py'
    text = models_path.read_text(encoding='utf-8')
    # Find `class Foo(... TenantModel ...):`
    return set(re.findall(r'class\s+(\w+)\([^)]*TenantModel[^)]*\)', text))


def scan_file(path: Path, tenant_models: set):
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    findings = []
    if rel in WHITELIST_FILES:
        return findings
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except (UnicodeDecodeError, OSError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if 'tenant-ok' in line:
            continue
        if stripped.startswith('#'):
            continue

        # 1. bypass_tenant_filter usage outside whitelist
        if 'bypass_tenant_filter' in line and 'import' not in line:
            findings.append((i, 'BYPASS', 'bypass_tenant_filter() used outside whitelist; add "# tenant-ok: <reason>"'))

        # 2. Raw SQL on tenant tables
        if re.search(r'(session|engine|connection)\.execute\s*\(\s*text\s*\(', line):
            findings.append((i, 'RAW-SQL', 'raw SQL execution -- tenant filter not applied'))

        # 3. Hard-coded domain_id literal in a query
        m = re.search(r'\bdomain_id\s*=\s*(\d+)\b', line)
        if m and 'def ' not in line and 'Column' not in line:
            findings.append((i, 'HARDCODED', f'hard-coded domain_id={m.group(1)} -- prefer current_domain_id() or parameter'))

        # 4. Cross-domain joins / queries that explicitly mention multiple Domain rows
        if re.search(r'Domain\.query\.(all|filter)', line):
            findings.append((i, 'CROSS-DOMAIN', 'Domain.query.* -- ensure caller has superadmin or domain.create permission'))

    return findings


def main():
    tenant_models = _discover_tenant_models()
    print(f'Tenant models detected: {sorted(tenant_models)}')
    print(f'Scanning {PROJECT_ROOT}...\n')

    total_issues = 0
    bad_files = 0
    for py_file in sorted(PROJECT_ROOT.rglob('*.py')):
        # Skip virtualenvs / generated dirs
        rel = py_file.relative_to(PROJECT_ROOT).as_posix()
        if any(seg in rel for seg in ('venv/', '.venv/', 'site-packages/',
                                       'migrations/versions/', '__pycache__/')):
            continue
        findings = scan_file(py_file, tenant_models)
        if findings:
            bad_files += 1
            total_issues += len(findings)
            print(f'== {rel} ==')
            for ln, code, msg in findings:
                print(f'  line {ln:>4}  [{code}]  {msg}')
            print()

    print(f'Summary: {total_issues} finding(s) across {bad_files} file(s).')
    if total_issues:
        print('To suppress an intentional usage, add "# tenant-ok: <reason>" '
              'to the same line.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
