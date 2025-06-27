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

    # sort by column and if it is ascending
    sort_by: dict[str, bool] = {}

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

        # set the CODE column
        self._trial_df[Columns.CODE.name] = self._trial_df[Columns.LLM_CODE.name].mask(
            self._trial_df[Columns.OVERRIDE_CODE.name].notna() & (self._trial_df[Columns.OVERRIDE_CODE.name] != ""),
            self._trial_df[Columns.OVERRIDE_CODE.name]
        )

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
    def cycle_sort_by(self, col: str):
        # we cycle between unsorted -> sort ascending -> sort descending
        logger.info(f"cycle sort by: {col}")
        if col in self.sort_by:
            ascending = self.sort_by[col]
            if ascending:
                # cycle to sort descending
                self.sort_by[col] = False
            else:
                # set to not sorted
                del self.sort_by[col]
        else:
            self.sort_by[col] = True
        self.apply_filters(reset_page=True)

    def apply_filters(self, reset_page: bool = False):
        """Apply all active filters."""
        if self._trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self._trial_df.index)

        logger.debug('applying filter')

        for filter_name, filter_values in self.deselected_dict.items():
            if len(filter_values) > 0:
                filter_mask &= ~self._trial_df[filter_name].isin(filter_values)

        self._filtered_trial_df = self._trial_df[filter_mask]

        if len(self.sort_by) > 0:
            self._filtered_trial_df = self._filtered_trial_df.sort_values(
                by=[s for s, _ in self.sort_by.items()],
                ascending=[d for _, d in self.sort_by.items()])

        self.total_pages = (len(self._filtered_trial_df) + self.page_size - 1) // self.page_size
        if reset_page:
            self.current_page = 0
        self.update_current_page_data()

    def update_current_page_data(self):
        """Get data for current page."""
        if self._filtered_trial_df.empty:
            self.current_page_data = []

        logger.debug('updating page data')

        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        page_data = self._filtered_trial_df.iloc[start_idx:end_idx]

        def process_row(idx, row):
            formatted_criterion = row[Columns.CODE.name]
            parse_error = None
            try:
                parse_criterion(formatted_criterion)
            except Exception as e:
                parse_error = str(e)

            result_row = {
                INDEX_COLUMN: idx,
                **{c.name: row[c.name] for c in COLUMN_DEFINITIONS if c.name in page_data.columns},
                Columns.ERROR.name: parse_error,
            }
            return result_row

        # Apply row-wise using index-based access to the vectorized values
        result = page_data.apply(lambda row: process_row(row.name, row), axis=1).values.tolist()
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
    async def update_override(self, index: int, criterion: str):
        """Update override for a specific row."""
        try:
            logger.info(f"update criterion: index={index}, criterion={criterion}")
            self._trial_df.loc[index, Columns.OVERRIDE_CODE.name] = criterion
            self._trial_df.loc[index, Columns.OVERRIDE.name] = True
            self._trial_df.loc[index, Columns.CODE.name] = criterion
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
        # restore the LLM code
        self._trial_df.loc[index, Columns.CODE.name] = self._trial_df.loc[index, Columns.LLM_CODE.name]
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
