import os
import numpy as np

import reflex as rx
import pandas as pd
import logging
from typing import Any
from pydantic_curator.criterion_parser import parse_criterion
from .column_definitions import ColumnDefinition, COLUMN_DEFINITIONS, CRITERION_TYPE_NAMES
from .excel_style_filter import excel_style_filter
from .local_file_picker import file_picker_dialog
from .editor import editor_dialog

logger = logging.getLogger(__name__)

AVAILABLE_COLUMNS = [col.name for col in COLUMN_DEFINITIONS]
CRITERION_TYPE_COLUMNS = CRITERION_TYPE_NAMES


class CriterionGridState(rx.State):
    # Data state
    _trial_df: pd.DataFrame = pd.DataFrame()
    _filtered_trial_df: pd.DataFrame = pd.DataFrame()
    _hidden_columns: set[str] = {col.name for col in COLUMN_DEFINITIONS if col.defaultHidden}
    no_filter_columns: list[str] = [col.name for col in COLUMN_DEFINITIONS if not col.filterable]
    thin_columns: list[str] = [col.name for col in COLUMN_DEFINITIONS if col.thin]

    # UI state
    current_page: int = 0
    page_size: int = 50
    total_pages: int = 0
    show_save_dialog: bool = False
    show_editor: bool = False

    @rx.var
    def total_records(self) -> int:
        return self._trial_df.shape[0]

    @rx.var
    def available_columns(self) -> list[str]:
        return [c.name for c in COLUMN_DEFINITIONS]

    @rx.var
    def visible_columns(self) -> list[str]:
        return [c.name for c in COLUMN_DEFINITIONS if c.name not in self._hidden_columns]

    @rx.event
    async def set_trial_df(self, trial_df: pd.DataFrame):

        self._trial_df = trial_df
        self._filtered_trial_df = self._trial_df

        self.total_pages = (len(self._trial_df) + self.page_size - 1) // self.page_size
        self.current_page = 0

        filter_state = await self.get_state(FilterState)

        for c in COLUMN_DEFINITIONS:
            if c.filterable:
                if c.type == bool:
                    await filter_state.add_filter(c.name, ['true', 'false'])
                else:
                    await filter_state.add_filter(c.name, sorted(self._trial_df[c.name].unique().tolist()))

    @rx.event
    def apply_filters(self, filters: dict[str, list[str]]):
        """Apply all active filters."""
        if self._trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self._trial_df.index)

        logger.info('applying filter')

        for filter_name, filter_values in filters.items():
            filter_mask &= self._trial_df[filter_name].isin(
                [convert_string_to_column_type(v, self._trial_df[filter_name]) for v in filter_values]
            )

        self._filtered_trial_df = self._trial_df[filter_mask]
        self.total_pages = (len(self._filtered_trial_df) + self.page_size - 1) // self.page_size
        self.current_page = 0

    @rx.var
    def current_page_data(self) -> list[dict[str, Any]]:
        """Get data for current page."""
        if self._filtered_trial_df.empty:
            return []

        logger.info('getting page data')

        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        page_data = self._filtered_trial_df.iloc[start_idx:end_idx]

        result = []
        for idx, row in page_data.iterrows():
            formatted_criterion = row['OverrideCode'] if row['OverrideCode'] else row['LlmCode']
            parse_error = None
            try:
                parse_criterion(formatted_criterion)
            except ValueError as e:
                # put the error in the table
                parse_error = str(e)

            result.append({
                'idx': idx,
                ** {c.name: row[c.name] for c in COLUMN_DEFINITIONS if c.name in page_data.columns},
                'Code': formatted_criterion,
                'Error': parse_error,
                'LlmCode': row['LlmCode'],
                'OverrideCode': row['OverrideCode']
            })
        return result

    @rx.event
    def go_to_page(self, page: int):
        """Navigate to specific page."""
        if 0 <= page < self.total_pages:
            self.current_page = page

    @rx.event
    def next_page(self):
        """Go to next page."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1

    @rx.event
    def prev_page(self):
        """Go to previous page."""
        if self.current_page > 0:
            self.current_page -= 1

    @rx.event
    async def update_criterion(self, index: int, criterion: str):
        logger.info(f"update criterion: index={index}, criterion={criterion}")
        """Update criterion override for a specific row."""
        try:
            self._trial_df.loc[index, 'Override'] = criterion
            # Refresh the filtered dataframe
            filter_state = await self.get_state(FilterState)
            await filter_state.apply_filters()
            return rx.toast.success("Criterion updated")
        except Exception as e:
            return rx.toast.error(f"Error updating criterion: {str(e)}")

    @rx.event
    def save_criteria(self):
        """Save the current criteria to file."""
        try:
            if not self.save_path:
                return rx.toast.error("Please provide a save path")

            save_path = os.path.expanduser(self.save_path)
            self._trial_df.to_csv(save_path, sep='\t', index=False)
            self.show_save_dialog = False
            return rx.toast.success(f"Saved criteria to {save_path}")
        except Exception as e:
            return rx.toast.error(f"Error saving criteria: {str(e)}")

    @rx.event
    def edit_criterion(self, index: int):
        try:
            self._trial_df.loc[index]
            self.show_editor = True
            return None
        except Exception as e:
            return rx.toast.error(f"Error updating criterion: {str(e)}")

    @rx.event
    def toggle_column_visibility(self, column: str):
        """Toggle visibility of a specific column."""
        if column in self._hidden_columns:
            self._hidden_columns.remove(column)
        else:
            self._hidden_columns.add(column)

class FilterState(rx.State):
    """State for individual filter components."""
    options_dict: dict[str, list[str]] = {}
    selected_dict: dict[str, list[str]] = {}

    @rx.event
    async def add_filter(self, filter_name: str, values: list[str]):
        self.options_dict[filter_name] = values.copy()
        self.selected_dict[filter_name] = values.copy()
        logger.info(f"added filter: {filter_name}")
        await self.apply_filters()

    @rx.event
    async def toggle_option(self, filter_name: str, option: str):
        logger.info(f"toggle option: {filter_name}")
        if option in self.selected_dict[filter_name]:
            self.selected_dict[filter_name].remove(option)
        else:
            self.selected_dict[filter_name].append(option)
        await self.apply_filters()

    @rx.event
    async def select_all(self, filter_name: str):
        logger.info(f"select all: {filter_name}")
        self.selected_dict[filter_name] = self.options_dict[filter_name].copy()
        await self.apply_filters()

    @rx.event
    async def clear_all(self, filter_name: str):
        logger.info(f"clear all: {filter_name}")
        self.selected_dict[filter_name].clear()
        await self.apply_filters()

    @rx.event
    async def apply_filters(self):
        grid_state = await self.get_state(CriterionGridState)
        grid_state.apply_filters(self.selected_dict)


@rx.event
def filter_dialog():
    """Filter dialog component."""
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button("Filter")
        ),
        rx.dialog.content(
            rx.dialog.title("Filter Criteria"),
            rx.vstack(
                rx.text("Trial ID:")
            )
        )
    )


def render_cell(row, col) -> rx.Component:
    return rx.match(
        col,
        ("Checked", rx.table.cell(rx.checkbox(row['Checked']))),
        ("Edit", rx.table.cell(
            editor_dialog(
                row['idx'],
                row['LlmCode'],
                rx.cond(row['OverrideCode'] == '', row['LlmCode'], row['OverrideCode']),
                CriterionGridState.update_criterion))
        ),
        ("Code", "LlmCode", "OverrideCode",
            rx.table.cell(
                rx.code_block(
                    row["Code"], language="python", can_copy=True,
                     copy_button=rx.button(
                         rx.icon(tag="copy", size=15),
                         size="1",
                         on_click=rx.set_clipboard(row["Code"]),
                         style={"position": "absolute", "top": "0.5em", "right": "0"}
                     ),
                     font_size="12px",
                     style={
                         "margin": "0"
                     }
                ),
                max_width="600px"
            )
        ),
        ("Override",
            rx.table.cell(
                rx.text(
                    rx.cond(row["Override"], "Yes", "No"),
                    bg=rx.cond(
                        row["Override"],
                        "green.100",
                        "gray.100"
                    ),
                    color=rx.cond(
                        row["Override"],
                        "green.800",
                        "gray.700"
                    ),
                    padding="2px 8px",
                    border_radius="4px",
                    font_size="12px",
                    font_weight="500",
                )
            )
        ),
        ("Description", rx.table.cell(row["Description"], white_space="pre-wrap", min_width="200px", max_width="300px")),
        rx.cond(CriterionGridState.thin_columns.contains(col),
            rx.table.cell(row[col], max_width="20px"),
            rx.table.cell(row[col], white_space="pre-wrap", max_width="150px")
        )
    )

def construct_table() -> rx.Component:
    """Create the table component for displaying criteria."""
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.foreach(
                    CriterionGridState.visible_columns,
                    lambda col: rx.table.column_header_cell(
                        rx.cond(CriterionGridState.no_filter_columns.contains(col),
                                rx.text(col),
                                excel_style_filter(
                                    col,
                                    CriterionGridState.thin_columns.contains(col),
                                    options=FilterState.options_dict,
                                    selected=FilterState.selected_dict,
                                    toggle_option=FilterState.toggle_option,
                                    select_all=FilterState.select_all,
                                    clear_all=FilterState.clear_all,
                                    label=col,
                                    compact=True
                                )
                            )
                    )
                )
            )
        ),
        rx.table.body(
            rx.foreach(
                CriterionGridState.current_page_data,
                lambda row: rx.table.row(
                    rx.foreach(
                        CriterionGridState.visible_columns,
                        lambda col: render_cell(row, col)
                    )
                )
            )
        )
    )


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
                rx.foreach(CriterionGridState.available_columns,
                           lambda col: rx.hstack(
                               rx.checkbox(
                                   checked=CriterionGridState.visible_columns.contains(col),
                                   on_change=lambda: CriterionGridState.toggle_column_visibility(col),
                               ),
                               rx.text(col),
                               width="100%",
                           ),
                           ),
                padding="1em",
            ),
        )
    )

def criteria_table(save_criteria: rx.EventHandler) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            file_picker_dialog(button_text="Save", on_submit=save_criteria),
            column_control_menu(),
            rx.button(
                "Prev",
                on_click=CriterionGridState.prev_page),
            rx.text(f"page {CriterionGridState.current_page + 1} / {CriterionGridState.total_pages}",
                    size="2",),
            rx.button(
                "Next",
                on_click=CriterionGridState.next_page,
            ),
            rx.spacer(width="100px"),
            rx.text(f"Total records: {CriterionGridState.total_records}", font_size="sm", color="gray.600"),
            spacing="4",
            justify="between",
            padding="4",
            align="center",
            width="100%",
        ),
        rx.box(construct_table(), width="100%"),
        width="100%"
    )

def convert_string_to_column_type(value_str, column_dtype):
    """
    Convert a string to the same type as a pandas column dtype.

    Parameters:
        value_str (str): The string to convert.
        column_dtype: The dtype of the target pandas column.

    Returns:
        Converted value in the column's dtype.
    """
    if pd.api.types.is_datetime64_any_dtype(column_dtype):
        # For datetime columns
        return pd.to_datetime(value_str)

    elif pd.api.types.is_bool_dtype(column_dtype):
        # For boolean columns
        value_lower = value_str.strip().lower()
        if value_lower in ["true", "1", "yes"]:
            return True
        elif value_lower in ["false", "0", "no"]:
            return False
        else:
            raise ValueError(f"Cannot convert '{value_str}' to boolean.")

    elif pd.api.types.is_numeric_dtype(column_dtype):
        # For numeric types (int, float)
        return np.dtype(column_dtype).type(value_str)

    elif pd.api.types.is_object_dtype(column_dtype) or pd.api.types.is_string_dtype(column_dtype):
        # For object/string types, return as-is
        return value_str

    else:
        raise TypeError(f"Conversion for dtype '{column_dtype}' is not supported.")
