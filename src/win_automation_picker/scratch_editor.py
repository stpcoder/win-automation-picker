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


@dataclass(frozen=True)
class ScratchLayout:
    block_height: float
    header_height: float
    footer_height: float
    stack_gap: float
    child_padding: float
    body_min_height: float
    start_height: float
    empty_slot_height: float
    child_indent: float
    child_right_gap: float
    max_stack_width: float
    title_font: int
    subtitle_font: int


def layout_for_density(value: str) -> ScratchLayout:
    if str(value).strip().casefold() in {"comfortable", "normal", "보통"}:
        return ScratchLayout(
            block_height=58.0,
            header_height=54.0,
            footer_height=24.0,
            stack_gap=5.0,
            child_padding=6.0,
            body_min_height=48.0,
            start_height=54.0,
            empty_slot_height=40.0,
            child_indent=28.0,
            child_right_gap=10.0,
            max_stack_width=820.0,
            title_font=10,
            subtitle_font=9,
        )
    return ScratchLayout(
        block_height=46.0,
        header_height=46.0,
        footer_height=18.0,
        stack_gap=2.0,
        child_padding=3.0,
        body_min_height=36.0,
        start_height=44.0,
        empty_slot_height=32.0,
        child_indent=24.0,
        child_right_gap=8.0,
        max_stack_width=700.0,
        title_font=9,
        subtitle_font=8,
    )


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
        # A nested slot wins while the pointer is inside it, but its depth must
        # not overpower the visible parent gutter used to move blocks outward.
        return abs(zone.y - y) + horizontal * 0.35 - zone.depth * 4.0

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
        self._density = "compact"
        self._layout = layout_for_density(self._density)

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

    def set_density(self, value: str) -> None:
        normalized = str(value).strip().casefold()
        density = "comfortable" if normalized in {"comfortable", "normal", "보통"} else "compact"
        if density == self._density:
            return
        self._density = density
        self._layout = layout_for_density(density)
        self.render(self._recipe, self._selected_path)

    def render(self, recipe: AutomationRecipe, selected_path: BlockPath | None = None) -> None:
        self._recipe = recipe
        self._selected_path = selected_path
        self.delete("all")
        self._drop_zones = []
        width = max(480, self.winfo_width() - 34)
        self._draw_background(width)
        stack_width = min(width - 22, self._layout.max_stack_width)
        y = 16.0
        self._draw_start_hat(22, y, stack_width)
        y += self._layout.start_height + self._layout.stack_gap
        y = self._draw_list(recipe.steps, (), x=22, y=y, width=stack_width, depth=0)
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
        height = self._layout.start_height
        connector_depth = 5.0 if self._density == "compact" else 7.0
        points = [
            x,
            y + 14,
            x + 8,
            y + 4,
            x + 24,
            y,
            x + width - 10,
            y,
            x + width,
            y + 10,
            x + width,
            y + height - connector_depth,
            x + 66,
            y + height - connector_depth,
            x + 60,
            y + height,
            x + 46,
            y + height,
            x + 40,
            y + height - connector_depth,
            x + 10,
            y + height - connector_depth,
            x,
            y + height - 15,
        ]
        self.create_polygon(points, fill="#0f766e", outline="#0b5f59", width=1)
        self.create_oval(x + 14, y + 12, x + 28, y + 26, fill="#ccfbf1", outline="")
        self.create_polygon(
            [x + 19, y + 15, x + 19, y + 23, x + 25, y + 19],
            fill="#0f766e",
            outline="",
        )
        self.create_text(
            x + 36,
            y + height / 2 - 2,
            anchor="w",
            text="매크로를 실행하면",
            fill="#ffffff",
            font=("TkDefaultFont", self._layout.title_font, "bold"),
        )

    def _measure_step(self, step: AutomationStep, width: float, depth: int) -> float:
        if step.kind not in CONTAINER_KINDS:
            return self._layout.block_height
        _child_indent, child_width = self._child_geometry(width)
        child_height = sum(self._measure_step(child, child_width, depth + 1) for child in step.children)
        child_height += max(0, len(step.children) - 1) * self._layout.stack_gap
        body_height = max(
            self._layout.body_min_height,
            child_height + self._layout.child_padding * 2,
        )
        return self._layout.header_height + body_height + self._layout.footer_height

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
        self._drop_zones.append(
            DropZone(parent_path, 0, x, x + width, y - self._layout.stack_gap / 2, depth)
        )
        if not steps:
            self._draw_empty_slot(x, y, width, parent_path)
            return y + self._layout.empty_slot_height + self._layout.stack_gap
        for index, step in enumerate(steps):
            path = (*parent_path, index)
            height = self._measure_step(step, width, depth)
            self._draw_step(path, step, x=x, y=y, width=width, height=height, depth=depth)
            y += height
            self._drop_zones.append(
                DropZone(parent_path, index + 1, x, x + width, y + self._layout.stack_gap / 2, depth)
            )
            y += self._layout.stack_gap
        return y

    def _draw_empty_slot(self, x: float, y: float, width: float, parent_path: BlockPath) -> None:
        height = self._layout.empty_slot_height
        self.create_rectangle(
            x + 8,
            y,
            x + width - 8,
            y + height,
            fill="#ffffff",
            outline="#94a3b8",
            dash=(4, 4),
            width=1,
        )
        self.create_text(
            x + width / 2,
            y + height / 2,
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
            header_height = self._layout.header_height
            footer_height = self._layout.footer_height
            body_top = y + header_height
            body_bottom = y + height - footer_height
            self._create_stack_shape(x, y, width, header_height, color, outline, outline_width, tags)
            self.create_rectangle(
                x,
                y + header_height - 6,
                x + (12 if self._density == "compact" else 16),
                body_bottom + 4,
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
                y=body_top + self._layout.child_padding,
                width=child_width,
                depth=depth + 1,
            )
            return

        self._create_stack_shape(x, y, width, height, color, outline, outline_width, tags)
        self._draw_block_text(path, step, x=x, y=y, width=width, tags=tags)

    def _child_geometry(self, width: float) -> tuple[float, float]:
        if width > 340:
            indent, right_gap = self._layout.child_indent, self._layout.child_right_gap
        elif width > 250:
            indent, right_gap = 16.0, 8.0
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
        badge_size = 20.0 if self._density == "compact" else 24.0
        badge_x = x + 11
        badge_y = y + (self._layout.header_height - badge_size) / 2
        self.create_oval(
            badge_x,
            badge_y,
            badge_x + badge_size,
            badge_y + badge_size,
            fill="#ffffff",
            outline="",
            tags=tags,
        )
        self.create_text(
            badge_x + badge_size / 2,
            badge_y + badge_size / 2,
            text=number,
            fill=self._color_for_step(step),
            font=("TkDefaultFont", 7 if self._density == "compact" else 8, "bold"),
            tags=tags,
        )
        text_x = badge_x + badge_size + 8
        title_y = y + (5 if self._density == "compact" else 7)
        subtitle_y = y + (24 if self._density == "compact" else 31)
        self.create_text(
            text_x,
            title_y,
            anchor="nw",
            text=self._ellipsize(step.block_title(), 44),
            width=max(140, width - (text_x - x) - 12),
            fill="#ffffff",
            font=("TkDefaultFont", self._layout.title_font, "bold"),
            tags=tags,
        )
        self.create_text(
            text_x,
            subtitle_y,
            anchor="nw",
            text=self._ellipsize(self._subtitle_for_step(step), 58),
            width=max(140, width - (text_x - x) - 12),
            fill="#f8fafc",
            font=("TkDefaultFont", self._layout.subtitle_font),
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
            self._shape_points(x + 1.5, y + 2, width, height, notch=notch),
            fill="#c5d0dc",
            outline="",
            tags=tags,
        )
        self.create_polygon(points, fill=fill, outline=outline, width=outline_width, tags=tags)

    def _shape_points(self, x: float, y: float, width: float, height: float, *, notch: bool) -> list[float]:
        if not notch:
            return [x, y, x + width, y, x + width, y + height, x, y + height]
        connector_start = 30.0 if self._density == "compact" else 38.0
        connector_depth = 5.0 if self._density == "compact" else 7.0
        connector_width = 28.0 if self._density == "compact" else 34.0
        shoulder = 6.0
        return [
            x,
            y,
            x + connector_start,
            y,
            x + connector_start + shoulder,
            y + connector_depth,
            x + connector_start + connector_width - shoulder,
            y + connector_depth,
            x + connector_start + connector_width,
            y,
            x + width,
            y,
            x + width,
            y + height,
            x + connector_start + connector_width,
            y + height,
            x + connector_start + connector_width - shoulder,
            y + height - connector_depth,
            x + connector_start + shoulder,
            y + height - connector_depth,
            x + connector_start,
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
            self.configure(cursor="fleur")
        self._autoscroll_root_point(event.x_root, event.y_root)
        x = self.canvasx(event.x)
        y = self.canvasy(event.y)
        zone = self._eligible_drop_zone(x, y, self._drag_source)
        self._show_drop_indicator(zone)
        self.delete("drag_ghost")
        try:
            step = get_step(self._recipe, self._drag_source)
            title = self._ellipsize(step.block_title(), 30)
        except Exception:
            title = "블록 이동"
        self.create_rectangle(
            x + 12,
            y + 10,
            x + 206,
            y + 38,
            fill="#0f172a",
            outline="#ffffff",
            width=1,
            stipple="gray50",
            tags="drag_ghost",
        )
        self.create_text(
            x + 26,
            y + 24,
            anchor="w",
            text=title,
            fill="#ffffff",
            font=("TkDefaultFont", 9, "bold"),
            tags="drag_ghost",
        )

    def _eligible_drop_zone(
        self,
        x: float,
        y: float,
        source: BlockPath | None,
    ) -> DropZone | None:
        zone = choose_drop_zone(self._drop_zones, x, y)
        if source is not None and zone and zone.parent_path[: len(source)] == source:
            return None
        return zone

    def _on_release(self, event: tk.Event[tk.Misc]) -> None:
        source = self._drag_source
        dragging = self._dragging
        destination = None
        if dragging and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            destination = self._eligible_drop_zone(
                self.canvasx(event.x),
                self.canvasy(event.y),
                source,
            )
        self._drag_source = None
        self._drag_start = None
        self._dragging = False
        self.configure(cursor="")
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
        self.create_rectangle(
            zone.x1 + 2,
            zone.y - 7,
            zone.x2 - 2,
            zone.y + 7,
            fill="#e0f2fe",
            outline="",
            tags="drop_indicator",
        )
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
            height=40,
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
        points = [
            2,
            3,
            28,
            3,
            34,
            9,
            50,
            9,
            56,
            3,
            width,
            3,
            width,
            37,
            56,
            37,
            50,
            31,
            34,
            31,
            28,
            37,
            2,
            37,
        ]
        self.create_polygon(points, fill=self.color, outline="")
        self.create_text(
            13,
            20,
            anchor="w",
            text=self.label,
            fill="#ffffff",
            font=("TkDefaultFont", 9, "bold"),
        )
        self.create_text(width - 16, 20, text="⋮⋮", fill="#ffffff", font=("TkDefaultFont", 9, "bold"))

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
                padx=14,
                pady=6,
                font=("TkDefaultFont", 9, "bold"),
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
