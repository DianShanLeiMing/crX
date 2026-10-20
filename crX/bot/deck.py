"""Deck selection, cycling, and randomization for crX.

Consolidates the former deck_utils.py, deck_cycle.py, and deck_randomization.py.
"""

import time

from crX.bot.coords import (
    CONFIRM_RANDOMIZE_DECK_BUTTON_COORD,
    DECK_OPTIONS_BUTTON_COORDS,
    DECK_PAGE_OPTIONS_BUTTON_COORDS,
    DECK_TAB_CLICK_OFFSET,
    DECK_TABS_REGION,
    RANDOMIZE_DECK_BUTTON_COORD,
    RANDOMIZE_DECK_BUTTON_COORDS,
    RANDOMIZE_DECK_CONFIRM_BUTTON_COORDS,
)
from crX.bot.nav import (
    _navigate_to_deck_selection,
    get_to_card_page_from_clash_main,
    return_to_clash_main_from_card_page,
    switch_deck_page,
)
from crX.bot.state_detect import (
    check_if_on_card_page,
    check_if_on_clash_main_menu,
    check_if_on_classic_1v1_deck_page,
    check_if_on_classic_2v2_deck_page,
    check_if_on_confirm_randomize_deck_page,
    check_if_on_trophy_road_deck_page,
    is_deck_full,
    is_single_deck_layout_by_pixel,
)
from crX.detection.image_rec import find_image
from crX.utils.logger import Logger


def randomize_and_check_deck(emulator, logger: Logger, deck_to_randomize: int) -> bool:
    """
    Randomizes the selected deck and verifies that it becomes full.

    This function intelligently handles whether the deck is full or partial
    before randomizing, applying the correct click sequence for each case.
    """
    logger.change_status(f"正在随机更换卡组 #{deck_to_randomize}……")

    deck_is_full_before_randomize = is_deck_full(emulator)

    emulator.click(*DECK_OPTIONS_BUTTON_COORDS)
    time.sleep(0.1)

    emulator.click(*RANDOMIZE_DECK_BUTTON_COORDS)
    time.sleep(0.1)

    if deck_is_full_before_randomize:
        emulator.click(*RANDOMIZE_DECK_CONFIRM_BUTTON_COORDS)
        time.sleep(0.1)

    time.sleep(1.0)
    logger.add_card_randomization()

    if not is_deck_full(emulator):
        logger.change_status(f"随机更换后，卡组 {deck_to_randomize} 仍未满编。")
        return False

    logger.change_status(f"卡组 #{deck_to_randomize} 已随机更换。")
    return True


def select_deck_state(emulator, logger: Logger, deck_number, deck_count) -> tuple[bool, int | None]:
    if not check_if_on_clash_main_menu(emulator):
        logger.change_status("当前不在主界面，无法选择卡组")
        return False, None
    logger.change_status(f"正在选择卡组 #{deck_number}")
    success, selected_deck_number = select_deck(emulator, logger, deck_number, deck_count)
    if not success:
        logger.change_status("卡组选择失败")
        return False, None
    return True, selected_deck_number


def find_and_click_deck(emulator, logger: Logger, deck_number: int, deck_count: int) -> tuple[bool, int | None]:
    if is_single_deck_layout_by_pixel(emulator):
        logger.change_status("像素检测确认当前为单卡组布局。")
        if is_deck_full(emulator):
            logger.change_status("当前卡组已满编，将直接使用。")
            return True, 1
        else:
            logger.change_status("当前卡组未满编，正在随机更换。")
            if randomize_and_check_deck(emulator, logger, 1):
                return True, 1
            else:
                logger.error("单卡组随机更换失败。")
                return False, None

    logger.change_status("单卡组像素检测未通过，将按多卡组布局处理。")
    ss = emulator.screenshot()
    has_deck_page_2 = find_image(ss, "deck_tabs/switch_deck", subcrop=DECK_TABS_REGION, tolerance=0.98) is not None
    on_page_1 = find_image(ss, "deck_tabs/deck_1", subcrop=DECK_TABS_REGION, tolerance=0.98) is not None
    current_page = 1 if on_page_1 else 2

    deck_order_to_check = list(range(deck_number, deck_count + 1)) + list(range(1, deck_number))

    for deck_to_check in deck_order_to_check:
        if deck_to_check > 5 and not has_deck_page_2:
            logger.change_status(f"卡组 #{deck_to_check} 位于第二页，但未找到第二页，已跳过。")
            continue

        page_to_be_on = 1 if 1 <= deck_to_check <= 5 else 2

        if current_page != page_to_be_on:
            if not has_deck_page_2:
                break
            if not switch_deck_page(emulator, logger):
                return False, None
            current_page = page_to_be_on

        deck_image_folder = f"deck_tabs/deck_{deck_to_check}"
        deck_coords = find_image(emulator.screenshot(), deck_image_folder, subcrop=DECK_TABS_REGION, tolerance=0.98)

        if deck_coords is None:
            logger.change_status(f"未找到卡组 #{deck_to_check}，已跳过。")
            continue

        emulator.click(deck_coords[0] + DECK_TAB_CLICK_OFFSET[0], deck_coords[1] + DECK_TAB_CLICK_OFFSET[1])
        time.sleep(1)

        if is_deck_full(emulator):
            logger.change_status(f"已找到满编卡组：#{deck_to_check}。")
            return True, deck_to_check
        else:
            logger.change_status(f"卡组 #{deck_to_check} 未满编，正在随机更换。")
            if randomize_and_check_deck(emulator, logger, deck_to_check):
                return True, deck_to_check
            else:
                logger.change_status(f"卡组 #{deck_to_check} 随机更换失败，继续检查……")
                continue

    logger.error("检查所有可用页面后，仍未找到可用卡组。")
    return False, None


def select_deck(emulator, logger: Logger, deck_number: int, deck_count: int) -> tuple[bool, int | None]:
    start_time = time.time()
    if not _navigate_to_deck_selection(emulator, logger):
        return False, None

    success, selected_deck_number = find_and_click_deck(emulator, logger, deck_number, deck_count)
    if not success:
        logger.change_status("查找和选择卡组失败")
        return_to_clash_main_from_card_page(emulator, logger)
        return False, None

    if not return_to_clash_main_from_card_page(emulator, logger):
        return False, None

    logger.add_deck_cycled()
    logger.change_status(f"已选择卡组 {selected_deck_number}，耗时 {str(time.time() - start_time)[:4]} 秒")
    return True, selected_deck_number


def randomize_deck_state(emulator, logger: Logger, deck_number: int = 2):
    if not check_if_on_clash_main_menu(emulator):
        logger.change_status("当前不在主界面，无法随机更换卡组")
        return False

    logger.change_status(f"开始随机更换卡组 #{deck_number}")
    if not randomize_deck(emulator, logger, deck_number):
        logger.change_status("随机更换卡组失败")
        return False

    return True


def randomize_deck(emulator, logger: Logger, deck_number: int = 2) -> bool:
    """Navigate to the deck page, randomize the deck, and return to main.

    Trophy Road, Classic 1v1, and Classic 2v2 all share the identical randomize-button
    flow, so we just confirm we're on one of those deck pages and run it. There is no
    confirm dialog: deck-options -> randomize -> back to main. `deck_number` is accepted
    for the caller's signature but unused by this flow.
    """
    if get_to_card_page_from_clash_main(emulator, logger) == "restart":
        return False

    on_known_deck_page = (
        check_if_on_card_page(emulator)
        or check_if_on_trophy_road_deck_page(emulator)
        or check_if_on_classic_2v2_deck_page(emulator)
        or check_if_on_classic_1v1_deck_page(emulator)
    )
    if on_known_deck_page:
        if not is_single_deck_layout_by_pixel(emulator):
            tab = find_image(
                emulator.screenshot(), f"deck_tabs/deck_{deck_number}", subcrop=DECK_TABS_REGION, tolerance=0.98
            )
            if tab is None:
                logger.change_status(f"未找到卡组位置 {deck_number}")
                return_to_clash_main_from_card_page(emulator, logger)
                return False
            emulator.click(tab[0] + DECK_TAB_CLICK_OFFSET[0], tab[1] + DECK_TAB_CLICK_OFFSET[1])
            time.sleep(1)
        logger.change_status("正在随机更换卡组……")
        emulator.click(*DECK_PAGE_OPTIONS_BUTTON_COORDS)
        time.sleep(0.5)
        emulator.click(*RANDOMIZE_DECK_BUTTON_COORD)
        time.sleep(0.5)
        # Randomizing a full deck pops a "replace deck?" confirm dialog; accept it
        # when present. A partial deck randomizes instantly with no dialog.
        if check_if_on_confirm_randomize_deck_page(emulator):
            emulator.click(*CONFIRM_RANDOMIZE_DECK_BUTTON_COORD)
            time.sleep(0.5)
        logger.add_card_randomization()
    else:
        logger.change_status("无法识别当前页面，未检测到经典 1v1、2v2 或皇室征程卡组页")

    return return_to_clash_main_from_card_page(emulator, logger)
