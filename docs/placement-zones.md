# Card placement zones

The bot picks a **play group** for each detected card, then taps a random point from that group’s left or right lane (`PLAY_COORDS` in `crX/bot/card_data.py`).

## Reference map

Runtime geometry is now **360×640 / 160 DPI**. The historical overlay below uses 419×633; multiply x by 360/419 and y by 640/633 to compare its points with the migrated `PLAY_COORDS` table. The `large_spell` points were subsequently recalibrated on native captures as described below and no longer follow that overlay.

![Placement zones overlay](placement-zones-overlay.png)

- **Your side** is the **bottom** of the image; **enemy** is the top.
- **Shape + color** = play group (see legend at the bottom of the image).
- Example: yellow **stars** = `center_spell`; red **circles** = `bridge_line`; pink **squares** = `spirit` (uses building coords on your side).

## Play groups (summary)

| Group | Arena area |
|-------|------------|
| `king_lane` | Deep lane / behind king |
| `princess` | Princess lane |
| `bridge_line` | Bridge line (tanks, heavies) |
| `bridge_rush` | In front of bridge |
| `back_support` | Behind bridge |
| `defense_building` | Back-field buildings |
| `siege_building` | Mortar / X-Bow |
| `center_spell` | Center behind river |
| `lane_spell` | Standard spell lane |
| `large_spell` | Enemy Princess Tower, slightly inward and toward the river |
| `reactive_spell` | Reactive spells (Zap, Void, Clone, Rage, Vines, …) |
| `spirit` | Spirits (your side; coords match `defense_building` for now) |
| `rocket`, `tornado`, `goblin_barrel`, `graveyard`, `miner`, `goblin_drill` | Card-specific |

Full card → group list lives in `CARD_GROUPS` in `crX/bot/card_data.py` (184 ids including pre-fingerprint evo/hero variants). Spirit Empress' air/ground recognition aliases resolve to the shared `spirit_empress` profile. Tower Troops are not deployable hand cards and have no placement profile.

## 2026-10-20 范围法术落点校准

按用户指定的“覆盖敌方公主塔，偏场地中心下方一点”，将 `large_spell` 改为左路 `(102,146)`、
右路 `(258,146)`。参照已采集的 360×640 原生对战画面，敌方塔中心约为 `(86,130)` / `(274,130)`；
新落点相对塔中心向内、向河道各偏移约 16 像素，替代原先偏移较大的 `(145,197)` / `(214,197)` 附近。

该组包含万箭齐发、地震法术、冰冻法术、雷电法术、毒药法术。继续使用固定落点与现有左右路选择，
不追踪敌军。坐标依据原生截图校准，尚未实测此次调整后的法术施放效果。

## 2026-10-19 新增卡牌落点

补齐当前名录中缺少落点的 7 项。下表是根据官方描述的卡牌定位作出的工程配置判断;
官方资料不提供本程序的像素坐标, 本次复用现有坐标, 尚未验证这些牌的实战效果。

| 卡牌 | 分组 | 配置依据 |
| --- | --- | --- |
| 浪人 `ronin` | `bridge_line` | 官方定位为近战反击单位, 适合与近战目标交战; 使用己方前场点作为基础交战落点。[7 月公告][july] |
| 觉醒版雷电巨人 `evo_electro_giant` | `bridge_line` | 觉醒增加周围电场效果, 仍沿用本体前场推进落点。[10 月公告][october] |
| 觉醒版野蛮人精锐 `evo_elite_barbarians` | `bridge_line` | 保留近战并增加远程投矛和友军狂暴路径, 沿用本体交战落点。[8 月公告][august] |
| 精英版狂战士 `hero_berserker` | `bridge_line` | 技能强化近战生存与攻击, 沿用本体近战落点。[8 月公告][august] |
| 精英版瓦基丽武神 `hero_valkyrie` | `bridge_line` | 技能强化对附近敌人的旋转范围攻击, 沿用本体落点。[8 月公告][august] |
| 精英版闪电法师 `hero_electro_wizard` | `back_support` | 保留控制支援定位, 技能可眩晕附近目标; 沿用本体后排落点。[10 月公告][october] |
| 精英版寒冰法师 `hero_ice_wizard` | `back_support` | 保留减速支援并通过技能冻结区域内敌军, 沿用本体后排落点。[9 月公告][september] |

`bridge_line` 已按 360×640 迁移为左路 `(99, 336)`、右路 `(253, 340)`。
`back_support` 沿用坐标表中每路 4 个点, 按既有逻辑随机选点。
左右路仍由桥附近画面变化决定; 本次不增加敌军定位、精确反击投放或技能触发策略。
这些落点只在正确识别卡牌后使用, 不代表已经补齐原生手牌指纹。
官方资料用于参考卡牌机制, 不据此判断国服上线进度。

[july]: https://supercell.com/en/games/clashroyale/blog/release-notes/new-season-honor-and-exile/
[august]: https://supercell.com/en/games/clashroyale/blog/news/new-season-k-h-a-o-s/
[september]: https://supercell.com/en/games/clashroyale/blog/release-notes/new-season-minion-academy/
[october]: https://supercell.com/en/games/clashroyale/blog/release-notes/new-season-shocktober/
