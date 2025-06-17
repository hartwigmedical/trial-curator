import platform
import reflex as rx
from pathlib import Path
from typing import Optional, List


class FilePickerState(rx.State):
    # Dialog state
    dialog_open: bool = False

    # File picker configuration
    current_path: str = ""
    upper_limit: Optional[str] = None
    multiple: bool = False
    show_hidden_files: bool = False

    # UI state
    grid_data: list[dict] = []
    selected_files: list[str] = []
    drives: list[str] = []
    current_drive: str = ""

    # Result
    submitted_files: List[str] = []

    def open_file_picker(self, directory: str, upper_limit: Optional[str] = None,
                         multiple: bool = False, show_hidden_files: bool = False):
        """Open the file picker dialog"""
        self.current_path = str(Path(directory).expanduser())
        self.upper_limit = str(
            Path(directory if upper_limit == "..." else upper_limit).expanduser()) if upper_limit else None
        self.multiple = multiple
        self.show_hidden_files = show_hidden_files
        self.selected_files = []

        # Initialize drives for Windows
        if platform.system() == 'Windows':
            try:
                import win32api
                self.drives = win32api.GetLogicalDriveStrings().split('\000')[:-1]
                self.current_drive = self.drives[0] if self.drives else ""
            except ImportError:
                self.drives = []
                self.current_drive = ""
        else:
            self.drives = []
            self.current_drive = ""

        self.update_grid()
        self.dialog_open = True

    def close_dialog(self):
        """Close the file picker dialog"""
        self.dialog_open = False

    def update_drive(self, drive: str):
        """Update current drive (Windows only)"""
        self.current_drive = drive
        self.current_path = str(Path(drive).expanduser())
        self.update_grid()

    def update_grid(self):
        """Update the file grid with current directory contents"""
        current_path_obj = Path(self.current_path)

        try:
            paths = list(current_path_obj.glob('*'))
            if not self.show_hidden_files:
                paths = [p for p in paths if not p.name.startswith('.')]

            # Sort: directories first, then alphabetically
            paths.sort(key=lambda p: p.name.lower())
            paths.sort(key=lambda p: not p.is_dir())

            self.grid_data = []

            # Add parent directory if not at upper limit
            should_add_parent = (
                    (self.upper_limit is None and current_path_obj != current_path_obj.parent) or
                    (self.upper_limit is not None and current_path_obj != Path(self.upper_limit))
            )
            if should_add_parent:
                self.grid_data.append({
                    'name': '📁 ..',
                    'path': str(current_path_obj.parent),
                    'is_dir': True
                })

            # Add files and directories
            for p in paths:
                if p.is_dir():
                    name = f'📁 {p.name}'
                else:
                    name = p.name

                self.grid_data.append({
                    'name': name,
                    'path': str(p),
                    'is_dir': p.is_dir()
                })

        except PermissionError:
            self.grid_data = [{'name': 'Permission denied', 'path': '', 'is_dir': False}]

    def handle_row_click(self, row_data: dict):
        """Handle row click to navigate directories or select files"""
        path_str = row_data.get('path', '')
        if not path_str:
            return

        path_obj = Path(path_str)

        if path_obj.is_dir():
            self.current_path = str(path_obj)
            self.update_grid()
        else:
            # File selected - toggle selection for multiple mode
            if self.multiple:
                if path_str in self.selected_files:
                    self.selected_files.remove(path_str)
                else:
                    self.selected_files.append(path_str)
            else:
                # Single selection - submit immediately
                self.submit_files([path_str])

    def toggle_file_selection(self, file_path: str):
        """Toggle file selection for multiple mode"""
        if file_path in self.selected_files:
            self.selected_files.remove(file_path)
        else:
            self.selected_files.append(file_path)

    def handle_form_submit(self, form_data: dict):
        """Handle form submission"""
        # Check if user entered a new filename
        new_filename = form_data.get("new_filename", "").strip()
        if new_filename:
            new_file_path = str(Path(self.current_path) / new_filename)
            self.submit_files([new_file_path])
            return

        # Use selected files from grid
        if not self.selected_files:
            return rx.toast.error("Please select a file or enter a name")

        self.submit_files(self.selected_files)

    def submit_files(self, file_paths: List[str]):
        """Submit selected files and close dialog"""
        self.submitted_files = file_paths
        self.dialog_open = False
        return rx.toast.success(f"Selected: {', '.join(file_paths)}")


def file_picker_dialog():
    """File picker dialog component"""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Select File"),
            rx.form(
                rx.vstack(
                    # Windows drive selector
                    rx.cond(
                        FilePickerState.drives,
                        rx.hstack(
                            rx.text("Drive:"),
                            rx.select(
                                FilePickerState.drives,
                                value=FilePickerState.current_drive,
                                on_change=FilePickerState.update_drive
                            ),
                            spacing="2"
                        )
                    ),

                    # Current path display
                    rx.text(f"Current path: {FilePickerState.current_path}", size="2", color="gray"),

                    # File table
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.cond(
                                    FilePickerState.multiple,
                                    rx.table.column_header_cell("Select"),
                                ),
                                rx.table.column_header_cell("File"),
                            )
                        ),
                        rx.table.body(
                            rx.foreach(
                                FilePickerState.grid_data,
                                lambda row: rx.table.row(
                                    rx.cond(
                                        FilePickerState.multiple,
                                        rx.table.cell(
                                            rx.cond(
                                                ~row['is_dir'],
                                                rx.checkbox(
                                                    checked=row['checked'],
                                                    on_change=lambda checked: FilePickerState.toggle_file_selection(row['path']),
                                                ),
                                                rx.text("")
                                            )
                                        ),
                                    ),
                                    rx.table.cell(
                                        rx.text(
                                            row['name'],
                                            cursor="pointer",
                                            _hover={"background_color": "var(--gray-3)"},
                                            padding="2",
                                            border_radius="4px",
                                            on_click=lambda: FilePickerState.handle_row_click(row)
                                        )
                                    ),
                                    _hover={"background_color": "var(--gray-2)"}
                                )
                            )
                        ),
                        size="2",
                        variant="surface",
                        height="300px",
                        overflow="auto",
                        border="1px solid var(--gray-6)",
                        border_radius="8px"
                    ),

                    # New filename input
                    rx.input(
                        name="new_filename",
                        placeholder="Or enter a new file name",
                        width="100%"
                    ),

                    # Buttons
                    rx.hstack(
                        rx.dialog.close(
                            rx.button("Cancel", variant="outline", type="button"),
                        ),
                        rx.button("OK", type="submit"),
                        spacing="3",
                        justify="end",
                        width="100%"
                    ),

                    spacing="4",
                    width="100%"
                ),
                on_submit=FilePickerState.handle_form_submit
            )
        ),
        open=FilePickerState.dialog_open,
        on_open_change=FilePickerState.set_dialog_open
    )


def demo_page():
    """Demo page showing how to use the file picker"""
    return rx.vstack(
        rx.heading("File Picker Demo"),

        rx.hstack(
            rx.button(
                "Single File",
                on_click=lambda: FilePickerState.open_file_picker(
                    directory=".",
                    multiple=False
                )
            ),
            rx.button(
                "Multiple Files",
                on_click=lambda: FilePickerState.open_file_picker(
                    directory=".",
                    multiple=True
                )
            ),
            rx.button(
                "Show Hidden Files",
                on_click=lambda: FilePickerState.open_file_picker(
                    directory=".",
                    show_hidden_files=True
                )
            ),
            spacing="3"
        ),

        rx.cond(
            FilePickerState.submitted_files,
            rx.vstack(
                rx.text("Selected files:", weight="bold"),
                rx.foreach(
                    FilePickerState.submitted_files,
                    lambda file: rx.text(f"• {file}")
                )
            )
        ),

        file_picker_dialog(),

        spacing="4",
        align="start",
        padding="4"
    )


app = rx.App()
app.add_page(demo_page, route="/")