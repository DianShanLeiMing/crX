"""Native Chinese shop screenshots and the complete free-only claim sequence."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from crX.bot import nav, states
from crX.bot import shop_daily_state as shop
from crX.bot.coords import (
    BOTTOM_NAV_MAIN_TAB_FROM_SHOP_COORD,
    BOTTOM_NAV_SHOP_TAB_COORD,
    SHOP_CATALOG_SWIPE,
    SHOP_DAILY_ALIGN_SWIPE,
    SHOP_OFFERS_TAB_COORD,
    SHOP_RECOMMENDATIONS_TAB_COORD,
    SHOP_SWIPE_DURATION_MS,
)
from crX.bot.find import (
    find_collected_shop_offer,
    find_daily_shop_heading,
    find_free_shop_confirmation,
    find_shop_offer_dialog_close,
    locate_free_shop_offer_icon,
)
from crX.bot.state_detect import check_if_on_shop
from crX.emulators.adb import AdbController
from crX.interface.enums import UIField

FIXTURES = Path(__file__).parent / "fixtures"


def image(name):
    result = cv2.imread(str(FIXTURES / f"{name}.png"))
    assert result is not None
    return result


class Screen:
    def __init__(self, frame):
        self.frame = frame

    def screenshot(self):
        return self.frame


@pytest.mark.parametrize("name", ["shop-daily-free-zh", "shop-daily-free-low-zh"])
def test_native_daily_free_offer(name):
    frame = image(name)
    coord = locate_free_shop_offer_icon(Screen(frame))
    assert check_if_on_shop(Screen(frame))
    assert coord is not None and 30 < coord[0] < 90 and 435 < coord[1] < 546
    assert find_collected_shop_offer(frame) is None


@pytest.mark.parametrize(
    "name",
    ["shop-catalog-zh", "shop-daily-collected-zh", "shop-free-dialog-zh", "shop-paid-dialog-zh", "training-battle-zh"],
)
def test_other_screens_are_not_claimable(name):
    assert locate_free_shop_offer_icon(Screen(image(name))) is None


def test_daily_heading_and_undimmed_label_are_both_required():
    original = image("shop-daily-free-zh")
    no_heading = original.copy()
    no_heading[212:243, 129:232] = 0
    assert locate_free_shop_offer_icon(Screen(no_heading)) is None
    dimmed = (original.astype(float) * 0.35).astype(np.uint8)
    assert find_daily_shop_heading(dimmed) is not None
    assert locate_free_shop_offer_icon(Screen(dimmed)) is None


def test_only_free_dialog_can_be_confirmed():
    assert find_free_shop_confirmation(image("shop-free-dialog-zh")) == (180, 404)
    assert find_shop_offer_dialog_close(image("shop-paid-dialog-zh")) is not None
    for name in ("shop-paid-dialog-zh", "shop-daily-free-zh", "shop-daily-collected-zh", "training-start-zh"):
        assert find_free_shop_confirmation(image(name)) is None
    dimmed = (image("shop-free-dialog-zh").astype(float) * 0.35).astype(np.uint8)
    assert find_free_shop_confirmation(dimmed) is None


def test_collected_marker_is_explicit_and_must_be_visible():
    assert find_collected_shop_offer(image("shop-daily-collected-zh")) is not None
    for name in ("shop-daily-low-zh", "shop-claim-animation-zh", "shop-daily-free-zh"):
        assert find_collected_shop_offer(image(name)) is None


@pytest.mark.parametrize("frame", [None, np.zeros((1600, 900, 3), dtype=np.uint8)])
def test_wrong_geometry_is_rejected(frame):
    assert not check_if_on_shop(Screen(frame))
    assert locate_free_shop_offer_icon(Screen(frame)) is None
    assert find_free_shop_confirmation(frame) is None


class ShopDevice:
    """Transitions replay actual native screenshots; detections are not mocked."""

    def __init__(self, *, collected=False, paid=False, missing=False, claim_fails=False, return_fails=False):
        self.page = "main"
        self.collected = collected
        self.paid = paid
        self.missing = missing
        self.claim_fails = claim_fails
        self.return_fails = return_fails
        self.clicks = []
        self.swipes = []
        self.frames = {
            "catalog": image("shop-catalog-zh"),
            "free_low": image("shop-daily-free-low-zh"),
            "free": image("shop-daily-free-zh"),
            "dialog": image("shop-paid-dialog-zh" if paid else "shop-free-dialog-zh"),
            "collected_low": image("shop-daily-low-zh"),
            "collected": image("shop-daily-collected-zh"),
            "main": image("main-zh"),
        }

    def screenshot(self):
        return self.frames[self.page]

    def click(self, x, y):
        point = (x, y)
        self.clicks.append(point)
        if self.page == "main" and point == BOTTOM_NAV_SHOP_TAB_COORD:
            self.page = "catalog"
        elif point in (SHOP_OFFERS_TAB_COORD, SHOP_RECOMMENDATIONS_TAB_COORD):
            assert self.page == "catalog"
        elif point == BOTTOM_NAV_MAIN_TAB_FROM_SHOP_COORD:
            if not self.return_fails:
                self.page = "main"
        elif self.page == "free" and point == (50, 457):
            self.page = "dialog"
        elif self.page == "dialog":
            if point == (180, 404):
                assert not self.paid, "A paid purchase must never be confirmed"
                self.page = "free" if self.claim_fails else "collected_low"
            else:
                assert point == find_shop_offer_dialog_close(self.screenshot())
                self.page = "free"
        else:
            pytest.fail(f"unexpected click {point} on {self.page}")

    def swipe(self, *coords, duration_ms=None):
        assert duration_ms == SHOP_SWIPE_DURATION_MS
        self.swipes.append(coords)
        if self.page == "catalog" and not self.missing:
            assert coords == SHOP_CATALOG_SWIPE
            self.page = "collected_low" if self.collected else "free_low"
        elif self.page == "free_low":
            assert coords == SHOP_DAILY_ALIGN_SWIPE
            self.page = "free"
        elif self.page == "collected_low":
            assert coords == SHOP_DAILY_ALIGN_SWIPE
            self.page = "collected"


class ShopLogger:
    def __init__(self):
        self.collections = 0
        self.statuses = []

    def change_status(self, message):
        self.statuses.append(message)

    def log(self, message):
        pass

    def set_current_state(self, *_args, **_kwargs):
        pass

    def error(self, message):
        self.statuses.append(message)

    def add_shop_offer_collection(self):
        self.collections += 1


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setattr(shop.time, "sleep", lambda *_: None)
    monkeypatch.setattr(shop, "check_if_on_clash_main_menu", lambda device: device.page == "main")
    monkeypatch.setattr(nav, "check_if_on_clash_main_menu", lambda device: device.page == "main")
    monkeypatch.setitem(nav.MAIN_PAGE_CHECKS, nav.PAGE_MAIN, lambda device: device.page == "main")
    return ShopLogger()


def test_full_claim_then_recheck_does_not_count_twice(replay):
    device = ShopDevice()
    assert shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 1
    assert device.clicks.count((180, 404)) == 1
    assert device.swipes == [SHOP_CATALOG_SWIPE, SHOP_DAILY_ALIGN_SWIPE, SHOP_DAILY_ALIGN_SWIPE]
    device.collected = True
    assert shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 1
    assert device.clicks.count((180, 404)) == 1


def test_paid_dialog_is_closed_without_purchase_or_count(replay):
    device = ShopDevice(paid=True)
    assert not shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 0
    assert (180, 404) not in device.clicks


def test_unverified_claim_is_not_success(replay):
    device = ShopDevice(claim_fails=True)
    assert not shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 0
    assert device.clicks.count((180, 404)) == 1


def test_missing_daily_section_is_bounded_and_not_success(replay):
    device = ShopDevice(missing=True)
    assert not shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 0
    assert len(device.swipes) == 48


def test_failure_to_return_main_does_not_finish_the_job(replay):
    device = ShopDevice(collected=True, return_fails=True)
    assert not shop.shop_daily_state(device, replay)
    assert replay.collections == 0


def test_navigation_waits_for_arrival_animation_without_reclicking(replay, monkeypatch):
    device = ShopDevice()
    arrivals = iter([False, False, True])
    monkeypatch.setitem(nav.MAIN_PAGE_CHECKS, nav.PAGE_SHOP, lambda _: next(arrivals))
    assert nav.navigate_main_page(device, replay, nav.PAGE_MAIN, nav.PAGE_SHOP)
    assert device.clicks == [BOTTOM_NAV_SHOP_TAB_COORD]


def test_navigation_timeout_does_not_repeat_a_tab_click(replay, monkeypatch):
    device = ShopDevice()
    monkeypatch.setitem(nav.MAIN_PAGE_CHECKS, nav.PAGE_SHOP, lambda _: False)
    assert not nav.navigate_main_page(device, replay, nav.PAGE_MAIN, nav.PAGE_SHOP)
    assert device.clicks == [BOTTOM_NAV_SHOP_TAB_COORD]


def test_unrecognized_daily_status_is_not_treated_as_already_collected(replay):
    device = ShopDevice()
    device.frames["free"][430:470, 30:110] = 0
    assert not shop.shop_daily_state(device, replay)
    assert device.page == "main" and replay.collections == 0
    assert (50, 457) not in device.clicks


@pytest.mark.parametrize("claim_fails", [False, True])
def test_only_a_verified_check_starts_the_hourly_interval(replay, claim_fails):
    device = ShopDevice(claim_fails=claim_fails)
    context = states.RuntimeContext()
    jobs = {UIField.SHOP_DAILY_OFFER_USER_TOGGLE: True}
    result = states.state_tree(device, replay, "shop_daily", jobs, context)
    if claim_fails:
        assert result == "restart" and context.last_shop_at is None
        assert context.completed_jobs == 0
    else:
        assert result == "select_battle_mode" and context.last_shop_at is not None
        assert context.completed_jobs == 1


def test_slow_swipe_passes_duration_to_adb(monkeypatch):
    device = AdbController(ShopLogger(), device_serial="emulator-5554")
    commands = []
    monkeypatch.setattr(device, "adb", commands.append)
    device.swipe(210, 520, 210, 280, duration_ms=1000)
    assert commands == [["shell", "input", "swipe", "210", "520", "210", "280", "1000"]]
    with pytest.raises(ValueError):
        device.swipe(210, 520, 210, 280, duration_ms=0)
