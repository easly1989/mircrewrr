#!/usr/bin/env python3
"""Test v4.0 - verifica espansione episodi per serie TV."""

import os
import sys

# Imposta env prima di importare proxy_server
os.environ["MIRCREW_USERNAME"] = os.getenv("MIRCREW_USERNAME", "amon2126")
os.environ["MIRCREW_PASSWORD"] = os.getenv("MIRCREW_PASSWORD", "I4vodyLwon9XjQQB")
os.environ["DATA_DIR"] = "/tmp/mircrew_test"

from proxy_server import search_mircrew, get_magnets_from_thread, BASE_URL

def test_search_masterchef():
    print("=" * 60)
    print("TEST: Ricerca MasterChef Italia 15")
    print("=" * 60)

    results = search_mircrew("masterchef italia 15")

    print(f"\n📊 Risultati totali: {len(results)}")

    # Raggruppa per topic_id
    by_topic = {}
    for r in results:
        tid = r["topic_id"]
        if tid not in by_topic:
            by_topic[tid] = []
        by_topic[tid].append(r)

    print(f"📁 Thread trovati: {len(by_topic)}")

    for topic_id, episodes in by_topic.items():
        print(f"\n🎬 Topic {topic_id}: {len(episodes)} risultati")
        for ep in episodes[:5]:  # Max 5 per topic
            ep_info = ep.get("episode_info")
            if ep_info:
                print(f"   ✅ S{ep_info['season']:02d}E{ep_info['episode']:02d} - {ep['title'][:50]}...")
                print(f"      GUID: {ep['guid']}, infohash: {ep.get('infohash', 'N/A')[:8]}...")
            else:
                print(f"   ❓ {ep['title'][:50]}...")
                print(f"      GUID: {ep['guid']}, infohash: {ep.get('infohash', 'N/A')}")

        if len(episodes) > 5:
            print(f"   ... e altri {len(episodes) - 5} episodi")

    # Verifica che E17 e E18 siano presenti
    e17 = [r for r in results if r.get("episode_info") and r["episode_info"]["episode"] == 17]
    e18 = [r for r in results if r.get("episode_info") and r["episode_info"]["episode"] == 18]

    print("\n" + "=" * 60)
    print("VERIFICA EPISODI SPECIFICI")
    print("=" * 60)

    if e17:
        print(f"✅ Episodio 17 trovato: {len(e17)} risultati")
        for r in e17:
            print(f"   - {r['title'][:60]}")
            print(f"     Download URL: /download?topic_id={r['topic_id']}&infohash={r['infohash']}")
    else:
        print("❌ Episodio 17 NON trovato!")

    if e18:
        print(f"✅ Episodio 18 trovato: {len(e18)} risultati")
        for r in e18:
            print(f"   - {r['title'][:60]}")
            print(f"     Download URL: /download?topic_id={r['topic_id']}&infohash={r['infohash']}")
    else:
        print("❌ Episodio 18 NON trovato!")

    return results


def test_download_specific_episode(results):
    """Simula download di episodio specifico."""
    print("\n" + "=" * 60)
    print("TEST: Download episodio specifico")
    print("=" * 60)

    # Prendi un risultato con infohash
    test_result = None
    for r in results:
        if r.get("infohash"):
            test_result = r
            break

    if not test_result:
        print("❌ Nessun risultato con infohash trovato!")
        return

    print(f"📥 Test download: {test_result['title'][:50]}...")
    print(f"   topic_id: {test_result['topic_id']}")
    print(f"   infohash: {test_result['infohash']}")

    # Simula il download
    url = f"{BASE_URL}/viewtopic.php?t={test_result['topic_id']}"
    magnets = get_magnets_from_thread(url)

    # Cerca il magnet esatto
    found = None
    for m in magnets:
        if m["infohash"] == test_result["infohash"]:
            found = m
            break

    if found:
        print(f"✅ Magnet trovato: {found['name'][:60]}...")
        print(f"   Size: {found['size'] / 1024**3:.2f} GB")
        if found["episode_info"]:
            print(f"   Episode: S{found['episode_info']['season']:02d}E{found['episode_info']['episode']:02d}")
    else:
        print(f"❌ Infohash {test_result['infohash']} NON trovato nel thread!")
        print(f"   Disponibili: {[m['infohash'][:8] for m in magnets]}")


if __name__ == "__main__":
    results = test_search_masterchef()
    if results:
        test_download_specific_episode(results)
