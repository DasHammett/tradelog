from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
_scheduler = None
def start_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler()
    hour = app.config.get("FLEX_SYNC_HOUR", 6)
    def _sync_job():
        with app.app_context():
            from app.services.flex_query import sync_flex
            result = sync_flex()
            if result["ok"]:
                app.logger.info(
                    f"Scheduled Flex sync: {result.get('new_rows', 0)} new row(s), "
                    f"{len(result.get('skipped', []))} skipped"
                )
            else:
                app.logger.warning(f"Scheduled Flex sync failed: {result.get('message')}")
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=hour, minute=0),
        id="flex_sync",
        replace_existing=True,
    )
    _scheduler.start()
    app.logger.info(f"Scheduler started — Flex sync at {hour}:00 daily (server local time)")
