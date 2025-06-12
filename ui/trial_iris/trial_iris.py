import sys
import os

from .codemirror import codemirror

# Add parent directory (or wherever pydantic_curator is) to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))
# Or use absolute path: sys.path.append('/absolute/path/to/directory/containing/pydantic_curator')

import reflex as rx
import pandas as pd
import logging
from typing import List, Dict, Optional, Any
from pydantic_curator.criterion_parser import parse_criterion
from pydantic_curator.criterion_formatter import format_criterion

logger = logging.getLogger(__name__)


class CurationState(rx.State):
    """State management for the Curation Assistant."""

    # Data state
    trial_df: pd.DataFrame = pd.DataFrame()
    filtered_trial_df: pd.DataFrame = pd.DataFrame()
    filters: Dict[str, List[str]] = {}

    # UI state
    file_path: str = rx.Cookie(name="TrialTsv")
    current_page: int = 0
    page_size: int = 30
    total_pages: int = 0
    show_save_dialog: bool = False
    show_editor: bool = False

    # Filter state
    trial_id_filter: List[str] = []
    cohort_filter: List[str] = []

    @rx.var
    def is_data_loaded(self) -> bool:
        """Check if data is loaded."""
        return not self.trial_df.empty

    @rx.var
    def unique_trial_ids(self) -> List[str]:
        """Get unique trial IDs for filtering."""
        if self.trial_df.empty:
            return []
        return self.trial_df['TrialId'].unique().tolist()

    @rx.var
    def unique_cohorts(self) -> List[str]:
        """Get unique cohorts for filtering."""
        if self.trial_df.empty:
            return []
        return self.trial_df['Cohort'].unique().tolist()

    @rx.var
    def current_page_data(self) -> List[Dict[str, Any]]:
        """Get data for current page."""
        if self.filtered_trial_df.empty:
            return []

        start_idx = self.current_page * self.page_size
        end_idx = start_idx + self.page_size
        page_data = self.filtered_trial_df.iloc[start_idx:end_idx]

        result = []
        for idx, row in page_data.iterrows():
            criterion = parse_criterion(row['Override'] if row['Override'] else row['Values'])
            result.append({
                'index': idx,
                'Trial ID': row['TrialId'],
                'Cohort': row['Cohort'],
                'Override': bool(row['Override']),
                'Text': row['Description'],
                'Code': format_criterion(criterion),
            })
        return result

    @rx.event
    def load_file(self, file_path: str):
        """Load trial data from TSV file."""
        try:
            file_path = os.path.expanduser(file_path)
            if not os.path.exists(file_path):
                return rx.toast.error("File not found")

            df = pd.read_csv(file_path, sep='\t')
            df['Override'] = df['Override'].fillna('')

            self.trial_df = df
            self.filtered_trial_df = df
            self.total_pages = (len(df) + self.page_size - 1) // self.page_size
            self.current_page = 0

            return rx.toast.success(f"Successfully loaded {len(df)} trials")
        except Exception as e:
            return rx.toast.error(f"Error loading file: {str(e)}")

    @rx.event
    def update_trial_id_filter(self, selected_ids: List[str]):
        """Update trial ID filter."""
        self.trial_id_filter = selected_ids
        self.filters['TrialId'] = selected_ids
        self.apply_filters()

    @rx.event
    def update_cohort_filter(self, selected_cohorts: List[str]):
        """Update cohort filter."""
        self.cohort_filter = selected_cohorts
        self.filters['Cohort'] = selected_cohorts
        self.apply_filters()

    @rx.event
    def apply_filters(self):
        """Apply all active filters."""
        if self.trial_df.empty:
            return

        filter_mask = pd.Series(True, index=self.trial_df.index)

        for filter_name, filter_values in self.filters.items():
            if filter_values:  # Only apply if filter has values
                filter_mask &= self.trial_df[filter_name].isin(filter_values)

        self.filtered_trial_df = self.trial_df[filter_mask]
        self.total_pages = (len(self.filtered_trial_df) + self.page_size - 1) // self.page_size
        self.current_page = 0

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
            self.trial_df.loc[index, 'Override'] = criterion
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
            self.trial_df.to_csv(save_path, sep='\t', index=False)
            self.show_save_dialog = False
            return rx.toast.success(f"Saved criteria to {save_path}")
        except Exception as e:
            return rx.toast.error(f"Error saving criteria: {str(e)}")

    @rx.event
    def edit_criterion(self, index: int):
        try:
            self.trial_df.loc[index]
            self.show_editor = True
            return None
        except Exception as e:
            return rx.toast.error(f"Error updating criterion: {str(e)}")


class FilterState(rx.State):
    """State for individual filter components."""
    show_filter: Dict[str, bool] = {}
    filter_search: Dict[str, str] = {}

    def toggle_filter(self, filter_name: str):
        """Toggle filter dropdown visibility."""
        self.show_filter[filter_name] = not self.show_filter.get(filter_name, False)

    def update_filter_search(self, filter_name: str, search_term: str):
        """Update filter search term."""
        self.filter_search[filter_name] = search_term

    @rx.event
    def test_filter(self):
        print("header cell clicked")


def save_dialog():
    """Save dialog component."""

    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button("Save")
        ),
        rx.dialog.content(
            rx.dialog.title("Save Criteria"),
            rx.form(
                rx.vstack(
                    rx.text("Enter the path where you want to save the criteria:"),
                    rx.input(
                        placeholder="~/criteria.tsv",
                        id="save_path",
                        width="100%"
                    ),
                    rx.hstack(
                        rx.dialog.close(
                            rx.button("Cancel", variant="soft", color_scheme="gray", type="button")
                        ),
                        rx.button("Save", type="submit"),
                        spacing="2",
                        justify="end"
                    ),
                    spacing="3",
                    width="100%"
                ),
                on_submit=CurationState.save_criteria,
            ),
            style={"max_width": "500px"}
        )
    )

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

def criteria_table():
    """Create the table component for displaying criteria."""
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell(rx.code_block("Trial ID"), on_click=FilterState.test_filter, max_width="10%"),
                rx.table.column_header_cell("Cohort", max_width="10%"),
                rx.table.column_header_cell("Override", max_width="5%"),
                rx.table.column_header_cell("Text", max_width="20%"),
                rx.table.column_header_cell("Edit", max_width="5%"),
                rx.table.column_header_cell("Code", max_width="35%"),
            )
        ),
        rx.table.body(
            rx.foreach(
                CurationState.current_page_data,
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
                    rx.table.cell(row["Text"]),
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

class EditorState(rx.State):
    local_code: str = rx.LocalStorage("code")
    saved_code: str = ""

    @rx.event
    def set_local_code(self, code):
        self.local_code = code

    @rx.event
    def save_code(self):
        self.saved_code = self.local_code

def editor_dialog(idx, code):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button(rx.icon(tag="pen"), on_click=EditorState.set_local_code(code))
        ),
        rx.dialog.content(
            rx.vstack(
                codemirror(
                    default_language='javascript',
                    value=EditorState.local_code,
                    style={"fontSize": "14px",
                           "height": "400px" } # take available vertical space
                ),
                rx.hstack(
                    rx.button("Save", on_click=EditorState.save_code),
                    rx.dialog.close(rx.button("Cancel")),
                    spacing="1",
                ),
                spacing="1",
                height="100%",
                justify="between",  # pushes content to top and bottom
            ),
            style={
                "width": "80%",
                "max_width": "80%",
                "height": "600px",
                "flexDirection": "column",
                "padding": "1em",
            },
        )
    )

def main_content():
    """Main content area."""
    return rx.cond(
        CurationState.is_data_loaded,
        rx.vstack(
            # Controls row
            rx.hstack(
                save_dialog(),
                rx.text(f"Total records: {0}", font_size="sm", color="gray.600"),
                spacing="4",
                justify="between",
                width="100%",
                padding="4"
            ),
            # Table
            criteria_table(),
            spacing="2",
            width="100%"
        ),
        rx.center(
            rx.vstack(
                rx.icon("file-text", size=48, color="gray.400"),
                rx.text("Load a TSV file to get started", color="gray.500", font_size="lg"),
                rx.text("The grid will show your clinical trial criteria data with Excel-like filtering",
                        color="gray.400", font_size="sm"),
                spacing="3",
                align="center"
            ),
            height="400px"
        )
    )


def index() -> rx.Component:
    """Main page component."""
    return rx.card(
        rx.vstack(
            # Header
            rx.hstack(
                rx.heading("Clinical Trial Eligibility Criteria Editor"),
                rx.hstack(
                    rx.input(
                        placeholder="Enter TSV file path...",
                        value=CurationState.file_path,
                        default_value=CurationState.file_path,
                        on_change=CurationState.set_file_path,
                        width="300px"
                    ),
                    rx.button(
                        "Load File",
                        on_click=lambda: CurationState.load_file(CurationState.file_path),
                    ),
                    spacing="2"
                ),
                justify="between",
                align="center",
                width="100%",
                padding="4"
            ),
            # Main content
            main_content(),
            width="100%"
        ),
        margin="20px",
        padding="20px"
    )


style = {
    "font_size": "10px",
}

# Create the app
app = rx.App(
    theme=rx.theme(
        appearance="light",
        radius="large",
        accent_color="gray",
    ),
    style=style
)
app.add_page(index)