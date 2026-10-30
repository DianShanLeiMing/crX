"""Card placement profiles, groups and color fingerprints (upstream data)."""

from crX.bot.card_fingerprints import load_builtin_fingerprints

_fingerprints = load_builtin_fingerprints()

# Additional native BGR fingerprints; preserve upstream entries and card identities.
# Each quadrant contains 27*33 pixels; channels follow card_detection.COLORS_KEYS.
# Native names verified against the current client's card information screens.
CARD_FINGERPRINT_VARIANTS = _fingerprints["variants"]

# 中文注释使用简体中文常用卡名, 便于对照英文 ID 阅读。
# evo_ / hero_ 分别标为觉醒版 / 精英版, 按现有 ID 区分变体。
# 2026-10-18 核对 RoyaleAPI Ranked 列表 188 项, 另保留精灵女王的两种形态 ID。
# 名称核对参考: https://royaleapi.com/cards/popular?time=7d&mode=grid&cat=Ranked&sort=rating
# 包含塔楼部队的显示名称; 名录收录不代表已有手牌指纹或出牌分组。

# Display names only; recognition, placement and recording keep the original card IDs.
CARD_NAMES_ZH: dict[str, str] = {
    "archer_queen": "弓箭女皇",
    "archers": "弓箭手",
    "arrows": "万箭齐发",
    "baby_dragon": "飞龙宝宝",
    "balloon": "气球兵",
    "bandit": "幻影刺客",
    "barb_barrel": "野蛮人滚桶",
    "barb_hut": "野蛮人小屋",
    "barbarians": "野蛮人",
    "bats": "蝙蝠",
    "battle_healer": "战斗天使",
    "battle_ram": "野蛮人攻城槌",
    "berserker": "狂战士",
    "bomb_tower": "炸弹塔",
    "bomber": "炸弹兵",
    "boss_bandit": "刺客头领",
    "bowler": "巨石投手",
    "cannon": "加农炮",
    "cannon_cart": "加农炮战车",
    "cannoneer": "炮兵",
    "clone": "克隆法术",
    "dagger_duchess": "飞刀女爵",
    "dark_prince": "黑暗王子",
    "dart_goblin": "吹箭哥布林",
    "earthquake": "地震法术",
    "electro_dragon": "雷电飞龙",
    "electro_giant": "雷电巨人",
    "electro_spirit": "雷电精灵",
    "electro_wizard": "闪电法师",
    "elite_barbarians": "野蛮人精锐",
    "elixir_collector": "圣水收集器",
    "elixir_golem": "圣水戈仑",
    "evo_archers": "觉醒版弓箭手",
    "evo_baby_dragon": "觉醒版飞龙宝宝",
    "evo_barbarians": "觉醒版野蛮人",
    "evo_bats": "觉醒版蝙蝠",
    "evo_battle_ram": "觉醒版野蛮人攻城槌",
    "evo_bomber": "觉醒版炸弹兵",
    "evo_cannon": "觉醒版加农炮",
    "evo_dart_goblin": "觉醒版吹箭哥布林",
    "evo_electro_dragon": "觉醒版雷电飞龙",
    "evo_electro_giant": "觉醒版雷电巨人",
    "evo_elite_barbarians": "觉醒版野蛮人精锐",
    "evo_executioner": "觉醒版飞斧屠夫",
    "evo_fire_cracker": "觉醒版烟花炮手",
    "evo_furnace": "觉醒版烈焰熔炉",
    "evo_goblin_barrel": "觉醒版哥布林飞桶",
    "evo_goblin_cage": "觉醒版哥布林牢笼",
    "evo_goblin_drill": "觉醒版哥布林钻机",
    "evo_goblin_giant": "觉醒版哥布林巨人",
    "evo_hunter": "觉醒版猎人",
    "evo_ice_spirit": "觉醒版冰雪精灵",
    "evo_inferno_dragon": "觉醒版地狱飞龙",
    "evo_knight": "觉醒版骑士",
    "evo_lumberjack": "觉醒版狂暴樵夫",
    "evo_mega_knight": "觉醒版超级骑士",
    "evo_minion_horde": "觉醒版亡灵大军",
    "evo_mortar": "觉醒版迫击炮",
    "evo_musketeer": "觉醒版火枪手",
    "evo_pekka": "觉醒版皮卡超人",
    "evo_princess": "觉醒版公主",
    "evo_royal_ghost": "觉醒版皇家幽灵",
    "evo_royal_giant": "觉醒版皇家巨人",
    "evo_royal_hogs": "觉醒版皇家野猪",
    "evo_royal_recruits": "觉醒版皇家卫队",
    "evo_skeleton_army": "觉醒版骷髅军团",
    "evo_skeleton_barrel": "觉醒版骷髅气球",
    "evo_skeletons": "觉醒版骷髅兵",
    "evo_snowball": "觉醒版巨大雪球",
    "evo_tesla": "觉醒版特斯拉电磁塔",
    "evo_valkyrie": "觉醒版瓦基丽武神",
    "evo_wall_breakers": "觉醒版攻城炸弹人",
    "evo_witch": "觉醒版女巫",
    "evo_wizard": "觉醒版法师",
    "evo_zap": "觉醒版电击法术",
    "executioner": "飞斧屠夫",
    "fire_cracker": "烟花炮手",
    "fire_spirit": "烈焰精灵",
    "fireball": "火球",
    "fisherman": "渔夫",
    "flying_machine": "飞行器",
    "freeze": "冰冻法术",
    "furnace": "烈焰熔炉",
    "giant": "巨人",
    "giant_skeleton": "骷髅巨人",
    "gob_curse": "哥布林诅咒",
    "goblin_barrel": "哥布林飞桶",
    "goblin_cage": "哥布林牢笼",
    "goblin_demolisher": "哥布林爆破手",
    "goblin_drill": "哥布林钻机",
    "goblin_gang": "哥布林团伙",
    "goblin_giant": "哥布林巨人",
    "goblin_hut": "哥布林小屋",
    "goblin_machine": "哥布林机甲",
    "goblins": "哥布林",
    "goblinstein": "科学怪哥布林",
    "golden_knight": "黄金圣骑",
    "golem": "戈仑石人",
    "graveyard": "骷髅召唤",
    "guards": "骷髅守卫",
    "heal_spirit": "治疗精灵",
    "hero_balloon": "精英版气球兵",
    "hero_barb_barrel": "精英版野蛮人滚桶",
    "hero_berserker": "精英版狂战士",
    "hero_bowler": "精英版巨石投手",
    "hero_dark_prince": "精英版黑暗王子",
    "hero_electro_wizard": "精英版闪电法师",
    "hero_giant": "精英版巨人",
    "hero_goblins": "精英版哥布林",
    "hero_ice_golem": "精英版戈仑冰人",
    "hero_ice_wizard": "精英版寒冰法师",
    "hero_knight": "精英版骑士",
    "hero_magic_archer": "精英版神箭游侠",
    "hero_mega_minion": "精英版重甲亡灵",
    "hero_mini_pekka": "精英版迷你皮卡",
    "hero_musketeer": "精英版火枪手",
    "hero_tombstone": "精英版骷髅墓碑",
    "hero_valkyrie": "精英版瓦基丽武神",
    "hero_wizard": "精英版法师",
    "hog": "野猪骑士",
    "hunter": "猎人",
    "ice_golem": "戈仑冰人",
    "ice_spirit": "冰雪精灵",
    "ice_wizard": "寒冰法师",
    "inferno_dragon": "地狱飞龙",
    "inferno_tower": "地狱之塔",
    "knight": "骑士",
    "lava_hound": "熔岩猎犬",
    "lightning": "雷电法术",
    "little_prince": "小王子",
    "log": "复仇滚木",
    "lumberjack": "狂暴樵夫",
    "magic_archer": "神箭游侠",
    "mega_knight": "超级骑士",
    "mega_minion": "重甲亡灵",
    "mighty_miner": "威猛矿工",
    "miner": "掘地矿工",
    "mini_pekka": "迷你皮卡",
    "minion_horde": "亡灵大军",
    "minions": "亡灵",
    "minion_giant": "亡灵巨人",
    "mirror": "镜像法术",
    "monk": "圣僧",
    "mortar": "迫击炮",
    "mother_witch": "女巫婆婆",
    "musketeer": "火枪手",
    "night_witch": "暗夜女巫",
    "pekka": "皮卡超人",
    "phoenix": "凤凰",
    "poison": "毒药法术",
    "prince": "王子",
    "princess": "公主",
    "rage": "狂暴法术",
    "ram_rider": "蛮羊骑士",
    "rascals": "绿林团伙",
    "rocket": "火箭",
    "ronin": "浪人",
    "royal_chef": "皇家大厨",
    "royal_delivery": "皇家速递",
    "royal_ghost": "皇家幽灵",
    "royal_giant": "皇家巨人",
    "royal_hogs": "皇家野猪",
    "royal_recruits": "皇家卫队",
    "rune_giant": "符文巨人",
    "skeleton_army": "骷髅军团",
    "skeleton_barrel": "骷髅气球",
    "skeleton_dragons": "骷髅飞龙",
    "skeleton_king": "骷髅帝王",
    "skeletons": "骷髅兵",
    "snowball": "巨大雪球",
    "sparky": "电磁炮",
    "spear_goblins": "哥布林投矛手",
    "spirit_empress": "精灵女王",
    "spirit_empress_air": "精灵女王空中形态",
    "spirit_empress_ground": "精灵女王地面形态",
    "suspicious_bush": "可疑草丛",
    "tesla": "特斯拉电磁塔",
    "three_musketeers": "三个火枪手",
    "tombstone": "骷髅墓碑",
    "tornado": "飓风法术",
    "tower_princess": "皇家塔公主",
    "valkyrie": "瓦基丽武神",
    "vines": "藤蔓法术",
    "void": "虚空法术",
    "wall_breakers": "攻城炸弹人",
    "witch": "女巫",
    "wizard": "法师",
    "xbow": "X连弩",
    "zap": "电击法术",
    "zappies": "电击车小队",
}


def get_card_display_name(card_id: str | None) -> str:
    """Return the Chinese card name for status messages."""
    return CARD_NAMES_ZH.get(card_id or "", "未知卡牌")


# Placement profiles: where on screen to tap (left/right), not card role in battle.
# Shared arena zones use zone_* keys; card-named keys stay when only that card (or its evo) uses the region.
# CARD_GROUPS maps card ids → key below (see #674 for full id list including pre-fingerprint evo/hero).
# Visual map: docs/placement-zones.md
PLAY_COORDS = {
    # Troops: bridge line (tanks + heavy win conditions share coords until a follow-up retune).
    "bridge_line": {
        "left": [(99, 336)],
        "right": [(253, 340)],
    },
    # Troops: bridge rush (Hog, Ram, Wall Breakers, …).
    "bridge_rush": {
        "left": [(66, 284), (97, 289), (132, 286)],
        "right": [(221, 286), (258, 287), (303, 286)],
    },
    # Troops/buildings: back field support (swarms, spawners, ranged support).
    "back_support": {
        "left": [(59, 447), (136, 449), (143, 398), (88, 456)],
        "right": [(212, 400), (227, 445), (295, 447), (268, 461)],
    },
    # Troops: king-tower lane (Witch, Archer Queen, …).
    "king_lane": {
        "left": [(60, 468), (158, 402), (143, 398), (164, 478)],
        "right": [(212, 400), (227, 445), (295, 468), (181, 476)],
    },
    # Princess (+ evo): deep lane, tuned separately from king_lane.
    "princess": {  # 公主
        "left": [(60, 468), (206, 404), (164, 476), (189, 406)],
        "right": [(295, 468), (158, 402), (181, 478), (143, 398)],
    },
    # Buildings: defensive structures in the back field.
    "defense_building": {
        "left": [(192, 324), (164, 355), (156, 366), (174, 343)],
        "right": [(192, 324), (192, 338), (184, 364), (181, 385)],
    },
    # Buildings: siege (Mortar, X-Bow).
    "siege_building": {
        "left": [(105, 314), (107, 315)],
        "right": [(251, 314), (256, 315)],
    },
    # Spells: center field behind bridge (Mirror, Barb Barrel, Royal Delivery).
    "center_spell": {
        "left": [(100, 162)],
        "right": [(259, 162)],
    },
    # Spells: standard lane target (Fireball, Log, …).
    "lane_spell": {
        "left": [(101, 187)],
        "right": [(253, 187)],
    },
    # Area spells: enemy Princess Tower, offset slightly inward and toward the river.
    # Calibrated on native 360x640 captures; tower centers are approximately (86,130)/(274,130).
    "large_spell": {"left": [(102, 146)], "right": [(258, 146)]},
    # Poison: closer to the tower center, retaining an 8 px inward/river offset.
    "poison": {"left": [(94, 138)], "right": [(266, 138)]},
    # Spells: small reactive (Zap, Void, Clone, Rage, Goblin Curse, …).
    "reactive_spell": {
        "left": [(101, 187)],
        "right": [(253, 187)],
    },
    # Troops: 1-elixir spirits (played on your side).
    # Same coords as defense_building until spirit lane is tuned.
    "spirit": {
        "left": [(192, 324), (164, 355), (156, 366), (174, 343)],
        "right": [(192, 324), (192, 338), (184, 364), (181, 385)],
    },
    "tornado": {  # 飓风法术
        "left": [(43, 184)],
        "right": [(305, 180)],
    },
    "rocket": {  # 火箭
        "left": [(109, 163)],
        "right": [(242, 164)],
    },
    "goblin_barrel": {  # 哥布林飞桶
        "left": [(99, 163), (100, 163), (101, 163)],
        "right": [(258, 163), (259, 163), (259, 163)],
    },
    "graveyard": {  # 骷髅召唤
        "left": [(76, 159)],
        "right": [(279, 158)],
    },
    "miner": {  # 掘地矿工
        "left": [(74, 158), (77, 105), (123, 114), (122, 155)],
        "right": [(235, 154), (237, 112), (291, 112), (278, 159)],
    },
    "goblin_drill": {  # 哥布林钻机
        "left": [(78, 151), (105, 185)],
        "right": [(262, 179), (287, 156)],
    },
}


# Maps card ids → arena zone profile → PLAY_COORDS (see #674).
# Includes base, Evolution, and Hero ids even before card_color_data fingerprints exist so
# new detection work automatically picks up tuned clicks instead of the No group fallback.
# New-card role rationale and sources: docs/placement-zones.md.
CARD_GROUPS: dict[str, list[str]] = {
    "king_lane": [
        "archer_queen",  # 弓箭女皇
        "electro_dragon",  # 雷电飞龙
        "evo_electro_dragon",  # 觉醒版雷电飞龙
        "goblin_demolisher",  # 哥布林爆破手
        "little_prince",  # 小王子
        "mega_minion",  # 重甲亡灵
        "hero_mega_minion",  # 精英版重甲亡灵
        "mother_witch",  # 女巫婆婆
        "night_witch",  # 暗夜女巫
        "royal_recruits",  # 皇家卫队
        "evo_royal_recruits",  # 觉醒版皇家卫队
        "three_musketeers",  # 三个火枪手
        "witch",  # 女巫
        "evo_witch",  # 觉醒版女巫
    ],
    "princess": [
        "princess",  # 公主
        "evo_princess",  # 觉醒版公主
    ],
    "bridge_line": [
        "balloon",  # 气球兵
        "hero_balloon",  # 精英版气球兵
        "electro_giant",  # 雷电巨人
        "evo_electro_giant",  # 觉醒版雷电巨人: 沿用本体的前场推进点
        "elixir_golem",  # 圣水戈仑
        "giant",  # 巨人
        "hero_giant",  # 精英版巨人
        "giant_skeleton",  # 骷髅巨人
        "goblin_giant",  # 哥布林巨人
        "evo_goblin_giant",  # 觉醒版哥布林巨人
        "golem",  # 戈仑石人
        "lava_hound",  # 熔岩猎犬
        "minion_giant",  # 亡灵巨人
        "royal_giant",  # 皇家巨人
        "evo_royal_giant",  # 觉醒版皇家巨人
        "rune_giant",  # 符文巨人
        "sparky",  # 电磁炮
        "bandit",  # 幻影刺客
        "barbarians",  # 野蛮人
        "evo_barbarians",  # 觉醒版野蛮人
        "boss_bandit",  # 刺客头领
        "bowler",  # 巨石投手
        "hero_bowler",  # 精英版巨石投手
        "dark_prince",  # 黑暗王子
        "hero_dark_prince",  # 精英版黑暗王子
        "elite_barbarians",  # 野蛮人精锐
        "evo_elite_barbarians",  # 觉醒版野蛮人精锐: 近战推进, 沿用本体落点
        "fisherman",  # 渔夫
        "golden_knight",  # 黄金圣骑
        "guards",  # 骷髅守卫
        "ice_golem",  # 戈仑冰人
        "hero_ice_golem",  # 精英版戈仑冰人
        "knight",  # 骑士
        "evo_knight",  # 觉醒版骑士
        "hero_knight",  # 精英版骑士
        "mega_knight",  # 超级骑士
        "evo_mega_knight",  # 觉醒版超级骑士
        "mighty_miner",  # 威猛矿工
        "mini_pekka",  # 迷你皮卡
        "hero_mini_pekka",  # 精英版迷你皮卡
        "monk",  # 圣僧
        "pekka",  # 皮卡超人
        "evo_pekka",  # 觉醒版皮卡超人
        "clone",  # 克隆法术
        "prince",  # 王子
        "ronin",  # 浪人: 近战单体交战, 使用己方前场点
        "royal_ghost",  # 皇家幽灵
        "evo_royal_ghost",  # 觉醒版皇家幽灵
        "skeleton_king",  # 骷髅帝王
        "valkyrie",  # 瓦基丽武神
        "evo_valkyrie",  # 觉醒版瓦基丽武神
        "hero_valkyrie",  # 精英版瓦基丽武神: 近战范围清理, 沿用本体落点
        "berserker",  # 狂战士
        "hero_berserker",  # 精英版狂战士: 近战输出, 沿用本体落点
        "spirit_empress",  # 精灵女王
    ],
    "center_spell": [
        "barb_barrel",  # 野蛮人滚桶
        "hero_barb_barrel",  # 精英版野蛮人滚桶
        "mirror",  # 镜像法术
        "royal_delivery",  # 皇家速递
    ],
    "lane_spell": [
        "fireball",  # 火球
        "log",  # 复仇滚木
        "snowball",  # 巨大雪球
        "evo_snowball",  # 觉醒版巨大雪球
    ],
    "large_spell": [
        "earthquake",
        "arrows",
        "freeze",
        "lightning",
    ],  # 地震法术、万箭齐发、冰冻法术、雷电法术
    "poison": ["poison"],  # 毒药法术: 独立校准为更靠近敌方公主塔中心
    "spirit": [
        "electro_spirit",  # 雷电精灵
        "fire_spirit",  # 烈焰精灵
        "heal_spirit",  # 治疗精灵
        "ice_spirit",  # 冰雪精灵
        "evo_ice_spirit",  # 觉醒版冰雪精灵
    ],
    "reactive_spell": [
        "gob_curse",  # 哥布林诅咒
        "rage",  # 狂暴法术
        "void",  # 虚空法术
        "vines",  # 藤蔓法术
        "zap",  # 电击法术
        "evo_zap",  # 觉醒版电击法术
    ],
    "rocket": ["rocket"],  # 火箭
    "tornado": ["tornado"],  # 飓风法术
    "goblin_drill": ["goblin_drill", "evo_goblin_drill"],  # 哥布林钻机、觉醒版哥布林钻机
    "graveyard": ["graveyard"],  # 骷髅召唤
    "defense_building": [
        "bomb_tower",  # 炸弹塔
        "cannon",  # 加农炮
        "evo_cannon",  # 觉醒版加农炮
        "cannon_cart",  # 加农炮战车
        "goblin_cage",  # 哥布林牢笼
        "evo_goblin_cage",  # 觉醒版哥布林牢笼
        "goblinstein",  # 科学怪哥布林
        "inferno_tower",  # 地狱之塔
        "tesla",  # 特斯拉电磁塔
        "evo_tesla",  # 觉醒版特斯拉电磁塔
    ],
    "bridge_rush": [
        "battle_ram",  # 野蛮人攻城槌
        "evo_battle_ram",  # 觉醒版野蛮人攻城槌
        "hog",  # 野猪骑士
        "lumberjack",  # 狂暴樵夫
        "evo_lumberjack",  # 觉醒版狂暴樵夫
        "ram_rider",  # 蛮羊骑士
        "royal_hogs",  # 皇家野猪
        "evo_royal_hogs",  # 觉醒版皇家野猪
        "skeleton_barrel",  # 骷髅气球
        "evo_skeleton_barrel",  # 觉醒版骷髅气球
        "suspicious_bush",  # 可疑草丛
        "wall_breakers",  # 攻城炸弹人
        "evo_wall_breakers",  # 觉醒版攻城炸弹人
    ],
    "miner": [
        "miner",  # 掘地矿工
    ],
    "goblin_barrel": [
        "goblin_barrel",  # 哥布林飞桶
        "evo_goblin_barrel",  # 觉醒版哥布林飞桶
    ],
    "siege_building": [
        "mortar",  # 迫击炮
        "evo_mortar",  # 觉醒版迫击炮
        "xbow",  # X连弩
    ],
    "back_support": [
        "archers",  # 弓箭手
        "evo_archers",  # 觉醒版弓箭手
        "baby_dragon",  # 飞龙宝宝
        "evo_baby_dragon",  # 觉醒版飞龙宝宝
        "barb_hut",  # 野蛮人小屋
        "bats",  # 蝙蝠
        "evo_bats",  # 觉醒版蝙蝠
        "battle_healer",  # 战斗天使
        "bomber",  # 炸弹兵
        "evo_bomber",  # 觉醒版炸弹兵
        "dart_goblin",  # 吹箭哥布林
        "evo_dart_goblin",  # 觉醒版吹箭哥布林
        "electro_wizard",  # 闪电法师
        "hero_electro_wizard",  # 精英版闪电法师: 后排控制支援, 沿用本体落点
        "elixir_collector",  # 圣水收集器
        "executioner",  # 飞斧屠夫
        "evo_executioner",  # 觉醒版飞斧屠夫
        "fire_cracker",  # 烟花炮手
        "evo_fire_cracker",  # 觉醒版烟花炮手
        "flying_machine",  # 飞行器
        "furnace",  # 烈焰熔炉
        "evo_furnace",  # 觉醒版烈焰熔炉
        "goblin_gang",  # 哥布林团伙
        "goblin_hut",  # 哥布林小屋
        "goblin_machine",  # 哥布林机甲
        "goblins",  # 哥布林
        "hero_goblins",  # 精英版哥布林
        "hunter",  # 猎人
        "evo_hunter",  # 觉醒版猎人
        "ice_wizard",  # 寒冰法师
        "hero_ice_wizard",  # 精英版寒冰法师: 后排减速支援, 沿用本体落点
        "inferno_dragon",  # 地狱飞龙
        "evo_inferno_dragon",  # 觉醒版地狱飞龙
        "magic_archer",  # 神箭游侠
        "hero_magic_archer",  # 精英版神箭游侠
        "minion_horde",  # 亡灵大军
        "evo_minion_horde",  # 觉醒版亡灵大军
        "minions",  # 亡灵
        "musketeer",  # 火枪手
        "evo_musketeer",  # 觉醒版火枪手
        "hero_musketeer",  # 精英版火枪手
        "phoenix",  # 凤凰
        "rascals",  # 绿林团伙
        "skeleton_army",  # 骷髅军团
        "evo_skeleton_army",  # 觉醒版骷髅军团
        "skeleton_dragons",  # 骷髅飞龙
        "skeletons",  # 骷髅兵
        "evo_skeletons",  # 觉醒版骷髅兵
        "spear_goblins",  # 哥布林投矛手
        "tombstone",  # 骷髅墓碑
        "hero_tombstone",  # 精英版骷髅墓碑
        "wizard",  # 法师
        "evo_wizard",  # 觉醒版法师
        "hero_wizard",  # 精英版法师
        "zappies",  # 电击车小队
    ],
}

# card classification data used for potential future filtering/analytics; currently informational only
CARD_ATTRIBUTES: dict[str, dict[str, str]] = {
    "minion_giant": {"type": "Troop", "subtype": "Flying win condition"},  # 亡灵巨人
    "cannon": {"type": "Building", "subtype": "Ground defense"},  # 加农炮
    "cannon_cart": {"type": "Troop/Building", "subtype": "Mobile defense"},  # 加农炮战车
    "tesla": {"type": "Building", "subtype": "Mixed defense"},  # 特斯拉电磁塔
    "bomb_tower": {"type": "Building", "subtype": "Anti-swarms"},  # 炸弹塔
    "inferno_tower": {"type": "Building", "subtype": "Anti-tank"},  # 地狱之塔
    "mortar": {"type": "Building", "subtype": "Win condition / long-range"},  # 迫击炮
    "xbow": {"type": "Building", "subtype": "Win condition / cycle"},  # X连弩
    "tombstone": {"type": "Building", "subtype": "Spawner / defense"},  # 骷髅墓碑
    "elixir_collector": {"type": "Building", "subtype": "Economy / support"},  # 圣水收集器
    "barb_hut": {"type": "Building", "subtype": "Spawner defense"},  # 野蛮人小屋
    "goblin_hut": {"type": "Building", "subtype": "Spawner / pressure"},  # 哥布林小屋
    "furnace": {"type": "Building", "subtype": "Chip pressure"},  # 烈焰熔炉
    "goblin_cage": {"type": "Building", "subtype": "Tank-lure defense"},  # 哥布林牢笼
    "knight": {"type": "Troop", "subtype": "Mini-tank / defense"},  # 骑士
    "barbarians": {"type": "Troop", "subtype": "Ground defense"},  # 野蛮人
    "elite_barbarians": {"type": "Troop", "subtype": "Pressure attack"},  # 野蛮人精锐
    "giant": {"type": "Troop", "subtype": "Tank / Win condition"},  # 巨人
    "royal_giant": {"type": "Troop", "subtype": "Win condition"},  # 皇家巨人
    "golem": {"type": "Troop", "subtype": "Tank / Win condition"},  # 戈仑石人
    "elixir_golem": {"type": "Troop", "subtype": "Offensive tank"},  # 圣水戈仑
    "giant_skeleton": {"type": "Troop", "subtype": "Anti-push"},  # 骷髅巨人
    "mini_pekka": {"type": "Troop", "subtype": "Anti-tank"},  # 迷你皮卡
    "pekka": {"type": "Troop", "subtype": "Heavy anti-tank"},  # 皮卡超人
    "prince": {"type": "Troop", "subtype": "Tower attacker"},  # 王子
    "dark_prince": {"type": "Troop", "subtype": "Ground splash"},  # 黑暗王子
    "golden_knight": {"type": "Champion", "subtype": "Assassin / pressure"},  # 黄金圣骑
    "monk": {"type": "Champion", "subtype": "Control / reflect"},  # 圣僧
    "mighty_miner": {"type": "Champion", "subtype": "Mobile win pressure"},  # 威猛矿工
    "bandit": {"type": "Troop", "subtype": "Dash assassin"},  # 幻影刺客
    "royal_ghost": {"type": "Troop", "subtype": "Stealth control"},  # 皇家幽灵
    "valkyrie": {"type": "Troop", "subtype": "Splash defense"},  # 瓦基丽武神
    "witch": {"type": "Troop", "subtype": "Skeleton support"},  # 女巫
    "night_witch": {"type": "Troop", "subtype": "Bat support"},  # 暗夜女巫
    "hunter": {"type": "Troop", "subtype": "Close-range burst"},  # 猎人
    "bomber": {"type": "Troop", "subtype": "Ground splash"},  # 炸弹兵
    "spear_goblins": {"type": "Troop", "subtype": "Cycle poke"},  # 哥布林投矛手
    "goblins": {"type": "Troop", "subtype": "Cycle defense"},  # 哥布林
    "goblin_gang": {"type": "Troop", "subtype": "Fast defense"},  # 哥布林团伙
    "skeletons": {"type": "Troop", "subtype": "Cycle"},  # 骷髅兵
    "skeleton_army": {"type": "Troop", "subtype": "Mass ground defense"},  # 骷髅军团
    "battle_ram": {"type": "Troop", "subtype": "Win condition"},  # 野蛮人攻城槌
    "hog": {"type": "Troop", "subtype": "Direct win condition"},  # 野猪骑士
    "ram_rider": {"type": "Troop", "subtype": "Win condition hybrid"},  # 蛮羊骑士
    "goblin_giant": {"type": "Troop", "subtype": "Offensive tank"},  # 哥布林巨人
    "dart_goblin": {"type": "Troop", "subtype": "Chip long-range"},  # 吹箭哥布林
    "ice_golem": {"type": "Troop", "subtype": "Cheap tank / slow"},  # 戈仑冰人
    "balloon": {"type": "Troop", "subtype": "Win condition / tower damage"},  # 气球兵
    "lava_hound": {"type": "Troop", "subtype": "Air tank / win condition"},  # 熔岩猎犬
    "baby_dragon": {"type": "Troop", "subtype": "Air splash"},  # 飞龙宝宝
    "inferno_dragon": {"type": "Troop", "subtype": "Air anti-tank"},  # 地狱飞龙
    "skeleton_dragons": {"type": "Troop", "subtype": "Support air"},  # 骷髅飞龙
    "minions": {"type": "Troop", "subtype": "Air defense"},  # 亡灵
    "minion_horde": {"type": "Troop", "subtype": "High DPS air"},  # 亡灵大军
    "flying_machine": {"type": "Troop", "subtype": "Long-range support"},  # 飞行器
    "phoenix": {"type": "Troop", "subtype": "Defensive/Counter push"},  # 凤凰
    "wizard": {"type": "Troop", "subtype": "Splash support"},  # 法师
    "ice_wizard": {"type": "Troop", "subtype": "Slow control"},  # 寒冰法师
    "electro_wizard": {"type": "Troop", "subtype": "Stun & reset"},  # 闪电法师
    "magic_archer": {"type": "Troop", "subtype": "Long-line chip"},  # 神箭游侠
    "archers": {"type": "Troop", "subtype": "Cheap ranged defense"},  # 弓箭手
    "battle_healer": {"type": "Troop", "subtype": "Healing support"},  # 战斗天使
    "graveyard": {"type": "Spell", "subtype": "Win condition"},  # 骷髅召唤
    "miner": {"type": "Troop", "subtype": "Chip win condition"},  # 掘地矿工
    "sparky": {"type": "Troop", "subtype": "Massive tower damage"},  # 电磁炮
    "arrows": {"type": "Spell", "subtype": "Anti-swarms"},  # 万箭齐发
    "snowball": {"type": "Spell", "subtype": "Control knockback"},  # 巨大雪球
    "zap": {"type": "Spell", "subtype": "Reset & cleanup"},  # 电击法术
    "log": {"type": "Spell", "subtype": "Ground chip + knockback"},  # 复仇滚木
    "barb_barrel": {"type": "Spell", "subtype": "Ground control"},  # 野蛮人滚桶
    "fireball": {"type": "Spell", "subtype": "Medium damage"},  # 火球
    "poison": {"type": "Spell", "subtype": "Zone control"},  # 毒药法术
    "lightning": {"type": "Spell", "subtype": "Support removal"},  # 雷电法术
    "rocket": {"type": "Spell", "subtype": "High damage"},  # 火箭
    "rage": {"type": "Spell", "subtype": "Offensive buff"},  # 狂暴法术
    "heal_spirit": {"type": "Spell", "subtype": "Healing burst"},  # 治疗精灵
    "freeze": {"type": "Spell", "subtype": "Full control"},  # 冰冻法术
    "tornado": {"type": "Spell", "subtype": "Group control"},  # 飓风法术
    "mirror": {"type": "Spell", "subtype": "Duplicate card"},  # 镜像法术
    "clone": {"type": "Spell", "subtype": "Offensive synergy"},  # 克隆法术
    "royal_delivery": {"type": "Spell", "subtype": "Defensive burst"},  # 皇家速递
    "goblin_barrel": {"type": "Spell", "subtype": "Chip win condition"},  # 哥布林飞桶
    "skeleton_barrel": {"type": "Spell", "subtype": "Surprise pressure"},  # 骷髅气球
}

# card color data (initially holds dict[str, int] entries, then transformed to ndarray)
card_color_data = _fingerprints["base"]

# card image stuff
