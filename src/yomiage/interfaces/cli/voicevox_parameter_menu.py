"""VOICEVOX音声パラメーターの矢印キー式ライブ調整UI。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output
from prompt_toolkit.styles import Style

PreviewCallback = Callable[[dict[str, float]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    digits: int


PARAMETERS = (
    ParameterSpec("speed_scale", "話速", 0.5, 2.0, 0.05, 2),
    ParameterSpec("pitch_scale", "ピッチ", -0.15, 0.15, 0.01, 2),
    ParameterSpec("intonation_scale", "抑揚", 0.0, 2.0, 0.05, 2),
    ParameterSpec("volume_scale", "音量", 0.0, 2.0, 0.05, 2),
)


@dataclass(slots=True)
class ParameterEditor:
    """UIから独立した選択位置・値変更ロジック。"""

    values: dict[str, float]
    selected: int = 0

    def move(self, direction: int) -> None:
        self.selected = (self.selected + direction) % len(PARAMETERS)

    def adjust(self, direction: int) -> bool:
        spec = PARAMETERS[self.selected]
        current = float(self.values[spec.key])
        changed = round(current + direction * spec.step, spec.digits)
        changed = min(spec.maximum, max(spec.minimum, changed))
        if changed == current:
            return False
        self.values[spec.key] = changed
        return True

    def reset_selected(self) -> bool:
        spec = PARAMETERS[self.selected]
        defaults = {
            "speed_scale": 1.0,
            "pitch_scale": 0.0,
            "intonation_scale": 1.0,
            "volume_scale": 1.0,
        }
        default = defaults[spec.key]
        if self.values[spec.key] == default:
            return False
        self.values[spec.key] = default
        return True


def _render(editor: ParameterEditor, status: str, *, preview_enabled: bool) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = [
        ("class:title", "VOICEVOX 音声パラメーター\n"),
        (
            "class:help",
            "↑↓ 項目選択  ←→ 値変更  R 初期値  Enter 決定  Esc キャンセル\n\n",
        ),
    ]
    for index, spec in enumerate(PARAMETERS):
        selected = index == editor.selected
        marker = "▶" if selected else " "
        value = editor.values[spec.key]
        ratio = (value - spec.minimum) / (spec.maximum - spec.minimum)
        filled = round(ratio * 20)
        gauge = "━" * filled + "─" * (20 - filled)
        style = "class:selected" if selected else "class:value"
        fragments.append(
            (
                style,
                f" {marker} {spec.label:<6} {value:.{spec.digits}f}  {gauge}\n",
            )
        )
    preview_note = status if preview_enabled else "自動試聴: 無効（--no-preview）"
    fragments.extend([("", "\n"), ("class:status", preview_note)])
    return fragments


async def run_parameter_menu(
    initial: dict[str, float],
    *,
    preview: PreviewCallback | None = None,
    input: Input | None = None,
    output: Output | None = None,
    preview_debounce_seconds: float = 0.2,
) -> dict[str, float] | None:
    """全パラメーターを一画面で編集し、変更ごとに非同期試聴する。"""

    editor = ParameterEditor(dict(initial))
    status = "値を変更すると自動で合成・再生します"
    preview_task: asyncio.Task[None] | None = None
    application: Application[dict[str, float] | None]

    def redraw() -> None:
        application.invalidate()

    async def preview_worker(snapshot: dict[str, float]) -> None:
        nonlocal status
        try:
            await asyncio.sleep(preview_debounce_seconds)
            status = "自動試聴: 合成中…"
            redraw()
            assert preview is not None
            await preview(snapshot)
            status = "自動試聴: 再生完了"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = f"自動試聴に失敗しました: {type(exc).__name__}"
        finally:
            redraw()

    def request_preview() -> None:
        nonlocal preview_task, status
        if preview is None:
            return
        if preview_task is not None and not preview_task.done():
            preview_task.cancel()
        status = "自動試聴: 更新待ち…"
        preview_task = asyncio.create_task(preview_worker(dict(editor.values)))

    def changed() -> None:
        request_preview()
        redraw()

    bindings = KeyBindings()

    @bindings.add("up")
    def move_up(_: KeyPressEvent) -> None:
        editor.move(-1)
        redraw()

    @bindings.add("down")
    def move_down(_: KeyPressEvent) -> None:
        editor.move(1)
        redraw()

    @bindings.add("left")
    def decrease(_: KeyPressEvent) -> None:
        if editor.adjust(-1):
            changed()

    @bindings.add("right")
    def increase(_: KeyPressEvent) -> None:
        if editor.adjust(1):
            changed()

    @bindings.add("r")
    def reset(_: KeyPressEvent) -> None:
        if editor.reset_selected():
            changed()

    @bindings.add("enter")
    def accept(event: KeyPressEvent) -> None:
        event.app.exit(result=dict(editor.values))

    @bindings.add("escape")
    @bindings.add("c-c")
    @bindings.add("q")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: _render(editor, status, preview_enabled=preview is not None),
        focusable=True,
    )
    application = Application(
        layout=Layout(Window(control, always_hide_cursor=True)),
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "title": "bold ansicyan",
                "help": "ansibrightblack",
                "selected": "bold ansiyellow",
                "value": "",
                "status": "ansigreen",
            }
        ),
        full_screen=False,
        erase_when_done=False,
        input=input,
        output=output,
    )
    try:
        return await application.run_async()
    finally:
        if preview_task is not None and not preview_task.done():
            preview_task.cancel()
            await asyncio.gather(preview_task, return_exceptions=True)


__all__ = ["PARAMETERS", "ParameterEditor", "run_parameter_menu"]
