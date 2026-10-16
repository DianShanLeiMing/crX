"""Shop daily free offer claim job.

Navigate to the Shop tab, scroll to find the free daily offer icon, click it,
confirm the collect, dismiss, then return to main.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from crX.bot.coords import (
    CONFIRM_COLLECT_DAILY_FREE_OFFER_BUTTON_COORDS,
    CONFIRM_FREE_REWARD_PURCHASE_1,
    PAGINATE_SHOP_PAGE_BUTTON,
    SHOP_PAGE_DEADSPACE_COORD,
)
from crX.bot.find import locate_free_shop_offer_icon
from crX.bot.nav import (
    PAGE_MAIN,
    PAGE_SHOP,
    navigate_main_page,
)
from crX.bot.state_detect import (
    check_for_free_offer_confirmation_condition_1,
    check_if_on_clash_main_menu,
    check_if_on_shop,
)

if TYPE_CHECKING:
    from crX.utils.logger import Logger

MAX_PAGINATE_ITERATIONS = 8


def _paginate_shop(emulator) -> None:
    emulator.click(*PAGINATE_SHOP_PAGE_BUTTON)
    time.sleep(1)


def shop_daily_state(emulator, logger: Logger) -> bool:
    logger.change_status("正在领取商店每日免费商品……")

    if not check_if_on_clash_main_menu(emulator):
        logger.change_status("当前不在主界面，无法领取商店每日免费商品")
        return False

    if not navigate_main_page(emulator, logger, PAGE_MAIN, PAGE_SHOP):
        logger.change_status("进入商店页面失败")
        return False

    if not check_if_on_shop(emulator):
        logger.change_status("未进入商店页面")
        return False

    found = None
    for _ in range(MAX_PAGINATE_ITERATIONS):
        coord = locate_free_shop_offer_icon(emulator)
        if coord is not None:
            found = coord
            break
        _paginate_shop(emulator)

    if found is None:
        logger.change_status("未找到商店每日免费商品，正在返回主界面")
        return navigate_main_page(emulator, logger, PAGE_SHOP, PAGE_MAIN)

    logger.change_status(f"已在 {found} 找到每日免费商品，正在领取……")
    emulator.click(*found)
    time.sleep(2)

    # The offer click usually opens a confirmation popup ("Get Gems? FREE!").
    # When it does, click its FREE! button; otherwise fall back to the default coord.
    if check_for_free_offer_confirmation_condition_1(emulator):
        logger.change_status("已检测到免费领取确认弹窗，正在确认……")
        emulator.click(*CONFIRM_FREE_REWARD_PURCHASE_1)
    else:
        emulator.click(*CONFIRM_COLLECT_DAILY_FREE_OFFER_BUTTON_COORDS)
    time.sleep(2)

    logger.add_shop_offer_collection()
    emulator.click(*SHOP_PAGE_DEADSPACE_COORD)
    time.sleep(1)

    return navigate_main_page(emulator, logger, PAGE_SHOP, PAGE_MAIN)
