"""Select existing decks from an explicitly ordered list of slots."""

import time

from crX.bot.coords import DECK_TAB_CLICK_OFFSET, DECK_TABS_REGION
from crX.bot.nav import _navigate_to_deck_selection, return_to_clash_main_from_card_page, switch_deck_page
from crX.bot.state_detect import check_if_on_clash_main_menu, is_deck_full, is_single_deck_layout_by_pixel
from crX.detection.image_rec import find_image
from crX.interface.config import selected_decks_from_settings
from crX.interface.enums import UIField
from crX.utils.logger import Logger


def select_deck_state(emulator, logger: Logger, deck_order: list[int]) -> tuple[bool, int | None]:
    deck_order = selected_decks_from_settings({UIField.SELECTED_DECKS: deck_order})
    if not deck_order:
        logger.change_status("未勾选任何卡组，无法选择卡组")
        return False, None
    if not check_if_on_clash_main_menu(emulator):
        logger.change_status("当前不在主界面，无法选择卡组")
        return False, None
    logger.change_status(f"正在按顺序查找勾选的卡组：{' → '.join(map(str, deck_order))}")
    success, selected_deck_number = select_deck(emulator, logger, deck_order)
    if not success:
        logger.change_status("卡组选择失败")
        return False, None
    return True, selected_deck_number


def find_and_click_deck(emulator, logger: Logger, deck_order: list[int]) -> tuple[bool, int | None]:
    """Try only checked slots; unavailable or incomplete decks are skipped."""
    if is_single_deck_layout_by_pixel(emulator):
        if 1 in deck_order and is_deck_full(emulator):
            logger.change_status("当前为单卡组布局，使用已勾选的卡组 #1。")
            return True, 1
        logger.change_status("当前为单卡组布局，卡组 #1 未勾选或未满编。")
        return False, None

    for number in deck_order:
        folder = f"deck_tabs/deck_{number}"
        image = emulator.screenshot()
        coords = find_image(image, folder, subcrop=DECK_TABS_REGION, tolerance=0.98)
        # Locate the requested slot directly instead of guessing the current page.
        # A candidate needs at most one flip, including when the initial page is 2.
        if (
            coords is None
            and find_image(image, "deck_tabs/switch_deck", subcrop=DECK_TABS_REGION, tolerance=0.98) is not None
        ):
            if not switch_deck_page(emulator, logger):
                return False, None
            coords = find_image(emulator.screenshot(), folder, subcrop=DECK_TABS_REGION, tolerance=0.98)
        if coords is None:
            logger.change_status(f"未找到勾选的卡组 #{number}，已跳过。")
            continue
        emulator.click(coords[0] + DECK_TAB_CLICK_OFFSET[0], coords[1] + DECK_TAB_CLICK_OFFSET[1])
        time.sleep(1)
        if is_deck_full(emulator):
            logger.change_status(f"已选择满编卡组：#{number}。")
            return True, number
        logger.change_status(f"勾选的卡组 #{number} 未满编，已跳过，请在游戏中补齐。")

    logger.change_status("勾选范围内未找到可用的满编卡组。")
    return False, None


def select_deck(emulator, logger: Logger, deck_order: list[int]) -> tuple[bool, int | None]:
    start_time = time.monotonic()
    if not _navigate_to_deck_selection(emulator, logger):
        return False, None
    success, selected_deck_number = find_and_click_deck(emulator, logger, deck_order)
    returned = return_to_clash_main_from_card_page(emulator, logger)
    if not success or not returned:
        return False, None
    logger.change_status(f"已选择卡组 {selected_deck_number}，耗时 {time.monotonic() - start_time:.1f} 秒")
    return True, selected_deck_number
