from typing import Callable
import pandas as pd
from nicegui import ui

from gui.excel_style_filter import ExcelStyleFilter

@ui.refreshable
class FilterSidebar:
    def __init__(
        self,
        on_apply: Callable[[dict], None],
    ):
        self.trial_df = pd.DataFrame(data={"TrialId": [], "Cohort": []})
        self.on_apply = on_apply

        self.get_checked, self.set_checked = ui.state(False)
        self.get_override, self.set_override = ui.state(False)

        # Excel-style filter components
        self.trial_filter = None
        self.cohort_filter = None
        self.type_filter = None

        self.container = ui.column().classes('w-64 p-4 bg-gray-100 gap-2 rounded-lg')  # ✅ Root container
        self._build_sidebar()

    def set_trial_df(self, trial_df):
        self.trial_df = trial_df
        self._build_sidebar()

    def _build_sidebar(self):
        self.container.clear()

        with self.container:
            ui.label('Filters').classes('text-lg font-bold mb-2')

            '''
            self.trial_filter = ExcelStyleFilter(
                label='Trial',
                options=self.trial_df['TrialId'].unique().tolist(),
                on_change=self._update_cohort_options
            )

            # initially show all cohorts
            all_cohorts = sorted(self.trial_df['Cohort'].unique().tolist())
            self.cohort_filter = ExcelStyleFilter(
                label='Cohort',
                options=all_cohorts
            )

            self.type_filter = ExcelStyleFilter(
                label='Criterion Type',
                options=self.trial_df['TrialId'].unique().tolist(),
            )
            '''

            ui.checkbox('Checked only', value=self.get_checked).on('change', lambda e: self.set_checked(e.value))
            ui.checkbox('Has override', value=self.get_override).on('change', lambda e: self.set_override(e.value))

            ui.button('Apply Filters', on_click=self._apply_filters).classes('mt-2')

    def _update_cohort_options(self, selected_trials: list[str]):
        # Dynamically update cohort options based on selected trials
        cohort_options = sorted({
            cohort
            for trial in selected_trials
            for cohort in self.cohorts_by_trial.get(trial, [])
        })
        self.cohort_filter.options = cohort_options
        self.cohort_filter.selected = set(cohort_options)
        for opt, cb in self.cohort_filter.checkbox_refs.items():
            cb.visible = opt in cohort_options
            cb.value = True if opt in cohort_options else False
        self.cohort_filter._update_label()

    def _apply_filters(self):
        self.on_apply({
            'trials': self.trial_filter.get_selected(),
            'cohorts': self.cohort_filter.get_selected(),
            'types': self.type_filter.get_selected(),
            'checked_only': self.get_checked,
            'override_only': self.get_override,
        })
