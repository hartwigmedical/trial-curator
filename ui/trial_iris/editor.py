import reflex as rx

from .codemirror import codemirror

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
                codemirror(
                    value=EditorState.local_code,
                    extensions=rx.Var("[EditorView.lineWrapping, langs.python()]"),
                    style={
                        "fontSize": "12px",
                        "maxWidth": "100%",
                        "maxHeight": "90%",
                        "overflow": "auto"
                    }
                ),
                rx.hstack(
                    rx.button("Save", on_click=EditorState.save_code),
                    rx.dialog.close(rx.button("Cancel"), on_click=EditorState.set_local_code("")),
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
                "width": "1000px",
                "maxWidth": "80%",
                "height": "600px",
                "flexDirection": "column",
                "padding": "1em",
            },
        )
    )
