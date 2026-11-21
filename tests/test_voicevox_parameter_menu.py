"""VOICEVOXライブパラメーターメニューのテスト。"""

from __future__ import annotations

import asyncio

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from yomiage.interfaces.cli.voicevox_parameter_menu import (
    ParameterEditor,
    run_parameter_menu,
)

INITIAL = {
    "speed_scale": 1.0,
    "pitch_scale": 0.0,
    "intonation_scale": 1.0,
    "volume_scale": 1.0,
}


def test_parameter_editor_moves_adjusts_clamps_and_resets() -> None:
    editor = ParameterEditor(dict(INITIAL))

    assert editor.adjust(1) is True
    assert editor.values["speed_scale"] == 1.05
    editor.move(1)
    assert editor.adjust(-1) is True
    assert editor.values["pitch_scale"] == -0.01
    assert editor.reset_selected() is True
    assert editor.values["pitch_scale"] == 0.0

    editor.values["pitch_scale"] = -0.15
    assert editor.adjust(-1) is False
    assert editor.values["pitch_scale"] == -0.15


async def test_arrow_menu_edits_multiple_values_and_previews_each_settled_change() -> None:
    previews: list[dict[str, float]] = []

    async def preview(values: dict[str, float]) -> None:
        previews.append(values)

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            run_parameter_menu(
                INITIAL,
                preview=preview,
                input=pipe,
                output=DummyOutput(),
                preview_debounce_seconds=0.01,
            )
        )
        await asyncio.sleep(0.03)
        pipe.send_text("\x1b[C")  # 話速を上げる
        await asyncio.sleep(0.04)
        pipe.send_text("\x1b[B\x1b[C")  # ピッチへ移動して上げる
        await asyncio.sleep(0.04)
        pipe.send_text("\r")
        result = await task

    assert result == {
        "speed_scale": 1.05,
        "pitch_scale": 0.01,
        "intonation_scale": 1.0,
        "volume_scale": 1.0,
    }
    assert [item["pitch_scale"] for item in previews] == [0.0, 0.01]


async def test_new_change_cancels_stale_background_preview() -> None:
    started: list[float] = []
    cancelled: list[float] = []

    async def preview(values: dict[str, float]) -> None:
        speed = values["speed_scale"]
        started.append(speed)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(speed)
            raise

    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            run_parameter_menu(
                INITIAL,
                preview=preview,
                input=pipe,
                output=DummyOutput(),
                preview_debounce_seconds=0.01,
            )
        )
        await asyncio.sleep(0.03)
        pipe.send_text("\x1b[C")
        await asyncio.sleep(0.04)
        pipe.send_text("\x1b[C")
        await asyncio.sleep(0.04)
        pipe.send_text("\r")
        await task

    assert started == [1.05, 1.1]
    assert cancelled == [1.05, 1.1]
