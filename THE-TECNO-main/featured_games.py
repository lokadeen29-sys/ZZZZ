"""V45: Static "Popular games" featured grid for the homepage.

This intentionally bypasses the database for the homepage hero grid so the
landing page renders instantly with hand-picked artwork (matches the new
NEONTOPUP design reference). Each entry can be tied to a real game in the
DB via `provider` + `game_key` so clicks still go to the real product page.
"""

FEATURED_GAMES = [
    {
        "title": "Mobile Legends",
        "subtitle_ar": "جواهر",
        "subtitle_en": "Diamonds",
        "image": "img/games/mobile_legends.jpg",
        "badge": "hot",
        "discount": "12% OFF",
        "provider": "server1",
        "game_key": "mobile_legends",
    },
    {
        "title": "PUBG Mobile",
        "subtitle_ar": "UC",
        "subtitle_en": "UC",
        "image": "img/games/pubg_mobile.jpg",
        "badge": "hot",
        "discount": None,
        "provider": "server1",
        "game_key": "pubg_mobile",
    },
    {
        "title": "Genshin Impact",
        "subtitle_ar": "بلورات أصلية",
        "subtitle_en": "Genesis Crystals",
        "image": "img/games/genshin_impact.jpg",
        "badge": None,
        "discount": None,
        "provider": "server1",
        "game_key": "genshin_impact",
    },
    {
        "title": "Valorant",
        "subtitle_ar": "نقاط VP",
        "subtitle_en": "VP Points",
        "image": "img/games/valorant.jpg",
        "badge": "hot",
        "discount": None,
        "provider": "server1",
        "game_key": "valorant_sg",
    },
    {
        "title": "Free Fire",
        "subtitle_ar": "جواهر",
        "subtitle_en": "Diamonds",
        "image": "img/games/free_fire.jpg",
        "badge": None,
        "discount": "8% OFF",
        "provider": "server1",
        "game_key": "freefire",
    },
    {
        "title": "League of Legends",
        "subtitle_ar": "RP",
        "subtitle_en": "RP",
        "image": "img/games/league_of_legends.jpg",
        "badge": None,
        "discount": None,
        "provider": "server1",
        "game_key": "league_of_legends_sg",
    },
    {
        "title": "Honkai: Star Rail",
        "subtitle_ar": "Oneiric Shards",
        "subtitle_en": "Oneiric Shards",
        "image": "img/games/honkai_star_rail.jpg",
        "badge": "hot",
        "discount": None,
        "provider": "server1",
        "game_key": "honkai_star_rail",
    },
    {
        "title": "Roblox",
        "subtitle_ar": "Robux",
        "subtitle_en": "Robux",
        "image": "img/games/web/game-roblox.jpg",
        "badge": None,
        "discount": "10% OFF",
        "provider": "server1",
        "game_key": "roblox",
    },
]
