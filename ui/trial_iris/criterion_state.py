import reflex as rx
import pandas as pd
import logging
from typing import Any
from pydantic_curator.criterion_parser import parse_criterion
from .column_definitions import *

logger = logging.getLogger(__name__)

class CriterionState(rx.State):
    # Data state
    _trial_df: pd.DataFrame = pd.DataFrame()
    _filtered_trial_df: pd.DataFrame = pd.DataFrame()

    # filters
    options_dict: dict[str, list[Any]] = {}
    deselected_dict: dict[str, list[Any]] = {}

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

        for c in COLUMN_DEFINITIONS:
            if c.filterable:
                if c.type == bool:
                    self.add_filter(c.name, ['true', 'false'])
                else:
                    self.add_filter(c.name, sorted(self._trial_df[c.name].unique().tolist()))

    @rx.event
    def add_filter(self, filter_name: str, values: list[Any]):
        self.options_dict[filter_name] = values.copy()
        self.deselected_dict[filter_name] = []
        #logger.debug(f"added filter: {filter_name}")
        self.apply_filters()

    @rx.event
    def toggle_option(self, filter_name: str, option: Any):
        logger.info(f"toggle option: {filter_name}")
        if option in self.deselected_dict[filter_name]:
            self.deselected_dict[filter_name].remove(option)
        else:
            self.deselected_dict[filter_name].append(option)
        self.apply_filters()

    @rx.event
    def select_all(self, filter_name: str):
        logger.info(f"select all: {filter_name}")
        self.deselected_dict[filter_name].clear()
        self.apply_filters()

    @rx.event
    def clear_all(self, filter_name: str):
        logger.info(f"clear all: {filter_name}")
        self.deselected_dict[filter_name] = self.options_dict[filter_name].copy()
        self.apply_filters()

    @rx.event
    def apply_filters(self):
        """Apply all active filters."""
        if self._trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self._trial_df.index)

        logger.info('applying filter')

        for filter_name, filter_values in self.deselected_dict.items():
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

        code = page_data[Columns.LLM_CODE.name]
        code = code.mask(page_data[Columns.OVERRIDE_CODE.name].notna() & (page_data[Columns.OVERRIDE_CODE.name] != ""),
                         page_data[Columns.OVERRIDE_CODE.name])

        def process_row(idx, row):
            formatted_criterion = code[idx]  # index-aligned access
            parse_error = None
            try:
                parse_criterion(formatted_criterion)
            except Exception as e:
                parse_error = str(e)

            result_row = {
                INDEX_COLUMN: idx,
                **{c.name: row[c.name] for c in COLUMN_DEFINITIONS if c.name in page_data.columns},
                Columns.CODE.name: formatted_criterion,
                Columns.ERROR.name: parse_error,
                Columns.LLM_CODE.name: row[Columns.LLM_CODE.name],
                Columns.OVERRIDE_CODE.name: row[Columns.OVERRIDE_CODE.name],
            }
            return result_row

        # Apply row-wise using index-based access to the vectorized values
        result = page_data.apply(lambda row: process_row(row.name, row), axis=1).tolist()
        self.current_page_data = result

    @rx.event
    def go_to_page(self, page: int):
        """Navigate to specific page."""
        if 0 <= page < self.total_pages:
            self.current_page = page
            self.update_current_page_data()

    @rx.event
    def next_page(self):
        """Go to next page."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_current_page_data()

    @rx.event
    def prev_page(self):
        """Go to previous page."""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_current_page_data()

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
            self.apply_filters()
            return rx.toast.success("Criterion updated")
        except Exception as e:
            return rx.toast.error(f"Error updating criterion: {str(e)}")

    @rx.event
    async def delete_override(self, index: int):
        logger.info(f"delete override: index={index}")
        self._trial_df.loc[index, Columns.OVERRIDE_CODE.name] = None
        self._trial_df.loc[index, Columns.OVERRIDE.name] = False
        # Refresh the filtered dataframe, in case we got filter on override
        self.apply_filters()
        return rx.toast.success("Override deleted")

    @rx.event
    def edit_notes(self, idx: int, notes: str):
        logger.info(f"edit notes: idx={idx}, notes={notes}")
        self._trial_df.loc[idx, Columns.NOTES.name] = notes
        # Refresh the filtered dataframe, in case we got filter on override
        self.apply_filters()

    @rx.event
    def mark_checked(self, idx: int, checked: bool):
        logger.info(f"mark checked: idx={idx}, checked={checked}")
        self._trial_df.loc[idx, Columns.CHECKED.name] = checked
        # Refresh the filtered dataframe, in case we got filter on override
        self.apply_filters()
