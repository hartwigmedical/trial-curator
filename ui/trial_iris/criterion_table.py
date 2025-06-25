import logging
import os
from typing import Any

import pandas as pd
import reflex as rx

from .column_control import column_control_menu, ColumnControlState
from .column_definitions import *
from .criterion_state import CriterionState
from .excel_style_filter import excel_style_header, excel_style_sort_header
from .row_action_menu import row_action_menu, row_action_menu_dialogs
from .local_file_picker import file_picker_dialog

logger = logging.getLogger(__name__)

# need to make them rx.Var such that can use them on rx.cond
NO_FILTER_COLUMNS: rx.Var[list[str]] = rx.Var.create([col.name for col in COLUMN_DEFINITIONS if not col.filterable])
THIN_COLUMNS: rx.Var[list[str]] = rx.Var.create([col.name for col in COLUMN_DEFINITIONS if col.thin])


class FileSaveLoadState(rx.State):
    """State management for the Curation Assistant."""
    is_data_loaded: bool = False
    total_records: int = 0

    show_confirm_overwrite_dialog: bool = False
    show_choose_file_dialog: bool = False
    file_path: str = rx.LocalStorage(name="criterion_tsv_path")
    file_picker_dir: str = rx.LocalStorage("~", name="file_picker_dir")

    @rx.var
    def file_exists(self) -> bool:
        return self.file_path != "" and os.path.exists(os.path.expanduser(self.file_path))

    @rx.event
    async def load_file(self, file_path: str):
        """Load trial data from TSV file."""
        try:
            file_path = os.path.expanduser(file_path)
            if not os.path.exists(file_path):
                yield rx.toast.error("File not found")

            logger.info(f"loading file: {file_path}")
            df = pd.read_csv(file_path, sep='\t',
                dtype={col.name: pd.StringDtype() if col.type == str else col.type for col in COLUMN_DEFINITIONS})
            logger.info(f"loaded file: {file_path}")

            # add any missing columns
            for col in COLUMN_DEFINITIONS:
                if col.name not in df.columns:
                    logger.info(f"adding missing column: {col.name}")
                    if col.type == bool:
                        df[col.name] = False
                    elif col.type == int:
                        df[col.name] = 0
                    elif col.type == float:
                        df[col.name] = 0.0
                    else:
                        df[col.name] = None

            criterion_state = await self.get_state(CriterionState)
            await criterion_state.set_trial_df(df)

            self.is_data_loaded = True
            self.total_records = len(df)
            yield rx.toast.success(f"Successfully loaded {len(df)} criteria")
        except Exception as e:
            logger.error(f"error loading file: {str(e)}")
            yield rx.toast.error(f"Error loading file: {str(e)}",
                                  duration=5000, close_button=True)

    @rx.event
    def save_button_clicked(self):
        if self.file_path == "":
            self.show_choose_file_dialog = True
        elif os.path.exists(os.path.expanduser(self.file_path)):
            logger.info(f"file already exists: {self.file_path}")
            self.show_confirm_overwrite_dialog = True
        else:
            return self.confirm_save()
        return None

    @rx.event
    def change_file_and_save(self, save_paths: list[str]):
        """Change the file and save."""
        if not save_paths:
            return rx.toast.error("Please provide a save path")
        if len(save_paths) > 1:
            return rx.toast.error("Please select only one file to save.")
        self.file_path = save_paths[0]

        # we do the same logic as if the save button is clicked
        self.save_button_clicked()
        return None

    @rx.event
    async def confirm_save(self):
        logger.info(f"saving criterion df to: {self.file_path}")
        criterion_state = await self.get_state(CriterionState)
        try:
            save_path = os.path.expanduser(self.file_path)
            columns = [c.name for c in COLUMN_DEFINITIONS if not c.isDerived]
            criterion_state._trial_df[columns].to_csv(save_path, sep='\t', index=False, na_rep='NULL')
            logger.info(f"saved criterion df to: {self.file_path}")
            yield rx.toast.success(f"Saved criteria to {self.file_path}", duration=30, close_button=True)
        except Exception as e:
            logger.error(f"error saving criteria: {str(e)}")
            yield rx.toast.error(f"Error saving criteria: {str(e)}", duration=300, close_button=True)


def render_cell(row: dict[str, Any], col: str) -> rx.Component:
    return rx.match(
        col,
        (Columns.CHECKED.name,
            rx.table.cell(
                rx.checkbox(row[col], on_change=CriterionState.mark_checked(row[INDEX_COLUMN]))
            )
         ),
        (Columns.ACTION.name,
            rx.table.cell(
                row_action_menu(row)
            )
        ),
        (Columns.CODE.name, Columns.LLM_CODE.name, Columns.OVERRIDE_CODE.name,
            rx.table.cell(
                rx.code_block(
                    row[col],
                    language="ada",
                    can_copy=False,
                    wrap_long_lines=True,
                    font_size="12px",
                    style={
                        "margin": "0"
                    }
                ),
                max_width="700px"
            )
        ),
        (Columns.OVERRIDE.name,
            rx.table.cell(
                rx.cond(
                    row[Columns.OVERRIDE_CODE.name],
                    rx.text(
                        "Yes",
                        background_color=rx.color("green", 5),
                        padding="2px 8px",
                        border_radius="4px"
                    ),
                    rx.text("No")
                )
            )
        ),
        (Columns.DESCRIPTION.name,
            rx.table.cell(row[Columns.DESCRIPTION.name], white_space="pre-wrap", min_width="200px", max_width="300px")),
        (Columns.NOTES.name,
            rx.table.cell(
                rx.text_area(
                    row[Columns.NOTES.name],
                    on_blur=CriterionState.edit_notes(row[INDEX_COLUMN]),
                    width="100%",
                    height="100%"
                ),
                min_width="100px",
                max_width="150px",
            ),
        ),
        rx.cond(THIN_COLUMNS.contains(col),
                rx.table.cell(row[col], max_width="20px"),
                rx.table.cell(row[col], white_space="pre-wrap", max_width="150px")
                )
    )

def construct_table() -> rx.Component:
    """Create the table component for displaying criteria."""
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.foreach(
                    ColumnControlState.visible_columns,
                    lambda col: rx.table.column_header_cell(
                        rx.cond(
                            NO_FILTER_COLUMNS.contains(col),
                            excel_style_sort_header(
                                col,
                                sorted_keys=CriterionState.sort_by,
                                cycle_sort_by=CriterionState.cycle_sort_by,
                                label=col
                            ),
                            excel_style_header(
                                col,
                                THIN_COLUMNS.contains(col),
                                options=CriterionState.options_dict,
                                deselected=CriterionState.deselected_dict,
                                sorted_keys=CriterionState.sort_by,
                                toggle_option=CriterionState.toggle_option,
                                select_all=CriterionState.select_all,
                                clear_all=CriterionState.clear_all,
                                cycle_sort_by=CriterionState.cycle_sort_by,
                                label=col
                            )
                        )
                    )
                )
            )
        ),
        rx.table.body(
            rx.foreach(
                CriterionState.current_page_data,
                lambda row: rx.table.row(
                    rx.foreach(
                        ColumnControlState.visible_columns,
                        lambda col: render_cell(row, col)
                    )
                )
            )
        ),
    )

def confirm_overwrite_file_dialog(file_picker) -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.vstack(
                rx.alert_dialog.description(
                    f"Overwrite existing file {FileSaveLoadState.file_path}?",
                ),
                rx.hstack(
                    rx.alert_dialog.action(
                        rx.button(
                            "Confirm",
                            on_click=FileSaveLoadState.confirm_save,
                        ),
                    ),
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Choose another file",
                            on_click=lambda: file_picker.State.open_file_picker(FileSaveLoadState.file_picker_dir)
                        ),
                    ),
                    rx.alert_dialog.cancel(
                        rx.button(
                            "Cancel"
                        ),
                    )
                )
            )
        ),
        open=FileSaveLoadState.show_confirm_overwrite_dialog,
        on_open_change=FileSaveLoadState.set_show_confirm_overwrite_dialog(False),
    )

def choose_file_dialog(file_picker) -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.dialog.description(
                    f"Please choose a file",
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.button(
                            "Choose a file",
                            on_click=lambda: file_picker.State.open_file_picker(
                                FileSaveLoadState.file_picker_dir)
                        ),
                    ),
                    rx.dialog.close(
                        rx.button(
                            "Cancel"
                        ),
                    )
                )
            ),
        ),
        open=FileSaveLoadState.show_choose_file_dialog,
        on_open_change=FileSaveLoadState.set_show_choose_file_dialog(False),
    )

def navbar() -> rx.Component:
    file_picker = file_picker_dialog(
        directory="~",
        button_text="Choose another file",
        on_submit=FileSaveLoadState.change_file_and_save
    )
    return rx.hstack(
        rx.cond(FileSaveLoadState.is_data_loaded,
            rx.hstack(
                column_control_menu(),
                rx.button(
                    "Prev",
                    on_click=CriterionState.prev_page,
                    variant="outline"),
                rx.text(f"page {CriterionState.current_page + 1} / {CriterionState.total_pages}",
                        size="2", ),
                rx.button(
                    "Next",
                    on_click=CriterionState.next_page,
                    variant="outline"
                ),
                rx.spacer(width="100px"),
                rx.text(f"Total records: {CriterionState.total_records}", font_size="sm", color="gray.600"),
                spacing="4",
                justify="between",
                padding="4",
                align="center",
                width="100%",
            ),
            rx.spacer() # force the file loading to the right hand side
        ),
        rx.hstack(
            rx.input(
                placeholder="Enter TSV file path...",
                value=FileSaveLoadState.file_path,
                default_value=FileSaveLoadState.file_path,
                on_change=FileSaveLoadState.set_file_path,
                width="300px"
            ),
            rx.button(
                "Load File",
                on_click=lambda: FileSaveLoadState.load_file(FileSaveLoadState.file_path),
                variant="outline"
            ),
            rx.button(
                "Save",
                on_click=lambda: FileSaveLoadState.save_button_clicked,
                variant="outline"
            ),
            spacing="2"
        ),
        confirm_overwrite_file_dialog(file_picker),
        choose_file_dialog(file_picker),
        file_picker,
        justify="between",
        align="center",
        width="100%"
    )

def criteria_table() -> rx.Component:
    return rx.vstack(
        navbar(),
        rx.cond(
            FileSaveLoadState.is_data_loaded,
            rx.box(
                construct_table(),
                width="100%",
                height="100%",
                overflow="auto"   # show scrollbar
            )
        ),
        row_action_menu_dialogs(CriterionState.update_criterion, CriterionState.delete_override),
        width="100%",
        height="100%"
    )
