import reflex as rx

from ui.trial_iris.column_definitions import COLUMN_DEFINITIONS

COLUMN_NAMES = [col.name for col in COLUMN_DEFINITIONS]

class ColumnFilterState(rx.State):
    # initialise to all non hidden columns
    _hidden_columns: set[str] = {col.name for col in COLUMN_DEFINITIONS if col.defaultHidden}

    @rx.var
    def visible_columns(self) -> list[str]:
        return [c for c in COLUMN_NAMES if c not in self._hidden_columns]

    @rx.event
    def toggle_column_visibility(self, column: str):
        """Toggle visibility of a specific column."""
        if column in self._hidden_columns:
            self._hidden_columns.remove(column)
        else:
            self._hidden_columns.add(column)


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
                    COLUMN_NAMES,
                    lambda col: rx.hstack(
                        rx.checkbox(
                            checked=ColumnFilterState.visible_columns.contains(col),
                            on_change=lambda: ColumnFilterState.toggle_column_visibility(col),
                        ),
                        rx.text(col),
                        width="100%",
                    ),
                ),
                padding="1em",
            ),
        )
    )
