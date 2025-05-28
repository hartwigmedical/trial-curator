from nicegui import ui

from pydantic_curator.criterion_formatter import CriterionFormatter
from pydantic_curator.criterion_parser import parse_criterion
from pydantic_curator.criterion_schema import BaseCriterion, SexCriterion


class CriterionEditor:
    def __init__(self, criterion: BaseCriterion, language: str = 'Python', height: str = 'h-60'):
        self.criterion = criterion
        self.code = CriterionFormatter.format(criterion)
        self.language = language
        self.height_class = height
        self.edit_mode = False
        self._key_listener = None
        self.card = ui.card()
        self._build()

    def _build(self):
        with self.card:
            self.code_display_container = ui.element().classes('w-full')
            self._show_code()
            with ui.row().classes('items-center'):
                self.edit_button = ui.button('Edit', on_click=self._enable_edit).classes('text-sm')
                self.save_button = ui.button('Done', on_click=self._save_code).props('outline').classes('text-sm')
                self.save_button.set_visibility(False)
                self.checkbox = ui.checkbox('Checked').classes('text-sm')
                self.checkbox.set_visibility(True)

    def _enable_edit(self):
        self.edit_mode = True
        self.edit_button.set_visibility(False)
        self.save_button.set_visibility(True)
        self.checkbox.set_visibility(False)
        self._show_editor()

    def _save_code(self):
        try:
            self.criterion = parse_criterion(self.editor.value)
        except ValueError as e:
            ui.notify(f"Error parsing criterion: {str(e)}", type="negative")
            return
        self.code = self.editor.value
        self.edit_mode = False
        self.edit_button.set_visibility(True)
        self.save_button.set_visibility(False)
        self.checkbox.set_visibility(True)
        self._show_code()

        # Clean up key listener after saving
        if self._key_listener:
            self._key_listener.close()
            self._key_listener = None

    def _show_code(self):
        self.code_display_container.clear()
        with self.code_display_container:
            ui.code(self.code, language=self.language).classes('w-full')

    def _show_editor(self):
        self.code_display_container.clear()
        with self.code_display_container:
            with ui.element().classes(f'w-full overflow-x-auto {self.height_class}'):
                self.editor = ui.codemirror(self.code, indent='   ', language=self.language).classes('min-w-max')
            self._key_listener = ui.on('keydown', self._handle_keypress)

    def _handle_keypress(self, e):
        if e.args.get('key') == 's' and (e.args.get('ctrlKey') or e.args.get('metaKey')):
            e.prevent_default()  # Stop browser Save dialog
            self._save_code()

    def get(self):
        """Returns the root card element for layout/placement."""
        return self.card


# Example usage
if __name__ in {"__main__", "__mp_main__"}:
    ui.page('/')
    CriterionEditor(SexCriterion(description='accept male', sex='male'))

    ui.run()
