import logging
import time

from app.config.settings import settings
from app.database.connection import SessionLocal
from app.services.call_dispatch_service import CallDispatchService
from app.services.call_event_service import CallEventService
from app.services.call_orchestrator import CallOrchestrator
from app.services.conversation_maintenance_service import (
    ConversationMaintenanceService,
)
from app.observability import configure_logging
from app.services.auth_service import validate_production_settings

logger = logging.getLogger(__name__)


class OutboundCallWorker:

    def __init__(
        self,
        session_factory=SessionLocal,
        poll_interval: float = 2,
        maintenance_interval: int = settings.MAINTENANCE_INTERVAL_SECONDS,
    ):
        self.session_factory = session_factory
        self.poll_interval = poll_interval
        self.maintenance_interval = maintenance_interval
        self._last_maintenance = 0.0

    def run_once(self) -> bool:
        db = self.session_factory()
        try:
            maintained = False
            now = time.monotonic()
            if now - self._last_maintenance >= self.maintenance_interval:
                try:
                    recovered_sessions = (
                        ConversationMaintenanceService.recover_stale_sessions(db)
                    )
                    purged_transcripts = (
                        ConversationMaintenanceService.purge_expired_transcripts(db)
                    )
                    maintained = bool(recovered_sessions or purged_transcripts)
                except Exception:
                    db.rollback()
                    logger.exception("Conversation maintenance failed")
                finally:
                    self._last_maintenance = time.monotonic()

            recovered = CallDispatchService.recover_stale_dispatches(db)
            handled_events = CallEventService.process_pending_events(db)
            prepared_call = CallOrchestrator.prepare_next_call(db)
            dispatched = CallDispatchService.dispatch_next(db)
            return bool(
                maintained or recovered or handled_events or prepared_call or dispatched
            )
        finally:
            db.close()

    def run_forever(self):
        while True:
            try:
                did_work = self.run_once()
            except Exception:
                logger.exception("Outbound call worker iteration failed")
                did_work = False

            if not did_work:
                time.sleep(self.poll_interval)


def main():
    configure_logging()
    validate_production_settings()
    OutboundCallWorker().run_forever()


if __name__ == "__main__":
    main()
