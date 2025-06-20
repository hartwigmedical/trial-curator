import os

import reflex as rx
import pandas as pd
import logging
from typing import Any
from pydantic_curator.criterion_parser import parse_criterion
from .column_definitions import *
from .column_filter import column_control_menu, ColumnFilterState
from .excel_style_filter import excel_style_filter
from .grid_action_menu import grid_action_menu, grid_action_menu_dialogs
from .local_file_picker import file_picker_dialog

logger = logging.getLogger(__name__)

# need to make them rx.Var such that can use them on rx.cond
NO_FILTER_COLUMNS: rx.Var[list[str]] = rx.Var.create([col.name for col in COLUMN_DEFINITIONS if not col.filterable])
THIN_COLUMNS: rx.Var[list[str]] = rx.Var.create([col.name for col in COLUMN_DEFINITIONS if col.thin])

class CriterionGridState(rx.State):
    # Data state
    _trial_df: pd.DataFrame = pd.DataFrame()
    _filtered_trial_df: pd.DataFrame = pd.DataFrame()

    # page data
    current_page_data: list[dict[str, Any]]

    # UI state
    current_page: int = 0
    page_size: int = 50
    total_pages: int = 0

    @rx.var
    def total_records(self) -> int:
        return self._trial_df.shape[0]

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
    def apply_filters(self, filters: dict[str, list[Any]]):
        """Apply all active filters."""
        if self._trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self._trial_df.index)

        logger.info('applying filter')

        for filter_name, filter_values in filters.items():
            if len(filter_values) > 0:
                filter_mask &= ~self._trial_df[filter_name].isin(filter_values)

        self._filtered_trial_df = self._trial_df[filter_mask]
        self.total_pages = (len(self._filtered_trial_df) + self.page_size - 1) // self.page_size
        self.current_page = 0
        self.update_current_page_data()

    def update_current_page_data(self):
        """Get data for current page."""
        if self._filtered_trial_df.empty:
            self.current_page_data = []

        logger.info('updating page data')

        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        page_data = self._filtered_trial_df.iloc[start_idx:end_idx]

        result = []
        for idx, row in page_data.iterrows():
            formatted_criterion = row[Columns.OVERRIDE_CODE.name] if row[Columns.OVERRIDE_CODE.name] else row[Columns.LLM_CODE.name]
            parse_error = None
            try:
                parse_criterion(formatted_criterion)
            except ValueError as e:
                # put the error in the table
                parse_error = str(e)

            result.append({
                INDEX_COLUMN: idx,
                ** {c.name: row[c.name] for c in COLUMN_DEFINITIONS if c.name in page_data.columns},
                Columns.CODE.name: formatted_criterion,
                Columns.ERROR.name: parse_error,
                Columns.LLM_CODE.name: row[Columns.LLM_CODE.name],
                Columns.OVERRIDE_CODE.name: row[Columns.OVERRIDE_CODE.name]
            })
        self.current_page_data = result

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
        """Update criterion override for a specific row."""

        try:
            parse_criterion(criterion)
        except Exception as e:
            # put the error in the table
            logger.info(f"error parsing criterion: index={index}, criterion={criterion}")
            return rx.toast.error(f"Error parsing criterion: {str(e)}")

        try:
            logger.info(f"update criterion: index={index}, criterion={criterion}")
            self._trial_df.loc[index, Columns.OVERRIDE_CODE.name] = criterion
            self._trial_df.loc[index, Columns.OVERRIDE.name] = True
            # Refresh the filtered dataframe, in case we got filter on override
            filter_state = await self.get_state(FilterState)
            await filter_state.apply_filters()
            return rx.toast.success("Criterion updated")
        except Exception as e:
            return rx.toast.error(f"Error updating criterion: {str(e)}")

    @rx.event
    async def delete_override(self, index: int):
        logger.info(f"delete override: index={index}")
        self._trial_df.loc[index, Columns.OVERRIDE_CODE.name] = None
        self._trial_df.loc[index, Columns.OVERRIDE.name] = False
        # Refresh the filtered dataframe, in case we got filter on override
        filter_state = await self.get_state(FilterState)
        await filter_state.apply_filters()
        return rx.toast.success("Override deleted")

    @rx.event
    def save_criteria(self):
        """Save the current criteria to file."""
        try:
            if not self.save_path:
                return rx.toast.error("Please provide a save path")

            save_path = os.path.expanduser(self.save_path)
            self._trial_df.to_csv(save_path, sep='\t', index=False)
            return rx.toast.success(f"Saved criteria to {save_path}")
        except Exception as e:
            return rx.toast.error(f"Error saving criteria: {str(e)}")


class FilterState(rx.State):
    """State for individual filter components."""
    options_dict: dict[str, list[Any]] = {}
    deselected_dict: dict[str, list[Any]] = {}

    @rx.event
    async def add_filter(self, filter_name: str, values: list[Any]):
        self.options_dict[filter_name] = values.copy()
        self.deselected_dict[filter_name] = []
        logger.info(f"added filter: {filter_name}")
        await self.apply_filters()

    @rx.event
    async def toggle_option(self, filter_name: str, option: Any):
        logger.info(f"toggle option: {filter_name}")
        if option in self.deselected_dict[filter_name]:
            self.deselected_dict[filter_name].remove(option)
        else:
            self.deselected_dict[filter_name].append(option)
        await self.apply_filters()

    @rx.event
    async def select_all(self, filter_name: str):
        logger.info(f"select all: {filter_name}")
        self.deselected_dict[filter_name].clear()
        await self.apply_filters()

    @rx.event
    async def clear_all(self, filter_name: str):
        logger.info(f"clear all: {filter_name}")
        self.deselected_dict[filter_name] = self.options_dict[filter_name].copy()
        await self.apply_filters()

    @rx.event
    async def apply_filters(self):
        grid_state = await self.get_state(CriterionGridState)
        grid_state.apply_filters(self.deselected_dict)


def render_cell(row, col) -> rx.Component:
    return rx.match(
        col,
        ("Checked", rx.table.cell(rx.checkbox(row['Checked']))),
        (Columns.ACTION.name,
            rx.table.cell(
                grid_action_menu(row)
            )
        ),
        (Columns.CODE.name, Columns.LLM_CODE.name, Columns.OVERRIDE_CODE.name,
            rx.table.cell(
                rx.code_block(
                    row[col],
                    language="python",
                    can_copy=False,
                    wrap_long_lines=True,
                    font_size="12px",
                    style={
                        "margin": "0"
                    }
                ),
                max_width="600px"
            )
        ),
        (Columns.OVERRIDE.name,
            rx.table.cell(
                rx.cond(
                    row[Columns.OVERRIDE_CODE.name],
                    rx.text(
                        "Yes",
                        background_color=rx.color("green", 5),
                        padding="2px 8px",
                        border_radius="4px"
                    ),
                    rx.text("No")
                )
            )
        ),
        ("Description", rx.table.cell(row["Description"], white_space="pre-wrap", min_width="200px", max_width="300px")),
        rx.cond(THIN_COLUMNS.contains(col),
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
                    ColumnFilterState.visible_columns,
                    lambda col: rx.table.column_header_cell(
                        rx.cond(NO_FILTER_COLUMNS.contains(col),
                                rx.text(col),
                                excel_style_filter(
                                    col,
                                    THIN_COLUMNS.contains(col),
                                    options=FilterState.options_dict,
                                    deselected=FilterState.deselected_dict,
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
                        ColumnFilterState.visible_columns,
                        lambda col: render_cell(row, col)
                    )
                )
            )
        ),
        grid_action_menu_dialogs(CriterionGridState.update_criterion, CriterionGridState.delete_override)
    )

def criteria_table(save_criteria: rx.EventHandler) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            file_picker_dialog(directory="~", button_text="Save", on_submit=save_criteria),
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
