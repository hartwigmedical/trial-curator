import reflex as rx
from reflex import ImportVar


class CodeMirrorMerge(rx.Component):
    library = "react-codemirror-merge"
    tag = "CodeMirrorMerge"
    is_default = True

    orientation: rx.Var[str]  # e.g., "a-b" or "b-a"
    extensions: rx.Var[list]


class CodeMirrorOriginal(rx.Component):
    tag = "Original"
    is_default = False

    value: rx.Var[str]
    extensions: rx.Var[list]

    def add_imports(self):
        return {
            "@codemirror/view": ImportVar(tag="EditorView", is_default=False),
            "@codemirror/state": ImportVar(tag="EditorState", is_default=False),
        }

    def add_custom_code(self) -> list[str]:
        # this is needed to make sure we can reference the type using Original as name
        return ['const Original = CodeMirrorMerge.Original;']


class CodeMirrorModified(rx.Component):
    tag = "Modified"
    is_default = False

    value: rx.Var[str]
    extensions: rx.Var[list]

    def add_imports(self):
        return {
            "@codemirror/view": ImportVar(tag="EditorView", is_default=False),
            "@codemirror/state": ImportVar(tag="EditorState", is_default=False),
        }

    def add_custom_code(self) -> list[str]:
        # this is needed to make sure we can reference the type using Modified as name
        return ['const Modified = CodeMirrorMerge.Modified;']

codemirror_merge = CodeMirrorMerge.create
codemirror_original = CodeMirrorOriginal.create
codemirror_modified = CodeMirrorModified.create
