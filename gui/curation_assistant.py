import logging
import os
import inspect
from abc import ABC
from typing import Dict, Any, Type
from nicegui import ui

from gui.criterion_editor import CriterionEditor
from pydantic_curator.criterion_schema import *
from pydantic_curator.eligibility_py_loader import exec_file_into_variable

logger = logging.getLogger(__name__)

BUTON_CLASS = 'bg-blue-500 text-sm'

def build_criterion_tree_data(criterion: BaseCriterion) -> dict:
    """Build tree data structure for a criterion."""
    criterion_type = criterion.__class__.__name__

    # For composite criteria (And, Or, Not, If)
    if isinstance(criterion, AndCriterion):
        return {
            'id': f"{criterion_type}: {criterion.description or 'AND Condition'}",
            'children': [build_criterion_tree_data(sub) for sub in criterion.criteria]
        }
    elif isinstance(criterion, OrCriterion):
        return {
            'id': f"{criterion_type}: {criterion.description or 'OR Condition'}",
            'children': [build_criterion_tree_data(sub) for sub in criterion.criteria]
        }
    elif isinstance(criterion, NotCriterion):
        return {
            'id': f"{criterion_type}: {criterion.description or 'NOT Condition'}",
            'children': [build_criterion_tree_data(criterion.criterion)]
        }
    elif isinstance(criterion, IfCriterion):
        children = [
            {'id': 'Condition:', 'children': [build_criterion_tree_data(criterion.condition)]},
            {'id': 'Then:', 'children': [build_criterion_tree_data(criterion.then)]}
        ]
        if criterion.else_:
            children.append({'text': 'Else:', 'children': [build_criterion_tree_data(criterion.else_)]})
        return {
            'id': f"{criterion_type}: {criterion.description or 'IF Condition'}",
            'children': children
        }
    else:
        # For simple criteria
        details = []
        for field_name, field_value in criterion:
            if field_name != 'description' and field_value is not None:
                if isinstance(field_value, BaseModel):
                    nested_data = field_value.model_dump()
                    nested_str = ", ".join(f"{k}: {v}" for k, v in nested_data.items() if v is not None)
                    details.append(f"{field_name}: {{{nested_str}}}")
                elif isinstance(field_value, list) and field_value:
                    details.append(f"{field_name}: [{', '.join(str(item) for item in field_value)}]")
                else:
                    details.append(f"{field_name}: {field_value}")

        criterion_details = " | ".join(details)
        display_text = f"{criterion_type}: {criterion.description or criterion_details}"
        return {'id': display_text}


class CurationAssistant:
    def __init__(self):
        self.cohort_criteria = {}
        self.cohort_viewer = None
        self.criterion_viewer = None

    @staticmethod
    def get_criterion_classes() -> Dict[str, Type[BaseCriterion]]:
        """Get all criterion classes from the schema."""
        criterion_classes = {}
        for name, obj in globals().items():
            if (inspect.isclass(obj) and issubclass(obj, BaseCriterion) and
                    obj != BaseCriterion and not ABC in obj.__bases__):
                criterion_classes[name] = obj
        return criterion_classes

    @staticmethod
    def criterion_to_dict(criterion: BaseCriterion) -> Dict[str, Any]:
        """Convert a criterion to a dictionary with its class name."""
        data = criterion.model_dump()
        data['_type'] = criterion.__class__.__name__

        # Handle nested criteria for composite types
        if isinstance(criterion, (AndCriterion, OrCriterion)):
            data['criteria'] = [CurationAssistant.criterion_to_dict(c) for c in criterion.criteria]
        elif isinstance(criterion, NotCriterion):
            data['criterion'] = CurationAssistant.criterion_to_dict(criterion.criterion)
        elif isinstance(criterion, IfCriterion):
            data['condition'] = CurationAssistant.criterion_to_dict(criterion.condition)
            data['then'] = CurationAssistant.criterion_to_dict(criterion.then)
            if criterion.else_:
                data['else'] = CurationAssistant.criterion_to_dict(criterion.else_)

        # Handle TimingInfo
        if hasattr(criterion, 'timing_info') and criterion.timing_info:
            data['timing_info'] = criterion.timing_info.model_dump()
            if criterion.timing_info.window_days:
                data['timing_info']['window_days'] = criterion.timing_info.window_days.model_dump()

        return data

    @staticmethod
    def load_criteria_from_file(file_path: str) -> Dict[str, list[BaseCriterion]]:
        """Load eligibility criteria from a Python file in format {cohort: list[BaseCriterion]}."""
        file_path = os.path.expanduser(file_path)
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return {}

            # Find all dictionaries with BaseCriterion values in the module
            cohort_criteria = exec_file_into_variable(file_path)
            return cohort_criteria
        except Exception as e:
            ui.notify(f"Error loading file: {str(e)}", type="negative")
            return {}

    def update_cohort_viewer(self):
        self.cohort_viewer.clear()

        if not self.cohort_criteria:
            with self.cohort_viewer:
                ui.label('No criteria loaded').classes('text-italic')
                return

        # Group by cohort
        with self.cohort_viewer:
            with ui.grid(columns=2).classes('gap-4 w-full'):
                for cohort, criteria in self.cohort_criteria.items():
                    ui.label(f"{cohort}").classes('border shadow-sm p-2 rounded')
                    ui.button('View',
                              on_click=lambda c=cohort, cr=criteria: self.display_criteria(c,
                                                                                           cr)).classes(
                        'bg-green-500 text-sm')

    # Main Application
    def run(self):
        with ui.header().classes('flex-col items-start'):
            ui.label('Clinical Trial Eligibility Criteria Editor').classes('text-h6')

        with ui.row().classes('w-full no-wrap'):
            with ui.column().classes('w-1/5'):
                with ui.card().classes('w-full'):
                    # File input section
                    file_path = ui.input(label='File Path').classes('w-full text-sm border rounded')

                    # Load button
                    def load_file():
                        if not file_path.value:
                            ui.notify('Please provide a file path', type='warning')
                            return

                        self.cohort_criteria = self.load_criteria_from_file(file_path.value)
                        if self.cohort_criteria:
                            ui.notify(f'Successfully loaded {len(self.cohort_criteria)} cohort criteria',
                                      type='positive')
                            self.update_cohort_viewer()

                            if self.criterion_viewer:
                                self.criterion_viewer.clear()
                        else:
                            ui.notify('No criteria found in file', type='warning')

                    ui.button('Load File', on_click=load_file).classes(BUTON_CLASS)

                # cohort viewer
                with ui.card().classes('w-full mt-4'):
                    ui.label('Cohorts:')
                    self.cohort_viewer = ui.element().classes('w-full h-full')

                    # Initial update
                    self.update_cohort_viewer()

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

    def display_criteria(self, cohort: str, criteria: list[BaseCriterion]):
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
                for criterion in criteria:
                    with ui.card():
                        ui.label(criterion.description).classes('text-base')
                    CriterionEditor(criterion).get().classes('col-span-2 p-3')


def main():
    app = CurationAssistant()
    app.run()
    ui.run(title="Clinical Trial Eligibility Criteria Editor")


if __name__ in {"__main__", "__mp_main__"}:
    main()
