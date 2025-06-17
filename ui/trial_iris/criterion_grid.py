import os

from .editor import editor_dialog

import reflex as rx
import pandas as pd
import logging
from typing import Any
from pydantic_curator.criterion_parser import parse_criterion
from .excel_style_filter import excel_style_filter

logger = logging.getLogger(__name__)


class CriterionGridState(rx.State):

    # Data state
    _trial_df: pd.DataFrame = pd.DataFrame()
    _filtered_trial_df: pd.DataFrame = pd.DataFrame()

    # UI state
    current_page: int = 0
    page_size: int = 30
    total_pages: int = 0
    show_save_dialog: bool = False
    show_editor: bool = False

    @rx.var
    def is_data_loaded(self) -> bool:
        """Check if data is loaded."""
        return not self._trial_df.empty

    @rx.event
    def apply_filters(self, filters: dict[str, list[str]]):
        """Apply all active filters."""
        if self._trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self._trial_df.index)

        logger.info('applying filter')

        for filter_name, filter_values in filters.items():
            filter_mask &= self._trial_df[filter_name].isin(filter_values)

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
            formatted_criterion = row['Override'] if row['Override'] else row['Values']
            parse_error = None
            try:
                parse_criterion(formatted_criterion)
            except ValueError as e:
                # put the error in the table
                parse_error = str(e)

            result.append({
                'index': idx,
                'Trial ID': row['TrialId'],
                'Cohort': row['Cohort'],
                'Override': bool(row['Override']),
                'Text': row['Description'],
                'Code': formatted_criterion,
                'Error': parse_error
            })
        return result

    @rx.event
    async def set_trial_df(self, trial_df: pd.DataFrame):

        self._trial_df = trial_df
        self._filtered_trial_df = self._trial_df

        self.total_pages = (len(self._trial_df) + self.page_size - 1) // self.page_size
        self.current_page = 0

        filter_state = await self.get_state(FilterState)

        for c in ['TrialId', 'Cohort', 'Override']:
            await filter_state.add_filter(c, self._trial_df[c].unique().tolist())

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
    def update_criterion(self, index: int, criterion: str):
        """Update criterion override for a specific row."""
        try:
            self._trial_df.loc[index, 'Override'] = criterion
            # Refresh the filtered dataframe
            self.apply_filters()
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

def construct_table() -> rx.Component:
    """Create the table component for displaying criteria."""
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell(
                    excel_style_filter(
                        'TrialId',
                        options=FilterState.options_dict,
                        selected=FilterState.selected_dict,
                        toggle_option=FilterState.toggle_option,
                        select_all=FilterState.select_all,
                        clear_all=FilterState.clear_all,
                        label="Trial ID",
                        compact=True
                    )
                ),
                rx.table.column_header_cell(
                    excel_style_filter(
                        'Cohort',
                        options=FilterState.options_dict,
                        selected=FilterState.selected_dict,
                        toggle_option=FilterState.toggle_option,
                        select_all=FilterState.select_all,
                        clear_all=FilterState.clear_all,
                        label="Cohort",
                        compact=True
                    ), max_width="10%"),
                rx.table.column_header_cell(
                    excel_style_filter(
                        'Override',
                        options=FilterState.options_dict,
                        selected=FilterState.selected_dict,
                        toggle_option=FilterState.toggle_option,
                        select_all=FilterState.select_all,
                        clear_all=FilterState.clear_all,
                        label="Override",
                        compact=True,
                    ), max_width="5%"),
                rx.table.column_header_cell("Text", max_width="20%"),
                rx.table.column_header_cell("Edit", max_width="5%"),
                rx.table.column_header_cell("Code", max_width="35%"),
            )
        ),
        rx.table.body(
            rx.foreach(
                CriterionGridState.current_page_data,
                lambda row: rx.table.row(
                    rx.table.cell(row["Trial ID"]),
                    rx.table.cell(row["Cohort"]),
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
                    ),
                    rx.table.cell(rx.text(row["Text"]), white_space="pre-wrap"),
                    rx.table.cell(editor_dialog(row['index'], row['Code'])),
                    rx.table.cell(rx.code_block(row["Code"], language="python", can_copy=True,
                                                copy_button=rx.button(
                                                    rx.icon(tag="copy", size=15),
                                                    size="1",
                                                    on_click=rx.set_clipboard(row["Code"]),
                                                    style={"position": "absolute", "top": "0.5em", "right": "0"}),
                                                font_size="12px",
                                                style={
                                                    "margin": "0"
                                                }),
                                  max_width="1000px"
                                  ),
                )
            )
        ),
        width="100%",
    )

def criteria_table() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.button(
                "Prev",
                on_click=CriterionGridState.prev_page,
            ),
            rx.text(
                f"page {CriterionGridState.current_page + 1} / {CriterionGridState.total_pages}"
            ),
            rx.button(
                "Next",
                on_click=CriterionGridState.next_page,
            ),
        ),
        construct_table())
