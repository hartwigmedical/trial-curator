import json
import logging
from typing import Any

import reflex as rx
from reflex import ImportVar, ImportDict

from ui.trial_iris.column_definitions import COLUMN_DEFINITIONS

logger = logging.getLogger(__name__)

class ColumnControlState(rx.State):

    available_columns: list[str] = [col.name for col in COLUMN_DEFINITIONS if col.defaultHidden]
    visible_columns: list[str] = [col.name for col in COLUMN_DEFINITIONS if not col.defaultHidden]

    # save preference to local storage for persistence
    column_preference: str = rx.LocalStorage(name="column_preference")

    def handle_drag_end(self, data: dict[str, Any]):
        event_type = data.get("type", "")

        match event_type:
            case "reorder":
                # Handle reordering within selected columns
                oldIndex = data.get("oldIndex", -1)
                newIndex = data.get("newIndex", -1)
                if oldIndex != newIndex and 0 <= oldIndex < len(self.visible_columns) and 0 <= newIndex < len(self.visible_columns) :
                    self.visible_columns.insert(newIndex, self.visible_columns.pop(oldIndex))

            case "move":
                # Handle moving between zones
                column = data.get("column", "")
                source = data.get("source", "")

                if not column:
                    return

                # Move from available to selected
                if source == "available" and column in self.available_columns:
                    self.available_columns.remove(column)
                    self.visible_columns.append(column)

                # Move from selected to available
                elif source == "selected" and column in self.visible_columns:
                    self.visible_columns.remove(column)
                    self.available_columns.append(column)
            case "insert":
                column = data.get("column", "")
                source = data.get("source", "")
                index = data.get("destinationIndex", 0)

                if not column or source != "available":
                    return

                self.available_columns.remove(column)
                self.visible_columns.insert(index, column)

        self.save_preference()

    # Persistence
    def save_preference(self):
        self.column_preference = json.dumps(self.visible_columns)

    def load_preference(self):
        if self.column_preference:
            logger.info(f"Loading column preference from local storage: {self.column_preference}")
            self.visible_columns = json.loads(self.column_preference)

    def reset_columns(self):
        self.available_columns = [col.name for col in COLUMN_DEFINITIONS if col.defaultHidden]
        self.visible_columns = [col.name for col in COLUMN_DEFINITIONS if not col.defaultHidden]


# Custom DnD Kit wrapper component
class ColumnSelectorWrapper(rx.Component):
    """Custom wrapper for dnd-kit functionality"""

    library = "/public/column-selector"
    tag = "ColumnSelector"
    is_default = True

    # Props
    available_columns: rx.Var[list[str]]
    selected_columns: rx.Var[list[str]]
    on_drag_end: rx.EventHandler[lambda data: [data]]

    def add_imports(self) -> ImportDict | list[ImportDict]:
        return {
            "@atlaskit/pragmatic-drag-and-drop": [
                ImportVar(tag="draggable", is_default=False),
                ImportVar(tag="dropTargetForElements", is_default=False),
                ImportVar(tag="monitorForElements", is_default=False),
                ]
        }


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
                rx.button("Reset to default", on_click=ColumnControlState.reset_columns),
                ColumnSelectorWrapper.create(
                    available_columns=ColumnControlState.available_columns,
                    selected_columns=ColumnControlState.visible_columns,
                    on_drag_end=ColumnControlState.handle_drag_end
                ),
                padding="1em",
                style={
                    "font_size": "12px",
                },
            ),
        ),
        on_mount=ColumnControlState.load_preference
    )
