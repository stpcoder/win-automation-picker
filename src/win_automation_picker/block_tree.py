from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from .automation import WindowsAutomationError
from .recipe import AutomationRecipe, AutomationStep


BlockPath = tuple[int, ...]


def _path_label(path: BlockPath) -> str:
    return ".".join(str(index + 1) for index in path) or "root"


def _validate_index(items: list[AutomationStep], index: int, path: BlockPath) -> None:
    if index < 0 or index >= len(items):
        raise WindowsAutomationError(f"Block path is out of range: {_path_label(path)}")


def get_step(recipe: AutomationRecipe, path: BlockPath) -> AutomationStep:
    if not path:
        raise WindowsAutomationError("The root workspace is not a block.")
    items = recipe.steps
    step: AutomationStep | None = None
    for depth, index in enumerate(path):
        _validate_index(items, index, path[: depth + 1])
        step = items[index]
        items = step.children
    if step is None:
        raise WindowsAutomationError("Block path is empty.")
    return step


def get_children(recipe: AutomationRecipe, parent_path: BlockPath) -> list[AutomationStep]:
    if not parent_path:
        return list(recipe.steps)
    return list(get_step(recipe, parent_path).children)


def iter_paths(recipe: AutomationRecipe) -> Iterator[tuple[BlockPath, AutomationStep]]:
    def visit(items: list[AutomationStep], parent: BlockPath) -> Iterator[tuple[BlockPath, AutomationStep]]:
        for index, step in enumerate(items):
            path = (*parent, index)
            yield path, step
            yield from visit(step.children, path)

    yield from visit(recipe.steps, ())


def replace_step(recipe: AutomationRecipe, path: BlockPath, step: AutomationStep) -> AutomationRecipe:
    if not path:
        raise WindowsAutomationError("Cannot replace the root workspace.")

    def replace_in(items: list[AutomationStep], depth: int) -> list[AutomationStep]:
        index = path[depth]
        _validate_index(items, index, path[: depth + 1])
        updated = list(items)
        if depth == len(path) - 1:
            updated[index] = step
        else:
            parent = updated[index]
            updated[index] = replace(parent, children=replace_in(parent.children, depth + 1))
        return updated

    return AutomationRecipe(steps=replace_in(recipe.steps, 0), monitor_view=dict(recipe.monitor_view))


def insert_step(
    recipe: AutomationRecipe,
    parent_path: BlockPath,
    index: int,
    step: AutomationStep,
) -> tuple[AutomationRecipe, BlockPath]:
    children = get_children(recipe, parent_path)
    insert_index = max(0, min(len(children), int(index)))
    children.insert(insert_index, step)
    if not parent_path:
        updated = AutomationRecipe(steps=children, monitor_view=dict(recipe.monitor_view))
    else:
        parent = get_step(recipe, parent_path)
        updated = replace_step(recipe, parent_path, replace(parent, children=children))
    return updated, (*parent_path, insert_index)


def remove_step(recipe: AutomationRecipe, path: BlockPath) -> tuple[AutomationRecipe, AutomationStep]:
    if not path:
        raise WindowsAutomationError("Cannot remove the root workspace.")
    parent_path = path[:-1]
    index = path[-1]
    children = get_children(recipe, parent_path)
    _validate_index(children, index, path)
    removed = children.pop(index)
    if not parent_path:
        updated = AutomationRecipe(steps=children, monitor_view=dict(recipe.monitor_view))
    else:
        parent = get_step(recipe, parent_path)
        updated = replace_step(recipe, parent_path, replace(parent, children=children))
    return updated, removed


def _adjust_parent_after_removal(parent_path: BlockPath, removed_path: BlockPath) -> BlockPath:
    removed_parent = removed_path[:-1]
    depth = len(removed_parent)
    if len(parent_path) <= depth or parent_path[:depth] != removed_parent:
        return parent_path
    parent_index = parent_path[depth]
    removed_index = removed_path[-1]
    if parent_index == removed_index:
        raise WindowsAutomationError("A block cannot be moved inside itself.")
    if parent_index > removed_index:
        return (*parent_path[:depth], parent_index - 1, *parent_path[depth + 1 :])
    return parent_path


def move_step(
    recipe: AutomationRecipe,
    source_path: BlockPath,
    destination_parent: BlockPath,
    destination_index: int,
) -> tuple[AutomationRecipe, BlockPath]:
    if not source_path:
        raise WindowsAutomationError("Select a block to move.")
    if destination_parent[: len(source_path)] == source_path:
        raise WindowsAutomationError("A block cannot be moved inside itself.")

    source_parent = source_path[:-1]
    source_index = source_path[-1]
    adjusted_parent = _adjust_parent_after_removal(destination_parent, source_path)
    adjusted_index = int(destination_index)
    if destination_parent == source_parent and adjusted_index > source_index:
        adjusted_index -= 1

    without_source, step = remove_step(recipe, source_path)
    return insert_step(without_source, adjusted_parent, adjusted_index, step)


def duplicate_step(recipe: AutomationRecipe, path: BlockPath) -> tuple[AutomationRecipe, BlockPath]:
    step = get_step(recipe, path)
    return insert_step(recipe, path[:-1], path[-1] + 1, step)


def nearest_path(recipe: AutomationRecipe, preferred: BlockPath | None) -> BlockPath | None:
    if not recipe.steps:
        return None
    path = preferred or (0,)
    while path:
        try:
            get_step(recipe, path)
            return path
        except WindowsAutomationError:
            path = path[:-1]
    return (len(recipe.steps) - 1,)
