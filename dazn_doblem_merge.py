"""
Genera guide.xml combinando:
  - La guia completa de EPG_dobleM (600+ canales, 7 dias)
  - La programacion oficial del API de DAZN Espana para sus 12 canales
    en directo (mas fiable y rellena canales que dobleM tiene vacios)
  - Logos corregidos para NFL Network, Unbeaten y NHL (URLs limpias de DAZN)

Pensado para ejecutarse solo, cada dia, en GitHub Actions.
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import uuid
import gzip
import io

# ------------------------- Configuracion -------------------------

# Guia base de dobleM (version comprimida, mucho mas rapida de descargar;
# el contenido es identico al de guiatv.xml)
DOBLEM_URL = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml.gz"

DAZN_URL = "https://rail-router.discovery.indazn.com/eu/v10/Rail"
DAZN_PARAMS = {
    "platform": "web",
    "id": "Livetvschedule",
    "country": "es",
    "brand": "dazn",
    "languageCode": "es",
}
DAZN_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://www.dazn.com/",
    "X-BRAND": "dazn",
    "X-DAZNID": str(uuid.uuid4()),
    "x-session-id": str(uuid.uuid4()),
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

OUTPUT_FILE = "guide.xml"

# Molde para construir la URL de logo de DAZN a partir de su identificador
LOGO_URL = ("https://image.discovery.indazn.com/eu/v3/linear-channel/none/"
            "{logo_id}/contain/center/center/none/80/102/84/png/image?brand=dazn")

# Correspondencia: titulo del canal en el API de DAZN -> id del canal en dobleM.
# Si el valor es None, el canal no existe en dobleM y se creara nuevo
# usando el propio titulo de DAZN como id.
CHANNEL_MAP = {
    "Eurosport 1": "Eurosport 1 HD",
    "Eurosport 2": "Eurosport 2",
    "DAZN Mundial": "DAZN Mundial HD",
    "LALIGA TV HYPERMOTION": "LaLiga TV Hypermotion HD",
    "NFL Network": "NFL Network (DAZN)",
    "DAZN F1\u00ae": "DAZN F1 HD",
    "Rally TV": None,
    "Red Bull TV": "Red Bull TV (DAZN)",
    "DAZN 1": "DAZN 1 HD",
    "DAZN 2": "DAZN 2 HD",
    "Unbeaten": "Unbeaten (DAZN)",
    "NHL FAST Channel": None,
}

# Logos a corregir/forzar (id de canal en la guia final -> id de logo DAZN).
# Los tres primeros arreglan los que tu app no lee (URLs con parentesis);
# los dos ultimos son para los canales nuevos.
LOGO_FIX = {
    "NFL Network (DAZN)": "Logo_LTV_NFL-Network",
    "Unbeaten (DAZN)": "Logo_LTV_Unbeaten",
    "NHL FAST Channel": "Logo_LTV_NHL_TV",
    "Rally TV": "Logo_Rally_TV",
}

# ------------------------------------------------------------------


def parse_dazn_time(iso_str):
    """"2026-07-18T11:00:00Z" -> "20260718110000 +0000" (formato XMLTV)."""
    dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y%m%d%H%M%S +0000")


def xmltv_to_utc_key(xmltv_time):
    """Convierte "20260718110000 +0200" a una clave UTC comparable (segundos)."""
    base = datetime.strptime(xmltv_time[:14], "%Y%m%d%H%M%S")
    offset = xmltv_time[15:] if len(xmltv_time) > 15 else "+0000"
    sign = 1 if offset[0] == "+" else -1
    hours = int(offset[1:3])
    minutes = int(offset[3:5])
    return base.timestamp() - sign * (hours * 3600 + minutes * 60)


def download_doblem():
    print("Descargando guia de dobleM...")
    r = requests.get(DOBLEM_URL, timeout=120)
    r.raise_for_status()
    xml_bytes = gzip.decompress(r.content)
    print(f"  Guia descargada: {len(xml_bytes) / 1024 / 1024:.1f} MB")
    return ET.fromstring(xml_bytes)


def download_dazn():
    print("Descargando programacion del API de DAZN...")
    r = requests.get(DAZN_URL, params=DAZN_PARAMS, headers=DAZN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def dazn_programmes(tile):
    """Extrae la lista de programas (Now + Next + Later) de un canal DAZN."""
    ls = tile.get("LinearSchedule") or {}
    progs = []
    if ls.get("Now"):
        progs.append(ls["Now"])
    if ls.get("Next"):
        progs.append(ls["Next"])
    progs.extend(ls.get("Later") or [])
    # quitar duplicados exactos (Now y Next a veces repiten el mismo tramo)
    seen = set()
    unique = []
    for p in progs:
        key = (p.get("Start"), p.get("End"))
        if key not in seen and p.get("Start") and p.get("End"):
            seen.add(key)
            unique.append(p)
    return unique


def build_programme_element(prog, channel_id):
    el = ET.Element("programme", {
        "start": parse_dazn_time(prog["Start"]),
        "stop": parse_dazn_time(prog["End"]),
        "channel": channel_id,
    })
    title = ET.SubElement(el, "title", {"lang": "es"})
    title.text = prog.get("Title", "")
    if prog.get("EpisodeTitle"):
        sub = ET.SubElement(el, "sub-title", {"lang": "es"})
        sub.text = prog["EpisodeTitle"]
    if prog.get("Description"):
        desc = ET.SubElement(el, "desc", {"lang": "es"})
        desc.text = prog["Description"]
    for genre in prog.get("Genre") or []:
        if genre.get("name"):
            cat = ET.SubElement(el, "category", {"lang": "en"})
            cat.text = genre["name"]
    return el


def main():
    tv = download_doblem()
    dazn = download_dazn()

    tiles = {t["Title"]: t for t in dazn.get("Tiles", [])}
    print(f"  Canales DAZN recibidos: {len(tiles)}")

    existing_channel_ids = {c.get("id") for c in tv.findall("channel")}

    # 1) Crear canales nuevos que dobleM no tiene
    for dazn_title, doblem_id in CHANNEL_MAP.items():
        final_id = doblem_id or dazn_title
        if final_id not in existing_channel_ids:
            ch = ET.Element("channel", {"id": final_id})
            dn = ET.SubElement(ch, "display-name")
            dn.text = final_id
            tile = tiles.get(dazn_title)
            logo_id = LOGO_FIX.get(final_id) or (
                (tile.get("LogoImage") or {}).get("Id") if tile else None)
            if logo_id:
                ET.SubElement(ch, "icon", {"src": LOGO_URL.format(logo_id=logo_id)})
            tv.insert(0, ch)
            existing_channel_ids.add(final_id)
            print(f"  Canal nuevo creado: {final_id}")

    # 2) Corregir logos de los canales indicados
    for ch in tv.findall("channel"):
        cid = ch.get("id")
        if cid in LOGO_FIX:
            new_src = LOGO_URL.format(logo_id=LOGO_FIX[cid])
            icon = ch.find("icon")
            if icon is not None:
                icon.set("src", new_src)
            else:
                ET.SubElement(ch, "icon", {"src": new_src})
            print(f"  Logo corregido: {cid}")

    # 3) Inyectar programacion de DAZN.
    #    Para cada canal: se eliminan los programas de dobleM que empiecen
    #    dentro de la ventana que cubre el API de DAZN (2-3 dias), se ponen
    #    los del API, y se conservan los de dobleM posteriores (dias 3-7).
    for dazn_title, doblem_id in CHANNEL_MAP.items():
        final_id = doblem_id or dazn_title
        tile = tiles.get(dazn_title)
        if not tile:
            print(f"  AVISO: el API no devolvio el canal '{dazn_title}', se deja como esta")
            continue
        progs = dazn_programmes(tile)
        if not progs:
            print(f"  AVISO: '{dazn_title}' sin programas en el API, se deja como esta")
            continue

        window_end = max(xmltv_to_utc_key(parse_dazn_time(p["End"])) for p in progs)

        removed = 0
        for old in tv.findall(f"programme[@channel='{final_id}']"):
            if xmltv_to_utc_key(old.get("start")) < window_end:
                tv.remove(old)
                removed += 1

        for p in progs:
            tv.append(build_programme_element(p, final_id))

        print(f"  {final_id:28s} quitados={removed:3d}  anadidos={len(progs):3d}")

    # 4) Guardar
    ET.ElementTree(tv).write(OUTPUT_FILE, encoding="UTF-8", xml_declaration=True)
    n_ch = len(tv.findall("channel"))
    n_pr = len(tv.findall("programme"))
    print(f"Listo: {OUTPUT_FILE} con {n_ch} canales y {n_pr} programas")


if __name__ == "__main__":
    main()
