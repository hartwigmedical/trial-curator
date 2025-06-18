import reflex as rx

from .codemirror import codemirror
from .codemirror_merge import codemirror_original, codemirror_merge, codemirror_modified


class EditorState(rx.State):
    local_code: str = rx.LocalStorage("code")
    saved_code: str = ""

    @rx.event
    def set_local_code(self, code):
        self.local_code = code

    @rx.event
    def save_code(self):
        self.saved_code = self.local_code

def editor_dialog(idx, code):
    return rx.dialog.root(
        rx.dialog.trigger(
            rx.button(rx.icon(tag="pen"), on_click=EditorState.set_local_code(code))
        ),
        rx.dialog.content(
            rx.vstack(
                codemirror_merge(
                    codemirror_original(
                        value=code,
                        extensions=rx.Var(
                            "[EditorView.editable.of(false), EditorState.readOnly.of(true)]"
                        ),
                        style={
                            "maxWidth": "50%",
                            "overflow": "auto"
                        }
                    ),
                    codemirror_modified(
                        value=EditorState.local_code,
                        extensions=rx.Var(
                            "[EditorView.editable.of(true), EditorState.readOnly.of(false)]"
                        ),
                        style={
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
                    rx.button("Save", on_click=EditorState.save_code),
                    rx.dialog.close(rx.button("Cancel")),
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
