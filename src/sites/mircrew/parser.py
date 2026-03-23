"""Funzioni di parsing per MIRCrew: titoli, magnets, episodi, media tags."""

import re
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, unquote

from bs4 import BeautifulSoup

from .constants import TV_FORUM_IDS


# === TITLE PARSING ===

def extract_season_from_title(title: str) -> Optional[int]:
    """Estrae numero stagione dal titolo thread."""
    patterns = [
        r'[Ss]tagione?\s*(\d+)',
        r'[Ss]eason\s*(\d+)',
        r'\b[Ss](\d{1,2})\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            return int(match.group(1))
    return None


def is_multi_season_title(title: str) -> bool:
    """Verifica se il titolo indica multiple stagioni."""
    patterns = [
        r'[Ss]tagion[ei]\s*\d+\s*[-–]\s*\d+',
        r'[Ss]\d+\s*[-–]\s*[Ss]?\d+',
        r'[Ss]eason\s*\d+\s*[-–]\s*\d+',
    ]
    for pattern in patterns:
        if re.search(pattern, title, re.I):
            return True
    return False


def extract_season_from_query(query: str) -> Optional[int]:
    """Estrae stagione dalla query di ricerca."""
    patterns = [
        r'[Ss](\d{1,2})[Ee]',
        r'[Ss]tagione?\s*(\d+)',
        r'[Ss]eason\s*(\d+)',
        r'\b(\d{1,2})[xX]\d',
    ]
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return int(match.group(1))
    return None


def extract_episode_from_query(query: str) -> Optional[int]:
    """Estrae episodio dalla query di ricerca."""
    patterns = [
        r'[Ss]\d{1,2}[Ee](\d{1,3})',
        r'[Ee]pisod[eio]+\s*(\d+)',
        r'\d{1,2}[xX](\d{1,3})',
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.I)
        if match:
            return int(match.group(1))
    return None


def title_matches_season(title: str, target_season: int) -> bool:
    """Verifica se il titolo corrisponde alla stagione cercata."""
    title_season = extract_season_from_title(title)
    if title_season is None:
        return True
    return title_season == target_season


def extract_episode_count_from_title(title: str) -> Optional[int]:
    """Estrae il numero di episodi disponibili dal titolo."""
    patterns = [
        r'\[IN CORSO[^\]]*?(\d+)/\d+\]',
        r'\[(\d+)/\d+\]',
        r'\((\d+)/\d+\)',
        r'\[IN CORSO\]\s*\[(\d+)/\d+\]',
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.I)
        if match:
            return int(match.group(1))
    if re.search(r'\[COMPLET[AEO]\]', title, re.I):
        return None
    return None


def generate_show_name_from_title(title: str) -> str:
    """Estrae il nome della serie dal titolo del thread."""
    name = re.split(r'\s*[-–]\s*[Ss]tagion', title)[0]
    name = re.sub(r'\s*\(\d{4}\)\s*', ' ', name)
    name = name.strip(' -–')
    return name


def extract_media_tags_from_title(title: str) -> str:
    """Estrae tag tecnici (risoluzione, codec, lingua, audio, sottotitoli) dal titolo."""
    tags = []
    upper = title.upper()

    # Risoluzione
    for pattern in [r'\b(2160[pP])\b', r'\b(4[kK])\b', r'\b(1080[pP])\b', r'\b(720[pP])\b', r'\b(480[pP])\b', r'\b(SD)\b']:
        m = re.search(pattern, title, re.IGNORECASE)
        if m:
            val = m.group(1).upper()
            if val == '4K':
                val = '2160p'
            tags.append(val)
            break

    # Codec
    for pattern in [r'\b(H\.?265|HEVC|[xX]265)\b', r'\b(H\.?264|AVC|[xX]264)\b', r'\b(AV1)\b']:
        m = re.search(pattern, title, re.IGNORECASE)
        if m:
            val = m.group(1).upper().replace('.', '')
            tags.append(val)
            break

    # Lingue
    lang_patterns = [
        (r'\bITA\b', 'ITA'), (r'\bENG\b', 'ENG'),
        (r'\b(?:JAP|JPN|JP)\b', 'JAP'), (r'\b(?:FRA|FR)\b', 'FRA'),
        (r'\b(?:SPA|ESP)\b', 'SPA'), (r'\b(?:GER|DEU)\b', 'GER'),
        (r'\bKOR\b', 'KOR'),
    ]
    for pattern, label in lang_patterns:
        if re.search(pattern, upper):
            tags.append(label)

    # Audio
    audio_patterns = [
        (r'\bATMOS\b', 'ATMOS'), (r'\bTRUEHD\b', 'TRUEHD'),
        (r'\bDTS[\s-]?HD\b', 'DTS-HD'), (r'\bDTS\b', 'DTS'),
        (r'\bEAC3\b', 'EAC3'), (r'\bAC3\b', 'AC3'),
        (r'\bAAC\b', 'AAC'), (r'\bFLAC\b', 'FLAC'),
        (r'\b7\.1\b', '7.1'), (r'\b5\.1\b', '5.1'), (r'\b2\.0\b', '2.0'),
    ]
    for pattern, label in audio_patterns:
        if re.search(pattern, upper):
            tags.append(label)

    # Sottotitoli
    sub_patterns = [
        (r'\bMULTISUB\b', 'MULTISUB'),
        (r'\bSUB[\s-]?ITA\b', 'SUB-ITA'), (r'\bSUB[\s-]?ENG\b', 'SUB-ENG'),
        (r'\bSOFTSUB\b', 'SOFTSUB'), (r'\bHARDSUB\b', 'HARDSUB'),
    ]
    for pattern, label in sub_patterns:
        if re.search(pattern, upper):
            tags.append(label)

    return ' '.join(tags)


def restore_italian_apostrophes(text: str) -> str:
    """Ripristina apostrofi italiani rimossi dal client (es. Lagente → L'agente)."""
    def _fix(m):
        return m.group(1) + "'" + m.group(2)
    # Prefissi lunghi (Dell', Nell', Sull', Dall', All') + vocale
    text = re.sub(r'\b(Dell|Nell|Sull|Dall|All)([aeiouAEIOU]\w+)\b', _fix, text, flags=re.IGNORECASE)
    # Prefissi corti (L', D', Un') + vocale, min 3 char dopo per evitare falsi positivi
    text = re.sub(r'\b(L|D|Un)([aeiouAEIOU]\w{2,})\b', _fix, text, flags=re.IGNORECASE)
    return text


def normalize_search_query(query: str) -> str:
    """Normalizza la query di ricerca per migliorare il match su MIRCrew."""
    q = re.sub(r'\b(19|20)\d{2}\b', '', query)
    q = re.sub(r'\b[Ss]\d{1,2}[Ee]\d{1,3}\b', '', q)
    q = re.sub(r'\b\d{1,2}[xX]\d{1,3}\b', '', q)
    q = restore_italian_apostrophes(q)
    q = re.sub(r'\s+', ' ', q).strip()
    return q


def extract_year_from_query(query: str) -> Optional[int]:
    """Estrae un anno (1900-2099) dalla query di ricerca."""
    match = re.search(r'\b(19|20)\d{2}\b', query)
    return int(match.group(0)) if match else None


def compute_relevance_score(title: str, normalized_query: str, original_query: str) -> float:
    """Calcola uno score di rilevanza (0.0-1.0) per ordinare i risultati.

    Criteri:
    - Substring match esatto della query nel titolo: +0.5
    - Rapporto parole della query trovate nel titolo: +0.3 * ratio
    - Bonus posizione (match all'inizio del titolo vale di più): +0.1
    - Match anno (se la query originale contiene un anno presente nel titolo): +0.1
    """
    score = 0.0
    title_lower = title.lower()
    query_lower = normalized_query.lower().strip()

    if not query_lower:
        return 0.0

    # 1. Exact substring match
    pos = title_lower.find(query_lower)
    if pos >= 0:
        score += 0.5
        # 3. Position bonus (earlier = better)
        score += 0.1 * max(0.0, 1.0 - pos / max(len(title_lower), 1))
    else:
        # 2. Word overlap ratio
        query_words = set(query_lower.split())
        title_words = set(title_lower.split())
        if query_words:
            overlap = len(query_words & title_words)
            score += 0.3 * (overlap / len(query_words))
            # Partial position bonus for first matching word
            for w in query_words:
                idx = title_lower.find(w)
                if idx >= 0:
                    score += 0.1 * max(0.0, 1.0 - idx / max(len(title_lower), 1))
                    break

    # 4. Year match
    year = extract_year_from_query(original_query)
    if year and str(year) in title:
        score += 0.1

    return min(score, 1.0)


# === URL/PARSING HELPERS ===

def clean_url(url: str, base_url: str) -> str:
    """Pulisce URL rimuovendo parametri non necessari."""
    url = unquote(url)
    url = re.sub(r'&hilit=[^&]*', '', url)
    url = re.sub(r'&sid=[^&]*', '', url)
    if not url.startswith('http'):
        url = urljoin(base_url, url)
    return url


def get_topic_id(url: str) -> Optional[str]:
    match = re.search(r'[?&]t=(\d+)', url)
    return match.group(1) if match else None


def get_post_id(url: str) -> Optional[str]:
    match = re.search(r'[?&]p=(\d+)', url)
    return match.group(1) if match else None


def get_infohash(magnet: str) -> Optional[str]:
    match = re.search(r'btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet)
    return match.group(1).upper() if match else None


def extract_size_from_text(text: str) -> int:
    patterns = [
        r'File\s*size\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Dimensione\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Size\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Filesize\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Peso\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return parse_size(match.group(1))
    match = re.search(r'\b([\d.,]+)\s*([KMGTP]i?[Bb])\b', text, re.I)
    if match:
        return parse_size(match.group(0))
    return 0


def parse_size(size_str: str) -> int:
    if not size_str:
        return 0
    size_str = size_str.upper().replace(',', '.').strip()
    parts = size_str.split('.')
    if len(parts) > 2:
        size_str = ''.join(parts[:-1]) + '.' + parts[-1]
    match = re.search(r'([\d.]+)\s*([KMGTP])?I?B?', size_str)
    if not match:
        return 0
    try:
        num = float(match.group(1))
    except ValueError:
        return 0
    unit = match.group(2) or 'M'
    mult = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4, 'P': 1024**5}
    return int(num * mult.get(unit, 1024**2))


def get_default_size(forum_id: int, title: str, tv_forum_ids=None) -> int:
    if tv_forum_ids is None:
        tv_forum_ids = TV_FORUM_IDS
    is_4k = bool(re.search(r'\b(2160p|4K|UHD)\b', title, re.I))
    if forum_id in [25, 26, 34, 36]:
        return 15*1024**3 if is_4k else 10*1024**3
    elif forum_id in tv_forum_ids:
        return 5*1024**3 if is_4k else 2*1024**3
    return 512*1024**2


# === EPISODE/PACK PARSING ===

def extract_episode_info(text: str) -> Optional[Dict[str, Any]]:
    """Estrae info episodio dal nome del magnet."""
    patterns = [
        r'[Ss](\d{1,2})[\.\s]?[Ee](\d{1,3})(?:-[Ee]?(\d{1,3}))?',
        r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?',
        r'[Ss]tagion[ei]\s*(\d{1,2}).*?[Ee]pisodio\s*(\d{1,3})',
        r'[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            return {
                'season': int(groups[0]),
                'episode': int(groups[1]),
                'episode_end': int(groups[2]) if len(groups) > 2 and groups[2] else None,
            }
    return None


def extract_pack_info(text: str) -> Optional[Dict[str, Any]]:
    """Rileva se il nome indica un pack di stagione/i."""
    if extract_episode_info(text):
        return None

    multi_patterns = [
        r'[Ss](\d{1,2})\s*[-–]\s*[Ss]?(\d{1,2})',
        r'[Ss]tagion[ei]\s*(\d{1,2})\s*[-–]\s*(\d{1,2})',
        r'[Ss]eason[s]?\s*(\d{1,2})\s*[-–]\s*(\d{1,2})',
    ]
    for pattern in multi_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return {
                'season_start': int(match.group(1)),
                'season_end': int(match.group(2)),
                'is_pack': True,
            }

    single_pack_patterns = [
        r'[Ss](\d{1,2})\s*[.-]?\s*[Cc]omplet[ae]',
        r'[Ss]tagion[ei]\s*(\d{1,2})\s*[Cc]omplet[ae]',
        r'[Ss]eason\s*(\d{1,2})\s*[Cc]omplete',
        r'[Cc]omplet[ae]\s*[Ss]tagion[ei]\s*(\d{1,2})',
        r'[Cc]omplete\s*[Ss]eason\s*(\d{1,2})',
    ]
    for pattern in single_pack_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return {
                'season': int(match.group(1)),
                'is_pack': True,
            }

    season_only = re.search(r'[Ss](\d{1,2})(?:\.|$|\s)(?!E\d)', text)
    if season_only:
        if re.search(r'\b(1080p|720p|2160p|4K|UHD|WEB-?DL|BluRay|HDTV)\b', text, re.I):
            return {
                'season': int(season_only.group(1)),
                'is_pack': True,
                'uncertain': True,
            }
    return None


def extract_name_from_magnet(magnet: str) -> str:
    match = re.search(r'dn=([^&]+)', magnet)
    return unquote(match.group(1)).replace('+', ' ') if match else ""


# === MAGNET EXTRACTION ===

def extract_magnets_from_soup(soup: BeautifulSoup, html: str,
                              post_content_selector: str = None) -> List[Dict[str, Any]]:
    """Estrae magnets dal contenuto HTML."""
    results = []

    first_post = soup.select_one(post_content_selector or "div.post div.content")
    if not first_post:
        return []

    post_text = first_post.get_text()
    default_size = extract_size_from_text(post_text)

    magnet_links = first_post.find_all("a", href=lambda x: x and str(x).startswith("magnet:"))

    if not magnet_links:
        magnets_raw = re.findall(r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\s"\'<>]*', html)
        magnets_raw += re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z2-7]{32}[^\s"\'<>]*', html)
        for m in magnets_raw:
            magnet = re.sub(r'\s+', '', m)
            infohash = get_infohash(magnet)
            if not infohash:
                continue
            name = extract_name_from_magnet(magnet)
            episode_info = extract_episode_info(name)
            pack_info = extract_pack_info(name) if not episode_info else None
            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": default_size,
                "episode_info": episode_info,
                "pack_info": pack_info,
            })
    else:
        for link in magnet_links:
            magnet = re.sub(r'\s+', '', link.get("href", ""))
            infohash = get_infohash(magnet)
            if not infohash:
                continue
            name = extract_name_from_magnet(magnet) or link.get_text(strip=True)
            episode_info = extract_episode_info(name)
            pack_info = extract_pack_info(name) if not episode_info else None
            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": default_size,
                "episode_info": episode_info,
                "pack_info": pack_info,
            })

    # Dedup
    seen = set()
    unique = []
    for r in results:
        if r["infohash"] not in seen:
            seen.add(r["infohash"])
            unique.append(r)
    return unique
