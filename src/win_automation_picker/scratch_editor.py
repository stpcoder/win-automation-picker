from __future__ import annotations

from dataclasses import dataclass
import math
import tkinter as tk
from typing import Callable

from .block_tree import BlockPath, get_step
from .recipe import AutomationRecipe, AutomationStep


CONTAINER_KINDS = {"repeat", "if_exists", "if_text", "if_color", "monitor_group"}


@dataclass(frozen=True)
class DropZone:
    parent_path: BlockPath
    index: int
    x1: float
    x2: float
    y: float
    depth: int


def choose_drop_zone(zones: list[DropZone], x: float, y: float) -> DropZone | None:
    if not zones:
        return None

    def score(zone: DropZone) -> float:
        if x < zone.x1:
            horizontal = zone.x1 - x
        elif x > zone.x2:
            horizontal = x - zone.x2
        else:
            horizontal = 0.0
        return abs(zone.y - y) + horizontal * 0.35 - zone.depth * 8.0

    nearby = [zone for zone in zones if zone.x1 - 54 <= x <= zone.x2 + 54]
    return min(nearby or zones, key=score)


class ScratchWorkspace(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        on_select: Callable[[BlockPath | None], None],
        on_move: Callable[[BlockPath, BlockPath, int], None],
        on_delete: Callable[[BlockPath], None],
        on_duplicate: Callable[[BlockPath], None],
        on_rename: Callable[[BlockPath], None],
        color_for_step: Callable[[AutomationStep], str],
        subtitle_for_step: Callable[[AutomationStep], str],
        **kwargs: object,
    ) -> None:
        super().__init__(master, **kwargs)
        self._on_select = on_select
        self._on_move = on_move
        self._on_delete = on_delete
        self._on_duplicate = on_duplicate
        self._on_rename = on_rename
        self._color_for_step = color_for_step
        self._subtitle_for_step = subtitle_for_step
        self._recipe = AutomationRecipe()
        self._selected_path: BlockPath | None = None
        self._drop_zones: list[DropZone] = []
        self._drag_source: BlockPath | None = None
        self._drag_start: tuple[float, float] | None = None
        self._dragging = False
        self._active_drop: DropZone | None = None
        self._render_pending: str | None = None

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Control-d>", self._on_duplicate_key)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-4>", lambda _event: self.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda _event: self.yview_scroll(3, "units"))

    def render(self, recipe: AutomationRecipe, selected_path: BlockPath | None = None) -> None:
        self._recipe = recipe
        self._selected_path = selected_path
        self.delete("all")
        self._drop_zones = []
        width = max(480, self.winfo_width() - 34)
        self._draw_background(width)
        y = 24.0
        self._draw_start_hat(22, y, width - 22)
        y += 68
        y = self._draw_list(recipe.steps, (), x=22, y=y, width=width - 22, depth=0)
        if not recipe.steps:
            self.create_text(
                54,
                y + 18,
                anchor="nw",
                text="왼쪽 블록을 이곳으로 끌어오세요",
                fill="#64748b",
                font=("TkDefaultFont", 12, "bold"),
            )
            self.create_text(
                54,
                y + 48,
                anchor="nw",
                text="클릭 녹화부터 시작하면 대상 선택과 이름 지정이 이어집니다.",
                fill="#94a3b8",
                font=("TkDefaultFont", 10),
            )
            y += 108
        self.configure(scrollregion=(0, 0, width + 24, max(y + 32, self.winfo_height())))

    def selected_path(self) -> BlockPath | None:
        return self._selected_path

    def contains_root_point(self, x_root: int, y_root: int) -> bool:
        left = self.winfo_rootx()
        top = self.winfo_rooty()
        return left <= x_root <= left + self.winfo_width() and top <= y_root <= top + self.winfo_height()

    def destination_at_root(self, x_root: int, y_root: int) -> DropZone | None:
        if not self.contains_root_point(x_root, y_root):
            return None
        x = self.canvasx(x_root - self.winfo_rootx())
        y = self.canvasy(y_root - self.winfo_rooty())
        return choose_drop_zone(self._drop_zones, x, y)

    def show_external_drop(self, x_root: int, y_root: int) -> DropZone | None:
        self._autoscroll_root_point(x_root, y_root)
        zone = self.destination_at_root(x_root, y_root)
        self._show_drop_indicator(zone)
        return zone

    def clear_drop_indicator(self) -> None:
        self.delete("drop_indicator")
        self._active_drop = None

    def _autoscroll_root_point(self, x_root: int, y_root: int) -> None:
        if not self.contains_root_point(x_root, y_root):
            return
        local_y = y_root - self.winfo_rooty()
        if local_y < 34:
            self.yview_scroll(-2, "units")
        elif local_y > self.winfo_height() - 34:
            self.yview_scroll(2, "units")

    def _on_configure(self, _event: tk.Event[tk.Misc]) -> None:
        if self._render_pending is not None:
            self.after_cancel(self._render_pending)
        self._render_pending = self.after(60, self._render_after_resize)

    def _render_after_resize(self) -> None:
        self._render_pending = None
        self.render(self._recipe, self._selected_path)

    def _draw_background(self, width: float) -> None:
        height = max(self.winfo_height(), 900)
        for x in range(20, int(width) + 20, 24):
            for y in range(18, int(height), 24):
                self.create_oval(x, y, x + 2, y + 2, fill="#dbe4ee", outline="")

    def _draw_start_hat(self, x: float, y: float, width: float) -> None:
        points = [
            x,
            y + 18,
            x + 8,
            y + 5,
            x + 26,
            y,
            x + width - 12,
            y,
            x + width,
            y + 12,
            x + width,
            y + 48,
            x + 76,
            y + 48,
            x + 68,
            y + 56,
            x + 50,
            y + 56,
            x + 42,
            y + 48,
            x + 12,
            y + 48,
            x,
            y + 36,
        ]
        self.create_polygon(points, fill="#0f766e", outline="#0b5f59", width=1)
        self.create_oval(x + 18, y + 15, x + 34, y + 31, fill="#ccfbf1", outline="")
        self.create_polygon(
            [x + 24, y + 19, x + 24, y + 27, x + 30, y + 23],
            fill="#0f766e",
            outline="",
        )
        self.create_text(
            x + 44,
            y + 23,
            anchor="w",
            text="매크로를 실행하면",
            fill="#ffffff",
            font=("TkDefaultFont", 11, "bold"),
        )

    def _measure_step(self, step: AutomationStep, width: float, depth: int) -> float:
        if step.kind not in CONTAINER_KINDS:
            return 66.0
        _child_indent, child_width = self._child_geometry(width)
        child_height = 0.0
        for child in step.children:
            child_height += self._measure_step(child, child_width, depth + 1) + 10.0
        body_height = max(64.0, child_height + 14.0)
        return 62.0 + body_height + 28.0

    def _draw_list(
        self,
        steps: list[AutomationStep],
        parent_path: BlockPath,
        *,
        x: float,
        y: float,
        width: float,
        depth: int,
    ) -> float:
        self._drop_zones.append(DropZone(parent_path, 0, x, x + width, y - 7, depth))
        if not steps:
            self._draw_empty_slot(x, y, width, parent_path)
            return y + 54
        for index, step in enumerate(steps):
            path = (*parent_path, index)
            height = self._measure_step(step, width, depth)
            self._draw_step(path, step, x=x, y=y, width=width, height=height, depth=depth)
            y += height + 10
            self._drop_zones.append(DropZone(parent_path, index + 1, x, x + width, y - 6, depth))
        return y

    def _draw_empty_slot(self, x: float, y: float, width: float, parent_path: BlockPath) -> None:
        self.create_rectangle(
            x + 14,
            y,
            x + width - 14,
            y + 38,
            fill="#ffffff",
            outline="#94a3b8",
            dash=(4, 4),
            width=1,
        )
        self.create_text(
            x + width / 2,
            y + 19,
            text="여기에 블록 놓기" if parent_path else "첫 블록 놓기",
            fill="#64748b",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _draw_step(
        self,
        path: BlockPath,
        step: AutomationStep,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        depth: int,
    ) -> None:
        tag = self._path_tag(path)
        tags = ("block", tag)
        color = self._color_for_step(step)
        selected = path == self._selected_path
        outline = "#fbbf24" if selected else self._shade(color, -0.16)
        outline_width = 4 if selected else 1

        if step.kind in CONTAINER_KINDS:
            header_height = 62.0
            footer_height = 28.0
            body_top = y + header_height
            body_bottom = y + height - footer_height
            self._create_stack_shape(x, y, width, header_height, color, outline, outline_width, tags)
            self.create_rectangle(
                x,
                y + header_height - 10,
                x + 18,
                body_bottom + 7,
                fill=color,
                outline=outline,
                width=outline_width,
                tags=tags,
            )
            self._create_stack_shape(
                x,
                body_bottom,
                width,
                footer_height,
                color,
                outline,
                outline_width,
                tags,
                notch=False,
            )
            self._draw_block_text(path, step, x=x, y=y, width=width, tags=tags)
            child_indent, child_width = self._child_geometry(width)
            child_x = x + child_indent
            self._draw_list(
                step.children,
                path,
                x=child_x,
                y=body_top + 8,
                width=child_width,
                depth=depth + 1,
            )
            return

        self._create_stack_shape(x, y, width, height, color, outline, outline_width, tags)
        self._draw_block_text(path, step, x=x, y=y, width=width, tags=tags)

    def _child_geometry(self, width: float) -> tuple[float, float]:
        if width > 360:
            indent, right_gap = 30.0, 12.0
        elif width > 260:
            indent, right_gap = 18.0, 10.0
        else:
            indent, right_gap = 0.0, 0.0
        return indent, max(180.0, width - indent - right_gap)

    def _draw_block_text(
        self,
        path: BlockPath,
        step: AutomationStep,
        *,
        x: float,
        y: float,
        width: float,
        tags: tuple[str, str],
    ) -> None:
        number = ".".join(str(index + 1) for index in path)
        self.create_oval(x + 15, y + 16, x + 40, y + 41, fill="#ffffff", outline="", tags=tags)
        self.create_text(
            x + 27.5,
            y + 28.5,
            text=number,
            fill=self._color_for_step(step),
            font=("TkDefaultFont", 8, "bold"),
            tags=tags,
        )
        self.create_text(
            x + 52,
            y + 13,
            anchor="nw",
            text=self._ellipsize(step.block_title(), 44),
            width=max(140, width - 70),
            fill="#ffffff",
            font=("TkDefaultFont", 11, "bold"),
            tags=tags,
        )
        self.create_text(
            x + 52,
            y + 38,
            anchor="nw",
            text=self._ellipsize(self._subtitle_for_step(step), 58),
            width=max(140, width - 70),
            fill="#f8fafc",
            font=("TkDefaultFont", 9),
            tags=tags,
        )

    def _create_stack_shape(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: str,
        outline: str,
        outline_width: int,
        tags: tuple[str, str],
        *,
        notch: bool = True,
    ) -> None:
        points = self._shape_points(x, y, width, height, notch=notch)
        self.create_polygon(
            self._shape_points(x + 3, y + 4, width, height, notch=notch),
            fill="#c5d0dc",
            outline="",
            tags=tags,
        )
        self.create_polygon(points, fill=fill, outline=outline, width=outline_width, tags=tags)

    def _shape_points(self, x: float, y: float, width: float, height: float, *, notch: bool) -> list[float]:
        if not notch:
            return [x, y, x + width, y, x + width, y + height, x, y + height]
        return [
            x,
            y,
            x + 40,
            y,
            x + 48,
            y + 8,
            x + 68,
            y + 8,
            x + 76,
            y,
            x + width,
            y,
            x + width,
            y + height,
            x + 76,
            y + height,
            x + 68,
            y + height - 8,
            x + 48,
            y + height - 8,
            x + 40,
            y + height,
            x,
            y + height,
        ]

    def _path_tag(self, path: BlockPath) -> str:
        return "block__" + "_".join(str(index) for index in path)

    def _path_from_current(self) -> BlockPath | None:
        current = self.find_withtag("current")
        if not current:
            return None
        for tag in self.gettags(current[0]):
            if not tag.startswith("block__"):
                continue
            raw = tag.removeprefix("block__")
            try:
                return tuple(int(part) for part in raw.split("_") if part != "")
            except ValueError:
                return None
        return None

    def _on_press(self, event: tk.Event[tk.Misc]) -> None:
        self.focus_set()
        path = self._path_from_current()
        self._drag_source = path
        self._drag_start = (self.canvasx(event.x), self.canvasy(event.y))
        self._dragging = False
        self._on_select(path)

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag_source is None or self._drag_start is None:
            return
        x = self.canvasx(event.x)
        y = self.canvasy(event.y)
        if not self._dragging:
            distance = math.hypot(x - self._drag_start[0], y - self._drag_start[1])
            if distance < 7:
                return
            self._dragging = True
        self._autoscroll_root_point(event.x_root, event.y_root)
        x = self.canvasx(event.x)
        y = self.canvasy(event.y)
        zone = choose_drop_zone(self._drop_zones, x, y)
        if zone and zone.parent_path[: len(self._drag_source)] == self._drag_source:
            zone = None
        self._show_drop_indicator(zone)
        self.delete("drag_ghost")
        try:
            step = get_step(self._recipe, self._drag_source)
            title = self._ellipsize(step.block_title(), 30)
        except Exception:
            title = "블록 이동"
        self.create_rectangle(
            x + 14,
            y + 14,
            x + 230,
            y + 48,
            fill="#0f172a",
            outline="#ffffff",
            width=1,
            stipple="gray50",
            tags="drag_ghost",
        )
        self.create_text(
            x + 26,
            y + 31,
            anchor="w",
            text=title,
            fill="#ffffff",
            font=("TkDefaultFont", 9, "bold"),
            tags="drag_ghost",
        )

    def _on_release(self, _event: tk.Event[tk.Misc]) -> None:
        source = self._drag_source
        destination = self._active_drop
        dragging = self._dragging
        self._drag_source = None
        self._drag_start = None
        self._dragging = False
        self.delete("drag_ghost")
        self.clear_drop_indicator()
        if dragging and source is not None and destination is not None:
            self._on_move(source, destination.parent_path, destination.index)

    def _on_double_click(self, _event: tk.Event[tk.Misc]) -> None:
        path = self._path_from_current()
        if path is not None:
            self._on_rename(path)

    def _on_delete_key(self, _event: tk.Event[tk.Misc]) -> str:
        if self._selected_path is not None:
            self._on_delete(self._selected_path)
        return "break"

    def _on_duplicate_key(self, _event: tk.Event[tk.Misc]) -> str:
        if self._selected_path is not None:
            self._on_duplicate(self._selected_path)
        return "break"

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> str:
        delta = int(getattr(event, "delta", 0))
        self.yview_scroll(-1 * (delta // 120 if delta else 0), "units")
        return "break"

    def _show_drop_indicator(self, zone: DropZone | None) -> None:
        self.delete("drop_indicator")
        self._active_drop = zone
        if zone is None:
            return
        self.create_line(
            zone.x1 + 4,
            zone.y,
            zone.x2 - 4,
            zone.y,
            fill="#0ea5e9",
            width=4,
            capstyle="round",
            tags="drop_indicator",
        )
        self.create_oval(
            zone.x1 - 2,
            zone.y - 5,
            zone.x1 + 8,
            zone.y + 5,
            fill="#e0f2fe",
            outline="#0ea5e9",
            width=2,
            tags="drop_indicator",
        )
        self.tag_raise("drop_indicator")

    def _ellipsize(self, value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."

    def _shade(self, color: str, amount: float) -> str:
        value = color.lstrip("#")
        if len(value) != 6:
            return "#334155"
        channels = [int(value[index : index + 2], 16) for index in (0, 2, 4)]
        shaded = [max(0, min(255, round(channel * (1.0 + amount)))) for channel in channels]
        return "#" + "".join(f"{channel:02x}" for channel in shaded)


class ScratchPaletteItem(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        kind: str,
        label: str,
        color: str,
        workspace: ScratchWorkspace,
        on_activate: Callable[[str], None],
        on_drop: Callable[[str, BlockPath, int], None],
        **kwargs: object,
    ) -> None:
        super().__init__(
            master,
            height=48,
            background="#ffffff",
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            **kwargs,
        )
        self.kind = kind
        self.label = label
        self.color = color
        self.workspace = workspace
        self._on_activate = on_activate
        self._on_drop = on_drop
        self._start: tuple[int, int] | None = None
        self._dragging = False
        self._ghost: tk.Toplevel | None = None
        self.bind("<Configure>", self._draw)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def _draw(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self.delete("all")
        width = max(140, self.winfo_width() - 4)
        points = [2, 4, 34, 4, 42, 12, 60, 12, 68, 4, width, 4, width, 44, 68, 44, 60, 36, 42, 36, 34, 44, 2, 44]
        self.create_polygon(points, fill=self.color, outline="")
        self.create_text(
            16,
            24,
            anchor="w",
            text=self.label,
            fill="#ffffff",
            font=("TkDefaultFont", 10, "bold"),
        )
        self.create_text(width - 18, 24, text="⋮⋮", fill="#ffffff", font=("TkDefaultFont", 10, "bold"))

    def _press(self, event: tk.Event[tk.Misc]) -> None:
        self._start = (event.x_root, event.y_root)
        self._dragging = False

    def _motion(self, event: tk.Event[tk.Misc]) -> None:
        if self._start is None:
            return
        if not self._dragging and math.hypot(event.x_root - self._start[0], event.y_root - self._start[1]) < 7:
            return
        self._dragging = True
        self._show_ghost(event.x_root, event.y_root)
        self.workspace.show_external_drop(event.x_root, event.y_root)

    def _release(self, event: tk.Event[tk.Misc]) -> None:
        dragging = self._dragging
        self._start = None
        self._dragging = False
        self._hide_ghost()
        zone = self.workspace.destination_at_root(event.x_root, event.y_root)
        self.workspace.clear_drop_indicator()
        if dragging:
            if zone is not None:
                self._on_drop(self.kind, zone.parent_path, zone.index)
            return
        self._on_activate(self.kind)

    def _show_ghost(self, x_root: int, y_root: int) -> None:
        if self._ghost is None:
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            try:
                ghost.attributes("-alpha", 0.9)
            except tk.TclError:
                pass
            label = tk.Label(
                ghost,
                text=self.label,
                background=self.color,
                foreground="#ffffff",
                padx=18,
                pady=9,
                font=("TkDefaultFont", 10, "bold"),
            )
            label.pack()
            self._ghost = ghost
        self._ghost.geometry(f"+{x_root + 14}+{y_root + 14}")

    def _hide_ghost(self) -> None:
        if self._ghost is None:
            return
        try:
            self._ghost.destroy()
        except tk.TclError:
            pass
        self._ghost = None
