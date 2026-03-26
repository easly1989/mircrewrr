"""Modello risultato Torznab e utility XML."""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


LANG_NAME_MAP = {
    'ITA': 'Italian', 'ENG': 'English', 'JAP': 'Japanese',
    'FRA': 'French', 'SPA': 'Spanish', 'GER': 'German',
    'KOR': 'Korean', 'MULTI': 'Italian',
}


def escape_xml(s) -> str:
    """Escape stringa per XML."""
    if not s:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


@dataclass
class TorznabResult:
    """Risultato di ricerca Torznab."""

    title: str
    link: str
    guid: str
    pub_date: str
    size: int
    category: int
    seeders: int = 1
    peers: int = 1
    infohash: Optional[str] = None
    episode_info: Optional[Dict[str, Any]] = None
    pack_info: Optional[Dict[str, Any]] = None
    languages: List[str] = field(default_factory=list)
    # Parametri extra per costruire il download URL
    download_params: Dict[str, str] = field(default_factory=dict)

    def to_xml_item(self, download_base_url: str) -> str:
        """Genera XML <item> per RSS Torznab."""
        # Costruisci download URL
        params = "&".join(f"{k}={v}" for k, v in self.download_params.items())
        dl_url = f"{download_base_url}?{params}" if params else download_base_url

        # Attributi lingua
        lang_attrs = ""
        if self.languages:
            seen = set()
            for code in self.languages:
                name = LANG_NAME_MAP.get(code, code)
                if name not in seen:
                    seen.add(name)
                    lang_attrs += f'<torznab:attr name="language" value="{escape_xml(name)}"/>\n'
        else:
            lang_attrs = '<torznab:attr name="language" value="Italian"/>\n'

        # Attributi season/episode/pack
        season_attr = ""
        episode_attr = ""

        if self.episode_info:
            season_attr = f'<torznab:attr name="season" value="{self.episode_info["season"]}"/>'
            episode_attr = f'<torznab:attr name="episode" value="{self.episode_info["episode"]}"/>'
        elif self.pack_info:
            if self.pack_info.get("season"):
                season_attr = f'<torznab:attr name="season" value="{self.pack_info["season"]}"/>'
            elif self.pack_info.get("season_start"):
                season_attr = f'<torznab:attr name="season" value="{self.pack_info["season_start"]}"/>'

        return f'''<item>
<title>{escape_xml(self.title)}</title>
<guid>{escape_xml(self.guid)}</guid>
<link>{escape_xml(self.link)}</link>
<comments>{escape_xml(self.link)}</comments>
<pubDate>{self.pub_date}</pubDate>
<size>{self.size}</size>
<enclosure url="{escape_xml(dl_url)}" length="{self.size}" type="application/x-bittorrent"/>
<torznab:attr name="category" value="{self.category}"/>
<torznab:attr name="size" value="{self.size}"/>
<torznab:attr name="seeders" value="{self.seeders}"/>
<torznab:attr name="peers" value="{self.peers}"/>
{lang_attrs}{season_attr}
{episode_attr}
<torznab:attr name="downloadvolumefactor" value="0"/>
<torznab:attr name="uploadvolumefactor" value="1"/>
</item>'''
