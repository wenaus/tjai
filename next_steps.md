# Next Steps

## Always-On Intellectual Agent

Transform tjai from a reactive knowledge base into a proactive thinking system that works while you're away, synthesizing your knowledge, finding deep connections, and bringing world awareness — then presenting the results when you return.

### ToDo

- AI-informed prioritized RSS reader. Inoreader has my RSS collection. Import it, and curate it overnight. priority unreads. Right now I have almost 4k unread items, accrued over less than a week. I need AI to pick out the important ones, present a priority RSS reader.
- daily synposis is in. Add daily news from tech and culture. reddit highlights in my subscribed areas and interest areas. (AI Picks system now handles the core tech/science/culture curation — extend synopsis to incorporate picks highlights)
- research queue. The Star Trek "Computer, perform an analysis" — and the computer actually performs the analysis. Real analysis, not the skimming that AIs reflexively do.
- dialog management. It is exploding tjai content. Absolutely must keep all of it. But how to manage. Also, synthesising, inferring from it, developing new plans from it.
- telegram triage. After a road trip day, capture what went into telegram. todos, ideas, research items, etc.
- E-R model. designed from real use cases.
- next steps, project planning. Make todo's actually work. Introduce vision items, with associated todos. tick off the todos as they get done. will really surface the plan much more than a next steps in github. also a good replacement/ancillary to claude's own planning system which is useless because it doesn't persist past restart. and it hides it (even from itself).

## AI ideas on agents

I don't sign on to all of them. Actual ToDos go above.

### Agent 1: Reflection Agent (the core)

Runs every few hours via cron. Selects a "seed" — a recent entry, a random older one, or a combination. Pulls related entries by context, tags, and (eventually) semantic similarity. Feeds them to Claude with a prompt:

> "Given everything you know about this person (profile, interests, knowledge base), what's interesting here? What patterns do you see? What questions does this raise? What connections to the wider world?"

Stores output as tjai entries (kind=memory, tag=reflection). Over time, the knowledge base grows not just from what the user puts in, but from what the system thinks about what the user put in.

### Agent 2: Deep Connection Finder (the gem mine)

Background batch: take each recent entry, find semantically similar entries across all contexts. Feed pairs to Claude:

> "These are from different parts of this person's life. Is there a meaningful connection?"

Most results will be noise. The hits are gold: "Your bookmark about streaming workflow orchestration at ePIC has a structural parallel to the Gary Snyder watershed poem you saved — both are about how distributed systems achieve coherence without central control."

This is where the 20x subscription token budget goes productively — paying for a system to read your own knowledge base more deeply than you have time to.

**With the relations model (see below), Agent 2 doesn't just report connections — it creates them.** Each discovered connection becomes a first-class edge in the knowledge graph: a relation with type and description capturing the insight. Over months of overnight runs, isolated entries weave into a connected structure. The brain graph builds itself while you sleep.

Requires adding embeddings to tjai entries for semantic similarity search. SQLite or Postgres vector extension, computed on entry creation/modification.

### Agent 5: Research Queue (user-initiated depth)

The other agents are system-initiated — the system decides what to think about. The research queue inverts this: the user encounters a topic that deserves real investigation and defers it to a process that can actually do the work.

The problem it solves: in real-time conversation, the AI gives reflexive, shallow, confident-sounding answers. Some questions deserve actual research — multiple sources, cross-referencing, cited evidence, explicit unknowns. The research queue is the mechanism for "Computer, perform an analysis" — and the computer actually performs the analysis.

**Relationship to bookmarks:** Bookmarks are "read later" (passive). Research items are "understand later" (active) — they come back with results. Both acknowledge that the moment of encounter isn't the right moment for depth.

### Agent 6: Dialog Synthesis (the integrator)

Overnight, review the day's captured dialog (Phase 3) in the context of recent days. Not summarizing — synthesizing. A conversation has a trajectory that's often invisible in the moment. A session that starts with a bug fix and ends with an architectural insight has a through-line the participants may not have articulated.

**What it produces:**
- **Meta-patterns**: "This week's sessions have been about moving tjai from flat storage to a self-organizing knowledge system"
- **Open questions**: implications raised but not discussed, tensions between ideas, unstated assumptions
- **New entries**: research items spawned from things mentioned but not explored, todos for follow-up work, relations between today's discussion and previous entries
- **Vision updates**: when dialog advances or reshapes a vision item, flag it
- **State of the art research**: proactive web search on the day's technical topics — who's doing what, what tools and patterns are emerging, what's changed recently. Hyperfocused overnight tech news, filtered through the day's actual work rather than generic feeds. AI moves at lightning speed; if we discussed knowledge graphs today, by morning the system has surveyed the current landscape and brought back what's relevant.

**Builds on:** Phase 3 dialog capture (already done), relations model (for linking synthesis outputs to the conversations that produced them), research queue (for spawning investigation items).

### Agent 7: Telegram Triage (the inbox processor)

Quick Telegram messages are raw intent — typed fast, no structure, no metadata. The user fires off "look into k8s operator patterns" or "remember to email jose about the beam test" and moves on. These land in =tgbot as unstructured memories. Left alone, they're write-only — captured but never acted on.

**What it does:**
- Scans recent =tgbot entries (memories created via memo command or saved from conversation)
- Classifies each: is this a todo? A research item? A watch-list item? Just a note that's fine as-is?
- **Todos**: creates a proper `kind=todo` entry with clear subject, appropriate context, tags. Marks the original =tgbot entry as processed (tag `:triaged`)
- **Tagging**: adds appropriate tags to entries — `:watch` for things to keep an eye on, `:research` for the research queue, `:idea` for nascent concepts, `:followup` for things needing action. Tagging is lightweight and additive — no entry restructuring needed, just metadata enrichment
- **Research**: entries tagged `:research` get picked up by the research queue (Agent 5)
- **Context assignment**: moves entries to their proper context when obvious (a message about ePIC → =swf, about the bot → =tjai)

**Why this matters:** The phone is the capture device. You're walking, commuting, half-awake. The barrier to entry must be zero — just type or speak. But zero-friction capture without triage is a junk drawer. This agent turns the junk drawer into a filing system, overnight, without the user ever having to go back and organize.

**Builds on:** =tgbot context entries, @watch named entry, todo system, research queue (Agent 5).

### Entry Relations (E-R Model)

tjai entries are currently isolated — no links between them. Adding relations transforms the knowledge base from a flat collection into a connected graph.

**Relations table:** `entry_id, related_id, relation_type, description`

Four columns. The description is essential — it captures the *why* of a relation, especially when discovered by AI. "Both describe distributed systems achieving coherence without central control" is the value, not just "related."

**Relation types** emerge from use rather than being predefined. Early examples:
- `parent` — vision → its todos, or any hierarchical grouping
- `related` — cross-references between entries
- `references` — a memory discussing a bookmark's content
- `prompted_by` — a research item linked to the entries that raised the question
- `discovered` — AI-found connection (Agent 2), description carries the insight

**MCP tools:** `relate_entries(id1, id2, type, description)`, `get_related(entry_id)`. Relations appear in entry responses. Queries can traverse the graph.

**dkbapp precedent:** The predecessor project was built on an E-R model — entities connected to entities, places in neighborhoods, things linked to the people who recommended them. Relations were essential to how the knowledge base was actually used. Their absence from tjai is a gap worth closing.

### Vision and Todo Structure

Project planning moves from next_steps.md (a file) to tjai (a database) for priorities, status tracking, distributed visibility, and evolution tracking.

- **`:vision` tagged todos** — the big items ("Always-On Intellectual Agent", "Location-Aware Personal Guide"). Rich content carrying the design narrative. These are the user's, not the AI's.
- **Plain todos** — work items linked to their parent vision via a `parent` relation. These are increasingly the AI's to generate and work through autonomously.
- **Design docs** stay in files — next_steps.md is a working scratchpad, docs/ is the settled reference. Vision todos are the *tracking layer*, not a replacement for documentation.

**Evolution tracking:** `get_todos(context="tjai", status="done")` gives the project's arc over time. Git history tells you what code changed; tjai tells you what was envisioned, when priorities shifted, when things completed. Searchable, sortable, filterable — no git mining required.

**Status flow:** Todos move active → done as work completes. A vision moves to done when the user decides the work is complete, not automatically. Done visions with their rich content become the historical record.


---

## Location-Aware Personal Guide

A location-aware system that combines GPS, the LLM, web search, and tjai's knowledge of the user to provide contextual place recommendations and build a personal places database through natural conversation.

### The Loop

1. User shares GPS location via Telegram (already working)
2. LLM researches interesting places ahead/nearby, filtered through user profile and preferences
3. User flags interesting ones via voice: "save that" → entry goes into tjai with lat/lon
4. Saved places inform future recommendations. The knowledge base learns your taste in places.

### Use Cases

**Road trip (driving)**
- "What's interesting in the next hour?" → LLM knows location, heading, speed. Searches ahead along route. Filters through profile.
- POI proximity alerts: Love's travel stops, rest areas, or any POI set within N miles ahead. Uses heading from Telegram live location to filter to "ahead not behind."
- Flagged places saved to tjai with coordinates, growing the places DB conversationally.

**Walking (city exploration)**
- "What's nearby worth seeing?" → checks tjai places near current location + web search + profile-based inference
- Voice suggestions while walking: "Georgia O'Keeffe gallery two blocks north"
- Send as Telegram venue (tappable pin → opens in Maps for directions)

### Implementation

**Phase 1: Places in tjai**
- Add `places` kind or use existing entries with geotagged data (lat/lon in entry.data)
- Create entry format: `entry.data = {"lat": N, "lon": N, "address": "..."}`
- Proximity query: haversine distance filter on geotagged entries
- CLI support: `tj place <name>` or flag via Telegram

**Phase 2: Proximity notifications**
- On GPS update, check places DB for entries within configurable radius
- For driving: filter by heading (±45°) and distance (e.g. 30 miles)
- For walking: radius only (e.g. 500m)
- Send via `sendVenue` (tappable pin with name/address)
- Dedup: don't re-notify same place within a trip
- Mode detection: speed from sequential GPS updates distinguishes driving vs walking

**Phase 3: LLM place research**
- On request ("what's nearby?"), LLM uses web search + user profile to find relevant places
- Knows user's interests from tjai profile entries
- Ranks by personal relevance, not generic popularity
- "Save it" voice command creates geotagged tjai entry
- Works in both driving (research ahead on route) and walking (research around current location)

**Phase 4: Mini App map**
- Telegram Mini App (web page in WebView) showing places on interactive Leaflet/Mapbox map
- Served from etaverse.com, reads from tjai API
- Button in chat opens full-screen map with all saved places as markers
- Current location shown on same map
- Filter by context, tags, proximity

### Curated Place Databases for Import

The goal is databases curated by people with taste and specific sensibilities - not generic tourist/commercial POI data. Ranked by curation quality and importability.

#### Top Tier

**Atlas Obscura** (~32K unusual/hidden/curious places worldwide)
- Community-submitted, editorially reviewed. Highest curation quality for "kindred spirits" sensibility.
- All places have lat/lon coordinates, descriptions, tags, rarity rankings.
- No official API but multiple unofficial scrapers:
  - [bartholomej/atlas-obscura-api](https://github.com/bartholomej/atlas-obscura-api) - JS/NPM, methods include `placesAll()`, `search({lat, lng})`, `placeFull(id)`
  - [csshen/atlas-obscura-api](https://github.com/csshen/atlas-obscura-api) - Flask/Python
- `placesAll()` returns all ~32K places with id, lat, lng in a single call. JSON format.
- **Recommended first import.** One scrape → 32K geotagged entries ready for tjai.

**Wikidata SPARQL** (millions of items, filtered to thousands by your taste)
- The power tool. You define your taste via queries. All Art Deco buildings with coordinates? All lighthouses? All astronomical observatories? All brutalist architecture? One SPARQL query, export CSV.
- Query at [query.wikidata.org](https://query.wikidata.org). Export as JSON, CSV, TSV, GeoJSON.
- Example: all Art Deco buildings worldwide with coordinates:
  ```sparql
  SELECT ?item ?itemLabel ?coord WHERE {
    ?item wdt:P149 wd:Q131681 .   # architectural style = Art Deco
    ?item wdt:P625 ?coord .        # has coordinates
    SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
  }
  ```
- Encyclopedic quality; your queries make it personal.

**Spotted by Locals** (~80 cities, ~2-4K places)
- Handpicked local residents writing about their own cities. Exactly the right curation sensibility.
- No API - would need scraping + geocoding addresses. Small enough to be manageable.
- [spottedbylocals.com](https://www.spottedbylocals.com/)

#### Practical and Easy

**UNESCO World Heritage** (1,248 sites)
- Gold standard curation. CSV download from [UNESCO DataHub](https://data.unesco.org/explore/dataset/whc001/). Also on Kaggle.
- Fields: name, country, category, criteria, year, description, lat, lon, area.
- Trivial import. Small, high-quality foundation layer.

**OpenStreetMap Overpass** (millions, filtered by tag)
- Query for specific categories matching your interests:
  - `historic=ruins`, `historic=castle`, `historic=archaeological_site`
  - `tourism=artwork`, `tourism=viewpoint`, `tourism=museum`
  - `man_made=lighthouse`, `man_made=windmill`
  - `building=cathedral`
- Use [Overpass Turbo](https://overpass-turbo.eu/) for interactive queries, Overpass API for programmatic access.
- Returns GeoJSON with coordinates. You pick the categories that match your sensibilities.

**WikiVoyage** (tens of thousands of POI listings)
- Open travel guide with structured listings (See/Do/Eat with coordinates).
- [DBvoyage](https://github.com/kwh44/dbvoyage) extracts structured data (1.7M semantic triples).
- MediaWiki API supports GeoSearch near coordinates.
- More "practical traveler" than "curiosity-driven explorer."

#### Niche

**iOverlander** (tens of thousands, road trip focused)
- Campsites, fuel, water, wild camping, border crossings, mechanics.
- Export as KML/GPX/CSV (subscription required). All include lat/lon.
- High quality for overlanding. Community-verified with last-verified dates.

**Carte-Urbex** (carte-urbex.com) - Abandoned places with GPS coordinates. Enthusiast-curated urban exploration.

**Google My Maps** - Individual curated maps exportable as KML. Finding the good ones is the challenge. Search `site:google.com/maps/d "hidden gems"`.

#### Not Worth It (generic/commercial)

- Foursquare OS Places (100M generic POIs, every chain restaurant)
- Overture Maps (same problem - massive and generic)
- Reddit (great taste, no structure - would need NLP + geocoding pipeline)
- Yelp/TripAdvisor/Google Maps (pleases everyone, therefore no one)

#### Recommended First Imports

1. **Atlas Obscura full scrape** → ~32K curated places with minimal effort
2. **Wikidata SPARQL queries** for specific architectural/cultural interests
3. **UNESCO World Heritage CSV** → 1,248 sites, trivial import
4. **OSM Overpass thematic queries** for categories that match personal taste

All import as: parse JSON/CSV/GeoJSON → create tjai entries with `data={"lat": N, "lon": N}`, context=places or context=poi.

### Telegram API Methods

- `sendVenue(lat, lon, title, address)` - tappable pin opening in Maps
- `sendLocation(lat, lon)` - map pin
- Live location updates include `heading` (1-360°) for direction filtering
- Mini App via `InlineKeyboardButton(web_app=WebAppInfo(url=...))` for multi-pin map

### Art-Informed Place Discovery

The art collection in `primus/art/` (630 works, 180+ artists) defines a clear taste profile that should drive museum and gallery recommendations. The LLM should know this profile when suggesting places.

#### Taste Profile (from collection analysis)

**Core artists (by collection depth):**
- Degas (62), Cezanne (61), Mucha (54), Klimt (48), Schiele (42), Van Gogh (37), Mapplethorpe (29), Toulouse-Lautrec (22), Macke (16), Crewdson (14), Bouguereau (14), Botticelli (14), de Lempicka (12), Redon (11), Cassatt (10), Ansel Adams (10)

**Periods & movements:**
- Heaviest: 1870-1920 (Impressionism through early Modernism)
- Strong: Art Nouveau, Austrian Expressionism, fine art photography
- Represented: Renaissance, Baroque, Romanticism, Symbolism, Cubism, Surrealism, Art Deco

**Aesthetic sensibility:** Technical mastery, figural art (especially ballet/female subjects), decorative/ornamental beauty, European tradition, museum-quality fine art. Both classical representation and symbolic/interpretive approaches.

#### Wikidata Queries for Art-Related Places

Museums holding works by collected artists:
```sparql
SELECT ?museum ?museumLabel ?coord ?collectionLabel WHERE {
  ?painting wdt:P170 wd:Q46373 .    # creator = Edgar Degas
  ?painting wdt:P195 ?museum .       # collection (museum)
  ?museum wdt:P625 ?coord .          # museum has coordinates
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
```
Replace Degas entity (Q46373) with any artist. Key artist Wikidata IDs:
- Degas: Q46373, Cezanne: Q35548, Mucha: Q147837, Klimt: Q34661
- Schiele: Q44032, Van Gogh: Q5582, Toulouse-Lautrec: Q82445
- Botticelli: Q5669, Mapplethorpe: Q365737, de Lempicka: Q230570

All Art Nouveau buildings with coordinates:
```sparql
SELECT ?item ?itemLabel ?coord WHERE {
  ?item wdt:P149 wd:Q34636 .   # architectural style = Art Nouveau
  ?item wdt:P625 ?coord .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
```

#### Key Museums by Alignment with Collection

**Essential (deep holdings in core interests):**
- Musée d'Orsay, Paris (Impressionism, 19th century core)
- Neue Galerie, New York (Klimt, Schiele - Austrian/German Expressionism)
- Leopold Museum, Vienna (world's largest Schiele collection)
- Mucha Museum, Prague
- Belvedere, Vienna (Klimt's "The Kiss" and extensive Austrian Modernism)

**Strong alignment:**
- Art Institute of Chicago (major Impressionism holdings)
- Musée de l'Orangerie, Paris (Monet water lilies, Cezanne, Renoir)
- Van Gogh Museum, Amsterdam
- Kröller-Müller Museum, Otterlo (Van Gogh, Mondrian)
- National Gallery, London (European masters breadth)
- Kunsthistorisches Museum, Vienna

**Photography:**
- Getty Center, Los Angeles (Mapplethorpe, Adams)
- International Center of Photography, New York
- SFMOMA (strong photography collection)

#### Integration with LLM Place Research

When the user asks "what's interesting nearby?" or is driving through a region, the LLM should:
1. Know the taste profile above (add to AI guidance or system prompt)
2. Cross-reference nearby museums/galleries against collected artists and movements
3. Prioritize lesser-known venues over obvious tourist destinations
4. Note specific works: "The Neue Galerie has Klimt's Adele Bloch-Bauer I - 15 minutes off your route"

The art collection metadata at `primus/art-metadata.txt` and `primus/artist-metadata.txt` could be used to build a tjai AI guidance entry summarizing the taste profile, so the Telegram bot's Claude instance knows the user's art interests without needing to read the full collection each time.

### dkbapp Data Recovery: 5,400 Personally Curated Places

The predecessor project **dkbapp** (https://github.com/wenaus/dkbapp), built ~2014-2018, was a full-stack Node.js/Express knowledge base app. Its primary deployed instance was **Mappeteer** - a map-centric personal knowledge base for curating places. Uses MySQL backend with Leaflet maps, mobile-optimized browser UI.

#### Recoverable Data

**`data/TorrePlaces.geojson`** (3.6MB) - **5,298 personally curated places + 100 regions**
- Global coverage: NYC, London, LA, and many other cities
- Breakdown: 2,998 restaurants, 526 sights, 334 bars, 264 culture, 241 beer, 205 food, 128 hotels, 115 wine, 112 cafes, 95 shopping, 84 music, 58 bookstores, 58 galleries, 56 info
- Fields: name, coordinates [lon, lat], category type, description, review URLs
- Standard GeoJSON, directly parseable

**`data/MirandaPlaces.geojson`** (98KB) - **138 London places**
- Shopping (31), Food (29), Restaurant (23), Culture (13), Bookshop (10), Cafe (10), Sights (7), Beer (6), Wine (5), Bar (3)
- Same fields as above with richer descriptions

**`data/mykb-dump.sql`** (56MB) - Full MySQL dump of the MyKB module (markdown notes/documents). May contain additional content worth examining.

#### GeoJSON Format

```json
{
  "name": "Houseman",
  "type": "restaurant",
  "description": "a restaurant for grown-ups. friendly service, great food. mustsee",
  "review url": "http://housemanrestaurant.com/ http://www.theinfatuation.com/...",
  "coordinates": [-74.009344, 40.725746]
}
```

Note: GeoJSON convention is `[longitude, latitude]` - reversed from typical lat/lon.

#### Import Script

Parse GeoJSON → create tjai entries:
- `kind`: `place` (new kind) or `bookmark` with geotagging
- `content`: place name
- `data`: `{"lat": N, "lon": N, "type": "restaurant", "description": "...", "urls": [...]}`
- `context`: `places` or by city
- `tags`: category type (restaurant, sights, culture, etc.)

One script, ~50 lines of Python, using tjai's Django ORM or REST API.

#### 100 Region Polygons

TorrePlaces.geojson includes 100 polygon regions (NYC neighborhoods, city boundaries, etc.) that could serve as organizational groupings for proximity queries. Store as entries with polygon geometry in data field.

#### dkbapp Entity Model (for reference)

The original entity had rich fields worth preserving where populated:
- `ename`, `nickname` - names
- `lat`, `lng` - coordinates
- `etype` (place, food, person, etc.), `subtype` (restaurant, cafe, sights, etc.)
- `description`, `content` (markdown/HTML)
- `attributes` (links, images), `tags`, `url`
- `json` - flexible JSON data
- Entity-relation system connecting places to each other

Entity IDs were formatted as `{name_normalized}@{lat:.3f},{lng:.3f}`.

#### Lessons and Takeaways from dkbapp for tjai

**What worked well in dkbapp:**
- GeoJSON as the interchange format - standard, portable, tooling everywhere
- Category/subtype system (restaurant, sights, culture, etc.) - good granularity for filtering
- Entity-relation model connecting places to each other and to regions
- Review URLs attached to places - links to the source of the recommendation
- Mobile-optimized browser UI with Leaflet - proven approach, reuse for Mini App
- Google My Maps → KMZ → GeoJSON pipeline for bulk import

**What tjai can do better:**
- No separate app needed - Telegram Mini App replaces the standalone web app
- Voice-first interaction - "save this place" while walking, no typing
- LLM intelligence - dkbapp had no AI; tjai can infer, recommend, research
- Unified knowledge base - places live alongside calendar, todos, memories, not in a silo
- Cross-session context - the LLM knows your places AND your schedule AND your preferences
- Sync built in - tjai's multi-device sync means places are everywhere instantly

**What to preserve from dkbapp's design:**
- The category/subtype taxonomy (restaurant, cafe, sights, culture, etc.) - proven useful over years of curation
- Region polygons for grouping (NYC neighborhoods, etc.)
- The entity-relation concept - "this restaurant is in this neighborhood" / "this gallery is near this park"
- Review URL linkage - knowing *why* a place was saved (which article, which recommendation)

**Key architectural difference:** dkbapp was a standalone app that needed its own hosting, auth, mobile optimization, offline support. tjai delegates all of that to Telegram (mobile), etaverse.com (hosting), and the existing sync infrastructure. The places feature is just more entries in the same system, not a new system.

### Dependencies on Existing Infrastructure

- GPS location from Telegram: working
- Voice dialogue: working
- Web search in LLM: working
- User profile in system prompt: working
- Background jobs (JobQueue): working (used by reminders)
- tjai entry creation from bot: working
