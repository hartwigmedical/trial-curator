import os

from .criterion_grid import criteria_table, CriterionGridState

import reflex as rx
import pandas as pd
import logging

from .local_file_picker import file_picker_dialog, FilePickerState

logger = logging.getLogger(__name__)

class TrialIrisState(rx.State):
    """State management for the Curation Assistant."""

    # Data state
    trial_df: pd.DataFrame = None

    # UI state
    file_path: str = rx.Cookie(name="TrialTsv")
    show_save_dialog: bool = False

    @rx.var
    def is_data_loaded(self) -> bool:
        """Check if data is loaded."""
        return self.trial_df is not None

    @rx.event
    async def load_file(self, file_path: str):
        """Load trial data from TSV file."""
        try:
            file_path = os.path.expanduser(file_path)
            if not os.path.exists(file_path):
                return rx.toast.error("File not found")

            df = pd.read_csv(file_path, sep='\t')
            df['Override'] = df['Override'].fillna('')
            self.trial_df = df
            criterion_grid_state = await self.get_state(CriterionGridState)
            await criterion_grid_state.set_trial_df(df)

            return rx.toast.success(f"Successfully loaded {len(df)} criteria")
        except Exception as e:
            return rx.toast.error(f"Error loading file: {str(e)}",
                                  duration=180, close_button=True)

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


def save_dialog():
    """Save dialog component."""

    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button("Save", on_click=lambda: FilePickerState.open_file_picker("."))
        ),
        rx.dialog.content(
            rx.dialog.title("Save Criteria"),
            rx.form(
                rx.vstack(
                    file_picker_dialog(),
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
                on_submit=TrialIrisState.save_criteria,
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

def main_content():
    """Main content area."""
    return rx.cond(
        TrialIrisState.is_data_loaded,
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
                rx.image('/trial_iris.ico'),
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
