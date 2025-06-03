import logging
import os

import pandas as pd
from nicegui import ui

from gui.criterion_editor import CriterionEditor
from gui.excel_style_filter import ExcelStyleFilter
from gui.local_file_picker import local_file_picker
from pydantic_curator.criterion_parser import parse_criterion

logger = logging.getLogger(__name__)

BUTTON_CLASS = 'text-sm'
CRTERION_GRID_COL_SPANS = [2, 2, 1, 5, 10]

class CurationAssistant:
    def __init__(self):
        self.trial_df = None
        self.filters: dict[str, list[str]] = {}
        self.filtered_trial_df = None
        self.criterion_viewer = None
        self.criterion_grid = None
        self.criterion_pagination = None
        self.page_size = 30

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

    def update_filter_bar(self):
        #self.filter_sidebar.set_trial_df(self.trial_df)
        pass

    def display_trial(self, trial_row: pd.Series):
        self.criterion_viewer.clear()
        with self.criterion_viewer:
            with ui.card().classes('w-full'):
                for col in trial_row.index:
                    with ui.row().classes('items-center gap-2'):
                        ui.label(f"{col}:").classes('font-bold')
                        ui.label(str(trial_row[col]))

    # Main Application
    def run(self):
        with ui.header().classes('justify-between items-center'):
            ui.label('Clinical Trial Eligibility Criteria Editor').classes('text-h6')
            with ui.row().classes('items-center gap-2 text-sm'):
                file_path = ui.input(label='TSV File Path').classes('w-64 border rounded')
                ui.button('Load File', on_click=lambda: self.load_file(file_path)).classes(BUTTON_CLASS)

        with ui.row().classes('w-full no-wrap'):
            self.criterion_viewer = ui.card().classes('w-full h-screen').tight()
            self.criterion_viewer.set_visibility(False)

    # Load button
    def load_file(self, file_path):
        if not file_path.value:
            ui.notify('Please provide a file path', type='warning')
            return

        self.trial_df = self.load_trial_df(file_path.value)
        self.filtered_trial_df = self.trial_df
        if self.trial_df is not None:
            self.trial_df['Override'] = self.trial_df['Override'].fillna('')

            ui.notify(f'Successfully loaded {len(self.trial_df)} trials',
                      type='positive')
            self.update_filter_bar()
            self.build_criterion_viewer()
            self.display_criteria()
        else:
            ui.notify('No trials found in file', type='warning')

    async def save_criteria(self):
        """Save the current criteria for the cohort."""

        def do_save(path: str):
            logger.info(f'Saving criteria to {path}')
            self.trial_df.to_csv(path, sep='\t', index=False)
            ui.notify(f'Saved criteria to {path}', type='positive')

        try:
            paths = await local_file_picker('~', multiple=False)

            if paths is None:
                return

            out_tsv = paths[0]
            if os.path.exists(out_tsv):
                dialog = ui.dialog().props('persistent')
                with dialog:
                    with ui.card():
                        ui.label(f'File "{out_tsv}" already exists. Overwrite?')
                        with ui.row().classes('justify-end w-full'):
                            ui.button('Cancel', on_click=dialog.close).props('outline')
                            ui.button('Overwrite',
                                      on_click=lambda: (dialog.close(), do_save(out_tsv)))
                dialog.open()

            else:
                do_save(out_tsv)

        except Exception as e:
            ui.notify(f'Error saving criteria: {str(e)}', type='negative')

    def build_criterion_viewer(self):
        total_pages = (len(self.trial_df) + self.page_size - 1) // self.page_size

        self.criterion_viewer.set_visibility(True)
        self.criterion_viewer.clear()

        with self.criterion_viewer:

            with ui.row().classes('items-center gap-10'):
                # ui.label(f"Cohort: {cohort}").classes('text-base border shadow-sm p-2 rounded')
                ui.button('Save', on_click=lambda: self.save_criteria()).classes(BUTTON_CLASS)

                self.criterion_pagination = ui.pagination(1, total_pages, direction_links=True,
                                                          on_change=lambda e: self.show_page(e.value - 1))

            # grid for the header row
            with ui.grid(columns=sum(CRTERION_GRID_COL_SPANS)).classes('gap-0 font-bold w-full items-center pr-4'):
                colspan_itr = iter(CRTERION_GRID_COL_SPANS)
                ExcelStyleFilter('Trial ID', self.trial_df['TrialId'].unique().tolist(),
                                 f'col-span-{next(colspan_itr)}',
                                 on_change=lambda x: self.on_filter_change('TrialId', x))
                ExcelStyleFilter('Cohort', self.trial_df['Cohort'].unique().tolist(),
                                 f'col-span-{next(colspan_itr)}',
                                 on_change=lambda x: self.on_filter_change('Cohort', x))
                ui.label('Override').classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')
                ui.label('Text').classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')
                ui.label('Code').classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')

            with ui.element().classes('flex-1 overflow-y-auto w-full'):
                self.criterion_grid = ui.grid(columns=sum(CRTERION_GRID_COL_SPANS)).classes('gap-0 w-full')

    def display_criteria(self):
        total_pages = (len(self.filtered_trial_df) + self.page_size - 1) // self.page_size
        self.criterion_pagination.max = total_pages
        self.show_page(0)

    def show_page(self, page: int):
        # convert them to criterion objects
        criteria = self.filtered_trial_df.apply(
            lambda row: (row.name, row['TrialId'], row['Cohort'], row['Override'], row['Description'], row['Values']), axis=1)

        #logger.info(f"showing {len(criteria)} criteria")

        self.criterion_grid.clear()

        with ((self.criterion_grid)):
            # filter by page number
            page_criteria: list = criteria[page * self.page_size:(page + 1) * self.page_size]
            if len(page_criteria) > 0:
                for index, trial_id, cohort, override, description, value in page_criteria:
                    criterion = parse_criterion(override if override else value)
                    colspan_itr = iter(CRTERION_GRID_COL_SPANS)
                    ui.label(trial_id).classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')
                    ui.label(cohort).classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')
                    label_color = 'bg-green-200' if override else ''
                    ui.label('Yes' if override else 'No').classes(f'{label_color} border shadow-sm p-2 col-span-{next(colspan_itr)}')
                    ui.label(description).classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')
                    CriterionEditor(criterion, on_save=lambda code, index=index: self.on_code_save(index, code)).get()\
                        .classes(f'border shadow-sm p-2 col-span-{next(colspan_itr)}')

    def on_filter_change(self, filter_name, filter_value):
        self.filters[filter_name] = filter_value

        #logger.info(f"filters: {self.filters}")

        # reapply all filters
        filter_flag = pd.Series(True, index=self.trial_df.index)
        for filter_name, filter_value in self.filters.items():
            filter_flag &= self.trial_df[filter_name].isin(filter_value)
        self.filtered_trial_df = self.trial_df[filter_flag]
        self.display_criteria()

    def on_code_save(self, index, criterion):
        # save it on the dataframe
        logger.info(f'updating index: {index} with criterion: {criterion}')
        self.trial_df.loc[index, 'Override'] = criterion

@ui.page('/gui')
def gui():
    app = CurationAssistant()
    app.run()

@ui.page('/')
def index():
    ui.navigate.to('/gui')

def main():
    ui.run(title="Clinical Trial Eligibility Criteria Editor")


if __name__ in {"__main__", "__mp_main__"}:
    main()
