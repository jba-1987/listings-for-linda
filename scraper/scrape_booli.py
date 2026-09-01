#!/usr/bin/env python3
"""
Scrapes Booli.se apartment listings for sale near a chosen point, using a real
browser (Playwright) rather than raw HTTP, since Booli's pages are Cloudflare
protected. Reads structured data out of the page's embedded Apollo/Next.js
state instead of parsing HTML.

Two-pass approach:
  1. Walk the paginated search results for the given area/room filters,
     pulling summary fields + up to 5 photos per listing (cheap, one page
     load per ~35 listings).
  2. Filter to listings within `--radius-km` of `--center-lat,--center-lng`
     (haversine distance - Booli only filters by municipality/area, not by
     an arbitrary point).
  3. For each surviving listing (up to `--max-detail`, newest first), visit
     its own detail page to pull the *complete* photo gallery and a few
     extra fields (floor, monthly fee, construction year).

Runs at a light, human-like pace (one page/listing at a time with a delay)
since this is a personal tool, not a bulk crawler.
"""
import argparse
import json
import math
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.booli.se"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def image_url(image_id, width=1200):
    return f"https://bcdn.se/images/cache/{image_id}_{width}x0.jpg"


def get_apollo_state(page):
    return page.evaluate(
        "() => window.__NEXT_DATA__?.props?.pageProps?.__APOLLO_STATE__ || null"
    )


def extract_listing_summary(listing_obj, apollo):
    images_key = next((k for k in listing_obj if k.startswith("images(")), None)
    image_ids = []
    if images_key:
        for ref in listing_obj[images_key]:
            image_ids.append(ref["__ref"].split(":")[1])

    display_key = next((k for k in listing_obj if k.startswith("displayAttributes(")), None)
    size_sqm = rooms_text = floor_text = fee_text = None
    if display_key:
        for dp in listing_obj[display_key].get("dataPoints", []):
            text = (dp["value"].get("plainText") or "").strip()
            if "m²" in text:
                size_sqm = text
            elif text.endswith("rum"):
                rooms_text = text
            elif text.startswith("vån"):
                floor_text = text
            elif "kr/mån" in text:
                fee_text = text

    return {
        "booli_id": listing_obj["booliId"],
        "url": BASE + listing_obj["url"],
        "street_address": listing_obj.get("streetAddress"),
        "area": listing_obj.get("descriptiveAreaName"),
        "municipality": (listing_obj.get("location") or {}).get("region", {}).get("municipalityName"),
        "object_type": listing_obj.get("objectType"),
        "tenure_form": listing_obj.get("tenureForm"),
        "list_price": (listing_obj.get("listPrice") or {}).get("raw"),
        "list_price_formatted": (listing_obj.get("listPrice") or {}).get("formatted"),
        "sqm_price_formatted": (listing_obj.get("listSqmPrice") or {}).get("formatted"),
        "latitude": listing_obj.get("latitude"),
        "longitude": listing_obj.get("longitude"),
        "published": listing_obj.get("published"),
        "upcoming_sale": listing_obj.get("upcomingSale"),
        "is_new_construction": listing_obj.get("isNewConstruction"),
        "size_sqm": size_sqm,
        "rooms_text": rooms_text,
        "floor_text": floor_text,
        "fee_text": fee_text,
        "photo_ids": image_ids,
    }


def fetch_search_pages(page, area_id, object_type, min_rooms, max_rooms, delay, max_pages=None):
    listings = {}
    page_num = 1
    total_pages = None
    while True:
        url = (
            f"{BASE}/sok/till-salu?areaIds={area_id}&objectType={object_type}"
            f"&minRooms={min_rooms}&maxRooms={max_rooms}&page={page_num}"
        )
        print(f"[search] page {page_num}{f'/{total_pages}' if total_pages else ''}: {url}", file=sys.stderr)
        page.goto(url, wait_until="networkidle")
        apollo = get_apollo_state(page)
        if not apollo:
            print("  no apollo state found, stopping", file=sys.stderr)
            break

        if total_pages is None:
            root = apollo.get("ROOT_QUERY", {})
            search_key = next(
                (k for k in root if k.startswith("searchForSale(") and "forceOnlyNewConstruction" not in k),
                None,
            )
            if search_key:
                total_pages = root[search_key].get("pages")

        found_on_page = 0
        for key, obj in apollo.items():
            if key.startswith("Listing:") and obj.get("objectType") == object_type:
                summary = extract_listing_summary(obj, apollo)
                listings[summary["booli_id"]] = summary
                found_on_page += 1

        print(f"  +{found_on_page} listings (total so far: {len(listings)})", file=sys.stderr)

        if total_pages and page_num >= total_pages:
            break
        if max_pages and page_num >= max_pages:
            print(f"  reached --max-pages limit ({max_pages})", file=sys.stderr)
            break
        if found_on_page == 0:
            break

        page_num += 1
        time.sleep(delay)

    return list(listings.values())


def fetch_full_photos(page, listing, delay):
    print(f"[detail] {listing['street_address']} -> {listing['url']}", file=sys.stderr)
    page.goto(listing["url"], wait_until="networkidle")
    apollo = get_apollo_state(page)
    if not apollo:
        return listing

    key = next((k for k in apollo if k.startswith("Listing:")), None)
    if not key:
        return listing
    obj = apollo[key]

    image_ids = [ref["__ref"].split(":")[1] for ref in obj.get("images", [])]
    if image_ids:
        listing["photo_ids"] = image_ids

    listing["floor"] = (obj.get("floor") or {}).get("raw")
    listing["rent_monthly"] = (obj.get("rent") or {}).get("raw")
    listing["construction_year"] = obj.get("constructionYear")
    listing["living_area_sqm"] = (obj.get("livingArea") or {}).get("raw")
    listing["rooms"] = (obj.get("rooms") or {}).get("raw")
    listing["operating_cost"] = (obj.get("operatingCost") or {}).get("raw")

    time.sleep(delay)
    return listing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--area-id", default="78", help="Booli areaId (78 = Malmö kommun)")
    ap.add_argument("--object-type", default="Lägenhet")
    ap.add_argument("--min-rooms", default="2")
    ap.add_argument("--max-rooms", default="3")
    ap.add_argument("--center-lat", type=float, default=55.60906, help="Malmö C latitude")
    ap.add_argument("--center-lng", type=float, default=13.00074, help="Malmö C longitude")
    ap.add_argument("--radius-km", type=float, default=8.0)
    ap.add_argument("--max-pages", type=int, default=None, help="cap search-result pages (debug/testing)")
    ap.add_argument("--max-detail", type=int, default=40, help="how many listings (newest first) to fetch full photo galleries for")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between page loads")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "listings.json"))
    args = ap.parse_args()
    scraped_at = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, locale="sv-SE")
        page = context.new_page()

        listings = fetch_search_pages(
            page, args.area_id, args.object_type, args.min_rooms, args.max_rooms,
            args.delay, max_pages=args.max_pages,
        )
        print(f"\n[filter] {len(listings)} total listings fetched from search", file=sys.stderr)

        for l in listings:
            if l["latitude"] is not None and l["longitude"] is not None:
                l["distance_km"] = round(
                    haversine_km(args.center_lat, args.center_lng, l["latitude"], l["longitude"]), 2
                )
            else:
                l["distance_km"] = None

        within_radius = [l for l in listings if l["distance_km"] is not None and l["distance_km"] <= args.radius_km]
        within_radius.sort(key=lambda l: l["published"] or "", reverse=True)
        print(f"[filter] {len(within_radius)} within {args.radius_km} km of ({args.center_lat}, {args.center_lng})", file=sys.stderr)

        to_detail = within_radius[: args.max_detail]
        print(f"[detail] fetching full photo galleries for {len(to_detail)} newest listings", file=sys.stderr)
        for listing in to_detail:
            fetch_full_photos(page, listing, args.delay)

        for l in within_radius:
            l["photos"] = [image_url(pid) for pid in l.get("photo_ids", [])]
            l["thumbnail"] = l["photos"][0] if l["photos"] else None

        browser.close()

    output = {
        "scraped_at": scraped_at,
        "criteria": {
            "area_id": args.area_id,
            "object_type": args.object_type,
            "min_rooms": args.min_rooms,
            "max_rooms": args.max_rooms,
            "center_lat": args.center_lat,
            "center_lng": args.center_lng,
            "radius_km": args.radius_km,
        },
        "listings": within_radius,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(within_radius)} listings to {out_path} (scraped_at={scraped_at})", file=sys.stderr)


if __name__ == "__main__":
    main()
