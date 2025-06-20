from typing import Any

import reflex as rx

from .column_definitions import *
from .criterion_grid import INDEX_COLUMN
from .editor import editor_dialog, EditorState

# follow example here: https://reflex.dev/docs/library/overlay/dropdown-menu/

class GridActionMenuState(rx.State):
    row_idx: int = -1
    delete_override_dialog_open: bool = False

    @rx.event
    def open_delete_override_dialog(self, idx: int):
        self.row_idx = idx
        self.delete_override_dialog_open = True

def grid_action_menu(row: dict[str, Any]) -> rx.Component:
    """Create action menu component for a row."""
    return rx.vstack(
        rx.menu.root(
            rx.menu.trigger(
                rx.icon("wrench")
            ),
            rx.menu.content(
                rx.menu.item(
                    rx.hstack(rx.text("Edit"), rx.icon("pen_line", size=15), align="center", justify="between"),
                    on_click=EditorState.open_dialog(
                        row[INDEX_COLUMN],
                        row[Columns.LLM_CODE.name],
                        rx.cond(row[Columns.OVERRIDE_CODE.name], row[Columns.OVERRIDE_CODE.name],
                                row[Columns.LLM_CODE.name])
                    ),
                ),
                rx.cond(
                    row[Columns.OVERRIDE.name],
                    rx.menu.item(
                        "Delete override",
                        on_click=GridActionMenuState.open_delete_override_dialog(row[INDEX_COLUMN]),
                    )
                ),
                rx.menu.sub(
                    rx.menu.sub_trigger(
                        rx.hstack(rx.text("Mark as"), rx.icon("notebook-pen", size=15), align="center", justify="between")
                    ),
                    rx.menu.sub_content(
                        rx.menu.item("Checked"),
                        rx.menu.item("Intent unclear"),
                        rx.menu.item("Advanced options…"),
                    ),
                )
            )
        ),
        align="center",
    )

def grid_action_menu_dialogs(save_override: rx.EventHandler, delete_override: rx.EventHandler) -> rx.Component:
    return rx.box(
        editor_dialog(save_override), delete_override_dialog(delete_override)
    )

def delete_override_dialog(delete_override: rx.EventHandler) -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Override?"),
            rx.flex(
                rx.alert_dialog.action(
                    rx.button("Confirm"),
                    on_click=delete_override(GridActionMenuState.row_idx)
                ),
                rx.alert_dialog.cancel(
                    rx.button("Cancel"),
                ),
                spacing="3",
            ),
        ),
        open=GridActionMenuState.delete_override_dialog_open,
        on_open_change=GridActionMenuState.set_delete_override_dialog_open(False)
    )
