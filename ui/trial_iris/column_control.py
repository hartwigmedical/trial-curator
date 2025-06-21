import json

import reflex as rx

from ui.trial_iris.column_definitions import COLUMN_DEFINITIONS

class ColumnControlState(rx.State):

    column_names: list[str] = [col.name for col in COLUMN_DEFINITIONS]
    # initialise to all non hidden columns
    _hidden_columns: set[str] = {col.name for col in COLUMN_DEFINITIONS if col.defaultHidden}

    # save preference to local storage for persistence
    column_order: str = rx.LocalStorage("column_order")

    @rx.var
    def visible_columns(self) -> list[str]:
        return [c for c in self.column_names if c not in self._hidden_columns]

    @rx.event
    def toggle_column_visibility(self, column: str):
        """Toggle visibility of a specific column."""
        if column in self._hidden_columns:
            self._hidden_columns.remove(column)
        else:
            self._hidden_columns.add(column)

    @rx.event
    def move_column(self, column: str, direction: str):
        """Move a column up or down."""
        if direction == "up":
            idx = self.column_names.index(column)
            if idx > 0:
                self.column_names.insert(idx - 1, self.column_names.pop(idx))
        elif direction == "down":
            idx = self.column_names.index(column)
            if idx < len(self.column_names) - 1:
                self.column_names.insert(idx + 1, self.column_names.pop(idx))
        yield self.save_preference()

    # Persistence
    def save_preference(self):
        self.column_order = json.dumps(self.column_names)

    def load_preference(self):
        self.column_names = json.loads(self.column_order)

    def reset_columns(self):
        self.column_names = [col.name for col in COLUMN_DEFINITIONS]
        rx.remove_local_storage("column_order")

def column_control_menu() -> rx.Component:
    """Create column control menu component."""
    return rx.menu.root(
        rx.menu.trigger(
            rx.button(
                rx.hstack(
                    rx.text("Columns"),
                    rx.icon("columns_3_cog", size=15),
                    align="center"
                ),
                variant="outline",
            ),
        ),
        rx.menu.content(
            rx.vstack(
                rx.foreach(
                    ColumnControlState.column_names,
                    lambda col: rx.hstack(
                        rx.checkbox(
                            checked=ColumnControlState.visible_columns.contains(col),
                            on_change=lambda: ColumnControlState.toggle_column_visibility(col),
                        ),
                        rx.text(col),
                        rx.spacer(),
                        rx.button(
                            rx.icon('chevron-up', size=20),
                            on_click=[ColumnControlState.move_column(col, "up")],
                            size="1",
                            id=f"btn-up-{col}"
                        ),
                        rx.button(
                            rx.icon('chevron-down', size=20),
                            on_click=[ColumnControlState.move_column(col, "down")],
                            size="1",
                            id=f"btn-down-{col}"
                        ),
                        width="100%",
                    ),
                ),
                padding="1em",
            ),
        ),
        on_mount=ColumnControlState.load_preference
    )
