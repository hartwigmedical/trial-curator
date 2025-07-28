import asyncio
import time
from typing import Any

import reflex as rx

from pydantic_curator import pydantic_curator_run
from pydantic_curator.criterion_formatter import format_criterion
from pydantic_curator.eligibility_py_loader import exec_py_into_variable
from trialcurator.openai_client import OpenaiClient
from .column_definitions import *
from .criterion_table import INDEX_COLUMN
from .editor import editor_dialog, EditorState



# follow example here: https://reflex.dev/docs/library/overlay/dropdown-menu/

class RowActionMenuState(rx.State):
    row_idx: int = -1
    delete_override_dialog_open: bool = False

    # LLM recurate states
    llm_curate_dialog_open: bool = False
    llm_instructions: str = ""
    rule_to_recurate: str = ""
    existing_code: str = ""

    llm_curate_status_dialog_open: bool = False
    waiting_for_llm: bool = False
    llm_curation_error: str = ""

    @rx.event
    def open_delete_override_dialog(self, idx: int):
        self.row_idx = idx
        self.delete_override_dialog_open = True

    @rx.event
    def open_llm_curate_dialog(self, idx: int, rule_to_recurate: str, existing_code: str):
        self.row_idx = idx
        self.rule_to_recurate = rule_to_recurate
        self.existing_code = existing_code
        self.llm_curate_dialog_open = True

    def llm_curate(self) -> str:
        client = OpenaiClient()
        eligibility_curator.llm_curate_by_batch(self.rule_to_recurate, client, self.llm_instructions)
        python_code = eligibility_curator.llm_curate_by_batch(self.rule_to_recurate, client, self.llm_instructions)
        criteria: list[BaseCriterion] = exec_py_into_variable(python_code)
        return format_criterion(criteria[0])

    @rx.event(background=True)
    async def run_llm_curate(self):
        async with self:
            self.waiting_for_llm = True
            self.llm_curate_status_dialog_open = True
        try:
            llm_curate_task = asyncio.create_task(rx.run_in_thread(self.llm_curate))
            # Run with a timeout of 60 second (not enough time)
            llm_curated_code = await asyncio.wait_for(
                llm_curate_task,
                timeout=60,
            )
            async with self:
                self.waiting_for_llm = False
                # open up the editor dialog with the LLM curation result
                editor_state = await self.get_state(EditorState)
                editor_state.open_dialog(self.row_idx, self.existing_code, llm_curated_code,
                                               "AI curation result:")

        except asyncio.TimeoutError:
            async with self:
                self.llm_curation_error = "Timeout: LLM recurate took too long."
                self.waiting_for_llm = False
        except asyncio.CancelledError:
            async with self:
                self.llm_curation_error = "Cancelled: LLM recurate was cancelled."
                self.waiting_for_llm = False
        except Exception as e:
            async with self:
                self.llm_curation_error = f"Error: {str(e)}"
                self.waiting_for_llm = False

    @rx.event
    def cancel_llm_curate(self):
        if self._llm_curate_task is not None:
            self._llm_curate_task.cancel()

    @rx.event
    def clear_llm_curate_state(self):
        self.llm_curate_dialog_open: bool = False
        self.llm_instructions: str = ""
        self.rule_to_recurate: str = ""
        self.existing_code: str = ""

        self.llm_curate_status_dialog_open: bool = False
        self.waiting_for_llm: bool = False
        self.llm_curate_status: str = ""
        self.llm_curation_error: str = ""


def row_action_menu(row: dict[str, Any]) -> rx.Component:
    """Create action menu component for a row."""
    return rx.vstack(
        rx.menu.root(
            rx.menu.trigger(
                rx.icon("wrench")
            ),
            rx.menu.content(
                rx.menu.item(
                    rx.hstack(rx.text("Edit"), rx.icon("pen_line", size=15), align="center", justify="between"),
                    on_click=EditorState.open_dialog(
                        row[INDEX_COLUMN],
                        row[Columns.LLM_CODE.name],
                        rx.cond(row[Columns.OVERRIDE_CODE.name], row[Columns.OVERRIDE_CODE.name],
                                row[Columns.LLM_CODE.name])
                    ),
                ),
                rx.cond(
                    row[Columns.OVERRIDE.name],
                    rx.menu.item(
                        "Delete override",
                        on_click=RowActionMenuState.open_delete_override_dialog(row[INDEX_COLUMN]),
                    )
                ),
                rx.menu.item(
                    "AI recurate",
                    on_click=RowActionMenuState.open_llm_curate_dialog(
                        row[INDEX_COLUMN],
                        row[Columns.DESCRIPTION.name],
                        row[Columns.LLM_CODE.name]
                    ),
                ),
                rx.menu.sub(
                    rx.menu.sub_trigger(
                        rx.hstack(rx.text("Mark as"), rx.icon("notebook-pen", size=15), align="center", justify="between")
                    ),
                    rx.menu.sub_content(
                        rx.menu.item("Checked"),
                        rx.menu.item("Intent unclear"),
                        rx.menu.item("Advanced options…"),
                    ),
                )
            )
        ),
        align="center",
    )

def row_action_menu_dialogs(save_override: rx.EventHandler, delete_override: rx.EventHandler) -> rx.Component:
    return rx.flex(
        editor_dialog(save_override),
        delete_override_dialog(delete_override),
        llm_curate_dialog(),
        llm_curate_status_dialog(),
    )

def delete_override_dialog(delete_override: rx.EventHandler) -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete Override?"),
            rx.flex(
                rx.alert_dialog.action(
                    rx.button("Confirm"),
                    on_click=delete_override(RowActionMenuState.row_idx)
                ),
                rx.alert_dialog.cancel(
                    rx.button("Cancel"),
                ),
                spacing="3",
            ),
        ),
        open=RowActionMenuState.delete_override_dialog_open,
        on_open_change=RowActionMenuState.set_delete_override_dialog_open(False)
    )

def llm_curate_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("AI recurate"),
                rx.vstack(
                    rx.text_area(
                        placeholder="additional instructions",
                        on_blur=RowActionMenuState.set_llm_instructions,
                        width="100%",
                        height="300px"),
                    rx.flex(
                        rx.dialog.close(
                            rx.button("Submit", on_click=RowActionMenuState.run_llm_curate)
                        ),
                        rx.dialog.close(
                            rx.button("Cancel"),
                        ),
                        spacing="3",
                    ),
                    spacing="3",
                )
        ),
        height="500px",
        open=RowActionMenuState.llm_curate_dialog_open,
        on_open_change=RowActionMenuState.set_llm_curate_dialog_open(False)
    )

def llm_waiting_for_llm_content() -> rx.Component:
    return rx.dialog.content(
        rx.vstack(
            rx.text("Running LLM recurate..."),
            rx.spinner(size="2"),
            rx.flex(
                rx.dialog.close(
                    rx.button("Cancel"),
                ),
            ),
            spacing="3",
        )
    )

def llm_curate_status_dialog_content(save_override: rx.EventHandler) -> rx.Component:
    return rx.dialog.content(
        rx.vstack(
            rx.text("AI curation result:"),
            rx.code_block(
                RowActionMenuState.llm_curate_status,
                can_copy=False,
                wrap_long_lines=True,
                font_size="12px",
                width="100%",
                height="300px"),
            rx.hstack(
                rx.dialog.close(
                    rx.button(
                        "Accept",
                        on_click=[save_override(RowActionMenuState.row_idx, RowActionMenuState.llm_curate_status),
                                  RowActionMenuState.clear_llm_curate_state]
                    ),
                ),
                rx.dialog.close(
                    rx.button("Discard", on_click=RowActionMenuState.clear_llm_curate_state),
                ),
                spacing="3",
            ),
            spacing="3",
        )
    )

def llm_curate_error_dialog_content() -> rx.Component:
    return rx.dialog.content(
        rx.vstack(
            rx.text("AI curation error:"),
            rx.text(RowActionMenuState.llm_curation_error),
            rx.dialog.close(
                rx.button("Close"),
            ),
            spacing="3",
        )
    )

def llm_curate_status_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.cond(
            RowActionMenuState.waiting_for_llm,
            llm_waiting_for_llm_content(),
            rx.cond(
                RowActionMenuState.llm_curation_error != "",
                llm_curate_error_dialog_content()
            )
        ),
        height="500px",
        open=RowActionMenuState.llm_curate_status_dialog_open,
        on_open_change=RowActionMenuState.set_llm_curate_status_dialog_open(False)
    )
