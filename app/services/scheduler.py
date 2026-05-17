from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


_scheduler = None


def start_scheduler(app):
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()

    hour = app.config.get("FLEX_SYNC_HOUR", 18)

    def _sync_job():
        with app.app_context():
            from app.services.flex_query import sync_flex
            result = sync_flex()
            app.logger.info(f"Scheduled Flex sync: {result}")

    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=hour, minute=0),
        id="flex_sync",
        replace_existing=True,
    )
    _scheduler.start()
    app.logger.info(f"Scheduler started — Flex sync at {hour}:00 daily")
