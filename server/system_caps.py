"""
System capability detection - Phase 1, Task 5.

On first boot the server detects its hardware and computes a "tier" used to
populate sensible defaults in system_settings. The admin can override any
setting; the auto-detected values stay readable for reference.

Detection handles:
  - Linux:    /proc/cpuinfo, /proc/meminfo, /sys/fs/cgroup (v1 + v2) for
              container CPU/memory limits
  - Windows:  os.cpu_count(), psutil if present, falls back to ctypes
  - macOS:    same as Linux + sysctl

Tiers
-----
  small  : <=4 effective cores, <=8 GB RAM   ~recommended for <=200 displays
  medium : 5-16 cores,         8-32 GB RAM   ~recommended for <=500 displays
  large  : >16 cores,          >32 GB RAM    ~recommended for <=1000 displays

Settings derived from tier
--------------------------
  job.max_concurrent_image_transcodes   small=1   medium=2   large=4
  job.max_concurrent_video_jobs         small=0   medium=1   large=2 (P2+)
  sse.max_concurrent_connections        small=200 medium=600 large=1200
  heartbeat.batch_seconds               60 (constant; tunable)
  upload.max_size_mb                    100 (constant; tunable)
  cache.in_memory_mb                    small=64  medium=256 large=512
"""
import os
import platform
import shutil
import sys


# -----------------------------------------------------------------------------
# Hardware detection
# -----------------------------------------------------------------------------
def _read_first_int(path):
    try:
        with open(path, 'r') as f:
            v = f.read().strip()
        return int(v)
    except (OSError, ValueError):
        return None


def _linux_cgroup_cpu_quota():
    """Return effective CPU count from cgroup limits, or None if unlimited /
    not a container."""
    # cgroup v2
    cpu_max = '/sys/fs/cgroup/cpu.max'
    if os.path.exists(cpu_max):
        try:
            with open(cpu_max, 'r') as f:
                parts = f.read().split()
            if len(parts) == 2 and parts[0] != 'max':
                quota, period = int(parts[0]), int(parts[1])
                return max(1, quota // period)
        except (OSError, ValueError):
            pass
    # cgroup v1
    quota = _read_first_int('/sys/fs/cgroup/cpu/cpu.cfs_quota_us')
    period = _read_first_int('/sys/fs/cgroup/cpu/cpu.cfs_period_us')
    if quota and quota > 0 and period and period > 0:
        return max(1, quota // period)
    return None


def _linux_cgroup_memory_limit():
    """Return memory limit in bytes from cgroup, or None if unlimited."""
    # cgroup v2
    mem_max = '/sys/fs/cgroup/memory.max'
    if os.path.exists(mem_max):
        try:
            with open(mem_max, 'r') as f:
                v = f.read().strip()
            if v != 'max':
                return int(v)
        except (OSError, ValueError):
            pass
    # cgroup v1
    v = _read_first_int('/sys/fs/cgroup/memory/memory.limit_in_bytes')
    if v and v < (1 << 62):
        return v
    return None


def _ram_bytes():
    """Total RAM in bytes (host or cgroup limit, whichever is smaller)."""
    host_ram = None
    if sys.platform == 'linux':
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    kb = int(line.split()[1])
                    host_ram = kb * 1024
                    break
        cg = _linux_cgroup_memory_limit()
        if cg and (host_ram is None or cg < host_ram):
            return cg
        return host_ram or 0
    # psutil if available -- works everywhere
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except ImportError:
        pass
    if sys.platform == 'win32':
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong),
                            ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong),
                            ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong),
                            ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong),
                            ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys)
        except Exception:
            return 0
    return 0


def _cpu_count():
    """Effective CPU count (host or cgroup limit, whichever is smaller)."""
    host = os.cpu_count() or 1
    if sys.platform == 'linux':
        cg = _linux_cgroup_cpu_quota()
        if cg and cg < host:
            return cg
    return host


def _free_disk_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except (OSError, FileNotFoundError):
        return 0


def _is_container():
    """Heuristic: are we running inside a container?"""
    if sys.platform != 'linux':
        return False
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'r') as f:
            data = f.read()
        return 'docker' in data or 'kubepods' in data or 'containerd' in data
    except OSError:
        return False


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def detect(uploads_path='.'):
    """Return a snapshot dict of detected system capabilities."""
    return {
        'cpu_count':        _cpu_count(),
        'ram_bytes':        _ram_bytes(),
        'free_disk_bytes':  _free_disk_bytes(uploads_path),
        'os':               platform.system(),
        'os_release':       platform.release(),
        'python_version':   platform.python_version(),
        'is_container':     _is_container(),
    }


def tier(caps=None):
    """Classify the host as small | medium | large based on detected caps."""
    caps = caps or detect()
    cpu = caps.get('cpu_count', 1)
    ram_gb = caps.get('ram_bytes', 0) / (1024 ** 3)
    if cpu <= 4 or ram_gb <= 8:
        return 'small'
    if cpu <= 16 or ram_gb <= 32:
        return 'medium'
    return 'large'


def defaults_for_tier(t):
    """Return the recommended setting defaults for a tier."""
    if t == 'small':
        return {
            'job.max_concurrent_image_transcodes': 1,
            'job.max_concurrent_video_jobs':       0,
            'sse.max_concurrent_connections':      200,
            'heartbeat.batch_seconds':             60,
            'upload.max_size_mb':                  100,
            'cache.in_memory_mb':                  64,
            'recommended_max_displays':            200,
        }
    if t == 'medium':
        return {
            'job.max_concurrent_image_transcodes': 2,
            'job.max_concurrent_video_jobs':       1,
            'sse.max_concurrent_connections':      600,
            'heartbeat.batch_seconds':             60,
            'upload.max_size_mb':                  200,
            'cache.in_memory_mb':                  256,
            'recommended_max_displays':            500,
        }
    return {  # large
        'job.max_concurrent_image_transcodes': 4,
        'job.max_concurrent_video_jobs':       2,
        'sse.max_concurrent_connections':      1200,
        'heartbeat.batch_seconds':             60,
        'upload.max_size_mb':                  500,
        'cache.in_memory_mb':                  512,
        'recommended_max_displays':            1000,
    }


def all_auto_settings(uploads_path='.'):
    """Convenience: detect + tier + defaults. Returns a flat dict suitable
    for writing into system_settings as auto_* records."""
    caps = detect(uploads_path)
    t = tier(caps)
    out = {
        'auto.detected_cpu_count':       caps['cpu_count'],
        'auto.detected_ram_bytes':       caps['ram_bytes'],
        'auto.detected_free_disk_bytes': caps['free_disk_bytes'],
        'auto.detected_os':              caps['os'],
        'auto.detected_is_container':    caps['is_container'],
        'auto.tier':                     t,
    }
    for k, v in defaults_for_tier(t).items():
        out[f'auto.{k}'] = v
    return out
