"""V53 REFACTOR (phase 5): poster / game-image URL resolution.

Two helpers used as Jinja globals via the ``inject_user`` context
processor: :func:`smart_game_image_url` (generated SVG fallback) and
:func:`game_image_url` (admin-uploaded image > matched WebP/JPG poster
> SVG fallback). They were ~200 lines near the top of ``app.py``.

Behaviour is preserved 1-for-1: same alias table (loaded from
``database._POSTER_ALIASES``), same priority order, same self-heal at
display time when an auto-generated poster file was deleted on disk
(V66).
"""
from __future__ import annotations

import os

from flask import url_for


# ---------------------------------------------------------------------------
# Manually-curated keyword → SVG filename map for the smart fallback.
# Keep this list in sync with static/img/smart-games/.
# ---------------------------------------------------------------------------
_SMART_MAPPING: dict[str, str] = {
    # --- Original mappings ---
    "8 ball": "8-ball-pool.svg",
    "afk": "afk-journey.svg",
    "acecraft": "acecraft.svg",
    "arena breakout": "arena-breakout.svg",
    "arena of valor": "arena-of-valor.svg",
    "asphalt": "asphalt-9-legends.svg",
    "black clover": "black-clover-m.svg",
    "blood strike": "blood-strike.svg",
    "call of duty": "call-of-duty-mobile.svg",
    "cod": "call-of-duty-mobile.svg",
    "crossfire": "crossfire-mobile.svg",
    "delta": "delta-force-mobile.svg",
    "dragon nest": "dragon-nest-m.svg",
    "fc": "ea-fc-mobile.svg",
    "fifa": "ea-fc-mobile.svg",
    "eafc": "ea-fc-mobile.svg",
    "eve": "eve-echoes.svg",
    "eggy": "eggy-party.svg",
    "farlight": "farlight-84.svg",
    "genshin": "genshin-impact.svg",
    "honkai": "honkai-star-rail.svg",
    "honor": "honor-of-kings.svg",
    "mobile legends": "mobile-legends.svg",
    "pubg": "pubg-mobile.svg",
    "free fire": "free-fire.svg",
    "freefire": "free-fire.svg",
    "roblox": "roblox.svg",
    "minecraft": "minecraft.svg",
    "valorant": "valorant.svg",
    "clash royale": "clash-royale.svg",
    "clash": "clash-of-clans.svg",
    "stumble": "stumble-guys.svg",
    "wild rift": "wild-rift.svg",
    "zenless": "zenless-zone-zero.svg",
    "ragnarok": "ragnarok-x.svg",
    "solo leveling": "solo-leveling.svg",
    "magic chess": "magic-chess.svg",
    "crystal": "crystal-of-atlan.svg",
    "etheria": "etheria-restart.svg",
    "watcher": "watcher-of-realms.svg",
    "harry potter": "harry-potter-magic-awakened.svg",
    "blockman": "blockman-go.svg",
    "bleach": "bleach-soul-resonance.svg",
    "devil may cry": "devil-may-cry.svg",
    "echocalypse": "echocalypse.svg",
    "frag": "frag-pro-shooter.svg",
    "heartopia": "heartopia.svg",
    "mecha break": "mecha-break.svg",
    "marvel duel": "marvel-duel.svg",
    # --- New mappings (V62) ---
    "age of empire": "age-of-empires-mobile.svg",
    "age of magic": "age-of-magic.svg",
    "arknights": "arknights-endfield.svg",
    "arknight": "arknights-endfield.svg",
    "azur lane": "azur-lane.svg",
    "bigo": "bigo-live.svg",
    "bullet echo": "bullet-echo.svg",
    "cats": "cats-arena.svg",
    "crash arena": "cats-arena.svg",
    "civilization": "civilization-mobile.svg",
    "crossout": "crossout-mobile.svg",
    "deadly dudes": "deadly-dudes.svg",
    "destiny": "destiny-rising.svg",
    "dragon raja": "dragon-raja.svg",
    "dragonheir": "dragonheir.svg",
    "duet night": "duet-night-abyss.svg",
    "dunk city": "dunk-city-dynasty.svg",
    "enhypen": "enhypen-world.svg",
    "nikke": "goddess-of-victory-nikke.svg",
    "gov": "goddess-of-victory-nikke.svg",
    "undawn": "garena-undawn.svg",
    "ghost story": "ghost-story.svg",
    "growtopia": "growtopia.svg",
    "haikyu": "haikyu-fly-high.svg",
    "hatsune": "hatsune-miku.svg",
    "miku": "hatsune-miku.svg",
    "heaven burns": "heaven-burns-red.svg",
    "identity v": "identity-v.svg",
    "kings choice": "kings-choice.svg",
    "king's choice": "kings-choice.svg",
    "kingshot": "kingshot.svg",
    "knives out": "knives-out.svg",
    "league of legends": "league-of-legends.svg",
    "lol": "league-of-legends.svg",
    "legend of the phoenix": "legend-of-phoenix.svg",
    "legend of phoenix": "legend-of-phoenix.svg",
    "legends of runeterra": "legends-of-runeterra.svg",
    "runeterra": "legends-of-runeterra.svg",
    "life makeover": "life-makeover.svg",
    "lifeafter": "lifeafter.svg",
    "likee": "likee.svg",
    "lineage": "lineage2m.svg",
    "lord of the rings": "lord-of-rings-war.svg",
    "lotr": "lord-of-rings-war.svg",
    "love nikki": "love-nikki.svg",
    "love and deepspace": "love-and-deepspace.svg",
    "maplestory": "maplestory-m.svg",
    "maple story": "maplestory-m.svg",
    "marvel rivals": "marvel-rivals.svg",
    "marvel mystic": "marvel-rivals.svg",
    "metal slug": "metal-slug-awakening.svg",
    "modern strike": "modern-strike-online.svg",
    "moonlight blade": "moonlight-blade.svg",
    "my singing": "my-singing-monsters.svg",
    "once human": "once-human.svg",
    "onmyoji": "onmyoji-arena.svg",
    "overmortal": "overmortal.svg",
    "oxide": "oxide-survival.svg",
    "path to nowhere": "path-to-nowhere.svg",
    "pixel gun": "pixel-gun-3d.svg",
    "poppo": "poppo-live.svg",
    "project entropy": "project-entropy.svg",
    "punishing": "punishing-gray-raven.svg",
    "gray raven": "punishing-gray-raven.svg",
    "puzzles": "puzzles-survival.svg",
    "racing master": "racing-master.svg",
    "rainbow six": "rainbow-six-mobile.svg",
    "r6": "rainbow-six-mobile.svg",
    "rememento": "rememento.svg",
    "sausage man": "sausage-man.svg",
    "sea of conquest": "sea-of-conquest.svg",
    "shining nikki": "shining-nikki.svg",
    "silver and blood": "silver-and-blood.svg",
    "sky children": "sky-children-light.svg",
    "sky: children": "sky-children-light.svg",
    "snowbreak": "snowbreak.svg",
    "soul land": "soul-land.svg",
    "spring valley": "spring-valley.svg",
    "star resonance": "star-resonance.svg",
    "starmaker": "starmaker.svg",
    "state of survival": "state-of-survival.svg",
    "stormshot": "stormshot.svg",
    "super sus": "super-sus.svg",
    "sword of justice": "sword-of-justice.svg",
    "t3 arena": "t3-arena.svg",
    "tarisland": "tarisland.svg",
    "teamfight": "teamfight-tactics.svg",
    "tft": "teamfight-tactics.svg",
    "teen patti": "teen-patti-gold.svg",
    "telegram": "telegram.svg",
    "the division": "the-division.svg",
    "division resurgence": "the-division.svg",
    "tiles survive": "tiles-survive.svg",
    "where winds": "where-winds-meet.svg",
    "whiteout": "whiteout-survival.svg",
    "wuthering": "wuthering-waves.svg",
    "yalla": "yalla-ludo.svg",
    "zepeto": "zepeto.svg",
}


# ---------------------------------------------------------------------------
# smart_game_image_url — generated SVG fallback
# ---------------------------------------------------------------------------
def smart_game_image_url(game) -> str:
    """Lightweight generated SVG thumbnails for games without uploaded images."""
    try:
        name = str((game or {}).get("name") or "")
        key = str((game or {}).get("game_key") or "")
    except Exception:
        name, key = str(game or ""), ""
    s = (name + " " + key).lower().replace("_", " ")
    for needle, filename in _SMART_MAPPING.items():
        if needle in s:
            return url_for("static", filename=f"img/smart-games/{filename}")
    return url_for("static", filename="img/smart-games/game-default-smart.svg")


# ---------------------------------------------------------------------------
# Available poster cache + resolver
# ---------------------------------------------------------------------------
_POSTER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static",
    "img",
    "games",
)


def _get_poster_available() -> dict[str, str]:
    """Cache poster basenames -> file extension from static/img/games/.

    V65: switched from a flat set of webp basenames to a {basename: ext}
    map so we can serve the new high-res ``.jpg`` artwork without breaking
    the handful of games still on the old ``.webp`` thumbnails. JPG takes
    priority when both are present.
    """
    if not hasattr(_get_poster_available, "_cache"):
        ext_map: dict[str, str] = {}
        if os.path.isdir(_POSTER_DIR):
            for f in os.listdir(_POSTER_DIR):
                if f.endswith(".jpg"):
                    ext_map[f[:-4]] = "jpg"
                elif f.endswith(".webp") and f[:-5] not in ext_map:
                    ext_map[f[:-5]] = "webp"
        _get_poster_available._cache = ext_map  # type: ignore[attr-defined]
    return _get_poster_available._cache  # type: ignore[attr-defined]


def _resolve_poster_for_display(game_key: str | None) -> str | None:
    """Use the same resolution logic as ``database._resolve_poster_key``.

    Resolution order:
      1. exact match
      2. explicit alias table (``database._POSTER_ALIASES``)
      3. progressively drop trailing _segment(s)
    """
    from database import _POSTER_ALIASES

    available = _get_poster_available()
    if not available or not game_key:
        return None

    gk = game_key.lower()

    if gk in available:
        return gk

    alias = _POSTER_ALIASES.get(gk)
    if alias and alias in available:
        return alias

    parts = gk.split("_")
    while len(parts) > 1:
        parts.pop()
        cand = "_".join(parts)
        if cand in available:
            return cand
        cand_alias = _POSTER_ALIASES.get(cand)
        if cand_alias and cand_alias in available:
            return cand_alias

    return None


# ---------------------------------------------------------------------------
# game_image_url — full resolver used by every game card
# ---------------------------------------------------------------------------
def game_image_url(game) -> str:
    """Priority: admin uploaded/custom image -> matched WebP/JPG poster ->
    smart SVG fallback.

    V64: replaced old substring-matching (which caused wrong images) with
    precise game_key-based poster resolution using the same alias table
    and suffix-stripping logic as ``attach_generated_posters()``.

    V66: self-heal at display time. If the stored ``image_url`` points to
    a file that was removed on disk (e.g. a ``/static/img/games/<key>.webp``
    that V65 replaced with ``.jpg``), skip it and fall through to the live
    resolver. Admin-uploaded URLs (anything not under
    ``/static/img/games/`` top-level) and remote URLs are still trusted.
    """
    try:
        name = str(
            (game or {}).get("name") or (game or {}).get("game_name") or ""
        )
        key = str((game or {}).get("game_key") or "")
        custom = str(
            (game or {}).get("image_url")
            or (game or {}).get("game_image_url")
            or ""
        )
    except Exception:
        name, key, custom = str(game or ""), "", ""

    # 1. Admin-uploaded or DB-assigned image (highest priority) — but only
    #    if the file actually exists for auto-generated /static/img/games/<x>
    #    paths; otherwise fall through to the live resolver below.
    if custom:
        if custom.startswith("/static/img/games/"):
            rel = custom[len("/static/img/games/"):]
            if "/" not in rel:  # top-level auto poster
                on_disk = os.path.join(_POSTER_DIR, rel)
                if os.path.isfile(on_disk):
                    return custom
                # else: fall through to resolver
            else:
                return custom
        else:
            return custom

    # 2. Match poster by game_key (precise, no substring false-positives).
    poster = _resolve_poster_for_display(key)
    if poster:
        ext = _get_poster_available().get(poster, "webp")
        return url_for("static", filename=f"img/games/{poster}.{ext}")

    # 3. Smart SVG fallback (generated thumbnails).
    return smart_game_image_url(game)
