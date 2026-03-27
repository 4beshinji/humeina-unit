"""Studio module — batch TTS synthesis for talk-software video production."""

from .engine import StudioEngine
from .models import ScriptLine, SpeakerMapping, StudioProject, SynthResult
from .script_parser import ScriptParser

__all__ = [
    "ScriptLine",
    "ScriptParser",
    "SpeakerMapping",
    "StudioEngine",
    "StudioProject",
    "SynthResult",
]
