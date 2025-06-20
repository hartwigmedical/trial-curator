from typing import Any

import reflex as rx

def excel_style_filter(
        key: str,
        thin: bool,
        options: dict[str, list[Any]],
        deselected: dict[str, list[Any]],
        toggle_option: rx.EventHandler,
        select_all: rx.EventHandler,
        clear_all: rx.EventHandler,
        label: str = "",
        classes: str = "",
        compact: bool = False,
) -> rx.Component:
    """
    Create an Excel-style filter component using ComponentState.

    Args:
        state:
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
                "Select All" if not compact else "All",
                on_click=select_all(key),
                size="1",
                variant="ghost",
                width="50%",
                _hover={"bg": "blue.50"}
            ),
            rx.button(
                "Clear All" if not compact else "None",
                on_click=clear_all(key),
                size="1",
                variant="ghost",
                width="50%",
                _hover={"bg": "red.50"}
            ),
            width="100%",
            spacing="1"
        ),
        rx.divider(),
        # Options list
        rx.vstack(
            rx.foreach(options[key], create_checkbox_item),
            spacing="1",
            width="100%",
            max_height="300px" if not compact else "200px",
            overflow_y="auto",
            padding="4px"
        ),
        spacing="2",
        padding="12px" if not compact else "8px",
        min_width="220px" if not compact else "150px",
    )

    # Filter indicator for compact mode
    filter_indicator = rx.cond(
        compact,
        rx.cond(
            deselected[key].length() != 0,
            rx.badge(
                options[key].length() - deselected[key].length(),
                color_scheme="blue",
                size="1",
                margin_left="2"
            ),
            rx.text("")  # No indicator when all selected
        ),
        rx.text("")
    )

    # Button content varies based on compact mode
    button_content = rx.cond(
        compact,
        # Compact mode for table headers
        rx.hstack(
            rx.cond(
                thin,
                rx.text(label, font_weight="semibold"),
                rx.text(label, font_weight="semibold")
            ),
            filter_indicator,
            rx.icon(
                "filter",
                size=12,
                color=rx.cond(deselected[key].length() != 0, "blue.500", "gray.500")
            ),
            align="center",
            spacing="1",
            justify="start",
            width="100%"
        ),
        # Regular mode
        rx.hstack(
            rx.text(label, font_weight="medium"),
            justify="between",
            align="center",
            width="100%"
        )
    )

    return rx.menu.root(
        rx.menu.trigger(
            rx.button(
                button_content,
                width="100%",
                variant="outline" if not compact else "ghost",
                bg="white" if not compact else "transparent",
                color="black",
                font_weight="bold",
                border_radius="md" if not compact else "sm",
                height="auto",
                _hover={"bg": "gray.50", "border_color": "gray.300" if not compact else "transparent"}
            ),
            width="100%"
        ),
        rx.menu.content(
            menu_content
        ),
        position="relative",
        width="100%",
        class_name=classes,
        # Click outside handler would need to be implemented at parent level
    )
