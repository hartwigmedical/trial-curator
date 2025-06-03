from typing import Callable

from nicegui import ui

from pydantic_curator.criterion_formatter import CriterionFormatter
from pydantic_curator.criterion_parser import parse_criterion
from pydantic_curator.criterion_schema import BaseCriterion, SexCriterion


class CriterionEditor:
    def __init__(self, criterion: BaseCriterion, on_save: Callable, height: str = 'h-60'):
        self.criterion = criterion
        self.code = CriterionFormatter.format(criterion, True)
        self.language = 'Text'
        self.height_class = height
        self.edit_mode = False
        self.on_save = on_save
        self._key_listener = None
        self.card = ui.card().classes('text-sm')
        self._build()

    def _build(self):
        with self.card:
            self.code_display_container = ui.element().classes('w-full')
            self._show_code()

            # Error message area (initially hidden)
            self.error_display = ui.element().classes(
                'text-red-600 font-mono text-sm whitespace-pre-wrap w-full')
            self.error_display.set_visibility(False)

            with ui.row().classes('items-center'):
                self.edit_button = ui.button('Edit', on_click=self._enable_edit)
                self.save_button = ui.button('Done', on_click=self._save_code).props('outline')
                self.save_button.set_visibility(False)
                self.checkbox = ui.checkbox('Checked')
                self.checkbox.set_visibility(True)

    def _enable_edit(self):
        self.edit_mode = True
        self.edit_button.set_visibility(False)
        self.save_button.set_visibility(True)
        self.checkbox.set_visibility(False)
        self.error_display.set_visibility(True)
        self._show_editor()

    def _save_code(self):
        try:
            self.criterion = parse_criterion(self.editor.value)
        except ValueError as e:
            self.show_error(str(e))
            return
        self.code = self.editor.value
        self.edit_mode = False
        self.edit_button.set_visibility(True)
        self.save_button.set_visibility(False)
        self.checkbox.set_visibility(True)
        self._show_code()
        self.clear_error()

        # Clean up key listener after saving
        if self._key_listener:
            self._key_listener.close()
            self._key_listener = None

        if self.on_save:
            self.on_save(self.code)

    def _show_code(self):
        self.code_display_container.clear()
        with self.code_display_container:
            ui.code(self.code, language=self.language).classes('w-full text-xs')

    def _show_editor(self):
        self.code_display_container.clear()
        with self.code_display_container:
            self.editor = ui.codemirror(self.code, indent='   ', language=self.language).classes('text-xs')
            self._key_listener = ui.on('keydown', self._handle_keypress)

    def _handle_keypress(self, e):
        if e.args.get('key') == 's' and (e.args.get('ctrlKey') or e.args.get('metaKey')):
            e.prevent_default()  # Stop browser Save dialog
            self._save_code()

    def show_error(self, message: str):
        self.error_display.clear()
        with self.error_display:
            ui.code(message, language='text').classes('w-full text-red-600')
        self.error_display.set_visibility(True)

    def clear_error(self):
        self.error_display.clear()
        self.error_display.set_visibility(False)

    def get(self):
        """Returns the root card element for layout/placement."""
        return self.card


# Example usage
if __name__ in {"__main__", "__mp_main__"}:
    ui.page('/')
    CriterionEditor(SexCriterion(description='accept male', sex='male'))

    ui.run()
