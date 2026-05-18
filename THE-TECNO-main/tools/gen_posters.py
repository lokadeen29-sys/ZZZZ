"""Generate Neon Cyberpunk posters for top games using Lovable AI Gateway."""
import os, json, base64, re, time, sys, urllib.request, urllib.error
from io import BytesIO
from PIL import Image

API_KEY = os.environ.get("LOVABLE_API_KEY")
assert API_KEY, "LOVABLE_API_KEY required"
MODEL = "google/gemini-3.1-flash-image-preview"
URL = "https://ai.gateway.lovable.dev/v1/chat/completions"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "img", "games")
os.makedirs(OUT_DIR, exist_ok=True)

GAMES = [
    "AFK Journey",
    "Acecraft / AceCraft",
    "Age of Empires Mobile",
    "Age of Magic",
    "Arena Breakout Infinite",
    "Arena of Valor",
    "Arknights: Endfield / Arknight: Endfield",
    "Asphalt 9: Legends",
    "Azur Lane",
    "Bigo Live Diamonds",
    "Black Clover M",
    "Bleach: Soul Resonance",
    "Blockman Go",
    "Blood Strike",
    "Bullet Echo",
    "CATS: Crash Arena Turbo Stars",
    "Call of Duty Mobile",
    "Civilization: Eras & Allies",
    "Crossfire: Legend / CrossFire Mobile",
    "Crossout Mobile",
    "Crystal of Atlan",
    "Deadly Dudes",
    "Delta Force",
    "Destiny Rising / Destiny: Rising",
    "Devil May Cry: Peak of Combat",
    "Dragon Nest M Classic",
    "Dragon Raja",
    "Dragonheir: Silent Gods",
    "Duet Night Abyss",
    "Dunk City Dynasty",
    "EA FC Mobile / EAFC 24 / FC Mobile",
    "EVE Echoes",
    "Echocalypse: Scarlet Covenant",
    "Eggy Party",
    "Enhypen World",
    "Etheria: Restart",
    "FRAG Pro Shooter",
    "Farlight 84",
    "Garena Undawn / Undawn Global",
    "Ghost Story Love Destiny",
    "Growtopia",
    "HAIKYU!! FLY HIGH",
    "Harry Potter: Magic Awakened",
    "Hatsune Miku",
    "Heartopia",
    "Heaven Burns Red",
    "Hero Clash",
    "Honkai Impact 3rd",
    "Kings Choice SEA",
    "Kingshot",
    "Knives Out",
    "Legend of Neverland",
    "Legend of the Phoenix",
    "Legends of Runeterra",
    "Life Makeover Global",
    "LifeAfter",
    "Likee",
    "Lineage2M",
    "Lord of the Rings: Rise to War",
    "Love Nikki",
    "Love and Deepspace",
    "Magic Chess: Go Go",
    "MapleStory M",
    "Marvel Duel",
    "Marvel Mystic Mayhem",
    "Mecha BREAK / Mecha Break",
    "Metal Slug Awakening",
    "Mobile Legends: Adventure",
    "Mobile Legends: Bang Bang (Multiple Regions)",
    "Modern Strike Online",
    "Moonlight Blade M",
    "My Singing Monsters",
    "Once Human",
    "Onmyoji Arena",
    "Overmortal Idle",
    "Oxide: Survival Island",
    "PUBG Mobile",
    "Path to Nowhere",
    "Poppo Live",
    "Project Entropy",
    "Punishing Gray Raven",
    "Puzzles & Survival",
    "Racing Master",
    "Ragnarok Origin",
    "Ragnarok X: Next Generation",
    "Rainbow Six Mobile",
    "Rememento: White Shadows",
    "Revelation: Infinite Journey",
    "Sausage Man",
    "Sea of Conquest",
    "Shining Nikki",
    "Silver and Blood",
    "Sky: Children of the Light",
    "Snowbreak: Containment Zone",
    "Soul Land New World",
    "Spring Valley: Farm Adventures",
    "Star Resonance",
    "Starmaker",
    "Stormshot",
    "Super Sus",
    "Sword of Justice",
    "Tarisland",
    "Teamfight Tactics (Multiple Regions)",
    "Teen Patti Gold",
    "The Division Resurgence",
    "Tiles Survive",
    "Watcher of Realms",
    "Where Winds Meet",
    "Wild Rift (Multiple Regions)",
    "Wuthering Waves",
    "Zenless Zone Zero",
    "Zepeto",
]

def slug(name):
    # Match the slugging used by the rest of the project: drop region/edition
    # suffixes and aliases so all variants collapse to a single image file.
    name = re.split(r'[/(]', name)[0]
    name = re.sub(r'\s+(SEA|MENA|Global|Europe|Asia|Garena|Classic|Mobile|Mobile Garena)\b',
                  '', name, flags=re.I)
    s = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return s

def prompt_for(game):
    return (
        f'Vertical 3:4 game key-art poster for "{game}". Neon Cyberpunk style: '
        f'deep dark navy background (#0a0814), vivid magenta-to-cyan neon accents '
        f'(#7c3aed to #06b6d4), glowing edges, glitch grid lines, futuristic '
        f'atmosphere, dramatic cinematic lighting, rich depth, ultra detailed 4k. '
        f'NO TEXT, NO LOGO, NO WATERMARK. Pure visual artwork only.'
    )

def gen_one(game):
    out_path = os.path.join(OUT_DIR, slug(game) + ".webp")
    if os.path.exists(out_path):
        return "skip"
    body = json.dumps({
        "model": MODEL,
        "messages":[{"role":"user","content": prompt_for(game)}],
        "modalities":["image","text"],
    }).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
        headers={"Authorization":f"Bearer {API_KEY}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return f"http_{e.code}"
    except Exception as e:
        return f"err:{e}"
    try:
        msg = resp["choices"][0]["message"]
        imgs = msg.get("images") or []
        if not imgs:
            return "no_image"
        url = imgs[0]["image_url"]["url"]
        b64 = url.split(",",1)[1]
        raw = base64.b64decode(b64)
        im = Image.open(BytesIO(raw)).convert("RGB")
        # crop/resize to 600x800 (3:4)
        w,h = im.size
        target_ratio = 3/4
        cur = w/h
        if cur > target_ratio:
            new_w = int(h*target_ratio)
            x = (w-new_w)//2
            im = im.crop((x,0,x+new_w,h))
        else:
            new_h = int(w/target_ratio)
            y = (h-new_h)//2
            im = im.crop((0,y,w,y+new_h))
        im = im.resize((600,800), Image.LANCZOS)
        im.save(out_path, "WEBP", quality=82, method=6)
        return "ok"
    except Exception as e:
        return f"parse:{e}"

manifest = {}
for g in GAMES:
    s = gen_one(g)
    manifest[slug(g)] = {"name": g, "status": s}
    print(f"{s:10s} {g}", flush=True)

json.dump(manifest, open(os.path.join(os.path.dirname(__file__),"posters_manifest.json"),"w"), indent=2, ensure_ascii=False)
print("DONE", sum(1 for v in manifest.values() if v["status"]=="ok"), "ok")
