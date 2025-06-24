import logging

import reflex as rx

from .criterion_table import criteria_table, FileSaveLoadState

logger = logging.getLogger(__name__)

def index() -> rx.Component:
    """Main page component."""

    return rx.card(
        rx.vstack(
            # Header
            rx.hstack(
                rx.icon('dna', size=30),
                rx.heading("Trial Iris"),
                justify="start",
                align="center",
                width="100%",
                padding="4"
            ),
            criteria_table(),
            width="100%"
        ),
        margin="20px",
        padding="20px"
    )

style = {
    "font_size": "10px",
}

# Create the app
app = rx.App(
    theme=rx.theme(
        appearance="light",
        radius="large",
        accent_color="blue",
    ),
    style=style
)
app.add_page(index)
