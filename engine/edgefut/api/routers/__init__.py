from .events import router as events_router
from .radar import router as radar_router
from .system import router as system_router
from .tools import router as tools_router

__all__ = ["events_router", "radar_router", "system_router", "tools_router"]
