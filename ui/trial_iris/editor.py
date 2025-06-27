import logging

import reflex as rx
from reflex import ImportVar

from pydantic_curator.criterion_parser import parse_criterion
from .codemirror_merge import codemirror_original, codemirror_merge, codemirror_modified

logger = logging.getLogger(__name__)

MODIFIED_EDITOR_VIEW_VAR = "trialIrisModifiedEditorView"

class EditorState(rx.State):
    idx: int = -1
    code: str = ""
    new_code: str = ""
    title: str = ""
    editor_open: bool = False
    error_message: str = ""

    save_override: rx.EventHandler = None

    @rx.event
    def save_button_clicked(self):
        logger.info("save button clicked")
        return rx.call_script(
            f"{MODIFIED_EDITOR_VIEW_VAR}.state.doc.toString()",
            callback=EditorState.check_save_code  # important: do not use self
        )

    @rx.event
    def check_save_code(self, new_code: str):
        logger.info(f"checking save code: {new_code}")

        # do this to ensure the edited code is not lost
        self.new_code = new_code

        # try to parse it
        try:
            parse_criterion(new_code)
            self.editor_open = False
            return self.__class__.save_override(self.idx, new_code)
        except Exception as e:
            # put the error in the table
            logger.info(f"error parsing criterion: {new_code}, error: {str(e)}")
            self.error_message = str(e)

    @rx.event
    def open_dialog(self, idx: int, code: str, new_code: str, title: str = ""):
        self.idx = idx
        self.code = code
        self.new_code = new_code
        self.title = title
        self.error_message = ""
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
                rx.text(EditorState.title),
                codemirror_diff_card(),
                rx.hstack(
                    rx.button("Save", on_click=EditorState.save_button_clicked),
                    rx.dialog.close(rx.button("Cancel")),
                    rx.spacer(width="150px"),
                    rx.cond(
                        EditorState.error_message != "",
                        rx.code_block(
                            EditorState.error_message,
                            wrap_long_lines=True,
                            font_size="12px",
                            height="auto",
                            overflow="auto"
                        ),
                    ),
                    spacing="3",
                    align="center"
                ),
                spacing="3",
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
                "height": "600px",  # fixed height
                "display": "flex",  # required for flex children
                "padding": "1em",
            }
        ),
        open=EditorState.editor_open,
        on_open_change=EditorState.set_editor_open(False),
    )

def codemirror_diff_card() -> rx.Component:
    return rx.card(
        codemirror_merge(
            codemirror_original(
                value=EditorState.code,
                extensions=rx.Var(
                    "[EditorView.editable.of(false), EditorState.readOnly.of(true), EditorView.lineWrapping]"
                ),
                style={
                    "minWidth": "50%",
                    "maxWidth": "50%"
                }
            ),
            codemirror_modified(
                value=EditorState.new_code,
                extensions=rx.Var(
                    "[EditorView.lineWrapping, criterionAutocomplete, tabAcceptKeymap, "
                    f"EditorView.updateListener.of(update => {{ {MODIFIED_EDITOR_VIEW_VAR} = update.view; }})]"
                ),
                style={
                    "minWidth": "50%",
                    "maxWidth": "50%"
                }
            ),
            orientation="a-b",
            width="100%",
            height="100%",
            fontSize="12px",
            overflow="auto"  # required to show scrollbar
        ),
        width="100%",
        height="100%",
    )
