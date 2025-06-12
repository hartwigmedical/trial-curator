import reflex as rx


class CodeMirror(rx.Component):
    """Wrapper for code mirror editor"""

    library = "@uiw/react-codemirror"

    tag = "CodeMirror"

    is_default = True

    value: rx.Var[str]

    theme: str

    on_change: rx.EventHandler[lambda code, update: [code]]

    extensions: rx.Var[list]


codemirror = CodeMirror.create
