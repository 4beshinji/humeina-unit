"""VoiSona Talk TTS Provider — REST API integration.

VoiSona Talk runs on a Windows VM and exposes a REST API.
Audio plays directly through VM speakers (SPICE → host audio).
"""

import asyncio
import time

import aiohttp
from loguru import logger

from .base import AudioResult, TTSProvider

API_BASE = "/api/talk/v1"
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 120.0


class VoisonaProvider(TTSProvider):
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.base_url = config.get("url", "http://192.168.1.173:32766")
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.voice_name = config.get("default_voice", "nurse-robot-type-t_ja_JP")
        self.language = config.get("language", "ja_JP")

        self._healthy = True
        self._synthesizing = False

        self._base_params: dict = {
            "speed": 1.0,
            "pitch": 0,
            "volume": 0,
            "intonation": 1.0,
            "huskiness": 0,
            "alp": 0,
        }
        self._tone_overrides: dict[str, dict] = {}

        for key in list(self._base_params):
            if key in config:
                self._base_params[key] = config[key]
        for tone_name, tone_cfg in config.get("tones", {}).items():
            if isinstance(tone_cfg, dict):
                self._tone_overrides[tone_name] = tone_cfg

    @property
    def name(self) -> str:
        return "voisona"

    @property
    def is_slow(self) -> bool:
        return True

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.username, self.password)

    @property
    def _api_url(self) -> str:
        return f"{self.base_url}{API_BASE}"

    def _build_params(self, tone: str, speed_override: float, **extra) -> dict:
        params = dict(self._base_params)

        if tone in self._tone_overrides:
            override = self._tone_overrides[tone]
            for key in ("speed", "pitch", "volume", "intonation", "huskiness", "alp"):
                if key in override and override[key] is not None:
                    params[key] = override[key]
            if "style_weights" in override:
                params["style_weights"] = override["style_weights"]

        # Apply extra params (from TTSParams)
        for key in ("pitch", "volume", "intonation", "huskiness", "alp"):
            if key in extra and extra[key] is not None:
                params[key] = params.get(key, 0) + extra[key]
        if "style_weights" in extra and extra["style_weights"] is not None:
            params["style_weights"] = extra["style_weights"]

        if speed_override != 1.0:
            params["speed"] = params["speed"] * speed_override

        # Clamp
        params["speed"] = max(0.2, min(5.0, params["speed"]))
        params["pitch"] = max(-600, min(600, params["pitch"]))
        params["volume"] = max(-8, min(8, params["volume"]))
        params["intonation"] = max(0, min(2, params["intonation"]))
        params["huskiness"] = max(-20, min(20, params["huskiness"]))
        params["alp"] = max(-1, min(1, params["alp"]))

        defaults = {
            "speed": 1.0,
            "pitch": 0,
            "volume": 0,
            "intonation": 1.0,
            "huskiness": 0,
            "alp": 0,
        }
        return {k: v for k, v in params.items() if k == "style_weights" or v != defaults.get(k)}

    def _build_body(self, text: str, voice: str, speed: float, **params) -> dict:
        global_params = self._build_params(voice, speed, **params)
        body: dict = {
            "language": self.language,
            "text": text,
            "voice_name": params.get("voice_id") or self.voice_name,
            "force_enqueue": True,
        }
        if global_params:
            body["global_parameters"] = global_params
        return body

    async def synthesize(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> AudioResult:
        body = self._build_body(text, voice, speed, **params)
        logger.debug(f"VoiSona synthesize: tone={voice}")

        self._synthesizing = True
        wall_start = time.monotonic()
        try:
            uuid = await self._post_synthesis(body)
            return await self._poll_synthesis(uuid, wall_start)
        finally:
            self._synthesizing = False

    async def enqueue_only(
        self, text: str, voice: str = "neutral", speed: float = 1.0, **params
    ) -> str:
        """POST synthesis to VoiSona queue without polling. Returns UUID."""
        body = self._build_body(text, voice, speed, **params)
        logger.debug(f"VoiSona enqueue: {text[:30]}...")
        return await self._post_synthesis(body)

    async def poll_until_done(self, uuid: str) -> AudioResult:
        """Poll a previously enqueued synthesis until completion."""
        wall_start = time.monotonic()
        return await self._poll_synthesis(uuid, wall_start)

    async def _post_synthesis(self, body: dict) -> str:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self._api_url}/speech-syntheses",
                json=body,
                auth=self._auth(),
            ) as resp:
                if resp.status != 201:
                    detail = await resp.text()
                    raise RuntimeError(
                        f"VoiSona speech-syntheses POST failed: {resp.status} {detail}"
                    )
                result = await resp.json()
        uuid = result["uuid"]
        logger.debug(f"VoiSona synthesis queued: {uuid}")
        return uuid

    async def _poll_synthesis(self, uuid: str, wall_start: float) -> AudioResult:
        """POST と同一セッションではなく専用セッションでポーリング.

        1リクエストずつ新規接続を確立し、接続を同時に複数持たない。
        """
        elapsed = 0.0
        while elapsed < POLL_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        f"{self._api_url}/speech-syntheses/{uuid}",
                        auth=self._auth(),
                    ) as resp:
                        if resp.status != 200:
                            continue
                        status = await resp.json()
                        state = status.get("state")
                        if state == "succeeded":
                            duration = status.get("duration", 0.0)
                            wall_elapsed = time.monotonic() - wall_start
                            self._healthy = True
                            logger.info(
                                f"VoiSona done: {duration:.2f}s "
                                f"(wall {wall_elapsed:.1f}s)"
                            )
                            return AudioResult(
                                audio_data=b"", format="wav", duration=duration
                            )
                        if state == "failed":
                            raise RuntimeError(f"VoiSona synthesis failed: {status}")
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug(f"Poll attempt failed ({elapsed:.0f}s): {e}")

        self._healthy = False
        raise RuntimeError(f"VoiSona synthesis timed out after {POLL_TIMEOUT}s")

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str,
        voice: str = "neutral",
        speed: float = 1.0,
        **params,
    ) -> AudioResult:
        """VoiSona destination:file モードで直接WAV出力."""
        body = self._build_body(text, voice, speed, **params)
        body["destination"] = "file"
        body["output_file_path"] = output_path

        logger.debug(f"VoiSona synthesize_to_file: {output_path}")

        self._synthesizing = True
        wall_start = time.monotonic()
        try:
            uuid = await self._post_synthesis(body)
            return await self._poll_synthesis(uuid, wall_start)
        finally:
            self._synthesizing = False

    async def is_available(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self._api_url}/voices", auth=self._auth()
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_voices(self) -> list[dict]:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self._api_url}/voices", auth=self._auth()
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return []
        except Exception:
            return []
