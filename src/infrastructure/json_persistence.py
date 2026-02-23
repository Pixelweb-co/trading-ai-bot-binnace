import json
import os
import logging
from ..application.interfaces import IPersistenceService
from ..domain.models import SessionStats

log = logging.getLogger(__name__)

class JsonPersistenceService(IPersistenceService):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def save_stats(self, stats: SessionStats):
        try:
            data = {
                "gain": stats.total_gain,
                "loss": stats.total_loss,
                "wins": stats.total_wins,
                "losses": stats.total_losses,
                "martingale_losses": stats.consecutive_losses
            }
            with open(self.file_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.error(f"❌ Error saving stats: {e}")

    def load_stats(self) -> SessionStats:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r") as f:
                    data = json.load(f)
                    return SessionStats(
                        total_gain=data.get("gain", 0.0),
                        total_loss=data.get("loss", 0.0),
                        total_wins=data.get("wins", 0),
                        total_losses=data.get("losses", 0),
                        consecutive_losses=data.get("martingale_losses", {})
                    )
            except Exception as e:
                log.error(f"❌ Error loading stats: {e}")
        return SessionStats()
