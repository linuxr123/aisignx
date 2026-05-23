"""
Scheduled backup runner.

Settings consulted on every tick (so changes take effect on the next run
without a restart):

    backup.schedule.enabled          bool   master switch
    backup.schedule.interval_hours   int    cadence (used at install time)
    backup.schedule.include_uploads  bool   include uploads tree in archive
    backup.schedule.include_plugins  bool   include plugins tree in archive
    backup.schedule.retain           int    how many archives to keep (0 = unlimited)
    backup.location                  str    target directory (consumed by backup.py)

The scheduler uses jobs.schedule_periodic() so it lives inside the same
in-process worker pool as audit retention and disk monitoring -- no
external scheduler/broker required.
"""
from logging_config import logger
import settings as settings_module
import backup


_JOB_NAME = 'scheduled-backup'


def run_now():
    """One scheduled-backup pass. Safe to call manually; never raises."""
    try:
        if not bool(settings_module.effective_value('backup.schedule.enabled')):
            return None
        include_uploads = bool(settings_module.effective_value(
            'backup.schedule.include_uploads'))
        include_plugins = bool(settings_module.effective_value(
            'backup.schedule.include_plugins'))
        retain = int(settings_module.effective_value(
            'backup.schedule.retain') or 0)

        info = backup.create_backup(
            include_uploads=include_uploads,
            include_plugins=include_plugins,
            source='scheduled',
        )
        logger.info(f'backup.scheduler: created {getattr(info, "name", info)}')

        if retain > 0:
            pruned = backup.prune_backups(retain)
            if pruned:
                logger.info(
                    f'backup.scheduler: pruned {len(pruned)} old archive(s)')
        return info
    except Exception as e:
        # Never let a backup failure kill the worker thread.
        logger.error(f'backup.scheduler: run failed: {e}')
        return None


def install():
    """Register the periodic backup job. Call exactly once at startup."""
    from jobs import schedule_periodic
    hours = int(settings_module.effective_value(
        'backup.schedule.interval_hours') or 24)
    hours = max(1, hours)
    schedule_periodic(run_now, every_s=hours * 3600.0, name=_JOB_NAME)
    logger.info(
        f'backup.scheduler: installed (every {hours}h, '
        f'enabled={bool(settings_module.effective_value("backup.schedule.enabled"))})'
    )
