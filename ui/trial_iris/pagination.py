from typing import ClassVar

import reflex as rx


class PaginationState(rx.ComponentState):
    current_page: int = 0
    show_page_input: bool = False
    page_input_value: str = ""
    set_items_per_page: ClassVar[rx.EventHandler] = None
    go_to_page: ClassVar[rx.EventHandler] = None

    def calculate_visible_pages(self) -> list[int | str]:
        visible = {1, self.total_pages}  # Always show first and last

        # Add pages around current
        for i in range(max(2, self.current_page - 7),
                       min(self.current_page + 8, self.total_pages)):
            visible.add(i)

        # Convert to sorted list and add ellipsis
        result = []
        last = 0
        for page in sorted(visible):
            if last and page > last + 1:
                result.append('...')
            result.append(page)
            last = page
        return result

    @rx.event
    def on_page_input_key_down(self, key: str):
        if key == "Enter":
            try:
                return self.__class__.go_to_page(int(self.page_input_value) - 1)
            except ValueError:
                # do nothing, it is ok
                pass
        return None

    @rx.event
    def on_items_per_page_change(self, value: str):
        try:
            return self.__class__.set_items_per_page(int(value))
        except ValueError:
            # do nothing
            return None

    @classmethod
    def get_component(cls,
                      current_page: int,
                      total_pages: int,
                      items_per_page: int,
                      set_items_per_page: rx.EventHandler,
                      go_to_page: rx.EventHandler,
                      prev_page: rx.EventHandler,
                      next_page: rx.EventHandler,
                      **props) -> rx.Component:
        cls.current_page = current_page
        cls.go_to_page = go_to_page
        cls.set_items_per_page = set_items_per_page
        return rx.hstack(
            rx.text("Items per page: ", size="2"),
            rx.input(
                value=items_per_page,
                on_change=cls.on_items_per_page_change,
                width="40px"
            ),
            rx.button(
                rx.icon("chevrons-left", size=20),
                on_click=go_to_page(0),
                variant="ghost",
                paddingLeft="0px",
                paddingRight="0px"
            ),
            rx.button(
                rx.icon("chevron-left", size=20),
                on_click=prev_page,
                variant="ghost",
                paddingLeft="0px",
                paddingRight="0px"
            ),
            rx.text(f"page", size="2"),
            rx.cond(
                cls.show_page_input,
                rx.input(
                    placeholder=(current_page + 1).to_string(),
                    on_change=cls.set_page_input_value,
                    on_key_down=cls.on_page_input_key_down,
                    on_blur=[lambda s: cls.set_show_page_input(False)],
                    width="40px"
                ),
                rx.button(
                    current_page + 1,
                    on_click=cls.set_show_page_input(True),
                    color="black",
                    font_weight="normal",
                    width="40px",
                    variant="outline",
                    paddingLeft="0px",
                    paddingRight="0px"
                )
            ),
            rx.text(f" / {total_pages}", size="2"),
            rx.button(
                rx.icon("chevron-right", size=20),
                on_click=next_page,
                variant="ghost",
                paddingLeft="0px",
                paddingRight="0px",
            ),
            rx.button(
                rx.icon("chevrons-right", size=20),
                on_click=go_to_page(total_pages - 1),
                variant="ghost",
                paddingLeft="0px",
                paddingRight="0px",
            ),
            justify="between",
            align="center",
        )


pagination = PaginationState.create