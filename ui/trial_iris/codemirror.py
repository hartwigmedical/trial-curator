import reflex as rx
from reflex import ImportVar


class CodeMirror(rx.Component):
    """Wrapper for code mirror editor"""

    library = "@uiw/react-codemirror"

    tag = "CodeMirror"

    is_default = True

    value: rx.Var[str]

    theme: str

    on_change: rx.EventHandler[lambda code, update: [code]]

    extensions: rx.Var[list]

    def add_imports(self):
        """Add imports to the component."""
        return {
            "@uiw/codemirror-extensions-langs": ImportVar(
                tag="langs",
                is_default=False),
            "@uiw/react-codemirror": ImportVar(
                tag="EditorView",
                is_default=False,
            )}

codemirror = CodeMirror.create
