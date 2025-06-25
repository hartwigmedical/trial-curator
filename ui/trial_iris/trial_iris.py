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
            width="100%",
            height="100%"
        ),
        margin="15px",
        padding="15px",
        width="calc(100vw - 30px)",
        height="calc(100vh - 30px)"
    )

style = {
    "font_size": "10px",
    rx.button: {
        "variant": "outline",
    },
    "overflow": "hidden"
}

# Create the app
app = rx.App(
    theme=rx.theme(
        appearance="light",
        radius="large",
        accent_color="blue",
    ),
    style=style,
    stylesheets=[
        "/styles.css",  # required to force code block to wrap
    ],
)
app.add_page(index)
