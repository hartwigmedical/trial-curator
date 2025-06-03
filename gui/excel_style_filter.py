from nicegui import ui
from typing import List, Callable


class ExcelStyleFilter:
    def __init__(self, label: str, options: List[str], classes: str, on_change: Callable[[List[str]], None] = None):
        self.options = options
        self.on_change = on_change
        self.selected = set(options)
        self.checkbox_refs = {}

        self.trigger_button = None
        self.menu = None

        with ui.column().classes('w-full ' + classes):
            with ui.row().classes('items-center gap-2 w-full'):
                with ui.button(label) \
                        .classes('normal-case border shadow-sm p-2 bg-white text-black font-bold rounded-none w-full') \
                        .props('flat'):
                    with ui.menu() as self.menu:
                        with ui.column().classes('p-2 min-w-[200px] max-h-[300px] overflow-y-auto'):
                            ui.button('Select All', on_click=self._select_all).props('dense flat')
                            ui.button('Clear All', on_click=self._clear_all).props('dense flat')

                            for opt in options:
                                checkbox = ui.checkbox(opt, value=True,
                                                       on_change=lambda e, opt=opt: self._toggle(opt, e.value))
                                self.checkbox_refs[opt] = checkbox

    def _open_menu(self):
        self.menu.open()

    def _toggle(self, opt: str, checked: bool):
        if checked:
            self.selected.add(opt)
        else:
            self.selected.discard(opt)
        if self.on_change:
            self.on_change(sorted(self.selected))

    def _select_all(self):
        self.selected = set(self.options)
        for cb in self.checkbox_refs.values():
            cb.value = True
        if self.on_change:
            self.on_change(sorted(self.selected))

    def _clear_all(self):
        self.selected.clear()
        for cb in self.checkbox_refs.values():
            cb.value = False
        if self.on_change:
            self.on_change([])

    def get_selected(self) -> List[str]:
        return sorted(self.selected)
