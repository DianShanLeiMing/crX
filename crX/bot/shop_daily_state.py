"""Claim the Chinese daily free offer only after verifying each screen."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from crX.bot.find import (
    find_collected_shop_offer,
    find_free_shop_confirmation,
    find_shop_offer_dialog_close,
    locate_free_shop_offer_icon,
)
from crX.bot.nav import PAGE_MAIN, PAGE_SHOP, align_daily_shop_offer, navigate_daily_shop, navigate_main_page
from crX.bot.state_detect import check_if_on_clash_main_menu, check_if_on_shop

if TYPE_CHECKING:
    from crX.utils.logger import Logger


def _return_to_main(emulator, logger: Logger) -> bool:
    if check_if_on_clash_main_menu(emulator):
        return True
    if not check_if_on_shop(emulator):
        close = find_shop_offer_dialog_close(emulator.screenshot())
        if close is None:
            return False
        emulator.click(*close)
        time.sleep(0.6)
    return navigate_main_page(emulator, logger, PAGE_SHOP, PAGE_MAIN)


def shop_daily_state(emulator, logger: Logger) -> bool:
    logger.change_status("正在检查商店每日免费商品……")
    if check_if_on_clash_main_menu(emulator):
        if not navigate_main_page(emulator, logger, PAGE_MAIN, PAGE_SHOP):
            logger.change_status("进入商店页面失败")
            return False
    elif not check_if_on_shop(emulator):
        logger.change_status("当前不在主界面或商店，无法检查免费商品")
        return False

    if not navigate_daily_shop(emulator, logger):
        _return_to_main(emulator, logger)
        return False
    if find_collected_shop_offer(emulator.screenshot()) is not None:
        logger.change_status("商店每日免费商品已收集，本次无需重复领取")
        return _return_to_main(emulator, logger)

    offer = locate_free_shop_offer_icon(emulator)
    if offer is None:
        logger.change_status("已找到每日精选，但未能确认免费或已收集状态，未完成本次检查")
        _return_to_main(emulator, logger)
        return False

    emulator.click(*offer)
    for _ in range(12):
        time.sleep(0.4)
        confirmation = find_free_shop_confirmation(emulator.screenshot())
        if confirmation is not None:
            break
    else:
        logger.change_status("未检测到明确的免费领取确认按钮，已取消本次领取")
        _return_to_main(emulator, logger)
        return False

    logger.change_status("已确认商品免费，正在领取……")
    emulator.click(*confirmation)
    for _ in range(16):
        time.sleep(0.5)
        # The shop can reflow after collection; realign before reading the collected marker.
        if check_if_on_shop(emulator) and align_daily_shop_offer(emulator):
            if find_collected_shop_offer(emulator.screenshot()) is not None:
                logger.add_shop_offer_collection()
                logger.change_status("已验证商店免费商品领取成功")
                return _return_to_main(emulator, logger)

    logger.change_status("未能验证免费商品领取成功，本次不增加领取次数")
    _return_to_main(emulator, logger)
    return False
