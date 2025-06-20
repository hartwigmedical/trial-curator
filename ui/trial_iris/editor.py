import logging

import reflex as rx
from reflex import ImportVar

from .codemirror_merge import codemirror_original, codemirror_merge, codemirror_modified

logger = logging.getLogger(__name__)

MODIFIED_EDITOR_VIEW_VAR = "trialIrisModifiedEditorView"

class EditorState(rx.State):
    idx: int = -1
    code: str = ""
    new_code: str = ""
    editor_open: bool = False
    save_override: rx.EventHandler = None

    @rx.event
    def save_code(self):
        return rx.call_script(
            f"{MODIFIED_EDITOR_VIEW_VAR}.state.doc.toString()",
            callback=self.__class__.save_override(self.idx)
        )

    @rx.event
    def open_dialog(self, idx: int, code: str, new_code: str):
        self.idx = idx
        self.code = code
        self.new_code = new_code
        self.editor_open = True

class EditorSetup(rx.Fragment):
    def add_imports(self):
        return {
            "/public/criterion-autocomplete": [
                ImportVar(tag="criterionAutocomplete", is_default=False),
                ImportVar(tag="tabAcceptKeymap", is_default=False)
            ]
        }

# NOTE: we use the update listener to store the view into a modifiedEditor variable
# this way the code can be retrieved in the event handler
def editor_dialog(save_override: rx.EventHandler):
    EditorState.save_override = save_override
    return rx.dialog.root(
        rx.script(f"var {MODIFIED_EDITOR_VIEW_VAR} = null;"),  # declare a variable
        EditorSetup.create(),
        rx.dialog.content(
            rx.vstack(
                codemirror_merge(
                    codemirror_original(
                        value=EditorState.code,
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
                        value=EditorState.new_code,
                        extensions=rx.Var(
                            "[EditorView.lineWrapping, criterionAutocomplete, tabAcceptKeymap, "
                            f"EditorView.updateListener.of(update => {{ {MODIFIED_EDITOR_VIEW_VAR} = update.view; }})]"
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
                    rx.dialog.close(rx.button("Save", on_click=EditorState.save_code)),
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
        ),
        open=EditorState.editor_open,
        on_open_change=EditorState.set_editor_open(False),
    )
