from typing import Any

import reflex as rx

# this is a small state to support searching for options
# similar to excel, when user type the search term, the
# the list of options shown will be reduced to those matching
class OptionSearchState(rx.State):
    key: str = ""
    search_matched_options: list[Any] = []

    @rx.event
    def on_search(self, key, search_term: str, options):
        self.key = key
        self.search_matched_options = [o for o in options if str(o).startswith(search_term)]

    @rx.event
    def on_clear(self):
        self.key = ""
        self.search_matched_options = []

def sort_button(key: str, cycle_sort_by: rx.EventHandler) -> rx.Component:
    return rx.button(
                "Sort",
                rx.icon("arrow-up-down", size=14),
                on_click=lambda: cycle_sort_by(key),
                variant="ghost",
                size="1")

def filter_header(
        key: str,
        options: dict[str, list[Any]],
        deselected: dict[str, list[Any]],
        sorted_keys: list[str],
        toggle_option: rx.EventHandler,
        select_all: rx.EventHandler,
        clear_all: rx.EventHandler,
        cycle_sort_by: rx.EventHandler,
        label: str
) -> rx.Component:
    """
    Args:
        options: list of filter options
        label: The button label
        classes: Additional CSS classes
        compact: Whether to use compact mode (for table headers)
        storage_key: Key for localStorage (auto-generated if not provided)
        on_change: Callback function when selection changes
    """
    def create_checkbox_item(option: Any):
        return rx.hstack(
            rx.checkbox(
                checked=~(deselected[key].contains(option)),
                on_change=lambda: toggle_option(key, option),
                size="1"
            ),
            rx.text(option, size="2"),
            align="center",
            spacing="2",
            width="100%",
            padding="4px 8px",
            _hover={"bg": "gray.100"},
            cursor="pointer",
            border_radius="sm"
        )

    # Create the dropdown menu content
    menu_content = rx.vstack(
        # Control buttons
        rx.hstack(
            rx.button(
                "All",
                on_click=select_all(key),
                size="1",
                variant="ghost",
                _hover={"bg": "blue.50"}
            ),
            rx.button(
                "None",
                on_click=clear_all(key),
                size="1",
                variant="ghost",
                _hover={"bg": "red.50"}
            ),
            sort_button(key, cycle_sort_by),
            width="100%",
            spacing="1",
            align="stretch",
            justify="between"
        ),
        rx.divider(),
        # Options list
        rx.vstack(
            rx.input(
                on_change=lambda o: OptionSearchState.on_search(key, o, options[key]),
                radius="small",
                width="90%",
            ),
            rx.cond(
                OptionSearchState.key == key,
                rx.foreach(
                    OptionSearchState.search_matched_options,
                    lambda option: create_checkbox_item(option)
                ),
                rx.foreach(
                    options[key],
                    lambda option: create_checkbox_item(option)
                ),
            ),
            spacing="1",
            width="100%",
            max_height="400px",
            overflow_y="auto",
            padding="4px"
        ),
        spacing="2",
        padding="8px",
        min_width="150px",
    )

    # Filter indicator for compact mode
    filter_indicator = rx.cond(
            deselected[key].length() != 0,
            rx.badge(
                options[key].length() - deselected[key].length(),
                color_scheme="blue",
                size="1",
                margin_left="2"
            )
        )

    button_content = rx.hstack(
        rx.text(label, font_weight="semibold"),
        filter_indicator,
        rx.icon(
            "filter",
            size=12,
            color=rx.cond(deselected[key].length() != 0, "blue.500", "gray.500")
        ),
        rx.cond(
            sorted_keys.contains(key),
            rx.cond(
                sorted_keys[key],
                rx.icon("arrow-up", size=14),
                rx.icon("arrow-down", size=14)
            )
        ),
        align="center",
        spacing="1",
        justify="start",
        width="100%"
    )

    return rx.menu.root(
        rx.menu.trigger(
            rx.button(
                button_content,
                width="100%",
                variant="ghost",
                bg="transparent",
                color="black",
                font_weight="bold",
                height="auto",
                _hover={"bg": "gray.50", "border_color": "transparent"}
            ),
            width="100%"
        ),
        rx.menu.content(
            menu_content
        ),
        width="100%",
    )


def column_header(
        key: str,
        sorted_keys: dict[str, bool],
        cycle_sort_by: rx.EventHandler,
        label: str = ""
) -> rx.Component:

    return rx.menu.root(
        rx.menu.trigger(
            rx.button(
                rx.hstack(
                    label,
                    rx.cond(
                        sorted_keys.contains(key),
                        rx.cond(
                            sorted_keys[key],
                            rx.icon("arrow-up", size=14),
                            rx.icon("arrow-down", size=14)
                        )
                    ),
                    align="center",
                    spacing="1"
                ),
                width="100%",
                variant="ghost",
                bg="transparent",
                color="black",
                font_weight="bold",
                height="auto",
                _hover={"bg": "gray.50", "border_color": "transparent"}
            ),
            width="100%"
        ),
        rx.menu.content(
            sort_button(key, cycle_sort_by),
            align="center"
        ),
        width="100%",
    )
