import logging
from typing import ClassVar

import reflex as rx
from reflex import ImportVar

from .codemirror_merge import codemirror_original, codemirror_merge, codemirror_modified

logger = logging.getLogger(__name__)

class EditorState(rx.ComponentState):
    # submit handler
    on_save: ClassVar[rx.EventHandler] = None

    @rx.event
    def save_code(self, idx):
        return rx.call_script(
            "modifiedEditor.state.doc.toString()",
            callback=self.__class__.on_save(idx)
        )

    @classmethod
    def get_component(cls, idx: int, code: str, new_code: str, on_save: rx.EventHandler, **props) -> rx.Component:
        cls.on_save = on_save
        return _create_editor_dialog(cls, idx, code, new_code)


editor_dialog = EditorState.create

class EditorSetup(rx.Fragment):

    def add_imports(self):
        return {
            "/public/criterion-autocomplete": ImportVar(tag="criterionAutocomplete", is_default=False)
        }

    def add_hooks(self) -> list[str | rx.Var]:
        """Add the hooks for the component."""
        return ["var modifiedEditor = null;"]

# NOTE: we use the update listener to store the view into a modifiedEditor variable
# this way the code can be retrieved in the event handler
def _create_editor_dialog(state, idx, code, new_code):
    return rx.dialog.root(
        EditorSetup.create(),
        rx.dialog.trigger(
            rx.button(rx.icon(tag="pen"))
        ),
        rx.dialog.content(
            rx.vstack(
                codemirror_merge(
                    codemirror_original(
                        value=code,
                        extensions=rx.Var(
                            "[EditorView.editable.of(false), EditorState.readOnly.of(true), EditorView.lineWrapping]"
                        ),
                        style={
                            "minWidth": "600px",
                            "maxWidth": "50%",
                            "overflow": "auto"
                        }
                    ),
                    codemirror_modified(
                        value=new_code,
                        extensions=rx.Var(
                            "[EditorView.lineWrapping, criterionAutocomplete, "
                            "EditorView.updateListener.of(update => { modifiedEditor = update.view; })]"
                        ),
                        style={
                            "minWidth": "600px",
                            "maxWidth": "50%",
                            "overflow": "auto"
                        }
                    ),
                    orientation="a-b",
                    style={
                        "fontSize": "12px",
                        "maxWidth": "100%",
                        "maxHeight": "90%",
                        "overflow": "auto"
                    }
                ),
                rx.hstack(
                    rx.button("Save", on_click=state.save_code(idx)),
                    rx.dialog.close(rx.button("Close")),
                    spacing="1",
                ),
                spacing="1",
                justify="between",  # pushes content to top and bottom
                style={
                    "flexGrow": "1",
                    "overflow": "hidden",
                    "height": "100%",
                }
            ),
            style={
                "width": "1400px",
                "maxWidth": "80%",
                "height": "600px",
                "flexDirection": "column",
                "padding": "1em",
            },
        )
    )
