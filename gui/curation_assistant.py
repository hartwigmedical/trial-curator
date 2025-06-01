import logging
import os
import pandas as pd
from nicegui import ui

from gui.criterion_editor import CriterionEditor
from pydantic_curator.criterion_parser import parse_criterion
from pydantic_curator.criterion_schema import *

logger = logging.getLogger(__name__)

BUTON_CLASS = 'bg-blue-500 text-sm'

class CurationAssistant:
    def __init__(self):
        self.trial_df = None
        self.trial_viewer = None
        self.cohort_viewer = None
        self.criterion_viewer = None
        self.page_size = 10

    @staticmethod
    def load_trial_df(file_path: str) -> pd.DataFrame | None:
        """Load eligibility criteria from a TSV file."""
        file_path = os.path.expanduser(file_path)
        try:
            if not os.path.exists(file_path):
                return None
            return pd.read_csv(file_path, sep='\t')
        except Exception as e:
            ui.notify(f"Error loading file: {str(e)}", type="negative")
            return None

    def update_trial_viewer(self):
        self.trial_viewer.clear()

        with self.trial_viewer:
            if self.trial_df is None:
                with self.trial_viewer:
                    ui.label('No trials loaded').classes('text-italic')
                    return

            ui.label('Trial IDs:').classes('text-h6 font-bold')
            trial_id_container = ui.element().classes('w-full h-full')

            trial_ids = self.trial_df['TrialId'].unique().tolist()

            page_size = 10
            total_pages = (len(trial_ids) + page_size - 1) // page_size

            def show_page(page: int):
                trial_id_container.clear()
                start_idx = page * page_size
                end_idx = min(start_idx + page_size, len(trial_ids))
                with trial_id_container:
                    with ui.grid(columns=2).classes('gap-4 w-full'):
                        for idx in range(start_idx, end_idx):
                            trial_id = trial_ids[idx]
                            ui.label(f"Trial ID: {trial_id}").classes('border shadow-sm p-2 rounded')
                            ui.button('View', on_click=lambda r=trial_id: self.switch_to_trial(r)).classes(
                                'bg-green-500 text-sm')
                    ui.pagination(1, total_pages, on_change=lambda e: show_page(e.value - 1)).classes('mt-4')

            show_page(0)

    def display_trial(self, trial_row: pd.Series):
        self.criterion_viewer.clear()
        with self.criterion_viewer:
            with ui.card().classes('w-full'):
                for col in trial_row.index:
                    with ui.row().classes('items-center gap-2'):
                        ui.label(f"{col}:").classes('font-bold')
                        ui.label(str(trial_row[col]))

    def switch_to_trial(self, trial_id):
        # show all the cohorts

        self.cohort_viewer.clear()

        with self.cohort_viewer:
            if self.trial_df.empty:
                ui.label('No criteria loaded').classes('text-italic')
                return

            cohorts = self.trial_df[self.trial_df['TrialId'] == trial_id]['Cohort'].unique().tolist()
            logger.info(f"cohorts: {cohorts}")

            ui.label(f'Cohorts for trial ID: {trial_id}').classes('text-h6 font-bold')

            with ui.grid(columns=3).classes('gap-4 w-full'):
                for cohort in cohorts:
                    # spans 2 columns
                    ui.label(f"{cohort}").classes('col-span-2 border shadow-sm p-2 rounded')
                    ui.button('View',
                              on_click=lambda: self.display_criteria(trial_id, cohort)).classes(
                        'bg-green-500 text-sm')

    # Main Application
    def run(self):
        with ui.header().classes('flex-col items-start'):
            ui.label('Clinical Trial Eligibility Criteria Editor').classes('text-h6')

        with ui.row().classes('w-full no-wrap'):
            with ui.column().classes('w-1/5'):
                with ui.card().classes('w-full'):
                    # File input section
                    file_path = ui.input(label='TSV File Path').classes('w-full text-sm border rounded')

                    # Load button
                    def load_file():
                        if not file_path.value:
                            ui.notify('Please provide a file path', type='warning')
                            return

                        self.trial_df = self.load_trial_df(file_path.value)
                        if self.trial_df is not None:
                            ui.notify(f'Successfully loaded {len(self.trial_df)} trials',
                                      type='positive')
                            self.update_trial_viewer()

                            if self.criterion_viewer:
                                self.criterion_viewer.clear()
                        else:
                            ui.notify('No trials found in file', type='warning')

                    ui.button('Load File', on_click=load_file).classes(BUTON_CLASS)

                # trial viewer
                self.trial_viewer = ui.card().classes('w-full mt-4')
                self.cohort_viewer = self.trial_viewer
                self.update_trial_viewer()

            # Right panel - Criterion Viewer
            with ui.column().classes('w-4/5'):
                self.criterion_viewer = ui.card().classes('w-full h-full')

    def save_criteria(self, cohort: str, criteria: list[BaseCriterion]):
        """Save the current criteria for the cohort."""
        try:
            # TODO: Implement actual save logic
            ui.notify(f'Saved criteria for cohort: {cohort}', type='positive')
        except Exception as e:
            ui.notify(f'Error saving criteria: {str(e)}', type='negative')

    def display_criteria(self, trial_id: str, cohort: str):

        # load up all the criteria
        criteria_df = self.trial_df[(self.trial_df['TrialId'] == trial_id) & (self.trial_df['Cohort'] == cohort)]

        # convert them to criterion objects
        criteria = criteria_df.apply(lambda row: (row['Description'], parse_criterion(row['Values'])), axis=1)

        self.criterion_viewer.clear()
        with self.criterion_viewer:
            with ui.row().classes('items-center gap-2'):
                ui.label(f"Cohort: {cohort}").classes('text-base border shadow-sm p-2 rounded')
                ui.button('Save', on_click=lambda: self.save_criteria(cohort, criteria)).classes(BUTON_CLASS)

            # grid for the header row
            with ui.grid().classes('grid-cols-3 gap-2 font-bold w-full items-center'):
                ui.label('Text').classes('border shadow-sm p-2 rounded')
                ui.label('Code').classes('border shadow-sm p-2 rounded col-span-2')

            with ui.grid(columns=3).classes('grid-cols-2 gap-2 w-full'):
                for description, criterion in criteria:
                    with ui.card():
                        ui.label(description).classes('text-base')
                    CriterionEditor(criterion).get().classes('col-span-2 p-3')


def main():
    app = CurationAssistant()
    app.run()
    ui.run(title="Clinical Trial Eligibility Criteria Editor")


if __name__ in {"__main__", "__mp_main__"}:
    main()
