import logging
import os

import pandas as pd
import reflex as rx

from .column_definitions import COLUMN_DEFINITIONS
from .criterion_grid import criteria_table, CriterionGridState

logger = logging.getLogger(__name__)


class TrialIrisState(rx.State):
    """State management for the Curation Assistant."""

    # Data state
    _trial_df: pd.DataFrame = None

    # UI state
    file_path: str = rx.Cookie(name="TrialTsv")
    show_save_dialog: bool = False

    @rx.var
    def is_data_loaded(self) -> bool:
        """Check if data is loaded."""
        return self._trial_df is not None

    @rx.var
    def total_records(self) -> int:
        return 0 if self._trial_df is None else self._trial_df.shape[0]

    @rx.event
    async def load_file(self, file_path: str):
        """Load trial data from TSV file."""
        try:
            file_path = os.path.expanduser(file_path)
            if not os.path.exists(file_path):
                return rx.toast.error("File not found")

            df = pd.read_csv(file_path, sep='\t')

            # add any missing columns
            for col in COLUMN_DEFINITIONS:
                if col.name not in df.columns:
                    logger.info(f"adding missing column: {col.name}")
                    if col.type == bool:
                        df[col.name] = False
                    elif col.type == int:
                        df[col.name] = 0
                    elif col.type == float:
                        df[col.name] = 0.0
                    else:
                        df[col.name] = None

            self._trial_df = df
            criterion_grid_state = await self.get_state(CriterionGridState)
            await criterion_grid_state.set_trial_df(df)

            return rx.toast.success(f"Successfully loaded {len(df)} criteria")
        except Exception as e:
            return rx.toast.error(f"Error loading file: {str(e)}",
                                  duration=180, close_button=True)

    @rx.event
    def save_criteria(self, save_paths: list[str]):
        logger.info(f"save criteria: {save_paths}")
        """Save the current criteria to file."""
        if not save_paths:
            return rx.toast.error("Please provide a save path")
        if len(save_paths) > 1:
            return rx.toast.error("Please select only one file to save.")
        save_path = save_paths[0]
        try:
            if not save_path:
                return rx.toast.error("Please provide a save path")

            save_path = os.path.expanduser(save_path)
            self._trial_df.to_csv(save_path, sep='\t', index=False)
            return rx.toast.success(f"Saved criteria to {save_path}")
        except Exception as e:
            return rx.toast.error(f"Error saving criteria: {str(e)}")


def main_content():
    """Main content area."""
    return rx.cond(
        TrialIrisState.is_data_loaded,
        criteria_table(TrialIrisState.save_criteria),
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
                rx.icon('dna', size=30),
                rx.heading("Trial Iris"),
                rx.hstack(
                    rx.input(
                        placeholder="Enter TSV file path...",
                        value=TrialIrisState.file_path,
                        default_value=TrialIrisState.file_path,
                        on_change=TrialIrisState.set_file_path,
                        width="300px"
                    ),
                    rx.button(
                        "Load File",
                        on_click=lambda: TrialIrisState.load_file(TrialIrisState.file_path),
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
        accent_color="teal",
    ),
    style=style
)
app.add_page(index)
