from dataclasses import dataclass
from typing import Optional, Dict

@dataclass
class LCUEventCache:
    gameflow_session: Optional[Dict] = None
    champion_select: Optional[Dict] = None
    previous_phase: Optional[str] = None
