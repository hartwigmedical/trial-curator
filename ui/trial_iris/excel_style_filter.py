import reflex as rx

class ExcelStyleFilterState(rx.State):
    """ComponentState for individual Excel-style filter instances."""

    options: list[str] = []
    selected: set[str] = set()
    label: str = ""

    on_change: rx.EventHandler = None

    def __init__(self, *args, **kwargs):
        print(f"constructing instance, kwargs: {kwargs}")
        super().__init__(*args, **kwargs)

    @rx.event
    def initialize(self, options: list[str], label: str):
        """Initialize the filter with options and settings."""
        self.options.extend(options)
        self.label = label
        self.selected.update(options)  # Start with all selected

    @rx.event
    def toggle_option(self, option: str):
        """Toggle a specific option on/off."""
        #print(type(self))
        print("toggled option: ", option, " self id: ", id(self), " on change: ", self.__class__.on_change)
        if option in self.selected:
            self.selected.discard(option)
        else:
            self.selected.add(option)
        if self.__class__.on_change is not None:
            print("triggering on change")
            # returning this will let the handler get triggered by the system
            return self.__class__.on_change(self.get_selected_list)

    @rx.event
    def select_all(self):
        """Select all options."""
        self.selected = set(self.options)

    @rx.event
    def clear_all(self):
        """Clear all selections."""
        self.selected = set()

    @rx.var
    def get_selected_list(self) -> list[str]:
        """Get sorted list of selected options."""
        return sorted(list(self.selected))

    @rx.var
    def selected_count(self) -> int:
        """Get count of selected items."""
        return len(self.selected)

    @rx.var
    def total_count(self) -> int:
        """Get total count of options."""
        return len(self.options)

    @rx.var
    def is_filtered(self) -> bool:
        """Check if filter is active (not all options selected)."""
        return len(self.selected) < len(self.options)

def excel_style_filter(
        key: str,
        options: dict[str, list[str]],
        selected: dict[str, list[str]],
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
    def create_checkbox_item(option: str):
        return rx.hstack(
            rx.checkbox(
                checked=selected[key].contains(option),
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
            selected[key].length() != options[key].length(),
            rx.badge(
                selected[key].length(),
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
            rx.text(label, font_weight="semibold"),
            filter_indicator,
            rx.icon(
                "filter",
                size=12,
                color=rx.cond(selected[key].length() != options[key].length(), "blue.500", "gray.500")
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
