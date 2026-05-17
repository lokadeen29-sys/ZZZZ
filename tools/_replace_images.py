#!/usr/bin/env python3
"""V65: Replace all game cover images with the new high-quality JPG set
delivered in `neon-gaming-topup-updated.zip`.

Targets:
  static/img/games/*.webp           -> rewritten as `<basename>.jpg`
                                       (existing .webp deleted)
  static/img/games/web/*.jpg        -> overwritten with new artwork
  static/img/games-neon/*.jpg       -> overwritten with new artwork

This script is one-shot: it's safe to delete after running. The poster
resolver in `app.py` is updated separately to look for `.jpg` first then
fall back to `.webp` for backwards compatibility.
"""
import os, shutil, sys

NEW_DIR = "/tmp/upload_extract/upload/src/assets"
ROOT    = os.path.join(os.path.dirname(__file__), "..", "static", "img")
ROOT    = os.path.abspath(ROOT)

POSTERS_DIR = os.path.join(ROOT, "games")
WEB_DIR     = os.path.join(POSTERS_DIR, "web")
NEON_DIR    = os.path.join(ROOT, "games-neon")

# ---------------------------------------------------------------------------
# Short-key (e.g. game-XXX.jpg) -> canonical poster basename
# ---------------------------------------------------------------------------
SHORT_KEY_TO_POSTER = {
    "8ball":          "8_ball_pool",
    "afk":            "afk_journey",
    "aoe":            "age_of_empires_mobile",
    "aov":            "arena_of_valor",
    "arenabreakout":  "arena_breakout",
    "asphalt":        "asphalt_9",
    "azur":           "azur_lane",
    "bigo":           "bigo_live",
    "blackclover":    "black_clover_m",
    "bleach":         "bleach_soul_resonance",
    "blockman":       "blockman_go",
    "bloodstrike":    "blood_strike",
    "cod":            "call_of_duty_mobile",
    "crossfire":      "crossfire_mobile",
    "deltaforce":     "delta_force",
    "eafc":           "fc_mobile",
    "eggy":           "eggy_party",
    "farlight":       "farlight_84",
    "fortnite":       None,                    # new title
    "freefire":       "free_fire",
    "genshin":        "genshin_impact",
    "hi3":            "honkai_impact_3rd",
    "hok":            "honor_of_kings",
    "hsr":            "honkai_star_rail",
    "idv":            "identity_v",
    "lds":            "love_and_deepspace",
    "likee":          "likee",
    "lol":            "league_of_legends",
    "maple":          "maplestory_m",
    "marvelrivals":   "marvel_rivals",
    "mecha":          "mecha_break",
    "miku":           "hatsune_miku",
    "mlbb":           "mobile_legends",
    "oncehuman":      "once_human",
    "pgr":            "punishing_gray_raven",
    "pubg":           "pubg_mobile",
    "r6m":            "rainbow_six_mobile",
    "sausage":        "sausage_man",
    "sky":            "sky_children_light",
    "snowbreak":      "snowbreak",
    "sololeveling":   "solo_leveling_arise",
    "sos":            "state_of_survival",
    "starmaker":      "starmaker",
    "stormshot":      "stormshot",
    "stumble":        "stumble_guys",
    "supersus":       "super_sus",
    "tarisland":      "tarisland",
    "telegram":       "telegram",
    "tft":            "teamfight_tactics",
    "tof":            None,                    # tower of fantasy (new)
    "valorant":       "valorant",
    "whiteout":       "whiteout_survival",
    "wildrift":       "wild_rift",
    "wuwa":           "wuthering_waves",
    "wwm":            "where_winds_meet",
    "yalla":          "yalla_ludo",
    "zepeto":         "zepeto",
    "zzz":            "zenless_zone_zero",
}

# ---------------------------------------------------------------------------
# Long descriptive names (e.g. Mobile_Legends_Bang_Bang.jpg) -> poster
# ---------------------------------------------------------------------------
LONG_NAME_TO_POSTER = {
    "Blood_Strike_MENA":               "blood_strike",
    "Bullet_Echo":                     "bullet_echo",
    "CATS_Crash_Arena_Turbo_Stars":    "cats_arena",
    "Call_of_Duty_Mobile":             "call_of_duty_mobile",
    "Civilization_Eras__Allies":       "civilization_eras_allies",
    "Crossfire_Legend__CrossFire_Mobile": "crossfire_mobile",
    "Crossout_Mobile":                 "crossout_mobile",
    "Crystal_of_Atlan":                "crystal_of_atlan",
    "Deadly_Dudes":                    "deadly_dudes",
    "Delta_Force":                     "delta_force",
    "Delta_Force_Mobile":              "delta_force_mobile",
    "Destiny_Rising":                  "destiny_rising",
    "Devil_May_Cry_Peak_of_Combat":    "devil_may_cry",
    "Dragonheir_Silent_Gods":          "dragonheir",
    "Dragon_Nest_M_Classic":           "dragon_nest_m",
    "Dragon_Raja":                     "dragon_raja",
    "Duet_Night_Abyss":                "duet_night_abyss",
    "Dunk_City_Dynasty":               "dunk_city_dynasty",
    "EA_FC_Mobile":                    "fc_mobile",
    "EVE_Echoes":                      "eve_echoes",
    "Echocalypse_Scarlet_Covenant":    "echocalypse",
    "Etheria_Restart":                 "etheria_restart",
    "FRAG_Pro_Shooter":                "frag_pro_shooter",
    "Farlight_84":                     "farlight_84",
    "Free_Fire":                       "free_fire",
    "Garena_Undawn":                   "garena_undawn",
    "Genshin_Impact":                  "genshin_impact",
    "Ghost_Story_Love_Destiny":        "ghost_story",
    "Growtopia":                       "growtopia",
    "HAIKYU_FLY_HIGH":                 "haikyu_fly_high",
    "Harry_Potter_Magic_Awakened":     "harry_potter_magic_awakened",
    "Hatsune_Miku":                    "hatsune_miku",
    "Heartopia":                       "heartopia",
    "Heaven_Burns_Red":                "heaven_burns_red",
    "Hero_Clash":                      "hero_clash",
    "Honkai_Impact_3rd":               "honkai_impact_3rd",
    "Honkai_Star_Rail":                "honkai_star_rail",
    "Honor_of_Kings":                  "honor_of_kings",
    "Identity_V":                      "identity_v",
    "Kings_Choice_SEA":                "kings_choice",
    "Kingshot":                        "kingshot",
    "Knives_Out":                      "knives_out",
    "League_of_Legends":               "league_of_legends",
    "Legend_of_Neverland":             "legend_of_neverland",
    "Legend_of_the_Phoenix":           "legend_of_phoenix",
    "Legends_of_Runeterra":            "legends_of_runeterra",
    "LifeAfter":                       "lifeafter",
    "Life_Makeover_Global":            "life_makeover",
    "Likee":                           "likee",
    "Lineage2M":                       "lineage2m",
    "Lord_of_the_Rings_Rise_to_War":   "lord_of_rings_war",
    "Love_Nikki":                      "love_nikki",
    "Love_and_Deepspace":              "love_and_deepspace",
    "Magic_Chess_Go_Go":               "magic_chess",
    "MapleStory_M":                    "maplestory_m",
    "Marvel_Duel":                     "marvel_duel",
    "Marvel_Mystic_Mayhem":            "marvel_mystic_mayhem",
    "Marvel_Rivals":                   "marvel_rivals",
    "Mecha_BREAK":                     "mecha_break",
    "Metal_Slug_Awakening":            "metal_slug_awakening",
    "Mobile_Legends_Adventure":        "mobile_legends_adventure",
    "Mobile_Legends_Bang_Bang":        "mobile_legends",
    "Modern_Strike_Online":            "modern_strike_online",
    "Moonlight_Blade_M":               "moonlight_blade",
    "My_Singing_Monsters":             "my_singing_monsters",
    "Once_Human":                      "once_human",
    "Onmyoji_Arena":                   "onmyoji_arena",
    "Oxide_Survival_Island":           "oxide_survival",
    "PUBG_Mobile":                     "pubg_mobile",
    "Path_to_Nowhere":                 "path_to_nowhere",
    "Pixel_Gun_3D":                    "pixel_gun_3d",
    "Poppo_Live":                      "poppo_live",
    "Project_Entropy":                 "project_entropy",
    "Punishing_Gray_Raven":            "punishing_gray_raven",
    "Puzzles__Survival":               "puzzles_survival",
    "Racing_Master":                   "racing_master",
    "Ragnarok_Origin":                 "ragnarok_origin",
    "Ragnarok_X_Next_Generation":      "ragnarok_x",
    "Rainbow_Six_Mobile":              "rainbow_six_mobile",
    "Rememento_White_Shadows":         "rememento",
    "Revelation_Infinite_Journey":     "revelation_infinite_journey",
    "Sausage_Man":                     "sausage_man",
    "Sea_of_Conquest":                 "sea_of_conquest",
    "Shining_Nikki":                   "shining_nikki",
    "Silver_and_Blood":                "silver_and_blood",
    "Sky_Children_of_the_Light":       "sky_children_light",
    "Snowbreak_Containment_Zone":      "snowbreak",
    "Solo_Leveling_Arise":             "solo_leveling_arise",
    "Soul_Land_New_World":             "soul_land",
    "Spring_Valley_Farm_Adventures":   "spring_valley",
    "Star_Resonance":                  "star_resonance",
    "Starmaker":                       "starmaker",
    "State_of_Survival":               "state_of_survival",
    "Stormshot":                       "stormshot",
    "Stumble_Guys":                    "stumble_guys",
    "Super_Sus":                       "super_sus",
    "Sword_of_Justice":                "sword_of_justice",
    "Tarisland":                       "tarisland",
    "Teamfight_Tactics":               "teamfight_tactics",
    "Teen_Patti_Gold":                 "teen_patti_gold",
    "Telegram":                        "telegram",
    "The_Division_Resurgence":         "the_division_resurgence",
    "Valorant":                        "valorant",
    "Watcher_of_Realms":               "watcher_of_realms",
    "Where_Winds_Meet":                "where_winds_meet",
    "Wild_Rift":                       "wild_rift",
    "Wuthering_Waves":                 "wuthering_waves",
    "Zenless_Zone_Zero":               "zenless_zone_zero",
    "Zepeto":                          "zepeto",
}


def _build_poster_to_src():
    """Long-name files override short-key files because they're the cleaner
    official artwork in the zip."""
    m = {}
    for short, poster in SHORT_KEY_TO_POSTER.items():
        if not poster:
            continue
        src = os.path.join(NEW_DIR, f"game-{short}.jpg")
        if os.path.isfile(src):
            m.setdefault(poster, src)
    for long_name, poster in LONG_NAME_TO_POSTER.items():
        src = os.path.join(NEW_DIR, f"{long_name}.jpg")
        if os.path.isfile(src):
            m[poster] = src
    return m


def main():
    poster_to_src = _build_poster_to_src()

    # 1. Replace static/img/games/<basename>.webp -> <basename>.jpg
    written, skipped = 0, 0
    for fname in sorted(os.listdir(POSTERS_DIR)):
        full = os.path.join(POSTERS_DIR, fname)
        if not os.path.isfile(full) or not fname.endswith(".webp"):
            continue
        base = fname[:-5]
        src  = poster_to_src.get(base)
        if src:
            shutil.copyfile(src, os.path.join(POSTERS_DIR, base + ".jpg"))
            os.remove(full)
            written += 1
        else:
            skipped += 1
    print(f"posters: replaced {written}, kept {skipped} (no new image)")

    # 2. Add brand-new posters with no existing webp counterpart.
    new_only = {
        "fortnite":          "game-fortnite.jpg",
        "tower_of_fantasy":  "game-tof.jpg",
    }
    for poster, src_name in new_only.items():
        src = os.path.join(NEW_DIR, src_name)
        if os.path.isfile(src):
            dst_jpg = os.path.join(POSTERS_DIR, poster + ".jpg")
            if not os.path.isfile(dst_jpg):
                shutil.copyfile(src, dst_jpg)
                print(f"posters: added {poster}.jpg (new title)")

    # 3. Replace static/img/games/web/*.jpg
    web_replacements = {
        "game-mlbb.jpg":     "Mobile_Legends_Bang_Bang.jpg",
        "game-pubg.jpg":     "PUBG_Mobile.jpg",
        "game-genshin.jpg":  "Genshin_Impact.jpg",
        "game-valorant.jpg": "Valorant.jpg",
        "game-freefire.jpg": "Free_Fire.jpg",
        "game-lol.jpg":      "League_of_Legends.jpg",
        "game-honkai.jpg":   "Honkai_Star_Rail.jpg",
    }
    os.makedirs(WEB_DIR, exist_ok=True)
    web_done = 0
    for tgt, src_name in web_replacements.items():
        src = os.path.join(NEW_DIR, src_name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(WEB_DIR, tgt))
            web_done += 1
    print(f"games/web: replaced {web_done} files")

    # 4. Replace static/img/games-neon/*.jpg
    neon_replacements = {
        "hero-gaming.jpg":   "hero-gaming.jpg",
        "game-cod.jpg":      "game-cod.jpg",
        "game-fortnite.jpg": "game-fortnite.jpg",
        "game-freefire.jpg": "game-freefire.jpg",
        "game-genshin.jpg":  "game-genshin.jpg",
        "game-mlbb.jpg":     "game-mlbb.jpg",
        "game-pubg.jpg":     "game-pubg.jpg",
    }
    os.makedirs(NEON_DIR, exist_ok=True)
    neon_done = 0
    for tgt, src_name in neon_replacements.items():
        src = os.path.join(NEW_DIR, src_name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(NEON_DIR, tgt))
            neon_done += 1
    print(f"games-neon: replaced {neon_done} files")
    print("Done.")


if __name__ == "__main__":
    main()
