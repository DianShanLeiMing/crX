import random
import time
from typing import Literal

from crX.bot.coords import (
    BATTLE_LOG_BUTTON,
    BATTLE_MODE_SCROLL_COORDS,
    BATTLE_MODE_SELECTOR_COORD,
    BATTLE_WAIT_DEADSPACE_COORD,
    BOTTOM_NAV_BATTLE_TAB_COORD,
    BOTTOM_NAV_CARD_TAB_FROM_MAIN_COORD,
    BOTTOM_NAV_MAIN_TAB_FROM_CARD_COORD,
    BOTTOM_NAV_MAIN_TAB_FROM_SHOP_COORD,
    BOTTOM_NAV_SHOP_TAB_COORD,
    CARD_PAGE_EXIT_BUTTON_COORDS,
    CARD_PAGE_ICON_FROM_CARD_PAGE,
    CARD_PAGE_ICON_FROM_CLASH_MAIN,
    CLAN_VOYAGE_CLOSE_BUTTON_COORDS,
    CLASH_MAIN_OPTIONS_BURGER_BUTTON,
    DECK_TABS_REGION,
    DECKS_PAGE_BUTTON_COORDS,
    OK_BUTTON_COORDS_IN_TROPHY_REWARD_PAGE,
    REWARD_LEFT_CHOICE_COORD,
    REWARD_RIGHT_CHOICE_COORD,
)
from crX.bot.coords import CLASH_MAIN_DEADSPACE_COORD as CLASH_MAIN_MENU_DEADSPACE_COORD
from crX.bot.find import find_fight_mode_icon, find_post_battle_button
from crX.bot.state_detect import (
    check_for_reward_choice_screen,
    check_for_trophy_reward_menu,
    check_if_in_battle,
    check_if_on_battle_log_page,
    check_if_on_card_page,
    check_if_on_clan_voyage,
    check_if_on_clash_main_burger_button_options_menu,
    check_if_on_clash_main_menu,
    check_if_on_shop,
)
from crX.detection.image_rec import find_image
from crX.interface.enums import BATTLE_MODE_LABELS
from crX.utils.logger import Logger

CLASH_MAIN_WAIT_TIMEOUT = 240


def wait_for_battle_start(emulator, logger, timeout: int = 120) -> bool:
    """Waits for any battle to start (1v1 or 2v2).

    Args:
    ----
        emulator: The emulator controller.
        logger: The logger object.
        timeout: Maximum time to wait in seconds

    Returns:
    -------
        bool: True if battle started, False if timed out.
    """
    start_time = time.time()
    last_logged_second = -1
    while time.time() - start_time < timeout:
        elapsed_second = int(time.time() - start_time)
        if elapsed_second != last_logged_second:
            logger.change_status(status=f"正在等待对战开始，已等待 {elapsed_second} 秒")
            last_logged_second = elapsed_second
        battle_result = check_if_in_battle(emulator)
        if battle_result:
            logger.change_status("已检测到对战")
            return True
        emulator.click(x_coord=BATTLE_WAIT_DEADSPACE_COORD[0], y_coord=BATTLE_WAIT_DEADSPACE_COORD[1])
    return False


def check_for_in_battle_with_delay(emulator) -> bool:
    """Checks if the virtual machine is in any battle with a delay.

    Args:
    ----
        emulator: The emulator controller.

    Returns:
    -------
        bool: True if the virtual machine is in any battle, False otherwise.

    """
    timeout = 3
    start_time = time.time()
    while time.time() - start_time < timeout:
        battle_result = check_if_in_battle(emulator)
        if battle_result:
            return True
        time.sleep(0.1)
    return False


def handle_trophy_reward_menu(emulator, logger: Logger, printmode=False) -> Literal["good"]:
    if printmode:
        logger.change_status(status="正在处理奖杯奖励弹窗")
    else:
        logger.log("Handling trophy reward menu")
    emulator.click(OK_BUTTON_COORDS_IN_TROPHY_REWARD_PAGE[0], OK_BUTTON_COORDS_IN_TROPHY_REWARD_PAGE[1])
    time.sleep(1)
    return "good"


def handle_reward_choice(emulator, logger: Logger) -> None:
    """Pick a reward on the post-battle 'Choose your reward' screen."""
    logger.change_status(status="正在处理奖励选择页面")
    emulator.click(*REWARD_LEFT_CHOICE_COORD)
    time.sleep(0.5)
    emulator.click(*REWARD_RIGHT_CHOICE_COORD)
    time.sleep(0.5)


def handle_clan_voyage_page(emulator) -> None:
    """Dismiss the clan voyage popup by clicking its close button."""
    emulator.click(*CLAN_VOYAGE_CLOSE_BUTTON_COORDS)


def wait_for_clash_main_menu(emulator, logger: Logger, deadspace_click=True, timeout: float | None = None) -> bool:
    """Waits for the user to be on the clash main menu.
    Returns True if on main menu, prints the pixels if False then return False
    """
    wait_timeout = CLASH_MAIN_WAIT_TIMEOUT if timeout is None else timeout
    start_time: float = time.time()
    while check_if_on_clash_main_menu(emulator) is not True:
        if time.time() - start_time > wait_timeout:
            logger.change_status("等待主界面超时")
            break
        if check_for_trophy_reward_menu(emulator):
            print("Handling trophy reward menu")
            handle_trophy_reward_menu(emulator, logger)
            time.sleep(2)
            continue
        if check_if_on_clan_voyage(emulator):
            logger.change_status("正在关闭部落旅途弹窗")
            handle_clan_voyage_page(emulator)
            time.sleep(2)
            continue
        if deadspace_click and random.randint(0, 1) == 0:
            emulator.click(CLASH_MAIN_MENU_DEADSPACE_COORD[0], CLASH_MAIN_MENU_DEADSPACE_COORD[1])
        time.sleep(1)
    time.sleep(1)
    if check_if_on_clash_main_menu(emulator) is not True:
        print("Failed to get to clash main! Saw these pixels before restarting:")
        return False
    return True


def get_to_card_page_from_clash_main(emulator, logger: Logger) -> Literal["restart", "good"]:
    if not check_if_on_clash_main_menu(emulator):
        return "restart"
    start_time = time.time()
    logger.change_status(status="正在从主界面打开卡牌页")
    emulator.click(CARD_PAGE_ICON_FROM_CLASH_MAIN[0], CARD_PAGE_ICON_FROM_CLASH_MAIN[1])
    time.sleep(2.5)
    while not check_if_on_card_page(emulator):
        time_taken = time.time() - start_time
        if time_taken > 30:
            return "restart"
        emulator.click(CARD_PAGE_ICON_FROM_CARD_PAGE[0], CARD_PAGE_ICON_FROM_CARD_PAGE[1])
        time.sleep(3)
    logger.change_status(status="已进入卡牌页")
    return "good"


def return_to_clash_main_from_card_page(emulator, logger: Logger) -> bool:
    """Click the card page exit button and verify the bot is on the main menu."""
    logger.change_status("正在返回主界面...")
    emulator.click(*CARD_PAGE_EXIT_BUTTON_COORDS)
    time.sleep(1)
    if not check_if_on_clash_main_menu(emulator):
        logger.change_status("从卡牌页返回主界面失败")
        return False
    return True


def open_clash_main_options_menu(emulator, logger: Logger, printmode: bool = False) -> bool:
    """Open the burger menu from the Clash main screen. Returns False if not on main or menu never opens."""
    if check_if_on_clash_main_menu(emulator) is not True:
        logger.change_status(status="当前不在主界面")
        return False
    if printmode:
        logger.change_status(status="正在打开主界面菜单")
    else:
        logger.log("正在打开主界面菜单")
    emulator.click(CLASH_MAIN_OPTIONS_BURGER_BUTTON[0], CLASH_MAIN_OPTIONS_BURGER_BUTTON[1])
    if wait_for_clash_main_burger_button_options_menu(emulator, logger, printmode) == "restart":
        logger.change_status(status="等待主界面菜单超时")
        return False
    return True


def get_to_activity_log(emulator, logger: Logger, printmode: bool = False) -> Literal["restart", "good"]:
    if printmode:
        logger.change_status(status="正在打开对战日志")
    else:
        logger.log("正在打开对战日志")
    if not open_clash_main_options_menu(emulator, logger, printmode):
        return "restart"
    if printmode:
        logger.change_status(status="正在点击对战日志按钮")
    else:
        logger.log("正在点击对战日志按钮")
    emulator.click(BATTLE_LOG_BUTTON[0], BATTLE_LOG_BUTTON[1])
    if wait_for_battle_log_page(emulator, logger, printmode) == "restart":
        logger.change_status(status="等待对战日志页面超时")
        return "restart"
    return "good"


def wait_for_battle_log_page(emulator, logger: Logger, printmode=False) -> Literal["restart", "good"]:
    start_time = time.time()
    if printmode:
        logger.change_status(status="正在等待对战日志页面显示")
    else:
        logger.log("正在等待对战日志页面显示")
    while not check_if_on_battle_log_page(emulator):
        time_taken = time.time() - start_time
        if time_taken > 20:
            logger.change_status(status="等待对战日志页面超时")
            return "restart"
    if printmode:
        logger.change_status(status="对战日志页面已显示")
    else:
        logger.log("对战日志页面已显示")
    return "good"


def wait_for_clash_main_burger_button_options_menu(
    emulator, logger: Logger, printmode: bool = False
) -> Literal["restart", "good"]:
    """Waits for the virtual machine to be on the clash main burger button options menu.

    Args:
    ----
        emulator (int): The index of the virtual machine.
        logger (Logger): The logger object to use for logging.
        printmode (bool, optional): Whether to print status messages. Defaults to False.

    Returns:
    -------
        Literal["restart", "good"]: "restart" if the function timed
        out and needs to be restarted, "good" otherwise.

    """
    start_time = time.time()
    if printmode:
        logger.change_status(status="正在等待主界面菜单显示")
    else:
        logger.log("正在等待主界面菜单显示")
    while not check_if_on_clash_main_burger_button_options_menu(emulator):
        time_taken = time.time() - start_time
        if time_taken > 20:
            logger.change_status(status="等待主界面菜单超时")
            return "restart"
    if printmode:
        logger.change_status(status="主界面菜单已打开")
    else:
        logger.log("主界面菜单已打开")
    return "good"


def select_mode(emulator, mode: str, logger: Logger | None = None):
    expected_mode_types = ["Classic 1v1", "Classic 2v2", "Trophy Road"]
    if type(mode) is not str:
        msg = f"对战模式参数类型无效：{mode}，应为字符串。"
        if logger is not None:
            logger.change_status(msg)
        else:
            print(msg)
        return False
    if mode not in expected_mode_types:
        msg = f"不支持的对战模式：{mode}，请选择经典 1v1、经典 2v2 或皇室征程。"
        if logger is not None:
            logger.change_status(msg)
        else:
            print(msg)
        return False
    if not check_if_on_clash_main_menu(emulator):
        msg = "当前不在主界面，无法选择对战模式"
        if logger is not None:
            logger.change_status(msg)
        else:
            print(f"[!] {msg}")
        return False
    if logger is not None:
        logger.change_status(f"正在选择 {BATTLE_MODE_LABELS.get(mode, mode)}……")
    emulator.click(*BATTLE_MODE_SELECTOR_COORD)
    time.sleep(2)

    def scroll_down_in_fight_mode_panel(emulator):
        emulator.swipe(*BATTLE_MODE_SCROLL_COORDS)
        time.sleep(1)

    search_timeout = 15
    start_time = time.time()
    while time.time() - start_time < search_timeout:
        coord = find_fight_mode_icon(emulator, mode)
        if coord is not None:
            emulator.click(*coord)
            time.sleep(3)
            try:
                emulator.click(CLASH_MAIN_MENU_DEADSPACE_COORD[0], CLASH_MAIN_MENU_DEADSPACE_COORD[1])
            except Exception:
                pass
            if logger is not None:
                logger.change_status(f"已选择 {BATTLE_MODE_LABELS.get(mode, mode)}")
            return True
        scroll_down_in_fight_mode_panel(emulator)
    if logger is not None:
        logger.change_status(f"在模式面板中未找到 {BATTLE_MODE_LABELS.get(mode, mode)}")
    return False


PAGE_MAIN = "main"
PAGE_CARD = "card_page"
PAGE_SHOP = "shop"
MAIN_PAGE_CHECKS = {
    PAGE_MAIN: check_if_on_clash_main_menu,
    PAGE_CARD: check_if_on_card_page,
    PAGE_SHOP: check_if_on_shop,
}
NAV_CLICKS: dict[tuple[str, str], list[tuple[int, int]]] = {
    (PAGE_MAIN, PAGE_CARD): [BOTTOM_NAV_CARD_TAB_FROM_MAIN_COORD],
    (PAGE_MAIN, PAGE_SHOP): [BOTTOM_NAV_SHOP_TAB_COORD],
    (PAGE_CARD, PAGE_MAIN): [BOTTOM_NAV_MAIN_TAB_FROM_CARD_COORD],
    (PAGE_CARD, PAGE_SHOP): [BOTTOM_NAV_SHOP_TAB_COORD],
    (PAGE_SHOP, PAGE_MAIN): [BOTTOM_NAV_MAIN_TAB_FROM_SHOP_COORD],
    (PAGE_SHOP, PAGE_CARD): [BOTTOM_NAV_BATTLE_TAB_COORD],
}


def navigate_main_page(emulator, logger: Logger, start_page: str, end_page: str) -> bool:
    """Navigate from start_page to end_page using the recorded click sequence.

    Caller must ensure the bot is on start_page before calling. Sleeps 2 s after
    every click (between clicks AND between the last click and the destination
    check). Returns True iff check_if_on_<end_page>(emulator) is True.
    """
    if not MAIN_PAGE_CHECKS[start_page](emulator):
        return False
    if start_page == end_page:
        return MAIN_PAGE_CHECKS[end_page](emulator)
    clicks = NAV_CLICKS.get((start_page, end_page))
    if clicks is None:
        raise ValueError(f"no recorded navigation for {start_page!r} -> {end_page!r}")
    logger.log(f"navigate_main_page: {start_page} -> {end_page} via {len(clicks)} click(s)")
    for i, (x, y) in enumerate(clicks, 1):
        logger.log(f"  step {i}/{len(clicks)}: clicking ({x}, {y})")
        emulator.click(x, y)
        time.sleep(2)
    arrived = MAIN_PAGE_CHECKS[end_page](emulator)
    logger.log(f"  arrival check: on {end_page}? {arrived}")
    return arrived


def _navigate_to_deck_selection(emulator, logger: Logger) -> bool:
    logger.change_status("正在打开卡组选择页……")
    if get_to_card_page_from_clash_main(emulator, logger) == "restart":
        logger.change_status("从主界面进入卡牌页失败。")
        return False
    emulator.click(*DECKS_PAGE_BUTTON_COORDS)
    time.sleep(0.1)
    return True


def switch_deck_page(emulator, logger: Logger) -> bool:
    logger.change_status("正在切换卡组页……")
    switch_button_coord = find_image(
        emulator.screenshot(), "deck_tabs/switch_deck", subcrop=DECK_TABS_REGION, tolerance=0.98
    )
    if switch_button_coord is not None:
        emulator.click(*switch_button_coord)
        time.sleep(1)
        return True
    logger.change_status("未找到卡组翻页按钮。")
    return False


def get_to_main_after_fight(emulator, logger):
    timeout = 120
    start_time = time.time()
    clicked_ok_or_exit = False
    logger.change_status("对战结束，正在返回主界面……")
    while time.time() - start_time < timeout:
        if check_if_on_clash_main_menu(emulator) is True:
            time.sleep(3)
            if check_for_trophy_reward_menu(emulator):
                handle_trophy_reward_menu(emulator, logger, printmode=True)
                time.sleep(2)
            logger.change_status("对战结束后已返回主界面")
            return True
        if check_for_trophy_reward_menu(emulator):
            handle_trophy_reward_menu(emulator, logger, printmode=True)
            time.sleep(3)
            continue
        if check_for_reward_choice_screen(emulator):
            handle_reward_choice(emulator, logger)
            time.sleep(3)
            continue
        if not clicked_ok_or_exit:
            button_coord = find_post_battle_button(emulator)
            if button_coord is not None:
                logger.change_status("正在点击战后确认按钮")
                emulator.click(button_coord[0], button_coord[1])
                clicked_ok_or_exit = True
                continue
        time.sleep(1)
        emulator.click(CLASH_MAIN_MENU_DEADSPACE_COORD[0], CLASH_MAIN_MENU_DEADSPACE_COORD[1])
    return False


if __name__ == "__main__":
    pass
