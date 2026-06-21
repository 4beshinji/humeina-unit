"""Tests for SDK event hooks."""

from __future__ import annotations

from yomiage.api.hooks import Event, EventHooks, EventType


class TestEventHooks:
    def test_on_and_emit(self):
        hooks = EventHooks()
        received: list[Event] = []

        @hooks.on(EventType.SYNTHESIS_START)
        def handler(event: Event) -> None:
            received.append(event)

        hooks.emit_synthesis_start("hello", "voicevox", {"speed": 1.0})

        assert len(received) == 1
        assert received[0].type == EventType.SYNTHESIS_START
        assert received[0].payload["text"] == "hello"
        assert received[0].payload["engine"] == "voicevox"

    def test_off(self):
        hooks = EventHooks()

        @hooks.on(EventType.SYNTHESIS_END)
        def handler(event: Event) -> None:
            pass

        hooks.off(EventType.SYNTHESIS_END, handler)
        hooks.emit_synthesis_end("hello", "voicevox", {}, 100.0, False)
        # ハンドラーが外れていれば何も起きないことを確認

    def test_emit_batch_job_status(self):
        hooks = EventHooks()
        received: list[Event] = []

        @hooks.on(EventType.BATCH_JOB_STATUS)
        def handler(event: Event) -> None:
            received.append(event)

        hooks.emit_batch_job_status(
            "job123", "completed", 100.0, "done", "/tmp/out.wav"
        )

        assert len(received) == 1
        payload = received[0].payload
        assert payload["job_id"] == "job123"
        assert payload["status"] == "completed"
        assert payload["output_path"] == "/tmp/out.wav"

    def test_handler_exception_isolated(self):
        hooks = EventHooks()

        @hooks.on(EventType.SYNTHESIS_START)
        def bad_handler(_event: Event) -> None:
            raise RuntimeError("boom")

        @hooks.on(EventType.SYNTHESIS_START)
        def good_handler(event: Event) -> None:
            received.append(event)

        received: list[Event] = []
        hooks.emit_synthesis_start("hello", "voicevox", {})

        assert len(received) == 1
